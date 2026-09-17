from __future__ import annotations

import signal
import time

from doc_automation.diagnostics import configure_logging, get_logger
from doc_automation.orchestrator import AutomationOrchestrator
from doc_automation.services import AutomationService


def main() -> None:
    log_file = configure_logging()
    logger = get_logger("worker")
    service = AutomationService()
    orchestrator = AutomationOrchestrator(service)
    orchestrator.start()
    logger.info("Automation worker started. Log file: %s", log_file)
    print(f"Automation worker started. Log file: {log_file}")

    def stop_worker(*_: object) -> None:
        logger.info("Automation worker stopping.")
        orchestrator.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
