# Changelog

Todas as alterações relevantes deste projeto são registadas aqui.

O formato baseia-se em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o projeto segue [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não publicado]

## [1.9.14] - 2026-07-14

### Corrigido
- **"correr após" já não parecia coexistir com intervalo/cron.** run_after já
  tinha sempre prioridade na execução real (`_compute_next_run`), mas isso não
  era visível: (1) o `schedule_mode` reportado ao dashboard dava prioridade ao
  cron sobre o encadeamento quando uma app tinha os dois configurados, mostrando
  "cron ..." para uma app que na prática só corria encadeada; (2) o editor de
  Configuração deixava "modo"/intervalo/cron editáveis ao mesmo tempo que
  "correr após", sugerindo que ambos se aplicavam. Agora o modo reportado dá
  sempre prioridade a "chain", e o editor desativa visualmente intervalo/cron
  (com uma nota) enquanto "correr após" tiver um valor.

### Adicionado
- **Dependência visual entre apps encadeadas.** No painel esquerdo, uma app com
  "correr após" aparece agora logo a seguir à(s) app(s) de que depende,
  indentada e com um conector (↳) — incluindo cadeias com vários níveis — em
  vez de aparecer misturada na lista sem indicação visual da relação.
- **Lazy start do histórico.** Com muitas apps/muitos logs, o arranque
  demorava a abrir o dashboard: `Registry.start()` lia o `.jsonl` de histórico
  de cada app de forma síncrona, dentro do mesmo lock que `/api/state` usa.
  As apps arrancam agora sem histórico e cada uma carrega o seu em paralelo,
  em segundo plano; o dashboard mostra "a carregar histórico…" nesse intervalo
  e preenche automaticamente quando os dados chegam (sem a UI ficar bloqueada
  a arrancar).

## [1.9.13] - 2026-07-14

### Corrigido
- **«Seguir novas linhas» do log deixava de atualizar.** O visualizador de logs
  detetava linhas novas pelo **número de linhas**, mas o `/api/logs` só devolve
  as últimas 300 e o `.log` da app acumula entre execuções — acima de 300 o total
  fica preso em 300, as linhas novas empurram as antigas sem mudar o total, e o
  visualizador nunca as mostrava. Passa a usar uma assinatura do tail (timestamps
  e última mensagem) que muda sempre que chega uma linha nova, mesmo no limite.

## [1.9.12] - 2026-07-14

### Corrigido
- **Acentos corrompidos quando um `.bat` mistura codificações.** O fix de 1.9.11
  descodificava toda a saída de um `.bat` na code page da consola (cp850). Mas
  um `.bat` que chama, p. ex., PowerShell com
  `[Console]::OutputEncoding = UTF8` (ou `python -u`) produz um stream **misto**:
  linhas do `echo` do cmd em cp850 e linhas do PowerShell em UTF-8. Uma
  codificação fixa corrompia sempre metade (`SINCRONIZAÇÃO` aparecia como
  `SINCRONIZA├ç├âO`). A saída passa a ser lida em binário e descodificada
  **linha-a-linha**: UTF-8 quando válido (auto-validável), recuando para a code
  page da consola só quando não é. Aplica-se a apps `.py` e `.bat`.

## [1.9.11] - 2026-07-13

### Corrigido
- **Seleção de texto no dashboard deixa de se perder a cada atualização.** O
  painel refrescava de 2 em 2 segundos reconstruindo os tiles, a lista de apps
  e outras zonas do DOM mesmo quando nada mudava, o que desmarcava qualquer
  texto selecionado. Estas zonas passam a reconstruir-se só quando o conteúdo
  muda de facto; as contagens decrescentes são atualizadas no lugar, sem tocar
  no DOM quando o texto é igual.
- **Acentos corrompidos na saída de apps `.bat`.** O output de um ficheiro
  `.bat` era descodificado com a code page ANSI (cp1252) em vez da code page de
  saída da consola (a OEM, p. ex. cp850 em PT-PT), corrompendo `ç`, `ã`, `á`…
  Passa a usar-se `GetConsoleOutputCP()`, que corresponde à consola que a app
  herda.

### Alterado
- Etiqueta do visualizador de logs no dashboard: «Log (Grafana Loki · …)» passa
  a «Logs (logs/&lt;app&gt;.log)».

## [1.9.10] - 2026-07-12

### Alterado
- Timestamps dos logs, do histórico e de `next_run` passam de UTC (`…Z`) para
  **hora local com offset explícito** (ex.: `2026-07-12T21:06:01+01:00`).
  Continua RFC3339 válido para o Promtail/Grafana, mas os ficheiros e o log
  viewer passam a mostrar o relógio local (antes apareciam 1 h atrás em
  Portugal no verão). Entradas antigas com `Z` continuam a ser interpretadas.

## [1.9.9] - 2026-07-11

### Corrigido
- **Causa raiz das janelas de consola a piscar** (finalmente identificada e
  reproduzida): o tray corre em `pythonw.exe` e as apps herdavam esse
  interpretador. O `pythonw` é do subsistema GUI — nunca se liga a nenhuma
  consola, nem à oculta do scheduler — pelo que os programas de consola
  chamados pela app (git, cmd, outro python…) ficavam sem consola para herdar
  e o Windows abria uma janela nova e visível por cada um. As apps passam a
  correr no `python.exe` ao lado do interpretador resolvido (também quando
  `python_exe` aponta para um `pythonw`), herdando a consola oculta — e os
  netos idem, sem janelas. O output continua capturado por pipes, como antes.

## [1.9.8] - 2026-07-11

### Adicionado
- O dashboard mostra a **versão do scheduler em execução** no cabeçalho (e o
  `/api/state` devolve `version`). Permite confirmar num relance que uma
  atualização está mesmo a correr — a versão do `pip show` é a instalada em
  disco, não necessariamente a do processo ativo.

## [1.9.7] - 2026-07-11

### Corrigido
- Janelas de consola a piscar quando uma app lança programas de consola
  (git, cmd, python…), **mesmo em máquinas onde a 1.9.6 não chegava** (ex.:
  Windows Terminal como terminal por omissão, que pode mostrar a janela na
  criação de cada sessão de consola). O scheduler passa a alocar **uma única
  consola oculta no arranque** (tray/pythonw) que as apps e os netos herdam —
  nenhuma sessão de consola nova é criada por execução, logo não há nada para
  piscar. Em modo consola (`bgo-scheduler`), os filhos herdam a consola do
  scheduler e ganham grupo de processos próprio (um Ctrl+C no scheduler já não
  mata as apps).

## [1.9.6] - 2026-07-11

### Corrigido
- Janelas de consola (DOS) a abrir/fechar quando uma app lança **outros
  programas** (netos de processo). O `CREATE_NO_WINDOW` só escondia a consola
  do processo lançado diretamente; agora a app recebe uma consola própria mas
  **oculta** (`CREATE_NEW_CONSOLE` + `SW_HIDE`), que os netos herdam sem abrir
  janela. O stdout/stderr continua a ser capturado.

### Alterado
- Versão passa a ter **fonte única** em `src/bgo_scheduler/__init__.py`; o
  `pyproject.toml` lê-a via `dynamic = ["version"]`.

### Adicionado
- Badges no README (CI, versão PyPI, Python, licença, Ruff).
- Testes de regressão para a supressão de janelas (`tests/test_no_window.py`).

## [1.9.5] - 2026-07-11

### Adicionado
- Publicação automática: workflow `release.yml` que, ao criar uma tag `v*`,
  constrói o wheel/sdist, cria a Release no GitHub e publica no **PyPI** via
  Trusted Publishing (OIDC, sem tokens).
- Disponível no PyPI: `pip install bgo-scheduler`.
- README: secção **Objetivos**, galeria de **screenshots** e secção **To Do**
  (multi-língua e cross-platform).

## [1.9.4] - 2026-07-11

### Adicionado
- Primeira versão pública. Scheduler de apps para Windows com system tray
  nativo (Win32/ctypes), dashboard web live (stdlib) e logs em formato Grafana
  Loki. **Zero dependências de runtime.** Licença MIT.
- Funcionalidades: agendamento por intervalo e cron, encadeamento de apps
  (`run_after`), sleep hours transversais e por app, concorrência
  (`max_parallel`), notificações de erro e mensagens de sucesso/warning,
  interpretador Python por app, histórico persistente, e edição da
  configuração no dashboard.

[Não publicado]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.14...HEAD
[1.9.14]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.13...v1.9.14
[1.9.13]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.12...v1.9.13
[1.9.12]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.11...v1.9.12
[1.9.11]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.10...v1.9.11
[1.9.10]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.9...v1.9.10
[1.9.9]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.8...v1.9.9
[1.9.8]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.7...v1.9.8
[1.9.7]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.6...v1.9.7
[1.9.6]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.5...v1.9.6
[1.9.5]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.4...v1.9.5
[1.9.4]: https://github.com/brunogoncalooliveira/bgo_scheduler/releases/tag/v1.9.4
