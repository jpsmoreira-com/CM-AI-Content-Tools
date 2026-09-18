import unittest

from doc_automation.branching import infer_base_branch_from_related_branch
from doc_automation.services import apply_reference_branch_context, apply_work_item_action_flags


class RelatedBranchInferenceTests(unittest.TestCase):
    def test_infers_configured_base_from_implementation_branch(self) -> None:
        result = infer_base_branch_from_related_branch(
            "refs/heads/12.0/feature/160229-doc-genai-misc-issues",
            ["11.3/dev", "12.0/dev"],
        )

        self.assertEqual(result, "12.0/dev")

    def test_returns_empty_when_version_is_not_in_branch_chain(self) -> None:
        result = infer_base_branch_from_related_branch(
            "13.0/fix/160229-doc-genai-misc-issues",
            ["11.3/dev", "12.0/dev"],
        )

        self.assertEqual(result, "")

    def test_existing_documentation_branch_offers_new_branch_action(self) -> None:
        item = {
            "id": 160229,
            "title": "DOC: GenAI miscellaneous issues",
            "branch_name": "12.0/fix/160229-doc-genai-misc-issues",
            "selected_base_branch": "12.0/dev",
            "selected_work_type": "fix",
            "has_branch": False,
            "has_pr": False,
            "push_status": "",
            "agent_result_status": "",
            "auto_flow_enabled": False,
        }

        apply_reference_branch_context(item, item["branch_name"], ["12.0/dev"])
        apply_work_item_action_flags(item)

        self.assertTrue(item["planned_branch_conflict"])
        self.assertFalse(item["can_create_branch"])
        self.assertTrue(item["can_start_rerun"])
        self.assertEqual(item["new_branch_action_label"], "Create New Work Branch")

    def test_implementation_branch_infers_base_without_forcing_rerun(self) -> None:
        item = {
            "id": 160229,
            "title": "DOC: GenAI miscellaneous issues",
            "type": "Task",
            "parent_type": "Bug",
            "tags": "",
            "iteration_path": "Product\\Gaivosa\\Sprint 13",
            "branch_name": "fix/160229-doc-genai-misc-issues",
            "selected_base_branch": "",
            "selected_work_type": "fix",
            "inferred_work_type": "fix",
            "plan_persisted": False,
            "has_branch": False,
            "has_pr": False,
            "push_status": "",
            "agent_result_status": "",
            "auto_flow_enabled": False,
        }

        apply_reference_branch_context(
            item,
            "12.0/feature/160229-genai-misc-issues",
            ["11.3/dev", "12.0/dev"],
        )
        apply_work_item_action_flags(item)

        self.assertEqual(item["selected_base_branch"], "12.0/dev")
        self.assertEqual(item["branch_name"], "12.0/fix/160229-doc-genai-miscellaneous-issues")
        self.assertFalse(item["planned_branch_conflict"])
        self.assertTrue(item["can_create_branch"])
        self.assertFalse(item["can_start_rerun"])
        self.assertEqual(item["create_branch_action_label"], "Create New Work Branch")

    def test_needs_plan_placeholder_is_regenerated_after_base_inference(self) -> None:
        item = {
            "id": 160229,
            "title": "DOC: GenAI miscellaneous issues",
            "type": "Task",
            "parent_type": "Bug",
            "tags": "",
            "iteration_path": "Product\\Gaivosa\\Sprint 13",
            "branch_name": "fix/160229-doc-genai-miscellaneous-issues",
            "selected_base_branch": "",
            "selected_work_type": "fix",
            "inferred_work_type": "fix",
            "plan_persisted": True,
            "auto_flow_runtime_status": "needs_plan",
        }

        apply_reference_branch_context(
            item,
            "12.0/feature/160229-genai-misc-issues",
            ["12.0/dev"],
        )

        self.assertEqual(item["selected_base_branch"], "12.0/dev")
        self.assertEqual(item["branch_name"], "12.0/fix/160229-doc-genai-miscellaneous-issues")


if __name__ == "__main__":
    unittest.main()
