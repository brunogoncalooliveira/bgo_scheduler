"""Execução, logging Loki, streaming, timeout, concorrência e agendamento."""

import json
import sys
import threading
import time
from datetime import time as dtime

from bgo_scheduler.config import SleepHours
from bgo_scheduler.scheduler_core import AppDef, AppRuntime, discover_apps, read_schedule
from conftest import wait_until


def log_lines(cfg, name):
    path = cfg.logs_dir / f"{name}.log"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# --------------------------------------------------------------------------
# Execução básica
# --------------------------------------------------------------------------

def test_runs_python_app_ok(make_app, make_runtime):
    d = make_app("ok", "print('ola')")
    rt = make_runtime(d)
    rt.run_once("teste")
    assert rt.last["status"] == "ok"
    assert rt.last["returncode"] == 0
    assert "ola" in rt.last["stdout_tail"]


def test_runs_bat_app(make_app, make_runtime):
    d = make_app("batapp", "@echo off\necho ola-bat\n", ext="bat")
    rt = make_runtime(d, kind="bat")
    rt.run_once("teste")
    assert rt.last["status"] == "ok"
    assert "ola-bat" in rt.last["stdout_tail"]


def test_failing_app_status_erro(make_app, make_runtime):
    d = make_app("bad", "import sys\nprint('a')\nsys.exit(1)")
    rt = make_runtime(d)
    rt.run_once("teste")
    assert rt.last["status"] == "erro"
    assert rt.last["returncode"] == 1


def test_utf8_preserved(make_app, make_runtime):
    d = make_app("acc", "print('acentuação: ção, ã, é')")
    rt = make_runtime(d)
    rt.run_once("teste")
    assert "acentuação" in rt.last["stdout_tail"]


def test_runs_with_app_dir_as_cwd(make_app, make_runtime):
    d = make_app("cwd", "import os\nprint(os.getcwd())")
    rt = make_runtime(d)
    rt.run_once("teste")
    assert str(d) in rt.last["stdout_tail"]


# --------------------------------------------------------------------------
# Logging em formato Grafana Loki
# --------------------------------------------------------------------------

def test_loki_json_log_format(make_app, make_runtime):
    d = make_app("logfmt", "print('linha')")
    rt = make_runtime(d)
    rt.run_once("teste")
    lines = log_lines(rt.registry.config, "logfmt")
    assert lines
    for entry in lines:
        assert {"ts", "level", "app", "event", "msg"} <= set(entry)
        assert entry["ts"].endswith("Z")
    events = [x["event"] for x in lines]
    assert "run_start" in events and "stdout" in events and "run_end" in events


def test_stderr_logged_as_error(make_app, make_runtime):
    d = make_app("errlog", "import sys\nprint('boom', file=sys.stderr)")
    rt = make_runtime(d)
    rt.run_once("teste")
    lines = log_lines(rt.registry.config, "errlog")
    assert any(x["event"] == "stderr" and x["level"] == "error" for x in lines)


# --------------------------------------------------------------------------
# Streaming de logs (linhas aparecem DURANTE a execução)
# --------------------------------------------------------------------------

def test_streaming_logs_live(make_app, make_runtime):
    d = make_app("stream",
                 "import time\n"
                 "for i in range(6):\n"
                 "    print('linha', i, flush=True)\n"
                 "    time.sleep(0.5)\n")
    rt = make_runtime(d)
    t = threading.Thread(target=rt.run_once, args=("teste",), daemon=True)
    t.start()
    # a meio da execução já devem existir linhas no log, com a app ainda a correr
    got_mid = wait_until(
        lambda: rt.running and sum(
            1 for x in log_lines(rt.registry.config, "stream") if x["event"] == "stdout") >= 2,
        timeout=5)
    assert got_mid
    t.join(timeout=15)
    lines = log_lines(rt.registry.config, "stream")
    assert sum(1 for x in lines if x["event"] == "stdout") == 6
    assert "linha 5" in rt.last["stdout_tail"]


# --------------------------------------------------------------------------
# Timeout mata a app (e a árvore de processos)
# --------------------------------------------------------------------------

def test_timeout_kills_app(make_app, make_runtime):
    d = make_app("inf", "import time\nwhile True:\n    time.sleep(1)\n")
    rt = make_runtime(d)
    rt.timeout_minutes = 0.03  # ~1.8s
    start = time.time()
    rt.run_once("teste")
    assert rt.last["status"] == "timeout"
    assert time.time() - start < 15


# --------------------------------------------------------------------------
# Concorrência: max_parallel limita execuções em simultâneo
# --------------------------------------------------------------------------

def test_concurrency_queues_second_app(make_app, make_runtime, make_registry):
    reg = make_registry(max_parallel=1)
    assert reg.exec_semaphore is not None
    slow = "import time\nfor i in range(4):\n    print(i, flush=True)\n    time.sleep(0.5)\n"
    d1 = make_app("cc1", slow)
    d2 = make_app("cc2", slow)
    rt1 = make_runtime(d1, registry=reg)
    rt2 = make_runtime(d2, registry=reg)
    t1 = threading.Thread(target=rt1.run_once, args=("teste",), daemon=True)
    t2 = threading.Thread(target=rt2.run_once, args=("teste",), daemon=True)
    t1.start()
    time.sleep(0.3)
    t2.start()
    # com um só lugar, a 1ª corre e a 2ª fica em fila
    assert wait_until(lambda: rt1.running and rt2.queued, timeout=5)
    t1.join(timeout=20)
    t2.join(timeout=20)
    assert rt1.last["status"] == "ok" and rt2.last["status"] == "ok"
    assert not rt2.queued


def test_no_semaphore_when_unlimited(make_registry):
    reg = make_registry(max_parallel=0)
    assert reg.exec_semaphore is None


# --------------------------------------------------------------------------
# Histórico persistente entre reinícios
# --------------------------------------------------------------------------

def test_history_persisted_across_restart(make_app, make_registry):
    reg = make_registry()
    d = make_app("hist", "print('run')")
    rt = AppRuntime(AppDef("hist", d, "py", d / "main.py"), reg)
    rt.run_once("teste")
    rt.run_once("teste")
    # um novo runtime (novo "arranque") lê o histórico do disco
    rt2 = AppRuntime(AppDef("hist", d, "py", d / "main.py"), reg)
    assert len(rt2.history) >= 2
    assert rt2.last["status"] == "ok"
    assert (reg.config.history_dir / "hist.jsonl").exists()


# --------------------------------------------------------------------------
# Toggle persiste no schedule.ini (com comentários preservados)
# --------------------------------------------------------------------------

def test_toggle_persists_and_preserves_comments(make_app, make_runtime):
    schedule = "; comentário importante\n[Schedule]\nenabled = true\ninterval_minutes = 30\n"
    d = make_app("tog", schedule=schedule)
    rt = make_runtime(d)
    rt.set_enabled(False)
    text = (d / "schedule.ini").read_text(encoding="utf-8")
    assert "enabled = false" in text
    assert "; comentário importante" in text
    assert "interval_minutes = 30" in text


# --------------------------------------------------------------------------
# Sleep hours
# --------------------------------------------------------------------------

def test_sleeping_flag_when_in_window(make_app, make_runtime, make_registry, load_cfg):
    cfg = load_cfg()
    cfg.sleep_hours = SleepHours(enabled=True, start=dtime(0, 0), end=dtime(23, 59))
    reg = make_registry(cfg=cfg)
    d = make_app("sl", "print('x')")
    rt = make_runtime(d, registry=reg)
    assert rt._sleep_active() is True
    assert rt.snapshot()["sleeping"] is True


def test_ignore_sleep_hours(make_app, make_runtime, make_registry, load_cfg):
    cfg = load_cfg()
    cfg.sleep_hours = SleepHours(enabled=True, start=dtime(0, 0), end=dtime(23, 59))
    reg = make_registry(cfg=cfg)
    d = make_app("sl2", "print('x')", schedule="[Schedule]\nignore_sleep_hours = true\n")
    rt = make_runtime(d, registry=reg)
    assert rt._sleep_active() is False


# --------------------------------------------------------------------------
# python_exe por app
# --------------------------------------------------------------------------

def test_python_exe_used(make_app, make_runtime):
    d = make_app("pyexe", "import sys\nprint(sys.executable)",
                 schedule=f"[Schedule]\npython_exe = {sys.executable}\n")
    rt = make_runtime(d)
    assert rt.python_exe == sys.executable
    rt.run_once("teste")
    assert rt.last["status"] == "ok"
    assert sys.executable in rt.last["stdout_tail"]


def test_python_exe_missing_produces_warning(make_app, make_runtime):
    d = make_app("pyexe2", "print('x')",
                 schedule="[Schedule]\npython_exe = Z:\\nao\\existe\\python.exe\n")
    rt = make_runtime(d)
    assert any("python_exe" in w for w in rt.schedule_warnings)


def test_invalid_cron_produces_warning(make_app, make_runtime):
    d = make_app("badcron", "print('x')", schedule="[Schedule]\ncron = 0 9 * *\n")
    rt = make_runtime(d)
    assert rt.cron_spec is None
    assert any("cron" in w for w in rt.schedule_warnings)


# --------------------------------------------------------------------------
# Deteção de apps e rescan
# --------------------------------------------------------------------------

def test_discover_prefers_py_over_bat(apps_root):
    d = apps_root / "both"
    d.mkdir()
    (d / "main.py").write_text("print(1)", encoding="utf-8")
    (d / "main.bat").write_text("echo 1", encoding="utf-8")
    apps, _ = discover_apps([apps_root], set())
    assert apps[0].kind == "py"


def test_discover_uses_bat_when_no_py(apps_root):
    d = apps_root / "onlybat"
    d.mkdir()
    (d / "main.bat").write_text("echo 1", encoding="utf-8")
    apps, _ = discover_apps([apps_root], set())
    assert apps[0].kind == "bat"


def test_duplicate_name_first_root_wins(tmp_path):
    r1 = tmp_path / "r1" / "dup"
    r2 = tmp_path / "r2" / "dup"
    r1.mkdir(parents=True)
    r2.mkdir(parents=True)
    (r1 / "main.py").write_text("print(1)", encoding="utf-8")
    (r2 / "main.py").write_text("print(2)", encoding="utf-8")
    apps, dups = discover_apps([r1.parent, r2.parent], set())
    assert len(apps) == 1
    assert apps[0].dir == r1
    assert dups


def test_read_schedule_returns_warnings(apps_root):
    d = apps_root / "s"
    d.mkdir()
    (d / "schedule.ini").write_text("[Schedule]\ninterval_minutes = xpto\n", encoding="utf-8")
    cfg, warns = read_schedule(d)
    assert cfg.interval_minutes == 60
    assert any("interval_minutes" in w for w in warns)


def test_rescan_adds_and_removes(make_registry, make_app, apps_root):
    reg = make_registry()
    make_app("first", "print(1)")
    reg.start()
    assert "first" in reg.apps
    # acrescenta uma app e faz rescan
    make_app("second", "print(2)")
    r = reg.rescan()
    assert "second" in r["added"]
    assert "second" in reg.apps
    # remove a pasta e faz rescan
    import shutil
    shutil.rmtree(apps_root / "second")
    # espera que a app termine execuções pendentes antes de remover
    time.sleep(0.5)
    r = reg.rescan()
    assert "second" in r["removed"]
    assert "second" not in reg.apps
