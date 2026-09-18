import unittest

from doc_automation.copilot import CopilotIntegrationError, _validate_commit_file_paths


class CommitPathValidationTests(unittest.TestCase):
    def test_accepts_normal_documentation_source(self) -> None:
        self.assertEqual(
            _validate_commit_file_paths(["docs/userguide/topic.md"]),
            ["docs/userguide/topic.md"],
        )

    def test_rejects_docsync_generated_content(self) -> None:
        with self.assertRaisesRegex(CopilotIntegrationError, "protected generated content"):
            _validate_commit_file_paths(["docs/includes/docsync/generated-topic.md"])

    def test_rejects_docsync_root(self) -> None:
        with self.assertRaisesRegex(CopilotIntegrationError, "protected generated content"):
            _validate_commit_file_paths(["docs/includes/docsync"])


if __name__ == "__main__":
    unittest.main()
