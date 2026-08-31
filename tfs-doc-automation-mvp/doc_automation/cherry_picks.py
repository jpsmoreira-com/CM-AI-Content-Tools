from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from .tfs_client import (
    TfsApiError,
    TfsClient,
    branch_ref,
    build_pr_web_url,
    build_work_item_web_url,
    clean_branch,
)


STATUS_PRIORITY = {"completed": 3, "active": 2, "abandoned": 1}
STATUS_LABELS = {
    "completed": "Done",
    "active": "Open",
    "abandoned": "Abandoned",
    "missing": "Missing",
}
STATUS_FILTERS = ["All", "Missing", "Open", "Done"]
SCOPE_FILTERS = ["All", "Mine"]
SORT_OPTIONS = [
    "Severity",
    "Original Branch",
    "Work Item",
    "Original Author",
    "Title",
    "Missing Targets",
    "Open PRs",
    "Original PR Created",
]
DEFAULT_SKIP_LABELS = ["No CP", "no-cp", "not to cp"]


def branch_token(value: str) -> str:
    return clean_branch(value).replace("/", "-")


def normalize_label_token(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[\s_-]+", " ", ascii_text).strip()


def parse_label_names(value: Any) -> List[str]:
    labels: List[str] = []
    raw_items = value if isinstance(value, list) else []
    for item in raw_items:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("labelName") or item.get("value") or "").strip()
        else:
            name = str(item or "").strip()
        if name and name not in labels:
            labels.append(name)
    return labels


def extract_pull_request_label_names(pull_request: Dict[str, Any]) -> List[str]:
    labels: List[str] = []
    for key in ("labels", "tags"):
        for name in parse_label_names(pull_request.get(key)):
            if name not in labels:
                labels.append(name)
    return labels


def pull_request_is_abandoned(pull_request: Dict[str, Any]) -> bool:
    return str(pull_request.get("status") or "").strip().lower() == "abandoned"


def extract_work_item_ids_from_branch(branch_name: str) -> List[int]:
    match = re.search(r"/(\d+)(?:-|$)", branch_name)
    if match:
        return [int(match.group(1))]
    return []


def normalize_source_branch(source_branch: str, branch_chain: List[str]) -> str:
    normalized = clean_branch(source_branch)
    for branch in branch_chain:
        suffix = f"-on-{branch_token(branch)}"
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_date(value: Optional[str]) -> str:
    parsed = parse_iso_date(value)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M") if parsed else "-"


def severity_rank(status: str) -> int:
    return {"Missing": 0, "Open": 1, "Abandoned": 2, "Done": 3}.get(status, 9)


def best_pr(pull_requests: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pull_requests:
        return None

    def sort_key(pull_request: Dict[str, Any]) -> tuple[int, float]:
        date_value = parse_iso_date(pull_request.get("closed_date") or pull_request.get("creation_date"))
        stamp = date_value.timestamp() if date_value else 0
        return (STATUS_PRIORITY.get(pull_request.get("status", ""), 0), stamp)

    return max(pull_requests, key=sort_key)


def summarize_target(pull_requests: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not pull_requests:
        return {"label": "Missing", "prs": [], "best_pr": None}
    selected = best_pr(pull_requests)
    label = STATUS_LABELS.get(selected.get("status", ""), "Missing") if selected else "Missing"
    return {"label": label, "prs": pull_requests, "best_pr": selected}


def short_source_label(source_branch: str) -> str:
    return clean_branch(source_branch).split("/")[-1]


def pull_request_time_range_types(min_time: Optional[str]) -> List[Optional[str]]:
    return ["created", "closed"] if min_time else [None]


def canonical_identity_token(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def identity_property_value(properties: Dict[str, Any], key: str) -> str:
    value = properties.get(key)
    if isinstance(value, dict):
        return str(value.get("$value") or "")
    return str(value or "")


def identity_tokens(value: Any) -> Set[str]:
    tokens: Set[str] = set()
    if isinstance(value, dict):
        for key in (
            "id",
            "descriptor",
            "subjectDescriptor",
            "displayName",
            "providerDisplayName",
            "uniqueName",
            "mail",
            "principalName",
        ):
            token = canonical_identity_token(value.get(key))
            if token:
                tokens.add(token)

        properties = value.get("properties")
        if isinstance(properties, dict):
            for key in ("Account", "Domain", "Mail", "DisplayName"):
                token = canonical_identity_token(identity_property_value(properties, key))
                if token:
                    tokens.add(token)
    else:
        token = canonical_identity_token(value)
        if token:
            tokens.add(token)
    return tokens


def normalize_identity(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("displayName") or value.get("providerDisplayName") or value.get("uniqueName") or ""
    return str(value or "")


def get_json_many(client: TfsClient, urls: List[str]) -> Dict[str, Any]:
    payloads: Dict[str, Any] = {}
    for url in urls:
        payloads[url] = client.get_json(url)
    return payloads


def pull_request_labels_from_api(client: TfsClient, repository_id: str, pull_request_ids: List[int]) -> Dict[int, List[str]]:
    labels_by_pr: Dict[int, List[str]] = {}
    for pull_request_id in pull_request_ids:
        url = client.build_url(f"git/repositories/{repository_id}/pullRequests/{pull_request_id}/labels")
        try:
            payload = client.get_json(url) or {}
        except TfsApiError:
            break
        rows = payload.get("value", []) if isinstance(payload, dict) else payload
        labels_by_pr[pull_request_id] = parse_label_names(rows)
    return labels_by_pr


def get_current_user(client: TfsClient) -> Dict[str, Any]:
    url = f"{client.base_url}/_apis/connectionData?api-version=1.0"
    payload = client.get_json(url) or {}
    user = payload.get("authenticatedUser", {}) if isinstance(payload, dict) else {}
    return {
        "display_name": normalize_identity(user),
        "tokens": sorted(identity_tokens(user)),
    }


def work_item_url(client: TfsClient, work_item_id: int) -> str:
    return build_work_item_web_url(client.base_url, client.project, int(work_item_id))


def _enrich_pull_requests(
    *,
    client: TfsClient,
    repository_id: str,
    repository_name: str,
    branch_chain: List[str],
    pull_requests: Dict[int, Dict[str, Any]],
    verify_work_items_via_api: bool,
    skip_labels: List[str],
) -> List[Dict[str, Any]]:
    skip_label_tokens = {normalize_label_token(label) for label in skip_labels if normalize_label_token(label)}
    labels_by_pr: Dict[int, List[str]] = {
        pull_request_id: extract_pull_request_label_names(pull_request)
        for pull_request_id, pull_request in pull_requests.items()
    }
    if skip_label_tokens and not any(labels_by_pr.values()):
        labels_by_pr.update(pull_request_labels_from_api(client, repository_id, sorted(pull_requests)))

    work_item_url_map: Dict[int, str] = {}
    if verify_work_items_via_api:
        for pull_request_id in pull_requests:
            work_item_url_map[pull_request_id] = client.build_url(
                f"git/repositories/{repository_id}/pullRequests/{pull_request_id}/workitems"
            )
    work_item_payloads = get_json_many(client, list(work_item_url_map.values())) if work_item_url_map else {}

    enriched: List[Dict[str, Any]] = []
    for pull_request_id, pull_request in pull_requests.items():
        source_branch = clean_branch(pull_request.get("sourceRefName", ""))
        normalized_source = normalize_source_branch(source_branch, branch_chain)
        label_names = labels_by_pr.get(pull_request_id) or []
        label_tokens = {normalize_label_token(label) for label in label_names if normalize_label_token(label)}
        is_skipped = bool(skip_label_tokens and label_tokens.intersection(skip_label_tokens))
        work_item_ids: List[int] = []
        if verify_work_items_via_api and pull_request_id in work_item_url_map:
            payload = work_item_payloads.get(work_item_url_map[pull_request_id]) or {}
            rows = payload.get("value", []) if isinstance(payload, dict) else payload
            parsed_ids: List[int] = []
            for item in rows:
                try:
                    parsed_ids.append(int(item.get("id")))
                except (TypeError, ValueError):
                    continue
            work_item_ids = sorted(set(parsed_ids))
        elif not verify_work_items_via_api:
            work_item_ids = extract_work_item_ids_from_branch(normalized_source) or extract_work_item_ids_from_branch(source_branch)

        enriched.append(
            {
                "pull_request_id": int(pull_request["pullRequestId"]),
                "title": pull_request.get("title", ""),
                "status": str(pull_request.get("status", "")).lower(),
                "creation_date": pull_request.get("creationDate"),
                "creation_date_label": fmt_date(pull_request.get("creationDate")),
                "closed_date": pull_request.get("closedDate"),
                "created_by": normalize_identity(pull_request.get("createdBy")),
                "created_by_tokens": sorted(identity_tokens(pull_request.get("createdBy"))),
                "source_branch": source_branch,
                "target_branch": clean_branch(pull_request.get("targetRefName", "")),
                "normalized_source_branch": normalized_source,
                "work_item_ids": work_item_ids,
                "labels": label_names,
                "is_cherry_pick_skipped": is_skipped,
                "url": pull_request.get("_links", {}).get("web", {}).get("href")
                or build_pr_web_url(client.base_url, client.project, repository_name, int(pull_request["pullRequestId"])),
            }
        )
    return enriched


def fetch_cherry_pick_rows(
    *,
    client: TfsClient,
    branch_chain: List[str],
    lookback_days: int,
    max_prs_per_branch: int,
    verify_work_items_via_api: bool,
    skip_labels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if len(branch_chain) < 2:
        raise TfsApiError("Configure at least two branches in the branch chain before loading Cherry Pick analysis.")

    repository = client.resolve_repository()
    repository_id = str(repository["id"])
    repository_name = str(repository.get("name") or client.repository)
    try:
        current_user = get_current_user(client)
    except Exception as exc:
        current_user = {"display_name": "", "tokens": [], "error": str(exc)}

    min_time = (
        (datetime.now(timezone.utc) - timedelta(days=max(0, int(lookback_days)))).strftime("%Y-%m-%dT%H:%M:%SZ")
        if int(lookback_days) > 0
        else None
    )
    pull_request_urls: List[str] = []
    branch_page_urls: Dict[str, List[List[str]]] = {}
    max_items = max(1, int(max_prs_per_branch))
    for branch in branch_chain:
        branch_page_urls[branch] = []
        for query_time_range_type in pull_request_time_range_types(min_time):
            page_urls: List[str] = []
            remaining = max_items
            skip = 0
            while remaining > 0:
                top = min(remaining, 100)
                url = client.build_url(
                    f"git/repositories/{repository_id}/pullrequests",
                    **{
                        "searchCriteria.status": "all",
                        "searchCriteria.targetRefName": branch_ref(branch),
                        "searchCriteria.minTime": min_time,
                        "searchCriteria.queryTimeRangeType": query_time_range_type,
                        "$top": top,
                        "$skip": skip,
                    },
                )
                page_urls.append(url)
                pull_request_urls.append(url)
                remaining -= top
                skip += top
            branch_page_urls[branch].append(page_urls)

    pull_request_payloads = get_json_many(client, pull_request_urls)
    all_pull_requests: List[Dict[str, Any]] = []
    for branch in branch_chain:
        branch_rows_by_id: Dict[int, Dict[str, Any]] = {}
        for page_urls in branch_page_urls[branch]:
            for url in page_urls:
                payload = pull_request_payloads.get(url) or {}
                batch = payload.get("value", []) if isinstance(payload, dict) else payload
                if not batch:
                    break
                for pull_request in batch:
                    branch_rows_by_id[int(pull_request["pullRequestId"])] = pull_request
                if len(batch) < 100:
                    break
        all_pull_requests.extend(list(branch_rows_by_id.values())[:max_items])

    deduped = {int(pull_request["pullRequestId"]): pull_request for pull_request in all_pull_requests}
    enriched = _enrich_pull_requests(
        client=client,
        repository_id=repository_id,
        repository_name=repository_name,
        branch_chain=branch_chain,
        pull_requests=deduped,
        verify_work_items_via_api=verify_work_items_via_api,
        skip_labels=skip_labels or DEFAULT_SKIP_LABELS,
    )
    ignored_label_pull_requests = [
        pull_request
        for pull_request in enriched
        if pull_request.get("is_cherry_pick_skipped")
    ]
    ignored_abandoned_pull_requests = [
        pull_request
        for pull_request in enriched
        if pull_request_is_abandoned(pull_request)
    ]
    ignored_by_id = {
        int(pull_request["pull_request_id"]): pull_request
        for pull_request in [*ignored_label_pull_requests, *ignored_abandoned_pull_requests]
    }
    enriched_for_analysis = [
        pull_request
        for pull_request in enriched
        if int(pull_request["pull_request_id"]) not in ignored_by_id
    ]

    branch_index = {branch: index for index, branch in enumerate(branch_chain)}
    groups: Dict[str, Dict[str, Any]] = {}
    for pull_request in enriched_for_analysis:
        if pull_request["target_branch"] not in branch_index:
            continue
        if pull_request["work_item_ids"]:
            tracking_keys = [
                (f"wi:{work_item_id}", [work_item_id], "work_item")
                for work_item_id in pull_request["work_item_ids"]
            ]
        else:
            tracking_keys = [
                (
                    f"branch:{pull_request['normalized_source_branch']}",
                    [],
                    "branch_fallback",
                )
            ]

        for key, work_item_ids, tracking_mode in tracking_keys:
            group = groups.setdefault(
                key,
                {
                    "key": key,
                    "work_item_ids": work_item_ids,
                    "tracking_mode": tracking_mode,
                    "prs": [],
                },
            )
            group["prs"].append(pull_request)

    rows: List[Dict[str, Any]] = []
    for group in groups.values():
        pull_requests = group["prs"]
        if not pull_requests:
            continue

        original_candidates = [
            pull_request
            for pull_request in pull_requests
            if pull_request["source_branch"] == pull_request["normalized_source_branch"]
            and pull_request["target_branch"] in branch_index
        ]
        if original_candidates:
            original = min(
                original_candidates,
                key=lambda pull_request: (
                    branch_index.get(pull_request["target_branch"], 999),
                    -STATUS_PRIORITY.get(pull_request["status"], 0),
                    parse_iso_date(pull_request.get("creation_date")).timestamp()
                    if parse_iso_date(pull_request.get("creation_date"))
                    else 0,
                ),
            )
        else:
            original = min(
                pull_requests,
                key=lambda pull_request: (
                    branch_index.get(pull_request["target_branch"], 999),
                    parse_iso_date(pull_request.get("creation_date")).timestamp()
                    if parse_iso_date(pull_request.get("creation_date"))
                    else 0,
                ),
            )

        original_target = original["target_branch"]
        original_index = branch_index.get(original_target)
        if original_index is None:
            continue

        expected_branches = branch_chain[original_index + 1 :]
        statuses: Dict[str, Dict[str, Any]] = {}
        for target in expected_branches:
            target_pull_requests = [
                pull_request
                for pull_request in pull_requests
                if pull_request["target_branch"] == target
            ]
            summary = summarize_target(target_pull_requests)
            best = summary["best_pr"]
            if best and not best.get("url"):
                best["url"] = build_pr_web_url(client.base_url, client.project, repository_name, best["pull_request_id"])
            statuses[target] = summary

        missing = [branch for branch, summary in statuses.items() if summary["label"] == "Missing"]
        open_branches = [branch for branch, summary in statuses.items() if summary["label"] == "Open"]
        abandoned = [branch for branch, summary in statuses.items() if summary["label"] == "Abandoned"]
        done = [branch for branch, summary in statuses.items() if summary["label"] == "Done"]

        if missing:
            overall = "Missing"
        elif open_branches:
            overall = "Open"
        elif abandoned:
            overall = "Abandoned"
        else:
            overall = "Done"

        rows.append(
            {
                "key": group["key"],
                "work_items": group["work_item_ids"],
                "work_item_links": [
                    {
                        "id": work_item_id,
                        "url": work_item_url(client, int(work_item_id)),
                    }
                    for work_item_id in group["work_item_ids"]
                ],
                "work_items_label": ", ".join(str(item) for item in group["work_item_ids"]) or "-",
                "family_branch": original["normalized_source_branch"],
                "family_label": short_source_label(original["normalized_source_branch"]),
                "tracking_mode": group["tracking_mode"],
                "title": original.get("title", ""),
                "original_pr": original,
                "original_created_by": original.get("created_by", ""),
                "original_created_by_tokens": original.get("created_by_tokens", []),
                "original_pr_url": original.get("url")
                or build_pr_web_url(client.base_url, client.project, repository_name, original["pull_request_id"]),
                "original_target": original_target,
                "original_status_label": STATUS_LABELS.get(original.get("status", ""), original.get("status", "-").title()),
                "expected_branches": expected_branches,
                "statuses": statuses,
                "missing": missing,
                "open": open_branches,
                "abandoned": abandoned,
                "done": done,
                "overall": overall,
                "original_created_at_label": fmt_date(original.get("creation_date")),
            }
        )

    rows.sort(key=lambda row: (severity_rank(row["overall"]), branch_index.get(row["original_target"], 999), row["work_items_label"], row["family_branch"]))

    return {
        "repository_id": repository_id,
        "repository_name": repository_name,
        "rows": rows,
        "pull_request_count": len(enriched_for_analysis),
        "ignored_pull_request_count": len(ignored_by_id),
        "ignored_label_pull_request_count": len(ignored_label_pull_requests),
        "ignored_abandoned_pull_request_count": len(ignored_abandoned_pull_requests),
        "ignored_pull_requests": list(ignored_by_id.values()),
        "ignored_label_pull_requests": ignored_label_pull_requests,
        "ignored_abandoned_pull_requests": ignored_abandoned_pull_requests,
        "current_user": current_user,
    }


def row_opened_by_current_user(row: Dict[str, Any], current_user: Dict[str, Any]) -> bool:
    current_tokens = set(current_user.get("tokens") or [])
    row_tokens = set(row.get("original_created_by_tokens") or [])
    return bool(current_tokens and row_tokens and current_tokens.intersection(row_tokens))


def parse_sort_timestamp(value: Any) -> float:
    parsed = parse_iso_date(str(value or ""))
    return parsed.timestamp() if parsed else 0.0


def first_work_item_id(row: Dict[str, Any]) -> int:
    values = row.get("work_items") or []
    return int(values[0]) if values else 999999999


def dashboard_sort_key(row: Dict[str, Any], sort_by: str, branch_chain: List[str]) -> Any:
    branch_index = {branch: index for index, branch in enumerate(branch_chain)}
    if sort_by == "Original Branch":
        return (branch_index.get(row.get("original_target"), 999), row.get("family_branch", ""))
    if sort_by == "Work Item":
        return (first_work_item_id(row), row.get("family_branch", ""))
    if sort_by == "Original Author":
        return (str(row.get("original_created_by") or "").casefold(), row.get("family_branch", ""))
    if sort_by == "Title":
        return (str(row.get("title") or "").casefold(), row.get("family_branch", ""))
    if sort_by == "Missing Targets":
        return (-len(row.get("missing") or []), row.get("family_branch", ""))
    if sort_by == "Open PRs":
        return (-len(row.get("open") or []), row.get("family_branch", ""))
    if sort_by == "Original PR Created":
        return (-parse_sort_timestamp((row.get("original_pr") or {}).get("creation_date")), row.get("family_branch", ""))
    return (severity_rank(row.get("overall", "")), branch_index.get(row.get("original_target"), 999), row.get("family_branch", ""))


def filter_and_sort_rows(
    *,
    rows: List[Dict[str, Any]],
    branch_chain: List[str],
    current_user: Dict[str, Any],
    scope: str,
    status: str,
    branch: str,
    sort_by: str,
    descending: bool,
) -> List[Dict[str, Any]]:
    filtered = list(rows)
    if scope == "Mine":
        filtered = [row for row in filtered if row_opened_by_current_user(row, current_user)]
    if status in STATUS_FILTERS and status != "All":
        filtered = [row for row in filtered if row.get("overall") == status]
    if branch and branch != "All":
        filtered = [
            row
            for row in filtered
            if row.get("original_target") == branch or branch in list(row.get("expected_branches") or [])
        ]
    filtered.sort(key=lambda row: dashboard_sort_key(row, sort_by, branch_chain), reverse=bool(descending))
    return filtered


def summarize_rows(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "total": len(rows),
        "missing": sum(1 for row in rows if row.get("overall") == "Missing"),
        "open": sum(1 for row in rows if row.get("overall") == "Open"),
        "abandoned": sum(1 for row in rows if row.get("overall") == "Abandoned"),
        "done": sum(1 for row in rows if row.get("overall") == "Done"),
    }


def build_cherry_pick_context(
    *,
    client: TfsClient,
    branch_chain: List[str],
    lookback_days: int,
    max_prs_per_branch: int,
    verify_work_items_via_api: bool,
    scope: str,
    status: str,
    branch: str,
    sort_by: str,
    descending: bool,
    skip_labels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    raw = fetch_cherry_pick_rows(
        client=client,
        branch_chain=branch_chain,
        lookback_days=lookback_days,
        max_prs_per_branch=max_prs_per_branch,
        verify_work_items_via_api=verify_work_items_via_api,
        skip_labels=skip_labels or DEFAULT_SKIP_LABELS,
    )
    rows = filter_and_sort_rows(
        rows=list(raw.get("rows") or []),
        branch_chain=branch_chain,
        current_user=dict(raw.get("current_user") or {}),
        scope=scope,
        status=status,
        branch=branch,
        sort_by=sort_by,
        descending=descending,
    )
    return {
        **raw,
        "rows": rows,
        "summary": summarize_rows(rows),
        "unfiltered_summary": summarize_rows(list(raw.get("rows") or [])),
    }
