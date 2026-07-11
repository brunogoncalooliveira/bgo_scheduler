"""Núcleo do bgo_scheduler: deteção de apps, agendamento, execução e regras de notificação.

Cada sub-pasta das raízes de apps com um main.py (ou, em alternativa, main.bat)
é uma app. O agendamento vem do schedule.ini dentro da pasta da app:

    [Schedule]
    enabled = true
    ; agendamento por intervalo:
    interval_minutes = 60
    ; OU agendamento cron (tem prioridade sobre interval_minutes):
    ; cron = 0 9 * * 1-5        <- dias úteis às 09:00 (hora local)
    ; opcional: aborta a execução ao fim de N minutos (0 = sem limite)
    timeout_minutes = 0
    ; opcional: interpretador Python próprio (ex.: venv da app);
    ; caminho absoluto ou relativo à pasta da app
    ; python_exe = .venv\\Scripts\\python.exe
    ; sleep hours (todos editáveis no dashboard):
    ; ignore_sleep_hours = true      -> app crítica, ignora a pausa transversal
    ; sleep_hours = 23:00-06:00      -> horário de pausa próprio da app
    ; encadeamento: corre quando uma app a montante termina com sucesso
    ; (em vez de por intervalo/cron). Vários nomes separados por vírgula:
    ; run_after = extrair, transformar
"""

import configparser
import copy
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .config import SleepHours, _parse_hhmm, _parse_roots, parse_sleep_window
from .cron import CronError, CronSpec
from .loki_logger import get_app_logger, get_scheduler_logger

# evita que cada execução abra uma janela de consola quando o scheduler
# corre sem consola (bgo-scheduler-tray / pythonw)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TAIL_CHARS = 4000       # máximo de output guardado por execução no histórico
STREAM_LINES = 2000     # linhas de stdout/stderr retidas em memória por execução
HISTORY_SIZE = 100
HISTORY_MAX_BYTES = 2 * 1024 * 1024   # compacta o .jsonl acima disto
INITIAL_DELAY_S = 5     # espera antes da primeira execução (deixa o tray arrancar)
STAGGER_S = 2           # desfasamento entre apps na primeira execução


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Deteção de apps e schedule.ini
# ---------------------------------------------------------------------------

@dataclass
class AppDef:
    name: str
    dir: Path
    kind: str    # "py" | "bat"
    entry: Path


@dataclass
class ScheduleCfg:
    enabled: bool = True
    interval_minutes: int = 60
    timeout_minutes: int = 0
    cron: Optional[str] = None
    python_exe: Optional[str] = None
    ignore_sleep_hours: bool = False
    sleep_hours: Optional[str] = None      # janela própria "HH:MM-HH:MM" (opcional)
    run_after: Optional[str] = None        # nomes de apps a montante (encadeamento)


def discover_apps(roots: list, exclude: set):
    """Percorre as raízes por ordem; em nomes duplicados a primeira raiz ganha.

    Devolve (apps, duplicados) — duplicados é uma lista de descrições das
    pastas ignoradas por colisão de nome.
    """
    apps, seen, duplicates = [], {}, []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for item in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not item.is_dir():
                continue
            if item.name.lower() in exclude or item.name.startswith("."):
                continue
            main_py = item / "main.py"
            main_bat = item / "main.bat"
            if main_py.exists():
                appdef = AppDef(item.name, item, "py", main_py)
            elif main_bat.exists():
                appdef = AppDef(item.name, item, "bat", main_bat)
            else:
                continue
            if item.name in seen:
                duplicates.append(f"{item} (já existe em {seen[item.name]})")
                continue
            seen[item.name] = item
            apps.append(appdef)
    return apps, duplicates


def read_schedule(app_dir: Path):
    """Lê o schedule.ini da app. Devolve (ScheduleCfg, lista de avisos)."""
    cfg = ScheduleCfg()
    warnings = []
    ini = app_dir / "schedule.ini"
    if not ini.exists():
        return cfg, warnings
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(ini, encoding="utf-8")
    except configparser.Error as e:
        warnings.append(f"schedule.ini inválido ({e}); a usar valores por omissão.")
        return cfg, warnings
    if "Schedule" in parser:
        sec = parser["Schedule"]
        try:
            cfg.enabled = sec.getboolean("enabled", fallback=True)
        except ValueError:
            warnings.append("enabled inválido; a assumir true.")
        try:
            cfg.interval_minutes = sec.getint("interval_minutes", fallback=60)
        except ValueError:
            warnings.append("interval_minutes inválido; a assumir 60.")
        try:
            cfg.timeout_minutes = sec.getint("timeout_minutes", fallback=0)
        except ValueError:
            warnings.append("timeout_minutes inválido; a assumir 0.")
        cfg.cron = sec.get("cron", fallback="").strip() or None
        cfg.python_exe = sec.get("python_exe", fallback="").strip() or None
        cfg.sleep_hours = sec.get("sleep_hours", fallback="").strip() or None
        cfg.run_after = sec.get("run_after", fallback="").strip() or None
        try:
            cfg.ignore_sleep_hours = sec.getboolean("ignore_sleep_hours", fallback=False)
        except ValueError:
            warnings.append("ignore_sleep_hours inválido; a assumir false.")
    if cfg.interval_minutes <= 0:
        cfg.interval_minutes = 60
    if cfg.timeout_minutes < 0:
        cfg.timeout_minutes = 0
    return cfg, warnings


def set_ini_values(ini: Path, section: str, values: dict):
    """Atualiza chaves de uma secção do INI, preservando comentários e o resto.

    - value None remove a chave (linha) se existir.
    - Cria a secção/chaves em falta no fim.
    """
    section_l = section.lower()
    lines = ini.read_text(encoding="utf-8").splitlines() if ini.exists() else []
    out, in_target, section_found = [], False, False
    remaining = dict(values)

    def flush(rem):
        for k, v in list(rem.items()):
            if v is not None:
                out.append(f"{k} = {v}")
        rem.clear()

    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_target:
                flush(remaining)           # chaves ainda por escrever nesta secção
            in_target = s[1:-1].strip().lower() == section_l
            if in_target:
                section_found = True
            out.append(line)
            continue
        if in_target:
            m = re.match(r"\s*([^=:;#\[]+?)\s*[=:]", line)
            if m and m.group(1).strip() in remaining:
                key = m.group(1).strip()
                v = remaining.pop(key)
                if v is not None:
                    out.append(f"{key} = {v}")
                continue                    # v None -> apaga a linha
        out.append(line)
    if in_target:
        flush(remaining)
    if not section_found:
        if out and out[-1].strip() != "":
            out.append("")
        out.append(f"[{section}]")
        for k, v in values.items():
            if v is not None:
                out.append(f"{k} = {v}")
    ini.write_text("\n".join(out) + "\n", encoding="utf-8")


def replace_section_keys(ini: Path, section: str, pairs: dict):
    """Substitui TODAS as chaves de uma secção pelas de `pairs` (remove as ausentes).

    Preserva comentários/linhas em branco e as outras secções. Usado no [Links],
    onde as próprias chaves (nomes dos atalhos) podem mudar ou desaparecer.
    """
    section_l = section.lower()
    lines = ini.read_text(encoding="utf-8").splitlines() if ini.exists() else []
    out, in_target, section_found, written = [], False, False, False

    def write_pairs():
        for k, v in pairs.items():
            out.append(f"{k} = {v}")

    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_target and not written:
                write_pairs()
                written = True
            in_target = s[1:-1].strip().lower() == section_l
            if in_target:
                section_found = True
            out.append(line)
            continue
        if in_target and re.match(r"\s*[^=:;#\[]+?\s*[=:]", line):
            continue                          # remove chaves antigas da secção
        out.append(line)
    if in_target and not written:
        write_pairs()
    if not section_found:
        if out and out[-1].strip() != "":
            out.append("")
        out.append(f"[{section}]")
        write_pairs()
    ini.write_text("\n".join(out) + "\n", encoding="utf-8")


def write_apps_roots(ini: Path, roots):
    """Escreve [Apps] roots como valor multi-linha (um caminho por linha indentada).

    Necessário porque os caminhos Windows têm ':' e não podem ir por set_ini_values.
    Preserva comentários, o 'exclude' e as restantes secções.
    """
    lines = ini.read_text(encoding="utf-8").splitlines() if ini.exists() else []
    out, in_apps, skipping, written = [], False, False, False

    def emit():
        out.append("roots =")
        for r in roots:
            out.append("    " + str(r))

    for line in lines:
        s = line.strip()
        if skipping:                          # salta as linhas de continuação antigas
            if line[:1] in (" ", "\t") and s:
                continue
            skipping = False
        if s.startswith("[") and s.endswith("]"):
            if in_apps and not written:
                emit()
                written = True
            in_apps = s[1:-1].strip().lower() == "apps"
            out.append(line)
            continue
        if in_apps and re.match(r"\s*roots\s*[=:]", line, re.IGNORECASE):
            emit()
            written = True
            skipping = True
            continue
        out.append(line)
    if in_apps and not written:
        emit()
        written = True
    if not written:
        if out and out[-1].strip() != "":
            out.append("")
        out.append("[Apps]")
        emit()
    ini.write_text("\n".join(out) + "\n", encoding="utf-8")


def write_schedule_enabled(app_dir: Path, enabled: bool):
    """Grava enabled no schedule.ini da app, preservando comentários e restantes linhas."""
    set_ini_values(app_dir / "schedule.ini", "Schedule",
                   {"enabled": "true" if enabled else "false"})


# ---------------------------------------------------------------------------
# Regras de notificação e mensagens (editadas no dashboard)
# ---------------------------------------------------------------------------

DEFAULT_RULES = {
    "defaults": {"notify_on_error": True, "patterns": [], "messages": []},
    "apps": {},
}

MAX_MESSAGES_PER_RUN = 5


class RulesStore:
    """Regras que decidem quando uma execução gera uma notificação ao utilizador
    e que linhas do output devem ser destacadas na homepage do dashboard.

    Estrutura (notification_rules.json):
        defaults.notify_on_error  -> notifica quando o código de saída != 0
        defaults.patterns         -> padrões globais que disparam notificação (erro)
        defaults.messages         -> padrões globais de mensagens de sucesso/warning
        apps.<nome>.notify_on_error / .patterns / .messages -> específicos da app
    Cada padrão: {"pattern": str, "is_regex": bool, "enabled": bool}
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._rules = copy.deepcopy(DEFAULT_RULES)
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.validate(loaded)
                self._rules = loaded
            except (ValueError, OSError):
                pass
        else:
            self._save()

    @staticmethod
    def validate(rules):
        if not isinstance(rules, dict):
            raise ValueError("estrutura inválida: esperado objeto JSON")
        sections = [rules.get("defaults", {})] + list(rules.get("apps", {}).values())
        for sec in sections:
            if not isinstance(sec, dict):
                raise ValueError("secção inválida nas regras")
            for field in ("patterns", "messages"):
                for pat in sec.get(field, []):
                    if not isinstance(pat, dict) or not str(pat.get("pattern", "")).strip():
                        raise ValueError("padrão vazio ou inválido")
                    if pat.get("is_regex"):
                        try:
                            re.compile(pat["pattern"])
                        except re.error as e:
                            raise ValueError(f"regex inválida '{pat['pattern']}': {e}") from e

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._rules)

    def set(self, rules: dict):
        self.validate(rules)
        rules.setdefault("defaults", copy.deepcopy(DEFAULT_RULES["defaults"]))
        rules.setdefault("apps", {})
        with self._lock:
            self._rules = copy.deepcopy(rules)
            self._save()

    def _save(self):
        self.path.write_text(
            json.dumps(self._rules, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def evaluate(self, app_name: str, returncode, text: str) -> list:
        """Devolve a lista de motivos de notificação (erro) para uma execução."""
        with self._lock:
            defaults = self._rules.get("defaults", {})
            app = self._rules.get("apps", {}).get(app_name, {})
        reasons = []

        notify_on_error = app.get("notify_on_error", defaults.get("notify_on_error", True))
        if notify_on_error and returncode != 0:
            if returncode is None:
                reasons.append("execução abortada (timeout/exceção)")
            else:
                reasons.append(f"código de saída {returncode}")

        patterns = list(defaults.get("patterns", [])) + list(app.get("patterns", []))
        for pat in patterns:
            if not pat.get("enabled", True):
                continue
            needle = str(pat.get("pattern", ""))
            if not needle:
                continue
            try:
                if pat.get("is_regex"):
                    if re.search(needle, text, re.IGNORECASE | re.MULTILINE):
                        reasons.append(f"padrão (regex) '{needle}' encontrado no output")
                else:
                    if needle.lower() in text.lower():
                        reasons.append(f"padrão '{needle}' encontrado no output")
            except re.error:
                continue
        return reasons

    def evaluate_messages(self, app_name: str, text: str) -> list:
        """Linhas do output que casam com os padrões de mensagens (sucesso/warning).

        Devolve as próprias linhas (até MAX_MESSAGES_PER_RUN, sem duplicados),
        para serem mostradas na homepage do dashboard.
        """
        with self._lock:
            defaults = self._rules.get("defaults", {})
            app = self._rules.get("apps", {}).get(app_name, {})
        patterns = list(defaults.get("messages", [])) + list(app.get("messages", []))
        if not patterns:
            return []
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        matches = []
        for pat in patterns:
            if not pat.get("enabled", True):
                continue
            needle = str(pat.get("pattern", ""))
            if not needle:
                continue
            for line in lines:
                try:
                    if pat.get("is_regex"):
                        hit = re.search(needle, line, re.IGNORECASE)
                    else:
                        hit = needle.lower() in line.lower()
                except re.error:
                    break
                if hit and line[:200] not in matches:
                    matches.append(line[:200])
                    if len(matches) >= MAX_MESSAGES_PER_RUN:
                        return matches
        return matches


# ---------------------------------------------------------------------------
# Execução e agendamento
# ---------------------------------------------------------------------------

class AppRuntime:
    """Estado + thread de agendamento de uma app."""

    def __init__(self, appdef: AppDef, registry):
        self.appdef = appdef
        self.registry = registry
        self.log = get_app_logger(appdef.name, registry.config.logs_dir)
        self.trigger = threading.Event()
        self._stop = threading.Event()           # paragem individual (rescan)
        self._run_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.running = False
        self.queued = False                  # à espera de um lugar (max_parallel)
        self.last = None                     # resumo da última execução
        self.next_run = None                 # epoch da próxima execução agendada
        self.history = deque(maxlen=HISTORY_SIZE)
        self.thread = None
        self.cron_spec = None
        self.schedule_warnings = []
        self._sleep_held = False              # já registei que estou em pausa por sleep hours?
        self._next_origem = "manual"          # origem do próximo run despoletado por trigger
        cfg, warns = read_schedule(appdef.dir)
        self._apply_schedule(cfg, warns)
        self._history_path = Path(registry.config.history_dir) / f"{appdef.name}.jsonl"
        self._load_history()

    # -- schedule -----------------------------------------------------------

    def _apply_schedule(self, cfg: ScheduleCfg, base_warnings=None):
        self.enabled = cfg.enabled
        self.interval_minutes = cfg.interval_minutes
        self.timeout_minutes = cfg.timeout_minutes
        self.cron_expr = cfg.cron
        self.python_exe = cfg.python_exe
        self.ignore_sleep_hours = cfg.ignore_sleep_hours
        self.cron_spec = None
        warns = list(base_warnings or [])
        # janela de sleep hours própria da app (opcional)
        self.app_sleep_hours = None
        self.app_sleep_raw = cfg.sleep_hours
        if cfg.sleep_hours:
            sh, sh_warn = parse_sleep_window(cfg.sleep_hours)
            self.app_sleep_hours = sh
            if sh_warn:
                warns.append(sh_warn)
        if cfg.cron:
            try:
                self.cron_spec = CronSpec(cfg.cron)
            except CronError as e:
                warns.append(f"cron inválido '{cfg.cron}': {e} — a usar interval_minutes")
                self.log.error(
                    f"Expressão cron inválida '{cfg.cron}': {e} — a usar interval_minutes",
                    extra={"app": self.appdef.name, "event": "cron_invalid"},
                )
        if cfg.python_exe:
            exe = Path(cfg.python_exe).expanduser()
            if not exe.is_absolute():
                exe = self.appdef.dir / exe
            if not exe.exists():
                warns.append(f"python_exe não encontrado: {exe}")
        # encadeamento (apps a montante). A validação de nomes/ciclos é feita
        # ao nível do Registry (precisa do grafo completo).
        self.run_after_raw = [
            n.strip() for n in (cfg.run_after or "").split(",") if n.strip()
        ]
        if self.appdef.name in self.run_after_raw:
            warns.append("run_after refere a própria app; ignorado.")
            self.run_after_raw = [n for n in self.run_after_raw if n != self.appdef.name]
        self.run_after = list(self.run_after_raw)   # efetivo (Registry pode filtrar)
        self.schedule_warnings = warns

    def refresh_def(self, appdef: AppDef):
        """Rescan: atualiza a definição e relê o schedule.ini (fonte de verdade)."""
        with self._state_lock:
            self.appdef = appdef
            cfg, warns = read_schedule(appdef.dir)
            self._apply_schedule(cfg, warns)
            self.next_run = None

    # -- histórico persistente ------------------------------------------------

    def _load_history(self):
        if not self._history_path.exists():
            return
        try:
            lines = self._history_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines[-HISTORY_SIZE:]:
            try:
                self.history.append(json.loads(line))
            except ValueError:
                continue
        if self.history:
            self.last = self.history[-1]

    def _append_history(self, entry: dict):
        try:
            with self._history_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if self._history_path.stat().st_size > HISTORY_MAX_BYTES:
                with self._state_lock:
                    keep = [json.dumps(h, ensure_ascii=False) for h in self.history]
                self._history_path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError as e:
            self.log.warning(
                f"Não foi possível gravar o histórico: {e}",
                extra={"app": self.appdef.name, "event": "history_error"},
            )

    # -- API usada pelo tray/dashboard ------------------------------------

    def start(self, index: int):
        self.thread = threading.Thread(
            target=self._loop, args=(index,), daemon=True,
            name=f"app-{self.appdef.name}",
        )
        self.thread.start()

    def shutdown(self):
        """Paragem individual (usada no rescan quando a app desaparece)."""
        self._stop.set()
        self.trigger.set()

    def request_run(self, origem: str = "manual"):
        with self._state_lock:
            self._next_origem = origem
        self.trigger.set()

    def set_enabled(self, enabled: bool):
        # o loop de espera reavalia enabled/next_run a cada segundo
        with self._state_lock:
            self.enabled = bool(enabled)
            self.next_run = None
        try:
            write_schedule_enabled(self.appdef.dir, bool(enabled))
        except OSError as e:
            self.log.warning(
                f"Não foi possível gravar enabled no schedule.ini: {e}",
                extra={"app": self.appdef.name, "event": "schedule_write_error"},
            )

    def snapshot(self) -> dict:
        with self._state_lock:
            return {
                "name": self.appdef.name,
                "kind": self.appdef.kind,
                "dir": str(self.appdef.dir),
                "enabled": self.enabled,
                "interval_minutes": self.interval_minutes,
                "timeout_minutes": self.timeout_minutes,
                "cron": self.cron_expr if self.cron_spec else None,
                "schedule_mode": "cron" if self.cron_spec else ("chain" if self.run_after else "interval"),
                "run_after": list(self.run_after),
                "downstream": list(self.registry._downstream.get(self.appdef.name, [])),
                "python_exe": self.python_exe,
                "ignore_sleep_hours": self.ignore_sleep_hours,
                "sleep_mode": self.sleep_mode(),
                "app_sleep_hours": (
                    {"start": self.app_sleep_hours.start.strftime("%H:%M"),
                     "end": self.app_sleep_hours.end.strftime("%H:%M")}
                    if self.app_sleep_hours else None),
                "sleeping": self.enabled and self._sleep_active(),
                "warnings": list(self.schedule_warnings),
                "running": self.running,
                "queued": self.queued,
                "next_run": (
                    datetime.fromtimestamp(self.next_run, tz=timezone.utc)
                    .isoformat(timespec="seconds").replace("+00:00", "Z")
                    if self.next_run else None
                ),
                "last": copy.deepcopy(self.last),
                "history": [copy.deepcopy(h) for h in list(self.history)[-25:]],
            }

    # -- loop de agendamento ----------------------------------------------

    def _stopping(self) -> bool:
        return self.registry.stop_event.is_set() or self._stop.is_set()

    def _effective_sleep(self):
        """Janela de sleep hours efetiva desta app (ou None se não se aplica).

        - ignore_sleep_hours: a app crítica nunca dorme -> None.
        - janela própria (sleep_hours no schedule.ini) -> essa.
        - caso contrário -> a transversal (scheduler.ini).
        """
        if self.ignore_sleep_hours:
            return None
        if self.app_sleep_hours is not None:
            return self.app_sleep_hours
        return self.registry.config.sleep_hours

    def sleep_mode(self) -> str:
        if self.ignore_sleep_hours:
            return "ignore"
        if self.app_sleep_hours is not None:
            return "custom"
        return "inherit"

    def _sleep_active(self) -> bool:
        """True se estamos dentro do período de sleep hours efetivo da app."""
        window = self._effective_sleep()
        return bool(window and window.active_at(datetime.now()))

    def _compute_next_run(self):
        # apps encadeadas não correm por tempo: só quando a montante termina
        if self.run_after:
            return None
        if self.cron_spec:
            try:
                return self.cron_spec.next_after(datetime.now()).timestamp()
            except CronError:
                return None
        return time.time() + self.interval_minutes * 60

    def _loop(self, index: int):
        if self.registry.stop_event.wait(INITIAL_DELAY_S + index * STAGGER_S):
            return
        if self._stop.is_set():
            return
        # execução no arranque só para apps por intervalo (nem cron nem encadeadas);
        # durante sleep hours, salta-se o arranque imediato.
        if (self.enabled and not self.cron_spec and not self.run_after
                and not self._sleep_active()):
            self.run_once("agendado")
        while not self._stopping():
            with self._state_lock:
                self.next_run = None  # _wait_for_next recalcula a partir de agora
            triggered = self._wait_for_next()
            if self._stopping():
                return
            if triggered:
                with self._state_lock:
                    origem = self._next_origem
                    self._next_origem = "manual"
                self.run_once(origem)
            else:
                self.run_once("agendado")

    def _wait_for_next(self) -> bool:
        """Espera até à próxima execução agendada, pedido manual ou paragem.

        Reavalia enabled/interval/cron a cada segundo, por isso alterações
        têm efeito imediato. Devolve True se a saída foi provocada por um
        pedido manual.
        """
        while not self._stopping():
            if self.trigger.is_set():
                self.trigger.clear()
                if self._stopping():
                    return False
                self._sleep_held = False  # execução manual ignora sleep hours
                return True
            with self._state_lock:
                if not self.enabled:
                    self.next_run = None
                elif self.next_run is None:
                    self.next_run = self._compute_next_run()
                deadline = self.next_run
            if deadline is not None and time.time() >= deadline:
                # a hora agendada chegou, mas em sleep hours espera-se até ao fim
                if self._sleep_active():
                    if not self._sleep_held:
                        self._sleep_held = True
                        window = self._effective_sleep()
                        wakes = window.next_end_after(datetime.now()) if window else None
                        self.log.info(
                            "Execução adiada: sleep hours"
                            + (f" (retoma às {wakes.strftime('%H:%M')})" if wakes else ""),
                            extra={"app": self.appdef.name, "event": "sleep_hold"},
                        )
                    self.trigger.wait(timeout=1.0)
                    continue
                self._sleep_held = False
                return False
            self.trigger.wait(timeout=1.0)
        return False

    # -- execução ------------------------------------------------------------

    def run_once(self, origem: str):
        if not self._run_lock.acquire(blocking=False):
            self.log.warning(
                "Execução ignorada: a app ainda está a correr",
                extra={"app": self.appdef.name, "event": "run_skipped",
                       "data": {"origem": origem}},
            )
            return
        sem = self.registry.exec_semaphore
        sem_acquired = False
        try:
            # limite global de concorrência (max_parallel); enquanto espera,
            # a app fica "em fila". A paragem/rescan interrompe a espera.
            if sem is not None and not sem.acquire(blocking=False):
                self._set_queued(True)
                self.log.info(
                    "Em fila: limite de execuções em simultâneo atingido",
                    extra={"app": self.appdef.name, "event": "run_queued",
                           "data": {"origem": origem}},
                )
                try:
                    while not sem.acquire(timeout=1.0):
                        if self._stopping():
                            return
                finally:
                    self._set_queued(False)
            sem_acquired = sem is not None
            self._set_running(True)
            try:
                self._execute(origem)
            finally:
                self._set_running(False)
        finally:
            if sem_acquired:
                sem.release()
            self._run_lock.release()

    def _set_running(self, value: bool):
        with self._state_lock:
            self.running = value
        self.registry.state_changed()

    def _set_queued(self, value: bool):
        with self._state_lock:
            self.queued = value
        self.registry.state_changed()

    def _pump(self, stream, event, level, sink):
        """Lê um pipe linha-a-linha, escreve no log e guarda no buffer (tail)."""
        try:
            for line in stream:
                line = line.rstrip("\r\n")
                sink.append(line)
                if line.strip():
                    level(line, extra={"app": self.appdef.name, "event": event})
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    @staticmethod
    def _kill_tree(proc):
        """Termina o processo e os seus descendentes (ex.: cmd.exe -> filhos)."""
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, creationflags=CREATE_NO_WINDOW,
                )
                return
            except Exception:
                pass
        try:
            proc.kill()
        except Exception:
            pass

    def _resolve_python(self):
        """Interpretador para apps py: python_exe do schedule.ini ou o do scheduler."""
        if not self.python_exe:
            return Path(sys.executable), None
        exe = Path(self.python_exe).expanduser()
        if not exe.is_absolute():
            exe = self.appdef.dir / exe
        if not exe.exists():
            return None, f"python_exe não encontrado: {exe}"
        return exe, None

    def _execute(self, origem: str):
        app = self.appdef
        name = app.name
        start_iso = _now_iso()
        t0 = time.monotonic()
        timeout_s = self.timeout_minutes * 60 or None

        self.log.info(
            f"Início da execução ({origem})",
            extra={"app": name, "event": "run_start",
                   "data": {"origem": origem, "entry": app.entry.name}},
        )

        env = None
        exe_error = None
        if app.kind == "py":
            exe, exe_error = self._resolve_python()
            cmd = [str(exe), "-u", str(app.entry)] if exe else None
            enc = "utf-8"
            # garante que o filho escreve UTF-8 no pipe (senão usa cp1252)
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        else:
            cmd = ["cmd.exe", "/c", str(app.entry)]
            enc = None  # encoding da consola do Windows

        stdout, stderr, returncode, status = "", "", None, "erro"
        if exe_error:
            status, stderr = "erro", exe_error
            self.log.error(exe_error, extra={"app": name, "event": "run_exception"})
        else:
            # Popen + threads: as linhas são escritas no log à medida que saem
            # (logs "live" no Grafana Loki), em vez de só no fim da execução.
            out_lines, err_lines = deque(maxlen=STREAM_LINES), deque(maxlen=STREAM_LINES)
            try:
                proc = subprocess.Popen(
                    cmd, cwd=str(app.dir),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding=enc, errors="replace", bufsize=1,
                    env=env, creationflags=CREATE_NO_WINDOW,
                )
            except Exception as e:
                status = "exceção"
                stderr = str(e)
                self.log.error(
                    f"Exceção ao arrancar: {e}",
                    extra={"app": name, "event": "run_exception"},
                )
                proc = None

            if proc is not None:
                t_out = threading.Thread(
                    target=self._pump, args=(proc.stdout, "stdout", self.log.info, out_lines),
                    daemon=True)
                t_err = threading.Thread(
                    target=self._pump, args=(proc.stderr, "stderr", self.log.error, err_lines),
                    daemon=True)
                t_out.start()
                t_err.start()
                try:
                    returncode = proc.wait(timeout=timeout_s)
                    status = "ok" if returncode == 0 else "erro"
                except subprocess.TimeoutExpired:
                    self._kill_tree(proc)
                    try:
                        returncode = proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        returncode = None
                    status = "timeout"
                    self.log.error(
                        f"Timeout após {self.timeout_minutes} min — processo terminado",
                        extra={"app": name, "event": "run_timeout",
                               "data": {"timeout_minutes": self.timeout_minutes}},
                    )
                # espera que os pumps esvaziem os pipes
                t_out.join(timeout=10)
                t_err.join(timeout=10)
                stdout = "\n".join(out_lines)
                stderr = "\n".join(err_lines)

        duration = round(time.monotonic() - t0, 2)

        level = self.log.info if status == "ok" else self.log.error
        level(
            f"Fim da execução: {status}",
            extra={"app": name, "event": "run_end",
                   "data": {"status": status, "returncode": returncode,
                            "duration_s": duration, "origem": origem}},
        )

        entry = {
            "start": start_iso,
            "end": _now_iso(),
            "duration_s": duration,
            "returncode": returncode,
            "status": status,
            "origem": origem,
            "stdout_tail": stdout[-TAIL_CHARS:],
            "stderr_tail": stderr[-TAIL_CHARS:],
        }

        combined = f"{stdout}\n{stderr}"

        # regras de notificação (erro -> toast do Windows)
        reasons = self.registry.rules.evaluate(name, returncode, combined)
        if reasons:
            entry["notified"] = reasons
            self.log.warning(
                "Notificação disparada: " + "; ".join(reasons),
                extra={"app": name, "event": "notify", "data": {"motivos": reasons}},
            )

        # mensagens de sucesso/warning (destacadas na homepage do dashboard)
        messages = self.registry.rules.evaluate_messages(name, combined)
        if messages:
            entry["messages"] = messages
            for m in messages:
                self.log.info(
                    f"Mensagem destacada: {m}",
                    extra={"app": name, "event": "highlight"},
                )

        with self._state_lock:
            self.last = entry
            self.history.append(entry)
        self._append_history(entry)

        if reasons:
            self.registry.notify(f"{name}", "; ".join(reasons)[:200])
        if status == "ok":
            self.registry.on_run_success(name)     # dispara apps encadeadas
        self.registry.state_changed()


class Registry:
    """Conjunto das apps geridas + callbacks para o tray."""

    def __init__(self, config, rules: RulesStore):
        self.config = config
        self.rules = rules
        self.stop_event = threading.Event()
        self.apps = {}                     # nome -> AppRuntime
        self._apps_lock = threading.Lock()
        self._downstream = {}              # nome a montante -> [nomes a jusante]
        # limite global de execuções em simultâneo (0 = sem limite)
        mp = getattr(config, "max_parallel", 0)
        self.exec_semaphore = threading.BoundedSemaphore(mp) if mp and mp > 0 else None
        # callable(title, message) -> toast Windows
        self.notifier: Optional[Callable[[str, str], None]] = None
        # callable() -> atualizar ícone do tray
        self.on_state_change: Optional[Callable[[], None]] = None
        # callable() -> reconstruir menu do tray depois de um rescan
        self.on_apps_changed: Optional[Callable[[], None]] = None
        self.log = get_scheduler_logger(config.logs_dir)

    def start(self):
        for w in getattr(self.config, "warnings", []):
            self.log.warning(
                f"Aviso de configuração: {w}",
                extra={"app": "scheduler", "event": "config_warning"},
            )
        defs, duplicates = discover_apps(self.config.apps_roots, self.config.exclude)
        for dup in duplicates:
            self.log.warning(
                f"App ignorada por nome duplicado: {dup}",
                extra={"app": "scheduler", "event": "duplicate_app"},
            )
        with self._apps_lock:
            for appdef in defs:
                self.apps[appdef.name] = AppRuntime(appdef, self)
            self._wire_dependencies()          # valida run_after e monta o grafo
            for i, rt in enumerate(self.apps.values()):
                rt.start(i)
        self.log.info(
            f"Scheduler iniciado com {len(defs)} app(s): "
            + ", ".join(f"{a.name} ({a.kind})" for a in defs),
            extra={"app": "scheduler", "event": "startup",
                   "data": {"apps_roots": [str(r) for r in self.config.apps_roots]}},
        )
        return defs

    def _wire_dependencies(self):
        """Valida run_after de todas as apps e (re)constrói o grafo de dependências.

        Deve ser chamado com self._apps_lock detido. Remove referências a apps
        inexistentes e quebra ciclos, acrescentando avisos às apps envolvidas.
        """
        names = set(self.apps)
        # 1) filtra referências a apps inexistentes
        for rt in self.apps.values():
            rt.schedule_warnings = [w for w in rt.schedule_warnings
                                    if "run_after" not in w or "própria app" in w]
            effective = []
            for up in rt.run_after_raw:
                if up not in names:
                    rt.schedule_warnings.append(f"run_after: app '{up}' não existe; ignorada.")
                else:
                    effective.append(up)
            rt.run_after = effective

        # 2) deteta ciclos (DFS sobre a relação "corre depois de")
        WHITE, GREY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self.apps}

        def visit(n, stack):
            color[n] = GREY
            for up in list(self.apps[n].run_after):
                if color.get(up) == GREY:
                    # aresta n->up fecha um ciclo: quebra-a
                    self.apps[n].run_after = [x for x in self.apps[n].run_after if x != up]
                    self.apps[n].schedule_warnings.append(
                        f"run_after: dependência de '{up}' cria um ciclo; ignorada.")
                elif color.get(up) == WHITE:
                    visit(up, stack + [n])
            color[n] = BLACK

        for n in self.apps:
            if color[n] == WHITE:
                visit(n, [])

        # 3) mapa a montante -> a jusante
        downstream = {}
        for name, rt in self.apps.items():
            for up in rt.run_after:
                downstream.setdefault(up, []).append(name)
        self._downstream = downstream

    def on_run_success(self, name: str):
        """Uma app terminou com sucesso: despoleta as apps encadeadas a jusante."""
        with self._apps_lock:
            targets = [(d, self.apps.get(d)) for d in self._downstream.get(name, [])]
        for down, rt in targets:
            if rt and rt.enabled:
                rt.request_run(origem=f"dependência ({name})")
                self.log.info(
                    f"Encadeamento: '{down}' despoletada por '{name}'",
                    extra={"app": "scheduler", "event": "chain_trigger",
                           "data": {"upstream": name, "downstream": down}},
                )

    def rescan(self) -> dict:
        """Re-deteta apps sem reiniciar. Devolve {"added", "removed", "updated"}."""
        defs, duplicates = discover_apps(self.config.apps_roots, self.config.exclude)
        for dup in duplicates:
            self.log.warning(
                f"App ignorada por nome duplicado: {dup}",
                extra={"app": "scheduler", "event": "duplicate_app"},
            )
        added, removed, updated = [], [], []
        with self._apps_lock:
            new_names = {d.name for d in defs}
            for name in [n for n in self.apps if n not in new_names]:
                rt = self.apps.pop(name)
                rt.shutdown()
                removed.append(name)
            new_runtimes = []
            for appdef in defs:
                if appdef.name in self.apps:
                    self.apps[appdef.name].refresh_def(appdef)
                    updated.append(appdef.name)
                else:
                    rt = AppRuntime(appdef, self)
                    self.apps[appdef.name] = rt
                    new_runtimes.append(rt)
                    added.append(appdef.name)
            self._wire_dependencies()          # revalida o grafo com o conjunto atual
            for rt in new_runtimes:
                rt.start(0)
        self.log.info(
            f"Rescan: +{len(added)} novas, -{len(removed)} removidas, {len(updated)} atualizadas",
            extra={"app": "scheduler", "event": "rescan",
                   "data": {"added": added, "removed": removed}},
        )
        if self.on_apps_changed:
            try:
                self.on_apps_changed()
            except Exception:
                pass
        self.state_changed()
        return {"added": added, "removed": removed, "updated": updated}

    def _get(self, name: str):
        with self._apps_lock:
            return self.apps.get(name)

    def trigger(self, name: str, origem: str = "manual") -> bool:
        rt = self._get(name)
        if not rt:
            return False
        rt.request_run(origem)
        return True

    def set_enabled(self, name: str, enabled: bool) -> bool:
        rt = self._get(name)
        if not rt:
            return False
        rt.set_enabled(enabled)
        self.log.info(
            f"Agendamento de {name} {'ativado' if enabled else 'desativado'} (gravado no schedule.ini)",
            extra={"app": "scheduler", "event": "toggle",
                   "data": {"target": name, "enabled": enabled}},
        )
        return True

    def update_sleep_hours(self, enabled: bool, start: str, end: str):
        """Atualiza as sleep hours transversais: grava no scheduler.ini e aplica já.

        Devolve (ok: bool, mensagem: str).
        """
        start_t, end_t = _parse_hhmm(start), _parse_hhmm(end)
        if enabled:
            if start_t is None or end_t is None:
                return False, "Horas inválidas (formato HH:MM)."
            if start_t == end_t:
                return False, "Início igual ao fim."
        new = SleepHours(enabled=enabled, start=start_t, end=end_t)
        try:
            set_ini_values(self.config.ini_path, "SleepHours", {
                "enabled": "true" if enabled else "false",
                "start": start_t.strftime("%H:%M") if start_t else "",
                "end": end_t.strftime("%H:%M") if end_t else "",
            })
        except OSError as e:
            return False, f"Não foi possível gravar o scheduler.ini: {e}"
        self.config.sleep_hours = new          # aplicado de imediato a todas as apps
        self.log.info(
            f"Sleep hours transversais {'ativadas' if enabled else 'desativadas'}"
            + (f" ({start_t:%H:%M}-{end_t:%H:%M})" if new.valid else ""),
            extra={"app": "scheduler", "event": "sleep_hours_update"},
        )
        self.state_changed()
        return True, "Sleep hours transversais guardadas."

    def update_settings(self, host=None, port=None, open_on_start=None,
                        max_parallel=None, links=None, roots=None):
        """Grava definições globais no scheduler.ini e aplica o que é possível ao vivo.

        Devolve (ok, msg). host/port/max_parallel só têm efeito após reiniciar;
        open_on_start, links e roots (via rescan) aplicam-se de imediato.
        Campos None são ignorados.
        """
        cfg = self.config
        dash, restart = {}, []

        new_roots = None
        if roots is not None:
            new_roots = _parse_roots("\n".join(str(r) for r in roots), cfg.config_dir)

        if host is not None:
            host = str(host).strip()
            if not host:
                return False, "host não pode estar vazio."
            if host != cfg.host:
                restart.append("host")
            dash["host"] = host
        if port is not None:
            try:
                p = int(port)
            except (TypeError, ValueError):
                return False, "port inválido."
            if not (1 <= p <= 65535):
                return False, "port fora do intervalo 1–65535."
            if p != cfg.port:
                restart.append("port")
            dash["port"] = str(p)
        if open_on_start is not None:
            dash["open_on_start"] = "true" if open_on_start else "false"

        exec_vals = {}
        if max_parallel is not None:
            try:
                mp = max(0, int(max_parallel))
            except (TypeError, ValueError):
                return False, "max_parallel inválido."
            if mp != cfg.max_parallel:
                restart.append("max_parallel")
            exec_vals["max_parallel"] = str(mp)

        clean_links = None
        if links is not None:
            clean_links = {}
            for name, url in links.items():
                name, url = str(name).strip(), str(url).strip()
                if name and url:
                    clean_links[name] = url

        try:
            if dash:
                set_ini_values(cfg.ini_path, "Dashboard", dash)
            if exec_vals:
                set_ini_values(cfg.ini_path, "Execution", exec_vals)
            if clean_links is not None:
                replace_section_keys(cfg.ini_path, "Links", clean_links)
            if new_roots is not None:
                write_apps_roots(cfg.ini_path, new_roots)
        except OSError as e:
            return False, f"Não foi possível gravar o scheduler.ini: {e}"

        # aplica ao vivo o que é seguro
        if host is not None:
            cfg.host = host
        if port is not None:
            cfg.port = int(port)
        if open_on_start is not None:
            cfg.open_on_start = bool(open_on_start)
        if max_parallel is not None:
            cfg.max_parallel = max(0, int(max_parallel))
        if clean_links is not None:
            cfg.links = clean_links               # dashboard e menu do tray refletem já

        rescan_info = None
        if new_roots is not None:
            cfg.apps_roots = new_roots
            cfg.roots_overridden = False           # o INI passa a mandar
            cfg.warnings = [w for w in cfg.warnings if "[Apps] raiz" not in w]
            for root in new_roots:
                if not root.exists():
                    cfg.warnings.append(f"[Apps] raiz não encontrada: {root}")
                elif not root.is_dir():
                    cfg.warnings.append(f"[Apps] raiz não é uma pasta: {root}")
            rescan_info = self.rescan()            # adiciona/remove apps já

        self.log.info(
            "Definições globais atualizadas"
            + (f" (exige reiniciar: {', '.join(restart)})" if restart else ""),
            extra={"app": "scheduler", "event": "settings_update",
                   "data": {"restart": restart}},
        )
        self.state_changed()
        msg = "Definições guardadas."
        if rescan_info:
            msg += (f" Apps: +{len(rescan_info['added'])} / "
                    f"-{len(rescan_info['removed'])}.")
        if restart:
            msg += " Requer reiniciar para: " + ", ".join(restart) + "."
        return True, msg

    def set_app_sleep(self, name: str, mode: str, start: str = "", end: str = ""):
        """Define o modo de sleep hours de uma app (inherit|ignore|custom).

        Grava no schedule.ini da app e reaplica de imediato. Devolve (ok, msg).
        """
        rt = self._get(name)
        if not rt:
            return False, f"App '{name}' desconhecida."
        if mode == "inherit":
            values = {"ignore_sleep_hours": None, "sleep_hours": None}
        elif mode == "ignore":
            values = {"ignore_sleep_hours": "true", "sleep_hours": None}
        elif mode == "custom":
            window, warn = parse_sleep_window(f"{(start or '').strip()}-{(end or '').strip()}")
            if window is None:
                return False, warn or "Janela inválida (formato HH:MM)."
            values = {"ignore_sleep_hours": None,
                      "sleep_hours": f"{window.start:%H:%M}-{window.end:%H:%M}"}
        else:
            return False, f"Modo inválido: {mode}"
        try:
            set_ini_values(rt.appdef.dir / "schedule.ini", "Schedule", values)
        except OSError as e:
            return False, f"Não foi possível gravar o schedule.ini: {e}"
        self._refresh_and_wire(rt)             # relê e aplica já
        self.log.info(
            f"Sleep hours de {name}: modo '{mode}'"
            + (f" ({start}-{end})" if mode == "custom" else ""),
            extra={"app": "scheduler", "event": "app_sleep_update",
                   "data": {"target": name, "mode": mode}},
        )
        self.state_changed()
        return True, "Sleep hours da app guardadas."

    def _refresh_and_wire(self, rt: "AppRuntime"):
        rt.refresh_def(rt.appdef)
        with self._apps_lock:
            self._wire_dependencies()

    def set_app_schedule(self, name: str, mode: str, interval_minutes=None,
                         cron: str = "", timeout_minutes=None, run_after: str = "",
                         python_exe: str = ""):
        """Edita o agendamento de uma app: grava no schedule.ini e aplica já.

        mode: "interval" | "cron". Devolve (ok, msg).
        """
        rt = self._get(name)
        if not rt:
            return False, f"App '{name}' desconhecida."
        values = {}
        if mode == "interval":
            try:
                n = int(interval_minutes)
            except (TypeError, ValueError):
                return False, "interval_minutes inválido."
            if n <= 0:
                return False, "interval_minutes tem de ser > 0."
            values["interval_minutes"] = str(n)
            values["cron"] = None              # remove cron -> volta ao intervalo
        elif mode == "cron":
            expr = (cron or "").strip()
            try:
                CronSpec(expr)
            except CronError as e:
                return False, f"cron inválido: {e}"
            values["cron"] = expr
        else:
            return False, f"Modo inválido: {mode}"

        if timeout_minutes is not None and str(timeout_minutes) != "":
            try:
                t = int(timeout_minutes)
            except (TypeError, ValueError):
                return False, "timeout_minutes inválido."
            values["timeout_minutes"] = str(max(0, t))

        ra = [n.strip() for n in (run_after or "").split(",") if n.strip()]
        values["run_after"] = ", ".join(ra) if ra else None

        # interpretador próprio da app (vazio = remove -> usa o do scheduler)
        values["python_exe"] = (python_exe or "").strip() or None

        try:
            set_ini_values(rt.appdef.dir / "schedule.ini", "Schedule", values)
        except OSError as e:
            return False, f"Não foi possível gravar o schedule.ini: {e}"
        self._refresh_and_wire(rt)             # relê; o loop reagenda em <=1s
        self.log.info(
            f"Agendamento de {name} atualizado (modo '{mode}')",
            extra={"app": "scheduler", "event": "schedule_update",
                   "data": {"target": name, "mode": mode}},
        )
        self.state_changed()
        return True, "Agendamento guardado."

    def snapshot(self) -> dict:
        with self._apps_lock:
            runtimes = list(self.apps.values())
        return {
            "generated_at": _now_iso(),
            "apps_roots": [str(r) for r in self.config.apps_roots],
            "config_path": str(self.config.ini_path),
            "links": dict(self.config.links),
            "sleep_hours": self.config.sleep_hours.as_dict(),
            "max_parallel": getattr(self.config, "max_parallel", 0),
            "settings": {
                "host": self.config.host,
                "port": self.config.port,
                "open_on_start": self.config.open_on_start,
                "max_parallel": getattr(self.config, "max_parallel", 0),
            },
            "warnings": list(getattr(self.config, "warnings", [])),
            "apps": [rt.snapshot() for rt in runtimes],
        }

    def notify(self, title: str, message: str):
        if self.notifier:
            try:
                self.notifier(title, message)
            except Exception:
                pass

    def state_changed(self):
        if self.on_state_change:
            try:
                self.on_state_change()
            except Exception:
                pass

    def stop(self):
        self.stop_event.set()
        with self._apps_lock:
            for rt in self.apps.values():
                rt.trigger.set()
