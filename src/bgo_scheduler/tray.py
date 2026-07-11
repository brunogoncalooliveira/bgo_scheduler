"""Ícone de system tray nativo do Windows (Win32 via ctypes).

Sem dependências externas: carrega ficheiros .ico com o Win32 e desenha o
menu com HMENU. Substitui o pystray/Pillow (a app fica sem dependências de
runtime). Só funciona no Windows — o único ambiente suportado.
"""

import ctypes
import sys
import webbrowser
from collections import deque
from contextlib import ExitStack
from ctypes import wintypes
from functools import partial
from importlib import resources

from .config import SchedulerConfig
from .scheduler_core import Registry, RulesStore
from .web_dashboard import start_dashboard

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

LRESULT = ctypes.c_ssize_t
LPARAM = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
HICON = wintypes.HANDLE
HMENU = wintypes.HANDLE

# mensagens
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_TRAY_UPDATE_ICON = WM_APP + 2
WM_TRAY_NOTIFY = WM_APP + 3
WM_TRAY_QUIT = WM_APP + 4
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

# Shell_NotifyIcon
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10

# LoadImage / menus
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040
MF_STRING, MF_SEPARATOR, MF_POPUP = 0x0000, 0x0800, 0x0010
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
CW_USEDEFAULT = -0x80000000

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def _setup_prototypes():
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
        ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.CreatePopupMenu.restype = HMENU
    user32.AppendMenuW.argtypes = [HMENU, wintypes.UINT, WPARAM, wintypes.LPCWSTR]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.TrackPopupMenu.argtypes = [
        HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class TrayApp:
    def __init__(self, config: SchedulerConfig):
        self.config = config
        self.rules = RulesStore(config.rules_path)
        self.registry = Registry(config, self.rules)
        self.registry.notifier = self.notify
        self.registry.on_state_change = self.request_icon_update
        self.server = None
        self.hwnd = None
        self._menu_actions = {}          # id -> callable
        self._notify_queue = deque()
        self._res_stack = ExitStack()
        self.hicons = {}
        _setup_prototypes()

    # -- ícones -----------------------------------------------------------

    def _load_icons(self):
        icons_dir = resources.files("bgo_scheduler") / "icons"
        for key, fname in (("ok", "ok.ico"), ("run", "run.ico"), ("err", "err.ico")):
            path = self._res_stack.enter_context(resources.as_file(icons_dir / fname))
            self.hicons[key] = user32.LoadImageW(
                None, str(path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)

    def _current_icon(self):
        apps = list(self.registry.apps.values())
        if any(rt.running for rt in apps):
            return self.hicons.get("run")
        if any(rt.last and rt.last.get("status") != "ok" for rt in apps):
            return self.hicons.get("err")
        return self.hicons.get("ok")

    # -- ações ------------------------------------------------------------

    def open_dashboard(self):
        webbrowser.open(self.config.dashboard_url)

    def open_app_dashboard(self, name):
        webbrowser.open(f"{self.config.dashboard_url}?app={name}")

    def run_app(self, name):
        if self.registry.trigger(name):
            self.notify(name, "Execução pedida.")

    def open_link(self, url):
        try:
            webbrowser.open(url)
        except Exception as e:
            self.notify("Erro", f"Não foi possível abrir o link: {e}")

    # -- notificações e ícone (marshalling para a thread da UI) -----------

    def notify(self, title, message):
        self._notify_queue.append((str(title)[:60], str(message)[:250]))
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_TRAY_NOTIFY, 0, 0)

    def request_icon_update(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_TRAY_UPDATE_ICON, 0, 0)

    # -- menu -------------------------------------------------------------

    def _build_menu(self):
        """Constrói o HMENU a partir do estado atual e devolve (hmenu, submenus)."""
        self._menu_actions.clear()
        submenus = []
        menu = user32.CreatePopupMenu()
        next_id = [1000]

        def add(hmenu, text, action):
            cid = next_id[0]
            next_id[0] += 1
            self._menu_actions[cid] = action
            user32.AppendMenuW(hmenu, MF_STRING, cid, text)

        add(menu, "Dashboard", self.open_dashboard)
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        for name in list(self.registry.apps):
            sub = user32.CreatePopupMenu()
            submenus.append(sub)
            add(sub, "Abrir dashboard", partial(self.open_app_dashboard, name))
            add(sub, "Executar agora", partial(self.run_app, name))
            user32.AppendMenuW(menu, MF_POPUP, ctypes.c_size_t(sub).value, name)
        if self.registry.apps:
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        for label, url in self.config.links.items():
            add(menu, label, partial(self.open_link, url))
        if self.config.links:
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        add(menu, "Redetetar apps", self._rescan)
        add(menu, "Sair", self._quit)
        return menu, submenus

    def _rescan(self):
        r = self.registry.rescan()
        self.notify("Redetetar apps", f"+{len(r['added'])} novas, -{len(r['removed'])} removidas")

    def _show_menu(self):
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        menu, submenus = self._build_menu()
        user32.SetForegroundWindow(self.hwnd)   # para o menu fechar ao clicar fora
        cmd = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)  # WM_NULL: fecha o menu corretamente
        user32.DestroyMenu(menu)
        for sub in submenus:
            user32.DestroyMenu(sub)
        action = self._menu_actions.get(cmd)
        if action:
            try:
                action()
            except Exception as e:
                print(f"Erro na ação do menu: {e}")

    # -- ciclo de vida / mensagens ----------------------------------------

    def _quit(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_TRAY_QUIT, 0, 0)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            low = lparam & 0xFFFF
            if low == WM_RBUTTONUP:
                self._show_menu()
            elif low == WM_LBUTTONDBLCLK:
                self.open_dashboard()
            return 0
        if msg == WM_TRAY_UPDATE_ICON:
            self._modify_icon()
            return 0
        if msg == WM_TRAY_NOTIFY:
            self._drain_notifications()
            return 0
        if msg == WM_TRAY_QUIT:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self._remove_icon()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _nid(self, flags):
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        return nid

    def _add_icon(self):
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._current_icon()
        nid.szTip = "bgo scheduler"
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _modify_icon(self):
        nid = self._nid(NIF_ICON)
        nid.hIcon = self._current_icon()
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _remove_icon(self):
        nid = self._nid(0)
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def _drain_notifications(self):
        while self._notify_queue:
            title, message = self._notify_queue.popleft()
            nid = self._nid(NIF_INFO)
            nid.szInfoTitle = title
            nid.szInfo = message
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _create_window(self):
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc_ref = WNDPROC(self._wndproc)   # manter viva (evita GC)
        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = "bgo_scheduler_tray"
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            0, wc.lpszClassName, "bgo scheduler", 0,
            CW_USEDEFAULT, CW_USEDEFAULT, CW_USEDEFAULT, CW_USEDEFAULT,
            None, None, hinst, None)

    # -- arranque ---------------------------------------------------------

    def start(self):
        try:
            self.server = start_dashboard(self.config, self.registry, self.rules)
        except OSError as e:
            print(f"Não foi possível abrir o dashboard em {self.config.dashboard_url}: {e}")
            print("Já existe outra instância? A abrir o dashboard existente.")
            webbrowser.open(self.config.dashboard_url)
            sys.exit(1)

        self._load_icons()
        self._create_window()
        self._add_icon()
        self.registry.start()

        if self.config.open_on_start:
            self.open_dashboard()

        print(f"bgo_scheduler ativo — dashboard em {self.config.dashboard_url}")
        print(f"configuração: {self.config.ini_path}")

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self.registry.stop()
            if self.server:
                self.server.shutdown()
            self._res_stack.close()
