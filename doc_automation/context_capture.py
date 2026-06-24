from __future__ import annotations

import html.parser
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .tfs_client import (
    TfsApiError,
    TfsClient,
    build_pr_web_url,
    build_work_item_web_url,
    clean_branch,
    extract_pull_request_relations,
    extract_work_item_id_from_url,
)


API_COMMENTS = "6.0-preview.3"
API_THREADS = "5.0"
CHILD_RELATION_TYPES = {"System.LinkTypes.Hierarchy-Forward", "Hierarchy-Forward"}
COMMIT_RE = re.compile(r"Git/Commit/([^/%]+)(?:%2[fF]|/)([^/%]+)(?:%2[fF]|/)([0-9a-f]+)", re.IGNORECASE)


class _HTMLText(html.parser.HTMLParser):
    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "ul", "ol", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        if tag == "li":
            self.parts.append("\n- ")
        elif tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: Any) -> str:
    if not str(value or "").strip():
        return ""
    parser = _HTMLText()
    parser.feed(str(value or ""))
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"\n[ \t]+\n", "\n\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def slug(value: Any, max_length: int = 60) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (text[:max_length].strip("-") or "untitled")


def field(work_item: Dict[str, Any], key: str, default: Any = "") -> Any:
    return (work_item.get("fields") or {}).get(key, default)


def identity_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("uniqueName") or "").strip()
    return str(value or "").strip()


def safe_get_json(client: TfsClient, url: str, errors: List[str], label: str) -> Any:
    try:
        return client.get_json(url)
    except Exception as exc:
        errors.append(f"{label}: {exc}")
        return None


def parse_child_ids(work_item: Dict[str, Any]) -> List[int]:
    child_ids: List[int] = []
    for relation in work_item.get("relations", []) or []:
        if str(relation.get("rel") or "") not in CHILD_RELATION_TYPES:
            continue
        child_id = extract_work_item_id_from_url(str(relation.get("url") or ""))
        if child_id and child_id not in child_ids:
            child_ids.append(child_id)
    return child_ids


def parse_commit_relations(work_item: Dict[str, Any]) -> List[Dict[str, str]]:
    commits: List[Dict[str, str]] = []
    for relation in work_item.get("relations", []) or []:
        name = str((relation.get("attributes") or {}).get("name") or "")
        if "Commit" not in name:
            continue
        match = COMMIT_RE.search(str(relation.get("url") or ""))
        if not match:
            continue
        commits.append(
            {
                "project_id": match.group(1),
                "repository_id": match.group(2),
                "commit": match.group(3),
            }
        )
    return commits


def fetch_work_item(client: TfsClient, work_item_id: int, errors: List[str]) -> Optional[Dict[str, Any]]:
    rows = safe_get_json(
        client,
        client.build_url(f"wit/workItems/{int(work_item_id)}", **{"$expand": "relations"}),
        errors,
        f"work item {work_item_id}",
    )
    if isinstance(rows, dict) and rows.get("id"):
        return rows
    return None


def fetch_comments(client: TfsClient, work_item_id: int, errors: List[str]) -> List[Dict[str, str]]:
    comments: List[Dict[str, str]] = []
    payload = safe_get_json(
        client,
        client.build_url(f"wit/workItems/{int(work_item_id)}/comments", **{"api-version": API_COMMENTS}),
        errors,
        f"work item {work_item_id} comments",
    )
    for comment in (payload or {}).get("comments", []) if isinstance(payload, dict) else []:
        comments.append(
            {
                "by": identity_name((comment.get("createdBy") or {})) or "?",
                "date": str(comment.get("createdDate") or ""),
                "text": html_to_text(comment.get("text") or ""),
                "source": "comment",
            }
        )

    updates = safe_get_json(
        client,
        client.build_url(f"wit/workItems/{int(work_item_id)}/updates"),
        errors,
        f"work item {work_item_id} history",
    )
    for update in (updates or {}).get("value", []) if isinstance(updates, dict) else []:
        history = ((update.get("fields") or {}).get("System.History") or {}).get("newValue")
        if not history:
            continue
        comments.append(
            {
                "by": identity_name(update.get("revisedBy") or {}) or "?",
                "date": str(update.get("revisedDate") or ""),
                "text": html_to_text(history),
                "source": "history",
            }
        )
    comments.sort(key=lambda row: row.get("date", ""))
    return comments


def walk_work_item_tree(client: TfsClient, root_id: int, *, max_items: int, errors: List[str]) -> List[Dict[str, Any]]:
    seen: set[int] = set()
    queue = [int(root_id)]
    items: List[Dict[str, Any]] = []
    while queue and len(items) < max_items:
        work_item_id = queue.pop(0)
        if work_item_id in seen:
            continue
        seen.add(work_item_id)
        work_item = fetch_work_item(client, work_item_id, errors)
        if not work_item:
            continue
        work_item["_children"] = parse_child_ids(work_item)
        work_item["_pull_requests"] = extract_pull_request_relations(client.base_url, work_item.get("relations", []) or [])
        work_item["_commits"] = parse_commit_relations(work_item)
        work_item["_comments"] = fetch_comments(client, work_item_id, errors)
        items.append(work_item)
        for child_id in work_item["_children"]:
            if child_id not in seen and child_id not in queue:
                queue.append(child_id)
    if queue:
        errors.append(f"Work item tree was truncated at {max_items} items.")
    return items


def fetch_pr(client: TfsClient, repository_id: str, pull_request_id: int, errors: List[str]) -> Optional[Dict[str, Any]]:
    payload = safe_get_json(
        client,
        client.build_url(f"git/repositories/{repository_id}/pullRequests/{int(pull_request_id)}"),
        errors,
        f"PR {pull_request_id}",
    )
    return payload if isinstance(payload, dict) and payload.get("pullRequestId") else None


def fetch_pr_commits(client: TfsClient, repository_id: str, pull_request_id: int, errors: List[str]) -> List[Dict[str, Any]]:
    payload = safe_get_json(
        client,
        client.build_url(f"git/repositories/{repository_id}/pullRequests/{int(pull_request_id)}/commits"),
        errors,
        f"PR {pull_request_id} commits",
    )
    return (payload or {}).get("value", []) if isinstance(payload, dict) else []


def fetch_pr_changes(client: TfsClient, repository_id: str, pull_request_id: int, errors: List[str]) -> List[Dict[str, Any]]:
    iterations = safe_get_json(
        client,
        client.build_url(f"git/repositories/{repository_id}/pullRequests/{int(pull_request_id)}/iterations"),
        errors,
        f"PR {pull_request_id} iterations",
    )
    rows = (iterations or {}).get("value", []) if isinstance(iterations, dict) else []
    if not rows:
        return []
    iteration_id = rows[-1].get("id")
    changes = safe_get_json(
        client,
        client.build_url(f"git/repositories/{repository_id}/pullRequests/{int(pull_request_id)}/iterations/{iteration_id}/changes"),
        errors,
        f"PR {pull_request_id} changes",
    )
    if not isinstance(changes, dict):
        return []
    return changes.get("changeEntries", []) or changes.get("value", []) or []


def fetch_pr_threads(client: TfsClient, repository_id: str, pull_request_id: int, errors: List[str]) -> List[Dict[str, str]]:
    payload = safe_get_json(
        client,
        client.build_url(f"git/repositories/{repository_id}/pullRequests/{int(pull_request_id)}/threads", **{"api-version": API_THREADS}),
        errors,
        f"PR {pull_request_id} review threads",
    )
    threads: List[Dict[str, str]] = []
    for thread in (payload or {}).get("value", []) if isinstance(payload, dict) else []:
        if thread.get("isDeleted"):
            continue
        for comment in thread.get("comments", []) or []:
            if comment.get("commentType") == "system" or not str(comment.get("content") or "").strip():
                continue
            threads.append(
                {
                    "by": identity_name(comment.get("author") or {}) or "?",
                    "date": str(comment.get("publishedDate") or ""),
                    "text": str(comment.get("content") or "").strip(),
                }
            )
    return threads


def run_command(command: List[str], *, timeout_seconds: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )


def run_git(path: str, args: List[str], *, distro: str = "", timeout_seconds: int = 120) -> subprocess.CompletedProcess[str]:
    clean_path = str(path or "").strip()
    if os.name == "nt" and clean_path.startswith("/"):
        command = ["wsl.exe"]
        if str(distro or "").strip():
            command.extend(["-d", str(distro).strip()])
        command.extend(["--", "git", "-C", clean_path, *args])
        return run_command(command, timeout_seconds=timeout_seconds)
    return run_command(["git", "-C", clean_path, *args], timeout_seconds=timeout_seconds)


def list_candidate_repo_paths(repo_name: str, *, workspace_path: str, scan_roots: List[str], distro: str) -> List[str]:
    candidates: List[str] = []
    for candidate in [workspace_path, f"/workspaces/{repo_name}"]:
        value = str(candidate or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    for root in scan_roots:
        clean_root = str(root or "").strip().rstrip("/")
        if not clean_root:
            continue
        if os.name == "nt" and clean_root.startswith("/"):
            script = f"find {shlex.quote(clean_root)} -maxdepth 1 -type d -name {shlex.quote(repo_name + '*')} -print 2>/dev/null"
            command = ["wsl.exe"]
            if str(distro or "").strip():
                command.extend(["-d", str(distro).strip()])
            command.extend(["--", "bash", "-lc", script])
            result = run_command(command, timeout_seconds=60)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    value = line.strip()
                    if value and value not in candidates:
                        candidates.append(value)
        else:
            root_path = Path(clean_root)
            if root_path.exists():
                for path in sorted(root_path.glob(f"{repo_name}*")):
                    value = str(path)
                    if value and value not in candidates:
                        candidates.append(value)
    return candidates


def repo_matches(path: str, repo_name: str, *, distro: str) -> bool:
    result = run_git(path, ["remote", "get-url", "origin"], distro=distro, timeout_seconds=30)
    if result.returncode != 0:
        return False
    origin = result.stdout.strip().replace("\\", "/").rstrip("/")
    return origin.lower().endswith(f"/_git/{repo_name}".lower())


def find_local_repo(repo_name: str, *, workspace_path: str, scan_roots: List[str], distro: str) -> Optional[str]:
    for path in list_candidate_repo_paths(repo_name, workspace_path=workspace_path, scan_roots=scan_roots, distro=distro):
        if repo_matches(path, repo_name, distro=distro):
            return path
    return None


def capture_git_diff(repo_name: str, commit: str, *, workspace_path: str, scan_roots: List[str], distro: str) -> tuple[str, str]:
    if not commit:
        return "", "no merge commit was available"
    repo_path = find_local_repo(repo_name, workspace_path=workspace_path, scan_roots=scan_roots, distro=distro)
    if not repo_path:
        return "", f"no local clone was found for repository {repo_name}"

    have = run_git(repo_path, ["cat-file", "-t", commit], distro=distro, timeout_seconds=60)
    if have.returncode != 0:
        run_git(repo_path, ["fetch", "origin", commit], distro=distro, timeout_seconds=180)
        have = run_git(repo_path, ["cat-file", "-t", commit], distro=distro, timeout_seconds=60)
    if have.returncode != 0:
        return "", f"commit {commit[:12]} was not found in local clone {repo_path}"

    show = run_git(repo_path, ["show", "--format=medium", "--no-color", commit], distro=distro, timeout_seconds=180)
    if show.returncode != 0:
        return "", show.stderr.strip() or show.stdout.strip() or f"failed to capture diff from {repo_path}"
    return show.stdout, repo_path


def collect_pull_request_refs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for work_item in items:
        for pr_ref in work_item.get("_pull_requests", []) or []:
            key = (str(pr_ref.get("repository_id") or ""), int(pr_ref.get("id") or 0))
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            refs.append(pr_ref)
    refs.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
    return refs


def render_work_item_markdown(client: TfsClient, work_item: Dict[str, Any]) -> str:
    work_item_id = int(work_item["id"])
    title = str(field(work_item, "System.Title", "(no title)") or "")
    lines = [
        f"# [{field(work_item, 'System.WorkItemType')}] {work_item_id} - {title}",
        "",
        f"- **State:** {field(work_item, 'System.State') or '-'}",
        f"- **Assigned to:** {identity_name(field(work_item, 'System.AssignedTo')) or '-'}",
        f"- **Area / Iteration:** {field(work_item, 'System.AreaPath') or '-'} / {field(work_item, 'System.IterationPath') or '-'}",
        f"- **Tags:** {field(work_item, 'System.Tags') or '-'}",
        f"- **URL:** {build_work_item_web_url(client.base_url, client.project, work_item_id)}",
        "",
    ]
    if work_item.get("_children"):
        lines.append("- **Children:** " + ", ".join(f"#{child_id}" for child_id in work_item["_children"]))
    if work_item.get("_pull_requests"):
        lines.append("- **Pull Requests:** " + ", ".join(f"!{pr['id']}" for pr in work_item["_pull_requests"]))
    if work_item.get("_commits"):
        lines.append("- **Direct commits:** " + ", ".join(f"`{commit['commit'][:12]}`" for commit in work_item["_commits"]))

    lines.extend(["", "## Description", "", html_to_text(field(work_item, "System.Description")) or "_(empty)_", ""])
    acceptance = html_to_text(field(work_item, "Microsoft.VSTS.Common.AcceptanceCriteria"))
    if acceptance:
        lines.extend(["## Acceptance Criteria", "", acceptance, ""])
    repro_steps = html_to_text(field(work_item, "Microsoft.VSTS.TCM.ReproSteps"))
    if repro_steps:
        lines.extend(["## Repro Steps", "", repro_steps, ""])

    lines.extend(["## Comments / Discussion", ""])
    comments = work_item.get("_comments", []) or []
    if not comments:
        lines.append("_(none)_")
    for comment in comments:
        lines.extend(
            [
                f"**{comment['by']}** - {comment['date']} - _{comment['source']}_",
                "",
                comment.get("text") or "_(empty)_",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_pr_files(
    *,
    client: TfsClient,
    pr_ref: Dict[str, Any],
    workspace_path: str,
    scan_roots: List[str],
    distro: str,
    include_diff: bool,
    errors: List[str],
) -> tuple[Dict[str, str], Dict[str, Any]]:
    repository_id = str(pr_ref.get("repository_id") or "")
    pull_request_id = int(pr_ref.get("id") or 0)
    pr = fetch_pr(client, repository_id, pull_request_id, errors)
    if not pr:
        return {}, {"id": pull_request_id, "repository_id": repository_id, "captured": False}

    repository_name = str((pr.get("repository") or {}).get("name") or repository_id)
    commits = fetch_pr_commits(client, repository_id, pull_request_id, errors)
    changes = fetch_pr_changes(client, repository_id, pull_request_id, errors)
    threads = fetch_pr_threads(client, repository_id, pull_request_id, errors)
    merge_commit = str(((pr.get("lastMergeCommit") or {}).get("commitId")) or "")

    pr_dir = f"capture/pullrequests/PR-{pull_request_id}"
    info_lines = [
        f"# PR !{pull_request_id} - {pr.get('title') or ''}",
        "",
        f"- **Repo:** {repository_name}",
        f"- **Status:** {pr.get('status') or '-'}",
        f"- **Author:** {identity_name(pr.get('createdBy') or {}) or '?'}",
        f"- **Source:** `{pr.get('sourceRefName') or '-'}`",
        f"- **Target:** `{pr.get('targetRefName') or '-'}`",
        f"- **Created:** {pr.get('creationDate') or '-'}",
        f"- **Closed:** {pr.get('closedDate') or '-'}",
        f"- **Merge commit:** {merge_commit or '-'}",
        f"- **URL:** {build_pr_web_url(client.base_url, client.project, repository_name, pull_request_id)}",
        "",
        "## Description",
        "",
        html_to_text(pr.get("description")) or "_(empty)_",
        "",
        "## Changed files",
        "",
    ]
    for change in changes:
        item = change.get("item", {}) or {}
        info_lines.append(f"- `[{change.get('changeType') or '?'}]` {item.get('path') or '?'}")
    if threads:
        info_lines.extend(["", "## Review comments", ""])
        for thread in threads:
            info_lines.extend([f"**{thread['by']}** - {thread['date']}", "", thread["text"], ""])

    commit_lines = [f"# PR !{pull_request_id} - commits", ""]
    for commit in commits:
        message = str(commit.get("comment") or "").splitlines()[0] if commit.get("comment") else ""
        commit_lines.append(f"- `{str(commit.get('commitId') or '')[:12]}` {message}")

    diff_text = ""
    diff_source = ""
    if include_diff:
        diff_text, diff_source = capture_git_diff(
            repository_name,
            merge_commit,
            workspace_path=workspace_path,
            scan_roots=scan_roots,
            distro=distro,
        )
    if not diff_text:
        diff_text = f"# diff unavailable: {diff_source or 'diff capture disabled'}\n"

    files = {
        f"{pr_dir}/info.md": "\n".join(info_lines).rstrip() + "\n",
        f"{pr_dir}/commits.md": "\n".join(commit_lines).rstrip() + "\n",
        f"{pr_dir}/diff.patch": diff_text,
    }
    summary = {
        "id": pull_request_id,
        "title": pr.get("title") or "",
        "repo": repository_name,
        "status": pr.get("status") or "",
        "merge": merge_commit,
        "source": clean_branch(str(pr.get("sourceRefName") or "")),
        "target": clean_branch(str(pr.get("targetRefName") or "")),
        "changed_files": len(changes),
        "commits": len(commits),
        "review_comments": len(threads),
        "diff_captured": bool(include_diff and diff_source and not diff_text.startswith("# diff unavailable")),
        "diff_source": diff_source,
        "file": f"pullrequests/PR-{pull_request_id}/info.md",
    }
    return files, summary


def doc_tasks(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        item
        for item in items
        if str(field(item, "System.WorkItemType") or "").strip().lower() == "task"
        and re.match(r"\s*doc\b", str(field(item, "System.Title") or ""), re.IGNORECASE)
    ]


def is_breaking_change(items: List[Dict[str, Any]]) -> bool:
    return any("breaking change" in str(field(item, "System.Tags") or "").lower() for item in items)


def release_info(pr_summaries: List[Dict[str, Any]]) -> Dict[str, str]:
    chosen = next((row for row in pr_summaries if str(row.get("status") or "").lower() == "completed"), None)
    chosen = chosen or (pr_summaries[0] if pr_summaries else None)
    target = str((chosen or {}).get("target") or "")
    match = re.match(r"(\d+)\.(\d+)", target)
    if not match:
        return {"version": "", "prefix": "", "dir_code": "", "target": target}
    major, minor = match.group(1), match.group(2)
    return {
        "version": f"{major}.{minor}",
        "prefix": f"{major}.{minor}",
        "dir_code": f"{major}{minor}0",
        "target": target,
    }


def render_summary(client: TfsClient, root: Dict[str, Any], items: List[Dict[str, Any]], item_files: Dict[int, str], pr_summaries: List[Dict[str, Any]], errors: List[str]) -> str:
    root_id = int(root["id"])
    lines = [
        f"# Capture: {field(root, 'System.WorkItemType')} #{root_id} - {field(root, 'System.Title')}",
        "",
        f"Captured {len(items)} work item(s) and {len(pr_summaries)} pull request(s).",
        "",
        "> Start with `INSTRUCTIONS.md`, then review the work item tree and pull request evidence below.",
        "",
        "## Work item tree",
        "",
    ]
    by_id = {int(item["id"]): item for item in items}

    def render_tree(work_item_id: int, depth: int) -> None:
        item = by_id.get(work_item_id)
        if not item:
            return
        prefix = "  " * depth
        pr_label = ""
        if item.get("_pull_requests"):
            pr_label = " - PRs: " + ", ".join("!" + str(pr["id"]) for pr in item["_pull_requests"])
        lines.append(
            f"{prefix}- [{field(item, 'System.WorkItemType')} #{work_item_id}]({item_files[work_item_id]}) "
            f"- {field(item, 'System.Title')} _({field(item, 'System.State')})_{pr_label}"
        )
        for child_id in item.get("_children", []) or []:
            render_tree(int(child_id), depth + 1)

    render_tree(root_id, 0)
    lines.extend(["", "## Pull requests", ""])
    if not pr_summaries:
        lines.append("No pull requests were linked to the captured work item tree.")
    for pr_summary in pr_summaries:
        warning = "" if pr_summary.get("diff_captured") else " - diff not captured"
        lines.append(
            f"- [!{pr_summary['id']}]({pr_summary['file']}) - {pr_summary.get('title') or ''} "
            f"_({pr_summary.get('status') or '-'}, {pr_summary.get('changed_files', 0)} files)_{warning}"
        )
    if errors:
        lines.extend(["", "## Capture warnings", ""])
        for error in errors:
            lines.append(f"- {error}")
    return "\n".join(lines).rstrip() + "\n"


def render_instructions(portal: Dict[str, Any], root: Dict[str, Any], items: List[Dict[str, Any]], pr_summaries: List[Dict[str, Any]]) -> str:
    root_id = int(root["id"])
    root_title = str(field(root, "System.Title") or "")
    release = release_info(pr_summaries)
    version = release.get("version") or "(unknown)"
    branch_prefix = release.get("prefix") or "<version>"
    breaking = is_breaking_change(items)
    tasks = doc_tasks(items)
    suggested_branch = f"{branch_prefix}/feature/{root_id}-{slug(root_title, 40)}"
    target_repository = str(portal.get("repository") or "-")
    target_workspace = str(portal.get("copilot_workspace_path") or "-")

    lines = [
        "# Instructions - captured implementation context",
        "",
        "You are working from a captured Azure DevOps/TFS work item tree and its linked implementation evidence.",
        "Use this capture to decide the smallest accurate change. Do not assume the selected task text is the whole truth.",
        "",
        "## What this change is",
        "",
        f"- **Root:** {field(root, 'System.WorkItemType')} #{root_id} - {root_title}",
        f"- **Release / target branch:** {version}" + (f" (PR target `{release.get('target')}`)" if release.get("target") else ""),
        f"- **Breaking change:** {'YES - migration or upgrade guidance may be required.' if breaking else 'not tagged as breaking.'}",
        f"- **Primary target repository:** {target_repository}",
        f"- **Primary workspace:** `{target_workspace}`",
        "",
        "## Documentation or content tasks",
        "",
    ]
    if tasks:
        lines.append("Treat DOC task descriptions as direct requirements, then verify them against the PR diffs and comments.")
        lines.append("")
        for task in tasks:
            description = html_to_text(field(task, "System.Description")) or "_(no description)_"
            lines.extend(
                [
                    f"### Task #{task['id']} - {field(task, 'System.Title')} _({field(task, 'System.State')})_",
                    "",
                    description,
                    "",
                ]
            )
    else:
        lines.append("No task whose title starts with `DOC` was found. Derive scope from the root item, child items, comments, and PR diffs.")
        lines.append("")

    lines.extend(
        [
            "## Required workflow",
            "",
            "1. Read `summary.md`.",
            "2. Read the relevant files under `workitems/`.",
            "3. Read each linked PR `info.md` and `diff.patch` when available.",
            "4. Decide whether a change is needed before editing.",
            "5. If no change is needed, write `agent-result.json` with `green_light=false` and a concise `no_change_reason` in `reviewer_notes`.",
            "6. If a change is needed, edit only the configured workspace branch and keep the change focused.",
            "7. In `agent-result.json`, list the capture files read in `capture_files_read`, PRs reviewed in `prs_reviewed`, diffs reviewed in `diffs_reviewed`, and work items reviewed in `work_items_reviewed`.",
            "",
            "## Branch guidance",
            "",
            "The dashboard owns branch creation and PR creation. If you need to refer to a feature branch convention, use the root work item id rather than a child task id.",
            "",
            "```text",
            suggested_branch,
            "```",
            "",
            "## Checklist",
            "",
            "- [ ] Read `summary.md` and this `INSTRUCTIONS.md`.",
            "- [ ] Reviewed linked PR diffs or explicitly noted why a diff was unavailable.",
            "- [ ] Checked DOC task requirements when present.",
            "- [ ] Followed repository `AGENTS.md`, `.github/copilot-instructions.md`, and `.agents` materials.",
            "- [ ] Changed only files that are needed for the selected work item.",
            "- [ ] Reported evidence used in `agent-result.json`.",
            "",
            "_Generated by the TFS Autonomous Pipeline context capture engine._",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_capture_error_package(item: Dict[str, Any], error: str) -> Dict[str, str]:
    work_item_id = int(item.get("id") or 0)
    summary = [
        f"# Capture unavailable for work item {work_item_id}",
        "",
        "The rich work item tree capture could not be generated.",
        "",
        "## Error",
        "",
        str(error or "Unknown capture error."),
        "",
        "Continue with the base `work-item.md` package, but record the missing capture in reviewer notes.",
        "",
    ]
    manifest = {
        "root": work_item_id,
        "status": "unavailable",
        "error": str(error or "Unknown capture error."),
        "work_items": [],
        "pull_requests": [],
    }
    return {
        "capture/summary.md": "\n".join(summary),
        "capture/INSTRUCTIONS.md": "\n".join(summary),
        "capture/manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    }


def build_context_capture_package(
    *,
    client: TfsClient,
    item: Dict[str, Any],
    portal: Dict[str, Any],
    workspace_path: str,
    distro: str,
    root_mode: str = "parent",
    include_pr_diffs: bool = True,
    max_tree_items: int = 50,
    workspace_scan_roots: Optional[List[str]] = None,
) -> Dict[str, str]:
    use_parent = str(root_mode or "parent").strip().lower() == "parent"
    root_id = int((item.get("parent_id") if use_parent else None) or item.get("id") or 0)
    if not root_id:
        return build_capture_error_package(item, "No root work item id was available for capture.")

    errors: List[str] = []
    scan_roots = workspace_scan_roots or ["/workspaces"]
    items = walk_work_item_tree(client, root_id, max_items=max_tree_items, errors=errors)
    if not items:
        return build_capture_error_package(item, f"Root work item {root_id} could not be loaded.")

    files: Dict[str, str] = {}
    item_files: Dict[int, str] = {}
    for work_item in items:
        work_item_id = int(work_item["id"])
        relative_path = f"capture/workitems/WI-{work_item_id}-{slug(field(work_item, 'System.Title'))}.md"
        item_files[work_item_id] = relative_path.replace("capture/", "")
        files[relative_path] = render_work_item_markdown(client, work_item)

    pr_summaries: List[Dict[str, Any]] = []
    for pr_ref in collect_pull_request_refs(items):
        pr_files, pr_summary = render_pr_files(
            client=client,
            pr_ref=pr_ref,
            workspace_path=workspace_path,
            scan_roots=scan_roots,
            distro=distro,
            include_diff=include_pr_diffs,
            errors=errors,
        )
        files.update(pr_files)
        if pr_summary:
            pr_summaries.append(pr_summary)

    root = items[0]
    files["capture/summary.md"] = render_summary(client, root, items, item_files, pr_summaries, errors)
    files["capture/INSTRUCTIONS.md"] = render_instructions(portal, root, items, pr_summaries)
    files["capture/manifest.json"] = json.dumps(
        {
            "root": root_id,
            "status": "captured",
            "collection": client.base_url,
            "project": client.project,
            "target_repository": portal.get("repository"),
            "work_items": [
                {
                    "id": int(work_item["id"]),
                    "type": field(work_item, "System.WorkItemType"),
                    "title": field(work_item, "System.Title"),
                    "state": field(work_item, "System.State"),
                    "children": work_item.get("_children", []),
                    "pull_requests": [pr.get("id") for pr in work_item.get("_pull_requests", []) or []],
                    "commits": [commit.get("commit") for commit in work_item.get("_commits", []) or []],
                    "file": item_files[int(work_item["id"])],
                }
                for work_item in items
            ],
            "pull_requests": pr_summaries,
            "errors": errors,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    return files
