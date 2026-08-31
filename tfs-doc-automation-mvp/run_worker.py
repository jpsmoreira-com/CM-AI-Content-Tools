from __future__ import annotations

import signal
import time

from doc_automation.orchestrator import AutomationOrchestrator
from doc_automation.services import AutomationService


def main() -> None:
    service = AutomationService()
    orchestrator = AutomationOrchestrator(service)
    orchestrator.start()
    print("Automation worker started.")

    def stop_worker(*_: object) -> None:
        orchestrator.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
