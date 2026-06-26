from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, unquote, urlencode, urlparse

import requests
try:
    from requests_ntlm import HttpNtlmAuth
except ImportError:  # pragma: no cover - exercised only when optional devcontainer auth dependency is absent.
    HttpNtlmAuth = None  # type: ignore[assignment]
try:
    import urllib3
    from urllib3.exceptions import InsecureRequestWarning
except ImportError:  # pragma: no cover - urllib3 is a transitive requests dependency in normal installs.
    urllib3 = None  # type: ignore[assignment]
    InsecureRequestWarning = None  # type: ignore[assignment]

from .telemetry import log_performance


ZERO_OBJECT_ID = "0000000000000000000000000000000000000000"
PARENT_RELATION_TYPES = {
    "System.LinkTypes.Hierarchy-Reverse",
    "Hierarchy-Reverse",
}
ATTACHMENT_RELATION_TYPES = {
    "AttachedFile",
}
HYPERLINK_RELATION_TYPES = {
    "Hyperlink",
}
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DNS_ERROR_MARKERS = [
    "The requested name is valid, but no data of the requested type was found.",
    "NameResolutionFailure",
    "No such host is known",
]


class TfsApiError(RuntimeError):
    """Raised when the TFS API returns an unexpected error."""


def clean_error_text(value: str) -> str:
    text = ANSI_ESCAPE_RE.sub("", str(value or ""))
    lines: List[str] = []
    for line in text.replace("\r", "\n").split("\n"):
        cleaned = re.sub(r"^\s*(Write-Error\s*:|Write-Error:)\s*", "", line).strip()
        if cleaned and cleaned not in lines:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def format_tfs_error(value: str, *, url: str = "") -> str:
    message = clean_error_text(value)
    if any(marker.lower() in message.lower() for marker in DNS_ERROR_MARKERS):
        host = urlparse(url).hostname or "the configured TFS host"
        return (
            f"Cannot resolve TFS host '{host}'. "
            "Check the corporate VPN/DNS connection and reload the dashboard."
        )
    return message or "Failed to call TFS."


def normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def clean_branch(value: str) -> str:
    return value.replace("refs/heads/", "").strip()


def branch_ref(value: str) -> str:
    branch = clean_branch(value)
    return f"refs/heads/{branch}" if branch else ""


def build_work_item_web_url(base_url: str, project: str, work_item_id: int) -> str:
    return f"{normalize_base_url(base_url)}/{project}/_workitems/edit/{work_item_id}"


def build_pr_web_url(base_url: str, project: str, repository: str, pr_id: int) -> str:
    return f"{normalize_base_url(base_url)}/{project}/_git/{repository}/pullrequest/{pr_id}"


def parse_identity(value: Any) -> Dict[str, str]:
    if isinstance(value, dict):
        return {
            "display_name": str(value.get("displayName") or value.get("uniqueName") or "").strip(),
            "unique_name": str(value.get("uniqueName") or "").strip(),
            "id": str(value.get("id") or "").strip(),
        }
    return {
        "display_name": str(value or "").strip(),
        "unique_name": "",
        "id": "",
    }


def extract_work_item_id_from_url(url: str) -> Optional[int]:
    match = re.search(r"/workItems/(\d+)", url or "", re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def escape_wiql_literal(value: Any) -> str:
    return str(value or "").replace("'", "''")


def chunked(values: List[int], size: int = 200) -> Iterable[List[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def git_credential_values(url: str, *, timeout_seconds: int = 10) -> Dict[str, str]:
    git = shutil.which("git")
    if not git:
        raise TfsApiError("Git executable was not found. Install Git or use PAT authentication.")

    try:
        result = subprocess.run(
            [git, "credential", "fill"],
            input=f"url={url}\n\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        parsed = urlparse(url)
        raise TfsApiError(
            "Git credential lookup timed out after "
            f"{timeout_seconds}s for {parsed.netloc}. "
            "Refresh the container Git credentials or switch this portal to PAT authentication."
        ) from exc
    if result.returncode != 0:
        parsed = urlparse(url)
        detail = result.stderr.strip() or result.stdout.strip() or "Git credential lookup failed."
        raise TfsApiError(f"Git credential lookup failed for {parsed.netloc}: {detail}")

    values: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value

    username = values.get("username", "")
    password = values.get("password", "")
    if not password:
        parsed = urlparse(url)
        raise TfsApiError(
            f"No Git credential password/token was found for {parsed.netloc}. "
            "Run a Git command against this repository first or configure a credential helper."
        )

    return {
        "username": username,
        "password": password,
    }


def parse_pull_request_relation(base_url: str, relation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    relation_url = str(relation.get("url", "") or "").strip()
    if "PullRequestId" not in relation_url:
        return None

    decoded = unquote(relation_url)
    match = re.search(r"PullRequestId/([^/]+)/([^/]+)/(\d+)$", decoded)
    if not match:
        return None

    project_id = match.group(1).strip()
    repository_id = match.group(2).strip()
    pull_request_id = int(match.group(3))
    return {
        "id": pull_request_id,
        "project_id": project_id,
        "repository_id": repository_id,
        "url": build_pr_web_url(base_url, project_id, repository_id, pull_request_id),
    }


def extract_pull_request_relations(base_url: str, relations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen_ids = set()
    for relation in relations or []:
        parsed = parse_pull_request_relation(base_url, relation)
        if not parsed:
            continue
        key = (parsed["project_id"], parsed["repository_id"], parsed["id"])
        if key in seen_ids:
            continue
        seen_ids.add(key)
        results.append(parsed)
    results.sort(key=lambda item: item["id"], reverse=True)
    return results


def extract_attachment_relations(relations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for relation in relations or []:
        relation_type = str(relation.get("rel") or "").strip()
        if relation_type not in ATTACHMENT_RELATION_TYPES:
            continue
        attributes = relation.get("attributes", {}) or {}
        results.append(
            {
                "name": str(attributes.get("name") or attributes.get("comment") or "Attachment").strip(),
                "url": str(relation.get("url") or "").strip(),
            }
        )
    return results


def extract_hyperlink_relations(relations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for relation in relations or []:
        relation_type = str(relation.get("rel") or "").strip()
        if relation_type not in HYPERLINK_RELATION_TYPES:
            continue
        attributes = relation.get("attributes", {}) or {}
        results.append(
            {
                "name": str(attributes.get("name") or attributes.get("comment") or "Link").strip(),
                "url": str(relation.get("url") or "").strip(),
            }
        )
    return results


def powershell_json_request(
    url: str,
    method: str = "GET",
    body_json: Optional[str] = None,
    content_type: str = "application/json",
) -> Any:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise TfsApiError(
            "Windows Credentials authentication requires PowerShell, but PowerShell was not found in this runtime. "
            "Use Git Credentials or PAT authentication when running from the devcontainer."
        )
    body_block = ""
    invoke_args = f'-Uri "{url}" -Method {method.upper()} -Headers @{{ Accept = \'application/json\' }}'
    if body_json is not None:
        body_block = f"""
$body = @'
{body_json}
'@
"""
        invoke_args += f" -ContentType '{content_type}' -Body $body"

    script = f"""
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ge 7) {{
    $PSStyle.OutputRendering = 'PlainText'
}}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
{body_block}
try {{
    $response = Invoke-RestMethod {invoke_args} -UseDefaultCredentials
    $response | ConvertTo-Json -Depth 100
}} catch {{
    $message = $_.Exception.Message
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) {{
        $message = $message + "`n" + $_.ErrorDetails.Message
    }}
    [Console]::Error.WriteLine($message)
    exit 1
}}
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise TfsApiError(format_tfs_error(result.stderr.strip() or result.stdout.strip(), url=url))
    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise TfsApiError(f"Invalid JSON response from the API: {exc}") from exc


def powershell_binary_request(url: str) -> Dict[str, Any]:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise TfsApiError(
            "Windows Credentials authentication requires PowerShell, but PowerShell was not found in this runtime. "
            "Use Git Credentials or PAT authentication when running from the devcontainer."
        )
    script = f"""
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ge 7) {{
    $PSStyle.OutputRendering = 'PlainText'
}}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$url = @'
{url}
'@
$tempPath = [System.IO.Path]::GetTempFileName()
try {{
    $response = Invoke-WebRequest -Uri $url -UseDefaultCredentials -Headers @{{ Accept = '*/*' }} -OutFile $tempPath -PassThru
    $contentType = $response.Headers['Content-Type']
    if ($contentType -is [array]) {{
        $contentType = $contentType[0]
    }}
    $bytes = [System.IO.File]::ReadAllBytes($tempPath)
    [PSCustomObject]@{{
        contentType = [string]$contentType
        content = [Convert]::ToBase64String($bytes)
    }} | ConvertTo-Json -Compress
}} catch {{
    $message = $_.Exception.Message
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) {{
        $message = $message + "`n" + $_.ErrorDetails.Message
    }}
    [Console]::Error.WriteLine($message)
    exit 1
}} finally {{
    if (Test-Path $tempPath) {{
        Remove-Item -LiteralPath $tempPath -Force
    }}
}}
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise TfsApiError(format_tfs_error(result.stderr.strip() or result.stdout.strip(), url=url))
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise TfsApiError(f"Invalid binary response metadata from the API: {exc}") from exc


class TfsClient:
    def __init__(
        self,
        *,
        base_url: str,
        project: str,
        repository: str,
        api_version: str,
        auth_mode: str,
        pat: str = "",
        timeout_seconds: int = 60,
        verify_ssl: bool | str = True,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.project = project.strip()
        self.repository = repository.strip()
        self.api_version = api_version.strip()
        self.auth_mode = auth_mode
        self.pat = pat.strip()
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl
        if self.verify_ssl is False and urllib3 and InsecureRequestWarning:
            urllib3.disable_warnings(InsecureRequestWarning)
        self._git_auth: Any = None

    def build_url(self, path: str, **params: Any) -> str:
        query = {"api-version": self.api_version}
        for key, value in params.items():
            if value is None or value == "":
                continue
            query[key] = value
        return f"{self.base_url}/{self.project}/_apis/{path}?{urlencode(query)}"

    def build_team_url(self, path: str, team: str, **params: Any) -> str:
        query = {"api-version": self.api_version}
        for key, value in params.items():
            if value is None or value == "":
                continue
            query[key] = value
        team_value = quote(team.strip(), safe="/") if team.strip() else ""
        team_segment = f"/{team_value}" if team_value else ""
        return f"{self.base_url}/{self.project}{team_segment}/_apis/{path}?{urlencode(query)}"

    def request_json(self, method: str, url: str, body: Any = None, *, content_type: str = "application/json") -> Any:
        started_at = time.perf_counter()
        request_failed = False
        if self.auth_mode in {"pat", "git_credentials"}:
            auth = None
            if self.auth_mode == "git_credentials":
                auth = self.request_auth()
            headers = {"Accept": "application/json"}
        if self.auth_mode == "pat":
            token = base64.b64encode(f":{self.pat}".encode("ascii")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        if self.auth_mode in {"pat", "git_credentials"}:
            if body is not None:
                headers["Content-Type"] = content_type
            try:
                response = requests.request(
                    method.upper(),
                    url,
                    headers=headers,
                    auth=auth,
                    json=body if body is not None else None,
                    timeout=self.timeout_seconds,
                    verify=self.verify_ssl,
                )
                response.raise_for_status()
            except requests.HTTPError as exc:
                request_failed = True
                detail = response.text[:1200]
                raise TfsApiError(f"{exc}\n{detail}") from exc
            except requests.exceptions.SSLError as exc:
                request_failed = True
                relative_url = url.split("_apis/")[-1]
                raise TfsApiError(
                    "TFS SSL certificate verification failed while calling "
                    f"{relative_url}. Configure the TFS CA bundle path in Runtime Settings, "
                    "or disable TFS SSL verification only for trusted internal devcontainer environments."
                ) from exc
            except requests.Timeout as exc:
                request_failed = True
                relative_url = url.split("_apis/")[-1]
                raise TfsApiError(
                    "TFS request timed out after "
                    f"{self.timeout_seconds}s while calling {relative_url}. "
                    "Check VPN/network connectivity from the dashboard runtime."
                ) from exc
            except requests.RequestException as exc:
                request_failed = True
                relative_url = url.split("_apis/")[-1]
                raise TfsApiError(f"TFS request failed while calling {relative_url}: {exc}") from exc
            finally:
                log_performance(
                    "tfs.request",
                    (time.perf_counter() - started_at) * 1000,
                    method=method.upper(),
                    auth=self.auth_mode,
                    failed=request_failed,
                    url=url.split("_apis/")[-1][:180],
                )
            if not response.text.strip():
                return None
            return response.json()
        try:
            return powershell_json_request(
                url,
                method.upper(),
                json.dumps(body) if body is not None else None,
                content_type,
            )
        except Exception:
            request_failed = True
            raise
        finally:
            log_performance(
                "tfs.request",
                (time.perf_counter() - started_at) * 1000,
                method=method.upper(),
                auth=self.auth_mode,
                failed=request_failed,
                url=url.split("_apis/")[-1][:180],
            )

    def get_binary(self, url: str) -> Dict[str, Any]:
        started_at = time.perf_counter()
        request_failed = False
        if self.auth_mode in {"pat", "git_credentials"}:
            headers = {"Accept": "*/*"}
            auth = None
            if self.auth_mode == "pat":
                token = base64.b64encode(f":{self.pat}".encode("ascii")).decode("ascii")
                headers["Authorization"] = f"Basic {token}"
            if self.auth_mode == "git_credentials":
                auth = self.request_auth()
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    auth=auth,
                    timeout=self.timeout_seconds,
                    verify=self.verify_ssl,
                )
                response.raise_for_status()
                return {
                    "content_type": response.headers.get("Content-Type") or "application/octet-stream",
                    "content": response.content,
                }
            except requests.exceptions.SSLError as exc:
                request_failed = True
                relative_url = url.split("_apis/")[-1]
                raise TfsApiError(
                    "TFS SSL certificate verification failed while downloading "
                    f"{relative_url}. Configure the TFS CA bundle path in Runtime Settings, "
                    "or disable TFS SSL verification only for trusted internal devcontainer environments."
                ) from exc
            except requests.Timeout as exc:
                request_failed = True
                relative_url = url.split("_apis/")[-1]
                raise TfsApiError(
                    "TFS asset request timed out after "
                    f"{self.timeout_seconds}s while calling {relative_url}. "
                    "Check VPN/network connectivity from the dashboard runtime."
                ) from exc
            except requests.RequestException as exc:
                request_failed = True
                relative_url = url.split("_apis/")[-1]
                raise TfsApiError(f"TFS asset request failed while calling {relative_url}: {exc}") from exc
            finally:
                log_performance(
                    "tfs.asset",
                    (time.perf_counter() - started_at) * 1000,
                    auth=self.auth_mode,
                    failed=request_failed,
                    url=url.split("_apis/")[-1][:180],
                )

        try:
            payload = powershell_binary_request(url)
            content = base64.b64decode(str(payload.get("content") or ""))
            return {
                "content_type": str(payload.get("contentType") or "application/octet-stream"),
                "content": content,
            }
        except Exception:
            request_failed = True
            raise
        finally:
            log_performance(
                "tfs.asset",
                (time.perf_counter() - started_at) * 1000,
                auth=self.auth_mode,
                failed=request_failed,
                url=url.split("_apis/")[-1][:180],
            )

    def get_json(self, url: str) -> Any:
        return self.request_json("GET", url)

    def post_json(self, url: str, body: Any) -> Any:
        return self.request_json("POST", url, body)

    def put_json(self, url: str, body: Any) -> Any:
        return self.request_json("PUT", url, body)

    def patch_json(self, url: str, body: Any, *, content_type: str = "application/json-patch+json") -> Any:
        return self.request_json("PATCH", url, body, content_type=content_type)

    def request_auth(self) -> Any:
        if self.auth_mode != "git_credentials":
            return None
        if HttpNtlmAuth is None:
            raise TfsApiError("The 'requests-ntlm' package is required for Git Credentials authentication.")
        if not self._git_auth:
            credentials = git_credential_values(
                f"{self.base_url}/{self.project}/_git/{self.repository}",
                timeout_seconds=min(10, max(5, int(self.timeout_seconds))),
            )
            self._git_auth = HttpNtlmAuth(credentials["username"], credentials["password"])
        return self._git_auth

    def resolve_repository(self) -> Dict[str, Any]:
        payload = self.get_json(self.build_url("git/repositories")) or {}
        repositories = payload.get("value", []) if isinstance(payload, dict) else payload
        wanted = self.repository.lower()
        for repository in repositories:
            name = str(repository.get("name", "")).lower()
            repo_id = str(repository.get("id", "")).lower()
            if name == wanted or repo_id == wanted:
                return repository
        raise TfsApiError(f"Repository '{self.repository}' was not found.")

    def list_team_iterations(self, team: str, timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
        url = self.build_team_url("work/teamsettings/iterations", team, **{"$timeframe": timeframe})
        payload = self.get_json(url) or {}
        return payload.get("values", []) if isinstance(payload, dict) else payload

    def get_current_iteration(self, team: str) -> Optional[Dict[str, Any]]:
        iterations = self.list_team_iterations(team, timeframe="current")
        if not iterations:
            return None
        return iterations[0]

    def query_by_wiql(self, query: str, top: Optional[int] = None) -> Dict[str, Any]:
        url = self.build_url("wit/wiql", **{"$top": top})
        return self.post_json(url, {"query": query}) or {}

    def get_work_items_batch(
        self,
        ids: Iterable[int],
        fields: Optional[List[str]] = None,
        *,
        expand: Optional[str] = None,
        error_policy: str = "Omit",
    ) -> List[Dict[str, Any]]:
        item_ids = [int(item_id) for item_id in ids]
        if not item_ids:
            return []
        rows: List[Dict[str, Any]] = []
        url = self.build_url("wit/workitemsbatch")
        for id_chunk in chunked(item_ids, 200):
            payload = {
                "ids": id_chunk,
                "errorPolicy": error_policy,
            }
            if fields:
                payload["fields"] = fields
            if expand:
                payload["$expand"] = expand
            data = self.post_json(url, payload) or {}
            chunk_rows = data.get("value", []) if isinstance(data, dict) else data
            rows.extend(chunk_rows)
        return rows

    def get_work_items_for_iteration(self, iteration_path: str, *, top: int = 400) -> List[Dict[str, Any]]:
        wiql = f"""
Select [System.Id]
From WorkItems
Where
    [System.TeamProject] = @project
    And [System.IterationPath] Under '{iteration_path}'
    And [System.State] <> 'Removed'
Order By [System.ChangedDate] Desc
""".strip()
        payload = self.query_by_wiql(wiql, top=top)
        work_items = payload.get("workItems", []) if isinstance(payload, dict) else []
        ordered_ids = [int(item["id"]) for item in work_items if "id" in item]
        return self.get_work_item_details(ordered_ids)

    def get_work_items_for_area(
        self,
        area_path: str,
        *,
        top: int = 600,
        work_item_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        work_item_clause = ""
        types = [item_type.strip() for item_type in (work_item_types or ["Task"]) if item_type.strip()]
        if types:
            work_item_clause = " And (" + " Or ".join(
                f"[System.WorkItemType] = '{escape_wiql_literal(item_type)}'"
                for item_type in types
            ) + ")"
        area_clause = ""
        if area_path.strip():
            area_clause = f" And [System.AreaPath] Under '{escape_wiql_literal(area_path.strip())}'"

        wiql = f"""
Select [System.Id]
From WorkItems
Where
    [System.TeamProject] = @project
    And [System.State] <> 'Removed'
    {area_clause}
    {work_item_clause}
Order By [System.ChangedDate] Desc
""".strip()
        payload = self.query_by_wiql(wiql, top=top)
        work_items = payload.get("workItems", []) if isinstance(payload, dict) else []
        ordered_ids = [int(item["id"]) for item in work_items if "id" in item]
        return self.get_work_item_details(ordered_ids)

    def get_work_items_for_assignees(
        self,
        assignees: List[str],
        *,
        top: int = 600,
        work_item_types: Optional[List[str]] = None,
        area_path: str = "",
        iteration_path: str = "",
        exclude_closed: bool = False,
    ) -> List[Dict[str, Any]]:
        ordered_ids = self.query_work_item_ids_for_assignees(
            assignees,
            top=top,
            work_item_types=work_item_types,
            area_path=area_path,
            iteration_path=iteration_path,
            exclude_closed=exclude_closed,
        )
        return self.get_work_item_details(ordered_ids)

    def query_work_item_ids_for_assignees(
        self,
        assignees: List[str],
        *,
        top: int = 600,
        work_item_types: Optional[List[str]] = None,
        area_path: str = "",
        iteration_path: str = "",
        exclude_closed: bool = False,
    ) -> List[int]:
        normalized_assignees = [assignee.strip() for assignee in assignees if assignee.strip()]
        if not normalized_assignees:
            return []

        assignee_clause = " Or ".join(
            f"[System.AssignedTo] = '{escape_wiql_literal(assignee)}'"
            for assignee in normalized_assignees
        )
        work_item_clause = ""
        types = [item_type.strip() for item_type in (work_item_types or ["Task"]) if item_type.strip()]
        if types:
            work_item_clause = " And (" + " Or ".join(
                f"[System.WorkItemType] = '{escape_wiql_literal(item_type)}'"
                for item_type in types
            ) + ")"
        area_clause = ""
        if area_path.strip():
            area_clause = f" And [System.AreaPath] Under '{escape_wiql_literal(area_path.strip())}'"
        iteration_clause = ""
        if iteration_path.strip():
            iteration_clause = f" And [System.IterationPath] Under '{escape_wiql_literal(iteration_path.strip())}'"
        state_clause = "And [System.State] <> 'Closed'" if exclude_closed else ""

        wiql = f"""
Select [System.Id]
From WorkItems
Where
    [System.TeamProject] = @project
    And [System.State] <> 'Removed'
    {state_clause}
    {area_clause}
    {iteration_clause}
    And ({assignee_clause})
    {work_item_clause}
Order By [System.ChangedDate] Desc
""".strip()
        payload = self.query_by_wiql(wiql, top=top)
        work_items = payload.get("workItems", []) if isinstance(payload, dict) else []
        return [int(item["id"]) for item in work_items if "id" in item]

    def get_work_item_summaries(self, ids: Iterable[int]) -> List[Dict[str, Any]]:
        ordered_ids = [int(item_id) for item_id in ids]
        if not ordered_ids:
            return []

        fields = [
            "System.Id",
            "System.WorkItemType",
            "System.Title",
            "System.State",
            "System.Tags",
            "System.IterationPath",
            "System.AreaPath",
            "System.AssignedTo",
            "System.ChangedDate",
        ]
        rows = self.get_work_items_batch(ordered_ids, fields, expand=None)
        rows_by_id = {int(row["id"]): row for row in rows if row.get("id")}
        items: List[Dict[str, Any]] = []
        for work_item_id in ordered_ids:
            row = rows_by_id.get(work_item_id)
            if not row:
                continue
            field_map = row.get("fields", {}) or {}
            items.append(
                {
                    "id": work_item_id,
                    "type": str(field_map.get("System.WorkItemType", "")).strip(),
                    "title": str(field_map.get("System.Title", "")).strip(),
                    "state": str(field_map.get("System.State", "")).strip(),
                    "tags": str(field_map.get("System.Tags", "")).strip(),
                    "iteration_path": str(field_map.get("System.IterationPath", "")).strip(),
                    "area_path": str(field_map.get("System.AreaPath", "")).strip(),
                    "assigned_to": parse_identity(field_map.get("System.AssignedTo")),
                    "description_html": "",
                    "acceptance_criteria_html": "",
                    "repro_steps_html": "",
                    "changed_date": str(field_map.get("System.ChangedDate", "") or ""),
                    "url": build_work_item_web_url(self.base_url, self.project, work_item_id),
                    "parent_id": None,
                    "parent_type": "",
                    "parent_title": "",
                    "pull_request_links": [],
                    "parent_pull_request_links": [],
                    "attachment_links": [],
                    "hyperlink_links": [],
                }
            )
        return items

    def get_work_item_details(self, ids: Iterable[int]) -> List[Dict[str, Any]]:
        ordered_ids = [int(item_id) for item_id in ids]
        if not ordered_ids:
            return []

        fields = [
            "System.Id",
            "System.WorkItemType",
            "System.Title",
            "System.State",
            "System.Tags",
            "System.IterationPath",
            "System.AreaPath",
            "System.AssignedTo",
            "System.Description",
            "System.ChangedDate",
            "Microsoft.VSTS.Common.AcceptanceCriteria",
            "Microsoft.VSTS.TCM.ReproSteps",
        ]
        full_rows = self.get_work_items_batch(ordered_ids, None, expand="Relations")
        parent_ids: List[int] = []
        rows_by_id: Dict[int, Dict[str, Any]] = {}
        for row in full_rows:
            if not row.get("id"):
                continue
            rows_by_id[int(row["id"])] = row

        for row in rows_by_id.values():
            for relation in row.get("relations", []) or []:
                if relation.get("rel") not in PARENT_RELATION_TYPES:
                    continue
                parent_id = extract_work_item_id_from_url(str(relation.get("url", "")))
                if parent_id and parent_id not in parent_ids:
                    parent_ids.append(parent_id)

        parent_rows = self.get_work_items_batch(
            parent_ids,
            None,
            expand="Relations",
        )
        parents = {int(row["id"]): row for row in parent_rows if row.get("id")}

        items: List[Dict[str, Any]] = []
        for work_item_id in ordered_ids:
            row = rows_by_id.get(work_item_id)
            if not row:
                continue
            field_map = row.get("fields", {}) or {}
            parent_id = None
            for relation in row.get("relations", []) or []:
                if relation.get("rel") not in PARENT_RELATION_TYPES:
                    continue
                parent_id = extract_work_item_id_from_url(str(relation.get("url", "")))
                if parent_id:
                    break
            parent_row = parents.get(parent_id) if parent_id else None
            parent_fields = parent_row.get("fields", {}) if parent_row else {}
            pull_request_links = extract_pull_request_relations(self.base_url, row.get("relations", []) or [])
            parent_pull_request_links = extract_pull_request_relations(
                self.base_url,
                (parent_row or {}).get("relations", []) or [],
            )
            attachment_links = extract_attachment_relations(row.get("relations", []) or [])
            hyperlink_links = extract_hyperlink_relations(row.get("relations", []) or [])
            assigned_to = parse_identity(field_map.get("System.AssignedTo"))
            items.append(
                {
                    "id": work_item_id,
                    "type": str(field_map.get("System.WorkItemType", "")).strip(),
                    "title": str(field_map.get("System.Title", "")).strip(),
                    "state": str(field_map.get("System.State", "")).strip(),
                    "tags": str(field_map.get("System.Tags", "")).strip(),
                    "iteration_path": str(field_map.get("System.IterationPath", "")).strip(),
                    "area_path": str(field_map.get("System.AreaPath", "")).strip(),
                    "assigned_to": assigned_to,
                    "description_html": str(field_map.get("System.Description", "") or ""),
                    "acceptance_criteria_html": str(field_map.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or ""),
                    "repro_steps_html": str(field_map.get("Microsoft.VSTS.TCM.ReproSteps", "") or ""),
                    "changed_date": str(field_map.get("System.ChangedDate", "") or ""),
                    "url": build_work_item_web_url(self.base_url, self.project, work_item_id),
                    "parent_id": parent_id,
                    "parent_type": str(parent_fields.get("System.WorkItemType", "")).strip(),
                    "parent_title": str(parent_fields.get("System.Title", "")).strip(),
                    "pull_request_links": pull_request_links,
                    "parent_pull_request_links": parent_pull_request_links,
                    "attachment_links": attachment_links,
                    "hyperlink_links": hyperlink_links,
                }
            )
        return items

    def get_ref(self, repository_id: str, branch_name: str) -> Optional[Dict[str, Any]]:
        filter_value = f"heads/{clean_branch(branch_name)}"
        url = self.build_url(f"git/repositories/{repository_id}/refs", filter=filter_value)
        payload = self.get_json(url) or {}
        refs = payload.get("value", []) if isinstance(payload, dict) else payload
        wanted = branch_ref(branch_name)
        for ref in refs:
            if str(ref.get("name", "")) == wanted:
                return ref
        return None

    def list_refs(self, repository_id: str, filter_prefix: str = "heads/") -> List[Dict[str, Any]]:
        url = self.build_url(f"git/repositories/{repository_id}/refs", filter=filter_prefix)
        payload = self.get_json(url) or {}
        return payload.get("value", []) if isinstance(payload, dict) else payload

    def list_pull_requests(
        self,
        repository_id: str,
        *,
        status: str = "active",
        source_branch: str = "",
        target_branch: str = "",
        top: int = 50,
    ) -> List[Dict[str, Any]]:
        query_params: Dict[str, Any] = {
            "searchCriteria.status": status,
            "$top": top,
        }
        if source_branch:
            query_params["searchCriteria.sourceRefName"] = branch_ref(source_branch)
        if target_branch:
            query_params["searchCriteria.targetRefName"] = branch_ref(target_branch)
        url = self.build_url(f"git/repositories/{repository_id}/pullrequests", **query_params)
        payload = self.get_json(url) or {}
        return payload.get("value", []) if isinstance(payload, dict) else payload

    def create_branch(self, repository_id: str, new_branch: str, base_branch: str) -> Dict[str, Any]:
        existing = self.get_ref(repository_id, new_branch)
        if existing:
            return {
                "status": "exists",
                "name": clean_branch(new_branch),
                "object_id": existing.get("objectId", ""),
            }

        base_ref = self.get_ref(repository_id, base_branch)
        if not base_ref:
            raise TfsApiError(f"Base branch '{base_branch}' was not found.")

        url = self.build_url(f"git/repositories/{repository_id}/refs")
        payload = [
            {
                "name": branch_ref(new_branch),
                "oldObjectId": ZERO_OBJECT_ID,
                "newObjectId": base_ref.get("objectId"),
            }
        ]
        data = self.post_json(url, payload) or {}
        results = data.get("value", []) if isinstance(data, dict) else data
        if not results:
            raise TfsApiError("The branch creation call returned no result.")
        result = results[0]
        if not result.get("success"):
            raise TfsApiError(result.get("customMessage") or result.get("updateStatus") or "Branch creation failed.")
        return {
            "status": "created",
            "name": clean_branch(new_branch),
            "object_id": result.get("newObjectId", ""),
        }

    def find_pull_request(
        self,
        repository_id: str,
        source_branch: str,
        target_branch: str = "",
        *,
        statuses: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        pull_requests: List[Dict[str, Any]] = []
        for status in (statuses or ["active"]):
            pull_requests.extend(
                self.list_pull_requests(
                    repository_id,
                    status=status,
                    source_branch=source_branch,
                    target_branch=target_branch,
                    top=25,
                )
            )
        if not pull_requests:
            return None
        pull_requests.sort(key=lambda pull_request: str(pull_request.get("creationDate", "")), reverse=True)
        return pull_requests[0]

    def create_pull_request(
        self,
        repository_id: str,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
        is_draft: bool = True,
    ) -> Dict[str, Any]:
        url = self.build_url(f"git/repositories/{repository_id}/pullrequests")
        payload = {
            "sourceRefName": branch_ref(source_branch),
            "targetRefName": branch_ref(target_branch),
            "title": title,
            "description": description,
            "isDraft": bool(is_draft),
        }
        return self.post_json(url, payload) or {}

    def add_required_reviewer(
        self,
        repository_id: str,
        pull_request_id: int,
        *,
        reviewer_id: str = "",
        reviewer_unique_name: str = "",
    ) -> Dict[str, Any]:
        if reviewer_id:
            url = self.build_url(
                f"git/repositories/{repository_id}/pullRequests/{pull_request_id}/reviewers/{reviewer_id}"
            )
            return self.put_json(
                url,
                {
                    "id": reviewer_id,
                    "vote": 0,
                    "isRequired": True,
                },
            ) or {}
        if reviewer_unique_name:
            url = self.build_url(
                f"git/repositories/{repository_id}/pullRequests/{pull_request_id}/reviewers"
            )
            return self.put_json(
                url,
                {
                    "uniqueName": reviewer_unique_name,
                    "vote": 0,
                    "isRequired": True,
                },
            ) or {}
        raise TfsApiError("The work item has no assigned reviewer identity.")

    def add_pull_request_work_item_link(
        self,
        work_item_id: int,
        *,
        project_id: str,
        repository_id: str,
        pull_request_id: int,
    ) -> Dict[str, Any]:
        rows = self.get_work_items_batch([work_item_id], None, expand="Relations")
        if not rows:
            raise TfsApiError(f"Work item {work_item_id} was not found.")

        existing_links = extract_pull_request_relations(
            self.base_url,
            rows[0].get("relations", []) or [],
        )
        if any(
            str(link.get("project_id") or "").lower() == str(project_id).lower()
            and str(link.get("repository_id") or "").lower() == str(repository_id).lower()
            and int(link.get("id") or 0) == int(pull_request_id)
            for link in existing_links
        ):
            return {
                "status": "exists",
                "work_item_id": int(work_item_id),
            }

        encoded_artifact = quote(f"{project_id}/{repository_id}/{pull_request_id}", safe="")
        url = self.build_url(f"wit/workitems/{int(work_item_id)}")
        payload = [
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "ArtifactLink",
                    "url": f"vstfs:///Git/PullRequestId/{encoded_artifact}",
                    "attributes": {
                        "name": "Pull Request",
                    },
                },
            }
        ]
        self.patch_json(url, payload)
        return {
            "status": "created",
            "work_item_id": int(work_item_id),
        }
