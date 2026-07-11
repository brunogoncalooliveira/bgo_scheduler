"""API HTTP do dashboard (http.server, sem dependências externas)."""

import json
import urllib.error
import urllib.request

import pytest

from bgo_scheduler.scheduler_core import Registry, RulesStore
from bgo_scheduler.web_dashboard import start_dashboard
from conftest import free_port, wait_until


@pytest.fixture
def server(load_cfg, make_app):
    make_app("demo", "print('informação processada')")
    cfg = load_cfg()
    cfg.port = free_port()
    rules = RulesStore(cfg.rules_path)
    reg = Registry(cfg, rules)
    srv = start_dashboard(cfg, reg, rules)
    reg.start()
    base = f"http://127.0.0.1:{cfg.port}"
    # espera pela primeira execução da app
    wait_until(lambda: any(a["last"] for a in reg.snapshot()["apps"]), timeout=15)
    yield base, reg, cfg
    reg.stop()
    srv.shutdown()


def call(base, path, method="GET", body=None, host=None):
    headers = {"Content-Type": "application/json"}
    if host:
        headers["Host"] = host
    req = urllib.request.Request(
        base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers)
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.status, json.loads(res.read().decode("utf-8"))


def test_index_served(server):
    base, _, _ = server
    req = urllib.request.Request(base + "/")
    with urllib.request.urlopen(req, timeout=10) as res:
        assert res.status == 200
        assert "bgo scheduler" in res.read().decode("utf-8")


def test_state_endpoint(server):
    base, _, _ = server
    status, body = call(base, "/api/state")
    assert status == 200
    assert body["apps"][0]["name"] == "demo"
    assert "sleep_hours" in body and "max_parallel" in body and "warnings" in body


def test_logs_endpoint(server):
    base, _, _ = server
    status, body = call(base, "/api/logs?app=demo&lines=50")
    assert status == 200
    assert len(body["lines"]) >= 1


def test_run_endpoint(server):
    base, reg, _ = server
    status, body = call(base, "/api/run?app=demo", "POST")
    assert status == 200 and body["ok"]


def test_run_unknown_app_404(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/run?app=nope", "POST")
    assert e.value.code == 404


def test_rules_round_trip(server):
    base, _, _ = server
    _, current = call(base, "/api/rules")
    current["apps"]["demo"] = {
        "patterns": [{"pattern": "informação", "is_regex": False, "enabled": True}],
        "messages": [],
    }
    status, _ = call(base, "/api/rules", "POST", current)
    assert status == 200
    _, echoed = call(base, "/api/rules")
    assert "demo" in echoed["apps"]


def test_rules_invalid_regex_400(server):
    base, _, _ = server
    bad = {"defaults": {"patterns": [{"pattern": "([", "is_regex": True, "enabled": True}]}, "apps": {}}
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/rules", "POST", bad)
    assert e.value.code == 400


def test_toggle_endpoint(server):
    base, reg, _ = server
    status, body = call(base, "/api/toggle?app=demo", "POST", {"enabled": False})
    assert status == 200 and body["enabled"] is False


def test_rescan_endpoint(server):
    base, _, _ = server
    status, body = call(base, "/api/rescan", "POST")
    assert status == 200 and "added" in body


def test_bad_host_rejected(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/state", host="evil.example.com")
    assert e.value.code == 403


def test_port_in_use_raises(server):
    base, reg, cfg = server
    rules = RulesStore(cfg.rules_path)
    with pytest.raises(OSError):
        start_dashboard(cfg, reg, rules)  # mesmo porto -> OSError


def test_sleep_hours_endpoint(server):
    base, reg, cfg = server
    status, body = call(base, "/api/sleep_hours", "POST",
                        {"enabled": True, "start": "22:00", "end": "07:00"})
    assert status == 200 and body["ok"]
    assert reg.config.sleep_hours.valid
    # reflete-se no estado
    _, state = call(base, "/api/state")
    assert state["sleep_hours"]["enabled"] and state["sleep_hours"]["start"] == "22:00"


def test_sleep_hours_endpoint_invalid(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/sleep_hours", "POST", {"enabled": True, "start": "xx", "end": "07:00"})
    assert e.value.code == 400


def test_app_sleep_endpoint(server):
    base, reg, _ = server
    status, body = call(base, "/api/app_sleep?app=demo", "POST",
                        {"mode": "custom", "start": "23:00", "end": "06:00"})
    assert status == 200 and body["ok"]
    _, state = call(base, "/api/state")
    demo = next(a for a in state["apps"] if a["name"] == "demo")
    assert demo["sleep_mode"] == "custom"
    assert demo["app_sleep_hours"] == {"start": "23:00", "end": "06:00"}


def test_app_sleep_unknown_404(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/app_sleep?app=nope", "POST", {"mode": "ignore"})
    assert e.value.code == 404


def test_app_schedule_endpoint_interval(server):
    base, _, _ = server
    status, body = call(base, "/api/app_schedule?app=demo", "POST",
                        {"mode": "interval", "interval_minutes": 15, "timeout_minutes": 3})
    assert status == 200 and body["ok"]
    _, state = call(base, "/api/state")
    demo = next(a for a in state["apps"] if a["name"] == "demo")
    assert demo["interval_minutes"] == 15 and demo["timeout_minutes"] == 3


def test_app_schedule_endpoint_cron(server):
    base, _, _ = server
    status, body = call(base, "/api/app_schedule?app=demo", "POST",
                        {"mode": "cron", "cron": "0 9 * * 1-5"})
    assert status == 200 and body["ok"]
    _, state = call(base, "/api/state")
    demo = next(a for a in state["apps"] if a["name"] == "demo")
    assert demo["cron"] == "0 9 * * 1-5" and demo["schedule_mode"] == "cron"


def test_app_schedule_endpoint_invalid_cron_400(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/app_schedule?app=demo", "POST", {"mode": "cron", "cron": "nope"})
    assert e.value.code == 400


def test_app_schedule_python_exe(server):
    import sys
    base, _, _ = server
    status, body = call(base, "/api/app_schedule?app=demo", "POST",
                        {"mode": "interval", "interval_minutes": 60, "python_exe": sys.executable})
    assert status == 200 and body["ok"]
    _, state = call(base, "/api/state")
    demo = next(a for a in state["apps"] if a["name"] == "demo")
    assert demo["python_exe"] == sys.executable


def test_settings_endpoint(server):
    base, reg, _ = server
    status, body = call(base, "/api/settings", "POST",
                        {"open_on_start": True, "max_parallel": 2,
                         "links": {"Grafana": "http://g"}})
    assert status == 200 and body["ok"]
    assert reg.config.open_on_start is True
    assert reg.config.links == {"Grafana": "http://g"}
    _, state = call(base, "/api/state")
    assert state["settings"]["max_parallel"] == 2
    assert state["links"] == {"Grafana": "http://g"}


def test_settings_endpoint_invalid_port(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/settings", "POST", {"port": 0})
    assert e.value.code == 400


def test_settings_endpoint_roots_live(server, tmp_path):
    base, reg, _ = server
    root2 = tmp_path / "extra"
    (root2 / "novaApp").mkdir(parents=True)
    (root2 / "novaApp" / "main.py").write_text("print(1)", encoding="utf-8")
    status, body = call(base, "/api/settings", "POST",
                        {"roots": [str(reg.config.apps_roots[0]), str(root2)]})
    assert status == 200 and body["ok"]
    _, state = call(base, "/api/state")
    names = [a["name"] for a in state["apps"]]
    assert "novaApp" in names               # detetada ao vivo
    assert str(root2) in state["apps_roots"]
