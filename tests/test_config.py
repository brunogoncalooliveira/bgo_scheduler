"""Carregamento do scheduler.ini: sleep hours, max_parallel e avisos."""

from datetime import datetime, time
from pathlib import Path

import pytest

from bgo_scheduler.config import SleepHours, _parse_hhmm, parse_sleep_window


@pytest.mark.parametrize("raw,expected", [
    ("22:00", time(22, 0)),
    ("7h30", time(7, 30)),
    ("9", time(9, 0)),
    ("25:99", None),
    ("", None),
    ("abc", None),
])
def test_parse_hhmm(raw, expected):
    assert _parse_hhmm(raw) == expected


def test_sleep_overnight_window():
    sh = SleepHours(enabled=True, start=time(22, 0), end=time(7, 0))
    assert sh.active_at(datetime(2026, 7, 8, 23, 30))
    assert sh.active_at(datetime(2026, 7, 8, 3, 0))
    assert not sh.active_at(datetime(2026, 7, 8, 12, 0))
    assert not sh.active_at(datetime(2026, 7, 8, 7, 0))  # fim exclusivo


def test_sleep_intraday_window():
    sh = SleepHours(enabled=True, start=time(1, 0), end=time(5, 0))
    assert sh.active_at(datetime(2026, 7, 8, 3, 0))
    assert not sh.active_at(datetime(2026, 7, 8, 6, 0))


def test_sleep_wakes_at():
    sh = SleepHours(enabled=True, start=time(22, 0), end=time(7, 0))
    assert sh.next_end_after(datetime(2026, 7, 8, 23, 30)).time() == time(7, 0)


def test_sleep_disabled_or_equal_is_invalid():
    assert not SleepHours(enabled=False, start=time(0), end=time(23)).valid
    assert not SleepHours(enabled=True, start=time(9), end=time(9)).valid


def test_parse_sleep_window_ok():
    sh, warn = parse_sleep_window("23:00-06:30")
    assert warn is None
    assert sh.valid and sh.start == time(23, 0) and sh.end == time(6, 30)


@pytest.mark.parametrize("raw", ["", "  "])
def test_parse_sleep_window_empty(raw):
    sh, warn = parse_sleep_window(raw)
    assert sh is None and warn is None


@pytest.mark.parametrize("raw", ["23:00", "9-9", "25:00-06:00", "abc-def"])
def test_parse_sleep_window_invalid(raw):
    sh, warn = parse_sleep_window(raw)
    assert sh is None and warn is not None


def test_defaults(load_cfg):
    cfg = load_cfg()
    assert cfg.max_parallel == 0
    assert cfg.warnings == []
    assert cfg.history_dir.exists()
    assert cfg.logs_dir.exists()


def test_max_parallel_parsed(load_cfg):
    cfg = load_cfg("[Execution]\nmax_parallel = 3\n")
    assert cfg.max_parallel == 3


def test_warning_on_bad_port(load_cfg):
    cfg = load_cfg("[Dashboard]\nport = abc\n")
    assert cfg.port == 8765
    assert any("port" in w for w in cfg.warnings)


def test_warning_on_bad_max_parallel(load_cfg):
    cfg = load_cfg("[Execution]\nmax_parallel = muitos\n")
    assert cfg.max_parallel == 0
    assert any("max_parallel" in w for w in cfg.warnings)


def test_warning_on_sleep_hours_missing_times(load_cfg):
    cfg = load_cfg("[SleepHours]\nenabled = true\nstart = 22:00\nend =\n")
    assert not cfg.sleep_hours.valid
    assert any("SleepHours" in w for w in cfg.warnings)


def test_warning_on_missing_root(load_cfg, tmp_path):
    missing = tmp_path / "nao_existe"
    cfg = load_cfg(roots=[missing])
    assert any("não encontrada" in w for w in cfg.warnings)


def test_creates_default_ini(tmp_path):
    from bgo_scheduler.config import load_config
    ini = tmp_path / "scheduler.ini"
    assert not ini.exists()
    load_config(config_path=ini, apps_roots_override=[str(tmp_path)])
    assert ini.exists()
    assert "[Dashboard]" in ini.read_text(encoding="utf-8")


def test_first_run_roots_default_to_config_dir(tmp_path):
    """Primeiro arranque sem --config-override: roots = a própria pasta de config."""
    from bgo_scheduler.config import load_config
    ini = tmp_path / "scheduler.ini"
    cfg = load_config(config_path=ini)          # sem apps_roots_override
    assert cfg.apps_roots == [tmp_path]
    assert str(tmp_path) in ini.read_text(encoding="utf-8")
    assert "history" in cfg.exclude


def test_existing_ini_not_overwritten(tmp_path):
    """Se o scheduler.ini já existe, não é recriado nem os roots são mexidos."""
    from bgo_scheduler.config import load_config
    ini = tmp_path / "scheduler.ini"
    ini.write_text("[Apps]\nroots =\n    D:\\meus_apps\n", encoding="utf-8")
    cfg = load_config(config_path=ini)
    assert cfg.apps_roots == [Path("D:\\meus_apps")]
