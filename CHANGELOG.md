# Changelog

## Unreleased

- `tfs-doc-automation-mvp`: shared Content AI assets are now installed with APM, following the `CM-AI-Content-Skills` 1.0 contract. `scripts/sync-content-ai-assets.sh` runs `apm install`/`apm compile` (honoring a committed portal `apm.yml`, or generating a git-excluded one pointing at the published `main` branch until a release tag exists), cleans up the previous `.agents/content-ai` + dotagents layout, and the pipeline's instruction discovery reads `.github/instructions/` and `.claude/rules/`. `CONTENT_AI_SYNC_ROOT_AGENTS` and `CONTENT_AI_DOTAGENTS*` are gone; `CONTENT_AI_APM_VERSION`, `CONTENT_AI_APM_DEPENDENCY` and `CONTENT_AI_APM_INSTALL_CLI` replace them.
- `tfs-doc-automation-mvp`: application log with reference ids, persisted runner health with `GET /health`, per-work-item diagnostics viewer and bundle, and machine-readable agent result codes.
- `tfs-doc-automation-mvp`: removed the `m365_desktop` provider and other dead code; README and technical design aligned with shipped behavior; added maintainers and user guides; `CONTENT_AI_WORKSPACE_ROOT` replaces the hardcoded `/workspaces`.

## 0.1.0 - 2026-08-31

- Created the repository as the home for the Content Team's internal AI tools.
- Moved `tfs-doc-automation-mvp` here from `CM-AI-Content-Skills/projects/` with its full commit history.
- Split the runtime contract into two checkouts: the tools repository (`CONTENT_AI_TOOLS_REPO_PATH`, default `/workspaces/CM-AI-Content-Tools`, seeded from `CONTENT_AI_TOOLS_IMAGE_REPO_PATH`) and the shared-assets repository (`CONTENT_AI_REPO_PATH`, default `/workspaces/CM-AI-Content-Skills`). The bootstrap and post-create scripts clone, refresh, and validate both.
- Licensed under BSD 3-Clause (`LICENSE`).
