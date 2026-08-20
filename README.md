# TFS Documentation Automation MVP

Isolated MVP project for validating an LLM-assisted pipeline that analyzes TFS work items, prepares documentation work branches, and creates draft PR workflows under human supervision.

This MVP started from a minimal copy of the Cherry Picks dashboard so it could reuse the existing TFS/Azure DevOps Server connection, authentication behavior, portal configuration, and initial data access patterns. The active copy now lives under the centralized Content AI projects workspace.

## Status

FastAPI dashboard baseline implemented with the automation control plane, background runner, provider handoff flow, and an integrated read-only Cherry Pick propagation page.

## Documentation

- [Technical Design](docs/technical-design.md)
- [Project Memory](.agents/memory.md)

## Current Stack

- FastAPI
- Jinja2 server-rendered templates
- SQLite for local workflow state
- TFS/Azure DevOps Server REST integration
- `.env` runtime settings
- VS Code Copilot / CM GPT automation integration for WSL workspaces
- configurable agent provider handoff for VS Code Copilot or local CLI executors

## Language Convention

All official project documentation, README files, and inline code comments must be written in English.

## Agent Memory

The `.agents/memory.md` file stores persistent project context, decisions, planned tasks, and open items. Update it whenever meaningful progress is made or the project direction changes.

## Running The Dashboard

```powershell
cd C:\CM-REPO\Content\CM-AI-Content-Skills\projects\tfs-doc-automation-mvp
python run_server.py
```

Or:

```powershell
.\run_dashboard.ps1
```

The default dashboard port is `7000` so it does not conflict with MkDocs, which commonly uses `8000`. If the preferred port is already in use or blocked, `run_server.py` reads `.env` and can automatically move to the next free port when automatic fallback is enabled.

## Implemented In This Slice

- separate `Dashboard` and `Settings` pages;
- portal selection using the existing configuration file;
- separation between work item source and target documentation repository;
- Content-team member based work item discovery;
- area-path scoped work item discovery under the configured development area;
- optional current iteration resolution through TFS team settings;
- optional current iteration pre-filtering on top of assigned-task discovery;
- work item normalization including parent type and assigned reviewer identity;
- branch inference from work item tags and branch-chain configuration;
- manual base-branch and work-type override in the dashboard;
- local SQLite persistence for work item planning and action state;
- work branch creation through TFS refs;
- CM GPT automation preparation on the configured WSL workspace for each portal;
- VS Code Copilot execution for environments where CM GPT is available as a VS Code-compatible custom model or mode;
- configurable CLI provider handoff for Codex, Claude, or another local command that can write the expected result file;
- blocking behavior for Microsoft 365 Copilot Desktop because it is not an automation-capable local repository executor;
- persisted agent result tracking through `agent-result.json`;
- final task reports under the configured reports folder;
- performance timing logs under `data/performance.log`;
- automatic commit, push, and draft PR continuation after the agent gives green light;
- durable background reconciliation that resumes unfinished automatic flows from SQLite after restarts;
- optional continuous discovery mode for open current-iteration tasks;
- draft PR creation through TFS pull request APIs;
- formal draft PR association to the parent work item when one exists, with the task itself as fallback;
- required reviewer assignment based on the team member assigned to the work item.
- integrated read-only Cherry Pick propagation analysis under the `Cherry Picks` navigation tab;
- `.env`-driven runtime settings editable from the dashboard;
- reviewer override mapping through `.env` for cases where work item identities must resolve to specific PR reviewers.

Operational actions stay on the main dashboard. Configuration concerns such as portal settings, authentication, runtime settings, and reviewer mapping live on the `Settings` page.

## Cherry Pick Propagation Tab

The `Cherry Picks` page ports the useful propagation analysis from the previous Streamlit dashboard into the FastAPI application. It uses the selected portal configuration, branch chain, authentication mode, lookback window, and work item verification setting.

The page is intentionally read-only:

- it analyzes pull requests across the configured branch chain;
- it groups related PRs by work item links or branch naming fallback;
- it highlights missing, open, abandoned, and completed propagation states;
- it supports scope, status, branch, and sort filters;
- it does not create branches, create PRs, update work items, or execute cherry-picks.

This keeps the previous review workflow available as a dashboard component without requiring a second Streamlit process or a second devcontainer task.

## Portal Configuration Note

Each portal now represents a target documentation repository plus a work item source.

- `project` and `repository` identify the target repository where branches and PRs are created.
- `work_item_project` and `work_item_team` identify the board/team used to resolve the current sprint.
- `work_item_area_path` defines the development area subtree used to load candidate work items, for example `Product\Development`.
- `copilot_workspace_path` identifies the WSL clone that CM GPT should use when the dashboard launches a Copilot session.
- Content team members are configured in `.env` and define which assigned tasks are loaded into the dashboard.

## CM GPT Integration Notes

The agent step is designed as an automatic pipeline step. A provider is valid only when it can edit the configured local WSL repository without user intervention and can write the expected `agent-result.json` file.

Microsoft 365 Copilot Desktop is not currently treated as a valid automation provider. It exposes the company `CM GPT` agent to a user-facing chat surface, but this MVP does not have a supported connector that lets that desktop agent check out branches, edit local files, run validations, and report changes back to the dashboard. If it is selected, the dashboard blocks the run instead of reporting a successful automation.

For VS Code Copilot:

- a custom Copilot agent file is generated under the WSL user profile;
- a work-item-specific context package is generated under the target repository `.automation-context/copilot/<branch>/` directory, including Markdown, JSON, and HTML exports of the work item content;
- a rich capture package is generated under `.automation-context/copilot/<branch>/capture/`, including the parent/root work item tree, comments/history, linked PR metadata, review comments, changed files, available local diffs, `summary.md`, `INSTRUCTIONS.md`, and `manifest.json`;
- repository instruction files are copied into `.automation-context/copilot/<branch>/repo-instructions/` with an index so the handoff is auditable even when the editor session does not attach files directly;
- referenced specification documents from the configured reference documentation workspace are resolved into `.automation-context/copilot/<branch>/reference-docs/`, including an index and text extracts for readable `.docx`/`.docm` files;
- the `.automation-context/` root is added to the repository's local `.git/info/exclude` file so the context package is visible to VS Code agents but is not committed or shown as normal untracked work;
- the dashboard switches the configured WSL clone to the selected work branch;
- the dashboard validates that the workspace is on the expected work branch before launching CM GPT;
- the dashboard can submit a `code chat` prompt using the generated custom agent when strict model-safety mode is disabled;
- the prompt names the configured Settings `Agent Name` and `Model Name` as the functional agent/model contract; the generated VS Code transport mode is only used to deliver the handoff and must not be treated as a reason to stop the run by itself;
- strict model-safety mode is blocked for the automatic pipeline because it prepares context only and does not execute repository edits.

For GitHub Copilot CLI, the recommended autonomous Copilot provider:

- select `GitHub Copilot CLI (autonomous)` in `Settings`;
- select an entitled Copilot CLI model in `Model Name` (for example, the configured GPT 5.6 model);
- complete the one-time GitHub Copilot CLI device authorization when Settings reports that authentication is missing, or provide a supported fine-grained token through `COPILOT_GITHUB_TOKEN` for unattended environments;
- the provider runs non-interactively inside the configured runtime, receives the complete generated context package, and is allowed to read, edit, and run validation commands only;
- the provider is explicitly denied Git commit, push, reset, clean, and removal commands. The dashboard owns the post-green-light commit, push, and Draft PR stages.

For other CLI providers such as Codex, Claude, or a custom command:

- select the provider in `Settings`;
- configure the `CLI Command Template`;
- use placeholders such as `{{prompt_path}}`, `{{workspace_path}}`, `{{workspace_unc_path}}`, `{{branch_name}}`, `{{model_name}}`, and `{{agent_result_path}}`;
- the command is launched through the configured execution runtime and must write the expected `agent-result.json`.
- for WSL repositories, prefer a native WSL CLI executable and `{{workspace_path}}`; using a Windows CLI against `{{workspace_unc_path}}` can fail when the agent process resolves the working directory.
- for Codex CLI test runs in a devcontainer, the CLI must exist inside the container runtime. The bootstrap uses `CODEX_HOME` (normally `/home/vscode/.codex`) and `NPM_CONFIG_PREFIX` (normally `/home/vscode/.npm-global`) so the container can reuse mounted Codex auth state while keeping the executable available on the container `PATH`.

The automatic continuation contract is:

1. The agent edits files on the current work branch.
2. The agent writes `.automation-context/copilot/<branch>/agent-result.json`.
3. The result file must set `green_light` to `true`, list repository-relative `changed_files`, include a concise final-report payload, and list repository instructions read in `instruction_files_read`.
4. The dashboard validates that required repository instructions were acknowledged.
5. The dashboard runs independent validation, including `git diff --check`, Markdown linting when available, and local Markdown/Mermaid link checks.
6. The dashboard writes a final report under the configured reports folder, using the parent work item ID and task ID in the folder name.
7. The dashboard commits only the listed files.
8. The dashboard pushes the work branch.
9. The dashboard creates the draft PR with the final automation report in the PR description, links the parent work item when available, and adds the configured required reviewer.

If `agent-result.json` is missing or the provider process is still running, the dashboard keeps the item active and the background runner polls again later. If the provider process exits before writing the result file, the dashboard records the provider log as an agent-result error and stops counting the item as an active flow.

If the agent writes repository changes but the result is not green-lighted, misses required instruction acknowledgements, fails dashboard-managed validation, or gives green light without listing changed files, the automatic flow can launch one repair attempt on the same dirty work branch. The repair prompt reuses the original context package, points the agent to the previous `agent-result.json`, lists dashboard validation failures, and requires the agent to fix the local changes before rewriting the result file. The repair attempt is reserved atomically in local state before the provider is relaunched, so duplicate dashboard/background workers cannot open multiple automatic repairs for the same item.

For VS Code-style providers, the dashboard waits for `agent-result.json` to remain unchanged for a short stability window before validation. This prevents the runner from stopping a flow while the agent is still rewriting or replacing its final result file.

If a VS Code handoff remains in `waiting` without an `agent-result.json` past the stale-launch grace window, selecting the automatic flow again regenerates the context package and relaunches the VS Code chat. This avoids orphaned waits caused by a previous chat handoff that never actually started or never wrote a result.

When a work item needs to be processed again because new information was added after a previous PR, use `Rerun on New Branch` from the work item detail panel. The rerun creates a fresh branch with a `-rerun-<timestamp>` suffix, clears only the local automation state for the new attempt, ignores older PR links for that rerun, and starts the automatic flow again. Existing PRs remain untouched for comparison and audit history.

The background runner periodically resumes any persisted automatic flow that has not reached a PR yet. This means the flow does not depend on the browser tab or on the original HTTP request staying alive. When `Continuous Mode` is enabled in Settings, the runner also checks for open current-iteration tasks at the configured discovery interval and starts the automatic flow for newly discovered eligible items.

The reconciliation step also resumes recoverable states that lost the active-flow flag after a process restart or transient failure, including green-lighted agent results that have not yet been pushed, pushed branches without a draft PR, and the transient result-stability wait state. This prevents a valid `agent-result.json` from being stranded before commit, push, or PR creation.

`run_worker.py` exposes the same runner loop for a future dedicated service deployment. The dashboard currently starts an embedded runner for local MVP use. Production should run a single active runner instance per automation environment so two processes do not race to launch the same work item.

The current handoff also:

- accepts WSL Linux paths, home-relative paths, and UNC paths such as `\\wsl.localhost\Ubuntu\workspaces\DocumentationPortal-#01`;
- includes `AGENTS.md`, `.github/copilot-instructions.md`, and Markdown files under `.agents` when they exist in the target repository, and requires the agent to acknowledge reading them;
- starts from the captured work item tree when available, so the agent can inspect the broader parent item, sibling DOC tasks, implementation PRs, review comments, and diffs before editing;
- lists work item attachment links, hyperlink relations, image sources found in the work item HTML, and referenced `.docx` files;
- creates a reference-documentation package for detected specification names so the agent can read `reference-docs/index.md` and packaged text extracts before reporting that a spec could not be found;
- serves protected TFS image attachments through the local `/tfs-assets` proxy so the dashboard can render images with the configured TFS credentials;
- can point CM GPT to a shared reference documentation workspace, for example `/workspaces/Documentation`.

Context capture can be configured from `Settings > Automation`:

- enable or disable rich capture;
- choose whether the capture starts from the selected task or from its parent work item when available;
- limit the maximum number of work items walked in the tree;
- enable or disable local PR diff capture;
- configure workspace scan roots used to discover local clones for PR diffs.

After a provider handoff is prepared, the work item detail panel exposes `View Context Package`. This opens a read-only dashboard page for the generated `capture/summary.md`, `capture/INSTRUCTIONS.md`, and `capture/manifest.json` files so reviewers can audit exactly which evidence was sent to the agent.

Before using the CM GPT action, configure these values in `Settings`:

- portal `CM GPT Workspace Path In WSL`;
- runtime `Execution Runtime`, left as `Devcontainer / native Linux` for the default one-click setup or changed to `Windows host via WSL` when the dashboard process runs on Windows and must call `wsl.exe`;
- runtime `WSL Distro`;
- runtime `Copilot Provider`, set to `VS Code Copilot` for automatic execution;
- runtime `Initial Agent Prompt Template`, used to generate each work item prompt;
- runtime `Final Reports Path`, used to store final task reports;
- runtime `Agent Name` set to `CM GPT`;
- runtime `Run Executor Automatically`, enabled;
- runtime `Strict CM GPT Safety Mode`, disabled only when VS Code Copilot can enforce the approved `CM GPT` model;
- runtime `Open Workspace In WSL Remote`, which avoids opening WSL repositories as local UNC folders in VS Code;
- runtime `Auto-Accept Edit Delay`, which maps to VS Code `chat.editing.autoAcceptDelay` for automatic acceptance of generated edits.

Temporary test mode:

- turning off `Strict CM GPT Safety Mode` disables the CM GPT-only guard and lets the dashboard use the configured `Model Name`;
- this is intended only for end-to-end pipeline validation with explicitly selected VS Code Copilot models such as `GPT-4o` or `GPT-4.1`;
- re-enable the strict guard before processing proprietary work items in the approved production flow.

## Content AI Devcontainer Bootstrap

For the preferred prebuilt Docker image flow, use `content-ai-post-create` as the target repository `postCreateCommand`. The image contract, required packages, persistent paths, and post-create responsibilities are documented in [docs/docker-image-post-create.md](docs/docker-image-post-create.md).

The generated `tfs-autonomous-pipeline dashboard` and `tfs-autonomous-pipeline worker` commands perform a safe Content AI project sync before starting. Local changes in the runtime copy are backed up and auto-stashed by default, so users should not need to run a separate sync task before the normal Run TFS Pipeline action. Use `tfs-autonomous-pipeline sync-project` only for manual diagnostics or when auto-stash is intentionally disabled.

Target repositories can install the pipeline and managed Content AI assets from a devcontainer by calling:

```bash
CONTENT_AI_REPO_URL=<repo-url> \
CONTENT_AI_TARGET_WORKSPACE="$PWD" \
bash /workspaces/CM-AI-Content-Skills/projects/tfs-doc-automation-mvp/scripts/devcontainer-bootstrap.sh
```

The recommended devcontainer layout keeps the target repository mounted at `/app`, bind-mounts the WSL host `/workspaces` folder into the container, keeps the central tool checkout at `/workspaces/CM-AI-Content-Skills`, and keeps persistent local settings under `/workspaces/.content-ai-settings/tfs-doc-automation-mvp`. This avoids cloning the tool into the target repository or into an ephemeral container-only folder, and it keeps Git worktree metadata visible when the target workspace is opened from a linked worktree.

For Git Credentials authentication inside a devcontainer, provide one of these optional bootstrap inputs before rebuild:

- `CONTENT_AI_HOST_GIT_CREDENTIALS_PATH`, pointing to a mounted `.git-credentials` file that can be copied into the devcontainer user home;
- `CONTENT_AI_TFS_GIT_USERNAME` plus `CONTENT_AI_TFS_GIT_PASSWORD` or `CONTENT_AI_TFS_GIT_TOKEN`, used to write a local `~/.git-credentials` entry for `CONTENT_AI_TFS_HOST`;
- `CONTENT_AI_TFS_HOST`, when the TFS host is different from `tfs-product.cmf.criticalmanufacturing.com`.
- `CONTENT_AI_TFS_VERIFY_SSL`, when the devcontainer should override the default internal setting for TFS SSL verification;
- `CONTENT_AI_TFS_CA_BUNDLE_PATH`, when the devcontainer has a mounted corporate CA bundle that Python `requests` should trust.
- `CONTENT_AI_SETTINGS_PATH`, when the devcontainer should persist `.env`, `config/tfs_dashboard.local.json`, and the local Git credential store mirror somewhere other than `/workspaces/.content-ai-settings/tfs-doc-automation-mvp`.
- `CONTENT_AI_AUTO_STASH_ON_UPDATE=false`, when the centralized runtime copy should stop and ask for manual review instead of auto-stashing local tool changes before updating.

If those inputs are not configured, open `Settings > Connection` after the dashboard starts and use `TFS Git Credentials Setup`. That setup writes the provided username and token/password through `git credential approve` into the devcontainer user's Git credential store, mirrors the store to `CONTENT_AI_SETTINGS_PATH/git-credentials`, then validates both dashboard credential lookup and `git ls-remote --heads origin`. Host Windows/GCM credentials are not assumed to be available inside Linux containers.

The bootstrap:

- clones or updates the centralized `CM-AI-Content-Skills` checkout;
- restores `CONTENT_AI_SETTINGS_PATH/git-credentials` into the devcontainer user's `~/.git-credentials` when available, then validates or prepares TFS Git credentials when one of the optional credential sources above is configured;
- writes TFS SSL runtime defaults for the devcontainer. Internal devcontainers default to `DOC_AUTOMATION_TFS_VERIFY_SSL=false` unless `CONTENT_AI_TFS_VERIFY_SSL` is provided;
- installs Codex CLI and GitHub Copilot CLI into the devcontainer user's npm prefix when `TFS_AUTONOMOUS_INSTALL_CODEX_CLI=true` and `TFS_AUTONOMOUS_INSTALL_GITHUB_COPILOT_CLI=true` respectively, and the executables are missing;
- installs the pipeline requirements into `~/.venvs/tfs-doc-automation-mvp`;
- creates a `tfs-autonomous-pipeline` wrapper in `~/.local/bin`;
- makes the wrapper sync the central Content AI runtime copy before starting the dashboard or worker;
- creates local runtime files for the target devcontainer, including `.env` and `config/tfs_dashboard.local.json`;
- restores those local runtime files from `CONTENT_AI_SETTINGS_PATH` when available, then mirrors dashboard saves back to that folder;
- points the active portal workspace to the target repository workspace, normally `/app` when the devcontainer mounts the repository there;
- keeps the central tool checkout at `CONTENT_AI_REPO_PATH`, by default `/workspaces/CM-AI-Content-Skills`;
- syncs managed AI assets into the target repository under `.agents/content-ai/`;
- copies the managed root `AGENTS.md` into the target repository root so editor agents can discover the shared instructions immediately.

Managed assets are copied from:

- `ai/instructions/AGENTS.md`, with fallback to root `AGENTS.md` if a custom asset repository does not provide the managed target baseline;
- `ai/manifest.json`;
- `ai/skills/`;
- `ai/agents/`;
- `ai/instructions/`.

By default, the sync script overwrites the target repository root `AGENTS.md` with the managed Content AI version and keeps a copy under `.agents/content-ai/AGENTS.md`. This makes the shared instructions visible to editor agents that only discover root-level instruction files. Set `CONTENT_AI_SYNC_ROOT_AGENTS=false` before running the bootstrap if a repository must keep its own root `AGENTS.md`.

The managed `.agents/content-ai/` folder is added to the local `.git/info/exclude` file. The root `AGENTS.md` is also locally excluded when it is untracked. If a target repository already tracks `AGENTS.md`, the sync marks it `skip-worktree` after writing the managed file so the bootstrap does not leave the repository dirty and block automation safety checks.

The generated `config/tfs_dashboard.local.json` is intentionally ignored by Git. It lets each devcontainer point the dashboard to its own workspace without changing the shared `config/tfs_dashboard.json` baseline.

This supports scenarios where the development team owns the sprint board while documentation changes happen in `DocumentationPortal` or `DeveloperPortal`.

Performance timings are written to `data/performance.log`. The most useful events while diagnosing slow page loads are `dashboard.load`, `dashboard.work_item_query`, `dashboard.repository_enrichment`, and `tfs.request`.

Note: current-iteration auto-resolution depends on the selected team having iterations configured in TFS team settings. If TFS returns no current iteration, leave the portal iteration team empty and narrow the dashboard with a manual iteration path. This keeps the initial dashboard render fast and avoids a slow TFS team-settings call that cannot produce a useful sprint path.

## Pull Request Context Capture

The rich context capture package includes linked implementation pull requests from the captured work item tree. For each PR, the package stores:

- PR metadata, description, changed files, commits, and review comments;
- a `diff.patch` generated from a local clone when the implementation repository exists in the configured workspace scan roots;
- a TFS API fallback diff when no local clone is available. The fallback compares the PR `lastMergeTargetCommit` and `lastMergeSourceCommit` through the Git Items API and emits a synthetic unified diff for text files.

The API fallback keeps context size bounded and skips binary, unavailable, or very large files rather than failing the full capture.

The dashboard list is optimized as a paginated summary-first view. Initial renders use WIQL plus a lightweight work item batch for the visible page, skip remote branch/PR scans, and rely on local persisted state for card-level status. Full work item details, parent information, remote repository validation, and action forms are loaded on demand when a card is opened.

## Copied Baseline

The following files were copied from the Cherry Picks dashboard:

- `app.py`
- `tfs_dashboard.py`
- `requirements.txt`
- `run_dashboard.ps1`
- `.gitignore`
- `config/tfs_dashboard.json`

The goal is to keep refactoring this baseline incrementally, with reusable TFS services separated from the dashboard layer and with future LLM-driven editing added on top of the current branch/PR workflow.
