# bgo_scheduler

Scheduler de apps para Windows com system tray (Win32/ctypes), dashboard web
live (stdlib, sem dependências) e logs em formato Grafana Loki. Fonte em
`src/bgo_scheduler/`; testes em `tests/` (pytest; alguns testes do JS do
dashboard correm via Node, ver `tests/conftest.py`).

## Depois de qualquer alteração a `src/bgo_scheduler/`

Corre **sempre** `scripts/verify-reinstall-restart.ps1` antes de considerares
a tarefa terminada. Só testar a partir do source tree (`PYTHONPATH=src`) já
deixou passar dois bugs reais que só apareceram no pacote instalado a sério
(um caractere de controlo perdido, e um processo antigo a continuar a servir
o dashboard depois de um "reinstall" mal verificado) — ver CHANGELOG 1.9.15.

O script corre pytest + ruff, compila a wheel real, para o
`bgo-scheduler-tray` a correr (se estiver), desinstala, instala a wheel nova,
e confirma pela resposta real de `/api/state` (não só pela existência de um
processo) que a versão nova ficou mesmo em execução, antes de reiniciar o
tray. Não mexe em git — commit/tag/push continuam a ser feitos à parte
(mensagem de commit depende do que mudou; ver memória `publish-without-asking`
e `maintain-changelog`).

**Exceção:** não é preciso correr o script para alterações só em testes,
documentação, ou noutro código sem superfície de execução própria (nada em
`src/bgo_scheduler/` mudou).
