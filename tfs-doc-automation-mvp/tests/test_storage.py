import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from doc_automation import storage


class PlanningStateTests(unittest.TestCase):
    def test_needs_plan_is_persisted_and_cleared_when_plan_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            storage, "DB_PATH", Path(directory) / "automation.db"
        ):
            storage.init_storage()
            storage.mark_auto_flow_needs_plan(
                portal="DocumentationPortal",
                work_item_id=160229,
                iteration_path="Product\\Gaivosa\\Sprint 13",
                triage_status="pending",
                work_type="fix",
                branch_name="fix/160229-doc-genai-miscellaneous-issues",
                reviewer_display_name="Reviewer",
                reviewer_unique_name="reviewer@example.com",
                reviewer_id="1",
                message="Select a base branch.",
            )

            blocked = storage.get_work_item_states("DocumentationPortal", [160229])[160229]
            self.assertEqual(blocked["auto_flow_runtime_status"], "needs_plan")
            self.assertFalse(blocked["auto_flow_enabled"])

            storage.save_work_item_plan(
                portal="DocumentationPortal",
                work_item_id=160229,
                iteration_path="Product\\Gaivosa\\Sprint 13",
                triage_status="selected",
                selected_base_branch="12.0/dev",
                work_type="fix",
                branch_name="12.0/fix/160229-doc-genai-miscellaneous-issues",
                reviewer_display_name="Reviewer",
                reviewer_unique_name="reviewer@example.com",
                reviewer_id="1",
            )

            planned = storage.get_work_item_states("DocumentationPortal", [160229])[160229]
            self.assertEqual(planned["auto_flow_runtime_status"], "")
            self.assertEqual(planned["selected_base_branch"], "12.0/dev")


if __name__ == "__main__":
    unittest.main()
