from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .config import load_runtime_settings
from .services import AutomationService


class AutomationOrchestrator:
    def __init__(self, service: AutomationService) -> None:
        self.service = service
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_discovery_at = 0.0
        self._snapshot: Dict[str, Any] = {
            "running": False,
            "last_reconcile_at": "",
            "last_discovery_at": "",
            "last_error": "",
            "last_reconcile_count": 0,
            "last_discovery_count": 0,
        }

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_forever, daemon=True)
            self._thread.start()
            self._snapshot["running"] = True

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        with self._lock:
            self._snapshot["running"] = False

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._snapshot)

    def run_once(self, *, force_discovery: bool = False) -> Dict[str, Any]:
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
        with self._lock:
            self._snapshot.update(
                {
                    "running": True,
                    "last_reconcile_at": timestamp,
                    "last_discovery_at": timestamp if discovery_ran else self._snapshot["last_discovery_at"],
                    "last_error": "",
                    "last_reconcile_count": int(reconcile_result.get("total") or 0),
                    "last_discovery_count": int(discovery_result.get("total") or 0),
                }
            )
        return {
            "status": "ok",
            "reconcile": reconcile_result,
            "discovery": discovery_result,
        }

    def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            runtime_settings = load_runtime_settings()
            interval_seconds = max(
                5,
                int(runtime_settings.get("automation_reconcile_interval_seconds") or 30),
            )
            try:
                self.run_once()
            except Exception as exc:
                with self._lock:
                    self._snapshot.update(
                        {
                            "running": True,
                            "last_error": str(exc),
                        }
                    )
            self._stop_event.wait(interval_seconds)
