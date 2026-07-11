"""Sleep hours transversais e por app (modos, escrita no INI, live update)."""

import re
from datetime import time

from bgo_scheduler.config import SleepHours
from bgo_scheduler.scheduler_core import AppDef, AppRuntime, read_schedule, set_ini_values

ALWAYS = SleepHours(enabled=True, start=time(0, 0), end=time(23, 59))


def has_key(text, key):
    """True se o INI tem a chave exata (evita 'sleep_hours' casar 'ignore_sleep_hours')."""
    return any(re.match(rf"\s*{re.escape(key)}\s*[=:]", ln) for ln in text.splitlines())


# --------------------------------------------------------------------------
# set_ini_values
# --------------------------------------------------------------------------

def test_set_ini_updates_existing_key(tmp_path):
    ini = tmp_path / "s.ini"
    ini.write_text("; nota\n[Schedule]\nenabled = true\ninterval_minutes = 30\n", encoding="utf-8")
    set_ini_values(ini, "Schedule", {"enabled": "false"})
    text = ini.read_text(encoding="utf-8")
    assert "enabled = false" in text
    assert "; nota" in text and "interval_minutes = 30" in text


def test_set_ini_adds_missing_key(tmp_path):
    ini = tmp_path / "s.ini"
    ini.write_text("[Schedule]\nenabled = true\n", encoding="utf-8")
    set_ini_values(ini, "Schedule", {"sleep_hours": "22:00-07:00"})
    assert "sleep_hours = 22:00-07:00" in ini.read_text(encoding="utf-8")


def test_set_ini_deletes_key_with_none(tmp_path):
    ini = tmp_path / "s.ini"
    ini.write_text("[Schedule]\nenabled = true\nsleep_hours = 1:00-2:00\n", encoding="utf-8")
    set_ini_values(ini, "Schedule", {"sleep_hours": None})
    assert not has_key(ini.read_text(encoding="utf-8"), "sleep_hours")


def test_set_ini_creates_section(tmp_path):
    ini = tmp_path / "s.ini"
    ini.write_text("[Dashboard]\nport = 8765\n", encoding="utf-8")
    set_ini_values(ini, "SleepHours", {"enabled": "true", "start": "22:00", "end": "07:00"})
    text = ini.read_text(encoding="utf-8")
    assert "[SleepHours]" in text and "start = 22:00" in text
    assert "[Dashboard]" in text  # secção existente preservada


# --------------------------------------------------------------------------
# read_schedule + modos por app
# --------------------------------------------------------------------------

def test_read_schedule_parses_sleep_window(apps_root):
    d = apps_root / "a"
    d.mkdir()
    (d / "schedule.ini").write_text("[Schedule]\nsleep_hours = 23:00-06:00\n", encoding="utf-8")
    cfg, _ = read_schedule(d)
    assert cfg.sleep_hours == "23:00-06:00"


def _runtime(reg, app_dir):
    return AppRuntime(AppDef(app_dir.name, app_dir, "py", app_dir / "main.py"), reg)


def test_mode_inherit_follows_global(make_app, make_registry, load_cfg):
    cfg = load_cfg()
    cfg.sleep_hours = ALWAYS
    reg = make_registry(cfg=cfg)
    rt = _runtime(reg, make_app("inh", "print(1)"))
    assert rt.sleep_mode() == "inherit"
    assert rt._sleep_active() is True


def test_mode_ignore_never_sleeps(make_app, make_registry, load_cfg):
    cfg = load_cfg()
    cfg.sleep_hours = ALWAYS
    reg = make_registry(cfg=cfg)
    rt = _runtime(reg, make_app("ign", "print(1)",
                                schedule="[Schedule]\nignore_sleep_hours = true\n"))
    assert rt.sleep_mode() == "ignore"
    assert rt._sleep_active() is False


def test_mode_custom_uses_own_window(make_app, make_registry, load_cfg):
    cfg = load_cfg()  # transversal desligada por omissão
    reg = make_registry(cfg=cfg)
    rt = _runtime(reg, make_app("cus", "print(1)",
                                schedule="[Schedule]\nsleep_hours = 00:00-23:59\n"))
    assert rt.sleep_mode() == "custom"
    assert rt._sleep_active() is True
    snap = rt.snapshot()
    assert snap["app_sleep_hours"] == {"start": "00:00", "end": "23:59"}


# --------------------------------------------------------------------------
# Registry.update_sleep_hours (transversal)
# --------------------------------------------------------------------------

def test_update_global_sleep_hours_writes_and_applies(make_registry):
    reg = make_registry()
    ok, _ = reg.update_sleep_hours(True, "22:00", "07:00")
    assert ok
    assert reg.config.sleep_hours.valid
    assert reg.config.sleep_hours.start == time(22, 0)
    text = reg.config.ini_path.read_text(encoding="utf-8")
    assert "[SleepHours]" in text and "start = 22:00" in text and "enabled = true" in text


def test_update_global_sleep_hours_rejects_invalid(make_registry):
    reg = make_registry()
    ok, msg = reg.update_sleep_hours(True, "99:99", "07:00")
    assert not ok and "inválid" in msg.lower()


# --------------------------------------------------------------------------
# Registry.set_app_sleep (por app)
# --------------------------------------------------------------------------

def test_set_app_sleep_custom_then_ignore_then_inherit(make_app, make_registry):
    reg = make_registry()
    d = make_app("app1", "print(1)")
    reg.apps["app1"] = _runtime(reg, d)

    ok, _ = reg.set_app_sleep("app1", "custom", "23:00", "06:00")
    assert ok
    assert "sleep_hours = 23:00-06:00" in (d / "schedule.ini").read_text(encoding="utf-8")
    assert reg.apps["app1"].sleep_mode() == "custom"

    ok, _ = reg.set_app_sleep("app1", "ignore")
    assert ok
    text = (d / "schedule.ini").read_text(encoding="utf-8")
    assert has_key(text, "ignore_sleep_hours") and not has_key(text, "sleep_hours")
    assert reg.apps["app1"].sleep_mode() == "ignore"

    ok, _ = reg.set_app_sleep("app1", "inherit")
    assert ok
    text = (d / "schedule.ini").read_text(encoding="utf-8")
    assert not has_key(text, "ignore_sleep_hours") and not has_key(text, "sleep_hours")
    assert reg.apps["app1"].sleep_mode() == "inherit"


def test_set_app_sleep_custom_invalid_window(make_app, make_registry):
    reg = make_registry()
    d = make_app("app2", "print(1)")
    reg.apps["app2"] = _runtime(reg, d)
    ok, msg = reg.set_app_sleep("app2", "custom", "10:00", "10:00")
    assert not ok and msg


def test_set_app_sleep_unknown_app(make_registry):
    reg = make_registry()
    ok, msg = reg.set_app_sleep("nope", "ignore")
    assert not ok and "desconhecida" in msg
