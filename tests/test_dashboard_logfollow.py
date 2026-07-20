"""Regressão do "seguir novas linhas" do dashboard.

A deteção de linhas novas no visualizador de logs não pode usar só o NÚMERO de
linhas: o /api/logs devolve apenas as últimas N e o .log da app acumula entre
execuções, por isso acima de N o count fica preso em N — linhas novas empurram
as antigas mas o total não muda, e as novas nunca apareciam. A assinatura
(logTailKey) tem de mudar quando chega uma linha nova, mesmo no limite.

O teste extrai a função pura logTailKey do dashboard.html e exercita-a com Node.
"""

import re
import subprocess

from conftest import DASHBOARD_HTML, NODE_BIN, extract_js_function, needs_node


@needs_node
def test_log_follow_signature_detects_new_lines(tmp_path):
    src = DASHBOARD_HTML.read_text(encoding="utf-8")
    assert "function logTailKey" in src, "dashboard.html devia definir logTailKey"
    fn = extract_js_function("logTailKey", src)
    # a chave não pode reduzir-se ao número de linhas (o bug original)
    assert not re.search(r"return\s+String\(\s*lines\.length\s*\)", fn), \
        "logTailKey não pode usar só lines.length como chave"

    harness = fn + r"""
function check(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

const N = 300;   // = lines pedido ao /api/logs (o endpoint corta no tail)
const cap = Array.from({length: N}, (_, i) => ({ ts: "2026-07-14T10:00:" + i, msg: "linha " + i }));
// chega UMA linha nova: a janela desliza, comprimento continua N (o caso do bug)
const capNext = cap.slice(1).concat([{ ts: "2026-07-14T10:01:00", msg: "linha nova" }]);
check(cap.length === capNext.length, "pré-condição: mesmo comprimento no limite");
check(logTailKey(cap) !== logTailKey(capNext),
      "linha nova no limite TEM de mudar a chave (senão 'seguir' não atualiza)");

// nada muda -> mesma chave (não re-renderiza, preserva a seleção de texto)
check(logTailKey(cap) === logTailKey(cap.slice()), "tail igual -> chave igual");

// crescimento abaixo do limite -> chave diferente
const a = [{ ts: "t1", msg: "a" }];
const b = [{ ts: "t1", msg: "a" }, { ts: "t2", msg: "b" }];
check(logTailKey(a) !== logTailKey(b), "linha nova (sem limite) muda a chave");

// mesmo comprimento mas última mensagem diferente -> chave diferente
const c = [{ ts: "t1", msg: "x" }];
const d = [{ ts: "t1", msg: "y" }];
check(logTailKey(c) !== logTailKey(d), "última msg diferente muda a chave");

// vazio é estável e não rebenta
check(logTailKey([]) === logTailKey([]), "vazio estável");
console.log("ok");
"""
    script = tmp_path / "logfollow.mjs"
    script.write_text(harness, encoding="utf-8")
    r = subprocess.run([NODE_BIN, str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "node falhou:\n" + r.stdout + r.stderr
    assert "ok" in r.stdout
