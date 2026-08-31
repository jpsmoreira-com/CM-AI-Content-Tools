# AGENTS.md

Rules for agents working in this repository.

- This repository holds internal AI tools for the Critical Manufacturing Content Team; one top-level folder per tool. Read the tool's own `README.md` and `.agents/memory.md` (when present) before changing it.
- Shared skills, subagents, and the managed rules block are owned by the sibling `CM-AI-Content-Skills` repository. Do not copy them here; reference them through `CONTENT_AI_REPO_PATH`.
- Write all documentation, comments, and commit messages in English.
- Do not commit secrets, `.env` files, local config overrides, or generated `data/` folders.
- Keep the tool's `.agents/memory.md` current when the project direction or a meaningful implementation step changes.
