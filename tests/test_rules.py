"""Regras de notificação de erro e mensagens de sucesso/warning."""

import pytest

from bgo_scheduler.scheduler_core import RulesStore


@pytest.fixture
def rules(tmp_path):
    return RulesStore(tmp_path / "notification_rules.json")


def test_notify_on_nonzero_exit(rules):
    assert rules.evaluate("app", 1, "output")          # rc != 0 -> notifica
    assert not rules.evaluate("app", 0, "output")       # rc 0 -> nada


def test_pattern_triggers_notification(rules):
    rules.set({
        "defaults": {"notify_on_error": False, "patterns": [], "messages": []},
        "apps": {"app": {"patterns": [{"pattern": "ERRO", "is_regex": False, "enabled": True}]}},
    })
    assert rules.evaluate("app", 0, "linha com ERRO aqui")
    assert not rules.evaluate("app", 0, "tudo bem")


def test_regex_pattern(rules):
    rules.set({
        "defaults": {"notify_on_error": False, "patterns": [
            {"pattern": r"falha\s+\d+", "is_regex": True, "enabled": True}]},
        "apps": {},
    })
    assert rules.evaluate("x", 0, "falha 42 detectada")
    assert not rules.evaluate("x", 0, "falha detectada")


def test_disabled_pattern_ignored(rules):
    rules.set({
        "defaults": {"notify_on_error": False, "patterns": [
            {"pattern": "ERRO", "is_regex": False, "enabled": False}]},
        "apps": {},
    })
    assert not rules.evaluate("x", 0, "ERRO")


def test_messages_return_matching_lines(rules):
    rules.set({
        "defaults": {"notify_on_error": True, "patterns": [], "messages": [
            {"pattern": "processados", "is_regex": False, "enabled": True}]},
        "apps": {},
    })
    msgs = rules.evaluate_messages("x", "a\n42 processados com sucesso\nb")
    assert msgs == ["42 processados com sucesso"]


def test_invalid_regex_rejected_on_set(rules):
    with pytest.raises(ValueError):
        rules.set({
            "defaults": {"patterns": [{"pattern": "([", "is_regex": True, "enabled": True}]},
            "apps": {},
        })


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "rules.json"
    RulesStore(path).set({
        "defaults": {"notify_on_error": True, "patterns": [], "messages": []},
        "apps": {"app": {"messages": [{"pattern": "ok", "is_regex": False, "enabled": True}]}},
    })
    reloaded = RulesStore(path).get()
    assert "app" in reloaded["apps"]
