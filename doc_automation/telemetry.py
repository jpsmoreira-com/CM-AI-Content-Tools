from __future__ import annotations

import logging
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from time import perf_counter
from typing import Any, Iterator

from .config import DATA_DIR


_LOGGER: logging.Logger | None = None


def get_performance_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("doc_automation.performance")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        handler = RotatingFileHandler(
            DATA_DIR / "performance.log",
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)

    _LOGGER = logger
    return logger


def _format_detail(value: Any) -> str:
    text = str(value if value is not None else "").replace("\n", " ").replace("\r", " ").strip()
    if len(text) > 180:
        return text[:177] + "..."
    return text


def log_performance(event: str, duration_ms: float, **details: Any) -> None:
    detail_text = " ".join(
        f"{key}={_format_detail(value)}"
        for key, value in details.items()
        if value is not None and _format_detail(value) != ""
    )
    get_performance_logger().info(
        "event=%s duration_ms=%.1f%s",
        event,
        duration_ms,
        f" {detail_text}" if detail_text else "",
    )


@contextmanager
def performance_span(event: str, **details: Any) -> Iterator[None]:
    started_at = perf_counter()
    try:
        yield
    finally:
        log_performance(event, (perf_counter() - started_at) * 1000, **details)
