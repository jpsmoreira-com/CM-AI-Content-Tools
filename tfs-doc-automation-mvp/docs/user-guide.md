# User Guide

Audience: technical writers using the TFS Documentation Automation dashboard from a portal devcontainer (DocumentationPortal, DeveloperPortal, and portals onboarded later). For internals, see the [Maintainers Guide](maintainers-guide.md).

## What The Tool Does — And Does Not Do

It helps you turn sprint work items into documentation draft PRs with an AI agent doing the first pass, while you keep every decision gate.

It does:

- list the work items assigned to the Content team for the selected portal and sprint;
- let you decide per item whether it needs documentation work, and on which base branch;
- create one work branch per item and a context package (work item tree, comments, linked PRs, diffs, referenced specs);
- run the configured AI agent on an isolated copy of the repository;
- after the agent reports a green light, commit, push, and open a **draft** PR with the required reviewer and the work item linked.

It never:

- merges or cherry-picks anything;
- publishes documentation;
- changes work item fields or state in TFS (it only links the draft PR to the work item);
- creates non-draft PRs;
- edits your own working copy — the agent works in a separate worktree.

## Create a Draft PR: End-to-End

### 1. Open the portal in a devcontainer

1. Open the target portal repository, such as `DocumentationPortal`, in VS Code.
2. Use **Rebuild and Reopen in Container** on first use, or after a devcontainer image update.
3. Wait for the setup to finish. It installs the pipeline, shared Content Team skills, and the dashboard dependencies.
4. Open the dashboard using the TFS Pipeline button in the VS Code status bar, or run the **Run TFS Pipeline** task.

### 2. Configure the connection once

Open **Settings > Connection** and confirm the following values.

- **Work Item Project** and **Discovery Area Path**: where the dashboard searches for Content work items.
- **Base URL**, **Repository Project**, and **Repository Name**: the TFS repository where the work branch and Draft PR will be created.
- **Agent Workspace Path**: the local clone of that target repository, available inside the current runtime.
- **Branch Chain**: the supported version and base branches, for example `12.0/dev`.

Save the settings. Then run **TFS Git Credentials Setup** and provide a TFS username and token with repository read/write access. The setup validates the credentials and persists them for later container rebuilds.

### 3. Configure the automation once

Open **Settings > Automation** and set the minimum values below.

- **Content Team Members**: add the TFS identities whose work items should appear in the dashboard.
- **Default Reviewer**: set the reviewer to add to generated Draft PRs.
- **Execution Runtime**: select `Devcontainer / native Linux` when the pipeline runs in a devcontainer.
- **Agent Provider**: select `GitHub Copilot CLI (autonomous)` unless your team has approved another provider.
- **Model Name**: select the approved model, for example `GPT 5.6 Terra`.
- **Agent Name**: enter the approved agent name, for example `Content AI Documentation`.
- **Run Executor Automatically**: enable it.
- **Preparation-Only Mode**: leave it disabled for automatic execution.
- **Context Capture**: enable it to include the parent work item, linked work items, PRs, diffs, and available reference material.

Save the settings. If the Copilot preflight requests device authorization, complete the browser authorization and save the settings again until the preflight succeeds.

### 4. Load and inspect a work item

1. Return to the dashboard.
2. Select the correct **Portal** and **Target Workspace**. The workspace must be the portal repository where documentation will be changed, not the tools repository.
3. Select an iteration path if required, then click **Load Work Items**.
4. Open the required work item.
5. Review **Work Item Context** and **Parent Work Item Context**. Check that the work item contains enough information for an accurate documentation change.

### 5. Plan and create the work branch

1. Confirm or change the suggested base branch and work type.
2. Save the plan.
3. Select **Create Work Branch**.

If the work item already has a related documentation branch or PR and you need a separate attempt, select **Create New Work Branch**. This creates a new `-rerun-<timestamp>` branch and preserves the earlier work for comparison.

### 6. Run the automatic flow

Start the configured agent from the work item detail panel. For multiple already-planned items, select them on the dashboard and use **Run Automatic TFS Flow for Selected**.

The dashboard then performs the following stages automatically:

1. Creates an isolated worktree for the work item.
2. Captures work item, parent, related work item, PR, diff, and reference-document context.
3. Runs the configured agent using the selected agent and model.
4. Validates the agent result and changed file list.
5. Commits and pushes approved changes.
6. Creates a Draft PR, adds the configured reviewer, and links the PR to the work item.

Do not make manual changes in the selected target workspace while a run is active. Each work item uses its own worktree, but the source clone must remain stable.

### 7. Monitor and review the result

The **Active Automation** summary shows work items that are in progress or queued. Open the work item detail panel to see the current stage, such as `Creating branch`, `Capturing context`, `Waiting for agent result`, `Validating result`, `Pushing changes`, or `Creating Draft PR`.

When the run completes, review the final report, changed files, validation result, commit, and Draft PR link. The pipeline stops at the Draft PR: review, final edits, approval, and merge remain part of the normal team process.

If the agent cannot identify a safe documentation change, the dashboard does not push or create a Draft PR. Review the captured context, update the work item if necessary, then use **Rerun Agent** or **Create New Work Branch**.

## The Context Package Viewer

Each card exposes `Context Package` once a handoff exists. It shows the generated `summary.md` (work item tree and PR overview), `INSTRUCTIONS.md` (the agent's playbook), and `manifest.json` without opening the repository. Use it to understand what evidence the agent had — and to spot missing context before rerunning.

If the rich capture fails, the run continues with the basic work item context and the agent is required to state the missing evidence in its notes.

## Settings You May Safely Change

`Settings > Automation`:

- **Context capture**: enable/disable rich capture, choose `parent` or `task` root mode, limit the number of work items walked, enable local PR diff capture.
- **Reference Documentation Workspace Path**: folder with specification documents the agent may consult (defaults to a `Documentation` folder next to the repositories).
- **Run Executor Automatically** for unattended agent execution.
- **Reviewer Resolution** defaults for the draft PR.

After onboarding, leave provider, model, execution runtime, workspace, and credential settings as configured by your team unless instructed otherwise.

## Troubleshooting

When something fails, the dashboard message ends with a **reference id** such as `(ref a3f9c1b2)`. Every event in the work item's History carries the same kind of id. Include it when you report a problem — it points support to the exact log lines.

For agent problems, open the work item card and use **Download Diagnostics Bundle** (a zip with the run state, the event timeline, the agent prompt, the agent result, the provider log and the bridge status). **View Agent Diagnostics** shows the same files in the browser. Agent failures also show an **error code** (for example `AGENT_NO_GREEN_LIGHT` or `PROVIDER_EXITED`) next to the guidance; the maintainers guide lists what each code means.

| Symptom | What to do |
| --- | --- |
| Dashboard shows "The background automation runner has failed N cycles in a row" | The runner cannot complete its cycle (usually TFS connectivity or credentials). Open `Settings > Automation` for the last error and its reference id, fix the cause, and the banner clears on the next successful cycle. |
| A page shows "Unexpected error" with a reference id | Report the reference id; the full stack trace is in the application log. |
| Settings reports the Copilot bridge is not installed | Rebuild/reopen the devcontainer so the bootstrap installs the extension. |
| Credential preflight fails | Re-run `TFS Git Credentials Setup` in `Settings > Connection`; check the token has code read/write scope. |
| "Workspace does not exist inside the current runtime" | The portal's workspace path points to a clone that is not mounted in this container; ask a maintainer to fix the portal configuration. |
| Agent status is `blocked` | Read the card's error text — usually the provider is misconfigured or automatic execution is disabled. |
| Capture package missing or partial | Capture failures are non-fatal; rerun the agent after checking TFS connectivity, or proceed and review the agent's missing-evidence notes. |
| Old worktrees piling up | Completed automation worktrees are removed after the draft PR; leftover ones under `<repos-parent>/.content-ai-worktrees/` can be removed by a maintainer. |
