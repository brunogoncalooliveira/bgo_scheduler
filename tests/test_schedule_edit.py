"""Edição do agendamento por app (Registry.set_app_schedule)."""

from bgo_scheduler.scheduler_core import AppDef, AppRuntime


def _rt(reg, app_dir):
    return AppRuntime(AppDef(app_dir.name, app_dir, "py", app_dir / "main.py"), reg)


def read_ini(app_dir):
    return (app_dir / "schedule.ini").read_text(encoding="utf-8")


def test_set_interval(make_app, make_registry):
    reg = make_registry()
    d = make_app("a", "print(1)")
    reg.apps["a"] = _rt(reg, d)
    ok, _ = reg.set_app_schedule("a", "interval", interval_minutes=15, timeout_minutes=5)
    assert ok
    assert "interval_minutes = 15" in read_ini(d)
    assert "timeout_minutes = 5" in read_ini(d)
    assert reg.apps["a"].interval_minutes == 15
    assert reg.apps["a"].timeout_minutes == 5


def test_set_cron(make_app, make_registry):
    reg = make_registry()
    d = make_app("b", "print(1)")
    reg.apps["b"] = _rt(reg, d)
    ok, _ = reg.set_app_schedule("b", "cron", cron="0 9 * * 1-5")
    assert ok
    assert "cron = 0 9 * * 1-5" in read_ini(d)
    assert reg.apps["b"].cron_spec is not None


def test_switch_cron_to_interval_removes_cron(make_app, make_registry):
    reg = make_registry()
    d = make_app("c", "print(1)", schedule="[Schedule]\ncron = 0 9 * * 1-5\n")
    reg.apps["c"] = _rt(reg, d)
    ok, _ = reg.set_app_schedule("c", "interval", interval_minutes=30)
    assert ok
    assert "cron" not in read_ini(d)
    assert reg.apps["c"].cron_spec is None
    assert reg.apps["c"].interval_minutes == 30


def test_invalid_interval_rejected(make_app, make_registry):
    reg = make_registry()
    d = make_app("d", "print(1)")
    reg.apps["d"] = _rt(reg, d)
    ok, msg = reg.set_app_schedule("d", "interval", interval_minutes=0)
    assert not ok and msg


def test_invalid_cron_rejected(make_app, make_registry):
    reg = make_registry()
    d = make_app("e", "print(1)")
    reg.apps["e"] = _rt(reg, d)
    ok, msg = reg.set_app_schedule("e", "cron", cron="0 9 * *")
    assert not ok and "cron" in msg.lower()


def test_set_run_after_via_schedule(make_app, make_registry):
    reg = make_registry()
    up = make_app("up1", "print(1)")
    dn = make_app("dn1", "print(1)")
    reg.apps["up1"] = _rt(reg, up)
    reg.apps["dn1"] = _rt(reg, dn)
    ok, _ = reg.set_app_schedule("dn1", "interval", interval_minutes=60, run_after="up1")
    assert ok
    assert "run_after = up1" in read_ini(dn)
    assert reg.apps["dn1"].run_after == ["up1"]
    assert reg._downstream.get("up1") == ["dn1"]


def test_unknown_app(make_registry):
    reg = make_registry()
    ok, msg = reg.set_app_schedule("nope", "interval", interval_minutes=10)
    assert not ok and "desconhecida" in msg


def test_set_python_exe(make_app, make_registry):
    import sys
    reg = make_registry()
    d = make_app("pe", "print(1)")
    reg.apps["pe"] = _rt(reg, d)
    ok, _ = reg.set_app_schedule("pe", "interval", interval_minutes=60, python_exe=sys.executable)
    assert ok
    assert "python_exe = " in read_ini(d)
    assert reg.apps["pe"].python_exe == sys.executable
    # vazio remove a chave (volta ao interpretador do scheduler)
    ok, _ = reg.set_app_schedule("pe", "interval", interval_minutes=60, python_exe="")
    assert ok
    assert "python_exe" not in read_ini(d)
    assert reg.apps["pe"].python_exe is None
