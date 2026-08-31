# CM AI Content Tools

AI tools built and used by the Critical Manufacturing Content Team.

This repository is the companion of [CM-AI-Content-Skills](https://github.com/usulpt/CM-AI-Content-Skills): that repository publishes the reusable assets (skills, subagents, shared rules) that documentation portals consume; this one holds the internal tooling that puts those assets to work.

## Tools

| Tool | Purpose | Documentation |
| --- | --- | --- |
| [`tfs-doc-automation-mvp/`](tfs-doc-automation-mvp/) | TFS Autonomous Pipeline: analyzes TFS work items, prepares documentation branches, hands work to an agent provider, and drafts PRs under human supervision. | [README](tfs-doc-automation-mvp/README.md) · [Technical design](tfs-doc-automation-mvp/docs/technical-design.md) · [Devcontainer image contract](tfs-doc-automation-mvp/docs/docker-image-post-create.md) |

## Conventions

- One top-level folder per tool. Each tool is self-contained: its own `README.md`, dependencies, scripts, docs, and local `.gitignore`.
- Tools consume the shared assets from a sibling `CM-AI-Content-Skills` checkout (by default `/workspaces/CM-AI-Content-Skills`, overridable with `CONTENT_AI_REPO_PATH`). Never copy skills, subagents, or the managed `AGENTS.md` block into this repository.
- Persistent, non-Git tool settings live outside the checkout, under `/workspaces/.content-ai-settings/<tool>/`.
- All documentation, README files, and code comments are written in English.
- Tools that agents work on keep their own `.agents/memory.md` project memory.

## Runtime layout

The tools assume two sibling checkouts on the WSL host, bind-mounted into every target devcontainer as `/workspaces`:

```text
/workspaces/CM-AI-Content-Tools     this repository   (CONTENT_AI_TOOLS_REPO_PATH)
/workspaces/CM-AI-Content-Skills    shared assets     (CONTENT_AI_REPO_PATH)
/workspaces/.content-ai-settings/   persistent per-tool settings
```

## Adding a tool

1. Create `<tool-name>/` at the repository root with a `README.md` that states the purpose, how to run it, and its configuration.
2. Keep environment variables namespaced (`CONTENT_AI_*` for shared runtime paths, a tool-specific prefix for the rest).
3. Add a row to the table above and an entry in `CHANGELOG.md`.
