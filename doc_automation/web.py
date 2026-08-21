from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .orchestrator import AutomationOrchestrator
from .services import AutomationService, ServiceError
from .tfs_client import TfsApiError


APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(APP_DIR / "templates"))
SERVICE = AutomationService()
ORCHESTRATOR = AutomationOrchestrator(SERVICE)
app = FastAPI(title="TFS Documentation Automation MVP")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.on_event("startup")
def start_automation_orchestrator() -> None:
    ORCHESTRATOR.start()


@app.on_event("shutdown")
def stop_automation_orchestrator() -> None:
    ORCHESTRATOR.stop()


def _safe_flash_message(message: str, *, max_length: int = 1200) -> str:
    clean_message = str(message or "").strip()
    if len(clean_message) <= max_length:
        return clean_message
    return clean_message[:max_length].rstrip() + " ... [truncated]"


def _summarize_bulk_result_details(results: list[dict[str, object]], *, max_items: int = 3) -> str:
    interesting_statuses = {"error", "branch-error", "automation-error", "needs-plan"}
    details: list[str] = []
    for result in results:
        status = str(result.get("status") or "").strip()
        if status not in interesting_statuses:
            continue
        work_item_id = str(result.get("work_item_id") or "-")
        detail = str(result.get("detail") or status).strip()
        details.append(f"WI {work_item_id}: {detail}")
        if len(details) >= max_items:
            break
    remaining = sum(
        1
        for result in results
        if str(result.get("status") or "").strip() in interesting_statuses
    ) - len(details)
    if remaining > 0:
        details.append(f"{remaining} more item(s) need attention.")
    return " ".join(details)


def _parse_optional_bool(value: str) -> bool | None:
    token = str(value or "").strip().lower()
    if not token:
        return None
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return None


def _redirect_to_dashboard(
    request: Request,
    *,
    portal: str,
    iteration_path: str,
    current_iteration_only: bool | None,
    hide_closed: bool | None,
    message: str,
    level: str,
) -> RedirectResponse:
    query_params = {
        "portal": portal,
        "iteration_path": iteration_path,
        "message": _safe_flash_message(message),
        "level": level,
    }
    if current_iteration_only is not None:
        query_params["current_iteration_only"] = "true" if current_iteration_only else "false"
        query_params["filter_mode"] = "manual"
    if hide_closed is not None:
        query_params["hide_closed"] = "true" if hide_closed else "false"
        query_params["visibility_mode"] = "manual"
    query = urlencode(query_params)
    url = f"{request.url_for('dashboard')}?{query}"
    return RedirectResponse(url=url, status_code=303)


def _redirect_to_settings(
    request: Request,
    *,
    portal: str,
    message: str,
    level: str,
    tab: str = "",
) -> RedirectResponse:
    query_params = {
        "portal": portal,
        "message": _safe_flash_message(message),
        "level": level,
    }
    clean_tab = _normalize_settings_tab(tab)
    if clean_tab:
        query_params["tab"] = clean_tab
    query = urlencode(query_params)
    url = f"{request.url_for('settings_page')}?{query}"
    return RedirectResponse(url=url, status_code=303)


def _normalize_settings_tab(tab: str) -> str:
    clean_tab = (tab or "").strip().lower()
    if clean_tab in {"connection", "automation", "cherry-picks", "runtime"}:
        return clean_tab
    return "connection"


def _preflight_attention_message(label: str, preflight: object) -> str:
    if not isinstance(preflight, dict):
        return ""
    if bool(preflight.get("ok", True)) and str(preflight.get("status") or "") not in {"warning"}:
        return ""
    message = str(preflight.get("message") or "Review the diagnostic details.").strip()
    remediation = preflight.get("remediation")
    if isinstance(remediation, dict) and remediation.get("message"):
        message = f"{message} {str(remediation.get('message')).strip()}"
    login = preflight.get("login")
    if isinstance(login, dict) and login.get("message"):
        message = f"{message} {str(login.get('message')).strip()}"
    return f"{label}: {message}"


@app.get("/", response_class=HTMLResponse, name="dashboard")
def dashboard(
    request: Request,
    portal: str = "",
    iteration_path: str = "",
    current_iteration_only: str = "",
    filter_mode: str = "",
    hide_closed: str = "",
    visibility_mode: str = "",
    page: int = 1,
    page_size: int = 10,
    message: str = "",
    level: str = "info",
) -> HTMLResponse:
    current_iteration_only_value = _parse_optional_bool(current_iteration_only)
    if filter_mode == "manual" and current_iteration_only_value is None:
        current_iteration_only_value = False
    hide_closed_value = _parse_optional_bool(hide_closed)
    if visibility_mode == "manual" and hide_closed_value is None:
        hide_closed_value = False
    context = SERVICE.load_dashboard_with_filters(
        portal_name=portal,
        iteration_path=iteration_path,
        current_iteration_only=current_iteration_only_value,
        hide_closed=bool(hide_closed_value),
        page=page,
        page_size=page_size,
    )
    active_automation_items = [
        item
        for item in list(context.get("items") or [])
        if bool(item.get("is_auto_flow_active"))
    ]
    active_automation = SERVICE.get_local_status_snapshots(
        portal_name=str(context.get("selected_portal") or portal),
        work_item_ids=[int(item["id"]) for item in active_automation_items],
    )
    active_item_titles = {
        int(item["id"]): str(item.get("title") or "Work item")
        for item in active_automation_items
    }
    for active_item in active_automation:
        active_item["title"] = active_item_titles.get(int(active_item["id"]), "Work item")
    runtime_settings = context.get("runtime_settings") or {}
    auto_refresh_seconds = 0
    if active_automation and bool(runtime_settings.get("automation_runner_enabled")):
        auto_refresh_seconds = max(
            10,
            min(15, int(runtime_settings.get("automation_reconcile_interval_seconds") or 15)),
        )
    context.update(
        {
            "request": request,
            "page_title": "TFS Documentation Automation MVP",
            "message": _safe_flash_message(message),
            "level": level,
            "active_page": "dashboard",
            "automation_runner": ORCHESTRATOR.snapshot(),
            "active_automation": active_automation,
            "active_automation_count": len(active_automation),
            "auto_refresh_seconds": auto_refresh_seconds,
        }
    )
    return TEMPLATES.TemplateResponse(request, "dashboard.html", context)


@app.get("/cherry-picks", response_class=HTMLResponse, name="cherry_picks")
def cherry_picks(
    request: Request,
    portal: str = "",
    load: str = "true",
    lookback_days: int = 0,
    max_prs_per_branch: int = 0,
    scope: str = "All",
    status: str = "All",
    branch: str = "All",
    sort_by: str = "Severity",
    descending: str = "",
    message: str = "",
    level: str = "info",
) -> HTMLResponse:
    parsed_load = _parse_optional_bool(load)
    should_load = True if parsed_load is None else parsed_load
    try:
        context = SERVICE.load_cherry_pick_dashboard(
            portal_name=portal,
            load=should_load,
            lookback_days=lookback_days if lookback_days > 0 else None,
            max_prs_per_branch=max_prs_per_branch if max_prs_per_branch > 0 else None,
            scope=scope,
            status=status,
            branch=branch,
            sort_by=sort_by,
            descending=_parse_optional_bool(descending) is True,
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        context = SERVICE.load_cherry_pick_dashboard(
            portal_name=portal,
            load=False,
            lookback_days=lookback_days if lookback_days > 0 else None,
            max_prs_per_branch=max_prs_per_branch if max_prs_per_branch > 0 else None,
            scope=scope,
            status=status,
            branch=branch,
            sort_by=sort_by,
            descending=_parse_optional_bool(descending) is True,
        )
        message = f"Failed to load Cherry Pick analysis: {exc}"
        level = "error"
    context.update(
        {
            "request": request,
            "page_title": "TFS Documentation Automation MVP",
            "message": _safe_flash_message(message),
            "level": level,
            "active_page": "cherry_picks",
            "automation_runner": ORCHESTRATOR.snapshot(),
        }
    )
    return TEMPLATES.TemplateResponse(request, "cherry_picks.html", context)


@app.get("/work-items/{work_item_id}/details", response_class=HTMLResponse)
def work_item_detail(
    request: Request,
    work_item_id: int,
    portal: str = "",
    iteration_path: str = "",
    current_iteration_only: str = "",
    hide_closed: str = "",
) -> HTMLResponse:
    context = SERVICE.load_work_item_detail(
        portal_name=portal,
        work_item_id=work_item_id,
    )
    context.update(
        {
            "request": request,
            "selected_iteration": iteration_path,
            "current_iteration_only": bool(_parse_optional_bool(current_iteration_only)),
            "hide_closed": bool(_parse_optional_bool(hide_closed)),
        }
    )
    return TEMPLATES.TemplateResponse(request, "_work_item_details.html", context)


@app.get("/work-items/{work_item_id}/report", response_class=HTMLResponse)
def final_report(
    request: Request,
    work_item_id: int,
    portal: str = "",
) -> HTMLResponse:
    try:
        context = SERVICE.get_final_report(
            portal_name=portal,
            work_item_id=work_item_id,
        )
    except ServiceError as exc:
        context = {
            "selected_portal": portal,
            "work_item_id": work_item_id,
            "report_path": "",
            "report_html": "",
            "error": str(exc),
        }
    context.update(
        {
            "request": request,
            "page_title": f"Final Report - WI {work_item_id}",
            "active_page": "dashboard",
        }
    )
    return TEMPLATES.TemplateResponse(request, "report.html", context)


@app.get("/work-items/{work_item_id}/capture", response_class=HTMLResponse)
def context_capture_package(
    request: Request,
    work_item_id: int,
    portal: str = "",
    file: str = "summary",
) -> HTMLResponse:
    try:
        context = SERVICE.get_context_capture_package(
            portal_name=portal,
            work_item_id=work_item_id,
            selected_file=file,
        )
    except ServiceError as exc:
        context = {
            "selected_portal": portal,
            "work_item_id": work_item_id,
            "selected_file": file,
            "capture_files": {},
            "capture_directory": "",
            "capture_path": "",
            "capture_content": "",
            "capture_html": "",
            "is_markdown": False,
            "error": str(exc),
        }
    context.update(
        {
            "request": request,
            "page_title": f"Context Package - WI {work_item_id}",
            "active_page": "dashboard",
        }
    )
    return TEMPLATES.TemplateResponse(request, "capture.html", context)


@app.get("/tfs-assets")
def tfs_asset(
    portal: str = "",
    url: str = "",
) -> Response:
    try:
        asset = SERVICE.fetch_tfs_asset(portal_name=portal, url=url)
    except ServiceError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    except TfsApiError as exc:
        return Response(str(exc), status_code=502, media_type="text/plain")
    return Response(
        content=asset.get("content") or b"",
        media_type=str(asset.get("content_type") or "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.get("/settings", response_class=HTMLResponse, name="settings_page")
def settings_page(
    request: Request,
    portal: str = "",
    tab: str = "connection",
    message: str = "",
    level: str = "info",
) -> HTMLResponse:
    active_settings_tab = _normalize_settings_tab(tab)
    context = SERVICE.get_settings_context(portal)
    context.update(
        {
            "request": request,
            "page_title": "Settings",
            "message": _safe_flash_message(message),
            "level": level,
            "active_page": "settings",
            "active_settings_tab": active_settings_tab,
            "automation_runner": ORCHESTRATOR.snapshot(),
        }
    )
    return TEMPLATES.TemplateResponse(request, "settings.html", context)


@app.get("/work-items/statuses")
def work_item_statuses(
    portal: str = "",
    work_item_ids: str = "",
) -> dict[str, object]:
    parsed_ids: list[int] = []
    for raw_value in str(work_item_ids or "").split(","):
        token = raw_value.strip()
        if not token:
            continue
        try:
            parsed_ids.append(int(token))
        except ValueError:
            continue
    return {
        "items": SERVICE.get_local_status_snapshots(
            portal_name=portal,
            work_item_ids=parsed_ids,
        )
    }


@app.post("/automation/run-cycle")
def run_automation_cycle(
    request: Request,
    portal: str = Form(""),
) -> RedirectResponse:
    try:
        result = ORCHESTRATOR.run_once(force_discovery=True)
        reconcile_total = int(result.get("reconcile", {}).get("total") or 0)
        discovery_total = int(result.get("discovery", {}).get("total") or 0)
        return _redirect_to_settings(
            request,
            portal=portal,
            message=(
                "Automation cycle completed: "
                f"{reconcile_total} persisted flow(s) reconciled, "
                f"{discovery_total} discovered flow(s) started."
            ),
            level="success",
            tab="runtime",
        )
    except Exception as exc:
        return _redirect_to_settings(
            request,
            portal=portal,
            message=f"Failed to run the automation cycle: {exc}",
            level="error",
            tab="runtime",
        )


@app.post("/auth/pat")
def set_pat(
    request: Request,
    portal: str = Form(...),
    iteration_path: str = Form(""),
    current_iteration_only: str = Form(""),
    hide_closed: str = Form(""),
    pat: str = Form(""),
    redirect_target: str = Form("dashboard"),
    active_settings_tab: str = Form("connection"),
) -> RedirectResponse:
    SERVICE.set_portal_pat(portal, pat)
    action = "updated" if pat.strip() else "cleared"
    if redirect_target == "settings":
        return _redirect_to_settings(
            request,
            portal=portal,
            message=f"PAT {action} for portal '{portal}'.",
            level="success",
            tab=active_settings_tab,
        )
    return _redirect_to_dashboard(
        request,
        portal=portal,
        iteration_path=iteration_path,
        current_iteration_only=_parse_optional_bool(current_iteration_only),
        hide_closed=_parse_optional_bool(hide_closed),
        message=f"PAT {action} for portal '{portal}'.",
        level="success",
    )


@app.post("/auth/git-credentials")
def setup_git_credentials(
    request: Request,
    portal: str = Form(...),
    tfs_username: str = Form(""),
    tfs_token: str = Form(""),
    active_settings_tab: str = Form("connection"),
) -> RedirectResponse:
    try:
        result = SERVICE.setup_portal_git_credentials(
            portal_name=portal,
            username=tfs_username,
            token=tfs_token,
        )
        return _redirect_to_settings(
            request,
            portal=portal,
            message=str(result.get("message") or "TFS Git credentials were configured."),
            level="success" if bool(result.get("ok")) else "warning",
            tab=active_settings_tab,
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_settings(
            request,
            portal=portal,
            message=f"Failed to configure TFS Git credentials: {exc}",
            level="error",
            tab=active_settings_tab,
        )


@app.post("/settings/portal/workspace")
def save_portal_workspace(
    request: Request,
    portal: str = Form(...),
    copilot_workspace_path: str = Form(...),
    iteration_path: str = Form(""),
    current_iteration_only: str = Form(""),
    hide_closed: str = Form(""),
) -> RedirectResponse:
    try:
        saved_portal = SERVICE.save_portal_workspace(
            portal_name=portal,
            copilot_workspace_path=copilot_workspace_path,
        )
        return _redirect_to_dashboard(
            request,
            portal=saved_portal["repository"],
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Target workspace set to {saved_portal.get('copilot_workspace_path')}.",
            level="success",
        )
    except (ServiceError, RuntimeError) as exc:
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Failed to save target workspace: {exc}",
            level="error",
        )


@app.post("/settings/portal")
def save_portal_settings(
    request: Request,
    current_repository: str = Form(...),
    base_url: str = Form(...),
    project: str = Form(...),
    repository: str = Form(...),
    work_item_project: str = Form(...),
    work_item_team: str = Form(""),
    work_item_area_path: str = Form(""),
    copilot_workspace_path: str = Form(""),
    api_version: str = Form(...),
    branch_chain_text: str = Form(""),
    auth_mode: str = Form(...),
    lookback_days: int = Form(...),
    max_prs_per_branch: int = Form(...),
    verify_work_items_via_api: bool = Form(False),
    cherry_pick_skip_labels_text: str = Form(""),
    active_settings_tab: str = Form("connection"),
) -> RedirectResponse:
    try:
        saved_portal = SERVICE.save_portal_settings(
            current_repository=current_repository,
            base_url=base_url,
            project=project,
            repository=repository,
            work_item_project=work_item_project,
            work_item_team=work_item_team,
            work_item_area_path=work_item_area_path,
            copilot_workspace_path=copilot_workspace_path,
            api_version=api_version,
            branch_chain_text=branch_chain_text,
            auth_mode=auth_mode,
            lookback_days=lookback_days,
            max_prs_per_branch=max_prs_per_branch,
            verify_work_items_via_api=verify_work_items_via_api,
            cherry_pick_skip_labels_text=cherry_pick_skip_labels_text,
        )
        preflight_warning = _preflight_attention_message(
            "TFS Git credentials",
            saved_portal.get("_git_credentials_preflight"),
        )
        message = f"Saved settings for portal '{saved_portal['repository']}'."
        level = "success"
        if preflight_warning:
            message = f"{message} {preflight_warning}"
            level = "warning"
        return _redirect_to_settings(
            request,
            portal=saved_portal["repository"],
            message=message,
            level=level,
            tab=active_settings_tab,
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_settings(
            request,
            portal=current_repository,
            message=f"Failed to save portal settings: {exc}",
            level="error",
            tab=active_settings_tab,
        )


@app.post("/settings/runtime")
def save_runtime_settings(
    request: Request,
    portal: str = Form(...),
    server_host: str = Form(...),
    server_port: int = Form(...),
    auto_port: bool = Form(False),
    tfs_request_timeout_seconds: int = Form(15),
    tfs_verify_ssl: bool = Form(False),
    tfs_ca_bundle_path: str = Form(""),
    automation_runner_enabled: bool = Form(False),
    automation_reconcile_interval_seconds: int = Form(30),
    automation_continuous_mode: bool = Form(False),
    automation_discovery_interval_minutes: int = Form(5),
    content_team_members_text: str = Form(""),
    default_current_iteration_only: bool = Form(False),
    execution_runtime: str = Form("devcontainer"),
    copilot_wsl_distro: str = Form("Ubuntu"),
    copilot_provider: str = Form("m365_desktop"),
    copilot_model_name: str = Form("CM GPT"),
    copilot_agent_name: str = Form("CM GPT"),
    copilot_cli_host: str = Form("https://github.com"),
    copilot_auto_launch: bool = Form(False),
    copilot_prompt_template: str = Form(""),
    copilot_cli_command_template: str = Form(""),
    final_reports_path: str = Form(""),
    copilot_desktop_url: str = Form("https://m365.cloud.microsoft/chat"),
    copilot_reference_docs_path: str = Form("/workspaces/Documentation"),
    copilot_strict_model_safety: bool = Form(False),
    copilot_open_wsl_remote: bool = Form(False),
    copilot_vscode_window_mode: str = Form("new"),
    copilot_vscode_apply_settings: bool = Form(False),
    copilot_vscode_settings_path: str = Form(""),
    copilot_vscode_permission_level: str = Form("autopilot"),
    copilot_vscode_global_auto_approve: bool = Form(False),
    copilot_vscode_auto_accept_edits_delay_ms: int = Form(1000),
    copilot_additional_read_access_folders_text: str = Form(""),
    context_capture_enabled: bool = Form(False),
    context_capture_root_mode: str = Form("parent"),
    context_capture_max_tree_items: int = Form(50),
    context_capture_include_pr_diffs: bool = Form(False),
    context_capture_workspace_scan_roots_text: str = Form("/workspaces"),
    default_reviewer_display_name: str = Form(""),
    default_reviewer_unique_name: str = Form(""),
    default_reviewer_id: str = Form(""),
    reviewer_overrides_text: str = Form("{}"),
    active_settings_tab: str = Form("automation"),
) -> RedirectResponse:
    try:
        saved_settings = SERVICE.save_runtime_settings(
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
            copilot_cli_host=copilot_cli_host,
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
        success_message = "Runtime settings saved to .env."
        if copilot_vscode_apply_settings:
            success_message = "Runtime settings saved to .env and applied to local VS Code Copilot settings."
        message_level = "success"
        preflight_warnings: list[str] = []
        preflight = saved_settings.get("_agent_provider_preflight") if isinstance(saved_settings, dict) else None
        if isinstance(preflight, dict) and not bool(preflight.get("ok", True)):
            preflight_warnings.append(
                _preflight_attention_message("Agent provider", preflight)
                or "Agent provider: Unknown provider preflight error."
            )
        elif isinstance(preflight, dict) and str(preflight.get("status") or "") == "warning":
            preflight_warnings.append(
                _preflight_attention_message("Agent provider", preflight)
                or "Agent provider: Review the provider diagnostics."
            )
        credential_preflight = SERVICE.check_portal_credentials(portal)
        credential_warning = _preflight_attention_message("TFS Git credentials", credential_preflight)
        if credential_warning:
            preflight_warnings.append(credential_warning)
        if preflight_warnings:
            success_message = (
                "Runtime settings saved, but one or more setup checks need attention. "
                + " ".join(preflight_warnings)
            )
            message_level = "warning"
        return _redirect_to_settings(
            request,
            portal=portal,
            message=success_message,
            level=message_level,
            tab=active_settings_tab,
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_settings(
            request,
            portal=portal,
            message=f"Failed to save runtime settings: {exc}",
            level="error",
            tab=active_settings_tab,
        )


@app.post("/work-items/{work_item_id}/plan")
def save_plan(
    request: Request,
    work_item_id: int,
    portal: str = Form(...),
    iteration_path: str = Form(""),
    current_iteration_only: str = Form(""),
    hide_closed: str = Form(""),
    triage_status: str = Form("pending"),
    selected_base_branch: str = Form(""),
    work_type: str = Form("task"),
    planned_branch_name: str = Form(""),
) -> RedirectResponse:
    try:
        plan = SERVICE.save_plan(
            portal_name=portal,
            work_item_id=work_item_id,
            iteration_path=iteration_path,
            triage_status=triage_status,
            selected_base_branch=selected_base_branch,
            work_type=work_type,
            planned_branch_name=planned_branch_name,
        )
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Saved plan for WI {work_item_id}: {plan['branch_name']}",
            level="success",
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Failed to save plan for WI {work_item_id}: {exc}",
            level="error",
        )


@app.post("/work-items/{work_item_id}/branch")
def create_branch(
    request: Request,
    work_item_id: int,
    portal: str = Form(...),
    iteration_path: str = Form(""),
    current_iteration_only: str = Form(""),
    hide_closed: str = Form(""),
    triage_status: str = Form("pending"),
    selected_base_branch: str = Form(""),
    work_type: str = Form("task"),
    planned_branch_name: str = Form(""),
) -> RedirectResponse:
    try:
        result = SERVICE.create_branch(
            portal_name=portal,
            work_item_id=work_item_id,
            iteration_path=iteration_path,
            triage_status=triage_status,
            selected_base_branch=selected_base_branch,
            work_type=work_type,
            planned_branch_name=planned_branch_name,
        )
        label = "already existed" if result["status"] == "exists" else "was created"
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Branch '{result['name']}' {label}.",
            level="success",
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Failed to create branch for WI {work_item_id}: {exc}",
            level="error",
        )


@app.post("/work-items/{work_item_id}/rerun")
def start_rerun(
    request: Request,
    work_item_id: int,
    portal: str = Form(...),
    iteration_path: str = Form(""),
    current_iteration_only: str = Form(""),
    hide_closed: str = Form(""),
    selected_base_branch: str = Form(""),
    work_type: str = Form("task"),
) -> RedirectResponse:
    try:
        result = SERVICE.start_rerun_automatic_flow(
            portal_name=portal,
            work_item_id=work_item_id,
            iteration_path=iteration_path,
            selected_base_branch=selected_base_branch,
            work_type=work_type,
        )
        detail = str(result.get("detail") or "").strip()
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=(
                f"WI {work_item_id}: rerun started on branch '{result['branch_name']}'. "
                f"{detail}"
            ).strip(),
            level="success",
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Failed to start rerun for WI {work_item_id}: {exc}",
            level="error",
        )


@app.post("/work-items/{work_item_id}/draft-pr")
def create_draft_pr(
    request: Request,
    work_item_id: int,
    portal: str = Form(...),
    iteration_path: str = Form(""),
    current_iteration_only: str = Form(""),
    hide_closed: str = Form(""),
    triage_status: str = Form("pending"),
    selected_base_branch: str = Form(""),
    work_type: str = Form("task"),
    planned_branch_name: str = Form(""),
) -> RedirectResponse:
    try:
        result = SERVICE.create_draft_pr(
            portal_name=portal,
            work_item_id=work_item_id,
            iteration_path=iteration_path,
            triage_status=triage_status,
            selected_base_branch=selected_base_branch,
            work_type=work_type,
            planned_branch_name=planned_branch_name,
        )
        if result.get("status") == "summary-repair-launched":
            return _redirect_to_dashboard(
                request,
                portal=portal,
                iteration_path=iteration_path,
                current_iteration_only=_parse_optional_bool(current_iteration_only),
                hide_closed=_parse_optional_bool(hide_closed),
                message=(
                    f"WI {work_item_id}: a reporting-only agent repair started to restore the missing Draft PR summary. "
                    "The pipeline will create the Draft PR when that result is ready."
                ),
                level="warning",
            )
        state_label = "reused existing draft PR" if result["status"] == "exists" else "created draft PR"
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"WI {work_item_id}: {state_label} #{result['pull_request_id']}.",
            level="success",
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Failed to create draft PR for WI {work_item_id}: {exc}",
            level="error",
        )


@app.post("/work-items/{work_item_id}/agent-result")
def check_agent_result(
    request: Request,
    work_item_id: int,
    portal: str = Form(...),
    iteration_path: str = Form(""),
    current_iteration_only: str = Form(""),
    hide_closed: str = Form(""),
) -> RedirectResponse:
    try:
        result = SERVICE.check_agent_result(
            portal_name=portal,
            work_item_id=work_item_id,
        )
        if result.get("green_light"):
            message = f"WI {work_item_id}: agent result has green light and passed dashboard validation. Ready to commit and push."
            level = "success"
        elif str(result.get("status") or "").strip().lower() == "repair_launched":
            message = f"WI {work_item_id}: automatic agent repair was launched. The background worker will continue when the new result is ready."
            level = "warning"
        elif str(result.get("status") or "").strip().lower() == "needs_agent_fix":
            message = f"WI {work_item_id}: agent result needs a fix. {result.get('error') or ''}".strip()
            level = "error"
        else:
            message = f"WI {work_item_id}: waiting for agent result. {result.get('error') or ''}".strip()
            level = "warning"
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=message,
            level=level,
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Failed to check agent result for WI {work_item_id}: {exc}",
            level="error",
        )


@app.post("/work-items/{work_item_id}/commit-push")
def commit_and_push_agent_result(
    request: Request,
    work_item_id: int,
    portal: str = Form(...),
    iteration_path: str = Form(""),
    current_iteration_only: str = Form(""),
    hide_closed: str = Form(""),
) -> RedirectResponse:
    try:
        result = SERVICE.commit_and_push_agent_result(
            portal_name=portal,
            work_item_id=work_item_id,
        )
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"WI {work_item_id}: pushed commit {result.get('commit') or '-'}. Draft PR can now be created.",
            level="success",
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Failed to commit and push WI {work_item_id}: {exc}",
            level="error",
        )


@app.post("/work-items/{work_item_id}/copilot")
def launch_copilot_session(
    request: Request,
    work_item_id: int,
    portal: str = Form(...),
    iteration_path: str = Form(""),
    current_iteration_only: str = Form(""),
    hide_closed: str = Form(""),
    triage_status: str = Form("pending"),
    selected_base_branch: str = Form(""),
    work_type: str = Form("task"),
    planned_branch_name: str = Form(""),
) -> RedirectResponse:
    try:
        result = SERVICE.launch_copilot_session(
            portal_name=portal,
            work_item_id=work_item_id,
            iteration_path=iteration_path,
            triage_status=triage_status,
            selected_base_branch=selected_base_branch,
            work_type=work_type,
            planned_branch_name=planned_branch_name,
        )
        action_label = "session opened" if result["status"] == "launched" else "automation prepared"
        tracked_changes = list(result.get("tracked_changes") or [])
        if result.get("status") == "desktop_prepared":
            message = (
                f"WI {work_item_id}: CM GPT Desktop handoff prepared for branch '{result['branch_name']}'. "
                f"The prompt was copied to the clipboard and saved at {result['desktop_prompt_path']}. "
                "Open the CM GPT agent in Microsoft 365 Copilot Desktop, paste the prompt, and run it manually. "
                "This provider cannot edit the local repository automatically from the dashboard."
            )
            result_level = "warning"
        elif tracked_changes:
            message = (
                f"WI {work_item_id}: CM GPT {action_label} for branch '{result['branch_name']}'. "
                f"Tracked repo changes were detected."
            )
            result_level = "success"
        else:
            message = (
                f"WI {work_item_id}: CM GPT {action_label} for branch '{result['branch_name']}'. "
                "Waiting for agent-result.json green light; the automatic worker will push and create the draft PR when it is ready."
            )
            result_level = "success"
        if result.get("prompt_path") and result["status"] == "prepared":
            message = (
                f"WI {work_item_id}: CM GPT handoff prepared for branch '{result['branch_name']}'. "
                f"Verify the active model is CM GPT, then run the generated prompt at {result['prompt_path']}."
            )
            result_level = "warning"
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=message,
            level=result_level,
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Failed to run CM GPT automation for WI {work_item_id}: {exc}",
            level="error",
        )


@app.post("/work-items/auto-flow")
def run_auto_flow(
    request: Request,
    portal: str = Form(...),
    iteration_path: str = Form(""),
    current_iteration_only: str = Form(""),
    hide_closed: str = Form(""),
    selected_work_item_ids: list[int] = Form([]),
) -> RedirectResponse:
    try:
        result = SERVICE.run_bulk_auto_flow(
            portal_name=portal,
            work_item_ids=selected_work_item_ids,
            iteration_path=iteration_path,
        )
        summary = result["summary"]
        message = (
            f"Automatic flow processed {result['total']} items: "
            f"{summary['completed']} completed, "
            f"{summary.get('queued', 0)} queued, "
            f"{summary['agent_running']} waiting for agent result, "
            f"{summary['already_has_pr']} already had PRs, "
            f"{summary['needs_plan']} need plan, "
            f"{summary['errors']} errors."
        )
        detail_message = _summarize_bulk_result_details(result["results"])
        if detail_message:
            message = f"{message} {detail_message}"
        level = "success" if summary["errors"] == 0 and summary["needs_plan"] == 0 else "warning"
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=message,
            level=level,
        )
    except (ServiceError, TfsApiError, RuntimeError) as exc:
        return _redirect_to_dashboard(
            request,
            portal=portal,
            iteration_path=iteration_path,
            current_iteration_only=_parse_optional_bool(current_iteration_only),
            hide_closed=_parse_optional_bool(hide_closed),
            message=f"Failed to run the automatic flow: {exc}",
            level="error",
        )
