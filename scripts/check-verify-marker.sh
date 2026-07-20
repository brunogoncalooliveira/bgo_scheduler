#!/usr/bin/env bash
# Hook "Stop": bloqueia terminar a resposta se src/bgo_scheduler mudou depois
# da ultima verificacao bem-sucedida de scripts/verify-reinstall-restart.ps1
# (ver .claude/settings.json). O marcador .claude/last-verify.txt e gravado
# pelo proprio script no fim de uma corrida bem-sucedida.
set -u
cd "/c/Users/asus/Desktop/bgo_scheduler" 2>/dev/null || exit 0

SRC="src/bgo_scheduler"
MARKER=".claude/last-verify.txt"
[ -d "$SRC" ] || exit 0

if [ ! -f "$MARKER" ]; then
  newest=$(find "$SRC" -type f 2>/dev/null | head -1)
  if [ -n "$newest" ]; then
    printf '{"decision":"block","reason":"src/bgo_scheduler existe mas scripts/verify-reinstall-restart.ps1 nunca correu nesta arvore de trabalho. Corre-o (testes + build real do wheel + reinstall + restart do tray) antes de terminar."}'
  fi
  exit 0
fi

newer=$(find "$SRC" -type f -newer "$MARKER" 2>/dev/null | head -1)
if [ -n "$newer" ]; then
  printf '{"decision":"block","reason":"src/bgo_scheduler mudou (ex.: %s) depois da ultima verificacao. Corre scripts/verify-reinstall-restart.ps1 antes de terminar."}' "$newer"
fi
exit 0
