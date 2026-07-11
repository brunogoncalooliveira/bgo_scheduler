"""Supressão de janelas de consola quando uma app lança outros programas.

`CREATE_NO_WINDOW` só esconde a consola do processo lançado diretamente; se a
app chamar outros programas de consola (netos), cada um abria uma janela. A
correção dá à app uma consola própria mas OCULTA para os netos a herdarem.
"""

import subprocess
import sys

import pytest

from bgo_scheduler.scheduler_core import _hidden_console

# a app faz o trabalho chamando OUTRO programa (um "neto") e só emite stderr,
# tal como a app real que expôs o bug
APP_SPAWNS_CHILD = (
    "import subprocess, sys\n"
    "subprocess.run([sys.executable, '-c', \"print('neto ok')\"], check=True)\n"
    "sys.stderr.write('progresso via stderr\\n')\n"
)


@pytest.mark.skipif(sys.platform != "win32", reason="consola oculta só se aplica no Windows")
def test_hidden_console_uses_new_hidden_console():
    creationflags, si = _hidden_console()
    # NÃO pode voltar a ser CREATE_NO_WINDOW (era isso que deixava os netos
    # abrir janelas); tem de ser uma consola nova, mas escondida.
    assert creationflags == subprocess.CREATE_NEW_CONSOLE
    assert not (creationflags & subprocess.CREATE_NO_WINDOW)
    assert si is not None
    assert si.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert si.wShowWindow == subprocess.SW_HIDE


@pytest.mark.skipif(sys.platform == "win32", reason="ramo não-Windows")
def test_hidden_console_noop_off_windows():
    assert _hidden_console() == (0, None)


def test_app_that_spawns_children_still_runs(make_app, make_runtime):
    """A consola oculta não pode partir a execução: o neto corre, rc=0 e o
    stderr da app é capturado (a visibilidade das janelas não é testável aqui)."""
    d = make_app("comnetos", body=APP_SPAWNS_CHILD)
    rt = make_runtime(d)
    rt.run_once("teste")
    assert rt.last["status"] == "ok"
    assert rt.last["returncode"] == 0
    assert "progresso via stderr" in rt.last["stderr_tail"]
