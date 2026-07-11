"""Dashboard web live do bgo_scheduler (stdlib http.server, sem dependências).

Endpoints:
    GET  /                   -> dashboard.html
    GET  /api/state          -> estado de todas as apps (execuções, agendamento)
    GET  /api/logs?app=X     -> últimas linhas do log JSON da app
    GET  /api/rules          -> regras de notificação
    POST /api/rules          -> substitui as regras de notificação (JSON)
    POST /api/run?app=X      -> executa a app agora
    POST /api/toggle?app=X   -> {"enabled": true|false} liga/desliga (gravado no schedule.ini)
    POST /api/rescan         -> re-deteta apps nas raízes configuradas
    POST /api/sleep_hours    -> {"enabled", "start", "end"} sleep hours transversais (scheduler.ini)
    POST /api/app_sleep?app=X-> {"mode": inherit|ignore|custom, "start", "end"} sleep hours da app
    POST /api/app_schedule?app=X -> {"mode": interval|cron, "interval_minutes", "cron",
                                     "timeout_minutes", "run_after", "python_exe"} agendamento da app
    POST /api/settings       -> {"host","port","open_on_start","max_parallel","links"} definições globais
"""

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

# dashboard.html é distribuído dentro do wheel como package data
DASHBOARD_HTML = resources.files("bgo_scheduler") / "dashboard.html"

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def make_handler(config, registry, rules):

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "bgo_scheduler"

        # -- helpers -------------------------------------------------------

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host in ALLOWED_HOSTS or host == config.host.lower()

        def _send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, path: Path):
            try:
                body = path.read_bytes()
            except OSError:
                self.send_error(404, "dashboard.html não encontrado")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _query(self):
            parsed = urllib.parse.urlparse(self.path)
            return parsed.path, urllib.parse.parse_qs(parsed.query)

        def _read_body_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 1_000_000:
                raise ValueError("corpo do pedido vazio ou demasiado grande")
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def log_message(self, fmt, *args):  # silencia o access-log na consola
            pass

        # -- rotas -----------------------------------------------------------

        def do_GET(self):
            if not self._host_ok():
                self.send_error(403)
                return
            path, qs = self._query()
            if path in ("/", "/index.html"):
                self._send_html(DASHBOARD_HTML)
            elif path == "/api/state":
                self._send_json(registry.snapshot())
            elif path == "/api/rules":
                self._send_json(rules.get())
            elif path == "/api/logs":
                self._api_logs(qs)
            else:
                self.send_error(404)

        def do_POST(self):
            if not self._host_ok():
                self.send_error(403)
                return
            path, qs = self._query()
            if path == "/api/run":
                app = (qs.get("app") or [""])[0]
                if registry.trigger(app):
                    self._send_json({"ok": True, "msg": f"Execução de {app} pedida"})
                else:
                    self._send_json({"ok": False, "msg": f"App '{app}' desconhecida"}, 404)
            elif path == "/api/toggle":
                self._api_toggle(qs)
            elif path == "/api/rules":
                self._api_rules_post()
            elif path == "/api/rescan":
                r = registry.rescan()
                r["ok"] = True
                r["msg"] = (f"Rescan: +{len(r['added'])} novas, "
                            f"-{len(r['removed'])} removidas, "
                            f"{len(r['updated'])} atualizadas")
                self._send_json(r)
            elif path == "/api/sleep_hours":
                self._api_sleep_hours()
            elif path == "/api/app_sleep":
                self._api_app_sleep(qs)
            elif path == "/api/app_schedule":
                self._api_app_schedule(qs)
            elif path == "/api/settings":
                self._api_settings()
            else:
                self.send_error(404)

        def _api_settings(self):
            try:
                body = self._read_body_json()
            except (ValueError, AttributeError) as e:
                self._send_json({"ok": False, "msg": f"Pedido inválido: {e}"}, 400)
                return
            links = body.get("links")
            roots = body.get("roots")
            ok, msg = registry.update_settings(
                host=body.get("host"), port=body.get("port"),
                open_on_start=body.get("open_on_start"),
                max_parallel=body.get("max_parallel"),
                links=links if isinstance(links, dict) else None,
                roots=roots if isinstance(roots, list) else None)
            self._send_json({"ok": ok, "msg": msg}, 200 if ok else 400)

        def _api_app_schedule(self, qs):
            app = (qs.get("app") or [""])[0]
            try:
                body = self._read_body_json()
            except (ValueError, AttributeError) as e:
                self._send_json({"ok": False, "msg": f"Pedido inválido: {e}"}, 400)
                return
            ok, msg = registry.set_app_schedule(
                app, str(body.get("mode", "interval")),
                interval_minutes=body.get("interval_minutes"),
                cron=str(body.get("cron", "")),
                timeout_minutes=body.get("timeout_minutes"),
                run_after=str(body.get("run_after", "")),
                python_exe=str(body.get("python_exe", "")))
            status = 200 if ok else (404 if "desconhecida" in msg else 400)
            self._send_json({"ok": ok, "msg": msg}, status)

        def _api_sleep_hours(self):
            try:
                body = self._read_body_json()
            except (ValueError, AttributeError) as e:
                self._send_json({"ok": False, "msg": f"Pedido inválido: {e}"}, 400)
                return
            ok, msg = registry.update_sleep_hours(
                bool(body.get("enabled")),
                str(body.get("start", "")), str(body.get("end", "")))
            self._send_json({"ok": ok, "msg": msg}, 200 if ok else 400)

        def _api_app_sleep(self, qs):
            app = (qs.get("app") or [""])[0]
            try:
                body = self._read_body_json()
            except (ValueError, AttributeError) as e:
                self._send_json({"ok": False, "msg": f"Pedido inválido: {e}"}, 400)
                return
            ok, msg = registry.set_app_sleep(
                app, str(body.get("mode", "inherit")),
                str(body.get("start", "")), str(body.get("end", "")))
            status = 200 if ok else (404 if "desconhecida" in msg else 400)
            self._send_json({"ok": ok, "msg": msg}, status)

        # -- implementações ---------------------------------------------------

        def _api_logs(self, qs):
            app = (qs.get("app") or [""])[0]
            try:
                n = min(int((qs.get("lines") or ["200"])[0]), 1000)
            except ValueError:
                n = 200
            safe = app and all(c.isalnum() or c in "._- " for c in app)
            log_path = config.logs_dir / f"{app}.log"
            if not safe or not log_path.exists():
                self._send_json({"app": app, "lines": []})
                return
            try:
                raw = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._send_json({"app": app, "lines": []})
                return
            lines = []
            for line in raw.splitlines()[-n:]:
                try:
                    lines.append(json.loads(line))
                except ValueError:
                    lines.append({"msg": line})
            self._send_json({"app": app, "lines": lines})

        def _api_toggle(self, qs):
            app = (qs.get("app") or [""])[0]
            try:
                body = self._read_body_json()
                enabled = bool(body.get("enabled"))
            except (ValueError, AttributeError) as e:
                self._send_json({"ok": False, "msg": f"Pedido inválido: {e}"}, 400)
                return
            if registry.set_enabled(app, enabled):
                self._send_json({"ok": True, "enabled": enabled,
                                 "msg": "Gravado no schedule.ini da app"})
            else:
                self._send_json({"ok": False, "msg": f"App '{app}' desconhecida"}, 404)

        def _api_rules_post(self):
            try:
                body = self._read_body_json()
                rules.set(body)
            except ValueError as e:
                self._send_json({"ok": False, "msg": str(e)}, 400)
                return
            self._send_json({"ok": True})

    return DashboardHandler


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    # no Windows, SO_REUSEADDR deixaria duas instâncias fazer bind ao mesmo
    # porto sem erro; desligar garante OSError quando o porto está ocupado
    allow_reuse_address = False


def start_dashboard(config, registry, rules) -> ThreadingHTTPServer:
    """Arranca o servidor numa thread daemon. Lança OSError se o porto estiver ocupado."""
    handler = make_handler(config, registry, rules)
    server = DashboardServer((config.host, config.port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="dashboard-http")
    thread.start()
    return server
