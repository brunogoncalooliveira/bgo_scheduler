"""Regressão: o histórico de execuções mostra só HIST_VISIBLE_ROWS linhas,
com scroll para o resto (altura calculada a partir das linhas reais do DOM,
não um valor de pixels fixo — funciona com qualquer tamanho de letra ou
célula que quebre para duas linhas).

sizeHistoryBody() lê offsetHeight (layout real do browser), por isso o teste
usa um stub mínimo de #hist-body (querySelector/querySelectorAll/style) em
vez de um browser real — testa o ALGORITMO, não o layout em si.
"""

import re
import subprocess

from conftest import DASHBOARD_HTML, NODE_BIN, extract_js_function, needs_node


@needs_node
def test_size_history_body_caps_at_visible_rows(tmp_path):
    src = DASHBOARD_HTML.read_text(encoding="utf-8")
    m = re.search(r"const HIST_VISIBLE_ROWS = (\d+);", src)
    assert m, "HIST_VISIBLE_ROWS não encontrado em dashboard.html"
    n_visible = m.group(1)
    fn = extract_js_function("sizeHistoryBody", src)

    harness = f"const HIST_VISIBLE_ROWS = {n_visible};\n" + fn + r"""
function check(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

function makeBody(theadH, rowHeights) {
  const style = {};
  return {
    style,
    querySelector: sel => sel === "thead" ? { offsetHeight: theadH } : null,
    querySelectorAll: sel => sel === "tbody tr" ? rowHeights.map(h => ({ offsetHeight: h })) : [],
  };
}

const got = () => body.style.maxHeight;

// menos linhas que HIST_VISIBLE_ROWS -> soma TODAS (cabe tudo, sem scroll)
let body = makeBody(26, [30, 30, 30]);
sizeHistoryBody(body);
check(got() === "116px", "3 linhas (< limite): thead + as 3 -> 116px, obteve " + got());

// exatamente HIST_VISIBLE_ROWS linhas -> soma todas
body = makeBody(26, [30, 30, 30, 30, 30]);
sizeHistoryBody(body);
check(got() === "176px", "5 linhas (== limite): thead + as 5 -> 176px, obteve " + got());

// mais linhas que HIST_VISIBLE_ROWS -> só as primeiras N contam, resto ignorado
// (linha 3 propositadamente mais alta, para confirmar que não assume altura uniforme)
body = makeBody(26, [30, 30, 50, 30, 30, 30, 30, 30]);
sizeHistoryBody(body);
check(got() === "196px", "8 linhas (> limite): só as 5 primeiras -> 196px, obteve " + got());

// sem linhas (histórico vazio) -> sem limite de altura (nada a mostrar)
body = makeBody(26, []);
sizeHistoryBody(body);
check(got() === "", "sem linhas -> maxHeight limpo, obteve " + JSON.stringify(got()));

console.log("ok");
"""
    script = tmp_path / "sizehistory.mjs"
    script.write_text(harness, encoding="utf-8")
    r = subprocess.run([NODE_BIN, str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "node falhou:\n" + r.stdout + r.stderr
    assert "ok" in r.stdout
