# Changelog

## 0.1.0 - 2026-08-31

- Created the repository as the home for the Content Team's internal AI tools.
- Moved `tfs-doc-automation-mvp` here from `CM-AI-Content-Skills/projects/` with its full commit history.
- Split the runtime contract into two checkouts: the tools repository (`CONTENT_AI_TOOLS_REPO_PATH`, default `/workspaces/CM-AI-Content-Tools`, seeded from `CONTENT_AI_TOOLS_IMAGE_REPO_PATH`) and the shared-assets repository (`CONTENT_AI_REPO_PATH`, default `/workspaces/CM-AI-Content-Skills`). The bootstrap and post-create scripts clone, refresh, and validate both.
- Licensed under BSD 3-Clause (`LICENSE`).
