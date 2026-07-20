"""Lazy start: o histórico de cada app carrega em segundo plano.

Com muitas apps/logs, ler o .jsonl de histórico de cada app de forma síncrona
durante Registry.start() (tudo dentro do mesmo lock que /api/state usa)
atrasava visivelmente a entrada no dashboard. As AppRuntime arrancam sem
histórico (history_loaded=False) e cada uma carrega o seu em paralelo, num
pool de threads, fundindo com o que entretanto já tenha corrido.
"""

import json
import threading
import time
from unittest.mock import patch

from bgo_scheduler.scheduler_core import AppDef, AppRuntime
from conftest import wait_until


def _rt_lazy(reg, app_dir, name="lazy"):
    return AppRuntime(AppDef(name, app_dir, "py", app_dir / "main.py"), reg, lazy_history=True)


def test_lazy_ctor_skips_disk_read(make_app, make_registry):
    reg = make_registry()
    d = make_app("l1", "print(1)")
    hist_path = reg.config.history_dir / "l1.jsonl"
    hist_path.write_text('{"start": "x", "status": "ok"}\n', encoding="utf-8")
    rt = _rt_lazy(reg, d, "l1")
    assert rt.history_loaded is False
    assert list(rt.history) == []
    assert rt.last is None


def test_eager_ctor_unchanged(make_app, make_registry):
    """Sem lazy_history, o comportamento é o de sempre: histórico já carregado."""
    reg = make_registry()
    d = make_app("l2", "print(1)")
    hist_path = reg.config.history_dir / "l2.jsonl"
    hist_path.write_text('{"start": "x", "status": "ok"}\n', encoding="utf-8")
    rt = AppRuntime(AppDef("l2", d, "py", d / "main.py"), reg)
    assert rt.history_loaded is True
    assert len(rt.history) == 1
    assert rt.last == {"start": "x", "status": "ok"}


def test_load_history_populates_and_flags_loaded(make_app, make_registry):
    reg = make_registry()
    d = make_app("l3", "print(1)")
    entries = [{"start": "a", "status": "ok"}, {"start": "b", "status": "erro"}]
    hist_path = reg.config.history_dir / "l3.jsonl"
    hist_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    rt = _rt_lazy(reg, d, "l3")
    rt._load_history()
    assert rt.history_loaded is True
    assert list(rt.history) == entries
    assert rt.last == entries[-1]


def test_load_history_no_file_flags_loaded_empty(make_app, make_registry):
    reg = make_registry()
    d = make_app("l4", "print(1)")
    rt = _rt_lazy(reg, d, "l4")
    rt._load_history()
    assert rt.history_loaded is True
    assert list(rt.history) == []
    assert rt.last is None


def test_load_history_merges_with_live_run_appended_first(make_app, make_registry):
    """A corrida exata do lazy start: a app corre (e escreve em memória) antes
    do carregamento em segundo plano terminar. O disco só tem passado, por
    isso tem de ficar sempre ANTES do que já está em memória — nunca apagar
    nem desordenar a execução em curso."""
    reg = make_registry()
    d = make_app("l5", "print(1)")
    old_entries = [{"start": "2026-01-01T00:00:00", "status": "ok"},
                   {"start": "2026-01-02T00:00:00", "status": "ok"}]
    hist_path = reg.config.history_dir / "l5.jsonl"
    hist_path.write_text("\n".join(json.dumps(e) for e in old_entries) + "\n", encoding="utf-8")
    rt = _rt_lazy(reg, d, "l5")

    live_entry = {"start": "2026-01-03T00:00:00", "status": "ok"}
    with rt._state_lock:
        rt.last = live_entry
        rt.history.append(live_entry)

    rt._load_history()
    assert list(rt.history) == old_entries + [live_entry]
    assert rt.last == live_entry
    assert rt.history_loaded is True


def test_registry_start_does_not_block_on_history_load(make_app, make_registry):
    """A propriedade central do lazy start: Registry.start() não pode ficar à
    espera do carregamento do histórico, senão o arranque continua lento com
    muitas apps/logs — exatamente o problema reportado."""
    reg = make_registry()
    make_app("slow", "print(1)")
    gate = threading.Event()
    orig = AppRuntime._load_history

    def blocked(self):
        gate.wait(timeout=5)
        return orig(self)

    try:
        with patch.object(AppRuntime, "_load_history", blocked):
            t0 = time.monotonic()
            reg.start()
            elapsed = time.monotonic() - t0
        assert elapsed < 2, f"Registry.start() esperou pelo histórico ({elapsed:.2f}s)"
    finally:
        gate.set()   # liberta a thread de fundo mesmo que a asserção falhe


def test_registry_start_apps_usable_before_history_loads(make_app, make_registry):
    """Enquanto o histórico carrega em segundo plano, a app já existe, já está
    agendável e o snapshot() (o que o /api/state expõe) já reflete isso —
    apenas o histórico/last ficam a "a carregar" até o loader terminar."""
    reg = make_registry()
    make_app("a1", "print(1)")
    gate = threading.Event()
    orig = AppRuntime._load_history

    def blocked(self):
        gate.wait(timeout=5)
        return orig(self)

    try:
        with patch.object(AppRuntime, "_load_history", blocked):
            reg.start()
            rt = reg.apps["a1"]
            assert rt.history_loaded is False
            snap = reg.snapshot()
            app_snap = next(a for a in snap["apps"] if a["name"] == "a1")
            assert app_snap["history_loaded"] is False
            assert app_snap["last"] is None
    finally:
        gate.set()
    assert wait_until(lambda: rt.history_loaded, timeout=5)


def test_rescan_added_app_still_loads_eagerly(make_app, make_registry):
    """rescan() só constrói apps NOVAS (normalmente poucas) — mantém-se
    síncrono; só o arranque completo (Registry.start) é que é lazy."""
    reg = make_registry()
    reg.start()
    make_app("new1", "print(1)")
    reg.rescan()
    assert reg.apps["new1"].history_loaded is True
