from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse


APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config" / "tfs_dashboard.json"
LOCAL_CONFIG_PATH = APP_DIR / "config" / "tfs_dashboard.local.json"
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "automation.db"
ENV_PATH = APP_DIR / ".env"
VS_CODE_SETTINGS_PATH = Path.home() / "AppData" / "Roaming" / "Code" / "User" / "settings.json"
AUTH_OPTIONS = ["Windows Credentials", "Git Credentials", "PAT"]
COPILOT_PERMISSION_LEVEL_OPTIONS = ["default", "autoApprove", "autopilot"]
COPILOT_PROVIDER_OPTIONS = ["vscode_bridge", "vscode", "codex_cli", "claude_cli", "custom_cli", "m365_desktop"]
COPILOT_VSCODE_WINDOW_MODE_OPTIONS = ["reuse", "new"]
CONTEXT_CAPTURE_ROOT_MODE_OPTIONS = ["parent", "task"]
EXECUTION_RUNTIME_OPTIONS = ["devcontainer", "windows_host"]
DEFAULT_AGENT_PROMPT_TEMPLATE = """Read `{{context_path}}`, the adjacent HTML/JSON files, and the generated capture package when present.
Start with `capture/INSTRUCTIONS.md` and `capture/summary.md` before inspecting repository files.
Inspect referenced specs, linked pull request diffs, and reference documentation when available.
Keep changes focused, consistent with the existing portal style, and avoid unrelated refactors.
If a spec is used, record the spec name/path and the section or topic that justified the change.
Finish with a reviewer-ready summary that explains what changed and why."""
PORTAL_TEMPLATE = {
    "base_url": "",
    "project": "",
    "repository": "",
    "work_item_project": "",
    "work_item_team": "",
    "work_item_area_path": "",
    "copilot_workspace_path": "",
    "team": "",
    "api_version": "6.0",
    "branch_chain": [],
    "lookback_days": 7,
    "max_prs_per_branch": 150,
    "verify_work_items_via_api": True,
    "cherry_pick_skip_labels": ["No CP", "no-cp", "not to cp"],
    "auth_mode": "Windows Credentials",
}
DEFAULT_CONFIG = {
    "DEFAULT_PORTAL": "",
    "portals": [],
}


def get_persisted_settings_file(filename: str) -> Optional[Path]:
    settings_root = os.environ.get("CONTENT_AI_SETTINGS_PATH", "").strip()
    if not settings_root:
        return None
    return Path(settings_root).expanduser() / filename


def restore_persisted_file(local_path: Path, filename: str) -> None:
    if local_path.exists():
        return
    persisted_path = get_persisted_settings_file(filename)
    if not persisted_path or not persisted_path.exists():
        return
    local_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(persisted_path, local_path)


def mirror_persisted_file(local_path: Path, filename: str) -> None:
    persisted_path = get_persisted_settings_file(filename)
    if not persisted_path or not local_path.exists():
        return
    persisted_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(local_path, persisted_path)
DEFAULT_RUNTIME_SETTINGS = {
    "server_host": "127.0.0.1",
    "server_port": 7000,
    "auto_port": True,
    "tfs_request_timeout_seconds": 15,
    "tfs_verify_ssl": True,
    "tfs_ca_bundle_path": "",
    "automation_runner_enabled": True,
    "automation_reconcile_interval_seconds": 30,
    "automation_continuous_mode": False,
    "automation_discovery_interval_minutes": 5,
    "content_team_members": [],
    "default_current_iteration_only": True,
    "execution_runtime": "devcontainer",
    "copilot_wsl_distro": "Ubuntu",
    "copilot_provider": "vscode_bridge",
    "copilot_model_name": "CM GPT",
    "copilot_agent_name": "CM GPT",
    "copilot_auto_launch": True,
    "copilot_prompt_template": DEFAULT_AGENT_PROMPT_TEMPLATE,
    "copilot_cli_command_template": "",
    "final_reports_path": str(DATA_DIR / "reports"),
    "copilot_desktop_url": "https://m365.cloud.microsoft/chat",
    "copilot_reference_docs_path": "/workspaces/Documentation",
    "copilot_strict_model_safety": False,
    "copilot_open_wsl_remote": True,
    "copilot_vscode_window_mode": "reuse",
    "copilot_vscode_apply_settings": True,
    "copilot_vscode_settings_path": str(VS_CODE_SETTINGS_PATH),
    "copilot_vscode_permission_level": "autopilot",
    "copilot_vscode_global_auto_approve": True,
    "copilot_vscode_auto_accept_edits_delay_ms": 1000,
    "copilot_additional_read_access_folders": [],
    "context_capture_enabled": True,
    "context_capture_root_mode": "parent",
    "context_capture_max_tree_items": 50,
    "context_capture_include_pr_diffs": True,
    "context_capture_workspace_scan_roots": ["/workspaces"],
    "default_reviewer_display_name": "",
    "default_reviewer_unique_name": "",
    "default_reviewer_id": "",
    "reviewer_overrides": {},
}


def normalize_work_item_team(value: Any) -> str:
    token = unquote(str(value or "").strip())
    if not token:
        return ""

    parsed = urlparse(token)
    if parsed.scheme and parsed.netloc:
        segments = [segment.strip() for segment in parsed.path.split("/") if segment.strip()]
        lowered = [segment.lower() for segment in segments]
        if "taskboard" in lowered:
            taskboard_index = lowered.index("taskboard")
            trailing_segments = segments[taskboard_index + 1 :]
            if trailing_segments:
                return trailing_segments[0]
        if "_apis" in lowered:
            api_index = lowered.index("_apis")
            if api_index >= 1:
                return segments[api_index - 1]

    normalized = token.replace("\\", "/").strip("/")
    if "/" in normalized:
        return normalized.split("/")[0].strip()
    return normalized


def load_json(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def _normalize_branch_chain(value: Any) -> List[str]:
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(",", "\n").splitlines()]
    else:
        parts = [str(part).strip() for part in value or []]
    return [part.replace("refs/heads/", "") for part in parts if part]


def _normalize_string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(",", "\n").splitlines()]
    else:
        parts = [str(part).strip() for part in value or []]
    normalized: List[str] = []
    for part in parts:
        if part and part not in normalized:
            normalized.append(part)
    return normalized


def normalize_portal_config(portal: Any, fallback_repository: Optional[str] = None) -> Dict[str, Any]:
    source = portal if isinstance(portal, dict) else {}
    repository = str(source.get("repository") or fallback_repository or PORTAL_TEMPLATE["repository"]).strip()
    auth_mode = str(source.get("auth_mode", PORTAL_TEMPLATE["auth_mode"])).strip()
    if auth_mode not in AUTH_OPTIONS:
        auth_mode = PORTAL_TEMPLATE["auth_mode"]

    return {
        "base_url": str(source.get("base_url", PORTAL_TEMPLATE["base_url"])).strip().rstrip("/"),
        "project": str(source.get("project", PORTAL_TEMPLATE["project"])).strip(),
        "repository": repository,
        "work_item_project": str(source.get("work_item_project") or source.get("project", PORTAL_TEMPLATE["project"])).strip(),
        "work_item_team": normalize_work_item_team(
            source.get("work_item_team") or source.get("team", PORTAL_TEMPLATE["team"])
        ),
        "work_item_area_path": str(
            source.get("work_item_area_path") or f"{source.get('project', PORTAL_TEMPLATE['project'])}\\Development"
        ).strip(),
        "copilot_workspace_path": str(
            source.get("copilot_workspace_path", PORTAL_TEMPLATE["copilot_workspace_path"])
        ).strip(),
        "team": str(source.get("team", PORTAL_TEMPLATE["team"])).strip(),
        "api_version": str(source.get("api_version", PORTAL_TEMPLATE["api_version"])).strip() or PORTAL_TEMPLATE["api_version"],
        "branch_chain": _normalize_branch_chain(source.get("branch_chain", PORTAL_TEMPLATE["branch_chain"])),
        "lookback_days": int(source.get("lookback_days", PORTAL_TEMPLATE["lookback_days"])),
        "max_prs_per_branch": int(source.get("max_prs_per_branch", PORTAL_TEMPLATE["max_prs_per_branch"])),
        "verify_work_items_via_api": bool(source.get("verify_work_items_via_api", PORTAL_TEMPLATE["verify_work_items_via_api"])),
        "cherry_pick_skip_labels": _normalize_string_list(
            source.get("cherry_pick_skip_labels", PORTAL_TEMPLATE["cherry_pick_skip_labels"])
        ),
        "auth_mode": auth_mode,
    }


def normalize_app_config(raw_config: Any) -> Dict[str, Any]:
    raw = raw_config if isinstance(raw_config, dict) else {}
    if "portals" not in raw:
        portal = normalize_portal_config(raw)
        return {
            "DEFAULT_PORTAL": portal["repository"],
            "portals": [portal],
        }

    portals: List[Dict[str, Any]] = []
    seen_repositories = set()
    for index, portal in enumerate(raw.get("portals", []), start=1):
        normalized = normalize_portal_config(portal, fallback_repository=f"Portal {index}")
        repository = normalized["repository"]
        if repository in seen_repositories:
            continue
        seen_repositories.add(repository)
        portals.append(normalized)

    if not portals:
        fallback_portal = normalize_portal_config({}, fallback_repository="Portal 1")
        portals = [fallback_portal]

    default_portal = str(
        raw.get("DEFAULT_PORTAL")
        or raw.get("default_portal")
        or raw.get("active_repository")
        or portals[0]["repository"]
    ).strip()
    if default_portal not in {portal["repository"] for portal in portals}:
        default_portal = portals[0]["repository"]

    return {
        "DEFAULT_PORTAL": default_portal,
        "portals": portals,
    }


def load_app_config() -> Dict[str, Any]:
    restore_persisted_file(LOCAL_CONFIG_PATH, "tfs_dashboard.local.json")
    active_path = LOCAL_CONFIG_PATH if LOCAL_CONFIG_PATH.exists() else CONFIG_PATH
    return normalize_app_config(load_json(active_path, DEFAULT_CONFIG))


def save_app_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_app_config(config)
    active_path = LOCAL_CONFIG_PATH if LOCAL_CONFIG_PATH.exists() else CONFIG_PATH
    save_json(active_path, normalized)
    mirror_persisted_file(active_path, "tfs_dashboard.local.json")
    return normalized


def get_portal_names(config: Dict[str, Any]) -> List[str]:
    return [portal["repository"] for portal in config["portals"]]


def get_portal_config(config: Dict[str, Any], repository: str) -> Dict[str, Any]:
    for portal in config["portals"]:
        if portal["repository"] == repository:
            return portal
    return config["portals"][0]


def save_portal_config(
    config: Dict[str, Any],
    current_repository: str,
    portal_payload: Dict[str, Any],
) -> Dict[str, Any]:
    normalized_portal = normalize_portal_config(portal_payload, fallback_repository=current_repository)
    updated_portals: List[Dict[str, Any]] = []
    found_current = False
    for portal in config["portals"]:
        if portal["repository"] == current_repository:
            updated_portals.append(normalized_portal)
            found_current = True
        else:
            updated_portals.append(portal)
    if not found_current:
        updated_portals.append(normalized_portal)

    duplicate_names = [
        portal["repository"]
        for portal in updated_portals
        if portal["repository"] == normalized_portal["repository"]
    ]
    if len(duplicate_names) > 1:
        raise ValueError(f"A portal named '{normalized_portal['repository']}' already exists.")

    saved = save_app_config(
        {
            "DEFAULT_PORTAL": normalized_portal["repository"],
            "portals": updated_portals,
        }
    )
    return get_portal_config(saved, normalized_portal["repository"])


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        try:
            return json.loads(value)
        except Exception:
            return value[1:-1]
    return value


def load_env_values(path: Path = ENV_PATH) -> Dict[str, str]:
    if path == ENV_PATH:
        restore_persisted_file(ENV_PATH, ".env")
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_optional_quotes(value.strip())
    return values


def save_env_values(values: Dict[str, str], path: Path = ENV_PATH) -> None:
    ordered_keys = [
        "DOC_AUTOMATION_SERVER_HOST",
        "DOC_AUTOMATION_SERVER_PORT",
        "DOC_AUTOMATION_SERVER_AUTO_PORT",
        "DOC_AUTOMATION_TFS_REQUEST_TIMEOUT_SECONDS",
        "DOC_AUTOMATION_TFS_VERIFY_SSL",
        "DOC_AUTOMATION_TFS_CA_BUNDLE_PATH",
        "DOC_AUTOMATION_RUNNER_ENABLED",
        "DOC_AUTOMATION_RECONCILE_INTERVAL_SECONDS",
        "DOC_AUTOMATION_CONTINUOUS_MODE",
        "DOC_AUTOMATION_DISCOVERY_INTERVAL_MINUTES",
        "DOC_AUTOMATION_CONTENT_TEAM_MEMBERS_JSON",
        "DOC_AUTOMATION_DEFAULT_CURRENT_ITERATION_ONLY",
        "DOC_AUTOMATION_EXECUTION_RUNTIME",
        "DOC_AUTOMATION_COPILOT_WSL_DISTRO",
        "DOC_AUTOMATION_COPILOT_PROVIDER",
        "DOC_AUTOMATION_COPILOT_MODEL_NAME",
        "DOC_AUTOMATION_COPILOT_AGENT_NAME",
        "DOC_AUTOMATION_COPILOT_AUTO_LAUNCH",
        "DOC_AUTOMATION_COPILOT_PROMPT_TEMPLATE",
        "DOC_AUTOMATION_COPILOT_CLI_COMMAND_TEMPLATE",
        "DOC_AUTOMATION_FINAL_REPORTS_PATH",
        "DOC_AUTOMATION_COPILOT_DESKTOP_URL",
        "DOC_AUTOMATION_COPILOT_REFERENCE_DOCS_PATH",
        "DOC_AUTOMATION_COPILOT_STRICT_MODEL_SAFETY",
        "DOC_AUTOMATION_COPILOT_OPEN_WSL_REMOTE",
        "DOC_AUTOMATION_COPILOT_VSCODE_WINDOW_MODE",
        "DOC_AUTOMATION_COPILOT_VSCODE_APPLY_SETTINGS",
        "DOC_AUTOMATION_COPILOT_VSCODE_SETTINGS_PATH",
        "DOC_AUTOMATION_COPILOT_VSCODE_PERMISSION_LEVEL",
        "DOC_AUTOMATION_COPILOT_VSCODE_GLOBAL_AUTO_APPROVE",
        "DOC_AUTOMATION_COPILOT_VSCODE_AUTO_ACCEPT_EDITS_DELAY_MS",
        "DOC_AUTOMATION_COPILOT_ADDITIONAL_READ_ACCESS_FOLDERS_JSON",
        "DOC_AUTOMATION_CONTEXT_CAPTURE_ENABLED",
        "DOC_AUTOMATION_CONTEXT_CAPTURE_ROOT_MODE",
        "DOC_AUTOMATION_CONTEXT_CAPTURE_MAX_TREE_ITEMS",
        "DOC_AUTOMATION_CONTEXT_CAPTURE_INCLUDE_PR_DIFFS",
        "DOC_AUTOMATION_CONTEXT_CAPTURE_WORKSPACE_SCAN_ROOTS_JSON",
        "DOC_AUTOMATION_DEFAULT_REVIEWER_DISPLAY_NAME",
        "DOC_AUTOMATION_DEFAULT_REVIEWER_UNIQUE_NAME",
        "DOC_AUTOMATION_DEFAULT_REVIEWER_ID",
        "DOC_AUTOMATION_REVIEWER_OVERRIDES_JSON",
    ]
    lines = [
        "# TFS Documentation Automation MVP runtime settings",
        "# Managed by the dashboard settings form.",
        "",
    ]
    for key in ordered_keys:
        value = str(values.get(key, ""))
        serialized = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}={serialized}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if path == ENV_PATH:
        mirror_persisted_file(path, ".env")


def parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def parse_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return default
    return parsed


def normalize_reviewer_override_entry(value: Any) -> Dict[str, str]:
    if isinstance(value, str):
        return {
            "display_name": "",
            "unique_name": value.strip(),
            "id": "",
        }
    if isinstance(value, dict):
        return {
            "display_name": str(value.get("display_name") or value.get("displayName") or "").strip(),
            "unique_name": str(value.get("unique_name") or value.get("uniqueName") or "").strip(),
            "id": str(value.get("id") or "").strip(),
        }
    return {
        "display_name": "",
        "unique_name": "",
        "id": "",
    }


def normalize_reviewer_overrides(value: Any) -> Dict[str, Dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, Dict[str, str]] = {}
    for key, entry in value.items():
        match_key = str(key).strip()
        if not match_key:
            continue
        normalized[match_key] = normalize_reviewer_override_entry(entry)
    return normalized


def normalize_string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        items = value.splitlines()
    elif isinstance(value, list):
        items = value
    else:
        items = []
    normalized: List[str] = []
    for item in items:
        token = str(item).strip()
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def load_runtime_settings() -> Dict[str, Any]:
    raw = load_env_values()
    overrides_raw = raw.get("DOC_AUTOMATION_REVIEWER_OVERRIDES_JSON", "").strip()
    overrides_error = ""
    try:
        overrides_value = json.loads(overrides_raw) if overrides_raw else {}
    except json.JSONDecodeError as exc:
        overrides_value = {}
        overrides_error = str(exc)
    members_raw = raw.get("DOC_AUTOMATION_CONTENT_TEAM_MEMBERS_JSON", "").strip()
    members_error = ""
    try:
        members_value = json.loads(members_raw) if members_raw else []
    except json.JSONDecodeError as exc:
        members_value = []
        members_error = str(exc)
    additional_folders_raw = raw.get("DOC_AUTOMATION_COPILOT_ADDITIONAL_READ_ACCESS_FOLDERS_JSON", "").strip()
    additional_folders_error = ""
    try:
        additional_folders_value = json.loads(additional_folders_raw) if additional_folders_raw else []
    except json.JSONDecodeError as exc:
        additional_folders_value = []
        additional_folders_error = str(exc)
    capture_scan_roots_raw = raw.get("DOC_AUTOMATION_CONTEXT_CAPTURE_WORKSPACE_SCAN_ROOTS_JSON", "").strip()
    capture_scan_roots_error = ""
    try:
        capture_scan_roots_value = json.loads(capture_scan_roots_raw) if capture_scan_roots_raw else []
    except json.JSONDecodeError as exc:
        capture_scan_roots_value = []
        capture_scan_roots_error = str(exc)
    overrides = normalize_reviewer_overrides(overrides_value)
    content_team_members = normalize_string_list(members_value)
    additional_read_access_folders = normalize_string_list(additional_folders_value)
    context_capture_scan_roots = normalize_string_list(capture_scan_roots_value)
    if not context_capture_scan_roots and not capture_scan_roots_error:
        context_capture_scan_roots = list(DEFAULT_RUNTIME_SETTINGS["context_capture_workspace_scan_roots"])
    permission_level = str(
        raw.get("DOC_AUTOMATION_COPILOT_VSCODE_PERMISSION_LEVEL")
        or DEFAULT_RUNTIME_SETTINGS["copilot_vscode_permission_level"]
    ).strip()
    if permission_level not in COPILOT_PERMISSION_LEVEL_OPTIONS:
        permission_level = DEFAULT_RUNTIME_SETTINGS["copilot_vscode_permission_level"]
    copilot_provider = str(raw.get("DOC_AUTOMATION_COPILOT_PROVIDER") or DEFAULT_RUNTIME_SETTINGS["copilot_provider"]).strip()
    if copilot_provider not in COPILOT_PROVIDER_OPTIONS:
        copilot_provider = DEFAULT_RUNTIME_SETTINGS["copilot_provider"]
    execution_runtime = str(
        raw.get("DOC_AUTOMATION_EXECUTION_RUNTIME")
        or DEFAULT_RUNTIME_SETTINGS["execution_runtime"]
    ).strip()
    if execution_runtime not in EXECUTION_RUNTIME_OPTIONS:
        execution_runtime = DEFAULT_RUNTIME_SETTINGS["execution_runtime"]
    copilot_vscode_window_mode = str(
        raw.get("DOC_AUTOMATION_COPILOT_VSCODE_WINDOW_MODE")
        or DEFAULT_RUNTIME_SETTINGS["copilot_vscode_window_mode"]
    ).strip()
    if copilot_vscode_window_mode not in COPILOT_VSCODE_WINDOW_MODE_OPTIONS:
        copilot_vscode_window_mode = DEFAULT_RUNTIME_SETTINGS["copilot_vscode_window_mode"]
    context_capture_root_mode = str(
        raw.get("DOC_AUTOMATION_CONTEXT_CAPTURE_ROOT_MODE")
        or DEFAULT_RUNTIME_SETTINGS["context_capture_root_mode"]
    ).strip()
    if context_capture_root_mode not in CONTEXT_CAPTURE_ROOT_MODE_OPTIONS:
        context_capture_root_mode = DEFAULT_RUNTIME_SETTINGS["context_capture_root_mode"]
    return {
        "server_host": str(raw.get("DOC_AUTOMATION_SERVER_HOST") or DEFAULT_RUNTIME_SETTINGS["server_host"]).strip(),
        "server_port": parse_int(raw.get("DOC_AUTOMATION_SERVER_PORT"), DEFAULT_RUNTIME_SETTINGS["server_port"]),
        "auto_port": parse_bool(raw.get("DOC_AUTOMATION_SERVER_AUTO_PORT"), DEFAULT_RUNTIME_SETTINGS["auto_port"]),
        "tfs_request_timeout_seconds": max(
            5,
            min(
                120,
                parse_int(
                    raw.get("DOC_AUTOMATION_TFS_REQUEST_TIMEOUT_SECONDS"),
                    DEFAULT_RUNTIME_SETTINGS["tfs_request_timeout_seconds"],
                ),
            ),
        ),
        "tfs_verify_ssl": parse_bool(
            raw.get("DOC_AUTOMATION_TFS_VERIFY_SSL"),
            DEFAULT_RUNTIME_SETTINGS["tfs_verify_ssl"],
        ),
        "tfs_ca_bundle_path": str(
            raw.get("DOC_AUTOMATION_TFS_CA_BUNDLE_PATH") or DEFAULT_RUNTIME_SETTINGS["tfs_ca_bundle_path"]
        ).strip(),
        "automation_runner_enabled": parse_bool(
            raw.get("DOC_AUTOMATION_RUNNER_ENABLED"),
            DEFAULT_RUNTIME_SETTINGS["automation_runner_enabled"],
        ),
        "automation_reconcile_interval_seconds": parse_int(
            raw.get("DOC_AUTOMATION_RECONCILE_INTERVAL_SECONDS"),
            DEFAULT_RUNTIME_SETTINGS["automation_reconcile_interval_seconds"],
        ),
        "automation_continuous_mode": parse_bool(
            raw.get("DOC_AUTOMATION_CONTINUOUS_MODE"),
            DEFAULT_RUNTIME_SETTINGS["automation_continuous_mode"],
        ),
        "automation_discovery_interval_minutes": parse_int(
            raw.get("DOC_AUTOMATION_DISCOVERY_INTERVAL_MINUTES"),
            DEFAULT_RUNTIME_SETTINGS["automation_discovery_interval_minutes"],
        ),
        "content_team_members": content_team_members,
        "content_team_members_text": "\n".join(content_team_members),
        "content_team_members_error": members_error,
        "default_current_iteration_only": parse_bool(
            raw.get("DOC_AUTOMATION_DEFAULT_CURRENT_ITERATION_ONLY"),
            DEFAULT_RUNTIME_SETTINGS["default_current_iteration_only"],
        ),
        "execution_runtime": execution_runtime,
        "copilot_wsl_distro": str(
            raw.get("DOC_AUTOMATION_COPILOT_WSL_DISTRO") or DEFAULT_RUNTIME_SETTINGS["copilot_wsl_distro"]
        ).strip(),
        "copilot_provider": copilot_provider,
        "copilot_model_name": str(
            raw.get("DOC_AUTOMATION_COPILOT_MODEL_NAME") or DEFAULT_RUNTIME_SETTINGS["copilot_model_name"]
        ).strip(),
        "copilot_agent_name": str(
            raw.get("DOC_AUTOMATION_COPILOT_AGENT_NAME") or DEFAULT_RUNTIME_SETTINGS["copilot_agent_name"]
        ).strip(),
        "copilot_auto_launch": parse_bool(
            raw.get("DOC_AUTOMATION_COPILOT_AUTO_LAUNCH"),
            DEFAULT_RUNTIME_SETTINGS["copilot_auto_launch"],
        ),
        "copilot_prompt_template": str(
            raw.get("DOC_AUTOMATION_COPILOT_PROMPT_TEMPLATE")
            or DEFAULT_RUNTIME_SETTINGS["copilot_prompt_template"]
        ).strip(),
        "copilot_cli_command_template": str(
            raw.get("DOC_AUTOMATION_COPILOT_CLI_COMMAND_TEMPLATE")
            or DEFAULT_RUNTIME_SETTINGS["copilot_cli_command_template"]
        ).strip(),
        "final_reports_path": str(
            raw.get("DOC_AUTOMATION_FINAL_REPORTS_PATH")
            or DEFAULT_RUNTIME_SETTINGS["final_reports_path"]
        ).strip(),
        "copilot_desktop_url": str(
            raw.get("DOC_AUTOMATION_COPILOT_DESKTOP_URL") or DEFAULT_RUNTIME_SETTINGS["copilot_desktop_url"]
        ).strip(),
        "copilot_reference_docs_path": str(
            raw.get("DOC_AUTOMATION_COPILOT_REFERENCE_DOCS_PATH") or DEFAULT_RUNTIME_SETTINGS["copilot_reference_docs_path"]
        ).strip(),
        "copilot_strict_model_safety": parse_bool(
            raw.get("DOC_AUTOMATION_COPILOT_STRICT_MODEL_SAFETY"),
            DEFAULT_RUNTIME_SETTINGS["copilot_strict_model_safety"],
        ),
        "copilot_open_wsl_remote": parse_bool(
            raw.get("DOC_AUTOMATION_COPILOT_OPEN_WSL_REMOTE"),
            DEFAULT_RUNTIME_SETTINGS["copilot_open_wsl_remote"],
        ),
        "copilot_vscode_window_mode": copilot_vscode_window_mode,
        "copilot_vscode_apply_settings": parse_bool(
            raw.get("DOC_AUTOMATION_COPILOT_VSCODE_APPLY_SETTINGS"),
            DEFAULT_RUNTIME_SETTINGS["copilot_vscode_apply_settings"],
        ),
        "copilot_vscode_settings_path": str(
            raw.get("DOC_AUTOMATION_COPILOT_VSCODE_SETTINGS_PATH") or DEFAULT_RUNTIME_SETTINGS["copilot_vscode_settings_path"]
        ).strip(),
        "copilot_vscode_permission_level": permission_level,
        "copilot_vscode_global_auto_approve": parse_bool(
            raw.get("DOC_AUTOMATION_COPILOT_VSCODE_GLOBAL_AUTO_APPROVE"),
            DEFAULT_RUNTIME_SETTINGS["copilot_vscode_global_auto_approve"],
        ),
        "copilot_vscode_auto_accept_edits_delay_ms": parse_int(
            raw.get("DOC_AUTOMATION_COPILOT_VSCODE_AUTO_ACCEPT_EDITS_DELAY_MS"),
            DEFAULT_RUNTIME_SETTINGS["copilot_vscode_auto_accept_edits_delay_ms"],
        ),
        "copilot_additional_read_access_folders": additional_read_access_folders,
        "copilot_additional_read_access_folders_text": "\n".join(additional_read_access_folders),
        "copilot_additional_read_access_folders_error": additional_folders_error,
        "context_capture_enabled": parse_bool(
            raw.get("DOC_AUTOMATION_CONTEXT_CAPTURE_ENABLED"),
            DEFAULT_RUNTIME_SETTINGS["context_capture_enabled"],
        ),
        "context_capture_root_mode": context_capture_root_mode,
        "context_capture_max_tree_items": max(
            1,
            min(
                200,
                parse_int(
                    raw.get("DOC_AUTOMATION_CONTEXT_CAPTURE_MAX_TREE_ITEMS"),
                    DEFAULT_RUNTIME_SETTINGS["context_capture_max_tree_items"],
                ),
            ),
        ),
        "context_capture_include_pr_diffs": parse_bool(
            raw.get("DOC_AUTOMATION_CONTEXT_CAPTURE_INCLUDE_PR_DIFFS"),
            DEFAULT_RUNTIME_SETTINGS["context_capture_include_pr_diffs"],
        ),
        "context_capture_workspace_scan_roots": context_capture_scan_roots,
        "context_capture_workspace_scan_roots_text": "\n".join(context_capture_scan_roots),
        "context_capture_workspace_scan_roots_error": capture_scan_roots_error,
        "default_reviewer_display_name": str(
            raw.get("DOC_AUTOMATION_DEFAULT_REVIEWER_DISPLAY_NAME") or DEFAULT_RUNTIME_SETTINGS["default_reviewer_display_name"]
        ).strip(),
        "default_reviewer_unique_name": str(
            raw.get("DOC_AUTOMATION_DEFAULT_REVIEWER_UNIQUE_NAME") or DEFAULT_RUNTIME_SETTINGS["default_reviewer_unique_name"]
        ).strip(),
        "default_reviewer_id": str(
            raw.get("DOC_AUTOMATION_DEFAULT_REVIEWER_ID") or DEFAULT_RUNTIME_SETTINGS["default_reviewer_id"]
        ).strip(),
        "reviewer_overrides": overrides,
        "reviewer_overrides_text": json.dumps(overrides, ensure_ascii=False, indent=2) if overrides else "{}",
        "reviewer_overrides_error": overrides_error,
        "env_path": str(ENV_PATH),
    }


def save_runtime_settings(
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
    if not server_host.strip():
        raise ValueError("Server host cannot be empty.")
    if server_port < 1 or server_port > 65535:
        raise ValueError("Server port must be between 1 and 65535.")
    if tfs_request_timeout_seconds < 5 or tfs_request_timeout_seconds > 120:
        raise ValueError("TFS request timeout must be between 5 and 120 seconds.")
    if automation_reconcile_interval_seconds < 5 or automation_reconcile_interval_seconds > 3600:
        raise ValueError("Automation reconcile interval must be between 5 and 3600 seconds.")
    if automation_discovery_interval_minutes < 1 or automation_discovery_interval_minutes > 1440:
        raise ValueError("Automation discovery interval must be between 1 and 1440 minutes.")
    if str(copilot_vscode_permission_level or "").strip() not in COPILOT_PERMISSION_LEVEL_OPTIONS:
        raise ValueError("Invalid VS Code Copilot permission level.")
    if str(copilot_provider or "").strip() not in COPILOT_PROVIDER_OPTIONS:
        raise ValueError("Invalid Copilot provider.")
    if str(execution_runtime or "").strip() not in EXECUTION_RUNTIME_OPTIONS:
        raise ValueError("Invalid execution runtime.")
    if str(copilot_vscode_window_mode or "").strip() not in COPILOT_VSCODE_WINDOW_MODE_OPTIONS:
        raise ValueError("Invalid VS Code window mode.")
    if copilot_vscode_auto_accept_edits_delay_ms < 0 or copilot_vscode_auto_accept_edits_delay_ms > 60000:
        raise ValueError("VS Code auto-accept edit delay must be between 0 and 60000 milliseconds.")
    if str(context_capture_root_mode or "").strip() not in CONTEXT_CAPTURE_ROOT_MODE_OPTIONS:
        raise ValueError("Invalid context capture root mode.")
    if context_capture_max_tree_items < 1 or context_capture_max_tree_items > 200:
        raise ValueError("Context capture max tree items must be between 1 and 200.")

    try:
        overrides_value = json.loads(reviewer_overrides_text.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Reviewer overrides JSON is invalid: {exc}") from exc
    content_team_members = normalize_string_list(content_team_members_text)
    additional_read_access_folders = normalize_string_list(copilot_additional_read_access_folders_text)
    context_capture_workspace_scan_roots = normalize_string_list(context_capture_workspace_scan_roots_text)
    if not context_capture_workspace_scan_roots:
        context_capture_workspace_scan_roots = list(DEFAULT_RUNTIME_SETTINGS["context_capture_workspace_scan_roots"])

    overrides = normalize_reviewer_overrides(overrides_value)
    env_values = {
        "DOC_AUTOMATION_SERVER_HOST": server_host.strip(),
        "DOC_AUTOMATION_SERVER_PORT": str(server_port),
        "DOC_AUTOMATION_SERVER_AUTO_PORT": "true" if auto_port else "false",
        "DOC_AUTOMATION_TFS_REQUEST_TIMEOUT_SECONDS": str(tfs_request_timeout_seconds),
        "DOC_AUTOMATION_TFS_VERIFY_SSL": "true" if tfs_verify_ssl else "false",
        "DOC_AUTOMATION_TFS_CA_BUNDLE_PATH": tfs_ca_bundle_path.strip(),
        "DOC_AUTOMATION_RUNNER_ENABLED": "true" if automation_runner_enabled else "false",
        "DOC_AUTOMATION_RECONCILE_INTERVAL_SECONDS": str(automation_reconcile_interval_seconds),
        "DOC_AUTOMATION_CONTINUOUS_MODE": "true" if automation_continuous_mode else "false",
        "DOC_AUTOMATION_DISCOVERY_INTERVAL_MINUTES": str(automation_discovery_interval_minutes),
        "DOC_AUTOMATION_CONTENT_TEAM_MEMBERS_JSON": json.dumps(content_team_members, ensure_ascii=False),
        "DOC_AUTOMATION_DEFAULT_CURRENT_ITERATION_ONLY": "true" if default_current_iteration_only else "false",
        "DOC_AUTOMATION_EXECUTION_RUNTIME": execution_runtime.strip(),
        "DOC_AUTOMATION_COPILOT_WSL_DISTRO": copilot_wsl_distro.strip(),
        "DOC_AUTOMATION_COPILOT_PROVIDER": copilot_provider.strip(),
        "DOC_AUTOMATION_COPILOT_MODEL_NAME": copilot_model_name.strip(),
        "DOC_AUTOMATION_COPILOT_AGENT_NAME": copilot_agent_name.strip(),
        "DOC_AUTOMATION_COPILOT_AUTO_LAUNCH": "true" if copilot_auto_launch else "false",
        "DOC_AUTOMATION_COPILOT_PROMPT_TEMPLATE": copilot_prompt_template.strip(),
        "DOC_AUTOMATION_COPILOT_CLI_COMMAND_TEMPLATE": copilot_cli_command_template.strip(),
        "DOC_AUTOMATION_FINAL_REPORTS_PATH": final_reports_path.strip(),
        "DOC_AUTOMATION_COPILOT_DESKTOP_URL": copilot_desktop_url.strip(),
        "DOC_AUTOMATION_COPILOT_REFERENCE_DOCS_PATH": copilot_reference_docs_path.strip(),
        "DOC_AUTOMATION_COPILOT_STRICT_MODEL_SAFETY": "true" if copilot_strict_model_safety else "false",
        "DOC_AUTOMATION_COPILOT_OPEN_WSL_REMOTE": "true" if copilot_open_wsl_remote else "false",
        "DOC_AUTOMATION_COPILOT_VSCODE_WINDOW_MODE": copilot_vscode_window_mode.strip(),
        "DOC_AUTOMATION_COPILOT_VSCODE_APPLY_SETTINGS": "true" if copilot_vscode_apply_settings else "false",
        "DOC_AUTOMATION_COPILOT_VSCODE_SETTINGS_PATH": copilot_vscode_settings_path.strip(),
        "DOC_AUTOMATION_COPILOT_VSCODE_PERMISSION_LEVEL": copilot_vscode_permission_level.strip(),
        "DOC_AUTOMATION_COPILOT_VSCODE_GLOBAL_AUTO_APPROVE": "true" if copilot_vscode_global_auto_approve else "false",
        "DOC_AUTOMATION_COPILOT_VSCODE_AUTO_ACCEPT_EDITS_DELAY_MS": str(copilot_vscode_auto_accept_edits_delay_ms),
        "DOC_AUTOMATION_COPILOT_ADDITIONAL_READ_ACCESS_FOLDERS_JSON": json.dumps(additional_read_access_folders, ensure_ascii=False),
        "DOC_AUTOMATION_CONTEXT_CAPTURE_ENABLED": "true" if context_capture_enabled else "false",
        "DOC_AUTOMATION_CONTEXT_CAPTURE_ROOT_MODE": context_capture_root_mode.strip(),
        "DOC_AUTOMATION_CONTEXT_CAPTURE_MAX_TREE_ITEMS": str(context_capture_max_tree_items),
        "DOC_AUTOMATION_CONTEXT_CAPTURE_INCLUDE_PR_DIFFS": "true" if context_capture_include_pr_diffs else "false",
        "DOC_AUTOMATION_CONTEXT_CAPTURE_WORKSPACE_SCAN_ROOTS_JSON": json.dumps(context_capture_workspace_scan_roots, ensure_ascii=False),
        "DOC_AUTOMATION_DEFAULT_REVIEWER_DISPLAY_NAME": default_reviewer_display_name.strip(),
        "DOC_AUTOMATION_DEFAULT_REVIEWER_UNIQUE_NAME": default_reviewer_unique_name.strip(),
        "DOC_AUTOMATION_DEFAULT_REVIEWER_ID": default_reviewer_id.strip(),
        "DOC_AUTOMATION_REVIEWER_OVERRIDES_JSON": json.dumps(overrides, ensure_ascii=False),
    }
    save_env_values(env_values)
    return load_runtime_settings()


def resolve_vscode_settings_path(path_value: str = "") -> Path:
    candidate = str(path_value or "").strip()
    if candidate:
        return Path(candidate).expanduser()
    return VS_CODE_SETTINGS_PATH


def load_vscode_settings(path_value: str = "") -> Dict[str, Any]:
    settings_path = resolve_vscode_settings_path(path_value)
    if not settings_path.exists():
        return {}
    try:
        with open(settings_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"VS Code settings at '{settings_path}' are not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"VS Code settings at '{settings_path}' do not contain a JSON object.")
    return payload


def save_vscode_settings(settings: Dict[str, Any], path_value: str = "") -> Path:
    settings_path = resolve_vscode_settings_path(path_value)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_path, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, ensure_ascii=False, indent=4)
        handle.write("\n")
    return settings_path
