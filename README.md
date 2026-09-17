# CM AI Content Tools

AI tools built and used by the Critical Manufacturing Content Team.

This repository is the companion of [CM-AI-Content-Skills](https://github.com/usulpt/CM-AI-Content-Skills): that repository publishes the reusable assets (skills, subagents, shared rules) that documentation portals consume; this one holds the internal tooling that puts those assets to work.

## Tools

| Tool | Purpose | Documentation |
| --- | --- | --- |
| [`tfs-doc-automation-mvp/`](tfs-doc-automation-mvp/) | TFS Autonomous Pipeline: analyzes TFS work items, prepares documentation branches, hands work to an agent provider, and drafts PRs under human supervision. | [README](tfs-doc-automation-mvp/README.md) · [User guide](tfs-doc-automation-mvp/docs/user-guide.md) · [Maintainers guide](tfs-doc-automation-mvp/docs/maintainers-guide.md) · [Technical design](tfs-doc-automation-mvp/docs/technical-design.md) · [Devcontainer image contract](tfs-doc-automation-mvp/docs/docker-image-post-create.md) |

## Conventions

- One top-level folder per tool. Each tool is self-contained: its own `README.md`, dependencies, scripts, docs, and local `.gitignore`.
- Tools install the shared assets into portals with APM straight from the published `CM-AI-Content-Skills` package (`CONTENT_AI_APM_DEPENDENCY`, default `jpsmoreira-com/CM-AI-Content-Skills#main`; a local checkout path works offline), as that repository prescribes. No tool keeps its own copy of the assets. Never copy skills, subagents, or instruction blocks into this repository or distribute them by another path.
- Persistent, non-Git tool settings live outside the checkout, under `<repos-parent>/.content-ai-settings/<tool>/`.
- All documentation, README files, and code comments are written in English.
- Tools that agents work on keep their own `.agents/memory.md` project memory.

## Runtime layout

The only layout assumption the tools make is that this checkout (and the target repositories they work on) sit under one shared parent folder. That folder can be any path and is provided via `CONTENT_AI_WORKSPACE_ROOT`; when the variable is not set, the tools log a warning and default to `/workspaces`, the conventional WSL/devcontainer mount point:

```text
<repos-parent>/CM-AI-Content-Tools     this repository   (CONTENT_AI_TOOLS_REPO_PATH)
<repos-parent>/.content-ai-settings/   persistent per-tool settings
```

When target devcontainers are used, the shared parent folder must be bind-mounted into the container so the checkout and the settings stay visible.

## Adding a tool

1. Create `<tool-name>/` at the repository root with a `README.md` that states the purpose, how to run it, and its configuration.
2. Keep environment variables namespaced (`CONTENT_AI_*` for shared runtime paths, a tool-specific prefix for the rest).
3. Add a row to the table above and an entry in `CHANGELOG.md`.

## License

[BSD 3-Clause](LICENSE), the same license as the other Critical Manufacturing repositories.
