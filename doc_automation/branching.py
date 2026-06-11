from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Optional


VERSION_PATTERN = re.compile(r"(?<!\d)(\d+)\.(\d+)(?:\.\d+)?(?!\d)")
BUG_TYPES = {"bug"}
FEATURE_TYPES = {"user story", "product backlog item", "feature", "product backlog item (pbi)"}
WORK_TYPES = ["fix", "feature", "task"]


def parse_tags(value: str) -> List[str]:
    return [tag.strip() for tag in value.split(";") if tag.strip()]


def extract_version_candidates(*values: str) -> List[str]:
    candidates: List[str] = []
    for value in values:
        if not value:
            continue
        for match in VERSION_PATTERN.finditer(value):
            token = f"{match.group(1)}.{match.group(2)}"
            if token not in candidates:
                candidates.append(token)
    return candidates


def infer_base_branch(tags: str, title: str, iteration_path: str, branch_chain: Iterable[str]) -> str:
    candidates = extract_version_candidates(tags, title, iteration_path)
    normalized_chain = [branch.replace("refs/heads/", "").strip() for branch in branch_chain]
    for candidate in candidates:
        expected = f"{candidate}/dev"
        for branch in normalized_chain:
            if branch.lower() == expected.lower():
                return branch
    return ""


def infer_work_type(work_item_type: str, parent_type: str) -> str:
    current = (work_item_type or "").strip().lower()
    parent = (parent_type or "").strip().lower()
    if current in BUG_TYPES or parent in BUG_TYPES:
        return "fix"
    if current in FEATURE_TYPES or parent in FEATURE_TYPES:
        return "feature"
    return "task"


def version_prefix_from_branch(base_branch: str) -> str:
    normalized = base_branch.replace("refs/heads/", "").strip("/")
    if not normalized:
        return ""
    return normalized.split("/")[0]


def slugify(value: str, max_length: int = 72) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        return "work-item"
    if len(slug) <= max_length:
        return slug
    trimmed = slug[:max_length].rstrip("-")
    return trimmed or "work-item"


def build_work_branch(base_branch: str, work_type: str, work_item_id: int, title: str) -> str:
    prefix = version_prefix_from_branch(base_branch)
    work_segment = work_type.strip().lower() if work_type.strip().lower() in WORK_TYPES else "task"
    title_slug = slugify(title)
    if prefix:
        return f"{prefix}/{work_segment}/{work_item_id}-{title_slug}"
    return f"{work_segment}/{work_item_id}-{title_slug}"


def normalize_branch_name(value: str) -> str:
    branch_name = str(value or "").replace("refs/heads/", "").replace("\\", "/").strip()
    branch_name = re.sub(r"\s+", "-", branch_name)
    branch_name = re.sub(r"/{2,}", "/", branch_name)
    return branch_name.strip("/")


def merge_branch_plan(
    work_item: Dict[str, object],
    branch_chain: Iterable[str],
    stored_state: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    state = stored_state or {}
    inferred_base_branch = infer_base_branch(
        str(work_item.get("tags", "")),
        str(work_item.get("title", "")),
        str(work_item.get("iteration_path", "")),
        branch_chain,
    )
    inferred_work_type = infer_work_type(
        str(work_item.get("type", "")),
        str(work_item.get("parent_type", "")),
    )

    selected_base_branch = str(state.get("selected_base_branch") or inferred_base_branch or "")
    selected_work_type = str(state.get("work_type") or inferred_work_type or "task")
    generated_branch_name = build_work_branch(
        selected_base_branch,
        selected_work_type,
        int(work_item["id"]),
        str(work_item.get("title", "")),
    )
    branch_name = normalize_branch_name(str(state.get("branch_name") or "")) or generated_branch_name

    return {
        "triage_status": str(state.get("triage_status") or "pending"),
        "inferred_base_branch": inferred_base_branch,
        "selected_base_branch": selected_base_branch,
        "inferred_work_type": inferred_work_type,
        "selected_work_type": selected_work_type,
        "generated_branch_name": generated_branch_name,
        "branch_name": branch_name,
    }
