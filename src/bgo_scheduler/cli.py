"""Linha de comandos do bgo_scheduler.

    bgo-scheduler                        arranca tray + dashboard
    bgo-scheduler --headless             sem tray (só scheduler + dashboard)
    bgo-scheduler --config C:\\x\\scheduler.ini
    bgo-scheduler --apps-root C:\\pasta1 --apps-root D:\\pasta2

Resolução da configuração: --config > BGO_SCHEDULER_CONFIG > %APPDATA%\\bgo_scheduler.
"""

import argparse
import sys
import threading

from . import __version__
from .config import load_config, resolve_config_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bgo-scheduler",
        description="Scheduler de apps com tray, dashboard live e logs Grafana Loki.",
    )
    parser.add_argument(
        "--config", metavar="INI",
        help="caminho do scheduler.ini (por omissão %%APPDATA%%\\bgo_scheduler\\scheduler.ini)",
    )
    parser.add_argument(
        "--apps-root", metavar="PASTA", action="append", dest="apps_roots",
        help="raiz de apps; repetível; substitui as roots do INI nesta execução",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="corre sem ícone de tray (scheduler + dashboard apenas)",
    )
    parser.add_argument("--version", action="version", version=f"bgo-scheduler {__version__}")
    return parser


def run_headless(config):
    from .scheduler_core import Registry, RulesStore
    from .web_dashboard import start_dashboard

    rules = RulesStore(config.rules_path)
    registry = Registry(config, rules)
    try:
        server = start_dashboard(config, registry, rules)
    except OSError as e:
        print(f"Não foi possível abrir o dashboard em {config.dashboard_url}: {e}", flush=True)
        print("Já existe outra instância do bgo-scheduler a correr?", flush=True)
        sys.exit(1)
    registry.start()
    print(f"bgo_scheduler (headless) — dashboard em {config.dashboard_url}", flush=True)
    print(f"configuração: {config.ini_path}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        registry.stop()
        server.shutdown()


def main(argv=None):
    args = build_parser().parse_args(argv)
    config = load_config(
        config_path=resolve_config_path(args.config),
        apps_roots_override=args.apps_roots,
    )
    if not config.apps_roots:
        print("Aviso: nenhuma raiz de apps configurada.", flush=True)
        print(f"Define 'roots' na secção [Apps] de {config.ini_path}", flush=True)
        print("ou usa --apps-root C:\\pasta\\das\\apps", flush=True)
    if args.headless:
        run_headless(config)
    else:
        from .tray import TrayApp
        TrayApp(config).start()


def main_tray(argv=None):
    """Entry point gui_scripts (sem consola): igual a main, sem --headless."""
    if argv is None:
        argv = [a for a in sys.argv[1:] if a != "--headless"]
    main(argv)


if __name__ == "__main__":
    main()
