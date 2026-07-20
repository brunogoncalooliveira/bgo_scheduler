"""Regressão de duas funcionalidades novas do dashboard:

1) displayName: o alias (nickname) substitui o nome da pasta quando definido
   — mas o nome da pasta continua a ser usado internamente (selectApp, API).
2) sortDisabledLast: apps desativadas aparecem sempre no fim da lista da
   esquerda, preservando (sort estável) a ordem por dependência dentro de
   cada grupo.

Os testes extraem as funções puras do dashboard.html e exercitam-nas com Node.
"""

import subprocess

from conftest import DASHBOARD_HTML, NODE_BIN, extract_js_function, needs_node


@needs_node
def test_display_name(tmp_path):
    src = DASHBOARD_HTML.read_text(encoding="utf-8")
    fn = extract_js_function("displayName", src)
    harness = fn + r"""
function check(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

check(displayName({ name: "pasta1", alias: "Extração diária" }) === "Extração diária",
      "com alias -> mostra o alias");
check(displayName({ name: "pasta1", alias: null }) === "pasta1",
      "sem alias -> mostra o nome da pasta");
check(displayName({ name: "pasta1", alias: "" }) === "pasta1",
      "alias vazio -> mostra o nome da pasta");
check(displayName({ name: "pasta1", alias: "   " }) === "pasta1",
      "alias só com espaços -> mostra o nome da pasta");
check(displayName({ name: "pasta1", alias: "  Bonito  " }) === "Bonito",
      "alias com espaços à volta -> mostra o alias já sem espaços");
check(displayName({ name: "pasta1" }) === "pasta1",
      "campo alias ausente -> mostra o nome da pasta");
console.log("ok");
"""
    _run_node(tmp_path, "displayname.mjs", harness)


@needs_node
def test_sort_disabled_last(tmp_path):
    src = DASHBOARD_HTML.read_text(encoding="utf-8")
    fn = extract_js_function("sortDisabledLast", src)
    harness = fn + r"""
function check(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
const it = (name, enabled) => ({ app: { name, enabled }, depth: 0 });
const names = arr => arr.map(x => x.app.name);

// apps desativadas vão para o fim
let input = [it("a", true), it("b", false), it("c", true)];
let out = sortDisabledLast(input);
check(names(out).join(",") === "a,c,b", "'b' (desativada) vai para o fim");

// todas ativas -> ordem original mantida
out = sortDisabledLast([it("x", true), it("y", true)]);
check(names(out).join(",") === "x,y", "todas ativas -> ordem original");

// todas desativadas -> ordem original mantida entre si
out = sortDisabledLast([it("x", false), it("y", false)]);
check(names(out).join(",") === "x,y", "todas desativadas -> ordem original entre si");

// sort estável: várias desativadas mantêm a SUA ordem relativa no fim
out = sortDisabledLast([it("a", false), it("b", true), it("c", false), it("d", true)]);
check(names(out).join(",") === "b,d,a,c",
      "estável: ativas primeiro (b,d), desativadas depois na ordem original (a,c)");

// não muta o array de entrada
input = [it("a", false), it("b", true)];
const inputCopy = names(input).join(",");
sortDisabledLast(input);
check(names(input).join(",") === inputCopy, "não deve mutar o array de entrada");

console.log("ok");
"""
    _run_node(tmp_path, "sortdisabled.mjs", harness)


def _run_node(tmp_path, filename, harness):
    script = tmp_path / filename
    script.write_text(harness, encoding="utf-8")
    r = subprocess.run([NODE_BIN, str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "node falhou:\n" + r.stdout + r.stderr
    assert "ok" in r.stdout
