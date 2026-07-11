"""Logging em JSON-lines compatível com Grafana Loki (via Promtail/Alloy).

Cada app tem um ficheiro logs/<app>.log em que cada linha é um objeto JSON:
    {"ts": "...RFC3339...", "level": "...", "app": "...", "event": "...", "msg": "...", ...}

O Promtail/Alloy lê estes ficheiros com um pipeline `json` (exemplo no README.md).
"""

import json
import logging
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

_lock = threading.Lock()
_loggers = {}


class LokiJsonFormatter(logging.Formatter):
    """Formata cada registo como uma linha JSON com timestamp RFC3339 (UTC)."""

    def format(self, record):
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
                  .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "app": getattr(record, "app", "scheduler"),
            "event": getattr(record, "event", "log"),
            "msg": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if isinstance(data, dict):
            entry.update(data)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def get_app_logger(app_name: str, logs_dir: Path) -> logging.Logger:
    """Devolve (e cria, se necessário) o logger JSON da app, com rotação de ficheiro."""
    key = f"bgo.{app_name}"
    with _lock:
        if key in _loggers:
            return _loggers[key]
        logger = logging.getLogger(key)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(
            Path(logs_dir) / f"{app_name}.log",
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(LokiJsonFormatter())
        logger.addHandler(handler)
        _loggers[key] = logger
        return logger


def get_scheduler_logger(logs_dir: Path) -> logging.Logger:
    """Logger do próprio scheduler (logs/scheduler.log)."""
    return get_app_logger("scheduler", logs_dir)
