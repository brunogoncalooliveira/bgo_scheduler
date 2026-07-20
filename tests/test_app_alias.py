"""Alias (nome amigável) e descrição de uma app: schedule.ini + Registry.set_app_alias."""

from bgo_scheduler.scheduler_core import AppDef, AppRuntime, read_schedule


def _rt(reg, app_dir):
    return AppRuntime(AppDef(app_dir.name, app_dir, "py", app_dir / "main.py"), reg)


def read_ini(app_dir):
    return (app_dir / "schedule.ini").read_text(encoding="utf-8")


def test_read_schedule_parses_alias_and_description(apps_root):
    d = apps_root / "a"
    d.mkdir()
    (d / "schedule.ini").write_text(
        "[Schedule]\nalias = Extração diária\ndescription = Lê o SharePoint e grava CSV\n",
        encoding="utf-8",
    )
    cfg, _ = read_schedule(d)
    assert cfg.alias == "Extração diária"
    assert cfg.description == "Lê o SharePoint e grava CSV"


def test_read_schedule_defaults_alias_none(apps_root):
    d = apps_root / "b"
    d.mkdir()
    cfg, _ = read_schedule(d)
    assert cfg.alias is None
    assert cfg.description is None


def test_set_app_alias_persists_and_applies_live(make_app, make_registry):
    reg = make_registry()
    d = make_app("app1", "print(1)")
    reg.apps["app1"] = _rt(reg, d)
    ok, _ = reg.set_app_alias("app1", "Extração diária", "Lê o SharePoint")
    assert ok
    assert "alias = Extração diária" in read_ini(d)
    assert "description = Lê o SharePoint" in read_ini(d)
    rt = reg.apps["app1"]
    assert rt.alias == "Extração diária"
    assert rt.description == "Lê o SharePoint"
    snap = rt.snapshot()
    assert snap["alias"] == "Extração diária"
    assert snap["description"] == "Lê o SharePoint"


def test_set_app_alias_empty_clears(make_app, make_registry):
    reg = make_registry()
    d = make_app("app2", "print(1)", schedule="[Schedule]\nalias = Antigo\n")
    reg.apps["app2"] = _rt(reg, d)
    assert reg.apps["app2"].alias == "Antigo"
    ok, _ = reg.set_app_alias("app2", "", "")
    assert ok
    assert "alias" not in read_ini(d)
    assert reg.apps["app2"].alias is None


def test_set_app_alias_whitespace_only_treated_as_empty(make_app, make_registry):
    reg = make_registry()
    d = make_app("app3", "print(1)")
    reg.apps["app3"] = _rt(reg, d)
    ok, _ = reg.set_app_alias("app3", "   ", "   ")
    assert ok
    assert reg.apps["app3"].alias is None
    assert reg.apps["app3"].description is None


def test_set_app_alias_collapses_embedded_newlines(make_app, make_registry):
    """schedule.ini é uma linha por chave; um valor multi-linha (ex.: vindo de
    um <textarea>) tem de ser colapsado para não corromper o ficheiro."""
    reg = make_registry()
    d = make_app("app4", "print(1)", schedule="[Schedule]\ninterval_minutes = 45\n")
    reg.apps["app4"] = _rt(reg, d)
    ok, _ = reg.set_app_alias("app4", "Nome\ncom linhas", "Descrição\ncom\nvárias linhas")
    assert ok
    # o ini continua válido (uma linha por chave) e as outras chaves sobrevivem intactas
    cfg, warns = read_schedule(d)
    assert not warns
    assert cfg.interval_minutes == 45
    assert cfg.alias == "Nome com linhas"
    assert cfg.description == "Descrição com várias linhas"


def test_set_app_alias_unknown_app(make_registry):
    reg = make_registry()
    ok, msg = reg.set_app_alias("nope", "X", "Y")
    assert not ok and "desconhecida" in msg
