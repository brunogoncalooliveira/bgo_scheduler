"""Configuração do bgo_scheduler.

Resolução do scheduler.ini, por ordem:
  1. --config <caminho>            (argumento de linha de comandos)
  2. BGO_SCHEDULER_CONFIG          (variável de ambiente)
  3. %APPDATA%\\bgo_scheduler\\scheduler.ini   (criado no primeiro arranque)

O notification_rules.json e a pasta logs\\ vivem ao lado do INI
(o caminho dos logs pode ser alterado em [Logs] dir).
"""

import configparser
import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

DEFAULT_INI = """\
; Configuração do bgo_scheduler.

[Dashboard]
host = 127.0.0.1
port = 8765
; abrir o dashboard no browser ao iniciar o scheduler
open_on_start = false

[Apps]
; Uma ou mais pastas com apps, UMA POR LINHA (indentadas).
; Cada sub-pasta com main.py (ou main.bat) é uma app.
; Por omissão aponta para esta própria pasta de configuração — basta criares
; aqui sub-pastas com as apps. Podes trocar por outras pastas:
;   roots =
;       C:\\Users\\asus\\Desktop\\bgo_apps
;       D:\\outros_jobs
roots =
    __ROOTS__
; Sub-pastas a ignorar na deteção de apps (separadas por vírgula)
exclude = logs, history, __pycache__, .git, .venv, venv

[Execution]
; Número máximo de apps a executar em simultâneo (0 = sem limite).
; Evita picos quando muitas apps partilham o mesmo horário.
max_parallel = 0

[Logs]
; Pasta dos logs JSON (Grafana Loki). Vazio = pasta "logs" ao lado deste INI.
dir =

[SleepHours]
; Período transversal em que as apps NÃO são executadas automaticamente.
; A execução manual ("Executar agora") não é afetada.
; Suporta janelas que atravessam a meia-noite (ex.: 22:00-07:00).
; Editável no dashboard (clica no indicador "sleep hours" no topo).
; Por app, no respetivo schedule.ini:
;   ignore_sleep_hours = true      -> app crítica, nunca dorme
;   sleep_hours = 23:00-06:00      -> horário próprio (em vez do transversal)
enabled = false
start = 22:00
end = 07:00

[Links]
; Menus de atalho no ícone do tray e no dashboard (Nome = URL)
Hello1 = https://example.com/hello1
Hello2 = https://example.com/hello2
"""


def default_ini_text(config_dir: Path) -> str:
    """Texto do scheduler.ini por omissão, com 'roots' a apontar para config_dir.

    No primeiro arranque, a raiz de apps fica a ser a própria pasta de
    configuração (ex.: %APPDATA%\\bgo_scheduler), pronta a receber sub-pastas.
    """
    return DEFAULT_INI.replace("__ROOTS__", str(config_dir))


def _parse_hhmm(value: str) -> Optional[time]:
    value = (value or "").strip()
    if not value:
        return None
    for sep in (":", "h", "H"):
        if sep in value:
            parts = value.split(sep)
            break
    else:
        parts = [value, "0"]
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
        if 0 <= h <= 23 and 0 <= m <= 59:
            return time(h, m)
    except (ValueError, IndexError):
        pass
    return None


def parse_sleep_window(raw: str):
    """Interpreta 'HH:MM-HH:MM' como SleepHours. Devolve (SleepHours|None, aviso|None)."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if "-" not in raw:
        return None, f"sleep_hours '{raw}' inválido (formato HH:MM-HH:MM)."
    a, b = raw.split("-", 1)
    start, end = _parse_hhmm(a), _parse_hhmm(b)
    if start is None or end is None:
        return None, f"sleep_hours '{raw}' inválido (formato HH:MM-HH:MM)."
    if start == end:
        return None, f"sleep_hours '{raw}': início igual ao fim; ignorado."
    return SleepHours(enabled=True, start=start, end=end), None


@dataclass
class SleepHours:
    """Período diário em que o agendamento automático fica em pausa."""
    enabled: bool = False
    start: Optional[time] = None
    end: Optional[time] = None

    @property
    def valid(self) -> bool:
        return bool(self.enabled and self.start and self.end and self.start != self.end)

    def active_at(self, dt: datetime) -> bool:
        if not self.valid:
            return False
        t = dt.time()
        if self.start <= self.end:
            return self.start <= t < self.end
        # janela que atravessa a meia-noite (ex.: 22:00-07:00)
        return t >= self.start or t < self.end

    def next_end_after(self, dt: datetime) -> Optional[datetime]:
        """Momento em que o período de pausa termina, a partir de dt (se ativo)."""
        if not self.active_at(dt):
            return None
        end_today = dt.replace(hour=self.end.hour, minute=self.end.minute,
                               second=0, microsecond=0)
        if end_today <= dt:
            end_today += timedelta(days=1)
        return end_today

    def as_dict(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now()
        active = self.active_at(now)
        end_dt = self.next_end_after(now) if active else None
        return {
            "enabled": self.valid,
            "start": self.start.strftime("%H:%M") if self.start else None,
            "end": self.end.strftime("%H:%M") if self.end else None,
            "active_now": active,
            "wakes_at": end_dt.strftime("%H:%M") if end_dt else None,
        }


def default_config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / "bgo_scheduler"


def resolve_config_path(cli_config: Optional[str] = None) -> Path:
    if cli_config:
        return Path(cli_config).expanduser().resolve()
    env = os.environ.get("BGO_SCHEDULER_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return default_config_dir() / "scheduler.ini"


@dataclass
class SchedulerConfig:
    config_dir: Path
    ini_path: Path
    apps_roots: list
    exclude: set
    host: str
    port: int
    open_on_start: bool
    links: dict          # nome do menu -> URL
    logs_dir: Path
    rules_path: Path
    history_dir: Path = None
    sleep_hours: SleepHours = field(default_factory=SleepHours)
    max_parallel: int = 0
    warnings: list = field(default_factory=list)
    roots_overridden: bool = field(default=False)

    @property
    def dashboard_url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{host}:{self.port}/"


def _parse_roots(raw: str, base: Path) -> list:
    roots = []
    for chunk in raw.replace(",", "\n").splitlines():
        chunk = chunk.strip().strip('"')
        if not chunk or chunk.startswith((";", "#")):
            continue
        p = Path(chunk).expanduser()
        if not p.is_absolute():
            p = (base / p).resolve()
        if p not in roots:
            roots.append(p)
    return roots


def load_config(config_path: Optional[Path] = None,
                apps_roots_override: Optional[list] = None) -> SchedulerConfig:
    warnings: list = []
    ini_path = Path(config_path) if config_path else resolve_config_path()
    config_dir = ini_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    if not ini_path.exists():
        # primeiro arranque: cria o INI com roots = a própria pasta de config
        ini_path.write_text(default_ini_text(config_dir), encoding="utf-8")

    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # preserva maiúsculas nos nomes dos menus
    try:
        parser.read(ini_path, encoding="utf-8")
    except configparser.Error as e:
        warnings.append(f"scheduler.ini inválido ({e}); a usar valores por omissão.")
        parser = configparser.ConfigParser(interpolation=None)

    host = parser.get("Dashboard", "host", fallback="127.0.0.1").strip()
    try:
        port = parser.getint("Dashboard", "port", fallback=8765)
    except ValueError:
        warnings.append(
            f"[Dashboard] port='{parser.get('Dashboard', 'port', fallback='')}' inválido; a usar 8765.")
        port = 8765
    try:
        open_on_start = parser.getboolean("Dashboard", "open_on_start", fallback=False)
    except ValueError:
        warnings.append("[Dashboard] open_on_start inválido; a assumir false.")
        open_on_start = False

    if apps_roots_override:
        apps_roots = _parse_roots("\n".join(apps_roots_override), Path.cwd())
    else:
        raw = parser.get("Apps", "roots", fallback="")
        # compatibilidade com o formato antigo (root singular)
        if not raw.strip():
            raw = parser.get("Apps", "root", fallback="")
        apps_roots = _parse_roots(raw, config_dir)
    for root in apps_roots:
        if not root.exists():
            warnings.append(f"[Apps] raiz não encontrada: {root}")
        elif not root.is_dir():
            warnings.append(f"[Apps] raiz não é uma pasta: {root}")

    exclude_raw = parser.get("Apps", "exclude", fallback="logs, history, __pycache__, .git, .venv, venv")
    exclude = {p.strip().lower() for p in exclude_raw.split(",") if p.strip()}
    # pastas nossas dentro da config (relevante quando roots = pasta de config)
    exclude.update({"logs", "history", "__pycache__"})

    links = {}
    if parser.has_section("Links"):
        for name, url in parser.items("Links"):
            url = url.strip()
            if url:
                links[name] = url

    logs_raw = parser.get("Logs", "dir", fallback="").strip()
    logs_dir = Path(logs_raw).expanduser() if logs_raw else config_dir / "logs"
    if not logs_dir.is_absolute():
        logs_dir = (config_dir / logs_dir).resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)

    history_dir = config_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    try:
        max_parallel = parser.getint("Execution", "max_parallel", fallback=0)
    except ValueError:
        warnings.append("[Execution] max_parallel inválido; a assumir 0 (sem limite).")
        max_parallel = 0
    if max_parallel < 0:
        max_parallel = 0

    try:
        sh_enabled = parser.getboolean("SleepHours", "enabled", fallback=False)
    except ValueError:
        warnings.append("[SleepHours] enabled inválido; a assumir false.")
        sh_enabled = False
    sh_start = _parse_hhmm(parser.get("SleepHours", "start", fallback=""))
    sh_end = _parse_hhmm(parser.get("SleepHours", "end", fallback=""))
    if sh_enabled and (sh_start is None or sh_end is None):
        warnings.append("[SleepHours] start/end em falta ou inválidos (formato HH:MM); pausa ignorada.")
    elif sh_enabled and sh_start == sh_end:
        warnings.append("[SleepHours] start igual a end; pausa ignorada.")
    sleep_hours = SleepHours(enabled=sh_enabled, start=sh_start, end=sh_end)

    return SchedulerConfig(
        config_dir=config_dir,
        ini_path=ini_path,
        apps_roots=apps_roots,
        exclude=exclude,
        host=host,
        port=port,
        open_on_start=open_on_start,
        links=links,
        logs_dir=logs_dir,
        rules_path=config_dir / "notification_rules.json",
        history_dir=history_dir,
        sleep_hours=sleep_hours,
        max_parallel=max_parallel,
        warnings=warnings,
        roots_overridden=bool(apps_roots_override),
    )
