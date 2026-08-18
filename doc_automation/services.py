from __future__ import annotations

import html
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import unicodedata
from datetime import datetime
import re as std_re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode, urljoin, urlparse

from .branching import WORK_TYPES, merge_branch_plan, normalize_branch_name, version_prefix_from_branch
from .cherry_picks import (
    SCOPE_FILTERS,
    SORT_OPTIONS,
    STATUS_FILTERS,
    build_cherry_pick_context,
)
from .copilot import (
    CopilotIntegrationError,
    check_agent_provider_prerequisites,
    commit_and_push_agent_changes,
    discover_workspace_instruction_files,
    execution_runtime_scope,
    get_vscode_user_data_agent_directory,
    get_windows_user_agent_directory,
    inspect_agent_result_file,
    normalize_wsl_target_path,
    prepare_cm_gpt_handoff,
    read_agent_result,
    read_agent_provider_status,
    read_wsl_text_file,
    start_codex_device_login,
    validate_agent_changes_for_push,
    wsl_path_to_unc_path,
)
from .config import (
    AUTH_OPTIONS,
    CONTEXT_CAPTURE_ROOT_MODE_OPTIONS,
    COPILOT_PERMISSION_LEVEL_OPTIONS,
    COPILOT_PROVIDER_OPTIONS,
    COPILOT_VSCODE_WINDOW_MODE_OPTIONS,
    DATA_DIR,
    EXECUTION_RUNTIME_OPTIONS,
    get_persisted_settings_file,
    get_portal_config,
    get_portal_names,
    load_app_config,
    load_runtime_settings,
    load_vscode_settings,
    resolve_vscode_settings_path,
    save_portal_config,
    save_runtime_settings as save_runtime_settings_file,
    save_vscode_settings,
)
from .context_capture import build_capture_error_package, build_context_capture_package
from .storage import (
    get_work_item_states,
    ensure_work_item_events_from_state,
    init_storage,
    list_auto_flow_states,
    list_work_item_events,
    mark_agent_result,
    mark_agent_repair_started,
    mark_auto_flow_enabled,
    mark_branch_result,
    mark_copilot_result,
    mark_final_report,
    mark_pr_result,
    mark_push_result,
    save_work_item_plan,
    start_rerun_state,
)
from .telemetry import log_performance, performance_span
from .tfs_client import TfsApiError, TfsClient, build_pr_web_url, clean_error_text, git_credential_values, normalize_base_url


_PORTAL_PATS: Dict[str, str] = {}
TAG_RE = re.compile(r"<[^>]+>")
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_CACHE: Dict[tuple, tuple[float, Any]] = {}
_CACHE_MISS = object()
CURRENT_ITERATION_TTL_SECONDS = 60.0
WORK_ITEM_QUERY_TTL_SECONDS = 60.0
REPOSITORY_SCAN_TTL_SECONDS = 45.0
REPOSITORY_ID_TTL_SECONDS = 300.0
AGENT_RESULT_POLL_INTERVAL_SECONDS = 15.0
AGENT_RESULT_POLL_TIMEOUT_SECONDS = 3600.0
AGENT_RESULT_STABILITY_SECONDS = 45.0
VSCODE_STALE_WAIT_RELAUNCH_SECONDS = 300.0
MAX_AUTOMATIC_AGENT_REPAIR_ATTEMPTS = 1
_AUTO_WORKER_LOCK = threading.Lock()
_AUTO_WORKERS: set[tuple[str, int]] = set()
_WORKSPACE_LOCKS_LOCK = threading.Lock()
_WORKSPACE_LOCKS: Dict[str, threading.RLock] = {}
GIT_CREDENTIAL_SOURCE_ENV = "CONTENT_AI_HOST_GIT_CREDENTIALS_PATH"
TFS_GIT_USERNAME_ENV = "CONTENT_AI_TFS_GIT_USERNAME"
TFS_GIT_PASSWORD_ENV = "CONTENT_AI_TFS_GIT_PASSWORD"
TFS_GIT_TOKEN_ENV = "CONTENT_AI_TFS_GIT_TOKEN"
PERSISTED_GIT_CREDENTIALS_FILENAME = "git-credentials"
TFS_PULL_REQUEST_DESCRIPTION_LIMIT = 3900
DEFAULT_RATIONALE_TEXT = (
    "The changes align the documentation with the work item requirements and captured implementation evidence."
)
MAX_WORKSPACE_OPTIONS = 40


class ServiceError(RuntimeError):
    """Raised when a dashboard action cannot be completed safely."""


def _cache_get(key: tuple) -> Any:
    entry = _CACHE.get(key)
    if not entry:
        return _CACHE_MISS
    expires_at, value = entry
    if expires_at < time.monotonic():
        _CACHE.pop(key, None)
        return _CACHE_MISS
    return value


def _cache_set(key: tuple, value: Any, ttl_seconds: float) -> Any:
    _CACHE[key] = (time.monotonic() + ttl_seconds, value)
    return value


def _age_seconds_from_iso_timestamp(value: Any) -> float:
    token = str(value or "").strip()
    if not token:
        return 0.0
    try:
        timestamp = datetime.fromisoformat(token)
    except ValueError:
        return 0.0
    now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
    return max(0.0, (now - timestamp).total_seconds())


def _cache_delete_prefix(prefix: tuple) -> None:
    for key in list(_CACHE.keys()):
        if key[: len(prefix)] == prefix:
            _CACHE.pop(key, None)


def strip_html(value: str) -> str:
    text = TAG_RE.sub(" ", value or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate_text(value: str, max_length: int, notice: str) -> str:
    text = str(value or "").strip()
    if max_length <= 0:
        return ""
    if len(text) <= max_length:
        return text
    clean_notice = str(notice or "").strip()
    suffix = f"\n\n_{clean_notice}_" if clean_notice else ""
    if len(suffix) >= max_length:
        return text[:max_length].rstrip()
    return text[: max_length - len(suffix)].rstrip() + suffix


def markdown_h2_sections(value: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current_title = ""
    for line in str(value or "").splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current_title = match.group(1).strip()
            sections.setdefault(current_title, [])
            continue
        if current_title:
            sections[current_title].append(line)
    return {title: "\n".join(lines).strip() for title, lines in sections.items()}


def report_section_has_content(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    empty_markers = [
        "No spec references were reported",
        "No validation was reported",
        "No reviewer notes were reported",
        "No detailed rationale was reported",
    ]
    return not any(marker.lower() in text.lower() for marker in empty_markers)


def _portal_tfs_host(portal: Dict[str, Any]) -> str:
    parsed = urlparse(normalize_base_url(str(portal.get("base_url") or "")))
    return parsed.netloc or parsed.path.strip("/")


def _git_credential_store_path() -> Path:
    return Path.home() / ".git-credentials"


def _persisted_git_credential_store_path() -> Optional[Path]:
    return get_persisted_settings_file(PERSISTED_GIT_CREDENTIALS_FILENAME)


def _secure_credential_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _credential_file_has_host(path: Path, host: str) -> bool:
    if not path.exists() or not host:
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(_credential_line_matches_host(line, host) for line in lines)


def _copy_git_credential_store(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target_path)
    _secure_credential_file(target_path)


def _restore_persisted_git_credentials(host: str = "") -> Dict[str, Any]:
    persisted_path = _persisted_git_credential_store_path()
    credential_path = _git_credential_store_path()
    if not persisted_path or not persisted_path.exists():
        return {
            "status": "skipped",
            "ok": False,
            "message": "No persisted Git credential store is available.",
        }
    if host and _credential_file_has_host(credential_path, host):
        _set_git_credential_store_helper()
        return {
            "status": "skipped",
            "ok": True,
            "message": f"Git credentials for {host} are already available in the local credential store.",
            "path": str(credential_path),
        }
    _copy_git_credential_store(persisted_path, credential_path)
    _set_git_credential_store_helper()
    return {
        "status": "restored",
        "ok": True,
        "message": f"Restored persisted Git credentials into {credential_path}.",
        "path": str(credential_path),
        "persisted_path": str(persisted_path),
    }


def _mirror_git_credentials_to_persisted_store() -> Dict[str, Any]:
    persisted_path = _persisted_git_credential_store_path()
    credential_path = _git_credential_store_path()
    if not persisted_path:
        return {
            "status": "skipped",
            "ok": False,
            "message": "CONTENT_AI_SETTINGS_PATH is not configured, so Git credentials were not mirrored.",
        }
    if not credential_path.exists():
        return {
            "status": "skipped",
            "ok": False,
            "message": f"Git credential store does not exist at {credential_path}.",
        }
    _copy_git_credential_store(credential_path, persisted_path)
    return {
        "status": "mirrored",
        "ok": True,
        "message": f"Mirrored Git credentials to {persisted_path}.",
        "persisted_path": str(persisted_path),
    }


def _path_exists_as_git_workspace(path_value: str) -> bool:
    clean_path = str(path_value or "").strip()
    if not clean_path:
        return False
    if WINDOWS_ABSOLUTE_PATH_RE.match(clean_path) or clean_path.startswith("\\\\"):
        return False
    try:
        path = Path(os.path.expanduser(clean_path))
        return path.exists() and (path / ".git").exists()
    except OSError:
        return False


def _workspace_option_label(path_value: str, *, current_path: str = "") -> str:
    clean_path = str(path_value or "").strip()
    if not clean_path:
        return ""
    if clean_path == "/app":
        label = "/app (current devcontainer workspace)"
    else:
        label = Path(clean_path.rstrip("/")).name or clean_path
    if current_path and clean_path.rstrip("/") == current_path.rstrip("/"):
        label = f"{label} (selected)"
    return label


def _workspace_lock_key(path_value: str) -> str:
    clean_path = str(path_value or "").strip().replace("\\", "/").rstrip("/")
    return clean_path.lower() or "__unconfigured_workspace__"


def _get_workspace_lock(path_value: str) -> threading.RLock:
    key = _workspace_lock_key(path_value)
    with _WORKSPACE_LOCKS_LOCK:
        lock = _WORKSPACE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WORKSPACE_LOCKS[key] = lock
        return lock


def _credential_line_matches_host(line: str, host: str) -> bool:
    parsed = urlparse(line.strip())
    if not parsed.netloc:
        return False
    return parsed.netloc.rsplit("@", 1)[-1].lower() == host.lower()


def _set_git_credential_store_helper() -> None:
    git = shutil.which("git")
    if not git:
        return
    try:
        subprocess.run(
            [git, "config", "--global", "--unset-all", "credential.helper"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        subprocess.run(
            [git, "config", "--global", "credential.helper", "store"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        subprocess.run(
            [git, "config", "--global", "credential.useHttpPath", "true"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def _write_git_store_credential(base_url: str, *, username: str, password: str) -> Dict[str, Any]:
    parsed = urlparse(normalize_base_url(base_url))
    scheme = parsed.scheme or "https"
    host = parsed.netloc or parsed.path.strip("/")
    if not host:
        return {
            "status": "error",
            "ok": False,
            "message": "Cannot write Git credentials because the TFS host is empty.",
        }
    if not username or not password:
        return {
            "status": "error",
            "ok": False,
            "message": (
                f"Set both {TFS_GIT_USERNAME_ENV} and {TFS_GIT_PASSWORD_ENV} "
                f"or {TFS_GIT_TOKEN_ENV} to configure Git credentials automatically."
            ),
        }

    credential_path = _git_credential_store_path()
    credential_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = (
        credential_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if credential_path.exists()
        else []
    )
    retained_lines = [line for line in existing_lines if not _credential_line_matches_host(line, host)]
    credential_url = (
        f"{scheme}://{quote(username, safe='')}:{quote(password, safe='')}@{host}"
    )
    retained_lines.append(credential_url)
    credential_path.write_text("\n".join(retained_lines).rstrip() + "\n", encoding="utf-8")
    _secure_credential_file(credential_path)
    _set_git_credential_store_helper()
    _mirror_git_credentials_to_persisted_store()
    return {
        "status": "repaired",
        "ok": True,
        "message": f"Stored Git credentials for {host} in {credential_path} and mirrored them to the persistent settings folder when configured.",
        "source": "environment",
    }


def _approve_git_credential(url: str, *, username: str, password: str) -> None:
    git = shutil.which("git")
    if not git:
        raise ServiceError("Git executable was not found. Install Git or use PAT authentication.")
    if not username.strip() or not password.strip():
        raise ServiceError("Enter both a TFS Git username and token/password.")
    credential_payload = (
        f"url={normalize_base_url(url)}\n"
        f"username={username.strip()}\n"
        f"password={password.strip()}\n\n"
    )
    result = subprocess.run(
        [git, "credential", "approve"],
        input=credential_payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "Git credential approve failed."
        raise ServiceError(detail)


def _git_remote_url(workspace_path: str) -> str:
    if not workspace_path.strip():
        return ""
    result = subprocess.run(
        ["git", "-C", workspace_path.strip(), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _validate_git_remote_access(workspace_path: str) -> Dict[str, Any]:
    if not workspace_path.strip():
        return {
            "status": "skipped",
            "ok": True,
            "message": "No workspace path is configured, so Git remote validation was skipped.",
        }
    git = shutil.which("git")
    if not git:
        raise ServiceError("Git executable was not found. Install Git or use PAT authentication.")
    result = subprocess.run(
        [git, "-C", workspace_path.strip(), "ls-remote", "--heads", "origin"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode == 0:
        return {
            "status": "ok",
            "ok": True,
            "message": "Git remote access validated with `git ls-remote --heads origin`.",
        }
    detail = result.stderr.strip() or result.stdout.strip() or "Git remote validation failed."
    return {
        "status": "error",
        "ok": False,
        "message": clean_error_text(detail),
    }


def _copy_git_store_credentials(source_path: str) -> Dict[str, Any]:
    expanded_source = Path(os.path.expandvars(os.path.expanduser(source_path)))
    if not expanded_source.exists() or not expanded_source.is_file():
        return {
            "status": "error",
            "ok": False,
            "message": f"Configured Git credentials file was not found: {expanded_source}",
            "source": str(expanded_source),
        }
    credential_path = _git_credential_store_path()
    if expanded_source.resolve() != credential_path.resolve():
        credential_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(expanded_source, credential_path)
    _secure_credential_file(credential_path)
    _set_git_credential_store_helper()
    _mirror_git_credentials_to_persisted_store()
    return {
        "status": "repaired",
        "ok": True,
        "message": f"Copied Git credentials from {expanded_source} to {credential_path} and mirrored them to the persistent settings folder when configured.",
        "source": str(expanded_source),
    }


def _attempt_git_credentials_remediation(base_url: str) -> Dict[str, Any]:
    source_path = str(os.environ.get(GIT_CREDENTIAL_SOURCE_ENV) or "").strip()
    if source_path:
        return _copy_git_store_credentials(source_path)

    username = str(os.environ.get(TFS_GIT_USERNAME_ENV) or "").strip()
    password = str(
        os.environ.get(TFS_GIT_PASSWORD_ENV)
        or os.environ.get(TFS_GIT_TOKEN_ENV)
        or ""
    ).strip()
    if username or password:
        return _write_git_store_credential(base_url, username=username, password=password)

    return {
        "status": "skipped",
        "ok": False,
        "message": (
            "No automatic Git credential source is configured. "
            f"Set {GIT_CREDENTIAL_SOURCE_ENV} to a mounted .git-credentials file, "
            f"or set {TFS_GIT_USERNAME_ENV} and {TFS_GIT_PASSWORD_ENV}/{TFS_GIT_TOKEN_ENV} "
            "before rebuilding the devcontainer."
        ),
    }


def _check_git_credentials_for_portal(portal: Dict[str, Any]) -> Dict[str, Any]:
    auth_mode = str(portal.get("auth_mode") or "").strip()
    host = _portal_tfs_host(portal)
    if auth_mode == "PAT":
        return {
            "status": "skipped",
            "ok": True,
            "message": "Portal uses PAT authentication; Git credential preflight is not required.",
        }
    runtime_settings = load_runtime_settings()
    execution_runtime = str(runtime_settings.get("execution_runtime") or "devcontainer").strip()
    if auth_mode == "Windows Credentials" and execution_runtime == "devcontainer":
        return {
            "status": "error",
            "ok": False,
            "message": (
                "Windows Credentials cannot be used from the devcontainer runtime. "
                "Switch the portal to Git Credentials or PAT authentication."
            ),
            "host": host,
        }
    if auth_mode != "Git Credentials":
        return {
            "status": "skipped",
            "ok": True,
            "message": "Git credential preflight is not required for the selected authentication mode.",
        }

    workspace_path = str(portal.get("copilot_workspace_path") or "").strip()
    if workspace_path:
        try:
            result = subprocess.run(
                ["git", "-C", workspace_path, "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "status": "error",
                "ok": False,
                "message": f"Could not validate Git workspace at '{workspace_path}': {exc}",
                "host": host,
            }
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if ".git/worktrees" in detail or "not a git repository" in detail.lower():
                detail = (
                    f"The configured workspace '{workspace_path}' is not a valid Git repository inside the "
                    "current devcontainer. If this workspace is a linked Git worktree, rebuild/reopen the "
                    "devcontainer with the WSL /workspaces folder mounted so the shared .git metadata is visible."
                )
            return {
                "status": "error",
                "ok": False,
                "message": detail or f"The configured workspace '{workspace_path}' is not a valid Git repository.",
                "host": host,
            }

    base_url = normalize_base_url(str(portal.get("base_url") or ""))
    try:
        _restore_persisted_git_credentials(host)
        _set_git_credential_store_helper()
        credentials = git_credential_values(base_url, timeout_seconds=10)
        _mirror_git_credentials_to_persisted_store()
        return {
            "status": "ok",
            "ok": True,
            "message": f"Git credentials are available for {host}.",
            "host": host,
            "username": credentials.get("username", ""),
        }
    except TfsApiError as first_error:
        remediation = _attempt_git_credentials_remediation(base_url)
        if remediation.get("ok"):
            try:
                credentials = git_credential_values(base_url, timeout_seconds=10)
                return {
                    "status": "repaired",
                    "ok": True,
                    "message": f"Git credentials were configured and validated for {host}.",
                    "host": host,
                    "username": credentials.get("username", ""),
                    "remediation": remediation,
                }
            except TfsApiError as second_error:
                return {
                    "status": "error",
                    "ok": False,
                    "message": str(second_error),
                    "host": host,
                    "remediation": remediation,
                }
        return {
            "status": "error",
            "ok": False,
            "message": str(first_error),
            "host": host,
            "remediation": remediation,
        }


def _resolve_tfs_ssl_verify(runtime_settings: Dict[str, Any]) -> bool | str:
    if not bool(runtime_settings.get("tfs_verify_ssl", True)):
        return False
    ca_bundle_path = str(runtime_settings.get("tfs_ca_bundle_path") or "").strip()
    if ca_bundle_path:
        return ca_bundle_path
    return True


class WorkItemHtmlSanitizer(HTMLParser):
    ALLOWED_TAGS = {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
    VOID_TAGS = {"br", "hr", "img"}
    URI_ATTRIBUTES = {"href", "src"}

    def __init__(self, *, base_url: str, portal_name: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url.rstrip("/") + "/"
        self.portal_name = portal_name
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        tag_name = tag.lower()
        if tag_name not in self.ALLOWED_TAGS:
            return

        clean_attrs: List[str] = []
        for raw_name, raw_value in attrs:
            name = str(raw_name or "").strip().lower()
            value = str(raw_value or "").strip()
            if not name or name.startswith("on"):
                continue
            if name in self.URI_ATTRIBUTES:
                value = self._safe_url(value, is_image=tag_name == "img")
                if not value:
                    continue
            elif tag_name == "img" and name in {"alt", "title"}:
                pass
            elif tag_name == "img" and name in {"width", "height"}:
                if not re.match(r"^[0-9]{1,4}%?$", value):
                    continue
            elif tag_name == "a" and name == "title":
                pass
            elif name in {"colspan", "rowspan"}:
                if not re.match(r"^[0-9]{1,2}$", value):
                    continue
            else:
                continue
            clean_attrs.append(f'{name}="{html.escape(value, quote=True)}"')

        if tag_name == "a":
            clean_attrs.append('target="_blank"')
            clean_attrs.append('rel="noreferrer noopener"')
        if tag_name == "img":
            clean_attrs.append('loading="lazy"')

        attrs_text = f" {' '.join(clean_attrs)}" if clean_attrs else ""
        self.parts.append(f"<{tag_name}{attrs_text}>")

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in self.ALLOWED_TAGS and tag_name not in self.VOID_TAGS:
            self.parts.append(f"</{tag_name}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data))

    def get_html(self) -> str:
        return "".join(self.parts).strip()

    def _safe_url(self, value: str, *, is_image: bool) -> str:
        if not value:
            return ""
        normalized = html.unescape(value).strip()
        lower_value = normalized.lower()
        if is_image and lower_value.startswith("data:image/"):
            return normalized
        if normalized.startswith("#") and not is_image:
            return normalized
        absolute = urljoin(self.base_url, normalized)
        parsed = urlparse(absolute)
        if parsed.scheme.lower() in {"http", "https"}:
            if is_image and self.portal_name:
                return "/tfs-assets?" + urlencode(
                    {
                        "portal": self.portal_name,
                        "url": absolute,
                    }
                )
            return absolute
        if not is_image and parsed.scheme.lower() == "mailto":
            return absolute
        return ""


def sanitize_work_item_html(value: str, *, base_url: str, portal_name: str = "") -> str:
    if not str(value or "").strip():
        return ""
    sanitizer = WorkItemHtmlSanitizer(base_url=base_url, portal_name=portal_name)
    sanitizer.feed(str(value or ""))
    sanitizer.close()
    return sanitizer.get_html()


def build_tfs_asset_proxy_url(portal_name: str, url: str) -> str:
    return "/tfs-assets?" + urlencode(
        {
            "portal": str(portal_name or ""),
            "url": str(url or ""),
        }
    )


def is_image_attachment(attachment: Dict[str, Any]) -> bool:
    name = str(attachment.get("name") or "").strip().lower()
    url = str(attachment.get("url") or "").strip().lower()
    image_extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
    return name.endswith(image_extensions) or any(extension in url for extension in image_extensions)


def prepare_image_attachment_links(portal_name: str, attachments: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    prepared: List[Dict[str, str]] = []
    for attachment in attachments:
        if not is_image_attachment(attachment):
            continue
        url = str(attachment.get("url") or "").strip()
        if not url:
            continue
        prepared.append(
            {
                "name": str(attachment.get("name") or "Image").strip(),
                "url": url,
                "proxy_url": build_tfs_asset_proxy_url(portal_name, url),
            }
        )
    return prepared


def repair_text_encoding(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    mojibake_markers = ("Ã", "Â", "â€", "â€™", "â€œ", "â€\x9d")
    if not any(marker in text for marker in mojibake_markers):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8").strip()
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired or text


def repair_identity(identity: Dict[str, Any]) -> Dict[str, str]:
    return {
        "display_name": repair_text_encoding(identity.get("display_name", "")),
        "unique_name": repair_text_encoding(identity.get("unique_name", "")),
        "id": str(identity.get("id") or "").strip(),
    }


def normalize_match_token(value: str) -> str:
    return str(value or "").strip().lower()


def normalize_identity_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_value.strip().lower()


def build_identity_match_tokens(identity: Dict[str, Any]) -> List[str]:
    tokens: List[str] = []
    candidates = [
        str(identity.get("display_name") or ""),
        str(identity.get("unique_name") or ""),
        str(identity.get("id") or ""),
    ]
    unique_name = str(identity.get("unique_name") or "").strip()
    if "\\" in unique_name:
        candidates.append(unique_name.split("\\")[-1])
    if "@" in unique_name:
        candidates.append(unique_name.split("@")[0])

    for candidate in candidates:
        token = normalize_identity_token(candidate)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def build_query_assignee_candidates(members: List[str]) -> List[str]:
    candidates: List[str] = []
    for member in members:
        raw = str(member or "").strip()
        if not raw:
            continue
        for candidate in [raw, f"CMF\\{raw}" if "\\" not in raw and "@" not in raw else ""]:
            token = candidate.strip()
            if token and token not in candidates:
                candidates.append(token)
    return candidates


def filter_work_items_by_members(items: List[Dict[str, Any]], members: List[str]) -> List[Dict[str, Any]]:
    normalized_members = {
        normalize_identity_token(member)
        for member in members
        if normalize_identity_token(member)
    }
    if not normalized_members:
        return []

    filtered: List[Dict[str, Any]] = []
    for item in items:
        assigned_to = item.get("assigned_to", {}) or {}
        match_tokens = build_identity_match_tokens(assigned_to)
        if any(token in normalized_members for token in match_tokens):
            filtered.append(item)
    return filtered


def iteration_matches_filter(item_iteration_path: str, selected_iteration: str) -> bool:
    item_value = str(item_iteration_path or "").strip().replace("/", "\\").lower()
    filter_value = str(selected_iteration or "").strip().replace("/", "\\").lower()
    if not filter_value:
        return True
    return item_value == filter_value or item_value.startswith(f"{filter_value}\\")


def branch_matches_work_item(branch_name: str, work_item_id: int) -> bool:
    pattern = rf"(^|[^0-9]){work_item_id}([^0-9]|$)"
    return bool(std_re.search(pattern, branch_name))


def choose_branch_candidate(
    branches: List[str],
    *,
    planned_branch: str,
    selected_work_type: str,
    work_item_id: int,
) -> str:
    if not branches:
        return ""
    normalized_planned = str(planned_branch or "").strip().lower()
    if normalized_planned:
        for branch in branches:
            if branch.lower() == normalized_planned:
                return branch

    preferred_type = str(selected_work_type or "").strip().lower()
    for branch in branches:
        segments = branch.split("/")
        if preferred_type and preferred_type in segments:
            return branch

    exact_id_suffix = f"/{work_item_id}"
    hyphen_suffix = f"/{work_item_id}-"
    for branch in branches:
        if branch.endswith(exact_id_suffix) or hyphen_suffix in branch:
            return branch

    return sorted(branches, key=len)[0]


def collect_branch_search_prefixes(
    branch_chain: List[str],
    selected_base_branch: str,
    planned_branch: str,
) -> List[str]:
    direct_prefix = version_prefix_from_branch(selected_base_branch) or version_prefix_from_branch(planned_branch)
    if direct_prefix and re.fullmatch(r"\d+\.\d+", direct_prefix):
        return [direct_prefix]

    prefixes: List[str] = []
    for branch in branch_chain:
        prefix = version_prefix_from_branch(branch)
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    return prefixes


def collapse_branch_fetch_prefixes(prefixes: List[str]) -> List[str]:
    normalized = [prefix.strip() for prefix in prefixes if prefix.strip()]
    if not normalized:
        return []
    if len(normalized) == 1:
        return normalized

    shared = normalized[0]
    for prefix in normalized[1:]:
        index = 0
        for left, right in zip(shared, prefix):
            if left != right:
                break
            index += 1
        shared = shared[:index]
        if not shared:
            break
    if shared and any(char.isdigit() for char in shared):
        return [shared]

    grouped: Dict[str, List[str]] = {}
    for prefix in normalized:
        major = prefix.split(".", 1)[0].strip()
        grouped.setdefault(major, []).append(prefix)

    collapsed: List[str] = []
    for major, values in grouped.items():
        if len(values) > 1 and major:
            collapsed.append(f"{major}.")
        else:
            collapsed.extend(values)
    return collapsed


def normalize_vscode_read_access_folder(path_value: str, default_distro: str) -> str:
    raw_value = str(path_value or "").strip()
    if not raw_value:
        return ""
    if raw_value.startswith("\\\\wsl") or raw_value.startswith("/") or raw_value.startswith("~"):
        effective_distro, normalized_path = normalize_wsl_target_path(raw_value, default_distro)
        return wsl_path_to_unc_path(effective_distro, normalized_path)
    if WINDOWS_ABSOLUTE_PATH_RE.match(raw_value):
        return str(Path(raw_value).expanduser())
    return raw_value


def filter_pull_requests_for_repository(
    pull_requests: List[Dict[str, Any]],
    repository_id: str,
) -> List[Dict[str, Any]]:
    wanted = str(repository_id or "").strip().lower()
    if not wanted:
        return []
    return [
        pull_request
        for pull_request in pull_requests
        if str(pull_request.get("repository_id") or "").strip().lower() == wanted
        and not pull_request_is_abandoned(pull_request)
    ]


def pull_request_is_abandoned(pull_request: Dict[str, Any]) -> bool:
    return str(pull_request.get("status") or "").strip().lower() == "abandoned"


def build_pull_request_index(pull_requests: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for pull_request in pull_requests:
        if pull_request_is_abandoned(pull_request):
            continue
        source_ref = str(pull_request.get("sourceRefName") or "").replace("refs/heads/", "").strip()
        if not source_ref:
            continue
        current = indexed.get(source_ref)
        if not current or str(pull_request.get("creationDate", "")) > str(current.get("creationDate", "")):
            indexed[source_ref] = pull_request
    return indexed


def find_pull_request_branch_match(
    pull_request_index: Dict[str, Dict[str, Any]],
    *,
    planned_branch: str,
    selected_work_type: str,
    work_item_id: int,
    parent_work_item_id: Optional[int],
) -> tuple[str, Optional[Dict[str, Any]], str]:
    if not pull_request_index:
        return "", None, ""

    normalized_planned = str(planned_branch or "").strip()
    if normalized_planned and normalized_planned in pull_request_index:
        return normalized_planned, pull_request_index[normalized_planned], "planned-branch"

    pull_request_branches = list(pull_request_index.keys())
    direct_matches = [
        branch
        for branch in pull_request_branches
        if branch_matches_work_item(branch, work_item_id)
    ]
    direct_branch = choose_branch_candidate(
        direct_matches,
        planned_branch=planned_branch,
        selected_work_type=selected_work_type,
        work_item_id=work_item_id,
    )
    if direct_branch:
        return direct_branch, pull_request_index.get(direct_branch), "work-item-branch"

    if parent_work_item_id:
        parent_matches = [
            branch
            for branch in pull_request_branches
            if branch_matches_work_item(branch, parent_work_item_id)
        ]
        parent_branch = choose_branch_candidate(
            parent_matches,
            planned_branch="",
            selected_work_type=selected_work_type,
            work_item_id=parent_work_item_id,
        )
        if parent_branch:
            return parent_branch, pull_request_index.get(parent_branch), "parent-branch"

    return "", None, ""


def build_progress_steps(item: Dict[str, Any]) -> List[Dict[str, str]]:
    plan_ready = bool(item.get("selected_base_branch"))
    has_branch = bool(item.get("has_branch"))
    copilot_status = str(item.get("copilot_status") or "").strip().lower()
    copilot_ready = copilot_status in {"prepared", "launched"}
    copilot_blocked = copilot_status in {"blocked", "desktop_prepared"}
    agent_result_status = str(item.get("agent_result_status") or "").strip().lower()
    agent_green = agent_result_status in {"green_light", "ready_for_push", "success"}
    agent_waiting = agent_result_status in {"", "waiting"}
    push_status = str(item.get("push_status") or "").strip().lower()
    pushed = push_status == "pushed"
    has_pr = bool(item.get("has_pr"))
    branch_error = bool(item.get("branch_error"))
    copilot_error = copilot_blocked or bool(item.get("copilot_error"))
    agent_error = agent_result_status in {"blocked", "invalid", "error", "needs_agent_fix"}
    push_error = push_status == "error" or bool(item.get("push_error"))
    pr_error = bool(item.get("pr_error"))

    steps = [
        {"label": "Plan", "state": "done" if plan_ready else "current"},
        {
            "label": "Branch",
            "state": "error" if branch_error else ("done" if has_branch else ("current" if plan_ready else "todo")),
        },
        {
            "label": "CM GPT",
            "state": "error" if copilot_error else ("done" if copilot_ready or agent_green or pushed or has_pr else ("current" if has_branch else "todo")),
        },
        {
            "label": "Result",
            "state": "done" if agent_green or pushed or has_pr else ("error" if agent_error else ("current" if copilot_ready and agent_waiting else "todo")),
        },
        {
            "label": "Push",
            "state": "done" if pushed or has_pr else ("error" if push_error else ("current" if agent_green else "todo")),
        },
        {
            "label": "Draft PR",
            "state": "done" if has_pr else ("error" if pr_error else ("current" if pushed else "todo")),
        },
        {"label": "Review", "state": "current" if has_pr else "todo"},
    ]
    return steps


def summarize_automation_stage(item: Dict[str, Any]) -> str:
    if item.get("has_pr"):
        return "In review"
    if str(item.get("push_status") or "").strip().lower() == "pushed":
        return "Ready to create draft PR"
    if item.get("pr_error"):
        return "Draft PR failed"
    if str(item.get("push_status") or "").strip().lower() == "error" or item.get("push_error"):
        return "Push failed"
    agent_result_status = str(item.get("agent_result_status") or "").strip().lower()
    if agent_result_status == "needs_agent_fix":
        return "Agent result needs fix"
    if agent_result_status in {"blocked", "invalid", "error"}:
        return "Agent result needs review"
    if agent_result_status in {"", "waiting"} and int(item.get("agent_repair_count") or 0) > 0:
        return "Repairing agent result"
    copilot_status = str(item.get("copilot_status") or "").strip().lower()
    if copilot_status in {"blocked", "desktop_prepared"}:
        return "CM GPT automation blocked"
    if item.get("copilot_error"):
        return "CM GPT launch failed"
    if item.get("branch_error"):
        return "Branch creation failed"
    if agent_result_status in {"green_light", "ready_for_push", "success"}:
        return "Ready to push"
    if copilot_status == "launched":
        return "Waiting for agent result"
    if copilot_status == "prepared":
        return "CM GPT handoff ready"
    if item.get("has_branch"):
        return "Ready for CM GPT"
    if item.get("selected_base_branch"):
        return "Ready to create branch"
    return "Plan branch"


def build_agent_result_guidance(item: Dict[str, Any]) -> Dict[str, Any]:
    """Translate stored agent diagnostics into an operational dashboard outcome."""
    status = str(item.get("agent_result_status") or "").strip().lower()
    error = str(item.get("agent_result_error") or "").strip()
    summary = str(item.get("agent_result_summary") or "").strip()
    if not status and not error:
        return {}

    if status == "needs_agent_fix":
        blockers: List[str] = []
        lower_error = error.lower()
        lower_summary = summary.lower()
        no_change = "did not give green light" in lower_error or "no accurate documentation change" in lower_summary
        if no_change:
            blockers.append("The agent did not identify a safe documentation update, so no changes were pushed.")
        if "instruction_files_read" in lower_error:
            blockers.append("The returned report did not confirm the repository instructions required by the pipeline.")
        if "automation context path" in lower_error:
            blockers.append("The returned report included an internal automation file instead of only repository changes.")
        if "workspace is on branch" in lower_error:
            blockers.append("The automatic repair could not start because the configured workspace changed to a different branch.")
        if not blockers:
            blockers.append("The agent result did not meet the validation requirements for an automatic push.")
        return {
            "level": "warning",
            "title": "Agent run completed, but no safe update is ready to publish",
            "summary": summary or "The result needs correction before the pipeline can continue.",
            "blockers": blockers,
            "next_steps": [
                "Review the final report and captured work item context.",
                "Keep the target workspace on the planned work-item branch before rerunning the agent.",
                "Rerun only after the required documentation evidence or implementation context is available.",
            ],
            "technical_details": error,
        }

    if status in {"blocked", "invalid", "error"}:
        return {
            "level": "error",
            "title": "Agent execution needs attention",
            "summary": summary or "The agent result cannot be used to continue the pipeline.",
            "blockers": ["No changes were pushed and no Draft PR was created."],
            "next_steps": ["Review the technical details, correct the reported issue, and rerun the agent."],
            "technical_details": error,
        }

    if status == "waiting" and error:
        return {
            "level": "info",
            "title": "Agent action is pending",
            "summary": error,
            "blockers": [],
            "next_steps": [],
            "technical_details": "",
        }
    return {}


def is_auto_flow_active(item: Dict[str, Any]) -> bool:
    if not bool(item.get("auto_flow_enabled")) or bool(item.get("has_pr")):
        return False
    if item.get("branch_error") or item.get("copilot_error") or item.get("push_error") or item.get("pr_error"):
        return False
    if str(item.get("copilot_status") or "").strip().lower() in {"blocked", "error", "desktop_prepared"}:
        return False
    if str(item.get("agent_result_status") or "").strip().lower() in {"blocked", "invalid", "error", "needs_agent_fix"}:
        return False
    if str(item.get("push_status") or "").strip().lower() == "error":
        return False
    return True


def build_state_style(state: str) -> Dict[str, str]:
    normalized = str(state or "").strip().lower()
    if normalized in {"closed", "done", "completed"}:
        return {"state_class": "state-closed", "state_label": "Closed"}
    if normalized in {"active", "in progress", "committed"}:
        return {"state_class": "state-active", "state_label": str(state or "Active").strip() or "Active"}
    if normalized in {"resolved", "ready for test", "ready for review"}:
        return {"state_class": "state-resolved", "state_label": str(state or "Resolved").strip() or "Resolved"}
    if normalized in {"new", "approved", "to do", "proposed"}:
        return {"state_class": "state-new", "state_label": str(state or "New").strip() or "New"}
    return {"state_class": "state-neutral", "state_label": str(state or "-").strip() or "-"}


def build_pr_link_label(pr_status: str) -> str:
    normalized = str(pr_status or "").strip().lower()
    if normalized == "created":
        return "Open Draft PR"
    return "Open Associated PR"


def validate_planned_branch_name(branch_name: str) -> str:
    normalized = normalize_branch_name(branch_name)
    if not normalized:
        return ""
    if any(token in normalized for token in ["..", "@{", "//"]):
        raise ServiceError("The planned branch name contains an unsupported Git ref pattern.")
    if normalized.startswith(".") or normalized.endswith(".") or normalized.endswith(".lock"):
        raise ServiceError("The planned branch name is not a valid Git branch name.")
    if " " in normalized:
        raise ServiceError("The planned branch name cannot contain spaces.")
    return normalized


def build_rerun_branch_name(branch_name: str) -> str:
    base = normalize_branch_name(branch_name)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    suffix = f"-rerun-{timestamp}"
    if len(base) + len(suffix) > 220:
        base = base[: 220 - len(suffix)].rstrip("-/")
    return validate_planned_branch_name(f"{base}{suffix}")


def safe_path_segment(value: Any, fallback: str = "item") -> str:
    normalized = re.sub(r"[^A-Za-z0-9._ -]+", "-", str(value or "").strip())
    normalized = re.sub(r"\s+", " ", normalized).strip(" .-_")
    return normalized or fallback


def format_report_value(value: Any, fallback: str = "Not reported.") -> str:
    if isinstance(value, list):
        lines = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(f"- {line}" for line in lines) if lines else fallback
    if isinstance(value, dict):
        lines = [
            f"- {str(key).replace('_', ' ').title()}: {str(item).strip()}"
            for key, item in value.items()
            if str(item).strip()
        ]
        return "\n".join(lines) if lines else fallback
    text = str(value or "").strip()
    return text or fallback


def format_spec_references(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "No spec references were reported by the agent."

    lines: List[str] = []
    for reference in value:
        if isinstance(reference, dict):
            spec = str(reference.get("spec") or reference.get("path") or reference.get("name") or "Unnamed spec").strip()
            section = str(reference.get("section") or reference.get("topic") or reference.get("part") or "").strip()
            usage = str(reference.get("used_for") or reference.get("applied_change") or reference.get("reason") or "").strip()
            line = f"- {spec}"
            if section:
                line += f" | Section/topic: {section}"
            if usage:
                line += f" | Applied to: {usage}"
            lines.append(line)
        else:
            token = str(reference or "").strip()
            if token:
                lines.append(f"- {token}")
    return "\n".join(lines) if lines else "No spec references were reported by the agent."


def normalize_instruction_reference(value: Any) -> str:
    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if "/repo-instructions/" in path:
        path = path.split("/repo-instructions/", 1)[1]
    return path.strip("/").lower()


def validate_instruction_acknowledgement(
    *,
    expected_instruction_files: List[Dict[str, str]],
    agent_result: Dict[str, Any],
) -> None:
    expected_paths = [
        str(item.get("relative_path") or "").strip().replace("\\", "/").strip("/")
        for item in expected_instruction_files
        if str(item.get("relative_path") or "").strip()
    ]
    if not expected_paths:
        return

    read_paths = {
        normalize_instruction_reference(candidate)
        for candidate in list(agent_result.get("instruction_files_read") or [])
        if normalize_instruction_reference(candidate)
    }
    missing = [
        path
        for path in expected_paths
        if normalize_instruction_reference(path) not in read_paths
    ]
    if missing:
        displayed = ", ".join(missing[:8])
        suffix = f", plus {len(missing) - 8} more" if len(missing) > 8 else ""
        raise ServiceError(
            "The agent result did not confirm that repository instructions were read. "
            f"Missing `instruction_files_read` entries: {displayed}{suffix}."
        )


def format_pipeline_validation(value: Any) -> str:
    if not isinstance(value, dict):
        return "No dashboard-managed validation was recorded."
    checks = list(value.get("checks") or [])
    if not checks:
        return "No dashboard-managed validation was recorded."
    lines: List[str] = []
    for check in checks:
        if isinstance(check, dict):
            name = str(check.get("name") or "Validation").strip()
            status = str(check.get("status") or "-").strip()
            lines.append(f"- {name}: {status}")
    return "\n".join(lines) if lines else "No dashboard-managed validation was recorded."


def render_basic_markdown(markdown_text: str) -> str:
    lines = str(markdown_text or "").splitlines()
    html_lines: List[str] = []
    paragraph: List[str] = []
    in_list = False
    in_code = False
    code_lines: List[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            html_lines.append(f"<p>{html.escape(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html_lines.append("</ul>")
            in_list = False

    for raw_line in lines:
        line = raw_line.rstrip()
        if line.startswith("```"):
            if in_code:
                html_lines.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                flush_paragraph()
                close_list()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            close_list()
            continue
        if line.startswith("#"):
            flush_paragraph()
            close_list()
            level = min(4, len(line) - len(line.lstrip("#")))
            text = line[level:].strip()
            if text:
                html_lines.append(f"<h{level}>{html.escape(text)}</h{level}>")
            continue
        if line.startswith("- "):
            flush_paragraph()
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{html.escape(line[2:].strip())}</li>")
            continue
        paragraph.append(line.strip())

    if in_code:
        html_lines.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    flush_paragraph()
    close_list()
    return "\n".join(html_lines)


class AutomationService:
    def __init__(self) -> None:
        init_storage()

    def discover_target_workspaces(
        self,
        *,
        portal: Dict[str, Any],
        runtime_settings: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        repository_name = str(portal.get("repository") or "").strip()
        current_path = str(portal.get("copilot_workspace_path") or "").strip().rstrip("/")
        scan_roots: List[str] = []

        for candidate in ["/app", current_path, "/workspaces"]:
            clean_candidate = str(candidate or "").strip().rstrip("/")
            if not clean_candidate:
                continue
            if clean_candidate in {"/app", "/workspaces"}:
                scan_roots.append(clean_candidate)
                continue
            parent = str(Path(clean_candidate).parent) if clean_candidate.startswith("/") else clean_candidate
            scan_roots.append(parent)

        for root in list(runtime_settings.get("context_capture_workspace_scan_roots") or []):
            scan_roots.append(str(root or "").strip().rstrip("/"))

        normalized_roots: List[str] = []
        for root in scan_roots:
            if root and root not in normalized_roots and root.startswith("/"):
                normalized_roots.append(root)

        candidates: List[str] = []
        if current_path:
            candidates.append(current_path)
        if Path("/app/.git").exists():
            candidates.append("/app")

        for root in normalized_roots:
            try:
                root_path = Path(root)
                if not root_path.exists() or not root_path.is_dir():
                    continue
                if root == "/app":
                    continue
                for child in root_path.iterdir():
                    if not child.is_dir():
                        continue
                    name = child.name
                    if repository_name and not (name == repository_name or name.startswith(f"{repository_name}-")):
                        continue
                    candidates.append(str(child))
            except OSError:
                continue

        unique_candidates: List[str] = []
        for candidate in candidates:
            clean_candidate = str(candidate or "").strip().rstrip("/")
            if not clean_candidate or clean_candidate in unique_candidates:
                continue
            unique_candidates.append(clean_candidate)
            if len(unique_candidates) >= MAX_WORKSPACE_OPTIONS:
                break

        def sort_key(path_value: str) -> tuple[int, str]:
            if current_path and path_value == current_path:
                return (0, path_value.lower())
            if path_value.endswith("-#01"):
                return (1, path_value.lower())
            if path_value == "/app":
                return (2, path_value.lower())
            return (3, path_value.lower())

        unique_candidates.sort(key=sort_key)
        return [
            {
                "path": candidate,
                "label": _workspace_option_label(candidate, current_path=current_path),
                "exists": Path(candidate).exists() if candidate.startswith("/") else False,
                "is_git": _path_exists_as_git_workspace(candidate),
                "is_current": bool(current_path and candidate == current_path),
            }
            for candidate in unique_candidates
        ]

    def _invalidate_portal_repository_cache(self, portal_name: str) -> None:
        _cache_delete_prefix(("repository_refs", portal_name))
        _cache_delete_prefix(("repository_prs", portal_name))
        _cache_delete_prefix(("branch_pr", portal_name))
        _cache_delete_prefix(("pull_request", portal_name))

    def _invalidate_portal_runtime_cache(self, portal_name: str) -> None:
        _cache_delete_prefix(("current_iteration", portal_name))
        _cache_delete_prefix(("work_items", portal_name))
        _cache_delete_prefix(("repository", portal_name))
        self._invalidate_portal_repository_cache(portal_name)

    def _get_current_iteration_cached(
        self,
        portal_name: str,
        client: TfsClient,
        team_name: str,
    ) -> Optional[Dict[str, Any]]:
        cache_key = ("current_iteration", portal_name, team_name)
        cached_value = _cache_get(cache_key)
        if cached_value is not _CACHE_MISS:
            return cached_value
        current_iteration = client.get_current_iteration(team_name)
        return _cache_set(cache_key, current_iteration, CURRENT_ITERATION_TTL_SECONDS)

    def _get_assigned_work_items_cached(
        self,
        portal_name: str,
        client: TfsClient,
        *,
        assignees: List[str],
        filter_members: List[str],
        area_path: str,
        iteration_path: str = "",
        exclude_closed: bool = False,
        include_details: bool = True,
        top: int,
        work_item_types: List[str],
    ) -> List[Dict[str, Any]]:
        cache_key = (
            "work_items",
            portal_name,
            tuple(sorted(assignees)),
            tuple(sorted(filter_members)),
            area_path,
            iteration_path,
            exclude_closed,
            include_details,
            tuple(work_item_types),
            top,
        )
        cached_value = _cache_get(cache_key)
        if cached_value is not _CACHE_MISS:
            return list(cached_value)
        candidate_ids = client.query_work_item_ids_for_assignees(
            assignees,
            top=top,
            work_item_types=work_item_types,
            area_path=area_path,
            iteration_path=iteration_path,
            exclude_closed=exclude_closed,
        )
        summaries = client.get_work_item_summaries(candidate_ids)
        filtered_summaries = filter_work_items_by_members(summaries, filter_members)
        if not include_details:
            return list(_cache_set(cache_key, filtered_summaries, WORK_ITEM_QUERY_TTL_SECONDS))
        items = client.get_work_item_details([int(item["id"]) for item in filtered_summaries])
        return list(_cache_set(cache_key, items, WORK_ITEM_QUERY_TTL_SECONDS))

    def _get_repository_id_cached(self, portal_name: str, client: TfsClient) -> str:
        repository = self._get_repository_cached(portal_name, client)
        return str(repository["id"])

    def _get_repository_cached(self, portal_name: str, client: TfsClient) -> Dict[str, Any]:
        cache_key = ("repository", portal_name)
        cached_value = _cache_get(cache_key)
        if cached_value is not _CACHE_MISS:
            return dict(cached_value)
        repository = client.resolve_repository()
        return _cache_set(cache_key, repository, REPOSITORY_ID_TTL_SECONDS)

    def _get_repository_refs_cached(
        self,
        portal_name: str,
        client: TfsClient,
        repository_id: str,
        filter_prefix: str,
    ) -> List[str]:
        cache_key = ("repository_refs", portal_name, repository_id, filter_prefix)
        cached_value = _cache_get(cache_key)
        if cached_value is not _CACHE_MISS:
            return list(cached_value)
        refs = client.list_refs(repository_id, filter_prefix)
        branch_names = [
            str(ref.get("name", "")).replace("refs/heads/", "")
            for ref in refs
            if str(ref.get("name", "")).startswith("refs/heads/")
        ]
        return list(_cache_set(cache_key, branch_names, REPOSITORY_SCAN_TTL_SECONDS))

    def _get_repository_pull_request_index_cached(
        self,
        portal_name: str,
        client: TfsClient,
        repository_id: str,
        top_limit: int,
    ) -> Dict[str, Dict[str, Any]]:
        cache_key = ("repository_prs", portal_name, repository_id, top_limit)
        cached_value = _cache_get(cache_key)
        if cached_value is not _CACHE_MISS:
            return dict(cached_value)

        pull_requests: List[Dict[str, Any]] = []
        for status in ["active", "completed"]:
            pull_requests.extend(
                client.list_pull_requests(
                    repository_id,
                    status=status,
                    top=top_limit,
                )
            )
        index = build_pull_request_index(pull_requests)
        return dict(_cache_set(cache_key, index, REPOSITORY_SCAN_TTL_SECONDS))

    def _get_pull_request_cached(
        self,
        portal_name: str,
        client: TfsClient,
        repository_id: str,
        pull_request_id: int,
    ) -> Dict[str, Any]:
        cache_key = ("pull_request", portal_name, repository_id, int(pull_request_id))
        cached_value = _cache_get(cache_key)
        if cached_value is not _CACHE_MISS:
            return dict(cached_value)
        pull_request = client.get_pull_request(repository_id, int(pull_request_id))
        return dict(_cache_set(cache_key, pull_request, REPOSITORY_SCAN_TTL_SECONDS))

    def _filter_blocking_pull_request_links_for_repository(
        self,
        portal_name: str,
        client: TfsClient,
        pull_requests: List[Dict[str, Any]],
        repository_id: str,
    ) -> List[Dict[str, Any]]:
        blocking_pull_requests: List[Dict[str, Any]] = []
        for pull_request in filter_pull_requests_for_repository(pull_requests, repository_id):
            try:
                pull_request_id = int(pull_request.get("id") or pull_request.get("pullRequestId") or 0)
            except (TypeError, ValueError):
                pull_request_id = 0
            if not pull_request_id:
                continue
            try:
                current_pull_request = self._get_pull_request_cached(
                    portal_name,
                    client,
                    repository_id,
                    pull_request_id,
                )
            except Exception:
                blocking_pull_requests.append(pull_request)
                continue
            if pull_request_is_abandoned(current_pull_request):
                continue
            enriched_pull_request = dict(pull_request)
            for key in ("status", "title", "sourceRefName", "targetRefName", "creationDate", "_links"):
                if current_pull_request.get(key) is not None:
                    enriched_pull_request[key] = current_pull_request.get(key)
            blocking_pull_requests.append(enriched_pull_request)
        return blocking_pull_requests

    def _stored_pull_request_is_abandoned(
        self,
        portal_name: str,
        client: TfsClient,
        repository_id: str,
        pull_request_id: Any,
    ) -> bool:
        try:
            normalized_pull_request_id = int(pull_request_id or 0)
        except (TypeError, ValueError):
            return False
        if not normalized_pull_request_id:
            return False
        try:
            pull_request = self._get_pull_request_cached(
                portal_name,
                client,
                repository_id,
                normalized_pull_request_id,
            )
        except Exception:
            return False
        return pull_request_is_abandoned(pull_request)

    def _find_pull_request_for_branch_cached(
        self,
        portal_name: str,
        client: TfsClient,
        repository_id: str,
        source_branch: str,
    ) -> Optional[Dict[str, Any]]:
        cache_key = ("branch_pr", portal_name, repository_id, source_branch)
        cached_value = _cache_get(cache_key)
        if cached_value is not _CACHE_MISS:
            return cached_value
        pull_request = client.find_pull_request(
            repository_id,
            source_branch,
            statuses=["active", "completed"],
        )
        return _cache_set(cache_key, pull_request, REPOSITORY_SCAN_TTL_SECONDS)

    def set_portal_pat(self, portal_name: str, pat: str) -> None:
        if pat.strip():
            _PORTAL_PATS[portal_name] = pat.strip()
        else:
            _PORTAL_PATS.pop(portal_name, None)
        self._invalidate_portal_runtime_cache(portal_name)

    def check_portal_credentials(self, portal_name: str = "") -> Dict[str, Any]:
        config = load_app_config()
        portal_names = get_portal_names(config)
        active_portal_name = portal_name or config["DEFAULT_PORTAL"]
        if active_portal_name not in portal_names:
            active_portal_name = config["DEFAULT_PORTAL"]
        portal = get_portal_config(config, active_portal_name)
        return _check_git_credentials_for_portal(portal)

    def setup_portal_git_credentials(
        self,
        *,
        portal_name: str,
        username: str,
        token: str,
    ) -> Dict[str, Any]:
        clean_username = username.strip()
        clean_token = token.strip()
        if not clean_username or not clean_token:
            raise ServiceError("Enter both the TFS Git username and token/password.")

        config = load_app_config()
        portal_names = get_portal_names(config)
        active_portal_name = portal_name or config["DEFAULT_PORTAL"]
        if active_portal_name not in portal_names:
            active_portal_name = config["DEFAULT_PORTAL"]
        portal = get_portal_config(config, active_portal_name)
        if str(portal.get("auth_mode") or "").strip() != "Git Credentials":
            raise ServiceError("Switch the selected portal to Git Credentials before configuring Git credentials.")

        base_url = normalize_base_url(str(portal.get("base_url") or ""))
        project = str(portal.get("project") or "").strip()
        repository = str(portal.get("repository") or "").strip()
        workspace_path = str(portal.get("copilot_workspace_path") or "").strip()
        repository_url = ""
        if base_url and project and repository:
            repository_url = f"{base_url}/{quote(project, safe='')}/_git/{quote(repository, safe='')}"
        remote_url = _git_remote_url(workspace_path)

        credential_urls: List[str] = []
        for candidate in [base_url, repository_url, remote_url]:
            clean_candidate = normalize_base_url(candidate)
            if clean_candidate and clean_candidate not in credential_urls:
                credential_urls.append(clean_candidate)
        if not credential_urls:
            raise ServiceError("Cannot configure credentials because the portal TFS URL is empty.")

        _set_git_credential_store_helper()
        for credential_url in credential_urls:
            _approve_git_credential(credential_url, username=clean_username, password=clean_token)
        persistent_store = _mirror_git_credentials_to_persisted_store()

        credential_preflight = _check_git_credentials_for_portal(portal)
        remote_validation = _validate_git_remote_access(workspace_path)
        ok = bool(credential_preflight.get("ok")) and bool(remote_validation.get("ok"))
        if ok:
            message = "TFS Git credentials were saved and validated for this devcontainer."
        else:
            message = (
                "TFS Git credentials were saved, but validation still needs attention. "
                f"{credential_preflight.get('message') or ''} {remote_validation.get('message') or ''}"
            ).strip()
        self._invalidate_portal_runtime_cache(portal["repository"])
        return {
            "status": "ok" if ok else "warning",
            "ok": ok,
            "message": message,
            "credential_preflight": credential_preflight,
            "remote_validation": remote_validation,
            "credential_urls_count": len(credential_urls),
            "credential_store_path": str(_git_credential_store_path()),
            "persistent_credential_store_path": persistent_store.get("persisted_path", ""),
        }

    def get_settings_context(self, portal_name: str = "") -> Dict[str, Any]:
        config = load_app_config()
        runtime_settings = load_runtime_settings()
        portal_names = get_portal_names(config)
        active_portal_name = portal_name or config["DEFAULT_PORTAL"]
        if active_portal_name not in portal_names:
            active_portal_name = config["DEFAULT_PORTAL"]
        portal = get_portal_config(config, active_portal_name)
        dashboard_started_at = time.perf_counter()
        runtime_settings["copilot_vscode_effective_settings_path"] = str(
            resolve_vscode_settings_path(str(runtime_settings.get("copilot_vscode_settings_path") or ""))
        )
        runtime_settings["copilot_vscode_derived_read_access_folders"] = self._derive_vscode_read_access_folders(
            config=config,
            runtime_settings=runtime_settings,
        )
        workspace_options = self.discover_target_workspaces(
            portal=portal,
            runtime_settings=runtime_settings,
        )
        return {
            "config": config,
            "portal_names": portal_names,
            "portal": portal,
            "selected_portal": portal["repository"],
            "runtime_settings": runtime_settings,
            "workspace_options": workspace_options,
            "has_pat": bool(_PORTAL_PATS.get(portal["repository"])),
            "auth_options": AUTH_OPTIONS,
            "copilot_permission_level_options": COPILOT_PERMISSION_LEVEL_OPTIONS,
            "copilot_provider_options": COPILOT_PROVIDER_OPTIONS,
            "copilot_vscode_window_mode_options": COPILOT_VSCODE_WINDOW_MODE_OPTIONS,
            "context_capture_root_mode_options": CONTEXT_CAPTURE_ROOT_MODE_OPTIONS,
            "execution_runtime_options": EXECUTION_RUNTIME_OPTIONS,
        }

    def _derive_vscode_read_access_folders(
        self,
        *,
        config: Dict[str, Any],
        runtime_settings: Dict[str, Any],
    ) -> List[str]:
        default_distro = str(runtime_settings.get("copilot_wsl_distro") or "").strip() or "Ubuntu"
        raw_candidates: List[str] = []

        reference_docs_path = str(runtime_settings.get("copilot_reference_docs_path") or "").strip()
        if reference_docs_path:
            raw_candidates.append(reference_docs_path)

        for portal in list(config.get("portals", []) or []):
            workspace_path = str((portal or {}).get("copilot_workspace_path") or "").strip()
            if workspace_path:
                raw_candidates.append(workspace_path)

        raw_candidates.extend(list(runtime_settings.get("copilot_additional_read_access_folders") or []))

        normalized: List[str] = []
        for candidate in raw_candidates:
            folder = normalize_vscode_read_access_folder(str(candidate or ""), default_distro)
            if folder and folder not in normalized:
                normalized.append(folder)
        return normalized

    def _apply_vscode_copilot_settings(
        self,
        *,
        config: Dict[str, Any],
        runtime_settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        settings_path = resolve_vscode_settings_path(str(runtime_settings.get("copilot_vscode_settings_path") or ""))
        settings = load_vscode_settings(str(settings_path))
        permission_level = str(runtime_settings.get("copilot_vscode_permission_level") or "default").strip()
        if permission_level not in COPILOT_PERMISSION_LEVEL_OPTIONS:
            permission_level = "default"

        additional_read_access_folders = self._derive_vscode_read_access_folders(
            config=config,
            runtime_settings=runtime_settings,
        )

        settings["chat.permissions.default"] = permission_level
        settings["chat.tools.global.autoApprove"] = bool(runtime_settings.get("copilot_vscode_global_auto_approve"))
        settings["chat.editing.autoAcceptDelay"] = int(
            runtime_settings.get("copilot_vscode_auto_accept_edits_delay_ms") or 0
        )
        settings["github.copilot.chat.additionalReadAccessFolders"] = additional_read_access_folders
        agent_files_locations = settings.get("chat.agentFilesLocations")
        if not isinstance(agent_files_locations, list):
            agent_files_locations = []
        for agent_directory in [str(get_windows_user_agent_directory()), str(get_vscode_user_data_agent_directory())]:
            if agent_directory not in agent_files_locations:
                agent_files_locations.append(agent_directory)
        settings["chat.agentFilesLocations"] = agent_files_locations

        save_vscode_settings(settings, str(settings_path))
        return {
            "settings_path": str(settings_path),
            "permission_level": permission_level,
            "global_auto_approve": bool(runtime_settings.get("copilot_vscode_global_auto_approve")),
            "auto_accept_edits_delay_ms": int(runtime_settings.get("copilot_vscode_auto_accept_edits_delay_ms") or 0),
            "additional_read_access_folders": additional_read_access_folders,
            "agent_files_locations": agent_files_locations,
        }

    def _get_pat(self, portal: Dict[str, Any]) -> str:
        if portal["auth_mode"] != "PAT":
            return ""
        return _PORTAL_PATS.get(portal["repository"], "")

    def _build_client(self, portal: Dict[str, Any]) -> TfsClient:
        return self._build_tfs_client(
            portal,
            project=portal["project"],
            repository=portal["repository"],
        )

    def _build_work_item_client(self, portal: Dict[str, Any]) -> TfsClient:
        return self._build_tfs_client(
            portal,
            project=portal["work_item_project"],
            repository=portal["repository"],
        )

    def _build_tfs_client(
        self,
        portal: Dict[str, Any],
        *,
        project: str,
        repository: str,
    ) -> TfsClient:
        runtime_settings = load_runtime_settings()
        pat = self._get_pat(portal)
        if portal["auth_mode"] == "PAT":
            auth_mode = "pat"
        elif portal["auth_mode"] == "Git Credentials":
            auth_mode = "git_credentials"
        else:
            auth_mode = "default_credentials"
        if portal["auth_mode"] == "PAT" and not pat:
            raise ServiceError("This portal uses PAT authentication. Enter a PAT before loading work items.")
        return TfsClient(
            base_url=portal["base_url"],
            project=project,
            repository=repository,
            api_version=portal["api_version"],
            auth_mode=auth_mode,
            pat=pat,
            timeout_seconds=int(runtime_settings.get("tfs_request_timeout_seconds") or 15),
            verify_ssl=_resolve_tfs_ssl_verify(runtime_settings),
        )

    def load_cherry_pick_dashboard(
        self,
        *,
        portal_name: str,
        load: bool,
        lookback_days: Optional[int] = None,
        max_prs_per_branch: Optional[int] = None,
        scope: str = "All",
        status: str = "All",
        branch: str = "All",
        sort_by: str = "Severity",
        descending: bool = False,
    ) -> Dict[str, Any]:
        config = load_app_config()
        portal_names = get_portal_names(config)
        selected_portal = portal_name if portal_name in portal_names else config["DEFAULT_PORTAL"]
        portal = get_portal_config(config, selected_portal)
        branch_chain = list(portal.get("branch_chain") or [])
        clean_scope = scope if scope in SCOPE_FILTERS else "All"
        clean_status = status if status in STATUS_FILTERS else "All"
        clean_branch = branch if branch == "All" or branch in branch_chain else "All"
        clean_sort = sort_by if sort_by in SORT_OPTIONS else "Severity"
        selected_lookback_days = max(0, int(lookback_days if lookback_days is not None else portal.get("lookback_days") or 7))
        selected_max_prs = max(1, min(500, int(max_prs_per_branch if max_prs_per_branch is not None else portal.get("max_prs_per_branch") or 150)))

        context: Dict[str, Any] = {
            "portal_names": portal_names,
            "selected_portal": selected_portal,
            "portal": portal,
            "branch_chain": branch_chain,
            "lookback_days": selected_lookback_days,
            "max_prs_per_branch": selected_max_prs,
            "scope": clean_scope,
            "status_filter": clean_status,
            "branch_filter": clean_branch,
            "sort_by": clean_sort,
            "descending": bool(descending),
            "scope_options": SCOPE_FILTERS,
            "status_options": STATUS_FILTERS,
            "sort_options": SORT_OPTIONS,
            "branch_options": ["All", *branch_chain],
            "loaded": bool(load),
            "rows": [],
            "summary": {"total": 0, "missing": 0, "open": 0, "abandoned": 0, "done": 0},
            "unfiltered_summary": {"total": 0, "missing": 0, "open": 0, "abandoned": 0, "done": 0},
            "pull_request_count": 0,
            "ignored_pull_request_count": 0,
            "ignored_label_pull_request_count": 0,
            "ignored_abandoned_pull_request_count": 0,
            "cherry_pick_skip_labels": list(portal.get("cherry_pick_skip_labels") or []),
            "current_user": {},
            "repository_name": str(portal.get("repository") or ""),
        }
        if not load:
            return context

        client = self._build_tfs_client(
            portal,
            project=portal["project"],
            repository=portal["repository"],
        )
        cherry_context = build_cherry_pick_context(
            client=client,
            branch_chain=branch_chain,
            lookback_days=selected_lookback_days,
            max_prs_per_branch=selected_max_prs,
            verify_work_items_via_api=bool(portal.get("verify_work_items_via_api")),
            scope=clean_scope,
            status=clean_status,
            branch=clean_branch,
            sort_by=clean_sort,
            descending=bool(descending),
            skip_labels=list(portal.get("cherry_pick_skip_labels") or []),
        )
        context.update(cherry_context)
        context["repository_name"] = str(cherry_context.get("repository_name") or portal.get("repository") or "")
        return context

    def _decorate_work_item(
        self,
        work_item: Dict[str, Any],
        portal: Dict[str, Any],
        state: Optional[Dict[str, Any]],
        runtime_settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        plan = merge_branch_plan(work_item, portal["branch_chain"], state)
        assigned_to = repair_identity(work_item.get("assigned_to", {}) or {})
        resolved_reviewer = self._resolve_reviewer(assigned_to, runtime_settings)
        state_style = build_state_style(work_item.get("state", ""))
        branch_status = str((state or {}).get("branch_status") or "")
        pr_status = str((state or {}).get("pr_status") or "")
        changed_date = work_item.get("changed_date") or ""
        changed_label = "-"
        if changed_date:
            try:
                changed_label = datetime.fromisoformat(str(changed_date).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                changed_label = str(changed_date)

        description_preview = strip_html(str(work_item.get("description_html", "")))
        acceptance_preview = strip_html(str(work_item.get("acceptance_criteria_html", "")))
        description_html = str(work_item.get("description_html") or "")
        acceptance_html = str(work_item.get("acceptance_criteria_html") or "")
        repro_steps_html = str(work_item.get("repro_steps_html") or "")
        attachment_links = list(work_item.get("attachment_links", []) or [])
        parent_description_html = str(work_item.get("parent_description_html") or "")
        parent_acceptance_html = str(work_item.get("parent_acceptance_criteria_html") or "")
        parent_repro_steps_html = str(work_item.get("parent_repro_steps_html") or "")
        parent_attachment_links = list(work_item.get("parent_attachment_links", []) or [])
        copilot_status = str((state or {}).get("copilot_status") or "")
        copilot_error = str((state or {}).get("copilot_error") or "")
        if copilot_status.strip().lower() == "desktop_prepared" and not copilot_error:
            copilot_error = (
                "Microsoft 365 Copilot Desktop handoffs are no longer treated as pipeline progress. "
                "Rerun with an automation-capable provider."
            )
        agent_result_status = str((state or {}).get("agent_result_status") or "")
        agent_result_summary = str((state or {}).get("agent_result_summary") or "")
        agent_result_error = str((state or {}).get("agent_result_error") or "")
        agent_result_guidance = build_agent_result_guidance(
            {
                "agent_result_status": agent_result_status,
                "agent_result_summary": agent_result_summary,
                "agent_result_error": agent_result_error,
            }
        )

        return {
            **work_item,
            "assigned_to": assigned_to,
            **plan,
            "triage_status": str((state or {}).get("triage_status") or plan["triage_status"]),
            "reviewer_display_name": repair_text_encoding((state or {}).get("reviewer_display_name") or resolved_reviewer.get("display_name") or ""),
            "reviewer_unique_name": repair_text_encoding((state or {}).get("reviewer_unique_name") or resolved_reviewer.get("unique_name") or ""),
            "reviewer_id": str((state or {}).get("reviewer_id") or resolved_reviewer.get("id") or ""),
            "reviewer_source": str(resolved_reviewer.get("source") or "work_item"),
            "branch_status": branch_status,
            "branch_error": str((state or {}).get("branch_error") or ""),
            "branch_created_at": str((state or {}).get("branch_created_at") or ""),
            "pr_status": pr_status,
            "pr_id": (state or {}).get("pr_id"),
            "pr_url": str((state or {}).get("pr_url") or ""),
            "pr_error": str((state or {}).get("pr_error") or ""),
            "copilot_status": copilot_status,
            "copilot_error": copilot_error,
            "copilot_context_path": str((state or {}).get("copilot_context_path") or ""),
            "copilot_workspace_path": str(portal.get("copilot_workspace_path") or (state or {}).get("copilot_workspace_path") or ""),
            "copilot_agent_name": str((state or {}).get("copilot_agent_name") or runtime_settings.get("copilot_agent_name") or ""),
            "copilot_provider_log_path": str((state or {}).get("copilot_provider_log_path") or ""),
            "copilot_process_id": str((state or {}).get("copilot_process_id") or ""),
            "copilot_prepared_at": str((state or {}).get("copilot_prepared_at") or ""),
            "copilot_auto_launch": bool(runtime_settings.get("copilot_auto_launch")),
            "agent_result_status": agent_result_status,
            "agent_result_path": str((state or {}).get("agent_result_path") or ""),
            "agent_result_summary": agent_result_summary,
            "agent_result_error": agent_result_error,
            "agent_result_guidance": agent_result_guidance,
            "agent_result_checked_at": str((state or {}).get("agent_result_checked_at") or ""),
            "agent_repair_count": int((state or {}).get("agent_repair_count") or 0),
            "agent_repair_last_started_at": str((state or {}).get("agent_repair_last_started_at") or ""),
            "agent_repair_last_reason": str((state or {}).get("agent_repair_last_reason") or ""),
            "push_status": str((state or {}).get("push_status") or ""),
            "push_commit": str((state or {}).get("push_commit") or ""),
            "push_error": str((state or {}).get("push_error") or ""),
            "pushed_at": str((state or {}).get("pushed_at") or ""),
            "final_report_path": str((state or {}).get("final_report_path") or ""),
            "final_report_created_at": str((state or {}).get("final_report_created_at") or ""),
            "rerun_active": bool((state or {}).get("rerun_active")),
            "rerun_started_at": str((state or {}).get("rerun_started_at") or ""),
            "auto_flow_enabled": bool((state or {}).get("auto_flow_enabled")),
            "state_updated_at": str((state or {}).get("updated_at") or ""),
            "changed_label": changed_label,
            "description_preview": description_preview[:280],
            "acceptance_preview": acceptance_preview[:280],
            "description_rendered_html": sanitize_work_item_html(
                description_html,
                base_url=portal["base_url"],
                portal_name=portal["repository"],
            ),
            "acceptance_rendered_html": sanitize_work_item_html(
                acceptance_html,
                base_url=portal["base_url"],
                portal_name=portal["repository"],
            ),
            "repro_steps_rendered_html": sanitize_work_item_html(
                repro_steps_html,
                base_url=portal["base_url"],
                portal_name=portal["repository"],
            ),
            "image_attachment_links": prepare_image_attachment_links(portal["repository"], attachment_links),
            "parent_description_rendered_html": sanitize_work_item_html(
                parent_description_html,
                base_url=portal["base_url"],
                portal_name=portal["repository"],
            ),
            "parent_acceptance_rendered_html": sanitize_work_item_html(
                parent_acceptance_html,
                base_url=portal["base_url"],
                portal_name=portal["repository"],
            ),
            "parent_repro_steps_rendered_html": sanitize_work_item_html(
                parent_repro_steps_html,
                base_url=portal["base_url"],
                portal_name=portal["repository"],
            ),
            "parent_image_attachment_links": prepare_image_attachment_links(
                portal["repository"], parent_attachment_links
            ),
            "linked_work_item_prs": list(work_item.get("pull_request_links", []) or []),
            "linked_parent_prs": list(work_item.get("parent_pull_request_links", []) or []),
            "pr_source_branch": "",
            "pr_match_source": "",
            "pr_link_label": build_pr_link_label(pr_status),
            **state_style,
            "work_type_options": WORK_TYPES,
            "is_locked": branch_status in {"created", "exists"} or bool((state or {}).get("pr_id")),
        }

    def _load_repository_context(self, portal: Dict[str, Any]) -> tuple[Optional[TfsClient], Optional[str]]:
        try:
            client = self._build_client(portal)
            return client, self._get_repository_id_cached(portal["repository"], client)
        except Exception:
            return None, None

    def _detect_existing_branch_name(
        self,
        client: TfsClient,
        portal_name: str,
        repository_id: str,
        planned_branch: str,
        selected_base_branch: str,
        selected_work_type: str,
        work_item_id: int,
        refs_cache: Dict[str, List[str]],
        branch_chain: List[str],
    ) -> str:
        prefixes = collect_branch_search_prefixes(branch_chain, selected_base_branch, planned_branch)
        available_branches: List[str] = []
        if prefixes:
            fetch_prefixes = collapse_branch_fetch_prefixes(prefixes)
            for fetch_prefix in fetch_prefixes:
                cache_key = f"prefix:{fetch_prefix}"
                if cache_key not in refs_cache:
                    refs_cache[cache_key] = self._get_repository_refs_cached(
                        portal_name,
                        client,
                        repository_id,
                        f"heads/{fetch_prefix}",
                    )
                for branch_name in refs_cache[cache_key]:
                    if branch_name not in available_branches:
                        available_branches.append(branch_name)
        else:
            cache_key = "__all__"
            if cache_key not in refs_cache:
                refs_cache[cache_key] = self._get_repository_refs_cached(
                    portal_name,
                    client,
                    repository_id,
                    "heads/",
                )
            available_branches = list(refs_cache[cache_key])

        if planned_branch and planned_branch in available_branches:
            return planned_branch

        branches = [
            branch
            for branch in available_branches
            if branch_matches_work_item(branch, work_item_id)
            and (
                not prefixes
                or version_prefix_from_branch(branch) in prefixes
            )
        ]
        if not branches and prefixes:
            branches = [
                branch
                for branch in available_branches
                if branch_matches_work_item(branch, work_item_id)
            ]
        return choose_branch_candidate(
            branches,
            planned_branch=planned_branch,
            selected_work_type=selected_work_type,
            work_item_id=work_item_id,
        )

    def _enrich_repository_state(
        self,
        portal: Dict[str, Any],
        items: List[Dict[str, Any]],
        *,
        remote_scan: bool = True,
    ) -> List[Dict[str, Any]]:
        if not items:
            return items

        if not remote_scan:
            for item in items:
                linked_work_item_prs = list(item.get("linked_work_item_prs", []) or [])
                linked_parent_prs = list(item.get("linked_parent_prs", []) or [])
                item["linked_target_work_item_prs"] = linked_work_item_prs
                item["linked_target_parent_prs"] = linked_parent_prs
                item["linked_work_item_pr_count"] = len(linked_work_item_prs)
                item["linked_parent_pr_count"] = len(linked_parent_prs)

                stored_branch_status = str(item.get("branch_status") or "")
                has_branch = bool(
                    stored_branch_status in {"created", "exists", "detected"}
                    and item.get("branch_name")
                )
                rerun_active = bool(item.get("rerun_active"))
                has_pr = bool(item.get("pr_status") in {"created", "exists"} and item.get("pr_id"))
                if not has_pr and not rerun_active and linked_work_item_prs:
                    linked_pr = linked_work_item_prs[0]
                    item["pr_status"] = "linked"
                    item["pr_id"] = linked_pr.get("id")
                    item["pr_url"] = linked_pr.get("url", "")
                    item["pr_match_source"] = "work-item-link"
                    has_pr = True
                elif not has_pr and not rerun_active and linked_parent_prs:
                    linked_pr = linked_parent_prs[0]
                    item["pr_status"] = "parent-linked"
                    item["pr_id"] = linked_pr.get("id")
                    item["pr_url"] = linked_pr.get("url", "")
                    item["pr_match_source"] = "parent-link"
                    has_pr = True

                item["effective_branch_name"] = str(item.get("branch_name") or "") if has_branch else ""
                item["has_branch"] = has_branch
                item["has_pr"] = has_pr
                item["pr_link_label"] = build_pr_link_label(str(item.get("pr_status") or ""))
                agent_green = str(item.get("agent_result_status") or "").strip().lower() in {"green_light", "ready_for_push", "success"}
                pushed = str(item.get("push_status") or "").strip().lower() == "pushed"
                item["can_create_branch"] = bool(item.get("selected_base_branch")) and not has_branch and not has_pr
                item["can_check_agent_result"] = bool(item.get("agent_result_path")) and has_branch and not has_pr
                item["can_commit_push"] = bool(agent_green and has_branch and not pushed and not has_pr)
                item["can_create_draft_pr"] = bool(item.get("selected_base_branch")) and has_branch and pushed and not has_pr
                item["can_launch_copilot"] = bool(item.get("copilot_workspace_path")) and has_branch and not has_pr
                item["can_start_rerun"] = bool(item.get("selected_base_branch")) and (has_pr or pushed or bool(item.get("branch_name")))
                item["plan_locked"] = has_branch or has_pr
                item["progress_steps"] = build_progress_steps(item)
                item["stage_label"] = summarize_automation_stage(item)
                item["is_auto_flow_active"] = is_auto_flow_active(item)
            return items

        client, repository_id = self._load_repository_context(portal)
        if not client or not repository_id:
            for item in items:
                stored_branch_exists = item.get("branch_status") in {"created", "exists"}
                has_branch = bool(stored_branch_exists and item.get("branch_name"))
                has_pr = bool(item.get("pr_status") in {"created", "exists"} and item.get("pr_id"))
                item["effective_branch_name"] = str(item.get("branch_name") or "") if has_branch else ""
                item["linked_target_work_item_prs"] = []
                item["linked_target_parent_prs"] = []
                item["linked_work_item_pr_count"] = len(item.get("linked_work_item_prs", []) or [])
                item["linked_parent_pr_count"] = len(item.get("linked_parent_prs", []) or [])
                item["has_branch"] = has_branch
                item["has_pr"] = has_pr
                item["pr_link_label"] = build_pr_link_label(str(item.get("pr_status") or ""))
                agent_green = str(item.get("agent_result_status") or "").strip().lower() in {"green_light", "ready_for_push", "success"}
                pushed = str(item.get("push_status") or "").strip().lower() == "pushed"
                item["can_create_branch"] = bool(item.get("selected_base_branch")) and not has_branch and not has_pr
                item["can_check_agent_result"] = bool(item.get("agent_result_path")) and has_branch and not has_pr
                item["can_commit_push"] = bool(agent_green and has_branch and not pushed and not has_pr)
                item["can_create_draft_pr"] = bool(item.get("selected_base_branch")) and has_branch and pushed and not has_pr
                item["can_launch_copilot"] = bool(item.get("copilot_workspace_path")) and has_branch and not has_pr
                item["can_start_rerun"] = bool(item.get("selected_base_branch")) and (has_pr or pushed or bool(item.get("branch_name")))
                item["plan_locked"] = has_branch or has_pr
                item["progress_steps"] = build_progress_steps(item)
                item["stage_label"] = summarize_automation_stage(item)
                item["is_auto_flow_active"] = is_auto_flow_active(item)
            return items

        refs_cache: Dict[str, List[str]] = {}
        branch_pr_scan_candidates: List[Dict[str, Any]] = []
        for item in items:
            stored_branch_status = str(item.get("branch_status") or "")
            stored_branch_name = str(item.get("branch_name") or "")

            linked_target_work_item_prs = self._filter_blocking_pull_request_links_for_repository(
                portal["repository"],
                client,
                list(item.get("linked_work_item_prs", []) or []),
                repository_id,
            )
            linked_target_parent_prs = self._filter_blocking_pull_request_links_for_repository(
                portal["repository"],
                client,
                list(item.get("linked_parent_prs", []) or []),
                repository_id,
            )
            item["linked_target_work_item_prs"] = linked_target_work_item_prs
            item["linked_target_parent_prs"] = linked_target_parent_prs
            item["linked_work_item_pr_count"] = len(item.get("linked_work_item_prs", []) or [])
            item["linked_parent_pr_count"] = len(item.get("linked_parent_prs", []) or [])

            has_pr = bool(item.get("pr_status") in {"created", "exists"} and item.get("pr_id"))
            if has_pr and self._stored_pull_request_is_abandoned(
                portal["repository"],
                client,
                repository_id,
                item.get("pr_id"),
            ):
                item["pr_status"] = ""
                item["pr_id"] = None
                item["pr_url"] = ""
                item["pr_match_source"] = ""
                has_pr = False
            rerun_active = bool(item.get("rerun_active"))
            if not has_pr and not rerun_active and linked_target_work_item_prs:
                linked_pr = linked_target_work_item_prs[0]
                item["pr_status"] = "linked"
                item["pr_id"] = linked_pr.get("id")
                item["pr_url"] = linked_pr.get("url", "")
                item["pr_match_source"] = "work-item-link"
                has_pr = True
            elif not has_pr and not rerun_active and linked_target_parent_prs:
                linked_pr = linked_target_parent_prs[0]
                item["pr_status"] = "parent-linked"
                item["pr_id"] = linked_pr.get("id")
                item["pr_url"] = linked_pr.get("url", "")
                item["pr_match_source"] = "parent-link"
                has_pr = True

            remote_branch_name = ""
            stored_branch_exists = stored_branch_status in {"created", "exists"}
            effective_branch_name = stored_branch_name if stored_branch_exists else ""
            has_branch = bool(effective_branch_name) and stored_branch_exists
            if not has_pr:
                if bool(item.get("rerun_active")):
                    remote_branch_name = stored_branch_name if stored_branch_exists else ""
                else:
                    remote_branch_name = self._detect_existing_branch_name(
                        client,
                        portal["repository"],
                        repository_id,
                        str(item.get("branch_name") or ""),
                        str(item.get("selected_base_branch") or item.get("inferred_base_branch") or ""),
                        str(item.get("selected_work_type") or item.get("inferred_work_type") or ""),
                        int(item["id"]),
                        refs_cache,
                        list(portal.get("branch_chain", []) or []),
                    )
                effective_branch_name = remote_branch_name or effective_branch_name
                has_branch = bool(effective_branch_name) and (
                    stored_branch_status in {"created", "exists"} or bool(remote_branch_name)
                )
                if remote_branch_name and stored_branch_status not in {"created", "exists"}:
                    item["branch_status"] = "detected"

            item["effective_branch_name"] = effective_branch_name
            item["has_branch"] = has_branch
            item["has_pr"] = has_pr
            if not has_pr and has_branch:
                branch_pr_scan_candidates.append(item)

        branch_pull_requests: Dict[str, Dict[str, Any]] = {}
        for candidate in branch_pr_scan_candidates:
            effective_branch_name = str(candidate.get("effective_branch_name") or "").strip()
            if not effective_branch_name:
                continue
            try:
                existing_pr = self._find_pull_request_for_branch_cached(
                    portal["repository"],
                    client,
                    repository_id,
                    effective_branch_name,
                )
            except Exception:
                existing_pr = None
            if existing_pr:
                branch_pull_requests[effective_branch_name] = existing_pr

        for item in items:
            has_branch = bool(item.get("has_branch"))
            has_pr = bool(item.get("has_pr"))
            effective_branch_name = str(item.get("effective_branch_name") or "")

            if has_branch and not has_pr and branch_pull_requests:
                existing_pr = branch_pull_requests.get(effective_branch_name)
                if existing_pr and not pull_request_is_abandoned(existing_pr):
                    item["pr_status"] = "exists"
                    item["pr_id"] = existing_pr.get("pullRequestId")
                    item["pr_url"] = (
                        existing_pr.get("_links", {}).get("web", {}).get("href")
                        or build_pr_web_url(
                            portal["base_url"],
                            portal["project"],
                            portal["repository"],
                            int(existing_pr.get("pullRequestId")),
                        )
                    )
                    item["pr_source_branch"] = str(existing_pr.get("sourceRefName") or "").replace("refs/heads/", "").strip()
                    item["pr_match_source"] = "branch"
                    has_pr = True

            item["has_pr"] = has_pr
            item["pr_link_label"] = build_pr_link_label(str(item.get("pr_status") or ""))
            agent_green = str(item.get("agent_result_status") or "").strip().lower() in {"green_light", "ready_for_push", "success"}
            pushed = str(item.get("push_status") or "").strip().lower() == "pushed"
            item["can_create_branch"] = bool(item.get("selected_base_branch")) and not has_branch and not has_pr
            item["can_check_agent_result"] = bool(item.get("agent_result_path")) and has_branch and not has_pr
            item["can_commit_push"] = bool(agent_green and has_branch and not pushed and not has_pr)
            item["can_create_draft_pr"] = bool(item.get("selected_base_branch")) and has_branch and pushed and not has_pr
            item["can_launch_copilot"] = bool(item.get("copilot_workspace_path")) and has_branch and not has_pr
            item["can_start_rerun"] = bool(item.get("selected_base_branch")) and (has_pr or pushed or bool(item.get("branch_name")))
            item["plan_locked"] = has_branch or has_pr
            item["progress_steps"] = build_progress_steps(item)
            item["stage_label"] = summarize_automation_stage(item)
            item["is_auto_flow_active"] = is_auto_flow_active(item)

        return items

    def _resolve_reviewer(self, assigned_to: Dict[str, Any], runtime_settings: Dict[str, Any]) -> Dict[str, str]:
        assigned_to = repair_identity(assigned_to)
        overrides = runtime_settings.get("reviewer_overrides", {}) or {}
        normalized_overrides = {
            normalize_match_token(key): value
            for key, value in overrides.items()
            if normalize_match_token(key)
        }
        match_tokens = [
            normalize_match_token(assigned_to.get("display_name", "")),
            normalize_match_token(assigned_to.get("unique_name", "")),
            normalize_match_token(assigned_to.get("id", "")),
        ]
        for token in match_tokens:
            if token and token in normalized_overrides:
                value = normalized_overrides[token]
                return {
                    "display_name": repair_text_encoding(value.get("display_name", "")),
                    "unique_name": repair_text_encoding(value.get("unique_name", "")),
                    "id": str(value.get("id", "")).strip(),
                    "source": "override",
                }

        if assigned_to.get("id") or assigned_to.get("unique_name"):
            return {
                "display_name": repair_text_encoding(assigned_to.get("display_name", "")),
                "unique_name": repair_text_encoding(assigned_to.get("unique_name", "")),
                "id": str(assigned_to.get("id", "")).strip(),
                "source": "work_item",
            }

        if runtime_settings.get("default_reviewer_unique_name") or runtime_settings.get("default_reviewer_id"):
            return {
                "display_name": repair_text_encoding(runtime_settings.get("default_reviewer_display_name", "")),
                "unique_name": repair_text_encoding(runtime_settings.get("default_reviewer_unique_name", "")),
                "id": str(runtime_settings.get("default_reviewer_id", "")).strip(),
                "source": "default",
            }

        return {
            "display_name": repair_text_encoding(assigned_to.get("display_name", "")),
            "unique_name": repair_text_encoding(assigned_to.get("unique_name", "")),
            "id": str(assigned_to.get("id", "")).strip(),
            "source": "unresolved",
        }

    def load_dashboard(self, portal_name: str = "", iteration_path: str = "") -> Dict[str, Any]:
        return self.load_dashboard_with_filters(
            portal_name=portal_name,
            iteration_path=iteration_path,
            current_iteration_only=None,
        )

    def load_dashboard_with_filters(
        self,
        *,
        portal_name: str = "",
        iteration_path: str = "",
        current_iteration_only: Optional[bool] = None,
        hide_closed: bool = False,
        page: int = 1,
        page_size: int = 10,
    ) -> Dict[str, Any]:
        config = load_app_config()
        runtime_settings = load_runtime_settings()
        portal_names = get_portal_names(config)
        active_portal_name = portal_name or config["DEFAULT_PORTAL"]
        if active_portal_name not in portal_names:
            active_portal_name = config["DEFAULT_PORTAL"]
        portal = get_portal_config(config, active_portal_name)
        dashboard_started_at = time.perf_counter()
        workspace_options = self.discover_target_workspaces(
            portal=portal,
            runtime_settings=runtime_settings,
        )

        auth_message = ""
        current_iteration = None
        iterations_error = ""
        work_items_error = ""
        selection_error = ""
        filter_notice = ""
        items: List[Dict[str, Any]] = []
        available_iterations: List[str] = []
        page = max(1, int(page or 1))
        page_size = max(5, min(50, int(page_size or 10)))

        work_item_client: Optional[TfsClient] = None
        try:
            work_item_client = self._build_work_item_client(portal)
        except ServiceError as exc:
            auth_message = str(exc)

        effective_current_iteration_only = (
            runtime_settings["default_current_iteration_only"]
            if current_iteration_only is None
            else bool(current_iteration_only)
        )
        work_item_team = portal.get("work_item_team") or portal.get("team") or ""
        work_item_area_path = portal.get("work_item_area_path") or ""
        if work_item_client and work_item_team and effective_current_iteration_only and not iteration_path.strip():
            try:
                with performance_span("dashboard.current_iteration", portal=portal["repository"], team=work_item_team):
                    current_iteration = self._get_current_iteration_cached(
                        portal["repository"],
                        work_item_client,
                        work_item_team,
                    )
            except Exception as exc:
                iterations_error = str(exc)

        selected_iteration = iteration_path.strip() or str((current_iteration or {}).get("path") or "").strip()
        members = runtime_settings.get("content_team_members", [])

        if work_item_client and not members:
            selection_error = (
                "No Content team members are configured. "
                "Add them in Settings before loading work items."
            )
        elif work_item_client and not work_item_area_path:
            selection_error = (
                "No discovery area path is configured. "
                "Add it in Settings before loading work items."
            )

        if effective_current_iteration_only and not selected_iteration:
            filter_notice = (
                "Current iteration filtering is enabled, but no iteration path could be resolved automatically. "
                "The dashboard is showing assigned tasks from the configured area. Enter an iteration path to narrow the list."
            )

        if work_item_client and members and work_item_area_path:
            try:
                query_members = build_query_assignee_candidates(members)
                query_iteration_path = selected_iteration if effective_current_iteration_only and selected_iteration else ""
                requested_top = page * page_size + 1
                with performance_span(
                    "dashboard.work_item_query",
                    portal=portal["repository"],
                    area=work_item_area_path,
                    iteration=query_iteration_path,
                    members=len(query_members),
                ):
                    live_items = self._get_assigned_work_items_cached(
                        portal["repository"],
                        work_item_client,
                        assignees=query_members,
                        filter_members=members,
                        top=requested_top,
                        work_item_types=["Task"],
                        area_path=work_item_area_path,
                        iteration_path=query_iteration_path,
                        exclude_closed=hide_closed,
                        include_details=False,
                    )
                if query_iteration_path:
                    available_iterations = [query_iteration_path]
                else:
                    available_iterations = sorted(
                        {
                            str(item.get("iteration_path", "")).strip()
                            for item in live_items
                            if str(item.get("iteration_path", "")).strip()
                        },
                        reverse=True,
                    )
                if effective_current_iteration_only and selected_iteration:
                    live_items = [
                        item
                        for item in live_items
                        if iteration_matches_filter(str(item.get("iteration_path", "")), selected_iteration)
                    ]
                if hide_closed:
                    live_items = [
                        item
                        for item in live_items
                        if str(item.get("state", "")).strip().lower() != "closed"
                    ]
                start_index = (page - 1) * page_size
                end_index = start_index + page_size
                has_next_page = len(live_items) > end_index
                paged_live_items = live_items[start_index:end_index]
                states = get_work_item_states(portal["repository"], [item["id"] for item in paged_live_items])
                items = [
                    self._decorate_work_item(item, portal, states.get(item["id"]), runtime_settings)
                    for item in paged_live_items
                ]
                with performance_span("dashboard.repository_enrichment", portal=portal["repository"], items=len(items), remote_scan=False):
                    items = self._enrich_repository_state(portal, items, remote_scan=False)
            except Exception as exc:
                work_items_error = str(exc)
                has_next_page = False
        else:
            has_next_page = False

        log_performance(
            "dashboard.load",
            (time.perf_counter() - dashboard_started_at) * 1000,
            portal=portal["repository"],
            items=len(items),
            current_iteration_only=effective_current_iteration_only,
            hide_closed=hide_closed,
            error=bool(work_items_error or auth_message or selection_error),
        )

        return {
            "config": config,
            "portal_names": portal_names,
            "portal": portal,
            "selected_portal": portal["repository"],
            "workspace_options": workspace_options,
            "selected_iteration": selected_iteration,
            "current_iteration": current_iteration,
            "work_item_project": portal["work_item_project"],
            "work_item_team": work_item_team,
            "work_item_area_path": work_item_area_path,
            "current_iteration_only": effective_current_iteration_only,
            "hide_closed": bool(hide_closed),
            "selection_error": selection_error,
            "filter_notice": filter_notice,
            "auth_message": auth_message,
            "iterations_error": iterations_error,
            "work_items_error": work_items_error,
            "has_pat": bool(_PORTAL_PATS.get(portal["repository"])),
            "runtime_settings": runtime_settings,
            "available_iterations": available_iterations,
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "has_previous": page > 1,
                "has_next": has_next_page,
                "previous_page": max(1, page - 1),
                "next_page": page + 1,
                "visible_count": len(items),
            },
        }

    def load_work_item_detail(
        self,
        *,
        portal_name: str,
        work_item_id: int,
    ) -> Dict[str, Any]:
        config = load_app_config()
        runtime_settings = load_runtime_settings()
        portal_names = get_portal_names(config)
        active_portal_name = portal_name or config["DEFAULT_PORTAL"]
        if active_portal_name not in portal_names:
            active_portal_name = config["DEFAULT_PORTAL"]
        portal = get_portal_config(config, active_portal_name)
        work_item = self._get_live_work_item(portal["repository"], int(work_item_id))
        state = get_work_item_states(portal["repository"], [int(work_item_id)]).get(int(work_item_id))
        ensure_work_item_events_from_state(
            portal=portal["repository"],
            work_item_id=int(work_item_id),
            state=state,
        )
        item = self._decorate_work_item(work_item, portal, state, runtime_settings)
        item = self._enrich_repository_state(portal, [item], remote_scan=True)[0]
        item["automation_events"] = list_work_item_events(
            portal["repository"],
            int(work_item_id),
            limit=75,
        )
        return {
            "config": config,
            "portal": portal,
            "selected_portal": portal["repository"],
            "runtime_settings": runtime_settings,
            "item": item,
        }

    def fetch_tfs_asset(
        self,
        *,
        portal_name: str,
        url: str,
    ) -> Dict[str, Any]:
        config = load_app_config()
        portal = get_portal_config(config, portal_name)
        asset_url = str(url or "").strip()
        if not asset_url:
            raise ServiceError("Asset URL is required.")

        normalized_base = normalize_base_url(portal["base_url"])
        if not asset_url.lower().startswith((normalized_base + "/").lower()):
            raise ServiceError("The requested asset is outside the configured TFS base URL.")

        client = self._build_work_item_client(portal)
        return client.get_binary(asset_url)

    def get_final_report(
        self,
        *,
        portal_name: str,
        work_item_id: int,
    ) -> Dict[str, Any]:
        config = load_app_config()
        portal_names = get_portal_names(config)
        active_portal_name = portal_name or config["DEFAULT_PORTAL"]
        if active_portal_name not in portal_names:
            active_portal_name = config["DEFAULT_PORTAL"]
        portal = get_portal_config(config, active_portal_name)
        state = get_work_item_states(portal["repository"], [int(work_item_id)]).get(int(work_item_id))
        if not state:
            raise ServiceError(f"No local automation state was found for WI {work_item_id}.")
        raw_report_path = str(state.get("final_report_path") or "").strip()
        if not raw_report_path:
            raise ServiceError(f"No final report has been created for WI {work_item_id}.")
        report_path = Path(raw_report_path)
        if not report_path.exists() or not report_path.is_file():
            raise ServiceError(f"The final report file does not exist: {report_path}")
        markdown_text = report_path.read_text(encoding="utf-8", errors="replace")
        return {
            "config": config,
            "portal": portal,
            "selected_portal": portal["repository"],
            "work_item_id": int(work_item_id),
            "report_path": str(report_path),
            "report_markdown": markdown_text,
            "report_html": render_basic_markdown(markdown_text),
        }

    def get_context_capture_package(
        self,
        *,
        portal_name: str,
        work_item_id: int,
        selected_file: str = "summary",
    ) -> Dict[str, Any]:
        config = load_app_config()
        runtime_settings = load_runtime_settings()
        portal_names = get_portal_names(config)
        active_portal_name = portal_name or config["DEFAULT_PORTAL"]
        if active_portal_name not in portal_names:
            active_portal_name = config["DEFAULT_PORTAL"]
        portal = get_portal_config(config, active_portal_name)
        state = get_work_item_states(portal["repository"], [int(work_item_id)]).get(int(work_item_id))
        if not state:
            raise ServiceError(f"No local automation state was found for WI {work_item_id}.")

        raw_context_path = str(state.get("copilot_context_path") or "").strip()
        if not raw_context_path:
            raise ServiceError(f"No agent context package has been created for WI {work_item_id}.")

        capture_files = {
            "summary": {"label": "Summary", "filename": "summary.md", "markdown": True},
            "instructions": {"label": "Instructions", "filename": "INSTRUCTIONS.md", "markdown": True},
            "manifest": {"label": "Manifest", "filename": "manifest.json", "markdown": False},
        }
        selected_key = selected_file if selected_file in capture_files else "summary"
        selected_definition = capture_files[selected_key]

        default_distro = str(runtime_settings.get("copilot_wsl_distro") or "").strip() or "Ubuntu"
        execution_runtime = str(runtime_settings.get("execution_runtime") or "devcontainer").strip()
        with execution_runtime_scope(execution_runtime):
            context_distro, normalized_context_path = normalize_wsl_target_path(raw_context_path, default_distro)
        if not normalized_context_path:
            raise ServiceError(f"The saved context path is not valid for WI {work_item_id}.")
        context_directory = normalized_context_path.rsplit("/", 1)[0]
        capture_directory = f"{context_directory}/capture"
        capture_path = f"{capture_directory}/{selected_definition['filename']}"
        try:
            with execution_runtime_scope(execution_runtime):
                content = read_wsl_text_file(context_distro, capture_path, max_chars=300000)
        except CopilotIntegrationError as exc:
            raise ServiceError(str(exc)) from exc

        return {
            "config": config,
            "portal": portal,
            "selected_portal": portal["repository"],
            "work_item_id": int(work_item_id),
            "selected_file": selected_key,
            "capture_files": capture_files,
            "capture_directory": capture_directory,
            "capture_path": capture_path,
            "capture_content": content,
            "capture_html": render_basic_markdown(content) if selected_definition["markdown"] else "",
            "is_markdown": bool(selected_definition["markdown"]),
        }

    def get_local_status_snapshots(
        self,
        *,
        portal_name: str,
        work_item_ids: List[int],
    ) -> List[Dict[str, Any]]:
        config = load_app_config()
        portal_names = get_portal_names(config)
        active_portal_name = portal_name or config["DEFAULT_PORTAL"]
        if active_portal_name not in portal_names:
            active_portal_name = config["DEFAULT_PORTAL"]
        portal = get_portal_config(config, active_portal_name)
        states = get_work_item_states(portal["repository"], work_item_ids)
        snapshots: List[Dict[str, Any]] = []
        for work_item_id in work_item_ids:
            state = states.get(int(work_item_id))
            if not state:
                continue
            branch_status = str(state.get("branch_status") or "")
            pr_status = str(state.get("pr_status") or "")
            snapshot = {
                **state,
                "id": int(work_item_id),
                "selected_base_branch": str(state.get("selected_base_branch") or ""),
                "branch_status": branch_status,
                "has_branch": branch_status in {"created", "exists", "detected"},
                "has_pr": pr_status in {"created", "exists"},
                "auto_flow_enabled": bool(state.get("auto_flow_enabled")),
                "copilot_status": str(state.get("copilot_status") or ""),
                "copilot_error": str(state.get("copilot_error") or ""),
                "agent_result_status": str(state.get("agent_result_status") or ""),
                "agent_result_error": str(state.get("agent_result_error") or ""),
                "push_status": str(state.get("push_status") or ""),
                "push_error": str(state.get("push_error") or ""),
                "pr_error": str(state.get("pr_error") or ""),
            }
            snapshot["is_auto_flow_active"] = is_auto_flow_active(snapshot)
            snapshots.append(
                {
                    "id": int(work_item_id),
                    "branch_status": branch_status,
                    "copilot_status": str(state.get("copilot_status") or ""),
                    "agent_result_status": str(state.get("agent_result_status") or ""),
                    "push_status": str(state.get("push_status") or ""),
                    "pr_status": pr_status,
                    "pr_id": state.get("pr_id"),
                    "pr_url": str(state.get("pr_url") or ""),
                    "final_report_path": str(state.get("final_report_path") or ""),
                    "has_pr": bool(snapshot["has_pr"]),
                    "auto_flow_enabled": bool(snapshot["auto_flow_enabled"]),
                    "is_auto_flow_active": bool(snapshot["is_auto_flow_active"]),
                    "stage_label": summarize_automation_stage(snapshot),
                    "progress_steps": build_progress_steps(snapshot),
                    "updated_at": str(state.get("updated_at") or ""),
                }
            )
        return snapshots

    def _get_live_work_item(self, portal_name: str, work_item_id: int) -> Dict[str, Any]:
        config = load_app_config()
        portal = get_portal_config(config, portal_name)
        client = self._build_work_item_client(portal)
        items = client.get_work_item_details([work_item_id])
        if not items:
            raise ServiceError(f"Work item {work_item_id} was not found.")
        return items[0]

    def _load_action_items(
        self,
        portal_name: str,
        work_item_ids: List[int],
    ) -> tuple[Dict[str, Any], Dict[int, Dict[str, Any]]]:
        ordered_ids = [int(work_item_id) for work_item_id in work_item_ids]
        if not ordered_ids:
            raise ServiceError("No work items were selected.")

        config = load_app_config()
        runtime_settings = load_runtime_settings()
        portal = get_portal_config(config, portal_name)
        client = self._build_work_item_client(portal)
        live_items = client.get_work_item_details(ordered_ids)
        states = get_work_item_states(portal["repository"], ordered_ids)
        decorated_items = [
            self._decorate_work_item(item, portal, states.get(item["id"]), runtime_settings)
            for item in live_items
        ]
        enriched_items = self._enrich_repository_state(portal, decorated_items)
        return portal, {int(item["id"]): item for item in enriched_items}

    def _get_action_item(self, portal_name: str, work_item_id: int) -> tuple[Dict[str, Any], Dict[str, Any]]:
        portal, items_by_id = self._load_action_items(portal_name, [work_item_id])
        item = items_by_id.get(int(work_item_id))
        if not item:
            raise ServiceError(f"Work item {work_item_id} was not found.")
        return portal, item

    def save_plan(
        self,
        *,
        portal_name: str,
        work_item_id: int,
        iteration_path: str,
        triage_status: str,
        selected_base_branch: str,
        work_type: str,
        planned_branch_name: str = "",
    ) -> Dict[str, Any]:
        config = load_app_config()
        runtime_settings = load_runtime_settings()
        portal = get_portal_config(config, portal_name)
        work_item = self._get_live_work_item(portal_name, work_item_id)
        existing_state = get_work_item_states(portal["repository"], [work_item_id]).get(work_item_id)
        decorated = self._decorate_work_item(work_item, portal, existing_state, runtime_settings)
        selected_branch = selected_base_branch.strip() or decorated["selected_base_branch"]
        selected_work_type = work_type.strip().lower() or decorated["selected_work_type"]
        if selected_work_type not in WORK_TYPES:
            raise ServiceError(f"Unsupported work type '{selected_work_type}'.")
        if selected_branch and selected_branch not in portal["branch_chain"]:
            raise ServiceError(f"Base branch '{selected_branch}' is not part of the configured branch chain.")

        saved = {
            **decorated,
            "triage_status": triage_status.strip() or "pending",
            "selected_base_branch": selected_branch,
            "selected_work_type": selected_work_type,
        }
        generated_branch_name = merge_branch_plan(
            {
                **work_item,
                "id": work_item["id"],
                "title": work_item["title"],
                "type": work_item["type"],
                "parent_type": work_item["parent_type"],
                "tags": work_item["tags"],
                "iteration_path": work_item["iteration_path"],
            },
            portal["branch_chain"],
            {
                "selected_base_branch": selected_branch,
                "work_type": selected_work_type,
            },
        )["generated_branch_name"]
        saved["generated_branch_name"] = generated_branch_name
        saved["branch_name"] = validate_planned_branch_name(planned_branch_name) or generated_branch_name

        reviewer_display_name = saved["reviewer_display_name"]
        reviewer_unique_name = saved["reviewer_unique_name"]
        reviewer_id = saved["reviewer_id"]

        save_work_item_plan(
            portal=portal_name,
            work_item_id=work_item_id,
            iteration_path=iteration_path.strip() or work_item["iteration_path"],
            triage_status=saved["triage_status"],
            selected_base_branch=selected_branch,
            work_type=selected_work_type,
            branch_name=saved["branch_name"],
            reviewer_display_name=reviewer_display_name,
            reviewer_unique_name=reviewer_unique_name,
            reviewer_id=reviewer_id,
        )
        return saved

    def save_runtime_settings(
        self,
        *,
        server_host: str,
        server_port: int,
        auto_port: bool,
        tfs_request_timeout_seconds: int,
        tfs_verify_ssl: bool,
        tfs_ca_bundle_path: str,
        automation_runner_enabled: bool,
        automation_reconcile_interval_seconds: int,
        automation_continuous_mode: bool,
        automation_discovery_interval_minutes: int,
        content_team_members_text: str,
        default_current_iteration_only: bool,
        execution_runtime: str,
        copilot_wsl_distro: str,
        copilot_provider: str,
        copilot_model_name: str,
        copilot_agent_name: str,
        copilot_auto_launch: bool,
        copilot_prompt_template: str,
        copilot_cli_command_template: str,
        final_reports_path: str,
        copilot_desktop_url: str,
        copilot_reference_docs_path: str,
        copilot_strict_model_safety: bool,
        copilot_open_wsl_remote: bool,
        copilot_vscode_window_mode: str,
        copilot_vscode_apply_settings: bool,
        copilot_vscode_settings_path: str,
        copilot_vscode_permission_level: str,
        copilot_vscode_global_auto_approve: bool,
        copilot_vscode_auto_accept_edits_delay_ms: int,
        copilot_additional_read_access_folders_text: str,
        context_capture_enabled: bool,
        context_capture_root_mode: str,
        context_capture_max_tree_items: int,
        context_capture_include_pr_diffs: bool,
        context_capture_workspace_scan_roots_text: str,
        default_reviewer_display_name: str,
        default_reviewer_unique_name: str,
        default_reviewer_id: str,
        reviewer_overrides_text: str,
    ) -> Dict[str, Any]:
        try:
            saved = save_runtime_settings_file(
                server_host=server_host,
                server_port=server_port,
                auto_port=auto_port,
                tfs_request_timeout_seconds=tfs_request_timeout_seconds,
                tfs_verify_ssl=tfs_verify_ssl,
                tfs_ca_bundle_path=tfs_ca_bundle_path,
                automation_runner_enabled=automation_runner_enabled,
                automation_reconcile_interval_seconds=automation_reconcile_interval_seconds,
                automation_continuous_mode=automation_continuous_mode,
                automation_discovery_interval_minutes=automation_discovery_interval_minutes,
                content_team_members_text=content_team_members_text,
                default_current_iteration_only=default_current_iteration_only,
                execution_runtime=execution_runtime,
                copilot_wsl_distro=copilot_wsl_distro,
                copilot_provider=copilot_provider,
                copilot_model_name=copilot_model_name,
                copilot_agent_name=copilot_agent_name,
                copilot_auto_launch=copilot_auto_launch,
                copilot_prompt_template=copilot_prompt_template,
                copilot_cli_command_template=copilot_cli_command_template,
                final_reports_path=final_reports_path,
                copilot_desktop_url=copilot_desktop_url,
                copilot_reference_docs_path=copilot_reference_docs_path,
                copilot_strict_model_safety=copilot_strict_model_safety,
                copilot_open_wsl_remote=copilot_open_wsl_remote,
                copilot_vscode_window_mode=copilot_vscode_window_mode,
                copilot_vscode_apply_settings=copilot_vscode_apply_settings,
                copilot_vscode_settings_path=copilot_vscode_settings_path,
                copilot_vscode_permission_level=copilot_vscode_permission_level,
                copilot_vscode_global_auto_approve=copilot_vscode_global_auto_approve,
                copilot_vscode_auto_accept_edits_delay_ms=copilot_vscode_auto_accept_edits_delay_ms,
                copilot_additional_read_access_folders_text=copilot_additional_read_access_folders_text,
                context_capture_enabled=context_capture_enabled,
                context_capture_root_mode=context_capture_root_mode,
                context_capture_max_tree_items=context_capture_max_tree_items,
                context_capture_include_pr_diffs=context_capture_include_pr_diffs,
                context_capture_workspace_scan_roots_text=context_capture_workspace_scan_roots_text,
                default_reviewer_display_name=default_reviewer_display_name,
                default_reviewer_unique_name=default_reviewer_unique_name,
                default_reviewer_id=default_reviewer_id,
                reviewer_overrides_text=reviewer_overrides_text,
            )
            if saved.get("copilot_vscode_apply_settings"):
                self._apply_vscode_copilot_settings(
                    config=load_app_config(),
                    runtime_settings=saved,
                )
            saved["_agent_provider_preflight"] = self._check_agent_provider_prerequisites(saved)
            _CACHE.clear()
            return saved
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc

    def _check_agent_provider_prerequisites(
        self,
        runtime_settings: Dict[str, Any],
        *,
        workspace_path: str = "",
    ) -> Dict[str, Any]:
        provider = str(runtime_settings.get("copilot_provider") or "").strip()
        if provider not in {"vscode_bridge", "vscode", "codex_cli", "claude_cli", "custom_cli"}:
            return {
                "status": "skipped",
                "ok": True,
                "message": "No CLI provider preflight is required for the selected agent provider.",
            }
        execution_runtime = str(runtime_settings.get("execution_runtime") or "devcontainer").strip()
        try:
            with execution_runtime_scope(execution_runtime):
                preflight = check_agent_provider_prerequisites(
                    distro=str(runtime_settings.get("copilot_wsl_distro") or "").strip(),
                    provider=provider,
                    cli_command_template=str(runtime_settings.get("copilot_cli_command_template") or "").strip(),
                    workspace_path=workspace_path,
                    model_name=str(runtime_settings.get("copilot_model_name") or "").strip(),
                )
                message = str(preflight.get("message") or "").lower()
                if (
                    provider == "codex_cli"
                    and not bool(preflight.get("ok"))
                    and ("credential" in message or "auth" in message or "login" in message)
                ):
                    preflight["login"] = start_codex_device_login(
                        distro=str(runtime_settings.get("copilot_wsl_distro") or "").strip(),
                    )
                return preflight
        except CopilotIntegrationError as exc:
            return {
                "status": "error",
                "ok": False,
                "message": str(exc),
            }

    def save_portal_settings(
        self,
        *,
        current_repository: str,
        base_url: str,
        project: str,
        repository: str,
        work_item_project: str,
        work_item_team: str,
        work_item_area_path: str,
        copilot_workspace_path: str,
        api_version: str,
        branch_chain_text: str,
        auth_mode: str,
        lookback_days: int,
        max_prs_per_branch: int,
        verify_work_items_via_api: bool,
        cherry_pick_skip_labels_text: str = "",
    ) -> Dict[str, Any]:
        if auth_mode not in AUTH_OPTIONS:
            raise ServiceError(f"Unsupported authentication mode '{auth_mode}'.")
        try:
            config = load_app_config()
            saved = save_portal_config(
                config,
                current_repository,
                {
                    "base_url": base_url,
                    "project": project,
                    "repository": repository,
                    "work_item_project": work_item_project,
                    "work_item_team": work_item_team,
                    "work_item_area_path": work_item_area_path,
                    "copilot_workspace_path": copilot_workspace_path,
                    "api_version": api_version,
                    "branch_chain": branch_chain_text,
                    "auth_mode": auth_mode,
                    "lookback_days": int(lookback_days),
                    "max_prs_per_branch": int(max_prs_per_branch),
                    "verify_work_items_via_api": bool(verify_work_items_via_api),
                    "cherry_pick_skip_labels": cherry_pick_skip_labels_text,
                },
            )
            self._invalidate_portal_runtime_cache(current_repository)
            self._invalidate_portal_runtime_cache(saved["repository"])
            saved["_git_credentials_preflight"] = _check_git_credentials_for_portal(saved)
            return saved
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc

    def save_portal_workspace(
        self,
        *,
        portal_name: str,
        copilot_workspace_path: str,
    ) -> Dict[str, Any]:
        workspace_path = str(copilot_workspace_path or "").strip()
        if not workspace_path:
            raise ServiceError("Select or enter a target workspace path before saving.")
        try:
            config = load_app_config()
            portal = get_portal_config(config, portal_name or config["DEFAULT_PORTAL"])
            payload = dict(portal)
            payload["copilot_workspace_path"] = workspace_path
            saved = save_portal_config(config, portal["repository"], payload)
            self._invalidate_portal_runtime_cache(portal["repository"])
            self._invalidate_portal_runtime_cache(saved["repository"])
            return saved
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc

    def start_rerun_automatic_flow(
        self,
        *,
        portal_name: str,
        work_item_id: int,
        iteration_path: str,
        selected_base_branch: str = "",
        work_type: str = "",
    ) -> Dict[str, Any]:
        portal, current_item = self._get_action_item(portal_name, work_item_id)
        base_branch = (
            selected_base_branch.strip()
            or str(current_item.get("selected_base_branch") or "").strip()
            or str(current_item.get("inferred_base_branch") or "").strip()
        )
        if not base_branch:
            raise ServiceError("Select a base branch before starting a rerun.")

        selected_work_type = (
            work_type.strip().lower()
            or str(current_item.get("selected_work_type") or "").strip().lower()
            or str(current_item.get("inferred_work_type") or "").strip().lower()
            or "task"
        )
        if selected_work_type not in WORK_TYPES:
            selected_work_type = "task"

        generated_plan = merge_branch_plan(
            current_item,
            portal["branch_chain"],
            {
                "selected_base_branch": base_branch,
                "work_type": selected_work_type,
            },
        )
        rerun_branch_name = build_rerun_branch_name(str(generated_plan["generated_branch_name"]))
        self.save_plan(
            portal_name=portal_name,
            work_item_id=work_item_id,
            iteration_path=iteration_path,
            triage_status="rerun",
            selected_base_branch=base_branch,
            work_type=selected_work_type,
            planned_branch_name=rerun_branch_name,
        )
        start_rerun_state(
            portal=portal_name,
            work_item_id=work_item_id,
            selected_base_branch=base_branch,
            work_type=selected_work_type,
            branch_name=rerun_branch_name,
            triage_status="rerun",
        )
        self._invalidate_portal_repository_cache(portal_name)

        result = self.run_bulk_auto_flow(
            portal_name=portal_name,
            work_item_ids=[work_item_id],
            iteration_path=iteration_path,
        )
        first_result = (list(result.get("results") or []) or [{}])[0]
        return {
            "status": str(first_result.get("status") or "started"),
            "detail": str(first_result.get("detail") or ""),
            "branch_name": rerun_branch_name,
            "result": result,
        }

    def run_bulk_auto_flow(
        self,
        *,
        portal_name: str,
        work_item_ids: List[int],
        iteration_path: str,
    ) -> Dict[str, Any]:
        ordered_ids: List[int] = []
        seen_ids = set()
        for work_item_id in work_item_ids:
            normalized_id = int(work_item_id)
            if normalized_id in seen_ids:
                continue
            seen_ids.add(normalized_id)
            ordered_ids.append(normalized_id)

        if not ordered_ids:
            raise ServiceError("Select at least one work item before starting the automatic flow.")

        _, items_by_id = self._load_action_items(portal_name, ordered_ids)
        runtime_settings = load_runtime_settings()
        runtime_provider = str(runtime_settings.get("copilot_provider") or "").strip()
        execution_runtime = str(runtime_settings.get("execution_runtime") or "devcontainer").strip()
        provider_requires_process = runtime_provider in {"codex_cli", "claude_cli", "custom_cli"}
        results: List[Dict[str, Any]] = []

        for work_item_id in ordered_ids:
            item = items_by_id.get(work_item_id)
            if not item:
                results.append(
                    {
                        "work_item_id": work_item_id,
                        "status": "error",
                        "detail": "The work item could not be loaded.",
                    }
                )
                continue

            selected_base_branch = str(item.get("selected_base_branch") or "")
            selected_work_type = str(item.get("selected_work_type") or "task")
            triage_status = str(item.get("triage_status") or "pending")

            if not selected_base_branch:
                results.append(
                    {
                        "work_item_id": work_item_id,
                        "status": "needs-plan",
                        "detail": "No base branch is selected for this work item.",
                    }
                )
                continue

            if item.get("has_pr"):
                pr_id = item.get("pr_id")
                detail = f"Associated PR #{pr_id} already exists." if pr_id else "An associated PR already exists."
                mark_pr_result(
                    portal=portal_name,
                    work_item_id=work_item_id,
                    pr_status="exists",
                    pr_id=int(pr_id) if pr_id else None,
                    pr_url=str(item.get("pr_url") or ""),
                    pr_error="",
                )
                results.append(
                    {
                        "work_item_id": work_item_id,
                        "status": "already-has-pr",
                        "detail": detail,
                    }
                )
                continue

            plan = self.save_plan(
                portal_name=portal_name,
                work_item_id=work_item_id,
                iteration_path=iteration_path,
                triage_status=triage_status,
                selected_base_branch=selected_base_branch,
                work_type=selected_work_type,
                planned_branch_name=str(item.get("branch_name") or ""),
            )
            selected_base_branch = str(plan.get("selected_base_branch") or selected_base_branch)
            selected_work_type = str(plan.get("selected_work_type") or selected_work_type)
            triage_status = str(plan.get("triage_status") or triage_status)
            mark_auto_flow_enabled(
                portal=portal_name,
                work_item_id=work_item_id,
                enabled=True,
            )
            item["auto_flow_enabled"] = True

            branch_detail = ""
            if item.get("has_branch"):
                branch_detail = "Branch already available."
            else:
                try:
                    branch_result = self.create_branch(
                        portal_name=portal_name,
                        work_item_id=work_item_id,
                        iteration_path=iteration_path,
                        triage_status=triage_status,
                        selected_base_branch=selected_base_branch,
                        work_type=selected_work_type,
                        planned_branch_name=str(plan.get("branch_name") or item.get("branch_name") or ""),
                    )
                    branch_detail = (
                        f"Branch {branch_result['name']} created."
                        if branch_result["status"] == "created"
                        else f"Branch {branch_result['name']} already existed."
                    )
                except Exception as exc:
                    mark_auto_flow_enabled(
                        portal=portal_name,
                        work_item_id=work_item_id,
                        enabled=False,
                    )
                    results.append(
                        {
                            "work_item_id": work_item_id,
                            "status": "branch-error",
                            "detail": str(exc),
                        }
                    )
                    continue

            try:
                self._schedule_auto_completion(
                    portal_name=portal_name,
                    work_item_id=work_item_id,
                    iteration_path=iteration_path,
                    triage_status=triage_status,
                    selected_base_branch=selected_base_branch,
                    work_type=selected_work_type,
                    planned_branch_name=str(plan.get("branch_name") or item.get("branch_name") or ""),
                )
                workspace_path = str(item.get("copilot_workspace_path") or "").strip()
                workspace_detail = f" for workspace {workspace_path}" if workspace_path else ""
                results.append(
                    {
                        "work_item_id": work_item_id,
                        "status": "queued",
                        "detail": (
                            f"{branch_detail} Queued automatic flow{workspace_detail}. "
                            "The runner serializes work items that share the same target repository clone."
                        ).strip(),
                    }
                )
            except Exception as exc:
                mark_auto_flow_enabled(
                    portal=portal_name,
                    work_item_id=work_item_id,
                    enabled=False,
                )
                results.append(
                    {
                        "work_item_id": work_item_id,
                        "status": "automation-error",
                        "detail": str(exc),
                    }
                )

        summary = {
            "completed": sum(1 for result in results if result["status"] == "completed"),
            "already_has_pr": sum(1 for result in results if result["status"] == "already-has-pr"),
            "needs_plan": sum(1 for result in results if result["status"] == "needs-plan"),
            "queued": sum(1 for result in results if result["status"] == "queued"),
            "agent_running": sum(1 for result in results if result["status"] in {"agent-running", "waiting-for-agent"}),
            "errors": sum(1 for result in results if result["status"] in {"error", "branch-error", "automation-error", "agent-error"}),
        }
        return {
            "total": len(ordered_ids),
            "summary": summary,
            "results": results,
        }

    def resume_persisted_auto_flows(self) -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []
        for state in list_auto_flow_states():
            portal_name = str(state.get("portal") or "").strip()
            work_item_id = int(state.get("work_item_id") or 0)
            if not portal_name or not work_item_id:
                continue
            try:
                result = self.run_bulk_auto_flow(
                    portal_name=portal_name,
                    work_item_ids=[work_item_id],
                    iteration_path=str(state.get("iteration_path") or ""),
                )
                results.extend(list(result.get("results") or []))
            except Exception as exc:
                results.append(
                    {
                        "work_item_id": work_item_id,
                        "status": "automation-error",
                        "detail": str(exc),
                    }
                )
        return {
            "total": len(results),
            "results": results,
        }

    def start_automatic_flow_for_discovered_items(self) -> Dict[str, Any]:
        config = load_app_config()
        results: List[Dict[str, Any]] = []
        skipped_portals: List[str] = []
        for portal in list(config.get("portals", []) or []):
            portal_name = str((portal or {}).get("repository") or "").strip()
            if not portal_name:
                continue
            dashboard = self.load_dashboard_with_filters(
                portal_name=portal_name,
                iteration_path="",
                current_iteration_only=True,
                hide_closed=True,
            )
            if not str(dashboard.get("selected_iteration") or "").strip():
                skipped_portals.append(portal_name)
                continue
            candidate_ids = [
                int(item["id"])
                for item in list(dashboard.get("items") or [])
                if not item.get("auto_flow_enabled")
                and not item.get("has_pr")
                and bool(item.get("selected_base_branch"))
            ]
            if not candidate_ids:
                continue
            result = self.run_bulk_auto_flow(
                portal_name=portal_name,
                work_item_ids=candidate_ids,
                iteration_path=str(dashboard.get("selected_iteration") or ""),
            )
            results.extend(list(result.get("results") or []))
        return {
            "total": len(results),
            "results": results,
            "skipped_portals": skipped_portals,
        }

    def create_branch(
        self,
        *,
        portal_name: str,
        work_item_id: int,
        iteration_path: str,
        triage_status: str,
        selected_base_branch: str,
        work_type: str,
        planned_branch_name: str = "",
    ) -> Dict[str, Any]:
        plan = self.save_plan(
            portal_name=portal_name,
            work_item_id=work_item_id,
            iteration_path=iteration_path,
            triage_status=triage_status,
            selected_base_branch=selected_base_branch,
            work_type=work_type,
            planned_branch_name=planned_branch_name,
        )
        if not plan["selected_base_branch"]:
            raise ServiceError("Select a base branch before creating a work branch.")

        portal, current_item = self._get_action_item(portal_name, work_item_id)
        if current_item.get("has_pr"):
            pull_request_id = current_item.get("pr_id")
            if pull_request_id:
                raise ServiceError(f"WI {work_item_id} already has an associated PR #{pull_request_id}.")
            raise ServiceError(f"WI {work_item_id} already has an associated PR.")
        client = self._build_client(portal)
        repository_id = self._get_repository_id_cached(portal_name, client)
        refs_cache: Dict[str, List[str]] = {}
        if current_item.get("rerun_active"):
            exact_ref = client.get_ref(repository_id, str(plan["branch_name"]))
            existing_branch_name = str(plan["branch_name"]) if exact_ref else ""
        else:
            existing_branch_name = self._detect_existing_branch_name(
                client,
                portal_name,
                repository_id,
                str(plan["branch_name"]),
                str(plan["selected_base_branch"]),
                str(plan["selected_work_type"]),
                int(work_item_id),
                refs_cache,
                list(portal.get("branch_chain", []) or []),
            )
        if existing_branch_name:
            existing_ref = client.get_ref(repository_id, existing_branch_name)
            mark_branch_result(
                portal=portal_name,
                work_item_id=work_item_id,
                branch_name=str(existing_branch_name),
                branch_status="exists",
                branch_error="",
            )
            self._invalidate_portal_repository_cache(portal_name)
            return {
                "status": "exists",
                "name": str(existing_branch_name),
                "object_id": str((existing_ref or {}).get("objectId", "")),
            }
        try:
            result = client.create_branch(
                repository_id,
                str(plan["branch_name"]),
                str(plan["selected_base_branch"]),
            )
        except Exception as exc:
            mark_branch_result(
                portal=portal_name,
                work_item_id=work_item_id,
                branch_name=str(plan["branch_name"]),
                branch_status="error",
                branch_error=str(exc),
            )
            raise

        mark_branch_result(
            portal=portal_name,
            work_item_id=work_item_id,
            branch_name=str(plan["branch_name"]),
            branch_status=str(result["status"]),
            branch_error="",
        )
        self._invalidate_portal_repository_cache(portal_name)
        return result

    def create_draft_pr(
        self,
        *,
        portal_name: str,
        work_item_id: int,
        iteration_path: str,
        triage_status: str,
        selected_base_branch: str,
        work_type: str,
        planned_branch_name: str = "",
    ) -> Dict[str, Any]:
        plan = self.save_plan(
            portal_name=portal_name,
            work_item_id=work_item_id,
            iteration_path=iteration_path,
            triage_status=triage_status,
            selected_base_branch=selected_base_branch,
            work_type=work_type,
            planned_branch_name=planned_branch_name,
        )
        if not plan["selected_base_branch"]:
            raise ServiceError("Select a base branch before creating a draft PR.")
        if not plan["branch_name"]:
            raise ServiceError("The work branch name could not be generated.")

        portal, current_item = self._get_action_item(portal_name, work_item_id)
        if current_item.get("has_pr"):
            pull_request_id = current_item.get("pr_id")
            if pull_request_id:
                raise ServiceError(f"WI {work_item_id} already has an associated PR #{pull_request_id}.")
            raise ServiceError(f"WI {work_item_id} already has an associated PR.")
        if str(current_item.get("push_status") or "").strip().lower() != "pushed":
            raise ServiceError("Commit and push the agent-approved changes before creating the draft PR.")
        client = self._build_client(portal)
        repository = self._get_repository_cached(portal_name, client)
        repository_id = str(repository["id"])
        repository_project_id = str((repository.get("project") or {}).get("id") or "").strip()
        source_branch = str(plan["branch_name"])
        target_branch = str(plan["selected_base_branch"])
        source_ref = client.get_ref(repository_id, source_branch)
        if not source_ref:
            refs_cache: Dict[str, List[str]] = {}
            detected_branch_name = self._detect_existing_branch_name(
                client,
                portal_name,
                repository_id,
                source_branch,
                target_branch,
                str(plan["selected_work_type"]),
                int(work_item_id),
                refs_cache,
                list(portal.get("branch_chain", []) or []),
            )
            if detected_branch_name:
                source_branch = detected_branch_name
                source_ref = client.get_ref(repository_id, source_branch)
                mark_branch_result(
                    portal=portal_name,
                    work_item_id=work_item_id,
                    branch_name=source_branch,
                    branch_status="exists",
                    branch_error="",
                )
        if not source_ref:
            raise ServiceError("Create the work branch before creating the draft PR.")

        existing_pr = client.find_pull_request(
            repository_id,
            source_branch,
            target_branch,
            statuses=["active", "completed"],
        )
        reviewer_id = str(plan["reviewer_id"])
        reviewer_unique_name = str(plan["reviewer_unique_name"])

        try:
            if existing_pr:
                pr_payload = existing_pr
                pr_status = "exists"
            else:
                pr_payload = client.create_pull_request(
                    repository_id,
                    source_branch=source_branch,
                    target_branch=target_branch,
                    title=f"WI {work_item_id}: {plan['title']}",
                    description=self._build_draft_pr_description(plan, portal, current_item),
                    is_draft=True,
                )
                pr_status = "created"

            pull_request_id = int(pr_payload["pullRequestId"])
            linked_work_item_id = int(current_item.get("parent_id") or current_item["id"])
            if not repository_project_id:
                raise ServiceError("The repository project ID is required to link the PR to the parent work item.")
            work_item_client = self._build_work_item_client(portal)
            work_item_client.add_pull_request_work_item_link(
                linked_work_item_id,
                project_id=repository_project_id,
                repository_id=repository_id,
                pull_request_id=pull_request_id,
            )
            client.add_required_reviewer(
                repository_id,
                pull_request_id,
                reviewer_id=reviewer_id,
                reviewer_unique_name=reviewer_unique_name,
            )
            pr_url = pr_payload.get("_links", {}).get("web", {}).get("href") or build_pr_web_url(
                portal["base_url"],
                portal["project"],
                portal["repository"],
                pull_request_id,
            )
        except Exception as exc:
            mark_pr_result(
                portal=portal_name,
                work_item_id=work_item_id,
                pr_status="error",
                pr_error=str(exc),
            )
            raise

        mark_pr_result(
            portal=portal_name,
            work_item_id=work_item_id,
            pr_status=pr_status,
            pr_id=pull_request_id,
            pr_url=str(pr_url),
            pr_error="",
        )
        self._invalidate_portal_repository_cache(portal_name)
        return {
            "status": pr_status,
            "pull_request_id": pull_request_id,
            "url": str(pr_url),
        }

    def _resolve_draft_pr_summary(self, portal: Dict[str, Any], current_item: Dict[str, Any]) -> str:
        stored_summary = str(current_item.get("agent_result_summary") or "").strip()
        if stored_summary:
            return stored_summary

        result_path = str(current_item.get("agent_result_path") or "").strip()
        if result_path:
            runtime_settings = load_runtime_settings()
            workspace_path = str(
                current_item.get("copilot_workspace_path")
                or portal.get("copilot_workspace_path")
                or ""
            ).strip()
            distro = str(runtime_settings.get("copilot_wsl_distro") or "").strip()
            execution_runtime = str(runtime_settings.get("execution_runtime") or "devcontainer").strip()
            with execution_runtime_scope(execution_runtime):
                effective_distro, _ = normalize_wsl_target_path(workspace_path, distro)
                result = read_agent_result(effective_distro, result_path)
            result_summary = str(result.get("summary") or "").strip()
            if result_summary:
                return result_summary

        raise ServiceError("The agent result must include a concise summary before creating the draft PR.")

    def _resolve_final_report_text(self, current_item: Dict[str, Any]) -> str:
        report_path = str(current_item.get("final_report_path") or "").strip()
        if report_path:
            candidate = Path(report_path).expanduser()
            if candidate.exists() and candidate.is_file():
                try:
                    return candidate.read_text(encoding="utf-8").strip()
                except OSError:
                    return ""
        return ""

    def _build_draft_pr_description(
        self,
        plan: Dict[str, Any],
        portal: Dict[str, Any],
        current_item: Dict[str, Any],
    ) -> str:
        change_summary = self._resolve_draft_pr_summary(portal, current_item)
        final_report = self._resolve_final_report_text(current_item)
        footer_lines = ["", f"Work item link: {plan['url']}"]
        footer = "\n".join(footer_lines)

        report_sections: Dict[str, str] = {}
        if final_report:
            report_sections = markdown_h2_sections(final_report)

        summary_text = report_sections.get("Summary") or change_summary
        description_sections: List[str] = []
        for section_title, section_body in [
            ("Work Item", report_sections.get("Work Item", "")),
            ("Summary", summary_text),
            ("Changes Made", report_sections.get("Changes Made", "")),
            ("Why These Changes Were Made", report_sections.get("Why These Changes Were Made", "")),
            ("Changed Files", report_sections.get("Changed Files", "")),
            ("Spec References", report_sections.get("Spec References", "")),
        ]:
            clean_body = str(section_body or "").strip()
            if not clean_body:
                continue
            description_sections.append(f"## {section_title}\n\n{clean_body}")

        description = "\n\n".join(description_sections).strip()
        if not description:
            description = "## Summary\n\n" + change_summary

        description += footer
        if len(description) > TFS_PULL_REQUEST_DESCRIPTION_LIMIT:
            description = (
                truncate_text(
                    description[: -len(footer)] if footer else description,
                    TFS_PULL_REQUEST_DESCRIPTION_LIMIT - len(footer),
                    "Description truncated in PR description.",
                )
                + footer
            )
        return description[:TFS_PULL_REQUEST_DESCRIPTION_LIMIT]

    def _write_final_report(
        self,
        *,
        portal_name: str,
        current_item: Dict[str, Any],
        agent_result: Dict[str, Any],
    ) -> str:
        runtime_settings = load_runtime_settings()
        raw_base_path = str(runtime_settings.get("final_reports_path") or "").strip()
        base_path = Path(raw_base_path).expanduser() if raw_base_path else DATA_DIR / "reports"
        if not base_path.is_absolute():
            base_path = DATA_DIR.parent / base_path
        work_item_id = int(current_item["id"])
        parent_id = current_item.get("parent_id")
        folder_label = (
            f"{parent_id} - Task {work_item_id}"
            if parent_id
            else f"Task {work_item_id}"
        )
        report_directory = base_path / safe_path_segment(folder_label, f"Task {work_item_id}")
        rerun_started_at = str(current_item.get("rerun_started_at") or "").strip()
        if rerun_started_at:
            rerun_label = safe_path_segment(
                f"rerun {rerun_started_at} {current_item.get('effective_branch_name') or current_item.get('branch_name') or ''}",
                "rerun",
            )
            report_directory = report_directory / rerun_label
        report_directory.mkdir(parents=True, exist_ok=True)
        report_path = report_directory / "final-report.md"

        final_report = agent_result.get("final_report") or {}
        summary = str(agent_result.get("summary") or current_item.get("agent_result_summary") or "").strip()
        if isinstance(final_report, dict):
            changes = (
                final_report.get("changes")
                or final_report.get("what_changed")
                or final_report.get("changed")
                or summary
            )
            rationale = (
                final_report.get("rationale")
                or final_report.get("why")
                or final_report.get("why_changed")
                or DEFAULT_RATIONALE_TEXT
            )
        else:
            changes = final_report or summary
            rationale = DEFAULT_RATIONALE_TEXT

        changed_files = list(agent_result.get("changed_files") or [])
        report_lines = [
            "# Final Automation Report",
            "",
            "## Work Item",
            f"- Task: {work_item_id}",
            f"- Parent: {parent_id or 'None'}",
            f"- Title: {current_item.get('title') or '-'}",
            f"- Portal: {portal_name}",
            f"- Branch: {current_item.get('effective_branch_name') or current_item.get('branch_name') or '-'}",
            "",
            "## Summary",
            summary or "No summary was reported by the agent.",
            "",
            "## Changes Made",
            format_report_value(changes),
            "",
            "## Why These Changes Were Made",
            format_report_value(rationale),
            "",
            "## Changed Files",
            format_report_value(changed_files),
            "",
            "## Spec References",
            format_spec_references(agent_result.get("spec_references")),
            "",
            "## Captured Evidence Used",
            "### Capture Files Read",
            format_report_value(agent_result.get("capture_files_read"), "No capture files were reported by the agent."),
            "",
            "### Work Items Reviewed",
            format_report_value(agent_result.get("work_items_reviewed"), "No captured work items were reported by the agent."),
            "",
            "### Pull Requests Reviewed",
            format_report_value(agent_result.get("prs_reviewed"), "No captured pull requests were reported by the agent."),
            "",
            "### Diffs Reviewed",
            format_report_value(agent_result.get("diffs_reviewed"), "No captured diffs were reported by the agent."),
            "",
            "## Validation",
            format_report_value(agent_result.get("validation"), "No validation was reported by the agent."),
            "",
            "## Dashboard Validation",
            format_pipeline_validation(agent_result.get("pipeline_validation")),
            "",
            "## Repository Instructions Read",
            format_report_value(agent_result.get("instruction_files_read"), "No repository instruction files were reported by the agent."),
            "",
            "## Reviewer Notes",
            format_report_value(agent_result.get("reviewer_notes"), "No reviewer notes were reported by the agent."),
            "",
        ]
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        mark_final_report(
            portal=portal_name,
            work_item_id=work_item_id,
            final_report_path=str(report_path),
        )
        return str(report_path)

    def launch_copilot_session(
        self,
        *,
        portal_name: str,
        work_item_id: int,
        iteration_path: str,
        triage_status: str,
        selected_base_branch: str,
        work_type: str,
        planned_branch_name: str = "",
    ) -> Dict[str, Any]:
        plan = self.save_plan(
            portal_name=portal_name,
            work_item_id=work_item_id,
            iteration_path=iteration_path,
            triage_status=triage_status,
            selected_base_branch=selected_base_branch,
            work_type=work_type,
            planned_branch_name=planned_branch_name,
        )
        portal, current_item = self._get_action_item(portal_name, work_item_id)
        if current_item.get("has_pr"):
            pull_request_id = current_item.get("pr_id")
            if pull_request_id:
                raise ServiceError(f"WI {work_item_id} already has an associated PR #{pull_request_id}.")
            raise ServiceError(f"WI {work_item_id} already has an associated PR.")

        effective_branch_name = str(current_item.get("effective_branch_name") or plan.get("branch_name") or "").strip()
        if not current_item.get("has_branch") or not effective_branch_name:
            raise ServiceError("Create or detect the work branch before launching CM GPT.")

        runtime_settings = load_runtime_settings()
        workspace_path = str(portal.get("copilot_workspace_path") or "").strip()
        provider = str(runtime_settings.get("copilot_provider") or "").strip()
        agent_name = str(runtime_settings.get("copilot_agent_name") or "").strip()
        model_name = str(runtime_settings.get("copilot_model_name") or "").strip()
        distro = str(runtime_settings.get("copilot_wsl_distro") or "").strip()
        execution_runtime = str(runtime_settings.get("execution_runtime") or "devcontainer").strip()
        auto_launch = bool(runtime_settings.get("copilot_auto_launch"))
        desktop_url = str(runtime_settings.get("copilot_desktop_url") or "").strip()
        reference_docs_path = str(runtime_settings.get("copilot_reference_docs_path") or "").strip()
        prompt_template = str(runtime_settings.get("copilot_prompt_template") or "").strip()
        cli_command_template = str(runtime_settings.get("copilot_cli_command_template") or "").strip()
        strict_model_safety = bool(runtime_settings.get("copilot_strict_model_safety"))
        open_wsl_remote = bool(runtime_settings.get("copilot_open_wsl_remote"))
        vscode_window_mode = str(runtime_settings.get("copilot_vscode_window_mode") or "reuse").strip()
        if provider == "vscode" and bool(runtime_settings.get("copilot_vscode_apply_settings")):
            self._apply_vscode_copilot_settings(
                config=load_app_config(),
                runtime_settings=runtime_settings,
            )

        if provider == "m365_desktop":
            error_message = (
                "Microsoft 365 Copilot Desktop is not an automation-capable provider for this pipeline. "
                "Configure an approved executor that can edit the local repository automatically, such as VS Code Copilot with the CM GPT model available."
            )
            mark_copilot_result(
                portal=portal_name,
                work_item_id=work_item_id,
                copilot_status="blocked",
                copilot_context_path="",
                copilot_workspace_path=workspace_path,
                copilot_agent_name=agent_name,
                copilot_error=error_message,
            )
            mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
            raise ServiceError(error_message)

        if not auto_launch:
            error_message = "CM GPT automatic execution is disabled. Enable Run Executor Automatically before running the pipeline."
            mark_copilot_result(
                portal=portal_name,
                work_item_id=work_item_id,
                copilot_status="blocked",
                copilot_context_path="",
                copilot_workspace_path=workspace_path,
                copilot_agent_name=agent_name,
                copilot_error=error_message,
            )
            mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
            raise ServiceError(error_message)

        if provider in {"vscode", "vscode_bridge"} and strict_model_safety:
            error_message = (
                "Strict CM GPT Safety Mode prepares context only and does not run automatic edits. "
                "Disable it only for temporary end-to-end testing or when VS Code Copilot can enforce the approved CM GPT model for this workspace."
            )
            mark_copilot_result(
                portal=portal_name,
                work_item_id=work_item_id,
                copilot_status="blocked",
                copilot_context_path="",
                copilot_workspace_path=workspace_path,
                copilot_agent_name=agent_name,
                copilot_error=error_message,
            )
            mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
            raise ServiceError(error_message)

        provider_preflight = self._check_agent_provider_prerequisites(
            runtime_settings,
            workspace_path=workspace_path,
        )
        if not bool(provider_preflight.get("ok")):
            error_message = str(provider_preflight.get("message") or "The configured agent provider is not ready.").strip()
            mark_copilot_result(
                portal=portal_name,
                work_item_id=work_item_id,
                copilot_status="blocked",
                copilot_context_path="",
                copilot_workspace_path=workspace_path,
                copilot_agent_name=agent_name,
                copilot_error=error_message,
            )
            mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
            raise ServiceError(error_message)

        if bool(runtime_settings.get("context_capture_enabled")):
            try:
                capture_client = self._build_tfs_client(
                    portal,
                    project=str(portal.get("work_item_project") or portal["project"]),
                    repository=portal["repository"],
                )
                capture_package_files = build_context_capture_package(
                    client=capture_client,
                    item=current_item,
                    portal=portal,
                    workspace_path=workspace_path,
                    distro=distro,
                    root_mode=str(runtime_settings.get("context_capture_root_mode") or "parent"),
                    include_pr_diffs=bool(runtime_settings.get("context_capture_include_pr_diffs")),
                    max_tree_items=int(runtime_settings.get("context_capture_max_tree_items") or 50),
                    workspace_scan_roots=list(runtime_settings.get("context_capture_workspace_scan_roots") or ["/workspaces"]),
                    execution_runtime=execution_runtime,
                )
            except Exception as exc:
                capture_package_files = build_capture_error_package(current_item, str(exc))
        else:
            capture_package_files = {}

        try:
            with execution_runtime_scope(execution_runtime):
                result = prepare_cm_gpt_handoff(
                    distro=distro,
                    workspace_path=workspace_path,
                    branch_name=effective_branch_name,
                    agent_name=agent_name,
                    model_name=model_name,
                    item=current_item,
                    portal=portal,
                    provider=provider,
                    reference_docs_path=reference_docs_path,
                    prompt_template=prompt_template,
                    cli_command_template=cli_command_template,
                    auto_launch=auto_launch,
                    desktop_url=desktop_url,
                    strict_model_safety=strict_model_safety,
                    open_wsl_remote=open_wsl_remote,
                    vscode_window_mode=vscode_window_mode,
                    capture_package_files=capture_package_files,
                )
        except CopilotIntegrationError as exc:
            mark_copilot_result(
                portal=portal_name,
                work_item_id=work_item_id,
                copilot_status="error",
                copilot_context_path="",
                copilot_workspace_path=workspace_path,
                copilot_agent_name=agent_name,
                copilot_error=str(exc),
            )
            mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
            raise ServiceError(str(exc)) from exc

        mark_copilot_result(
            portal=portal_name,
            work_item_id=work_item_id,
            copilot_status=str(result["status"]),
            copilot_context_path=str(result["context_path"]),
            copilot_workspace_path=str(result["workspace_path"]),
            copilot_agent_name=str(result["agent_name"]),
            copilot_provider_log_path=str(result.get("cli_log_path") or ""),
            copilot_process_id=str(result.get("cli_pid") or ""),
            copilot_error="",
            agent_result_path=str(result.get("agent_result_path") or ""),
            auto_flow_enabled=True,
        )
        mark_agent_result(
            portal=portal_name,
            work_item_id=work_item_id,
            agent_result_status="waiting",
            agent_result_path=str(result.get("agent_result_path") or ""),
            agent_result_summary="",
            agent_result_error="",
        )
        self._schedule_auto_completion(
            portal_name=portal_name,
            work_item_id=work_item_id,
            iteration_path=iteration_path,
            triage_status=triage_status,
            selected_base_branch=selected_base_branch,
            work_type=work_type,
            planned_branch_name=planned_branch_name,
        )
        return result

    def _run_agent_preflight_diagnostics(
        self,
        *,
        distro: str,
        workspace_path: str,
        branch_name: str,
        changed_files: List[str],
        execution_runtime: str,
    ) -> Dict[str, Any]:
        if not changed_files:
            return {
                "status": "skipped",
                "error": "No changed files were reported by the agent.",
                "checks": [],
            }
        try:
            with execution_runtime_scope(execution_runtime):
                return validate_agent_changes_for_push(
                    distro=distro,
                    workspace_path=workspace_path,
                    branch_name=branch_name,
                    changed_files=changed_files,
                )
        except CopilotIntegrationError as exc:
            return {
                "status": "failed",
                "error": str(exc),
                "changed_files": changed_files,
                "checks": [],
            }

    def _build_agent_repair_prompt_template(
        self,
        *,
        current_item: Dict[str, Any],
        previous_result: Dict[str, Any],
        repair_reason: str,
    ) -> str:
        branch_name = str(current_item.get("effective_branch_name") or current_item.get("branch_name") or "").strip()
        result_path = str(current_item.get("agent_result_path") or "").strip()
        package_directory = result_path.rsplit("/", 1)[0] if result_path else ""
        relative_package_directory = ""
        if "/.automation-context/" in package_directory:
            relative_package_directory = ".automation-context/" + package_directory.split("/.automation-context/", 1)[1]
        elif "/.git/copilot-context/" in package_directory:
            relative_package_directory = ".git/copilot-context/" + package_directory.split("/.git/copilot-context/", 1)[1]

        changed_files = "\n".join(f"- `{path}`" for path in list(previous_result.get("changed_files") or [])) or "- No changed files were reported."
        spec_references = format_spec_references(previous_result.get("spec_references"))
        instruction_files_read = format_report_value(
            previous_result.get("instruction_files_read"),
            "No repository instruction files were reported by the previous result.",
        )
        pipeline_validation = format_pipeline_validation(previous_result.get("pipeline_validation"))
        previous_validation = format_report_value(
            previous_result.get("validation"),
            "No agent validation details were reported.",
        )
        previous_summary = str(previous_result.get("summary") or "").strip() or "No previous summary was reported."

        lines = [
            "Repair the previous documentation automation attempt for this work item.",
            "",
            "This is a continuation on the same branch with local changes already present.",
            f"- Current branch: `{branch_name}`",
            f"- Full context package: `{relative_package_directory or package_directory or '-'}`",
            "",
            "Before editing or running commands:",
            "- Read `{{context_path}}` again.",
            "- Read the repository instruction package index if present: "
            f"`{relative_package_directory}/repo-instructions/index.md`.",
            "- Read every repository instruction file listed there and include the original paths in `instruction_files_read`.",
            "- Read the reference documentation package index if present: "
            f"`{relative_package_directory}/reference-docs/index.md`.",
            "- Read the packaged reference text extracts listed there before reporting that a spec is unavailable.",
            "",
            "Previous result summary:",
            previous_summary,
            "",
            "Previous changed files:",
            changed_files,
            "",
            "Repair reason:",
            repair_reason.strip() or "The previous agent result was not ready for push.",
            "",
            "Previous spec references:",
            spec_references,
            "",
            "Previous instruction files read:",
            instruction_files_read,
            "",
            "Previous agent validation:",
            previous_validation,
            "",
            "Dashboard preflight validation:",
            pipeline_validation,
            "",
            "Required repair outcome:",
            "- Fix any broken paths, links, validation errors, missing files, or incomplete documentation caused by the previous attempt.",
            "- If the work item references specs, inspect the reference package and record every used spec in `spec_references` with the source path/name, section or topic, and how it informed the change.",
            "- Confirm all required repository instruction files in `instruction_files_read` using their original repository paths.",
            "- Rewrite `{{agent_result_path}}` with a complete JSON result.",
            "- Set `green_light` to true only if the local changes are ready for dashboard commit, push, and Draft PR creation.",
            "- If you still cannot make the result ready, set `green_light` to false and explain the blocker precisely.",
            "",
            "Original configurable task instructions:",
            "{{prompt_path}} contains this repair prompt; use `{{context_path}}` as the primary work item context.",
        ]
        return "\n".join(lines)

    def _can_start_agent_repair(self, current_item: Dict[str, Any]) -> bool:
        if current_item.get("has_pr"):
            return False
        if str(current_item.get("push_status") or "").strip().lower() == "pushed":
            return False
        repair_count = int(current_item.get("agent_repair_count") or 0)
        return repair_count < MAX_AUTOMATIC_AGENT_REPAIR_ATTEMPTS

    def _launch_agent_repair_session(
        self,
        *,
        portal_name: str,
        current_item: Dict[str, Any],
        previous_result: Dict[str, Any],
        repair_reason: str,
        iteration_path: str = "",
        triage_status: str = "",
        selected_base_branch: str = "",
        work_type: str = "",
        planned_branch_name: str = "",
    ) -> Dict[str, Any]:
        portal = get_portal_config(load_app_config(), portal_name)
        runtime_settings = load_runtime_settings()
        workspace_path = str(current_item.get("copilot_workspace_path") or portal.get("copilot_workspace_path") or "").strip()
        branch_name = str(current_item.get("effective_branch_name") or current_item.get("branch_name") or planned_branch_name or "").strip()
        if not workspace_path or not branch_name:
            raise ServiceError("The agent repair flow requires a workspace path and work branch.")

        provider = str(runtime_settings.get("copilot_provider") or "").strip()
        if provider == "m365_desktop":
            raise ServiceError("Microsoft 365 Copilot Desktop cannot run automatic agent repair.")

        repair_count = mark_agent_repair_started(
            portal=portal_name,
            work_item_id=int(current_item["id"]),
            reason=repair_reason,
            max_attempts=MAX_AUTOMATIC_AGENT_REPAIR_ATTEMPTS,
        )
        if repair_count <= 0:
            raise ServiceError("The automatic agent repair attempt limit has already been reached for this work item.")

        prompt_template = self._build_agent_repair_prompt_template(
            current_item=current_item,
            previous_result=previous_result,
            repair_reason=repair_reason,
        )
        if provider == "vscode" and bool(runtime_settings.get("copilot_vscode_apply_settings")):
            self._apply_vscode_copilot_settings(
                config=load_app_config(),
                runtime_settings=runtime_settings,
            )

        execution_runtime = str(runtime_settings.get("execution_runtime") or "devcontainer").strip()
        with execution_runtime_scope(execution_runtime):
            result = prepare_cm_gpt_handoff(
                distro=str(runtime_settings.get("copilot_wsl_distro") or "").strip(),
                workspace_path=workspace_path,
                branch_name=branch_name,
                agent_name=str(runtime_settings.get("copilot_agent_name") or "").strip(),
                model_name=str(runtime_settings.get("copilot_model_name") or "").strip(),
                item=current_item,
                portal=portal,
                provider=provider,
                reference_docs_path=str(runtime_settings.get("copilot_reference_docs_path") or "").strip(),
                prompt_template=prompt_template,
                cli_command_template=str(runtime_settings.get("copilot_cli_command_template") or "").strip(),
                auto_launch=bool(runtime_settings.get("copilot_auto_launch")),
                desktop_url=str(runtime_settings.get("copilot_desktop_url") or "").strip(),
                strict_model_safety=bool(runtime_settings.get("copilot_strict_model_safety")),
                open_wsl_remote=bool(runtime_settings.get("copilot_open_wsl_remote")),
                vscode_window_mode=str(runtime_settings.get("copilot_vscode_window_mode") or "reuse").strip(),
                allow_existing_changes=True,
            )
        mark_copilot_result(
            portal=portal_name,
            work_item_id=int(current_item["id"]),
            copilot_status=str(result["status"]),
            copilot_context_path=str(result["context_path"]),
            copilot_workspace_path=str(result["workspace_path"]),
            copilot_agent_name=str(result["agent_name"]),
            copilot_provider_log_path=str(result.get("cli_log_path") or ""),
            copilot_process_id=str(result.get("cli_pid") or ""),
            copilot_error="",
            agent_result_path=str(result.get("agent_result_path") or ""),
            auto_flow_enabled=True,
        )
        mark_agent_result(
            portal=portal_name,
            work_item_id=int(current_item["id"]),
            agent_result_status="waiting",
            agent_result_path=str(result.get("agent_result_path") or ""),
            agent_result_summary=str(previous_result.get("summary") or ""),
            agent_result_error=f"Automatic repair attempt {repair_count} launched. {repair_reason}".strip(),
        )
        self._schedule_auto_completion(
            portal_name=portal_name,
            work_item_id=int(current_item["id"]),
            iteration_path=iteration_path or str(current_item.get("iteration_path") or ""),
            triage_status=triage_status or str(current_item.get("triage_status") or "pending"),
            selected_base_branch=selected_base_branch or str(current_item.get("selected_base_branch") or ""),
            work_type=work_type or str(current_item.get("selected_work_type") or current_item.get("work_type") or "task"),
            planned_branch_name=planned_branch_name or str(current_item.get("branch_name") or ""),
        )
        return {
            **result,
            "repair_count": repair_count,
            "repair_reason": repair_reason,
        }

    def check_agent_result(
        self,
        *,
        portal_name: str,
        work_item_id: int,
    ) -> Dict[str, Any]:
        portal, current_item = self._get_action_item(portal_name, work_item_id)
        runtime_settings = load_runtime_settings()
        workspace_path = str(current_item.get("copilot_workspace_path") or portal.get("copilot_workspace_path") or "").strip()
        distro = str(runtime_settings.get("copilot_wsl_distro") or "").strip()
        execution_runtime = str(runtime_settings.get("execution_runtime") or "devcontainer").strip()
        with execution_runtime_scope(execution_runtime):
            effective_distro, normalized_workspace_path = normalize_wsl_target_path(workspace_path, distro)
        result_path = str(current_item.get("agent_result_path") or "").strip()
        if not result_path and current_item.get("copilot_context_path"):
            result_path = str(current_item["copilot_context_path"]).rsplit("/", 1)[0] + "/agent-result.json"

        provider = str(runtime_settings.get("copilot_provider") or "").strip()
        if (
            provider in {"codex_cli", "claude_cli", "custom_cli"}
            and str(current_item.get("copilot_status") or "").strip().lower() in {"launched", "prepared"}
            and not str(current_item.get("copilot_process_id") or "").strip()
        ):
            provider_error = "The CLI provider launch did not return a process id. Relaunch the automatic flow."
            mark_agent_result(
                portal=portal_name,
                work_item_id=work_item_id,
                agent_result_status="error",
                agent_result_path=result_path,
                agent_result_error=provider_error,
            )
            mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
            return {
                "status": "error",
                "green_light": False,
                "summary": "",
                "error": provider_error,
                "changed_files": [],
                "result_path": result_path,
            }

        with execution_runtime_scope(execution_runtime):
            result = read_agent_result(effective_distro, result_path)
        if (
            str(result.get("status") or "").strip().lower() != "waiting"
            and bool(current_item.get("auto_flow_enabled"))
            and not bool(current_item.get("has_pr"))
            and str(current_item.get("push_status") or "").strip().lower() != "pushed"
        ):
            with execution_runtime_scope(execution_runtime):
                result_file = inspect_agent_result_file(effective_distro, result_path)
            result_age = float(result_file.get("age_seconds") or 0.0)
            if bool(result_file.get("exists")) and result_age < AGENT_RESULT_STABILITY_SECONDS:
                wait_message = (
                    "Agent result file changed recently; waiting for it to stabilize before validation "
                    f"({result_age:.0f}s/{AGENT_RESULT_STABILITY_SECONDS:.0f}s)."
                )
                mark_agent_result(
                    portal=portal_name,
                    work_item_id=work_item_id,
                    agent_result_status="waiting",
                    agent_result_path=result_path,
                    agent_result_summary=str(result.get("summary") or ""),
                    agent_result_error=wait_message,
                )
                return {
                    **result,
                    "status": "waiting",
                    "green_light": False,
                    "error": wait_message,
                    "changed_files": list(result.get("changed_files") or []),
                    "result_path": result_path,
                }
        if str(result.get("status") or "").strip().lower() == "waiting":
            with execution_runtime_scope(execution_runtime):
                provider_status = read_agent_provider_status(
                    effective_distro,
                    result_path=result_path,
                    log_path=str(current_item.get("copilot_provider_log_path") or ""),
                    process_id=str(current_item.get("copilot_process_id") or ""),
                )
            if provider_status.get("terminal_error"):
                provider_error = str(provider_status.get("error") or "The agent provider stopped before writing a result.")
                mark_agent_result(
                    portal=portal_name,
                    work_item_id=work_item_id,
                    agent_result_status="error",
                    agent_result_path=result_path,
                    agent_result_error=provider_error,
                )
                mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
                return {
                    **result,
                    "status": "error",
                    "green_light": False,
                    "error": provider_error,
                    "changed_files": [],
                    "result_path": result_path,
                    "provider_log_tail": str(provider_status.get("tail") or ""),
                }
            if provider_status.get("waiting_for_user_action"):
                wait_message = str(provider_status.get("error") or "Waiting for VS Code Copilot authorization.")
                mark_agent_result(
                    portal=portal_name,
                    work_item_id=work_item_id,
                    agent_result_status="waiting",
                    agent_result_path=result_path,
                    agent_result_error=wait_message,
                )
                return {
                    **result,
                    "status": "waiting",
                    "green_light": False,
                    "error": wait_message,
                    "changed_files": [],
                    "result_path": result_path,
                    "provider_log_tail": str(provider_status.get("tail") or ""),
                }

        skip_pipeline_validation = (
            bool(current_item.get("has_pr"))
            or str(current_item.get("push_status") or "").strip().lower() == "pushed"
        )
        branch_name = str(current_item.get("effective_branch_name") or current_item.get("branch_name") or "").strip()
        changed_files = list(result.get("changed_files") or [])
        expected_instruction_files: List[Dict[str, str]] = []
        acknowledgement_error = ""
        if str(result.get("status") or "").strip().lower() != "waiting" and not skip_pipeline_validation:
            with execution_runtime_scope(execution_runtime):
                expected_instruction_files = discover_workspace_instruction_files(
                    effective_distro,
                    normalized_workspace_path,
                )
            try:
                validate_instruction_acknowledgement(
                    expected_instruction_files=expected_instruction_files,
                    agent_result=result,
                )
            except ServiceError as exc:
                acknowledgement_error = str(exc)

            if changed_files:
                result["pipeline_validation"] = self._run_agent_preflight_diagnostics(
                    distro=effective_distro,
                    workspace_path=normalized_workspace_path,
                    branch_name=branch_name,
                    changed_files=changed_files,
                    execution_runtime=execution_runtime,
                )

        repair_reasons: List[str] = []
        if str(result.get("status") or "").strip().lower() != "waiting" and not skip_pipeline_validation:
            result_status = str(result.get("status") or "").strip().lower()
            if result_status == "invalid":
                repair_reasons.append(str(result.get("error") or "The agent result file is invalid."))
            if changed_files and not bool(result.get("green_light")):
                repair_reasons.append(
                    "The previous agent result reported changed files but did not give green light for push."
                )
            elif bool(result.get("green_light")) and not changed_files:
                repair_reasons.append(
                    "The agent result gave green light but did not list any changed files for validation and commit."
                )
            elif not bool(result.get("green_light")) and result_status in {"green", "green_light", "ready", "ready_for_push", "success", "completed"}:
                repair_reasons.append(
                    "The previous agent result used a completion status but did not give green light for push."
                )
            if acknowledgement_error:
                repair_reasons.append(acknowledgement_error)
            pipeline_validation = result.get("pipeline_validation")
            if isinstance(pipeline_validation, dict) and str(pipeline_validation.get("status") or "").strip().lower() == "failed":
                repair_reasons.append(
                    "Dashboard preflight validation failed: "
                    + str(pipeline_validation.get("error") or "Unknown validation error.")
                )

        if repair_reasons and bool(current_item.get("auto_flow_enabled")) and self._can_start_agent_repair(current_item):
            repair_reason = "\n".join(repair_reasons)
            try:
                repair_result = self._launch_agent_repair_session(
                    portal_name=portal_name,
                    current_item=current_item,
                    previous_result=result,
                    repair_reason=repair_reason,
                )
            except (ServiceError, CopilotIntegrationError) as exc:
                error_message = str(exc)
                mark_agent_result(
                    portal=portal_name,
                    work_item_id=work_item_id,
                    agent_result_status="needs_agent_fix",
                    agent_result_path=result_path,
                    agent_result_summary=str(result.get("summary") or ""),
                    agent_result_error=f"{repair_reason}\nAutomatic repair could not be launched: {error_message}",
                )
                mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
                return {
                    **result,
                    "status": "needs_agent_fix",
                    "green_light": False,
                    "error": f"{repair_reason}\nAutomatic repair could not be launched: {error_message}",
                    "result_path": result_path,
                }
            return {
                **result,
                "status": "repair_launched",
                "green_light": False,
                "error": repair_reason,
                "result_path": result_path,
                "repair": repair_result,
            }

        if repair_reasons:
            error_message = "\n".join(repair_reasons)
            mark_agent_result(
                portal=portal_name,
                work_item_id=work_item_id,
                agent_result_status="needs_agent_fix",
                agent_result_path=result_path,
                agent_result_summary=str(result.get("summary") or ""),
                agent_result_error=error_message,
            )
            mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
            return {
                **result,
                "status": "needs_agent_fix",
                "green_light": False,
                "error": error_message,
                "result_path": result_path,
            }

        if bool(result.get("green_light")) and not skip_pipeline_validation:
            try:
                if acknowledgement_error:
                    raise ServiceError(acknowledgement_error)
                with execution_runtime_scope(execution_runtime):
                    expected_instruction_files = discover_workspace_instruction_files(
                        effective_distro,
                        normalized_workspace_path,
                    )
                validate_instruction_acknowledgement(
                    expected_instruction_files=expected_instruction_files,
                    agent_result=result,
                )
                with execution_runtime_scope(execution_runtime):
                    result["pipeline_validation"] = validate_agent_changes_for_push(
                        distro=effective_distro,
                        workspace_path=normalized_workspace_path,
                        branch_name=branch_name,
                        changed_files=list(result.get("changed_files") or []),
                    )
            except (ServiceError, CopilotIntegrationError) as exc:
                error_message = str(exc)
                mark_agent_result(
                    portal=portal_name,
                    work_item_id=work_item_id,
                    agent_result_status="needs_agent_fix",
                    agent_result_path=result_path,
                    agent_result_summary=str(result.get("summary") or ""),
                    agent_result_error=error_message,
                )
                mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
                return {
                    **result,
                    "status": "needs_agent_fix",
                    "green_light": False,
                    "error": error_message,
                    "result_path": result_path,
                }

        status = "green_light" if result.get("green_light") else str(result.get("status") or "waiting")
        stored_error = "" if status == "waiting" else str(result.get("error") or "")
        mark_agent_result(
            portal=portal_name,
            work_item_id=work_item_id,
            agent_result_status=status,
            agent_result_path=result_path,
            agent_result_summary=str(result.get("summary") or ""),
            agent_result_error=stored_error,
        )
        if status in {"blocked", "invalid", "error", "needs_agent_fix"}:
            mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
        final_report_path = ""
        if bool(result.get("green_light")):
            final_report_path = self._write_final_report(
                portal_name=portal_name,
                current_item=current_item,
                agent_result=result,
            )
        return {
            **result,
            "status": status,
            "result_path": result_path,
            "final_report_path": final_report_path,
        }

    def commit_and_push_agent_result(
        self,
        *,
        portal_name: str,
        work_item_id: int,
    ) -> Dict[str, Any]:
        portal, current_item = self._get_action_item(portal_name, work_item_id)
        if current_item.get("has_pr"):
            raise ServiceError(f"WI {work_item_id} already has an associated PR.")

        agent_result = self.check_agent_result(portal_name=portal_name, work_item_id=work_item_id)
        if not agent_result.get("green_light"):
            raise ServiceError(str(agent_result.get("error") or "The agent result is not green-lighted yet."))

        runtime_settings = load_runtime_settings()
        workspace_path = str(current_item.get("copilot_workspace_path") or portal.get("copilot_workspace_path") or "").strip()
        distro = str(runtime_settings.get("copilot_wsl_distro") or "").strip()
        execution_runtime = str(runtime_settings.get("execution_runtime") or "devcontainer").strip()
        with execution_runtime_scope(execution_runtime):
            effective_distro, normalized_workspace_path = normalize_wsl_target_path(workspace_path, distro)
        branch_name = str(current_item.get("effective_branch_name") or current_item.get("branch_name") or "").strip()
        if not branch_name:
            raise ServiceError("The work branch is not available for push.")

        try:
            with execution_runtime_scope(execution_runtime):
                push_result = commit_and_push_agent_changes(
                    distro=effective_distro,
                    workspace_path=normalized_workspace_path,
                    branch_name=branch_name,
                    work_item_id=work_item_id,
                    title=str(current_item.get("title") or ""),
                    changed_files=list(agent_result.get("changed_files") or []),
                    summary=str(agent_result.get("summary") or ""),
                )
        except CopilotIntegrationError as exc:
            mark_push_result(
                portal=portal_name,
                work_item_id=work_item_id,
                push_status="error",
                push_error=str(exc),
            )
            raise ServiceError(str(exc)) from exc

        mark_push_result(
            portal=portal_name,
            work_item_id=work_item_id,
            push_status="pushed",
            push_commit=str(push_result.get("commit") or ""),
            push_error="",
        )
        self._invalidate_portal_repository_cache(portal_name)
        return push_result

    def continue_automatic_flow_for_item(
        self,
        *,
        portal_name: str,
        work_item_id: int,
        iteration_path: str,
        triage_status: str,
        selected_base_branch: str,
        work_type: str,
        planned_branch_name: str = "",
    ) -> Dict[str, Any]:
        _, current_item = self._get_action_item(portal_name, work_item_id)
        if current_item.get("has_pr"):
            return {
                "status": "already-has-pr",
                "detail": "An associated PR already exists.",
            }

        pushed = str(current_item.get("push_status") or "").strip().lower() == "pushed"
        if not pushed:
            agent_result = self.check_agent_result(portal_name=portal_name, work_item_id=work_item_id)
            if not agent_result.get("green_light"):
                if str(agent_result.get("status") or "").strip().lower() in {"blocked", "invalid", "error", "needs_agent_fix"}:
                    return {
                        "status": "agent-error",
                        "detail": str(agent_result.get("error") or "The agent result needs review."),
                    }
                return {
                    "status": "waiting-for-agent",
                    "detail": str(agent_result.get("error") or "Waiting for the agent result file."),
                }
            push_result = self.commit_and_push_agent_result(portal_name=portal_name, work_item_id=work_item_id)
            pushed = True
        else:
            push_result = {
                "status": "pushed",
                "commit": str(current_item.get("push_commit") or ""),
            }

        if pushed:
            pr_result = self.create_draft_pr(
                portal_name=portal_name,
                work_item_id=work_item_id,
                iteration_path=iteration_path,
                triage_status=triage_status,
                selected_base_branch=selected_base_branch,
                work_type=work_type,
                planned_branch_name=planned_branch_name,
            )
            return {
                "status": "completed",
                "detail": f"Pushed commit {str(push_result.get('commit') or '').strip() or '-'} and created PR #{pr_result['pull_request_id']}.",
                "push": push_result,
                "pr": pr_result,
            }

        return {
            "status": "waiting-for-agent",
            "detail": "Waiting for the agent result file.",
        }

    def _schedule_auto_completion(
        self,
        *,
        portal_name: str,
        work_item_id: int,
        iteration_path: str,
        triage_status: str,
        selected_base_branch: str,
        work_type: str,
        planned_branch_name: str = "",
    ) -> None:
        key = (portal_name, int(work_item_id))
        with _AUTO_WORKER_LOCK:
            if key in _AUTO_WORKERS:
                return
            _AUTO_WORKERS.add(key)

        thread = threading.Thread(
            target=self._auto_completion_worker,
            kwargs={
                "key": key,
                "portal_name": portal_name,
                "work_item_id": int(work_item_id),
                "iteration_path": iteration_path,
                "triage_status": triage_status,
                "selected_base_branch": selected_base_branch,
                "work_type": work_type,
                "planned_branch_name": planned_branch_name,
            },
            daemon=True,
        )
        thread.start()

    def _auto_completion_worker(
        self,
        *,
        key: tuple[str, int],
        portal_name: str,
        work_item_id: int,
        iteration_path: str,
        triage_status: str,
        selected_base_branch: str,
        work_type: str,
        planned_branch_name: str,
    ) -> None:
        try:
            portal, _ = self._get_action_item(portal_name, work_item_id)
            workspace_path = str(portal.get("copilot_workspace_path") or "").strip()
            workspace_lock = _get_workspace_lock(workspace_path)
            with workspace_lock:
                deadline = time.monotonic() + AGENT_RESULT_POLL_TIMEOUT_SECONDS
                while time.monotonic() < deadline:
                    try:
                        _, current_item = self._get_action_item(portal_name, work_item_id)
                        if current_item.get("has_pr"):
                            return

                        copilot_status = str(current_item.get("copilot_status") or "").strip().lower()
                        agent_result_status = str(current_item.get("agent_result_status") or "").strip().lower()
                        push_status = str(current_item.get("push_status") or "").strip().lower()
                        result_path = str(current_item.get("agent_result_path") or "").strip()
                        branch_name = str(
                            current_item.get("effective_branch_name")
                            or current_item.get("branch_name")
                            or planned_branch_name
                            or ""
                        ).strip()
                        provider_is_waiting = (
                            copilot_status in {"launched", "prepared"}
                            and agent_result_status in {"", "waiting"}
                            and bool(result_path)
                        )
                        has_result_to_continue = (
                            bool(result_path)
                            and agent_result_status
                            and agent_result_status not in {"", "error"}
                        )
                        needs_agent_launch = (
                            push_status != "pushed"
                            and not provider_is_waiting
                            and not has_result_to_continue
                        )

                        if needs_agent_launch:
                            self.launch_copilot_session(
                                portal_name=portal_name,
                                work_item_id=work_item_id,
                                iteration_path=iteration_path,
                                triage_status=triage_status,
                                selected_base_branch=selected_base_branch,
                                work_type=work_type,
                                planned_branch_name=branch_name,
                            )

                        result = self.continue_automatic_flow_for_item(
                            portal_name=portal_name,
                            work_item_id=work_item_id,
                            iteration_path=iteration_path,
                            triage_status=triage_status,
                            selected_base_branch=selected_base_branch,
                            work_type=work_type,
                            planned_branch_name=branch_name,
                        )
                        if result["status"] != "waiting-for-agent":
                            return
                    except Exception as exc:
                        mark_agent_result(
                            portal=portal_name,
                            work_item_id=work_item_id,
                            agent_result_status="error",
                            agent_result_error=str(exc),
                        )
                        mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
                        return
                    time.sleep(AGENT_RESULT_POLL_INTERVAL_SECONDS)
            mark_agent_result(
                portal=portal_name,
                work_item_id=work_item_id,
                agent_result_status="error",
                agent_result_error="Timed out waiting for agent-result.json.",
            )
            mark_auto_flow_enabled(portal=portal_name, work_item_id=work_item_id, enabled=False)
        finally:
            with _AUTO_WORKER_LOCK:
                _AUTO_WORKERS.discard(key)
