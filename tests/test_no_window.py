"""Supressão de janelas de consola quando uma app lança outros programas.

Processos de consola (git, cmd, python…) lançados por uma app criam uma sessão
de consola nova — e uma janela — quando não há consola para herdarem. O
scheduler aloca UMA consola oculta no arranque (tray/pythonw) e os filhos e
netos herdam-na: zero sessões novas por execução, zero janelas.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import bgo_scheduler
from bgo_scheduler.scheduler_core import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    _child_creationflags,
    _console_python,
    _ensure_hidden_console,
    _has_console,
)

win_only = pytest.mark.skipif(sys.platform != "win32", reason="consola oculta só se aplica no Windows")

# a app faz o trabalho chamando OUTRO programa (um "neto") e só emite stderr,
# tal como a app real que expôs o bug
APP_SPAWNS_CHILD = (
    "import subprocess, sys\n"
    "subprocess.run([sys.executable, '-c', \"print('neto ok')\"], check=True)\n"
    "sys.stderr.write('progresso via stderr\\n')\n"
)

DETACHED_PROCESS = 0x00000008   # simula o pythonw do tray: processo SEM consola


@win_only
def test_child_flags_inherit_console_when_present():
    # pytest corre com consola -> os filhos herdam-na (sem janela nova) e o
    # grupo próprio evita que um Ctrl+C no scheduler mate as apps
    assert _has_console()
    flags = _child_creationflags()
    assert flags == CREATE_NEW_PROCESS_GROUP
    assert not (flags & CREATE_NO_WINDOW)


@win_only
def test_ensure_hidden_console_noop_with_console():
    _ensure_hidden_console()          # já há consola: não pode partir nada
    assert _has_console()


@pytest.mark.skipif(sys.platform == "win32", reason="ramo não-Windows")
def test_console_helpers_noop_off_windows():
    assert _has_console() is False
    assert _child_creationflags() == 0
    _ensure_hidden_console()          # não levanta


@win_only
def test_grandchild_console_hidden_without_scheduler_console(tmp_path):
    """Cenário real do bug: scheduler sem consola (pythonw) executa uma app que
    chama um programa de consola (neto). O neto tem de herdar a consola OCULTA
    alocada pelo scheduler — HWND existe mas a janela NÃO é visível."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import ctypes\n"
        "h = ctypes.windll.kernel32.GetConsoleWindow()\n"
        "v = ctypes.windll.user32.IsWindowVisible(h) if h else 0\n"
        "print(h, v)\n",
        encoding="utf-8",
    )
    child = tmp_path / "child.py"   # a "app": lança o neto via cmd.exe (janela DOS clássica)
    child.write_text(
        "import subprocess, sys\n"
        f"r = subprocess.run(['cmd.exe', '/c', sys.executable, {str(probe)!r}],\n"
        "                   capture_output=True, text=True)\n"
        "print(r.stdout.strip())\n",
        encoding="utf-8",
    )
    sim = tmp_path / "sim.py"       # o "scheduler" em modo tray
    sim.write_text(
        "import subprocess, sys\n"
        "from bgo_scheduler.scheduler_core import _child_creationflags, _ensure_hidden_console\n"
        "_ensure_hidden_console()\n"
        f"r = subprocess.run([sys.executable, {str(child)!r}], capture_output=True, text=True,\n"
        "                   creationflags=_child_creationflags())\n"
        "print(r.stdout.strip())\n",
        encoding="utf-8",
    )
    src_dir = str(Path(bgo_scheduler.__file__).parents[1])
    env = {**os.environ, "PYTHONPATH": src_dir}
    r = subprocess.run(
        [sys.executable, str(sim)], capture_output=True, text=True, timeout=60,
        env=env, creationflags=DETACHED_PROCESS,
    )
    assert r.returncode == 0, r.stderr
    hwnd, visible = r.stdout.split()
    assert int(hwnd) != 0, "o neto devia ter herdado a consola oculta do scheduler"
    assert int(visible) == 0, "a consola herdada pelo neto não pode ser visível"


def _fake_interp(tmp_path, *names):
    d = tmp_path / "interp"
    d.mkdir(exist_ok=True)
    for n in names:
        (d / n).write_text("", encoding="utf-8")
    return d


def test_pythonw_mapped_to_console_python(tmp_path):
    """O bug real da máquina afetada: o tray (pythonw) passava pythonw às apps;
    em pythonw (GUI) os netos de consola (git, cmd…) abrem janelas VISÍVEIS.
    pythonw.exe tem de ser trocado pelo python.exe ao lado."""
    d = _fake_interp(tmp_path, "pythonw.exe", "python.exe")
    assert _console_python(d / "pythonw.exe") == d / "python.exe"


def test_pythonw_kept_when_no_console_python(tmp_path):
    d = _fake_interp(tmp_path, "pythonw.exe")   # sem python.exe ao lado
    assert _console_python(d / "pythonw.exe") == d / "pythonw.exe"


def test_console_python_untouched(tmp_path):
    d = _fake_interp(tmp_path, "python.exe")
    assert _console_python(d / "python.exe") == d / "python.exe"


def test_resolve_python_maps_scheduler_pythonw(make_app, make_runtime, monkeypatch, tmp_path):
    """Sem python_exe configurado, o interpretador do scheduler (pythonw no
    tray) tem de chegar à app já mapeado para python.exe."""
    d = _fake_interp(tmp_path, "pythonw.exe", "python.exe")
    monkeypatch.setattr(sys, "executable", str(d / "pythonw.exe"))
    rt = make_runtime(make_app("mapeada"))
    exe, err = rt._resolve_python()
    assert err is None
    assert exe == d / "python.exe"


def test_resolve_python_maps_configured_pythonw(make_app, make_runtime, tmp_path):
    """python_exe explícito a apontar para um pythonw (ex.: venv) também é
    mapeado — debaixo do scheduler o pythonw só traz o bug das janelas."""
    d = _fake_interp(tmp_path, "pythonw.exe", "python.exe")
    rt = make_runtime(make_app("cfgw"))
    rt.python_exe = str(d / "pythonw.exe")
    exe, err = rt._resolve_python()
    assert err is None
    assert exe == d / "python.exe"


def test_app_that_spawns_children_still_runs(make_app, make_runtime):
    """A consola herdada não pode partir a execução: o neto corre, rc=0 e o
    stderr da app é capturado."""
    d = make_app("comnetos", body=APP_SPAWNS_CHILD)
    rt = make_runtime(d)
    rt.run_once("teste")
    assert rt.last["status"] == "ok"
    assert rt.last["returncode"] == 0
    assert "progresso via stderr" in rt.last["stderr_tail"]
