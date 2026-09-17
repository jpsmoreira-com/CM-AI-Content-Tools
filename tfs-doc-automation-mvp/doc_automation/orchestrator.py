from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .config import load_runtime_settings
from .diagnostics import get_logger, get_reference_id, reference_scope
from .services import AutomationService
from .storage import load_runner_status, save_runner_status


LOGGER = get_logger("orchestrator")


class AutomationOrchestrator:
    def __init__(self, service: AutomationService) -> None:
        self.service = service
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_discovery_at = 0.0
        self._running = False

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_forever, daemon=True)
            self._thread.start()
            self._running = True
        LOGGER.info("Automation runner started.")

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        with self._lock:
            self._running = False
        status = load_runner_status()
        status["running"] = False
        save_runner_status(status)
        LOGGER.info("Automation runner stopped.")

    def snapshot(self) -> Dict[str, Any]:
        status = load_runner_status()
        status.setdefault("last_reconcile_at", "")
        status.setdefault("last_error", "")
        status.setdefault("last_error_at", "")
        status.setdefault("last_error_reference_id", "")
        status.setdefault("consecutive_failures", 0)
        with self._lock:
            embedded_running = self._running
        status["running"] = bool(status.get("running")) or embedded_running
        status["healthy"] = int(status.get("consecutive_failures") or 0) == 0
        return status

    def run_once(self, *, force_discovery: bool = False) -> Dict[str, Any]:
        with reference_scope():
            try:
                result = self._run_cycle(force_discovery=force_discovery)
            except Exception as exc:
                self._record_failure(exc)
                raise
            return result

    def _run_cycle(self, *, force_discovery: bool) -> Dict[str, Any]:
        runtime_settings = load_runtime_settings()
        if not runtime_settings.get("automation_runner_enabled"):
            return {
                "status": "disabled",
                "reconcile": {"total": 0, "results": []},
                "discovery": {"total": 0, "results": [], "skipped_portals": []},
            }

        reconcile_result = self.service.resume_persisted_auto_flows()
        discovery_result = {"total": 0, "results": [], "skipped_portals": []}
        now = time.monotonic()
        discovery_interval_seconds = max(
            60,
            int(runtime_settings.get("automation_discovery_interval_minutes") or 5) * 60,
        )
        discovery_due = force_discovery or (now - self._last_discovery_at >= discovery_interval_seconds)
        discovery_ran = bool(runtime_settings.get("automation_continuous_mode") and discovery_due)
        if discovery_ran:
            discovery_result = self.service.start_automatic_flow_for_discovered_items()
            self._last_discovery_at = now

        timestamp = datetime.now(timezone.utc).isoformat()
        status = load_runner_status()
        status.update(
            {
                "running": True,
                "last_reconcile_at": timestamp,
                "last_discovery_at": timestamp if discovery_ran else status.get("last_discovery_at") or "",
                "last_reconcile_count": int(reconcile_result.get("total") or 0),
                "last_discovery_count": int(discovery_result.get("total") or 0),
                "consecutive_failures": 0,
            }
        )
        save_runner_status(status)
        LOGGER.info(
            "Automation cycle completed: reconciled=%s discovered=%s discovery_ran=%s",
            status["last_reconcile_count"],
            status["last_discovery_count"],
            discovery_ran,
        )
        return {
            "status": "ok",
            "reconcile": reconcile_result,
            "discovery": discovery_result,
        }

    def _record_failure(self, exc: Exception) -> None:
        status = load_runner_status()
        failures = int(status.get("consecutive_failures") or 0) + 1
        status.update(
            {
                "running": True,
                "last_error": str(exc),
                "last_error_at": datetime.now(timezone.utc).isoformat(),
                "last_error_reference_id": get_reference_id(),
                "consecutive_failures": failures,
            }
        )
        try:
            save_runner_status(status)
        except Exception:
            LOGGER.exception("Could not persist runner failure status.")
        LOGGER.error("Automation cycle failed (%s consecutive): %s", failures, exc, exc_info=True)

    def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            runtime_settings = load_runtime_settings()
            interval_seconds = max(
                5,
                int(runtime_settings.get("automation_reconcile_interval_seconds") or 30),
            )
            try:
                self.run_once()
            except Exception:
                pass
            self._stop_event.wait(interval_seconds)
