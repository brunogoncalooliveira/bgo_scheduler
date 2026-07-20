"""Encadeamento de apps (run_after): disparo a jusante, ciclos, edição."""

import time

from bgo_scheduler.scheduler_core import read_schedule
from conftest import wait_until


def test_read_schedule_parses_run_after(apps_root):
    d = apps_root / "b"
    d.mkdir()
    (d / "schedule.ini").write_text("[Schedule]\nrun_after = a1, a2\n", encoding="utf-8")
    cfg, _ = read_schedule(d)
    assert cfg.run_after == "a1, a2"


def test_chain_app_is_not_time_scheduled(make_app, make_registry):
    reg = make_registry()
    make_app("up", "print('up')")
    make_app("down", "print('down')", schedule="[Schedule]\nrun_after = up\n")
    reg.start()
    down = reg.apps["down"]
    assert down.run_after == ["up"]
    assert down.snapshot()["schedule_mode"] == "chain"
    # uma app encadeada não tem próxima execução por tempo
    assert down._compute_next_run() is None


def test_downstream_triggered_on_success(make_app, make_registry):
    reg = make_registry()
    # up escreve um ficheiro; down corre a seguir e escreve outro
    outdir = reg.config.config_dir
    make_app("up", f"open(r'{outdir / 'up.txt'}', 'w').write('x')\nprint('up ok')")
    make_app("down", f"open(r'{outdir / 'down.txt'}', 'w').write('y')\nprint('down ok')",
             schedule="[Schedule]\nrun_after = up\n")
    reg.start()
    # up corre no arranque (intervalo), deve despoletar down
    assert wait_until(lambda: (outdir / "down.txt").exists(), timeout=30)
    down = reg.apps["down"]
    assert down.last and down.last["status"] == "ok"
    assert any("dependência" in h["origem"] for h in down.history)


def test_downstream_not_triggered_on_failure(make_app, make_registry):
    reg = make_registry()
    make_app("upf", "import sys\nprint('falhou')\nsys.exit(1)")
    make_app("downf", "print('nao deve correr')", schedule="[Schedule]\nrun_after = upf\n")
    reg.start()
    wait_until(lambda: reg.apps["upf"].last is not None, timeout=20)
    time.sleep(2)  # dá tempo a um eventual (indevido) disparo
    assert reg.apps["downf"].last is None


def test_downstream_map_built(make_app, make_registry):
    reg = make_registry()
    make_app("a", "print(1)")
    make_app("b", "print(1)", schedule="[Schedule]\nrun_after = a\n")
    make_app("c", "print(1)", schedule="[Schedule]\nrun_after = b\n")
    reg.start()
    assert reg._downstream.get("a") == ["b"]
    assert reg._downstream.get("b") == ["c"]


def test_unknown_upstream_warns(make_app, make_registry):
    reg = make_registry()
    make_app("solo", "print(1)", schedule="[Schedule]\nrun_after = naoexiste\n")
    reg.start()
    rt = reg.apps["solo"]
    assert rt.run_after == []
    assert any("não existe" in w for w in rt.schedule_warnings)


def test_self_reference_ignored(make_app, make_registry):
    reg = make_registry()
    make_app("selfref", "print(1)", schedule="[Schedule]\nrun_after = selfref\n")
    reg.start()
    rt = reg.apps["selfref"]
    assert rt.run_after == []
    assert any("própria app" in w for w in rt.schedule_warnings)


def test_chain_wins_over_cron_in_schedule_mode(make_app, make_registry):
    """run_after tem sempre prioridade sobre cron/intervalo (_compute_next_run
    já o fazia); o schedule_mode reportado ao dashboard tem de refletir o
    mesmo, senão o dashboard mostrava "cron ..." para uma app que na prática
    só corre encadeada."""
    reg = make_registry()
    make_app("up2", "print(1)")
    make_app("both", "print(1)",
             schedule="[Schedule]\ncron = 0 9 * * 1-5\nrun_after = up2\n")
    reg.start()
    rt = reg.apps["both"]
    assert rt.cron_spec is not None
    assert rt.run_after == ["up2"]
    assert rt.snapshot()["schedule_mode"] == "chain"
    assert rt._compute_next_run() is None


def test_cycle_is_broken(make_app, make_registry):
    reg = make_registry()
    make_app("x", "print(1)", schedule="[Schedule]\nrun_after = y\n")
    make_app("y", "print(1)", schedule="[Schedule]\nrun_after = x\n")
    reg.start()
    # o ciclo x<->y é quebrado: pelo menos uma das arestas é removida, sem loop infinito
    edges = len(reg.apps["x"].run_after) + len(reg.apps["y"].run_after)
    assert edges <= 1
    warned = (any("ciclo" in w for w in reg.apps["x"].schedule_warnings)
              or any("ciclo" in w for w in reg.apps["y"].schedule_warnings))
    assert warned
