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

## Getting Started

1. Open the portal repository in its devcontainer. The bootstrap installs the pipeline, the VS Code Copilot Bridge extension, and starts the dashboard on port `7001` (the port is shown in the terminal if it moved).
2. Open the dashboard in the browser and go to `Settings > Connection`.
3. Run `TFS Git Credentials Setup` with your TFS username and token. The credentials are stored in the container's Git credential store and mirrored to your persistent settings folder, so they survive container rebuilds. If your team pre-configured `CONTENT_AI_TFS_GIT_*` inputs, this step is already done.
4. Check `Settings > Automation`: the agent provider should be `VS Code Copilot Bridge (autonomous)` and the model `CM GPT` unless your team told you otherwise.

## The Sprint Workflow

1. **Select the portal and sprint** on the dashboard. Use the filters (type, state, iteration, closed items) to narrow the list.
2. **Open a work item card.** The details panel shows the parent item, description, acceptance criteria, attachments, and the inferred base branch and work type.
3. **Triage it.** Confirm or override the base branch and work type, then save the plan. Items that need no documentation can simply be left untouched or marked accordingly.
4. **Create the branch.** One branch per work item, named from the version prefix, work type, item id, and title.
5. **Run the agent.** The dashboard captures the context package and launches the provider. The card shows progress: prepared → launched → agent result → pushed → draft PR. With `Auto flow` enabled, the background runner continues automatically once the agent gives a green light.
6. **Review the structured result.** The agent's summary, changed files, evidence fields (which captured files, work items, PRs, and diffs it reviewed), and any reviewer notes are shown on the card. If it reports that no accurate documentation change could be made, nothing is pushed.
7. **Draft PR.** After push, the draft PR is created with the required reviewer and linked to the work item. From here the normal PR review process applies; the pipeline is done.
8. **Rerun when needed.** `Rerun Agent` starts a fresh agent run on the same branch, for example after you refine the work item or the agent result was rejected.

## The Context Package Viewer

Each card exposes `Context Package` once a handoff exists. It shows the generated `summary.md` (work item tree and PR overview), `INSTRUCTIONS.md` (the agent's playbook), and `manifest.json` without opening the repository. Use it to understand what evidence the agent had — and to spot missing context before rerunning.

If the rich capture fails, the run continues with the basic work item context and the agent is required to state the missing evidence in its notes.

## Settings You May Safely Change

`Settings > Automation`:

- **Context capture**: enable/disable rich capture, choose `parent` or `task` root mode, limit the number of work items walked, enable local PR diff capture.
- **Reference Documentation Workspace Path**: folder with specification documents the agent may consult (defaults to a `Documentation` folder next to the repositories).
- **Run Executor Automatically** and **Auto flow** toggles.
- **Reviewer Resolution** defaults for the draft PR.

Leave provider, model, execution runtime, worktree, and credential settings as configured by your team unless instructed.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Settings reports the Copilot bridge is not installed | Rebuild/reopen the devcontainer so the bootstrap installs the extension. |
| Credential preflight fails | Re-run `TFS Git Credentials Setup` in `Settings > Connection`; check the token has code read/write scope. |
| "Workspace does not exist inside the current runtime" | The portal's workspace path points to a clone that is not mounted in this container; ask a maintainer to fix the portal configuration. |
| Agent status is `blocked` | Read the card's error text — usually the provider is misconfigured or automatic execution is disabled. |
| Capture package missing or partial | Capture failures are non-fatal; rerun the agent after checking TFS connectivity, or proceed and review the agent's missing-evidence notes. |
| Old worktrees piling up | Completed automation worktrees are removed after the draft PR; leftover ones under `<repos-parent>/.content-ai-worktrees/` can be removed by a maintainer. |
