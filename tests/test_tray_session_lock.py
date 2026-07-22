"""Regressão: notificações não devem chegar em rajada ao desbloquear a sessão.

Causa raiz: Shell_NotifyIconW continua a aceitar chamadas com a sessão
bloqueada (o processo do scheduler corre em 2º plano sem sessão interativa),
mas o Windows só mostra os balões depois do desbloqueio — todos de uma vez.
Com 10-15 execuções acumuladas durante o período bloqueado, o utilizador via
10-15 toasts em sequência ao desbloquear.

Fix: TrayApp subscreve WM_WTSSESSION_CHANGE (WTSRegisterSessionNotification).
Enquanto _session_locked, notify() só incrementa um contador em vez de
enfileirar um toast por chamada. Ao desbloquear, um único toast resumo
("N notificações em espera") é enfileirado — nenhum toast se n == 0.

Estes testes exercitam a lógica (notify/_on_session_lock/_on_session_unlock)
num TrayApp construído sem __init__ (sem Registry/RulesStore/dashboard reais
e sem criar nenhuma janela Win32), para isolar o comportamento de
acumulação/lock do resto da infraestrutura do tray. user32 é mockado porque
_on_session_unlock() invoca PostMessageW com um HWND que aqui é fake.
"""

import threading
from collections import deque
from unittest.mock import patch

import pytest

from bgo_scheduler.tray import TrayApp


@pytest.fixture
def app():
    """TrayApp sem __init__: só os campos que notify()/_on_session_lock()/
    _on_session_unlock() tocam. hwnd é um valor fake não-None; user32 fica
    mockado para o teste todo, para não fazer nenhuma chamada Win32 real."""
    a = object.__new__(TrayApp)
    a._notify_queue = deque()
    a._session_lock = threading.Lock()
    a._session_locked = False
    a._locked_notify_count = 0
    a.hwnd = 0xDEAD
    with patch("bgo_scheduler.tray.user32"):
        yield a


def test_notify_queues_normally_when_unlocked(app):
    app.notify("app1", "ok")
    assert list(app._notify_queue) == [("app1", "ok")]


def test_notify_suppressed_and_counted_while_locked(app):
    app._on_session_lock()
    for i in range(15):
        app.notify(f"app{i}", "erro")
    assert list(app._notify_queue) == [], "nenhum toast deve sair enquanto bloqueado"
    assert app._locked_notify_count == 15


def test_unlock_emits_single_summary_toast_with_total_count(app):
    app._on_session_lock()
    for i in range(12):
        app.notify(f"app{i}", "erro")
    app._on_session_unlock()
    assert len(app._notify_queue) == 1, "deve sair exatamente 1 toast, não 12"
    _, message = app._notify_queue[0]
    assert "12" in message


def test_unlock_emits_singular_message_for_exactly_one(app):
    app._on_session_lock()
    app.notify("app1", "erro")
    app._on_session_unlock()
    assert len(app._notify_queue) == 1
    _, message = app._notify_queue[0]
    assert "1 notificação" in message


def test_unlock_emits_nothing_when_no_notifications_pending(app):
    app._on_session_lock()
    app._on_session_unlock()
    assert list(app._notify_queue) == [], "sem notificações pendentes -> sem toast resumo"


def test_lock_resets_stale_counter_from_previous_cycle(app):
    app._on_session_lock()
    app.notify("app1", "erro")
    app._on_session_unlock()
    app._notify_queue.clear()   # simula o drain do toast resumo do 1º ciclo

    app._on_session_lock()      # 2º ciclo de lock/unlock
    assert app._locked_notify_count == 0, "o contador não deve arrastar do ciclo anterior"
    app.notify("app2", "erro")
    app._on_session_unlock()
    _, message = app._notify_queue[0]
    assert "1 notificação" in message, "só a ocorrência do 2º ciclo deve ser contada"


def test_notify_after_unlock_goes_through_normally_not_counted(app):
    app._on_session_lock()
    app.notify("app1", "erro")
    app._on_session_unlock()
    app._notify_queue.clear()

    app.notify("app2", "ok, já desbloqueado")
    assert list(app._notify_queue) == [("app2", "ok, já desbloqueado")]
