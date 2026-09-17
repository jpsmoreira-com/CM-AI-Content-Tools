# Maintainers Guide

Audience: the team that maintains and evolves `tfs-doc-automation-mvp`. For the technical-writer workflow, see the [User Guide](user-guide.md). For the original design rationale, see the [Technical Design](technical-design.md).

## What This Tool Is

A FastAPI + SQLite pipeline that reads Content-team work items from TFS/Azure DevOps Server, prepares one documentation work branch per selected item, captures a rich context package, hands off to an AI agent provider that edits an isolated worktree, and — after a green-lighted structured result — commits, pushes, and creates a draft PR with the required reviewer and work item association. Humans keep the gates; the pipeline never merges, publishes, or changes work item fields.

## Architecture Map

| Module | Lines (approx.) | Responsibility |
| --- | --- | --- |
| `doc_automation/services.py` | ~5300 | Application layer: discovery, flow orchestration, validation, commit/push/PR, settings persistence. The largest concentration of logic — read this first. |
| `doc_automation/copilot.py` | ~3000 | Provider handoff: context package assembly, isolated worktree management, provider launch (bridge, CLIs, legacy VS Code), `agent-result.json` handling. |
| `doc_automation/web.py` | ~1150 | FastAPI routes and form handling (see route table below). |
| `doc_automation/tfs_client.py` | ~1130 | TFS/Azure DevOps Server REST client (WIQL, work items, branches, PRs). |
| `doc_automation/storage.py` | ~1100 | SQLite persistence of per-work-item workflow state. |
| `doc_automation/context_capture.py` | ~1030 | Work item tree / PR capture package generation. |
| `doc_automation/config.py` | ~830 | Portal + runtime settings load/save, `.env` handling, `CONTENT_AI_WORKSPACE_ROOT` resolution. |
| `doc_automation/cherry_picks.py` | ~650 | Read-only cherry-pick propagation analysis. |
| `doc_automation/branching.py` | ~120 | Branch inference from tags/versions, branch name generation. |
| `doc_automation/orchestrator.py` | ~105 | Background reconciliation loop (`run_worker.py` / embedded runner). |
| `doc_automation/telemetry.py` | ~65 | Performance timing log (`data/performance.log`). |

Entry points: `main.py` (FastAPI app), `run_server.py` (port fallback wrapper), `run_worker.py` (background worker). In a bootstrapped devcontainer these are wrapped as `tfs-autonomous-pipeline dashboard|worker`.

## Request Flow

1. Dashboard (`/`) lists candidate work items via WIQL + lightweight batch reads; card details load on demand.
2. Triage (`/work-items/{id}/plan`) stores base branch, work type, and planned branch name in SQLite.
3. Branch creation (`/work-items/{id}/branch`) creates the ref through the TFS API.
4. Agent run (`/work-items/{id}/copilot`) builds the context + capture package, creates an isolated worktree under `<repos-parent>/.content-ai-worktrees/<repo>/<branch-slug>`, and launches the configured provider.
5. The orchestrator polls for `agent-result.json`; a green light with `ready_for_push` enables commit/push (`/work-items/{id}/commit-push`) and draft PR creation (`/work-items/{id}/draft-pr`), which also assigns the required reviewer and links the PR to the work item.
6. Final reports land under the configured reports path; `/work-items/{id}/report` renders them.

## Routes

| Route | Method | Purpose |
| --- | --- | --- |
| `/` | GET | Dashboard (work item list, filters) |
| `/cherry-picks` | GET | Read-only cherry-pick propagation page |
| `/work-items/{id}/details` | GET | On-demand card details panel |
| `/work-items/{id}/report` | GET | Final report viewer |
| `/work-items/{id}/capture` | GET | Context package viewer |
| `/work-items/statuses` | GET | Card status refresh (JSON) |
| `/tfs-assets` | GET | Authenticated TFS asset proxy (images/attachments) |
| `/settings` | GET | Settings page |
| `/settings/runtime` | POST | Save runtime settings (persisted to `.env`) |
| `/settings/portal` | POST | Save portal configuration |
| `/settings/portal/workspace` | POST | Save per-portal workspace path |
| `/auth/pat`, `/auth/git-credentials` | POST | Credential setup flows |
| `/automation/run-cycle` | POST | Trigger one background reconciliation cycle |
| `/work-items/{id}/plan\|branch\|copilot\|agent-result\|commit-push\|draft-pr\|rerun` | POST | Pipeline step actions |
| `/work-items/auto-flow` | POST | Toggle automatic continuation for an item |

## Configuration Layers

1. `config/tfs_dashboard.json` — committed portal definitions (see Portal Onboarding).
2. `config/tfs_dashboard.local.json` — git-ignored local overrides, written by Settings and restored from `CONTENT_AI_SETTINGS_PATH`.
3. `.env` — runtime settings (`DOC_AUTOMATION_*`), written by `Settings > Save`; template in `.env.example`.
4. `CONTENT_AI_*` environment variables — devcontainer bootstrap inputs (see `.env.example` and [docker-image-post-create.md](docker-image-post-create.md)). `CONTENT_AI_WORKSPACE_ROOT` defines the shared parent folder of all checkouts; unset falls back to `/workspaces` with a logged warning.
5. Persistent non-Git state lives outside the checkout under `CONTENT_AI_SETTINGS_PATH` (default `<repos-parent>/.content-ai-settings/tfs-doc-automation-mvp`): `.env`, local config, Git credential mirror, logs.

## Agent Providers

- `vscode_bridge` (shipped default): the `vscode-copilot-bridge/` VS Code extension, installed by the bootstrap. The dashboard queues a job file; the bridge drives the Copilot session and reports via `bridge-status.json`. Rebuild the `.vsix` with `scripts/build-vscode-copilot-bridge.sh`.
- `copilot_cli`: GitHub Copilot CLI, recommended for fully headless runs; denied Git write commands — the dashboard owns commit/push/PR.
- `codex_cli` / `claude_cli` / `custom_cli`: generic CLI template execution (`CLI Command Template` with `{{prompt_path}}`, `{{workspace_path}}`, ... placeholders).
- `vscode` (legacy): drives Windows VS Code Chat; only functional when `DOC_AUTOMATION_EXECUTION_RUNTIME=windows_host`.

Legacy note: databases written before the `m365_desktop` provider was removed may still contain `desktop_prepared` statuses; `services.py` treats them as blocked and asks for a rerun with an automation-capable provider.

## Portal Onboarding

A portal entry in `config/tfs_dashboard.json` requires: `base_url`, `project`, `repository` (target docs repo), `work_item_project`, `work_item_area_path`, `branch_chain` (ordered version branches), `copilot_workspace_path` (local clone used by the agent), `auth_mode`, and the cherry-pick tuning fields. `get_portal_config` matches on `repository`.

Known risk: `get_portal_config` silently falls back to the first portal when the requested name is not found — verify portal names carefully when adding entries, and consider making the fallback an error as part of multi-portal hardening.

| Portal | Status | Missing inputs |
| --- | --- | --- |
| DocumentationPortal | Configured (default) | — |
| DeveloperPortal | Configured | — |
| CustomerPortal Help | Not configured | Repository name, project, branch chain, area path, workspace clone, validation commands |
| Information Center | Not configured | Same as above |
| Apps Center | Not configured | Same as above |
| Other portals | Not configured | Same as above |

Onboarding a new portal is a config + clone exercise: add the entry, provision the target repository clone in the shared workspace, and confirm branch-chain and reviewer conventions with the owning team.

## Known Debt

- **No automated tests.** Verification is manual (compile + smoke-run + recorded end-to-end validations in `.agents/memory.md`). Recommended starting points for a test suite: `branching.py` (pure functions), `config.py` normalization/env round-trip, and `tfs_client.py` against recorded REST responses.
- **Logic concentration**: ~47% of the Python lives in `services.py` + `copilot.py`. Prefer extracting cohesive services over growing either file.
- The presentation mockups in `presentation-assets/` (`agent-handoff.html`, `agent-output.html`, `context-package.html` and PNGs) are hand-built demo slideware, not live UI.
