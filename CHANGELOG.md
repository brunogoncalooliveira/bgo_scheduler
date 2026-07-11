# Changelog

Todas as alterações relevantes deste projeto são registadas aqui.

O formato baseia-se em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o projeto segue [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não publicado]

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

[Não publicado]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.6...HEAD
[1.9.6]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.5...v1.9.6
[1.9.5]: https://github.com/brunogoncalooliveira/bgo_scheduler/compare/v1.9.4...v1.9.5
[1.9.4]: https://github.com/brunogoncalooliveira/bgo_scheduler/releases/tag/v1.9.4
