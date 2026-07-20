"""Regressão das funcionalidades novas do dashboard ligadas a "correr após".

1) chainActive: fonte única para decidir se uma app está encadeada — usada no
   editor de agendamento para desativar intervalo/cron (o backend já dava
   sempre prioridade a run_after em _compute_next_run; o editor deixava-os
   editáveis ao mesmo tempo, sugerindo que ambos se aplicavam).
2) orderAppsForChainView: ordena/indenta a lista de apps (painel esquerdo)
   para mostrar visualmente a dependência de uma app "correr após" com a(s)
   app(s) a montante.
3) statusInfo: distingue "histórico ainda a carregar" (lazy start) de
   "nunca correu", para o dashboard não mentir durante o carregamento em
   segundo plano do histórico.

Os testes extraem as funções puras do dashboard.html e exercitam-nas com Node.
"""

import subprocess

from conftest import DASHBOARD_HTML, NODE_BIN, extract_js_function, needs_node


@needs_node
def test_chain_active(tmp_path):
    src = DASHBOARD_HTML.read_text(encoding="utf-8")
    fn = extract_js_function("chainActive", src)
    harness = fn + r"""
function check(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

check(chainActive("") === false, "vazio -> inativo");
check(chainActive("   ") === false, "só espaços -> inativo");
check(chainActive(undefined) === false, "undefined -> inativo");
check(chainActive("up1") === true, "um nome -> ativo");
check(chainActive("up1, up2") === true, "vários nomes -> ativo");
check(chainActive("  up1  ") === true, "espaços à volta -> ativo");
console.log("ok");
"""
    _run_node(tmp_path, "chainactive.mjs", harness)


@needs_node
def test_order_apps_for_chain_view(tmp_path):
    src = DASHBOARD_HTML.read_text(encoding="utf-8")
    fn = extract_js_function("orderAppsForChainView", src)
    harness = fn + r"""
function check(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
const names = ordered => ordered.map(o => o.app.name);
const depths = ordered => ordered.map(o => o.depth);
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// sem dependências: ordem original preservada, tudo em depth 0
let r = orderAppsForChainView([{ name: "a", run_after: [] }, { name: "b", run_after: [] }]);
check(eq(names(r), ["a", "b"]), "ordem original preservada sem dependências");
check(depths(r).every(d => d === 0), "sem run_after -> todas em depth 0");

// cadeia simples: down aparece logo a seguir a up, indentada
r = orderAppsForChainView([
  { name: "down", run_after: ["up"] },
  { name: "up", run_after: [] },
]);
check(eq(names(r), ["up", "down"]), "down aparece logo a seguir a up (não pela ordem original)");
check(eq(depths(r), [0, 1]), "down fica indentada (depth 1)");

// cadeia multi-nível a -> b -> c
r = orderAppsForChainView([
  { name: "c", run_after: ["b"] },
  { name: "a", run_after: [] },
  { name: "b", run_after: ["a"] },
]);
check(eq(names(r), ["a", "b", "c"]), "cadeia multi-nível em ordem a, b, c");
check(eq(depths(r), [0, 1, 2]), "profundidade cresce um nível por elo");

// múltiplas apps a montante: z não duplica, todas aparecem
r = orderAppsForChainView([
  { name: "x", run_after: [] },
  { name: "y", run_after: [] },
  { name: "z", run_after: ["x", "y"] },
]);
check(names(r).filter(n => n === "z").length === 1, "z não aparece duplicada");
check(r.length === 3, "todas as apps aparecem exatamente uma vez");

check(orderAppsForChainView([]).length === 0, "lista vazia não rebenta");
console.log("ok");
"""
    _run_node(tmp_path, "orderchain.mjs", harness)


@needs_node
def test_status_info_reflects_lazy_history_loading(tmp_path):
    src = DASHBOARD_HTML.read_text(encoding="utf-8")
    fn = extract_js_function("statusInfo", src)
    harness = fn + r"""
function check(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

// lazy start: histórico ainda não chegou do disco -> não é "sem execuções"
check(statusInfo({ running: false, queued: false, last: null, enabled: true, history_loaded: false }).label
      === "a carregar histórico…", "history_loaded=false não pode dizer 'sem execuções'");

// já carregado e continua sem execuções -> mensagem normal
check(statusInfo({ running: false, queued: false, last: null, enabled: true, history_loaded: true }).label
      === "sem execuções", "history_loaded=true e sem last -> sem execuções");

// campo ausente (apps carregadas de forma eager) -> comportamento inalterado
check(statusInfo({ running: false, queued: false, last: null, enabled: true }).label
      === "sem execuções", "history_loaded ausente -> comportamento de sempre");
check(statusInfo({ running: false, queued: false, last: null, enabled: false }).label
      === "desativada", "history_loaded ausente + desativada -> comportamento de sempre");
console.log("ok");
"""
    _run_node(tmp_path, "statusinfo.mjs", harness)


def _run_node(tmp_path, filename, harness):
    script = tmp_path / filename
    script.write_text(harness, encoding="utf-8")
    r = subprocess.run([NODE_BIN, str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "node falhou:\n" + r.stdout + r.stderr
    assert "ok" in r.stdout
