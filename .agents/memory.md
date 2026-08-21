# Project Memory - TFS Documentation Automation MVP

Last updated: 2026-08-21

## Purpose

This file preserves project context, decisions, progress, pending work, and useful constraints for any LLM or developer working on this MVP later.

Keep this file concise, current, and factual. Update it whenever the project direction changes, a meaningful implementation step is completed, or a pending task becomes obsolete.

## Project Context

The project explores an automation pipeline for documentation work items in TFS/Azure DevOps Server.

Current problem:

- At the start of each sprint, documentation team members manually inspect development work items.
- Many work items only require very small documentation changes, or no documentation change at all.
- This manual triage consumes time that could be used for larger documentation work such as new feature docs and tutorials.

Target solution:

- Read new/current sprint work items from TFS.
- Classify their likely documentation impact.
- Let a human select candidates.
- Create isolated branches per work item.
- Use an approved LLM workflow, likely Copilot in the company environment, to suggest documentation changes.
- Run validations.
- Create PRs only after human approval.

## Current Repository

Path:

```text
C:\CM-REPO\Content\CM-AI-Content-Skills\projects\tfs-doc-automation-mvp
```

This is the active project copy for the Content AI projects workspace. All future implementation work for this initiative must happen under `C:\CM-REPO\Content\CM-AI-Content-Skills\projects`.

This project was originally created as an isolated copy of the existing Cherry Picks dashboard, then evolved into the FastAPI automation pipeline. The original dashboard source should remain untouched unless explicitly requested.

Copied baseline files:

- `app.py`
- `tfs_dashboard.py`
- `requirements.txt`
- `run_dashboard.ps1`
- `.gitignore`
- `config/tfs_dashboard.json`

New project files:

- `README.md`
- `docs/technical-design.md`
- `.agents/memory.md`

## Related Existing Project

Original dashboard:

```text
C:\CM-REPO\Content\CM-AI-Content-Skills\projects\tfs-cherry-pick-dashboard
```

Useful existing capabilities from that project:

- TFS/Azure DevOps Server client.
- Windows Credentials and PAT authentication.
- Portal/repository configuration.
- PR and work item reads.
- Streamlit UI.

Important: the Cherry Picks dashboard source is used as a reference. The integrated Cherry Pick propagation page in `tfs-doc-automation-mvp` is read-only and should not create branches, PRs, work item updates, or cherry-picks.

## Current Technical Direction

- Persisted automatic flows resume after dashboard restarts using the original work item ID. A rerun clears any prior result-repair metadata so the dashboard status only describes the active rerun.
- Autonomous CLI providers run in a dedicated per-branch Git worktree under `/workspaces/.content-ai-worktrees`. The configured workspace acts only as a dispatcher and is never checked out or modified by a work-item run.
- When an agent result file already exists, the worker validates and continues it before considering any relaunch. This prevents the agent's own uncommitted changes from being mistaken for unrelated workspace changes.
- After a successful Draft PR, the pipeline removes its isolated worktree and prunes Git worktree metadata. Reports remain in the configured persistent reports directory.
- The Active Automation panel separates queued work items from work items running inside a worktree. Queue state is persisted locally and records when a work item is waiting for the shared repository lock.

Framework decision:

- Use FastAPI with server-rendered Jinja templates for the MVP dashboard.
- Keep business logic outside the web layer so the project can later move to a richer frontend if needed.

Architecture direction:

```text
Dashboard / Control Plane
  -> TFS Adapter
  -> Documentation Impact Classifier
  -> Repository Adapter
  -> LLM Agent Adapter
  -> Validation Runner
  -> Approval Gate

Background Runner / Worker
  -> resumes persisted flows
  -> polls agent results
  -> advances green-lighted items to push and draft PR
  -> optionally discovers new open work items continuously
```

Initial philosophy:

- Human-in-the-loop by default.
- No automatic PRs without explicit approval for final publication.
- TFS branch and draft PR actions are allowed from the dashboard when explicitly triggered.
- Prefer supported VS Code and Copilot integration points over undocumented Copilot automation APIs.
- Treat the dashboard as a control surface, not as the only process responsible for progressing long-running automation.

## Decisions Made

1. Create a separate project instead of modifying the Cherry Picks dashboard directly.
2. Reuse a minimal copy of the Cherry Picks dashboard as the initial technical base.
3. Document the MVP before implementing new automation behavior.
4. Start from the Cherry Picks dashboard baseline, then move the MVP to FastAPI for operational actions.
5. Add this `.agents/memory.md` file as persistent context for future LLM-assisted development.
6. Write all official project documentation, README files, and inline code comments in English.
7. Replace the initial Streamlit direction with a FastAPI dashboard because direct operational actions are part of the MVP scope.
8. Store runtime parameters in a local `.env` file and make them editable from the dashboard.
9. Treat the work item source board and the target documentation repository as separate configuration concerns.
10. Keep configuration concerns off the main dashboard and group them in a dedicated Settings page.
11. Load candidate work items primarily by Content-team assignee, with current-sprint filtering as an optional pre-filter instead of the only discovery strategy.
12. Normalize `work_item_team` from a plain team name, a longer path, or a sprint-board URL so the current iteration lookup stays user-friendly.
13. Scope candidate work item discovery to a configured development area path before applying Content-team assignee filters.
14. Integrate CM GPT through the supported VS Code Copilot surface by launching `code chat` with a custom agent and a generated work item context file, instead of pretending there is a stable backend Copilot chat API.
15. Store the target local documentation workspace per portal because TFS repository targets and WSL clone paths are separate concerns.
16. Support WSL UNC workspace paths such as `\\wsl.localhost\Ubuntu\workspaces\DocumentationPortal-#01` and normalize them before branch checkout.
17. Build the CM GPT handoff as a package that includes full work item Markdown/JSON/HTML exports plus repository instruction files discovered from `AGENTS.md`, `.github/copilot-instructions.md`, and `.agents`.
18. Reduce dashboard latency with short-lived in-memory caching for current iteration lookup, work item discovery, repository refs, and per-branch PR checks.
19. Reduce TFS work item detail calls by collapsing the initial item read to a single relations-expanded batch call per child/parent set.
20. Launch CM GPT from the Windows VS Code CLI instead of the WSL `code` wrapper, because the Remote WSL wrapper does not reliably forward the `chat` subcommand.
21. Store generated CM GPT work item context files under the target repository `.automation-context/copilot/...` directory so the chat session can read them as normal workspace files without prompting for access to a folder outside the workspace. The folder is excluded through local `.git/info/exclude`.
22. Let the dashboard manage local VS Code Copilot permission defaults and additional read-access folders by writing `chat.permissions.default`, `chat.tools.global.autoApprove`, and `github.copilot.chat.additionalReadAccessFolders` into the user's VS Code `settings.json`.
23. Treat the CM GPT model selection as safety-sensitive. In strict model-safety mode, the dashboard opens the WSL remote workspace and prepares a prompt file, but does not automatically submit proprietary work item content to chat.
24. Prefer opening target repositories through the WSL remote URI instead of UNC paths, because UNC launch opens VS Code in a local Windows context and can hide WSL/Docker/devcontainer tooling from the agent.
25. Manage `chat.editing.autoAcceptDelay` from the dashboard so accepted CM GPT edits can be applied automatically when the VS Code session is in a trusted configuration.
26. Treat Microsoft 365 Copilot Desktop agents and VS Code Copilot agents as separate providers. The company `CM GPT` agent is visible in Microsoft 365 Copilot Desktop, but that surface is not automation-capable for local repository edits in this MVP.
27. Copy Microsoft 365 Copilot Desktop prompts through PowerShell standard input instead of command-line or base64 arguments so large work item payloads do not break `Set-Clipboard`.
28. Percent-encode WSL remote folder URIs before opening VS Code, because workspace paths such as `DocumentationPortal-#01` otherwise treat `#01` as a URI fragment.
29. Validate the current WSL Git branch after checkout before handing work item context to CM GPT, so the model only receives the task when the workspace is on the expected work branch.
30. Treat dashboard flash messages as transient URL state. The page removes `message` and `level` query parameters after rendering, and the backend truncates long messages before display or redirect.
31. Treat `desktop_prepared` as a deprecated non-pipeline state. The automatic CM GPT action should now block Microsoft 365 Copilot Desktop instead of presenting a manual handoff as successful progress.
32. The CM GPT dashboard action is an automatic pipeline action. It should either launch an automation-capable executor or fail clearly; it must not rely on manual prompt paste steps.
33. Temporary end-to-end testing may disable `Strict CM GPT Safety Mode` and use another configured VS Code Copilot model such as `GPT-4o`. This is only for pipeline validation and should be reverted before processing proprietary work items in the approved flow.
34. Agent completion is coordinated through `.automation-context/copilot/<branch>/agent-result.json`. The dashboard only commits, pushes, and creates a draft PR when that file is valid, green-lighted, and lists repository-relative changed files.
35. Draft PR creation is now gated behind a successful dashboard-managed push. The automatic worker can poll for the agent result file and continue with commit, push, and PR creation when green light appears.
36. VS Code handoff window behavior is configurable. The current environment can reuse an existing window or open a dedicated one depending on the configured runtime setting.
37. Long-running automation must be durable. The background runner resumes persisted flows, polls for agent results, and can continue work after dashboard restarts or after the initial request has already returned.
38. Continuous mode is a supported future operating mode: the runner can periodically discover eligible current-iteration work items and start the full pipeline without dashboard interaction.
39. Draft PR creation must formally associate a work item in TFS. Prefer the parent work item when one exists; otherwise link the task itself.
40. Microsoft Loop is a planned documentation context source because development implementation notes may diverge from formal specs. The pipeline should ingest relevant Loop content before asking the agent to change documentation, while surfacing conflicts instead of silently choosing one source.
41. Draft PR descriptions should stay reviewer-focused: include a concise change summary from the agent result plus the work item link, and avoid repeating source branch, target branch, or boilerplate creation text already visible elsewhere in the PR.
42. Each green-lighted task should produce a final Markdown report explaining what changed, why it changed, which files changed, validation notes, reviewer notes, and any spec/reference sections used.
43. The initial agent instruction body must be configurable from Settings. Safety gates and the result-file contract remain controlled by the application.
44. Agent execution must be provider-oriented. VS Code Copilot remains the main executor, while Codex, Claude, or another local CLI can be selected when a command template is configured.
45. Performance problems must be measurable. Dashboard load, work item query, repository enrichment, and TFS request timings are logged to `data/performance.log`.
46. Centralized Content AI project work must use `C:\CM-REPO\Content\CM-AI-Content-Skills\projects` as the active workspace root.
47. The Cherry Pick dashboard is now integrated as a FastAPI/Jinja page at `/cherry-picks` instead of running as a separate Streamlit app or iframe.
48. The integrated Cherry Pick page is a read-only analysis component and reuses the automation project's portal configuration, TFS client, authentication modes, branch chain, and devcontainer task flow.

## MVP Scope

Phase 1 now covers discovery, triage, TFS workflow preparation, durable background progression, and the first automation-capable CM GPT execution path:

- Select or detect the current sprint/iteration.
- Query TFS tasks assigned to configured Content team members.
- Restrict candidate discovery to the configured development area subtree.
- Optionally pre-filter assigned tasks to the current sprint.
- Display work item metadata.
- Filter by type, state, tags, area, assigned user, and text.
- Allow a human to approve/skip candidates.
- Store local run state in SQLite.
- Infer the target base branch from work item information.
- Allow dashboard override of base branch and work type.
- Create work branches in TFS.
- Launch CM GPT automatically on the corresponding WSL workspace branch when an approved automation-capable executor is configured.
- Persist the agent result, push state, and PR state locally so the flow can continue from the last completed stage.
- Commit and push only when the agent result gives green light.
- Create draft PRs and assign the required reviewer from the work item assignee.
- Link the parent work item to the draft PR when available, with fallback to the task itself.
- Resume unfinished automatic flows in the background.
- Optionally discover eligible current-iteration work items continuously.

Still out of scope for the current slice:

- merge/cherry-pick;
- changing TFS work items;
- publishing documentation;
- Microsoft Loop content extraction;
- advanced validation orchestration;
- production hardening for a dedicated always-on worker deployment.

## Proposed Work Item Classification

Initial categories:

- `No documentation needed`
- `Potential small doc update`
- `Needs human review`
- `Likely new documentation/tutorial`

Possible classification signals:

- work item type;
- tags;
- title keywords;
- description keywords;
- acceptance criteria;
- area path;
- linked PRs or commits;
- affected components.

Start with deterministic rules. Add LLM classification later.

## Implemented Persistent Checkpoints

The current implementation persists enough state to resume work after the initial request:

- triage and saved branch plan;
- branch status and effective branch;
- CM GPT launch status;
- agent result status;
- final report path;
- push status;
- PR status;
- automatic-flow eligibility.

The orchestration layer uses these checkpoints to decide what can happen next instead of assuming a request-response interaction will finish the full pipeline.

## Planned Internal States

Suggested states for automation records:

- `Discovered`
- `Classified`
- `Selected`
- `Branch Created`
- `LLM Drafted`
- `Validation Failed`
- `Ready For Review`
- `Approved For PR`
- `PR Created`
- `Skipped`

These names are still useful as a conceptual workflow vocabulary, but the current implementation now persists several concrete stage checkpoints instead of relying on only the initial triage states.

## Backlog

### Immediate

- [Done] Rename/refactor copied UI so it no longer presents itself as a Cherry Pick dashboard.
- [Done] Extract reusable TFS client code from `tfs_dashboard.py`.
- [Done] Add WIQL query support for work items by iteration path.
- [Done] Add a triage page for sprint work items.
- [Done] Add a local persistence layer for work item planning decisions.
- [Done] Add work-branch planning based on version tags, branch chain, and work item type/parent type.
- [Done] Add work-branch creation through the TFS refs API.
- [Done] Add draft PR creation with required reviewer assignment.
- [Done] Add CM GPT handoff and launch support through VS Code Copilot custom-agent and CLI workflow.
- [Done] Add WSL UNC path normalization and richer CM GPT context packaging.
- [Done] Add short-lived dashboard caching and lighter TFS read paths to reduce repeated page-load latency.
- [Done] Add configurable VS Code handoff window behavior, including a non-blocking dedicated-window request and explicit Copilot authorization/model-wait states.
- [Done] Add durable automatic-flow resumption with a background runner and standalone worker entrypoint.
- [Done] Add optional continuous discovery mode for current-iteration work items.
- [Done] Add formal PR-to-work-item linking, preferring the parent work item when available.
- [Done] Add final per-task report generation after a green-light agent result.
- [Done] Add configurable initial agent prompt template.
- [Done] Add provider selection for VS Code Copilot, Codex CLI, Claude CLI, Custom CLI, and Microsoft 365 Desktop visibility.
- [Done] Add performance logging to support slow dashboard-load analysis.
- [Done] Add a Settings overview with operational status and section shortcuts.

### Next

- Add rules-based documentation impact classifier.
- Add feedback controls: approve candidate, skip, needs manual review.
- Add export of triage results.
- [Done] Add durable run history and audit records for each automated stage.
- Add configurable documentation repositories.
- Add better reviewer fallback behavior when the assignee identity is incomplete.
- Add a friendlier reviewer override editor on top of the current JSON textarea.
- [Done] Add a portal settings UI so `work_item_project` and `work_item_team` can be edited without changing JSON manually.
- Add a friendlier reviewer/member mapping UI on top of the current text and JSON inputs.
- Add create/delete portal management on the Settings page if multiple documentation targets become common.
- Add Microsoft Loop context ingestion for work items that reference implementation notes.
- Split the embedded runner into a dedicated deployable service for production.
- Add a single-runner lease or lock so two workers cannot process the same automatic flow concurrently.
- Add retry/backoff policy and failure classification for TFS, Git, and agent-execution steps.
- [Done] Add lightweight dashboard polling while active automatic flows are still in progress.
- Use `data/performance.log` to identify and reduce the slowest TFS calls on initial dashboard load.
- Turn the Settings page overview into real tabs or separate pages if the single-page layout remains too dense.
- Add first-class report viewing/download/opening from the dashboard instead of showing only the filesystem path.

### Later

- Add LLM-assisted classification.
- Add candidate file discovery in documentation repos.
- Expand CM GPT automation from per-item launch to richer bulk and end-to-end flows.
- [Done] Improve reference-spec discovery from the shared `Documentation` repository beyond exact `.docx` name matches.
- Consider lazy-loading full work item detail panels only when a card is expanded if first-load latency still feels too high.
- Add validation runner.
- Add human approval gate for PR creation.

## Planned Task: Microsoft Loop Context Ingestion

Goal:

- Treat Microsoft Loop implementation notes as a first-class source of truth when a work item references them, especially when those notes contain architecture decisions or implementation detail that may differ from older specs.

Expected behavior:

1. Detect Microsoft Loop references in the work item body, comments, linked artifacts, or manually entered dashboard metadata.
2. Resolve each Loop sharing link to a Microsoft Graph-backed drive item.
3. Export the Loop page or component into a stable text representation such as HTML or Markdown.
4. Store the exported material inside the generated `.automation-context/copilot/<branch>/...` package beside the work item export and repository instructions.
5. Include Loop material in the CM GPT prompt context before documentation edits are attempted.
6. When Loop notes and formal specs conflict, require the agent to surface the conflict explicitly in its result instead of silently choosing one source.

Implementation outline:

- add portal/runtime settings for Microsoft Graph authentication and feature enablement;
- add a Loop reference extractor in the work-item packaging layer;
- add a Graph adapter that can resolve share URLs and download/export Loop content;
- add local caching keyed by source URL plus last-modified metadata to avoid repeated Graph fetches;
- extend the context manifest so reviewers can see which Loop sources were included;
- add UI fields for manually attaching or correcting Loop links when they are not present directly in the work item;
- add tests for link extraction, Graph resolution failure, duplicate links, and conflict reporting behavior.

Open questions for this task:

- Which Loop URL patterns are most common in real work items in this team?
- Will the service use delegated access, an application identity, or a hybrid model?
- What Microsoft Graph permissions and admin consent are available in the company tenant?
- Are all relevant Loop pages stored in OneDrive or SharePoint, or do some use SharePoint Embedded containers with additional permission requirements?
- Should the pipeline process only explicitly linked Loop pages, or also search by work item ID/title in a controlled way?
- How should source precedence be communicated when Loop notes, specs, and the work item disagree?

## Open Questions

- How should the current sprint be identified: manual iteration selection, configured current iteration, or TFS team settings?
- Which work item types enter the MVP once task-only discovery is stable: Task only, or should Bug/User Story items also appear directly?
- Should the CM GPT step operate on the main WSL clone directly, or should the dashboard create a dedicated local worktree per work item for safer parallel work?
- Which fields are consistently available in this TFS setup: description, acceptance criteria, repro steps, system info?
- Can Copilot be invoked programmatically in the company environment, or should the MVP generate prompts/context for manual VS Code execution?
- Which documentation repository should be targeted first: DocumentationPortal, DeveloperPortal, or both?
- Which validation commands exist in the documentation repositories?
- Which exact display names or unique names should be configured for the Content team member list?
- Why does the `Development` team currently return no configured iterations through the Work API, even though sprint taskboards exist in the web UI?
- Which Microsoft Loop URL patterns and Graph permissions are available in the company tenant?
- Should production deployment keep the runner embedded in the dashboard process, or move immediately to a dedicated worker service with single-runner coordination?

## Development Notes For Future Agents

- Prefer small, incremental changes.
- Do not modify `C:\CM-REPO\Content\tfs-cherry-pick-dashboard` unless the user explicitly asks.
- Keep copied MVP code working while refactoring.
- Update this file after meaningful progress.
- Keep official documentation, README files, and inline code comments in English.
- Keep secrets out of files.
- Preserve Windows Credentials/PAT behavior from the original dashboard.
- Avoid creating branches, PRs, or TFS writes outside the explicit automation flows already approved by the user.
- The current local test environment may temporarily use `GPT-4o` with strict CM GPT enforcement disabled. Before processing proprietary work items in the approved production flow, restore the CM GPT-only safety posture.
- The dashboard currently starts an embedded runner for convenience. Production deployment should prefer exactly one dedicated worker process or an equivalent lease-based coordination mechanism.

## Latest Progress

2026-04-21:

- Created isolated project folder: `C:\CM-REPO\Content\tfs-doc-automation-mvp`.
- Copied minimal Cherry Picks dashboard baseline into the new project.
- Added `README.md`.
- Added `docs/technical-design.md`.
- Added `.agents/memory.md`.
- Converted project documentation to English and recorded the language convention.

2026-04-23:

- Replaced the dashboard implementation with FastAPI and Jinja templates.
- Added reusable modules for configuration, SQLite persistence, branch planning, TFS API access, service orchestration, and web routes.
- Implemented WIQL-based work item loading by iteration path.
- Implemented current-iteration lookup through TFS team settings when a portal team is configured.
- Implemented branch inference from work item version tags and branch-chain configuration.
- Implemented branch-name generation with the pattern `version.minor/work-type/work-item-id-short-title`.
- Implemented work-branch creation through the TFS refs API.
- Implemented draft PR creation through the TFS pull request APIs.
- Implemented required reviewer assignment using the work item assignee identity.
- Added local SQLite persistence for triage, branch planning, and branch/PR status.
- Added `.env` runtime settings with dashboard editing support.
- Added preferred-port and automatic port fallback handling through `run_server.py`.
- Added reviewer override mapping and default reviewer settings through `.env`.
- Separated work item source configuration from target repository configuration.
- Moved portal/runtime/authentication configuration into a dedicated Settings page and kept the main dashboard focused on operations.
- Switched work item discovery to an assignee-first model using the configured Content team member list.
- Added optional current-iteration filtering on top of assigned-task discovery.
- Added runtime settings for Content team members and default sprint filtering behavior.
- Made `work_item_team` normalization tolerant of plain team names, longer paths, and sprint-board URLs.
- Verified against the live TFS project that `Gaivosa` is not a valid Work API team name for current-iteration lookup, while `Development` is valid but currently returns no configured iterations.
- Added `work_item_area_path` to portal settings and to the WIQL query so candidate tasks are now constrained to `Product\Development` before assignee filtering.
- Fixed work item loading so assignee discovery now queries TFS with `AreaPath + AssignedTo`, expands aliases such as `lmpereira` to `CMF\lmpereira`, and chunks work item batch reads in groups of 200 to respect TFS limits.
- Reworked the dashboard item list into collapsed detail panels and made action availability repository-aware: the UI now detects existing branches by work item ID, hides branch creation when a matching branch already exists, and only enables draft PR creation when a branch is present.
- Added work item and parent-work-item PR screening by combining relation-based PR links with repository branch/PR checks.
- Fixed identity rendering so names with accents such as `Luís Pereira` are preserved correctly in the dashboard, with an extra defensive mojibake-repair layer for locally stored reviewer values.
- Tightened dashboard action gating so branch creation is also blocked when the work item or its parent is already associated with a draft/completed PR in the target repository.
- Optimized repository screening by caching the repository ref list once per load, scanning PRs only when a branch still needs PR detection, and reducing the dashboard refresh for the current sprint scenario from roughly 70-90 seconds to about 30-35 seconds in local validation.
- Added dashboard state styling so each work item row/card now has a different background treatment based on the TFS work item state.
- Added a `Hide Closed` dashboard toggle, wired through the load/filter/action round-trips, so reviewers can focus only on still-open tasks without losing their current filter context after save/branch/PR actions.
- Tightened PR handling so the backend now blocks branch/PR actions when a work item already has an associated PR, and the UI labels those links generically as associated PRs instead of always calling them draft PRs.
- Added visible work item selection checkboxes plus a bulk `Run Automatic TFS Flow for Selected` action that uses each item's saved or inferred plan, creates branches when needed, and skips items that already have associated PRs or still need planning.
- Added editable planned-branch naming in the work-item plan form, including persistence of custom branch names across save/branch/PR actions and a generated-branch reference that can be restored by clearing the custom field and saving again.
- Refined the bulk-selection UI so the select-all checkbox and per-item selection checkboxes are aligned with the card content instead of sitting in separate broken grid columns.
- Added a global loading overlay for form submissions so long-running dashboard actions visibly gray out the page and show a spinner/status while waiting for TFS operations to complete.

2026-04-27:

- Switched the Microsoft 365 Copilot Desktop clipboard handoff from base64 command arguments to UTF-8 standard input, fixing failures with large proprietary work item prompts.
- Fixed VS Code WSL remote launch targets by percent-encoding folder URIs, including `#` in workspace names such as `DocumentationPortal-#01`.
- Added explicit branch validation after checkout before CM GPT handoff, and rewrote workspace state checks to use direct Git calls instead of fragile shell substitution.
- Made dashboard flash messages transient by cleaning `message` and `level` from the browser URL after rendering, and added server-side truncation for long flash payloads.
- Rejected the Microsoft 365 Copilot Desktop path as a valid pipeline executor because it cannot edit the local WSL repository without user intervention in the current integration.
- Switched the default runtime provider to VS Code Copilot with automatic execution enabled and strict preparation-only mode disabled, so the CM GPT action now represents an automatic executor path.
- Made the CM GPT-only model guard conditional on `Strict CM GPT Safety Mode`, added VS Code Copilot model suggestions in Settings, and set the local `.env` model to `GPT-4o` for temporary end-to-end testing.
- Added persisted agent-result and push states, plus `Check Agent Result` and `Commit & Push` actions.
- Changed the automatic flow so it no longer creates a draft PR immediately after branch creation. It launches the agent, waits for `agent-result.json`, commits and pushes green-lighted changes, then creates the draft PR.

2026-04-28:

- Fixed the automatic flow branch step by moving the push-required validation out of branch creation and into draft PR creation.
- Improved bulk automatic-flow flash messages so item-level errors include the affected work item ID and detail instead of only aggregate counts.

2026-05-18:

- Added configurable VS Code workspace window behavior. The current environment defaults to `reuse`, while Settings can switch to `new` when a dedicated handoff window is preferable.
- Added a durable automation orchestrator that resumes persisted automatic flows in the background, polls unfinished agent work after restarts, and provides a standalone `run_worker.py` entrypoint for future service deployment.
- Added automation-runner settings for enablement, reconcile interval, continuous mode, and discovery interval.
- Added continuous discovery support for open current-iteration tasks and dashboard visibility for runner/continuous-mode state.
- Added formal PR-to-work-item linking during draft PR creation, preferring the parent work item and falling back to the task itself when no parent exists.
- Backfilled the missing parent work item association for draft PR `#86667` linked to WI `151873`, using parent WI `150572`.
- Recorded Microsoft Loop content ingestion as a planned future capability because implementation notes there may contain authoritative detail that is not present in the work item or formal specification.
- Added lightweight dashboard status polling while visible automatic flows are still active, and changed stage summarization so a created PR wins over stale earlier agent-error state.
- Simplified newly created draft PR descriptions to a reviewer-facing change summary plus the work item link, and preserved existing agent summaries when later error updates do not provide a replacement summary.

2026-05-26:

- Added final Markdown report generation for green-lighted agent results. Reports are stored under the configured reports folder using a parent/task folder name such as `149746 - Task 151996`.
- Extended the agent result contract to include `final_report`, `spec_references`, `validation`, and `reviewer_notes` in addition to status, green light, summary, and changed files.
- Added configurable `Initial Agent Prompt Template`, `CLI Command Template`, and `Final Reports Path` runtime settings.
- Added provider options for VS Code Copilot, Codex CLI, Claude CLI, Custom CLI, and Microsoft 365 Copilot Desktop visibility. CLI providers require a command template and must write `agent-result.json`.
- Added performance logging to `data/performance.log`, including TFS request timings and dashboard load phases.
- Added a Settings overview panel with current portal, provider, runner status, performance log path, and section shortcuts.
- Generated a real final report for WI `151996` at `data/reports/149746 - Task 151996/final-report.md` while validating the report writer.
- Reduced dashboard initial-load latency by making the list summary-first: WIQL now returns candidate IDs, a lightweight batch fetches only list fields, and full details are deferred to operational actions.
- Changed dashboard repository enrichment to avoid remote branch/PR scans during the list render. The list now uses local persisted state first; branch, agent, push, and PR actions still perform remote validation.
- Moved current-iteration auto-resolution behind the active current-iteration filter so the dashboard does not call TFS team settings when that filter is off.
- Cleared the portal iteration team in the local MVP configuration because the `Development` team currently returns no current iteration through the Work API. Manual iteration paths should be used for sprint narrowing until a reliable team iteration source is available.
- Validated the performance improvement locally: blank initial load with current-iteration mode now returns in about `8 ms`, manual `Product\Gaivosa\Sprint 04` with closed items hidden returns in about `12 s`, and manual Sprint 04 including closed items returns in about `26 s`.

2026-05-27:

- Corrected the previous performance change because it hid work items when current-iteration filtering was enabled but no current iteration could be resolved.
- Changed the dashboard to a paginated summary-first flow. The initial query requests only enough IDs and lightweight fields for the current page, with a default page size of 10.
- Added lazy work item detail loading through `/work-items/{id}/details`. Opening a card now fetches full work item details, parent metadata, repository state, linked PR screening, and action forms only for that one item.
- Updated the dashboard empty/current-iteration notice so unresolved current iteration no longer blocks assigned-task discovery.
- Reworked the Settings top summary into a horizontal context strip instead of squeezing discovery, target, workspace, runtime, and the load action into one awkward grid row.
- Validated locally: page 1 with 10 visible assigned tasks loaded in about `6.1 s`; opening one detailed work item loaded the full detail/action panel in about `5.6 s`; FastAPI template checks returned HTTP 200 for both dashboard and detail routes.
- Improved work item detail rendering so Description, Acceptance Criteria, and Repro Steps show sanitized rich HTML instead of truncated plain-text previews. Images embedded in the TFS HTML now render in the dashboard, and image attachments are shown as preview tiles when available.
- Added detail-state metadata so the collapsed card status and progress bar can be updated after the lazy detail load returns remote branch/PR state.
- Fixed progress calculation so an existing associated PR always marks the Draft PR step as done, even if an old PR error is still stored locally.
- Added a `/tfs-assets` authenticated proxy so embedded TFS images load through the dashboard using the configured TFS credentials instead of requiring the browser to fetch protected attachment URLs directly.
- Improved the global loading overlay with action-specific progress steps for branch creation, agent launch, auto-flow, result checking, push, and draft PR creation.
- Installed Codex CLI for local testing through npm and added a WSL shim at `/home/lmpereira/.local/bin/codex`. Because this install runs the Windows Codex executable, the CLI command template now uses the `{{workspace_unc_path}}` placeholder.
- Configured the local MVP `.env` for `codex_cli` with model `gpt-5.5` and a non-interactive command template that runs `codex exec` with bypassed approvals/sandbox for end-to-end automation testing.
- Replaced the Windows-backed Codex shim path with a native WSL Node/Codex install under `/home/lmpereira/.local/node/current` and `/home/lmpereira/.npm-global/bin/codex`.
- Copied the current Codex auth into the WSL user `CODEX_HOME` and updated the local CLI command template to run native Codex against `{{workspace_path}}` instead of the UNC path.
- Added persisted provider PID/log fields and provider-log failure detection. If a CLI provider exits without writing `agent-result.json`, the dashboard now records an agent-result error and no longer shows the item as an active flow.
- Cleaned stale local automatic-flow states for WIs `152658`, `152421`, and `151120`, which were waiting for result files that could no longer appear.
- Validated the native WSL Codex CLI in an isolated `/tmp/doc-automation-codex-smoke` repository: it created `smoke.txt` and a valid green-light `agent-result.json`.
- Current environment note: DNS resolution for `tfs-product.cmf.criticalmanufacturing.com` failed during final dashboard verification, so the dashboard could not reload live work items in this session even though local status endpoints were working.
- Fixed the automatic-flow retry path for failed agent runs. A work item with `agent_result_status=error` now relaunches the configured provider instead of repeatedly reading the old provider log.
- The CLI launcher now removes stale `agent-result.json` and `agent-provider.log` before starting a new provider process, so retries cannot show a previous run's error as if it were current.
- Launching a provider now resets the local agent result state to `waiting`, clears the previous agent error, and stores the current provider PID/log path for later diagnostics.
- Reworked the CLI provider launcher to write an `agent-provider.sh` wrapper into the work item context directory, record a diagnostic header in `agent-provider.log`, and launch that wrapper from WSL bash.
- Switched the WSL command runner from `sh -lc` to `bash -lc` because PID/job behavior differed under `sh` when called through `wsl.exe`.
- Fixed PID capture by using a grouped `nohup ... & jobs -p | tail -n 1` launch. `$!` was empty when invoked through `wsl.exe`, and an ungrouped `&& ... &` could background the wrong command chain.
- Validated the corrected wrapper launcher with a benign prompt in the `152658` context. It returned PID `7039` and wrote a log containing the diagnostic header plus the Codex CLI run output.
- Added a guard in agent-result checking so CLI providers with `copilot_status=launched` but no stored PID are marked as an error instead of being repeatedly reset to `waiting` by the background worker.
- Cleaned TFS PowerShell error handling so ANSI escape sequences and `Write-Error` prefixes no longer leak into the dashboard; DNS failures now show an actionable VPN/DNS message.

- Made repeated commit/push handling idempotent for duplicate workers: if the expected WI commit is already HEAD, the push step reuses that commit instead of marking `No staged changes` as a failure.
- Normalized the local WI `152658` state after duplicate workers raced: the branch was pushed at commit `dab690c575a39323f384125ea00ea02d629cafb4` and draft PR `#87233` was created.

2026-05-28:

- Reviewed PR `#87233` for WI `152658` and found that the documentation content matches the work item/spec intent, but the tutorial Mermaid `click` links resolve to missing local paths.
- Hardened the agent handoff so repository instructions from `AGENTS.md`, `.github/copilot-instructions.md`, and `.agents/**/*.md` are copied into `.automation-context/copilot/<branch>/repo-instructions/` with an index file.
- Extended the agent result contract with `instruction_files_read`. The dashboard now blocks green-lighted results when repository instruction files exist but the agent does not confirm reading them.
- Added dashboard-managed post-agent validation before push/PR progression. The validation checks `git diff --check`, runs markdownlint when a local command is available, and validates local Markdown links plus Mermaid `click` targets in changed Markdown files.
- Added a `needs_agent_fix` local state for green-lighted agent results that fail dashboard validation. These items stop automatic progression before push and surface the validation error in the dashboard.
- Extended final reports with dashboard validation details and the repository instruction files reported by the agent.
- Added a rendered final-report page at `/work-items/{id}/report`, linked from the work item detail panel.
- Added a controlled rerun action in the work item detail panel. A rerun creates a fresh `-rerun-<timestamp>` branch, clears the local automation state for the new attempt, ignores older PR links only for that rerun, launches the automatic flow again, and stores rerun reports in a branch/timestamp-specific subfolder.

2026-06-01:

- Fixed the VS Code Copilot handoff after a run showed that the `code chat` prompt was being truncated in the chat UI. The launcher now sends the full prompt through stdin (`code chat ... -`) instead of passing the full Markdown body as a command-line argument.
- Made the VS Code prompt self-contained by embedding the work item Markdown context directly in `prompt.md`, while still instructing the agent to read the adjacent HTML/JSON files and repository instruction package before doing any repository work.
- Hardened custom-agent discovery by writing the generated `cmf-tfs-doc-automation.agent.md` file to the WSL profile, the Windows `~/.copilot/agents` profile, and the VS Code user-data prompts folder.
- Extended the VS Code settings writer to include `chat.agentFilesLocations` for the generated agent directories, and the launch path now reapplies VS Code Copilot settings before starting a VS Code provider run.
- The VS Code launcher now attaches the generated prompt/context files as `--add-file` entries and records their UNC paths in the handoff metadata.

- Investigated the 152939 VS Code rerun issue and found a stale dashboard process still listening on port `8001` with the old handoff code. That process regenerated the old 2 KB prompt after the fix. Stopped the stale process and kept the active dashboard on port `8000`.
- Reinforced the VS Code launcher to pass a short bootstrap prompt as the normal `code chat` argument and the complete handoff through stdin, matching the documented `code chat <prompt> -` pattern. The bootstrap explicitly tells Copilot to read the attached `prompt.md` and context package before commands or edits.
- Added a reference-documentation package to the agent handoff. Detected `.docx`/`.docm`/`.doc` names are matched against the configured reference docs workspace, an index is written under `.automation-context/copilot/<branch>/reference-docs/index.md`, and readable `.docx`/`.docm` files are extracted to text files so the agent can inspect the spec without relying on binary document support.
- Validated the spec discovery path with `CMF-NAV-2011-SRS-00783-Material-I-Feature-Group.docx`; it resolves to `/workspaces/Documentation/Requirements/CMF-NAV-2011-SRS-00783-Material-I-Feature-Group.docx` and produces a packaged text extract.
- Changed Draft PR creation so newly created PR descriptions include the final automation report from the generated `final-report.md`, with a length cap and truncation note if needed.

2026-06-02:

- Investigated WI `152923` after the agent produced local changes and an `agent-result.json` with `status=completed` but `green_light=false`.
- Found that the previous flow could treat `completed` as a ready state in some UI/continuation paths even when explicit green light was denied.
- Fixed `read_agent_result`/service progression semantics so explicit `green_light=false` is respected. Local automation now treats only `green_light`, `ready_for_push`, or `success` as push-ready states; `completed` is no longer sufficient.
- Added automatic agent repair support for active automatic flows. If the agent changed files but did not green-light the result, missed required repository instruction acknowledgements, or failed dashboard preflight validation, the runner can relaunch the configured provider once on the same dirty work branch.
- The repair prompt reuses the existing context package, repository instruction package, reference-doc package, previous result, changed files, and dashboard validation failures. It asks the agent to fix the local changes and rewrite `agent-result.json`.
- Added local state columns `agent_repair_count`, `agent_repair_last_started_at`, and `agent_repair_last_reason`, plus dashboard visibility for repair attempts and last repair reason.
- Added preflight diagnostics before repair so failures such as broken local Markdown links or Mermaid click targets can be sent back to the agent instead of only stopping the flow.
- Current repair cap is one automatic attempt per work item branch. If the repair cannot be launched or still fails, the item moves to `needs_agent_fix` and automatic progression is disabled until reviewer action or a controlled rerun.
- Checked the next WI `152923` run and confirmed the repair flow did activate: the repair prompt included the missing instruction acknowledgements and the broken local link from the previous attempt.
- The run exposed two follow-up hardening needs: duplicate repair launch protection and avoiding validation while VS Code Copilot is still rewriting `agent-result.json`. Added atomic repair reservation with the configured repair cap and a short result-file stability window before validation.
- Added explicit repair reasons for invalid `agent-result.json` and for green-lighted results that do not list `changed_files`, because the dashboard commits only the files listed in the agent result.

2026-06-03:

- Fixed a VS Code handoff prompt issue where the agent was told to stop if the internal generated transport mode `cmf-tfs-doc-automation` was not visibly selected.
- The functional agent/model contract now comes from dashboard Settings: `Agent Name` and `Model Name`. The generated VS Code transport mode is still used to deliver the handoff, but the prompt explicitly says it is not by itself a reason to stop.
- The bootstrap prompt also names the configured Settings agent/model and tells the agent to use embedded stdin context if attached files are not visible in VS Code.
- Investigated a follow-up WI `152923` automatic-flow run where nothing appeared to happen in VS Code. Local state was `copilot_status=launched`, `agent_result_status=waiting`, `auto_flow_enabled=1`, but `agent-result.json` was missing and the package prompt was still the pre-fix version.
- Added stale VS Code wait detection: if a VS Code handoff is still waiting, no `agent-result.json` exists, and the launch is older than the configured grace window, a selected automatic flow regenerates the package and relaunches the VS Code chat instead of waiting indefinitely.
- Fixed the state merge used by automatic-flow items so `copilot_prepared_at`, `agent_repair_count`, `agent_repair_last_started_at`, and `agent_repair_last_reason` are available to flow decisions and detail rendering. Without this, stale VS Code wait detection could not calculate the previous launch age.
- Moved the generated agent context package out of `.git/copilot-context/...` and into repository-root `.automation-context/copilot/<branch>/...`. VS Code Copilot could fail to discover or read files under `.git`, while the root-level folder is visible as normal workspace content.
- The dashboard now adds `/.automation-context/` to the target repository's local `.git/info/exclude` and rejects `.automation-context/...` entries in `changed_files`, keeping the package readable by the agent but out of commits.

2026-06-11:

- Switched the local MVP settings to the `codex_cli` provider with model `gpt-5.5` and `model_reasoning_effort="high"` for automated WSL execution tests.
- Investigated WI `153571` after the provider failed with `nohup: failed to run command .../agent-provider.sh: No such file or directory`.
- Root cause: the generated WSL provider wrapper existed, but it had Windows CRLF line endings. Linux interpreted the shebang as `/bin/sh\r`, which surfaces as a misleading `No such file or directory` error.
- Fixed `_run_wsl_script` so stdin payloads are sent as UTF-8 bytes when writing WSL files. This prevents Python/Windows text-mode newline conversion from introducing CRLF into scripts written inside WSL.
- Added a launcher smoke test with a fake CLI provider. The regenerated `agent-provider.sh` now has LF line endings, launches via `nohup`, writes `agent-result.json`, and records the diagnostic provider header in `agent-provider.log`.
- Confirmed the WI `153571` branch was based on `origin/11.2/dev`, so the original 10.2 vs 11.2 base change did not cause the provider failure.
- Noted a branch naming inconsistency for WI `153571`: the saved branch was `fix/153571-doc-provide-the-capability-to-unterminate-a-material-that-is-in-an-inval`, while the selected base branch would normally infer `11.2/fix/153571-doc-provide-the-capability-to-unterminate-a-material-that-is-in-an-inval`. This likely came from a manual planned-branch override and should be handled separately if strict branch naming is required before PR creation.
- Hardened automatic-flow retry behavior for CLI providers. If a CLI provider failed before creating `agent-result.json`, the next selected automatic-flow run now relaunches the provider instead of treating the stale provider-log error as a repairable agent result.
- Investigated the next WI `153571` provider failure after the CRLF fix. The wrapper executed, but Codex CLI failed with `refresh_token_reused`, `token_expired`, and `401 Unauthorized`.
- Confirmed the same failure with a minimal `codex exec` smoke prompt in WSL, while `codex login status` still reported stored ChatGPT auth. This means the saved WSL Codex auth exists but cannot refresh and must be recreated with `codex logout` followed by `codex login`.
- Added provider-log summarization so dashboard errors no longer include large prompt/context blocks. Known Codex auth failures now render as a concise actionable message while the full `agent-provider.log` remains on disk for diagnostics.

2026-06-12:

- Continued WI `153571` after the agent had already written a valid `agent-result.json` with `green_light=true` but no push or draft PR had been created.
- Root cause for the missing draft PR: the local automation state had `auto_flow_enabled=0`, so the background runner did not reconcile the green-lighted agent result after the previous transient result-stability/error state.
- Used the normal service continuation path, not manual Git commands, to commit, push, and create the draft PR. The branch was pushed at commit `a491e3c0a5df02471ee56dca490107e21647028c` and draft PR `#87939` was created.
- Hardened persisted-flow reconciliation. `list_auto_flow_states()` now returns recoverable states even when `auto_flow_enabled` was lost: pushed branches without PRs, green-lighted agent results without push/PR, waiting result states, and the transient `Agent result file changed recently` stability state.
- Validated that WI `153571` now has `push_status=pushed`, `pr_status=created`, `pr_id=87939`, and that no persisted auto-flow states remain pending.

2026-06-17:

- New working rule: all future implementation work for this initiative must happen under `C:\CM-REPO\Content\CM-AI-Content-Skills\projects`.
- Planned the integration of `projects/tfs-cherry-pick-dashboard` as a first-class component of `projects/tfs-doc-automation-mvp`.
- Integration goal: expose Cherry Pick propagation analysis as a new dashboard tab/page inside the FastAPI pipeline application instead of running Streamlit as a separate application.
- Architecture decision: migrate the Cherry Pick domain logic into reusable FastAPI service modules and render the UI through Jinja templates. Do not embed Streamlit in an iframe and do not require a second devcontainer task/server for normal use.
- Devcontainer requirement: the combined dashboard must continue to run from the existing `tfs-doc-automation-mvp` devcontainer and VS Code task flow.

Cherry Pick integration task plan:

1. [Done] Create a reusable Cherry Pick service module in `doc_automation/` that ports propagation logic from `tfs-cherry-pick-dashboard/tfs_dashboard.py`.
2. [Done] Reuse the existing pipeline `TfsClient`, portal configuration, authentication modes, and branch-chain settings.
3. [Done] Add missing generic TFS client helpers needed by the Cherry Pick logic, keeping duplicated client code out of the final design.
4. [Done] Add a `/cherry-picks` FastAPI route.
5. [Done] Add a `templates/cherry_picks.html` Jinja page with portal, lookback, scope, status, branch, and sorting controls.
6. [Done] Add top navigation for `Automation`, `Cherry Picks`, and `Settings`.
7. [Done] Keep the Cherry Pick page read-only: no branch creation, PR creation, work item updates, or cherry-pick execution from this component.
8. [Done] Update README and project memory to describe the integrated Cherry Pick tab.
9. [Done] Validate with Python compilation and a local HTTP smoke test.
10. [Pending] Later enhancement: merge Settings UX so Cherry Pick-specific controls are clearly grouped with automation settings without making the configuration page noisy.

2026-06-17 implementation notes:

- Added `doc_automation/cherry_picks.py` as the reusable Cherry Pick analysis service.
- Added `/cherry-picks` in `doc_automation/web.py` and `templates/cherry_picks.html`.
- Added Cherry Pick styling to `static/site.css`.
- Added `Git Credentials` as an auth mode so the integrated page can run from Linux/devcontainer contexts that rely on Git credential helpers.
- Added optional `requests-ntlm` support for Git Credentials authentication. The app still imports when the package is absent, but using that auth mode requires installing `requests-ntlm`.
- Verified `python -m compileall` and a FastAPI TestClient render of `/cherry-picks` returning HTTP 200 without loading TFS data.

2026-06-17 devcontainer test preparation:

- Prepared `/workspaces/DocumentationPortal-#12.0` as the first WSL devcontainer test host for the integrated dashboard.
- Updated its `01-light` and `02-full` devcontainer profiles to mount the centralized project from `/mnt/c/CM-REPO/Content/CM-AI-Content-Skills/projects/tfs-doc-automation-mvp` into `/workspaces/tfs-doc-automation-mvp`.
- Forwarded port `8010` for the integrated TFS Autonomous Pipeline dashboard so it does not conflict with MkDocs on port `8000`.
- Extended the repository VS Code tasks with `TFS Autonomous Pipeline - Install Dependencies`, `Run Dashboard`, `Run Worker`, and `Stop`.
- Extended `post-create.sh` to create `/home/vscode/.venvs/tfs-doc-automation-mvp` and install the dashboard requirements separately from the MkDocs environment.
- JSON validation passed for the modified devcontainer and tasks files.
- Open implementation note: full agent/branch automation from inside the devcontainer still needs native Linux execution support or a confirmed host bridge, because current workspace automation code still uses `wsl.exe` for several Git and provider operations.

2026-06-18 devcontainer test adjustment:

- Investigated the `DocumentationPortal-#12.0` devcontainer startup failure. The actual failure occurs before post-create/tasks: Docker cannot pull `mcr.microsoft.com/devcontainers/python:3.11-bookworm` because the WSL Docker daemon reports `connect: no route to host`.
- Removed the manual `TFS Autonomous Pipeline - Install Dependencies` task from the test host.
- Added `.devcontainer/common/tfs-autonomous-pipeline.sh` in `DocumentationPortal-#12.0`. `Run Dashboard` and `Run Worker` now call this helper, which creates/updates `/home/vscode/.venvs/tfs-doc-automation-mvp` automatically when `requirements.txt` changes.
- Updated `post-create.sh` to reuse the same helper with the `ensure` command.
- Validated JSON for the devcontainer/task files and shell syntax for the helper script.
- Resolved the WSL outbound networking issue by creating `C:\Users\lmpereira\.wslconfig` with `networkingMode=mirrored`, `dnsTunneling=true`, and `autoProxy=true`, followed by `wsl --shutdown`.
- After the WSL restart, `curl -4 -I https://mcr.microsoft.com/v2/` succeeded from Ubuntu and `docker pull mcr.microsoft.com/devcontainers/python:3.11-bookworm` completed successfully.

2026-06-18 devcontainer runtime validation:

- Copied the previous local MVP `.env` from `C:\CM-REPO\Content\tfs-doc-automation-mvp\.env` into the centralized project at `C:\CM-REPO\Content\CM-AI-Content-Skills\projects\tfs-doc-automation-mvp\.env`.
- Updated the centralized `DocumentationPortal` portal config to use the WSL test workspace `\\wsl.localhost\Ubuntu\workspaces\DocumentationPortal-#12.0` and `Git Credentials` authentication for devcontainer testing.
- Fixed Settings rendering inside Linux/devcontainer by avoiding `wsl.exe` calls for already-absolute Linux paths in `normalize_wsl_target_path`.
- Settings now returns HTTP 200 on the devcontainer dashboard at port `8010`.
- Copied the WSL host Git credential store into the active devcontainer user for this test session so `git credential fill` can resolve TFS credentials inside the container. This is session/container-local and may need repeating after a rebuild.
- Current external blocker: TFS itself is unreachable from Windows, WSL, and the container while off VPN/internal network. `tfs-product.cmf.criticalmanufacturing.com` resolves to `10.24.14.100`, but TCP 443 times out. Reconnect VPN/internal network before testing live TFS reads.

2026-06-18 Cherry Pick UI and Settings UX plan:

- Updated the Cherry Picks navigation link to show the global loading overlay while the propagation analysis page is opened. The overlay uses Cherry Pick-specific progress messages so the page does not feel stuck while TFS PR data is loading.
- Renamed the Cherry Picks page submit button from `Load Cherry Picks` to `Load`.
- Settings UX direction: restructure the Settings page into top-level tabs instead of one long mixed form.
- Proposed tab model:
  1. `Connection`: shared TFS/project/repository/authentication/API/branch-chain settings used by both Automation and Cherry Picks.
  2. `Automation`: work item discovery, Content team members, reviewer resolution, branch planning defaults, agent provider/model/prompt/reference docs/final reports, VS Code permissions, and automatic runner settings.
  3. `Cherry Picks`: propagation-analysis defaults such as lookback days, maximum PRs per branch, default scope/status/branch/sort values, and read-only behavior notes.
  4. `Runtime`: local dashboard/worker host and port, performance log location, and service health/status controls.
- Keep the portal selector and a compact horizontal status summary above the tabs. Avoid duplicating shared repository/auth fields inside individual dashboard-specific tabs.

2026-06-18 Settings tabs implementation:

- Reworked the Settings page into four server-rendered tabs: `Connection`, `Automation`, `Cherry Picks`, and `Runtime`.
- The top of Settings now has a compact portal selector plus horizontal status summary instead of a tall mixed configuration strip.
- `Connection` holds shared TFS/work-item source, target repository, authentication, workspace, and branch-chain settings.
- `Cherry Picks` holds propagation-analysis defaults such as lookback days, maximum PRs per branch, and work-item API verification. It also shows the shared branch chain as a read-only preview.
- `Automation` holds work-item discovery defaults, reviewer resolution, agent/provider/model/prompt settings, and VS Code Copilot permission settings.
- `Runtime` holds the background runner controls and local dashboard server settings.
- Settings redirects preserve the active tab after saving or running the automation cycle.
- Validated all four tabs with FastAPI TestClient and the running devcontainer dashboard on port `8010`.

2026-06-19 TFS connectivity recovery:

- Investigated a dashboard failure after VPN was connected where work items were not loading from TFS.
- The persisted portal configuration was still correct: `Git Credentials`, `Product\Development`, and the expected TFS base URL.
- Root cause: the active devcontainer no longer had usable Git credentials for `tfs-product.cmf.criticalmanufacturing.com`, so `git credential fill` failed for the `vscode` user inside the container.
- Restored the test session by copying `/home/lmpereira/.gitconfig` and `/home/lmpereira/.git-credentials` from the WSL host into `/home/vscode` inside container `b5bda713f6b8`, then fixing ownership and permissions.
- Added `DOC_AUTOMATION_TFS_REQUEST_TIMEOUT_SECONDS` with a default of 15 seconds so failed TFS calls no longer hold the dashboard for about one minute.
- Added timeout and clearer error messages around Git credential lookup so missing/stale container credentials surface as a visible dashboard error instead of a long ambiguous wait.
- Verified the dashboard again through `http://127.0.0.1:8010/`: it returned HTTP 200 in about 5 seconds and displayed 10 visible work items.

2026-06-19 Cherry Picks portal switching and No-CP labels:

- Reproduced a `500 Internal Server Error` when opening the Cherry Picks dashboard with `DeveloperPortal`.
- Root cause: `DeveloperPortal` was configured with `Windows Credentials`, but the active devcontainer is Linux-based and does not have PowerShell. The legacy fallback attempted to execute `powershell` anyway, causing `FileNotFoundError`.
- Fixed PowerShell-based TFS requests to fail as a controlled `TfsApiError` when PowerShell is unavailable, with guidance to use `Git Credentials` or PAT in the devcontainer.
- Updated the local `DeveloperPortal` config to use `Git Credentials`, matching the active devcontainer runtime.
- Added configurable Cherry Pick skip labels at portal level. Defaults are `No CP`, `no-cp`, and `not to cp`.
- Skip-label matching ignores case, spaces, hyphens, underscores, and accents, so variants such as `NO-CP` and `no cp` match the same rule.
- The Cherry Picks page now shows an `Ignored` metric and displays the configured ignored labels in the page hint.
- Current validation: `DeveloperPortal` Cherry Picks load returns HTTP 200 and reports 3 analyzed PRs, 0 ignored PRs, and 2 visible rows. `DocumentationPortal` returns HTTP 200 and reports 34 analyzed PRs, 0 ignored PRs, and 18 visible rows.
- Abandoned PRs are now ignored automatically by the Cherry Pick propagation analysis. They are removed before grouping, so an abandoned original PR no longer creates a visible family and an abandoned downstream PR no longer satisfies a target branch.

2026-06-23 Context Capture and Managed Assets:

- Created a full project backup before implementation at `C:\CM-REPO\Content\CM-AI-Content-Skills\projects\_backups\tfs-doc-automation-mvp-20260623-165016`.
- Reviewed `projects/ado-capture` and decided not to replace the automation MVP. The MVP remains the operational pipeline, while the `ado-capture` approach becomes a reusable rich context capture engine inside the handoff.
- Added `doc_automation/context_capture.py`.
- The capture engine starts from the selected task parent when available, otherwise the selected task itself.
- It captures the work item tree, comments, legacy history, linked PR metadata, commits, changed files, review comments, and local PR diffs when matching clones are available under the configured workspace scan roots.
- The agent context package now includes `capture/summary.md`, `capture/INSTRUCTIONS.md`, `capture/manifest.json`, `capture/workitems/...`, and `capture/pullrequests/...`.
- Capture failures are non-fatal. A small capture error package is generated and the pipeline continues with the base work item context.
- The agent result contract now accepts `capture_files_read`, `prs_reviewed`, `diffs_reviewed`, and `work_items_reviewed`.
- Final reports now include a `Captured Evidence Used` section.
- Established the managed shared-asset destination standard: target repositories receive shared Content AI assets under `.agents/content-ai/`; root `AGENTS.md` in target repositories is never overwritten.
- Added `scripts/sync-content-ai-assets.sh` to copy `AGENTS.md`, `ai/manifest.json`, `ai/skills`, `ai/agents`, and `ai/instructions` from the central repository into `.agents/content-ai/`, with an install manifest and local Git exclude rule.
- Added `scripts/devcontainer-bootstrap.sh` for target repositories to clone/update `CM-AI-Content-Skills`, install pipeline requirements, create the `tfs-autonomous-pipeline` wrapper, and sync managed assets.
- Updated `.devcontainer/setup.sh` so scripts are executable and optional asset sync can run when `CONTENT_AI_SYNC_ASSETS=true`.
- Updated the project devcontainer to forward port `8010`.
- Validated Python compilation, shell syntax, and `devcontainer.json`.

2026-06-23 Context Capture Settings:

- Added runtime settings for the rich context capture engine:
  - `DOC_AUTOMATION_CONTEXT_CAPTURE_ENABLED`;
  - `DOC_AUTOMATION_CONTEXT_CAPTURE_ROOT_MODE`;
  - `DOC_AUTOMATION_CONTEXT_CAPTURE_MAX_TREE_ITEMS`;
  - `DOC_AUTOMATION_CONTEXT_CAPTURE_INCLUDE_PR_DIFFS`;
  - `DOC_AUTOMATION_CONTEXT_CAPTURE_WORKSPACE_SCAN_ROOTS_JSON`.
- Added `Context Capture` controls under `Settings > Automation`.
- Root modes are `parent` and `task`. `parent` uses the parent work item when available and remains the default because implementation evidence usually lives around the user story or bug, not only the DOC task.
- The agent launcher now respects the configured capture settings instead of always using fixed defaults.
- Updated `.env.example`, README, and technical design with the new capture controls.
- Validation passed for Python compilation, runtime settings loading, and Settings page rendering through FastAPI TestClient.

2026-06-23 Context Package Viewer:

- Added a read-only context package viewer at `/work-items/{id}/capture`.
- The work item detail panel now shows `View Context Package` when a provider handoff has generated a local context path.
- The viewer reads generated files from `.automation-context/copilot/<branch>/capture/` in the configured WSL distro.
- Supported viewer tabs are `summary.md`, `INSTRUCTIONS.md`, and `manifest.json`.
- This gives reviewers and maintainers a dashboard-level audit trail for what evidence was sent to the configured agent before reading the final report or PR.

2026-06-23 DocumentationPortal Devcontainer One-Click Bootstrap:

- Updated `/workspaces/DocumentationPortal-#12.0` devcontainer setup so it no longer depends on bind-mounting a local `C:\CM-REPO\Content\CM-AI-Content-Skills` checkout.
- The target repository helper `.devcontainer/common/tfs-autonomous-pipeline.sh` now clones or updates the central `CM-AI-Content-Skills` repository from Git, using `CONTENT_AI_REPO_URL`, `CONTENT_AI_BRANCH`, `CONTENT_AI_REPO_PATH`, and `CONTENT_AI_TARGET_WORKSPACE`.
- Both `01-light` and `02-full` devcontainer profiles now configure the central repository URL, branch, clone path, target workspace, and pipeline port through `containerEnv`.
- The target repository `post-create.sh` now runs the pipeline helper unconditionally when present, so opening the repo in a devcontainer prepares the pipeline automatically.
- Removed legacy Streamlit dashboard tasks from the target repo because Cherry Picks are now integrated in the FastAPI pipeline dashboard.
- Added a `TFS Autonomous Pipeline - Sync Assets` VS Code task in the target repo.
- The central pipeline now supports an ignored local config file, `config/tfs_dashboard.local.json`, and reads/writes it when present.
- The central devcontainer bootstrap now creates `.env` from `.env.example` when missing, writes local dashboard runtime defaults for the devcontainer, creates `config/tfs_dashboard.local.json` with the active portal workspace set to `/app`, syncs managed assets, and excludes `.agents/content-ai/`, `.automation-context/`, and `.automation-reports/` locally from the target repo Git index.
- Current devcontainer branch points to `CONTENT_AI_BRANCH=projects-initial-backup`. Change this to `main` after the central Content AI changes are merged.

Next recommended tasks:

1. Test the capture package on a real work item with linked implementation PRs and compare generated drafts against the previous flow.
2. Remove or archive the standalone `projects/tfs-cherry-pick-dashboard` copy after confirming no unique source material remains there. Cherry Pick propagation is now integrated into this tool.
3. Add a first-class dashboard view for captured PR details and local diffs if the `summary.md`/`manifest.json` view is not enough during review.
4. Harden the dedicated worker/service deployment shape so production can run one continuous runner independently from the dashboard process.
5. Continue reducing Windows-host assumptions in the agent execution path. CLI providers should run natively inside the devcontainer; VS Code/Microsoft 365 Copilot providers remain Windows-host integrations unless a supported Linux/devcontainer automation surface is introduced.
6. Add workspace resolution UX for agent execution:
   - default to the current devcontainer workspace when it matches the configured portal repository;
   - support an explicit configured path for stable production use;
   - scan `/workspaces` for matching clones and expose a dashboard/settings selector when multiple valid workspaces exist;
   - validate the chosen workspace before launch by checking Git repository status, remote origin, selected branch/base branch access, and local-change safety.

2026-06-25 Devcontainer validation and execution portability:

- Confirmed with the user that the `DocumentationPortal-#12.0` one-click devcontainer setup is functionally working: the central project is installed, target assets are synced, VS Code tasks are available, and status-bar buttons work.
- Structural validation in the active devcontainer confirmed:
  - target repository exists at `/workspaces/DocumentationPortal-#12.0`;
  - `.agents/content-ai/` is synced into the target repository;
  - `.agents/content-ai/`, `.automation-context/`, and `.automation-reports/` are excluded locally from the target repository Git index;
  - pipeline virtual environment exists at `/home/vscode/.venvs/tfs-doc-automation-mvp`;
  - `tfs-autonomous-pipeline` wrapper exists and is executable;
  - VS Code task/settings/devcontainer JSON files are valid;
  - pipeline source syntax validates inside the container.
- Found a non-blocking environment inconsistency: the active container still has `CONTENT_AI_REPO_PATH=/home/vscode/.local/share/content-ai/CM-AI-Content-Skills`, while the current checked-in devcontainer files point to `/workspaces/CM-AI-Content-Skills`. Both clones currently point to `projects-initial-backup` at commit `c9c901f`; the active wrapper uses the `/home/vscode/.local/share/...` clone. A clean rebuild from the latest devcontainer files should align the environment, but the working setup is not blocked.
- Decided that `CONTENT_AI_BRANCH=projects-initial-backup` should remain as the working branch for now. It is a local/user backup branch and should not be automatically changed to `main`.
- Noted that the standalone Cherry Pick dashboard project is no longer needed as an active project because the functionality has migrated into this FastAPI tool. Do not delete it automatically without an explicit cleanup step.
- Clarified the previous "wsl.exe" implementation note: it referred to Windows-host assumptions in the execution layer. The target operating model is still devcontainer-on-WSL. CLI providers such as Codex/Claude/custom CLI must run natively inside Linux/devcontainer, while VS Code Copilot and Microsoft 365 Desktop flows remain Windows-host integrations.
- Updated `_run_wsl_script` so it uses `wsl.exe` only on Windows and uses local `bash -lc` when the dashboard is running inside Linux/devcontainer. The active container clone was also synchronized for immediate testing, and `container-native-ok` validation passed.

2026-06-25 Execution runtime setting:

- Added an explicit runtime setting named `Execution Runtime`.
- Default value is `devcontainer`, meaning workspace commands run directly with local Linux tools such as `bash` and `git` inside the current devcontainer/WSL environment.
- Optional value is `windows_host`, meaning workspace commands are bridged through `wsl.exe -d <distro>` so the dashboard can still run from Windows PowerShell when needed.
- The setting is persisted as `DOC_AUTOMATION_EXECUTION_RUNTIME` in `.env`, exposed in `Settings > Automation`, and summarized in the Settings header next to the agent provider.
- The setting is applied to the agent handoff, agent result polling, preflight validation, commit/push, context package viewer, and rich context capture local-diff collection.
- Keep `devcontainer` as the default for the one-click setup. Switch to `windows_host` only when intentionally running the dashboard process outside the devcontainer on Windows.

2026-06-25 Agent provider settings preflight:

- Added a provider preflight when saving runtime/automation settings.
- For `codex_cli`, the preflight runs `codex doctor --json` through the configured execution runtime and checks whether the CLI is installed and whether authentication is available in the runtime-specific `CODEX_HOME`.
- Settings are still saved even when the provider preflight fails, but the dashboard returns a warning message so missing/expired auth is detected before launching a real work item.

- This was added after a devcontainer run failed because the host Codex login did not exist inside `/home/vscode/.codex/auth.json`.
- When the Codex preflight fails because authentication is missing, the dashboard now starts `codex login --device-auth` in the configured runtime and returns the browser URL/device code in the Settings warning. This avoids requiring less experienced users to open a devcontainer terminal and manually discover the right login command.
- Added a similar preflight/remediation flow for TFS Git credentials inside the devcontainer.
- Portal settings save now validates `Git Credentials` with `git credential fill` for the configured TFS host and returns a warning when the devcontainer user cannot resolve credentials.
- Runtime settings save now also checks the selected portal credentials, so changing provider/runtime settings still surfaces missing TFS credentials before a real work item run.
- Automatic remediation is intentionally explicit: the process can copy a mounted credentials file from `CONTENT_AI_HOST_GIT_CREDENTIALS_PATH`, or write a local store entry from `CONTENT_AI_TFS_GIT_USERNAME` plus `CONTENT_AI_TFS_GIT_PASSWORD`/`CONTENT_AI_TFS_GIT_TOKEN`.
- The devcontainer bootstrap now performs the same Git credential preflight before cloning/updating the central `CM-AI-Content-Skills` repository and before writing the dashboard local config.
- The immediate test fix that proved the path was copying the host `.git-credentials` into `/home/vscode/.git-credentials`, setting `credential.helper=store`, and validating with `git credential fill` plus `git ls-remote`.

2026-06-25 TFS SSL runtime preflight:

- A later dashboard load reached TFS with valid credentials but failed because Python `requests` inside the devcontainer could not verify the corporate TFS certificate chain.
- Added runtime settings `DOC_AUTOMATION_TFS_VERIFY_SSL` and `DOC_AUTOMATION_TFS_CA_BUNDLE_PATH`.
- The `TfsClient` now passes the configured verification value to both JSON API calls and binary asset downloads.
- Runtime Settings exposes `Verify TFS SSL Certificate` and `TFS CA Bundle Path` so teams can either point to a corporate CA bundle or disable verification for trusted internal devcontainer environments.
- The devcontainer bootstrap writes `DOC_AUTOMATION_TFS_VERIFY_SSL=false` by default for the internal one-click setup unless `CONTENT_AI_TFS_VERIFY_SSL` is explicitly provided. Use `CONTENT_AI_TFS_CA_BUNDLE_PATH` when a proper mounted CA bundle is available.

2026-06-26 TFS API pull request diff fallback:

- A real test run for WI 154513 proved that the rich context capture correctly collected the parent User Story 133754, the full child task tree, linked PR 88346, PR description, changed files, commits, and review comments.
- The run failed before edits because the configured Codex CLI provider hit usage limits before writing `agent-result.json`; no push or draft PR was attempted, which is the expected safe behavior.
- The only capture gap was PR diff availability: the linked implementation PR belonged to repository `Product`, which was not cloned under `/workspaces`, so the previous local-git diff strategy wrote `diff unavailable`.
- Added a TFS API fallback for PR diffs. When no suitable local clone is found, the capture engine now uses PR `lastMergeTargetCommit`, `lastMergeSourceCommit`, iteration changes, and Git Items API content to generate a synthetic unified `diff.patch`.
- The fallback protects the LLM context with file and total-size limits and skips binary or unavailable content instead of failing the whole capture.
- Validation against PR 88346 produced `diff_source: tfs-api (16 file diff(s))` and a readable unified diff without requiring a local `Product` clone.

2026-06-26 Draft PR description size limit:

- A later WI 154513 run reached commit and push successfully, but Draft PR creation failed because TFS rejects pull request descriptions longer than 4000 characters.
- The previous PR description included too much of the final report and could include audit-trail sections that are useful in the dashboard but not useful in the PR overview.
- Draft PR descriptions are now capped below the TFS limit and include only reviewer-facing sections from the final report: Work Item, Summary, Changes Made, Why These Changes Were Made, Changed Files, and Spec References.
- Detailed capture evidence, instruction files read, PRs reviewed, diffs reviewed, dashboard validation, and reviewer audit details remain in the full final report but are intentionally omitted from the Draft PR description.
- Retried WI 154513 after the fix and created Draft PR 88746 successfully.

2026-06-26 Real work item validation and abandoned PR handling:

- Validated the end-to-end automatic flow on WI `154513` in `DocumentationPortal-#12.0`.
- Abandoned the previous draft PR `88746`, deleted the old work branch, reset the persisted automation state, and relaunched the flow from a clean branch.
- Confirmed the context capture package included related work items, repository instructions from `AGENTS.md` and `.agents/content-ai/`, and implementation PR evidence from PR `88346`.
- The agent produced documentation changes, validation passed, the dashboard pushed commit `c65f293a0a0e6dcae61249c474f83d838b10dedd`, and draft PR `88780` was created successfully.
- Confirmed the draft PR was linked to parent work item `133754`.
- Found and fixed a flow bug where abandoned PR links attached to a parent work item could still be treated as blocking associated PRs.
- The automation now validates linked PR references against the current TFS PR status, ignores abandoned PRs for work-item and branch matching, and avoids using abandoned PRs when creating a new draft PR.
- Final report and draft PR description generation now use a useful default rationale instead of the empty fallback text `No detailed rationale was reported by the agent.`

2026-06-26 Work item automation history:

- Added durable `work_item_events` storage for per-work-item automation audit events.
- Existing state transition helpers now append events for plan saves, branch results, agent handoff, automatic-flow enable/disable, agent repair launches, agent result checks, pushes, final report creation, reruns, and draft PR results.
- Added idempotent legacy backfill from `work_item_state` so previously processed work items still show a useful initial timeline.
- The lazy-loaded work item detail panel now includes an `Automation History` section with event stage, status, timestamp, message, level, and metadata such as branch names, paths, commits, and PR URLs.
- Smoke tested in the active `DocumentationPortal-#12.0` devcontainer on WI `154513`; the detail view rendered a seven-event history from Plan through Draft PR.

2026-06-26 Work item detail UX redesign:

- Reworked the lazy-loaded work item detail panel into a control-panel layout.
- The top area now shows the active automation stage, branch/agent/push/PR status, progress strip, primary actions, effective branch, reviewer, and parent work item.
- Moved noisy details into collapsible sections: `Automation`, `Work Item Context`, `Branch & Review`, and `Reports & Technical Evidence`.
- Kept all existing forms, hidden fields, action gating, context package links, final report links, linked PRs, and rich work item rendering behavior.
- Added CSS support for compact action bars, status signals, metadata rows, two-column detail groups, and disclosure panels.
- Local validation passed with `git diff --check`, Jinja parsing, Python compilation, and direct template rendering inside the active devcontainer.
- A live detail request in the active devcontainer could not complete because the container runtime currently lacks interactive TFS Git credential resolution; the dashboard homepage still responded successfully on port `8010`.

2026-06-30 Devcontainer and publication hardening:

- Removed internal runtime report paths from Draft PR descriptions. The PR description now keeps reviewer-facing final report sections plus the TFS work item link, but no longer publishes local paths such as `/app/.automation-reports/.../final-report.md`.
- Changed the central dashboard default port from `8000` to `7000` in runtime defaults, `.env.example`, the central project devcontainer, VS Code task input defaults, and the devcontainer bootstrap wrapper. This avoids the common MkDocs `8000` port.
- Kept the central project clone destination standardized as `CONTENT_AI_REPO_PATH=/workspaces/CM-AI-Content-Skills`; target repositories still mount as `/app` inside their devcontainer profiles, and that path is only the active target workspace.
- Confirmed the `DocumentationPortal-#12.0` devcontainer bootstrap changes belong on branch `12.0/chore/content-ai-devcontainer-bootstrap`; used a separate worktree at `/workspaces/_review_worktrees/DocumentationPortal-content-ai-devcontainer-bootstrap` to avoid disturbing the active WI test branch.
- Removed manual VS Code extension installation from the target repo `post-create.sh`. Status bar/task button extensions are now left to VS Code devcontainer recommendations and `customizations.vscode.extensions`.
- Added a managed target-repository `ai/instructions/AGENTS.md` asset and updated the sync script to copy that file into the target repository root by default, while still copying the complete asset package under `.agents/content-ai/`.
- The sync script now backs up a pre-existing root `AGENTS.md` under `.agents/content-ai/backups/`, excludes untracked root `AGENTS.md`, and marks tracked root `AGENTS.md` as `skip-worktree` after managed sync so bootstrap-only instruction updates do not block automation safety checks.
- Added `CONTENT_AI_SYNC_ROOT_AGENTS=false` as the opt-out for repositories that must keep a repository-owned root `AGENTS.md`.

2026-07-01 Persistent devcontainer tool checkout and settings:

- Confirmed that the stale Windows dashboard process on port `8001` was a leftover local `uvicorn` instance and stopped it. The active devcontainer dashboard should use port `7000`.
- The `DocumentationPortal-#12.0` devcontainer profiles now bind-mount `/workspaces/CM-AI-Content-Skills` into the container, so the central Content AI project clone is visible from the WSL host and reusable across target repositories instead of living only in the container filesystem.
- Added a second bind mount for `/workspaces/.content-ai-settings`, with the pipeline settings path set to `/workspaces/.content-ai-settings/tfs-doc-automation-mvp`.
- The devcontainer bootstrap now restores `.env` and `config/tfs_dashboard.local.json` from `CONTENT_AI_SETTINGS_PATH` when available, writes the devcontainer defaults, and mirrors the resulting files back to the persistent settings folder.
- The dashboard settings save path now mirrors runtime `.env` and local portal config changes to `CONTENT_AI_SETTINGS_PATH`, so user configuration survives rebuilds and repo switches.
- The intended layout is now: target repository at `/app`, central tool checkout at `/workspaces/CM-AI-Content-Skills`, and non-Git local settings at `/workspaces/.content-ai-settings/tfs-doc-automation-mvp`.
- A follow-up test showed that opening the devcontainer from a Git worktree can leave `/app/.git` pointing to a shared Git directory outside the container mount, such as `/workspaces/DocumentationPortal-#12.0/.git/worktrees/...`. The target devcontainer profiles now mount the full WSL host `/workspaces` tree so Git worktree metadata, sibling repositories, the central tool checkout, and persistent settings are all visible inside the container.
- Added an explicit `TFS Git Credentials Setup` form to the Settings > Connection page for portals using `Git Credentials`. The form writes the provided username/token through `git credential approve` into the devcontainer user's Git credential store, validates dashboard credential lookup, and validates repository access with `git ls-remote --heads origin`.
- Confirmed that Windows Git/GCM credentials can be available on the host but not reusable by Linux Git inside the devcontainer. The one-click setup must therefore include an explicit in-container TFS credential step instead of assuming checkout credentials are automatically available to the automation runtime.

2026-07-01 Codex CLI devcontainer preflight:

- A Settings save showed `Codex CLI executable was not found on PATH or at $HOME/.npm-global/bin/codex` even though Codex was configured on the WSL host.
- Root cause: the dashboard was running inside the devcontainer, where the WSL host Codex executable is not automatically available. The container needs its own executable on `PATH`, while auth can be reused through a mounted `CODEX_HOME`.
- The active devcontainer now has Codex CLI installed at `/home/vscode/.npm-global/bin/codex` and uses mounted auth under `/home/vscode/.codex`.
- Updated the Codex preflight and device-login scripts to export `CODEX_HOME`, `NPM_CONFIG_PREFIX`, and a devcontainer-safe `PATH`, and to search `$NPM_CONFIG_PREFIX/bin/codex` before falling back to `$HOME/.npm-global/bin/codex`.
- Validated inside the active container that `codex doctor --json` reports `installation ok` and `auth.credentials ok`; the remaining overall warning was only that the Codex app server was not running, which does not block CLI execution for this automation.

2026-07-01 Target workspace selector:

- A WI run attempted to use the active devcontainer workspace (`/app`, backed by the 12.0 bootstrap worktree) even though the intended target for that task was `/workspaces/DocumentationPortal-#01`.
- Added a Dashboard `Target Workspace` selector that discovers matching local clones under `/workspaces`, shows the currently selected workspace, and saves only the portal workspace path without requiring the full Settings form.
- Added workspace suggestions to Settings > Connection for the manual `Agent Workspace Path` field.
- Changed work item decoration to prefer the current portal workspace over any historical per-WI workspace stored from a previous agent handoff, so changing the target workspace affects the next automation action.
- Fixed workspace discovery so `/workspaces` is treated as a scan root rather than being reduced to `/`.
- Set the active `DocumentationPortal` portal workspace to `/workspaces/DocumentationPortal-#01` for the WI 152523 test.

2026-07-01 Workspace-level automation serialization:

- A parallel test with multiple WIs against the same `DocumentationPortal-#01` clone showed that independent worker threads could switch branches while another agent was still editing the same working tree.
- Failure observed: WI 152535 produced a green-light `agent-result.json`, but dashboard validation ran while the clone was still on WI 152523's branch, causing `Workspace is on branch ... expected ...`.
- Updated the bulk automatic flow so selected items are queued and scheduled instead of launching every agent immediately from the HTTP request.
- Added a per-workspace runner lock. The automatic worker holds the lock for the whole lifecycle of one WI on that clone: launch, wait for agent result, validation, push, and draft PR creation. Other WIs targeting the same workspace remain active but wait their turn.
- Updated the dashboard bulk summary to report queued items separately from items already waiting for agent output.
- Recovered the affected WI 152535 by moving the generated `perform_setup.md` change from the WI 152523 branch onto the WI 152535 branch, then continuing the pipeline. WI 152523 created PR 89068 and WI 152535 created PR 89085.

2026-07-02 Persistent TFS Git credentials:

- A dashboard restart/rebuild test showed that the user had to re-enter TFS Git credentials and WI execution still failed with `Git credential lookup timed out after 10s`.
- Root cause: `.env` and `config/tfs_dashboard.local.json` were persisted through `CONTENT_AI_SETTINGS_PATH`, but the Git credential store was only written to the ephemeral devcontainer home at `/home/vscode/.git-credentials`.
- Added persistence for the Git credential store: `Settings > Connection > TFS Git Credentials Setup` now mirrors `~/.git-credentials` to `CONTENT_AI_SETTINGS_PATH/git-credentials`.
- The dashboard credential preflight now restores `CONTENT_AI_SETTINGS_PATH/git-credentials` back to `~/.git-credentials` before calling `git credential fill`.
- The devcontainer bootstrap now restores the same persisted credential store before clone/pull and forces the global Git credential helper to `store` to avoid interactive helper timeouts.
- Secrets remain outside the project repository and outside `.env`; they live only in the local non-Git settings folder under `/workspaces/.content-ai-settings/tfs-doc-automation-mvp`.

2026-07-02 Git push Docker credential fallback:

- WI 152491 reached agent `green_light`, created the final report, and committed locally, but failed during push because the repository `pre-push` hook runs markdownlint through Docker.
- The failing hook command used `proxy.criticalmanufacturing.io/davidanson/markdownlint-cli2:v0.12.1`; Docker failed before linting with `error getting credentials` because the devcontainer Docker config pointed to a broken credential helper.
- Confirmed that the same image can be pulled with an isolated empty `DOCKER_CONFIG`, and that the repository hook then passes markdownlint successfully.
- Added a push retry path: when `git push` fails with Docker credential-helper output, the automation retries the same push with a temporary isolated `DOCKER_CONFIG`. This keeps repository hooks enabled while avoiding stale devcontainer Docker credential stores.
- Added idempotent push recovery: if a retry/race returns a non-zero push error but `origin/<branch>` already points to the local `HEAD`, the automation treats the push as successful instead of leaving the dashboard in an error state.

2026-07-06 Prebuilt devcontainer image planning:

- Added `scripts/content-ai-post-create.sh` for the future Docker image flow where the Content AI project and dependencies are already installed in the image.
- The new post-create script uses the image copy as a seed, ensures a writable runtime copy under `/workspaces/CM-AI-Content-Skills`, restores persistent settings from `/workspaces/.content-ai-settings/tfs-doc-automation-mvp`, syncs managed AI assets into the target repository, prepares a clean Docker config for hook-driven linting, and creates the `tfs-autonomous-pipeline` wrapper.
- Added `docs/docker-image-post-create.md` to define the Docker image requirements, expected paths, target devcontainer configuration, post-create responsibilities, environment variables, security model, and failure policy.

2026-07-08 Pipeline run auto-sync:

- A user hit a raw `git pull` failure because the runtime Content AI copy in `/workspaces/CM-AI-Content-Skills` had local changes while the remote branch was ahead.
- Decided that the normal Run TFS Pipeline task must not require a hidden manual sync step. The generated `tfs-autonomous-pipeline dashboard` and `worker` commands now run a safe project sync first.
- The sync fetches the configured `CONTENT_AI_BRANCH`, backs up local runtime-copy changes to `CONTENT_AI_SETTINGS_PATH/backups`, auto-stashes them when `CONTENT_AI_AUTO_STASH_ON_UPDATE=true`, and then fast-forwards.
- If auto-stash is disabled or the image-managed runtime copy cannot be updated from Git, the wrapper prints a clear remediation message instead of exposing raw Git merge errors.

2026-08-10 VS Code Copilot handoff diagnostics:

- Investigated a failed automatic handoff for WI 157706 in the active devcontainer. The portal workspace `/app` is a container mount, so it must never be opened as a `vscode-remote://wsl+Ubuntu/app` folder URI because that path does not exist on the WSL host and causes VS Code to request a workspace.
- Updated the VS Code handoff logic to use the current devcontainer workspace and container-local context paths when the dashboard runs through the VS Code Remote CLI. The resolver also explicitly prefers the VS Code `remote-cli` binary over generic `code` wrappers that can appear earlier on `PATH`.
- The live capability test established that the installed VS Code CLI does not support `code chat --mode ... --add-file ...`; it ignores the chat options and therefore cannot programmatically start a Copilot agent with the prepared context package.
- Added a VS Code provider preflight on Settings save and before WI launch. Unsupported environments now fail immediately with a clear remediation message, before context capture, branch edits, or background polling begin.
- Current operational decision: use Codex CLI (or another supported CLI executor) for fully autonomous runs in this devcontainer. VS Code Copilot requires a future VS Code version with Chat CLI support or a dedicated extension/bridge that exposes an automation API.

2026-08-11 VS Code Copilot Bridge implementation:

- Added `vscode_bridge` as a distinct provider, preserving the existing Codex CLI execution and result-processing flow.
- Added the private `criticalmanufacturing.cmf-content-ai-pipeline-bridge` VS Code workspace extension under `vscode-copilot-bridge/`, together with a reproducible VSIX packaging script.
- The dashboard now writes a durable `bridge-job.json` next to each prepared context package. The bridge processes the job in the active devcontainer workspace, uses the public VS Code Language Model API to select the configured Copilot model, performs a bounded controlled read/list/search/apply loop, and writes the standard `agent-result.json` and bridge state files.
- The bridge only accepts workspace-relative edits and requires exact `old_text` preconditions for replacements. It does not execute shell commands, access the network, perform Git operations, or create pull requests.
- Added `vscode_bridge` preflight validation. It checks that the bridge is installed in the current devcontainer user's VS Code Server extensions folder before a WI context is prepared.
- Added bridge installation to both the regular and prebuilt-image devcontainer bootstrap paths. The bootstrap copies the bridge into the VS Code Server extensions folder and uses the Remote CLI to register its VSIX when that CLI socket is available; the copied extension remains available after the next remote reconnect.
- Important platform constraint: VS Code may require a one-time signed-in-user consent dialog before any extension can invoke Copilot through the public Language Model API. This is not bypassed. Once granted, the bridge can execute queued jobs without an item-by-item manual chat handoff.
- Next validation: rebuild/reopen the target devcontainer, confirm bridge preflight in Settings, then run one real WI with `vscode_bridge` and the approved configured Copilot model. Confirm the job produces `agent-result.json`, passes dashboard validation, and continues through push and Draft PR creation.

2026-08-17 Parent work item dashboard context:

- Detail loading already retrieves the parent work item lazily from TFS. Extended that payload to retain the parent title, state, description, acceptance criteria, repro steps, attachments, hyperlinks, and web URL.
- Added a collapsed `Parent Work Item Context` disclosure between the task context and branch/review controls. It shows the linked parent identifier, title, state, rich description, optional criteria/repro steps, and parent references/images.
- Parent rich HTML uses the same sanitization and TFS asset proxy as the task context. The dashboard list remains fast because no extra parent data is requested until a user opens a work item card.
- Parent fields are explicitly requested from the TFS batch API, avoiding dependence on the API default field set when rendering parent descriptions. TFS does not permit `fields` and `Relations` in one batch request, so parent fields and relations are fetched separately and combined locally.
- TFS Bug work items can store their substantive context in `Microsoft.VSTS.TCM.ReproSteps` instead of `System.Description`. The parent panel treats Repro Steps as a labeled description fallback and avoids rendering the same content twice.

2026-08-18 VS Code Copilot bridge window and consent handling:

- New installations now default to opening VS Code in a new window for agent handoffs. The bridge job records this request and the bridge extension opens the current remote workspace in a separate VS Code window before processing the task.
- The VS Code Language Model API requires a user-granted consent decision before an extension can access Copilot models. This approval cannot be bypassed programmatically. The bridge now persists an `awaiting_copilot_access` state while the platform consent UI is pending, rather than silently appearing stalled.
- Fixed a window-handoff deadlock observed for WI 158015: requesting a new window no longer marks the request as complete and waits forever for a second extension host. The active bridge claims the job, records `running`, and continues processing after requesting the new window. A per-window execution identifier prevents another VS Code window from processing the same job concurrently.
- Added a `waiting_for_model` bridge state so dashboard polling distinguishes a pending VS Code Copilot authorization/model-selection dialog from normal agent execution.
- Reworked `needs_agent_fix` rendering after WI 158015 exposed raw internal diagnostics in the primary UI. The dashboard now presents a concise pipeline decision, blocking conditions, recommended next steps, and an opt-in technical-details disclosure rather than showing file paths and validation output as the main error message.

2026-08-18 Isolated VS Code bridge worktrees:

- A live WI 157502 test confirmed that VS Code Remote can accept `vscode.openFolder(..., forceNewWindow)` for the same `/app` folder without opening a visibly separate window. The old fallback therefore continued the bridge in the dashboard workspace and changed its active Git branch.
- The `vscode_bridge` new-window mode now creates or reuses a dedicated worktree at `/workspaces/.content-ai-worktrees/<repository>/<branch>` before preparing the agent context. The dashboard workspace remains a dispatcher and is never switched to the work-item branch.
- The dispatcher bridge job opens the separate worktree with the existing remote authority. The bridge extension activated in that window processes the actual job stored in the isolated worktree. This makes new windows and Git isolation a single reliable mechanism.
- Improved the bridge JSON parser to accept the first complete JSON object when a Copilot model appends extra JSON or prose. WI 157502 previously failed because the old parser combined multiple objects into invalid JSON.
- Validation completed with Python compilation, Node syntax checking, and a live isolated worktree creation for the WI 157502 rerun branch. The active devcontainer bridge files were refreshed; VS Code must reload its window once to activate the new extension code.
- A first rerun exposed a worktree path parsing defect: some Git versions print `HEAD is now at ...` to standard output during `git worktree add`. The launcher now redirects that informational output and reads only the final explicit path line, preventing a malformed multi-line workspace path from reaching Git validation.
- A subsequent WI 157502 run proved that the isolated target window executes the bridge successfully, selecting GPT-5.6 Terra and updating the target documentation file. The VS Code Language Model API does not create a visible Chat conversation; dashboard/bridge status is the authoritative execution signal.
- The bridge now includes the packaged contents of every indexed repository instruction in its controlled context, records their original paths in `instruction_files_read`, and filters `.automation-context` artifacts from `changed_files` before dashboard validation and publication.
- The pipeline runs Git credential preflight again immediately before commit/push. This restores the persistent `store` helper if VS Code Remote has replaced it with its interactive helper after container startup.
- WI 157502 rerun history: the first isolated-window run edited the documentation successfully but did not publish because the Git credential helper was still interactive. The following rerun failed before edits because the bridge treated one missing model-requested file as a terminal error. No WI 157502 rerun has completed through commit, push, and Draft PR yet.
- The bridge now treats read/list/search/apply tool errors as feedback for the model's next iteration. A missing guessed path can no longer terminate an otherwise recoverable job.

2026-08-20 GitHub Copilot CLI autonomous provider:

- Added `copilot_cli` as a separate autonomous provider without changing the established `codex_cli` path.
- The provider executes GitHub Copilot CLI non-interactively inside the configured DevContainer/Linux runtime, passes the dashboard `Model Name` to `--model`, and gives the agent the full prepared handoff through `-p`.
- The agent can read, write, and run validation shell commands but is denied Git commit, push, reset, clean, and removal commands. The dashboard remains responsible for validating `agent-result.json`, committing, pushing, writing the final report, and creating the Draft PR after `green_light`.
- Added bootstrap and post-create installation for `@github/copilot`, controlled by `TFS_AUTONOMOUS_INSTALL_GITHUB_COPILOT_CLI=true`.
- Provider preflight now confirms that the executable exists, authentication works, and the configured model can answer a small non-interactive request. Missing authentication starts a one-time GitHub OAuth device flow and stores its CLI state under the persisted Content AI settings directory when available.
- WI 157946 is the next real end-to-end validation once the GitHub device authorization is completed for the active devcontainer.
- Added the persisted `GitHub Copilot Host` runtime setting for Enterprise Cloud data-residency tenants. The active test environment uses `https://criticalmanufacturing.ghe.com`; device authorization is launched with `copilot login --host <configured-host> --device-code`.
- Headless DevContainers generally have no Linux keychain. Copilot CLI therefore asks whether its OAuth token may be stored in its plaintext `config.json`; the dashboard login launcher accepts this explicit fallback and applies mode `0700` to the persisted Copilot home. Without that response, OAuth can succeed but the CLI cannot retain the token.

2026-08-20 GitHub Copilot CLI result recovery:

- A live WI 157946 run proved that GitHub Copilot CLI can authenticate through the persisted native `~/.copilot` state, read the complete context package, and edit the target branch. The authorization is not per work item: bootstrap links `~/.copilot` to `CONTENT_AI_SETTINGS_PATH/copilot-home`, so it survives DevContainer recreation unless the login expires, is revoked, or the persisted settings are removed.
- The provider may finish a useful implementation response without creating the required `agent-result.json`. The CLI wrapper now detects this successful-but-incomplete condition and starts a second bounded Copilot request that only inspects the prepared context and current diff, returning the required JSON contract. A local parser stores that response as `agent-result.json`.
- If the recovery response is not parseable JSON, the wrapper still writes a conservative `needs_manual_review` result. This prevents a silent/stuck waiting state and never grants a push green light without a machine-readable report that passes the existing dashboard validation.
- Copilot CLI runs use `--mode=autopilot`, `--stream=off`, and a bounded continuation count. This prevents a non-interactive invocation from stopping after an intermediate progress message while preserving the existing restricted tool allow/deny policy.

2026-08-20 WI 157946 end-to-end Copilot CLI validation:

- Reran WI 157946 on `12.0/fix/157946-doc-is-is-not-possible-to-use-google-as-a-viable-ai-provider-rerun-20260820183525`. GitHub Copilot CLI read the captured work items and linked implementation PRs, updated only `docs/includes/docsync/dockerenvironmentvariables.md` and `docs/includes/docsync/generativeaimodels.md`, completed the MkDocs build, and wrote a green-light result.
- A DevContainer source reload exposed a resume bug: persisted flows called the normal bulk-start path, which rewrote the plan and attempted a second agent launch against the agent's own uncommitted diff. Resume now schedules continuation with the saved plan and branch instead. The worker also treats an existing non-waiting result state as work to continue, including recovery from stale state metadata.
- The local Markdown link check was incorrectly failing links that already existed in the base revision and are resolved by the portal's MkDocs configuration. It now compares current broken-link targets against `HEAD` and fails only newly introduced unresolved targets. WI 157946 passed `git diff --check` and the baseline-aware link validation; markdownlint was unavailable in the active container and was recorded as skipped.
- The first dashboard commit attempt exposed that the container had no Git author identity. Configured the test worktree locally as `Luís Pereira <LuisPereira@criticalmanufacturing.com>` and recorded a follow-up: add Git author name/email to the Settings preflight and bootstrap, rather than discovering it during commit.
- The completed run created commit `146c7ef25` (`Docs: update for WI 157946`), pushed the rerun branch, and created Draft PR `#91499`.

2026-08-21 Automation observability and Draft PR report resilience:

- Added a compact `Active Automation` panel at the beginning of the dashboard for visible work items with an active automatic flow. It polls persisted local state every 10 to 15 seconds without reloading TFS and reports the current durable pipeline activity.
- The status message is derived from branch, agent, result, push, and Draft PR state, so it survives dashboard restarts and remains separate from the orchestration worker.
- A missing concise agent summary no longer blocks a successful push from creating a Draft PR. The pipeline first starts one metadata-only agent repair that must rewrite `agent-result.json` without editing repository files. If no usable summary is available after that attempt, the Draft PR uses a safe summary from the final report or changed-file list and records the fallback in the event history.

2026-08-21 Isolated worktree repair continuity:

- Work item state persists the isolated `copilot_workspace_path` returned by the agent launcher. Item reconstruction must always prefer that persisted path over the portal's configured dispatcher workspace.
- This is essential after an agent result needs repair: the repair prompt, instruction acknowledgement, Git preflight, and change validation must run in the work item's own worktree, never in `/app`.
- A two-item concurrent rerun (WI 157946 and WI 157980) exposed the previous precedence bug. Both isolated worktrees and their agent results were correct, but state reconstruction replaced their paths with `/app`, causing a false branch-mismatch error and preventing the automatic repair from starting.
- The bootstrap-managed `.agents/content-ai` tree is excluded from mandatory instruction acknowledgement. It remains available to agents, while the target repository `AGENTS.md` and its own `.agents` skills remain captured and must be acknowledged. This avoids blocking a valid result because of copied changelogs, readmes, and duplicate helper instructions.
