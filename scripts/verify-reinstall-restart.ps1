<#
.SYNOPSIS
  Verifica uma alteração ao bgo_scheduler ponta-a-ponta: testes, lint, build
  do wheel real, reinstalação por cima da instalação existente, e reinício
  do tray — a única forma de confirmar que o PACOTE instalado (não só o
  source tree) funciona, com o teu tray já a correr a versão nova no fim.

.DESCRIPTION
  Passos (para em qualquer falha; nunca deixa o tray desligado a meio nem o
  pacote meio-instalado sem avisar):
    1. pytest -q
    2. ruff check .
    3. python -m build --wheel   (dist/ e build/ são recriados)
    4. Envia WM_TRAY_QUIT à janela do tray (classe bgo_scheduler_tray) — o
       mesmo que "Sair" no menu do tray — e espera terminar.
    5. pip uninstall -y bgo-scheduler
    6. pip install <wheel recém-compilada>
    7. Smoke-check: pip show confirma a versão instalada.
    8. Reinicia bgo-scheduler-tray.exe.

  NÃO mexe em git (commit/tag/push) — fica à parte, porque a mensagem de
  commit depende do que mudou em cada caso.

.PARAMETER PythonExe
  Interpretador a usar para pytest/ruff/build/pip. Por omissão, o que gere a
  instalação atual deste utilizador.

.PARAMETER TrayExe
  Caminho do executável do tray a (re)lançar no fim.

.PARAMETER DashboardPort
  Porta do dashboard configurada em scheduler.ini (secção [Dashboard]).
  Usada para identificar sem ambiguidade o processo real do scheduler a
  terminar -- ver nota no passo 4.

.EXAMPLE
  .\scripts\verify-reinstall-restart.ps1
#>
[CmdletBinding()]
param(
    [string]$PythonExe = "C:\Python314\python.exe",
    [string]$TrayExe = "$env:APPDATA\Python\Python314\Scripts\bgo-scheduler-tray.exe",
    [int]$DashboardPort = 8765
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# -- 1) testes --------------------------------------------------------------
Step "pytest"
& $PythonExe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "pytest falhou — nada foi tocado no pacote instalado." }

# -- 2) lint ------------------------------------------------------------
Step "ruff check"
& $PythonExe -m ruff check .
if ($LASTEXITCODE -ne 0) { throw "ruff falhou — nada foi tocado no pacote instalado." }

# -- 3) build do wheel --------------------------------------------------
Step "build do wheel"
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
& $PythonExe -m build --wheel
if ($LASTEXITCODE -ne 0) { throw "build da wheel falhou." }
$whl = Get-ChildItem "$repoRoot\dist\*.whl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $whl) { throw "nenhuma .whl encontrada em dist\ depois do build." }
Write-Host "wheel: $($whl.Name)"

# -- 4) parar o tray -------------------------------------------------------
# WM_TRAY_QUIT (== "Sair" no menu) só alcança a janela do tray quando este
# script corre na MESMA sessão/window-station do desktop interativo. Numa
# shell não-interativa (agente, tarefa agendada) FindWindow devolve sempre
# zero mesmo com o tray a correr -- confirmado na prática: o script concluiu
# "não estava a correr" com o processo bem vivo, o pip reinstalou os
# ficheiros em disco mas o processo antigo continuou a servir o dashboard a
# partir do código já carregado em memória (a instalação nova só ficou
# visível depois de matar o processo antigo à força). Por isso a fonte de
# verdade aqui é sempre o PROCESSO real (identificado pela porta do
# dashboard, robusto seja qual for o mecanismo do launcher), nunca a janela.
Step "a parar o bgo-scheduler-tray (se estiver a correr)"
Add-Type -Namespace BgoScheduler -Name Native -MemberDefinition @'
[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
[DllImport("user32.dll")]
public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
'@

function Get-BgoSchedulerProcessIds {
    # processo real: quem está à escuta na porta do dashboard (fonte de
    # verdade, independente de como o launcher/gui_scripts foi montado)
    $listenerPids = @(Get-NetTCPConnection -LocalPort $DashboardPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -ne 0 })
    # + o launcher bgo-scheduler-tray.exe em si (pode ser um processo à parte)
    $launcherPids = @(Get-Process -Name "bgo-scheduler-tray" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Id)
    @($listenerPids + $launcherPids) | Select-Object -Unique
}

$pids = Get-BgoSchedulerProcessIds
if ($pids) {
    # tentativa graciosa primeiro (funciona quando corrido à mão, na sessão do utilizador)
    $hwnd = [BgoScheduler.Native]::FindWindow("bgo_scheduler_tray", $null)
    if ($hwnd -ne [IntPtr]::Zero) {
        [void][BgoScheduler.Native]::PostMessage($hwnd, 0x8004, [IntPtr]::Zero, [IntPtr]::Zero)
    }
    $deadline = (Get-Date).AddSeconds(8)
    while ((Get-BgoSchedulerProcessIds) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    # fallback: ainda vivo (FindWindow não alcançou a janela, ou não reagiu a
    # tempo) -- termina pelo PID, que funciona sempre.
    $stillAlive = Get-BgoSchedulerProcessIds
    if ($stillAlive) {
        Write-Host "WM_TRAY_QUIT nao alcancou o tray -- a terminar pelo PID ($($stillAlive -join ', '))." -ForegroundColor Yellow
        $stillAlive | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    if (Get-BgoSchedulerProcessIds) {
        throw "nao foi possivel parar o bgo-scheduler-tray."
    }
    Write-Host "tray parado."
} else {
    Write-Host "tray nao estava a correr."
}

# -- 5/6) uninstall + install --------------------------------------------
Step "pip uninstall bgo-scheduler"
& $PythonExe -m pip uninstall -y bgo-scheduler

Step "pip install $($whl.Name)"
& $PythonExe -m pip install $whl.FullName
if ($LASTEXITCODE -ne 0) {
    throw "pip install falhou -- o tray fica desligado; corre 'pip install $($whl.FullName)' manualmente antes de reiniciar."
}

# -- 7) smoke-check -------------------------------------------------------
Step "smoke-check"
& $PythonExe -m pip show bgo-scheduler

# -- 8) reiniciar o tray --------------------------------------------------
# Confirma pela RESPOSTA REAL do dashboard, não só pela existência de um
# processo com o nome certo -- foi assim que se percebeu, da primeira vez,
# que o processo "a correr" era na verdade o antigo (1.9.14) ainda vivo.
Step "a reiniciar o bgo-scheduler-tray"
Start-Process -FilePath $TrayExe
$expectedVersion = $null
if ($whl.Name -match 'bgo_scheduler-([\d.]+)-') { $expectedVersion = $Matches[1] }
$deadline = (Get-Date).AddSeconds(15)
$liveVersion = $null
while ((Get-Date) -lt $deadline -and -not $liveVersion) {
    try {
        $liveVersion = (Invoke-RestMethod -Uri "http://127.0.0.1:$DashboardPort/api/state" -TimeoutSec 2 -ErrorAction Stop).version
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $liveVersion) {
    throw "o dashboard nao respondeu em http://127.0.0.1:$DashboardPort/api/state depois de reiniciar -- confirma manualmente."
}
if ($expectedVersion -and $liveVersion -ne $expectedVersion) {
    throw "o dashboard esta a responder com a versao $liveVersion, mas esperava-se $expectedVersion -- ainda ha um processo antigo a servir a porta $DashboardPort."
}
Write-Host "tray a correr -- versao confirmada em execucao: $liveVersion" -ForegroundColor Green
