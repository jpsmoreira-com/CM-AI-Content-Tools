import unittest
from types import SimpleNamespace
from unittest.mock import patch

from doc_automation.copilot import (
    CopilotIntegrationError,
    _validate_commit_file_paths,
    build_agent_markdown,
    get_custom_agent_identifier,
    is_wsl_process_running,
)


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


class ConfiguredAgentTests(unittest.TestCase):
    def test_normalizes_configured_agent_name_for_cli_identifier(self) -> None:
        self.assertEqual(
            get_custom_agent_identifier("Content AI Documentation"),
            "content-ai-documentation",
        )

    def test_builds_copilot_cli_agent_profile(self) -> None:
        profile = build_agent_markdown(
            agent_name="Content AI Documentation",
            model_name="GPT 5.6 Terra",
            target="github-copilot",
        )
        self.assertIn('target: "github-copilot"', profile)
        self.assertIn('model: "gpt-5.6-terra"', profile)
        self.assertNotIn('tools: ["changes"', profile)


class ProviderProcessTests(unittest.TestCase):
    @patch("doc_automation.copilot._run_wsl_script")
    def test_zombie_process_is_not_running(self, run_script) -> None:
        run_script.return_value = SimpleNamespace(returncode=1)

        self.assertFalse(is_wsl_process_running("Ubuntu", "1088"))
        self.assertIn("/proc/1088/stat", run_script.call_args.args[1])

    @patch("doc_automation.copilot._run_wsl_script")
    def test_live_process_is_running(self, run_script) -> None:
        run_script.return_value = SimpleNamespace(returncode=0)

        self.assertTrue(is_wsl_process_running("Ubuntu", "1088"))


if __name__ == "__main__":
    unittest.main()
