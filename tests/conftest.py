"""Fixtures partilhadas da suite do bgo_scheduler."""

import socket
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bgo_scheduler import scheduler_core  # noqa: E402
from bgo_scheduler.config import load_config  # noqa: E402
from bgo_scheduler.scheduler_core import AppDef, AppRuntime, Registry, RulesStore  # noqa: E402


@pytest.fixture(autouse=True)
def _fast_scheduler(monkeypatch):
    """Remove os atrasos de arranque para os testes correrem depressa."""
    monkeypatch.setattr(scheduler_core, "INITIAL_DELAY_S", 0)
    monkeypatch.setattr(scheduler_core, "STAGGER_S", 0)


@pytest.fixture(autouse=True)
def _reset_loggers():
    """O loki_logger faz cache global dos loggers por nome; limpa entre testes
    para cada teste escrever na sua própria tmp dir (e libertar ficheiros no Windows)."""
    from bgo_scheduler import loki_logger

    def _clear():
        for lg in loki_logger._loggers.values():
            for h in list(lg.handlers):
                h.close()
                lg.removeHandler(h)
        loki_logger._loggers.clear()

    _clear()
    yield
    _clear()


@pytest.fixture
def apps_root(tmp_path):
    root = tmp_path / "apps"
    root.mkdir()
    return root


@pytest.fixture
def make_app(apps_root):
    """Cria uma app de teste: make_app('nome', body=..., schedule=..., ext='py'|'bat')."""
    def _make(name, body="print('ok')", schedule=None, ext="py"):
        d = apps_root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / ("main.py" if ext == "py" else "main.bat")).write_text(body, encoding="utf-8")
        if schedule is not None:
            (d / "schedule.ini").write_text(schedule, encoding="utf-8")
        return d
    return _make


@pytest.fixture
def load_cfg(tmp_path, apps_root):
    """Carrega um SchedulerConfig; ini_text opcional escreve o scheduler.ini."""
    def _load(ini_text=None, roots=None, **overrides):
        ini_path = tmp_path / "scheduler.ini"
        if ini_text is not None:
            ini_path.write_text(ini_text, encoding="utf-8")
        cfg = load_config(
            config_path=ini_path,
            apps_roots_override=[str(r) for r in (roots or [apps_root])],
        )
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg
    return _load


@pytest.fixture
def make_registry(load_cfg, tmp_path):
    created = []

    def _make(cfg=None, **cfg_overrides):
        cfg = cfg or load_cfg(**cfg_overrides)
        reg = Registry(cfg, RulesStore(cfg.rules_path))
        created.append(reg)
        return reg
    yield _make
    for reg in created:
        reg.stop()


@pytest.fixture
def make_runtime(make_registry):
    """Cria um AppRuntime isolado para uma pasta de app já existente."""
    def _make(app_dir, registry=None, kind="py"):
        registry = registry or make_registry()
        entry = app_dir / ("main.py" if kind == "py" else "main.bat")
        return AppRuntime(AppDef(app_dir.name, app_dir, kind, entry), registry)
    return _make


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until(predicate, timeout=15.0, interval=0.1):
    """Espera até predicate() ser verdadeiro ou expirar. Devolve o resultado final."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = predicate()
        if val:
            return val
        time.sleep(interval)
    return predicate()
