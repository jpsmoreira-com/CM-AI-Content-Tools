from __future__ import annotations

import logging
import os
import secrets
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator

from .config import DATA_DIR


LOG_FILENAME = "doc-automation.log"
_REFERENCE_ID: ContextVar[str] = ContextVar("doc_automation_reference_id", default="")
_CONFIGURED = False


def new_reference_id() -> str:
    return secrets.token_hex(4)


def get_reference_id() -> str:
    return _REFERENCE_ID.get()


@contextmanager
def reference_scope(reference_id: str = "") -> Iterator[str]:
    token = _REFERENCE_ID.set(reference_id or new_reference_id())
    try:
        yield _REFERENCE_ID.get()
    finally:
        _REFERENCE_ID.reset(token)


def resolve_log_directory() -> Path:
    settings_root = os.environ.get("CONTENT_AI_SETTINGS_PATH", "").strip()
    base = Path(settings_root).expanduser() if settings_root else DATA_DIR
    return base / "logs"


def resolve_log_file() -> Path:
    return resolve_log_directory() / LOG_FILENAME


class _ReferenceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.reference_id = get_reference_id() or "-"
        return True


def configure_logging() -> Path:
    global _CONFIGURED
    log_file = resolve_log_file()
    if _CONFIGURED:
        return log_file

    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(reference_id)s] %(name)s: %(message)s")
    reference_filter = _ReferenceIdFilter()

    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(reference_filter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(reference_filter)

    app_logger = logging.getLogger("doc_automation")
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    app_logger.addHandler(file_handler)
    app_logger.addHandler(stream_handler)

    # Uvicorn writes unhandled request tracebacks here; keep them next to the app log.
    uvicorn_logger = logging.getLogger("uvicorn.error")
    uvicorn_logger.addHandler(file_handler)

    _CONFIGURED = True
    app_logger.info("Logging configured. Log file: %s", log_file)
    return log_file


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("doc_automation") else f"doc_automation.{name}")
