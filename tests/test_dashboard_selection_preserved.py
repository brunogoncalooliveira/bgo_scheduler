"""Regressão: a seleção de texto no dashboard não pode perder-se a cada refresh.

Duas causas distintas já apanharam este bug, e este ficheiro guarda-se contra
as duas:

1) Uma única chave para uma lista inteira (cards de apps, tabela da visão
   geral, tabela de histórico): qualquer item a mudar de estado reconstruía
   TODOS os nós da lista — incluindo os que não mudaram — desmarcando
   qualquer texto selecionado neles. A correção (reconcileList) faz
   reconciliação "keyed": só o item cuja PRÓPRIA chave mudou é reconstruído;
   a identidade dos nós DOM dos restantes tem de se manter (===) entre
   rondas — é essa identidade que o browser usa para saber que uma seleção
   continua válida.
2) Um caractere de controlo perdido (U+0001) coado para dentro do
   dashboard.html (num `.join("\\x01")` que devia ser `.join("")`) — no
   editor deste ficheiro, escapes mal geridos podem inserir bytes de controlo
   em vez do texto pretendido. Isto não impede a sintaxe de validar, por isso
   nenhum `node --check` o apanha; um scan direto ao ficheiro sim.
"""

import subprocess

from conftest import DASHBOARD_HTML, NODE_BIN, extract_js_function, needs_node


def test_no_stray_control_characters_in_dashboard():
    src = DASHBOARD_HTML.read_text(encoding="utf-8")
    bad = [(i, ch) for i, ch in enumerate(src) if ord(ch) < 32 and ch not in "\n\r\t"]
    assert not bad, (
        "dashboard.html tem caractere(s) de controlo inesperado(s) — "
        + ", ".join(f"U+{ord(ch):04X} no offset {i}" for i, ch in bad[:10])
    )


@needs_node
def test_reconcile_list_preserves_identity_of_unchanged_items(tmp_path):
    src = DASHBOARD_HTML.read_text(encoding="utf-8")
    fn = extract_js_function("reconcileList", src)
    harness = fn + r"""
function check(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

/* DOM mínimo só com a superfície que reconcileList usa. */
class FakeNode {
  constructor(id) { this.id = id; this.parent = null; }
  get isConnected() { return this.parent !== null; }
  replaceWith(other) {
    if (!this.parent) return;
    const c = this.parent;
    const i = c.children.indexOf(this);
    c.children[i] = other;
    other.parent = c;
    this.parent = null;
  }
  remove() {
    if (!this.parent) return;
    const c = this.parent;
    c.children.splice(c.children.indexOf(this), 1);
    this.parent = null;
  }
}
class FakeContainer {
  constructor() { this.children = []; }
  get lastChild() { return this.children[this.children.length - 1]; }
  insertBefore(node, ref) {
    const cur = this.children.indexOf(node);
    if (cur !== -1) this.children.splice(cur, 1);
    const idx = ref ? this.children.indexOf(ref) : -1;
    if (ref && idx === -1) throw new Error("ref não está no container");
    if (idx === -1) this.children.push(node); else this.children.splice(idx, 0, node);
    node.parent = this;
  }
}

const built = {};   // id -> quantas vezes build(id) foi chamado
function build(id) { built[id] = (built[id] || 0) + 1; return new FakeNode(id); }

const container = new FakeContainer();
const cache = new Map();
const it = (id, key) => ({ id, key });

// ronda 1: três items novos -> todos construídos
reconcileList(container, cache, [it("a", "k1"), it("b", "k1"), it("c", "k1")], build);
check(built.a === 1 && built.b === 1 && built.c === 1, "primeira ronda constrói todos os items novos");
check(container.children.map(n => n.id).join(",") === "a,b,c", "ordem inicial correta");
const nodeA1 = cache.get("a").el, nodeC1 = cache.get("c").el;

// ronda 2: SÓ "b" muda de chave — é o cenário exato do bug (uma app a
// mudar de estado não pode tocar nas outras)
reconcileList(container, cache, [it("a", "k1"), it("b", "k2"), it("c", "k1")], build);
check(built.a === 1, "'a' inalterada nunca é reconstruída — preserva a seleção nela");
check(built.c === 1, "'c' inalterada nunca é reconstruída — preserva a seleção nela");
check(built.b === 2, "só o item cuja chave mudou é reconstruído");
check(cache.get("a").el === nodeA1, "identidade do nó de 'a' mantém-se (===) entre rondas");
check(cache.get("c").el === nodeC1, "identidade do nó de 'c' mantém-se (===) entre rondas");
check(container.children.map(n => n.id).join(",") === "a,b,c", "ordem mantida após atualizar só 'b'");

// ronda 3: nada muda -> nada é reconstruído, nenhuma chamada extra a build
const totalBefore = built.a + built.b + built.c;
reconcileList(container, cache, [it("a", "k1"), it("b", "k2"), it("c", "k1")], build);
check(built.a + built.b + built.c === totalBefore, "nada muda -> nenhum item é reconstruído");

// ronda 4: "b" é removida, "d" é nova -> só isso muda
reconcileList(container, cache, [it("a", "k1"), it("c", "k1"), it("d", "k1")], build);
check(built.a === 1 && built.c === 1, "remover/acrescentar noutro sítio não toca em 'a'/'c'");
check(built.d === 1, "'d' nova é construída");
check(!cache.has("b"), "'b' removida sai da cache");
check(container.children.map(n => n.id).join(",") === "a,c,d", "ordem final correta após remoção/adição");
check(cache.get("a").el === nodeA1, "identidade de 'a' sobrevive mesmo a remoções/adições noutros items");

console.log("ok");
"""
    script = tmp_path / "reconcile.mjs"
    script.write_text(harness, encoding="utf-8")
    r = subprocess.run([NODE_BIN, str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "node falhou:\n" + r.stdout + r.stderr
    assert "ok" in r.stdout


@needs_node
def test_app_card_key_only_changes_for_the_app_that_changed(tmp_path):
    """Reproduz o cenário real: 3 apps, só o estado de UMA muda entre duas
    rondas (ex.: começou a correr) — a chave das outras duas tem de ficar
    exatamente igual, senão reconcileList rebuilda-as sem necessidade."""
    src = DASHBOARD_HTML.read_text(encoding="utf-8")
    status_fn = extract_js_function("statusInfo", src)
    key_fn = extract_js_function("appCardKey", src)
    harness = "let selected = null;\n" + status_fn + "\n" + key_fn + r"""
function check(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

function mkApp(name, overrides) {
  return Object.assign({
    name, kind: "py", enabled: true, warnings: [], running: false, queued: false,
    last: { end: "t", status: "ok" }, sleeping: false, schedule_mode: "interval",
    next_run: "2026-01-01T00:00:00", cron: null, interval_minutes: 60, run_after: [],
  }, overrides);
}

const up = mkApp("up");
const down = mkApp("down", { run_after: ["up"] });
const other = mkApp("other");

const keysBefore = {
  up: appCardKey(up, 0), down: appCardKey(down, 1), other: appCardKey(other, 0),
};

// só "up" passa a estar a correr
const upRunning = mkApp("up", { running: true });
const keysAfter = {
  up: appCardKey(upRunning, 0), down: appCardKey(down, 1), other: appCardKey(other, 0),
};

check(keysBefore.up !== keysAfter.up, "'up' passou a correr -> a sua própria chave muda");
check(keysBefore.down === keysAfter.down, "'down' não mudou -> chave igual (não pode ser reconstruída)");
check(keysBefore.other === keysAfter.other, "'other' não mudou -> chave igual (não pode ser reconstruída)");
console.log("ok");
"""
    script = tmp_path / "appcardkey.mjs"
    script.write_text(harness, encoding="utf-8")
    r = subprocess.run([NODE_BIN, str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "node falhou:\n" + r.stdout + r.stderr
    assert "ok" in r.stdout
