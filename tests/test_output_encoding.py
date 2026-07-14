"""Descodificação da saída das apps (acentos corretos).

A saída de uma app é lida em binário e descodificada linha-a-linha: UTF-8 quando
válido (o que python -u e o PowerShell com OutputEncoding=UTF8 emitem) e, quando
não é UTF-8 válido, a code page da consola (OEM, p. ex. cp850). Um único .bat
pode misturar as duas — echo do cmd (OEM) e PowerShell (UTF-8) — e as duas têm
de sair corretas. Regressão do bug em que `SINCRONIZAÇÃO` aparecia como
`SINCRONIZA├ç├âO` (UTF-8 descodificado como cp850).
"""

import sys

import pytest

from bgo_scheduler.scheduler_core import _decode_console

win_only = pytest.mark.skipif(sys.platform != "win32", reason="cmd/powershell só no Windows")


# ---------------- unidade: _decode_console (portável, determinístico) ----------------

def test_utf8_line_decoded_as_utf8():
    raw = "SINCRONIZAÇÃO alterações da stream".encode("utf-8")
    assert _decode_console(raw, "cp850") == "SINCRONIZAÇÃO alterações da stream"


def test_utf8_not_mangled_as_console_cp():
    """O bug exato: UTF-8 descodificado como cp850 dava 'SINCRONIZA├ç├âO'."""
    raw = "SINCRONIZAÇÃO".encode("utf-8")
    got = _decode_console(raw, "cp850")
    assert got == "SINCRONIZAÇÃO"
    assert "├" not in got and "�" not in got


def test_oem_line_falls_back_to_console_cp():
    """Linha do echo do cmd (cp850): bytes que NÃO são UTF-8 válido recuam."""
    raw = "ação coração gestão".encode("cp850")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")            # garante que o teste exercita o fallback
    assert _decode_console(raw, "cp850") == "ação coração gestão"


def test_mixed_stream_decoded_per_line():
    """Stream misto: uma linha OEM (cmd) e outra UTF-8 (PowerShell), como no .bat real."""
    oem = "[main.bat] Configuração terminada".encode("cp850")
    utf = "[INFO] Aceitando alterações da stream...".encode("utf-8")
    assert _decode_console(oem, "cp850") == "[main.bat] Configuração terminada"
    assert _decode_console(utf, "cp850") == "[INFO] Aceitando alterações da stream..."


def test_ascii_line_unaffected():
    raw = b"================================================"
    assert _decode_console(raw, "cp850") == "=" * 48


# ---------------- ponta-a-ponta: app real através do AppRuntime ----------------

def test_py_app_utf8_accents_captured(make_app, make_runtime):
    """App .py: com PYTHONUTF8 a saída é UTF-8 e os acentos chegam intactos."""
    d = make_app("pyacentos", body="print('operação: coração à noite, versão final')")
    rt = make_runtime(d)
    rt.run_once("teste")
    assert rt.last["status"] == "ok"
    assert "operação: coração à noite, versão final" in rt.last["stdout_tail"]


@win_only
def test_bat_calling_powershell_utf8(make_app, make_runtime):
    """Cenário exato do bug: main.bat faz echo (cp850) e chama um PowerShell que
    força OutputEncoding=UTF8 e escreve acentos. As duas codificações no mesmo
    stream têm de sair corretas — sem a mojibake `├ç├â`."""
    d = make_app(
        "acentos",
        ext="bat",
        body=(
            "@echo off\n"
            "echo ===== inicio (cmd echo) =====\n"
            'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0emit.ps1"\n'
        ),
    )
    # .ps1 gravado em UTF-8 COM BOM (como os ficheiros reais), para o PS 5.1 o
    # ler como UTF-8; força a saída da consola para UTF-8, como o RTC-Common.ps1
    (d / "emit.ps1").write_text(
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
        "Write-Host '[INFO] ========== SINCRONIZAÇÃO DO WORKSPACE INICIADA =========='\n"
        "Write-Host '[INFO] Aceitando alterações da stream...'\n",
        encoding="utf-8-sig",
    )
    rt = make_runtime(d, kind="bat")
    rt.run_once("teste")
    out = rt.last["stdout_tail"]
    assert rt.last["returncode"] == 0, out + "\n" + rt.last["stderr_tail"]
    assert "===== inicio (cmd echo) =====" in out      # linha OEM do cmd
    assert "SINCRONIZAÇÃO DO WORKSPACE INICIADA" in out  # linha UTF-8 do PowerShell
    assert "Aceitando alterações da stream" in out
    assert "├" not in out and "�" not in out
