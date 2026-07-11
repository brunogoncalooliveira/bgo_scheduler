"""Definições globais: replace_section_keys, write_apps_roots e update_settings."""

from pathlib import Path

from bgo_scheduler.scheduler_core import replace_section_keys, write_apps_roots


def test_replace_section_keys_replaces_and_removes(tmp_path):
    ini = tmp_path / "s.ini"
    ini.write_text(
        "[Dashboard]\nport = 8765\n\n[Links]\n; atalhos\nHello1 = http://a\nHello2 = http://b\n",
        encoding="utf-8")
    replace_section_keys(ini, "Links", {"Novo": "http://c"})
    text = ini.read_text(encoding="utf-8")
    assert "Novo = http://c" in text
    assert "Hello1" not in text and "Hello2" not in text   # removidos
    assert "; atalhos" in text                          # comentário preservado
    assert "[Dashboard]" in text and "port = 8765" in text


def test_replace_section_keys_creates_section(tmp_path):
    ini = tmp_path / "s.ini"
    ini.write_text("[Dashboard]\nport = 8765\n", encoding="utf-8")
    replace_section_keys(ini, "Links", {"X": "http://x"})
    assert "[Links]" in ini.read_text(encoding="utf-8")


def test_update_settings_writes_and_applies_live(make_registry):
    reg = make_registry()
    ok, msg = reg.update_settings(
        host="0.0.0.0", port=9000, open_on_start=True, max_parallel=3,
        links={"Grafana": "http://g", "": "ignora", "SemUrl": ""})
    assert ok
    # aplicado ao vivo onde é seguro
    assert reg.config.open_on_start is True
    assert reg.config.max_parallel == 3
    assert reg.config.links == {"Grafana": "http://g"}   # entradas vazias filtradas
    # gravado no scheduler.ini
    text = reg.config.ini_path.read_text(encoding="utf-8")
    assert "port = 9000" in text and "open_on_start = true" in text
    assert "max_parallel = 3" in text and "Grafana = http://g" in text
    # host/port/max_parallel exigem reiniciar
    assert "reiniciar" in msg.lower()


def test_update_settings_rejects_bad_port(make_registry):
    reg = make_registry()
    ok, msg = reg.update_settings(port=99999)
    assert not ok and "port" in msg.lower()


def test_update_settings_rejects_empty_host(make_registry):
    reg = make_registry()
    ok, msg = reg.update_settings(host="  ")
    assert not ok and "host" in msg.lower()


def test_update_settings_partial(make_registry):
    """Só open_on_start: não deve exigir reinício."""
    reg = make_registry()
    ok, msg = reg.update_settings(open_on_start=False)
    assert ok and "reiniciar" not in msg.lower()
    assert reg.config.open_on_start is False


def test_write_apps_roots_multiline(tmp_path):
    ini = tmp_path / "s.ini"
    ini.write_text(
        "[Apps]\n; comentário\nroots =\n    C:\\velho\n; Sub-pastas\n"
        "exclude = logs\n\n[Dashboard]\nport = 8765\n", encoding="utf-8")
    write_apps_roots(ini, [Path("C:\\novo1"), Path("D:\\novo2")])
    text = ini.read_text(encoding="utf-8")
    assert "    C:\\novo1" in text and "    D:\\novo2" in text
    assert "C:\\velho" not in text                    # continuação antiga removida
    assert "; comentário" in text and "exclude = logs" in text  # preservados
    assert "[Dashboard]" in text and "port = 8765" in text


def test_write_apps_roots_creates_section(tmp_path):
    ini = tmp_path / "s.ini"
    ini.write_text("[Dashboard]\nport = 8765\n", encoding="utf-8")
    write_apps_roots(ini, [Path("C:\\x")])
    text = ini.read_text(encoding="utf-8")
    assert "[Apps]" in text and "    C:\\x" in text


def test_update_settings_roots_applies_live(make_registry, make_app, tmp_path):
    """Acrescentar uma raiz deteta as apps dessa raiz sem reiniciar (via rescan)."""
    reg = make_registry()
    make_app("appA", "print(1)")               # já na raiz por omissão do fixture
    reg.start()
    assert "appA" in reg.apps
    # nova raiz com outra app
    root2 = tmp_path / "root2"
    (root2 / "appB").mkdir(parents=True)
    (root2 / "appB" / "main.py").write_text("print(2)", encoding="utf-8")
    ok, msg = reg.update_settings(roots=[str(reg.config.apps_roots[0]), str(root2)])
    assert ok
    assert "appB" in reg.apps                   # detetada ao vivo
    assert root2 in reg.config.apps_roots
    assert reg.config.roots_overridden is False
    # gravado no INI como multi-linha
    text = reg.config.ini_path.read_text(encoding="utf-8")
    assert str(root2) in text


def test_update_settings_roots_removes_apps(make_registry, make_app):
    """Remover a raiz remove as apps dessa raiz."""
    reg = make_registry()
    make_app("appA", "print(1)")
    reg.start()
    assert "appA" in reg.apps
    ok, _ = reg.update_settings(roots=[])       # sem raízes
    assert ok
    assert "appA" not in reg.apps
