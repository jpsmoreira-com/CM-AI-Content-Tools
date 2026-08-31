# Content AI Pipeline Bridge

This private VS Code workspace extension executes queued `vscode_bridge` jobs created by the TFS Autonomous Pipeline.

It is intentionally separate from the Codex CLI provider. The extension uses the public VS Code Language Model API, reads a work item package from `.automation-context/copilot`, applies only validated workspace-relative edits, and writes the existing `agent-result.json` contract consumed by the dashboard.

The first use of the VS Code Language Model API may request Copilot consent from the signed-in VS Code user. This is a VS Code platform requirement and is not bypassed by the extension. Once consent is granted, queued jobs are processed without a per-work-item chat handoff.

## Job Contract

The dashboard creates `<workspace>/.automation-context/copilot/<branch>/bridge-job.json`. The bridge writes:

- `bridge-status.json` for diagnostics and preflight visibility;
- `bridge-job-state.json` while the job is running;
- `agent-result.json` when the job completes or fails.

The work item context, captured implementation evidence, repository instructions, and reference-document extracts remain in the same package directory.

