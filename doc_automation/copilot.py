from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import quote


class CopilotIntegrationError(RuntimeError):
    """Raised when the CM GPT handoff cannot be prepared safely."""


UNC_WSL_RE = re.compile(r"^\\\\wsl(?:\.localhost)?\\([^\\]+)\\?(.*)$", re.IGNORECASE)
DOCX_RE = re.compile(r"\b[^\\/\s<>:\"|?*]+\.(?:docx|docm|doc)\b", re.IGNORECASE)
IMG_SRC_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
CUSTOM_AGENT_FILE_BASENAME = "cmf-tfs-doc-automation"
WORKSPACE_CONTEXT_ROOT = ".automation-context/copilot"
ISOLATED_WORKTREE_ROOT = "/workspaces/.content-ai-worktrees"
VSCODE_BRIDGE_STATUS_FILE = "bridge-status.json"
EXECUTION_RUNTIME_DEVCONTAINER = "devcontainer"
EXECUTION_RUNTIME_WINDOWS_HOST = "windows_host"
EXECUTION_RUNTIME_OPTIONS = {EXECUTION_RUNTIME_DEVCONTAINER, EXECUTION_RUNTIME_WINDOWS_HOST}
_EXECUTION_RUNTIME: ContextVar[str] = ContextVar(
    "doc_automation_execution_runtime",
    default=EXECUTION_RUNTIME_DEVCONTAINER,
)


def _yaml_string(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = text.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _shell_quote(value: str) -> str:
    return shlex.quote(str(value or ""))


def normalize_execution_runtime(value: str) -> str:
    token = str(value or "").strip()
    if token in EXECUTION_RUNTIME_OPTIONS:
        return token
    return EXECUTION_RUNTIME_DEVCONTAINER


def current_execution_runtime() -> str:
    return normalize_execution_runtime(_EXECUTION_RUNTIME.get())


@contextmanager
def execution_runtime_scope(value: str):
    token = _EXECUTION_RUNTIME.set(normalize_execution_runtime(value))
    try:
        yield
    finally:
        _EXECUTION_RUNTIME.reset(token)


def _run_wsl_script(
    distro: str,
    script: str,
    *,
    input_text: str | None = None,
    timeout_seconds: int = 240,
) -> subprocess.CompletedProcess[str]:
    execution_runtime = current_execution_runtime()
    if execution_runtime == EXECUTION_RUNTIME_WINDOWS_HOST:
        command = ["wsl.exe"]
        if str(distro or "").strip():
            command.extend(["-d", str(distro).strip()])
        command.extend(["--", "bash", "-lc", str(script or "")])
    else:
        if os.name == "nt":
            raise CopilotIntegrationError(
                "Execution runtime is set to devcontainer, but the dashboard process is running on Windows. "
                "Run the dashboard inside the devcontainer or switch Execution Runtime to Windows host via WSL."
            )
        command = ["bash", "-lc", str(script or "")]
    try:
        if input_text is not None:
            binary_result = subprocess.run(
                command,
                input=input_text.encode("utf-8"),
                capture_output=True,
                text=False,
                timeout=timeout_seconds,
            )
            return subprocess.CompletedProcess(
                args=binary_result.args,
                returncode=binary_result.returncode,
                stdout=binary_result.stdout.decode("utf-8", errors="replace"),
                stderr=binary_result.stderr.decode("utf-8", errors="replace"),
            )
        return subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        if execution_runtime == EXECUTION_RUNTIME_WINDOWS_HOST:
            raise CopilotIntegrationError(
                "Execution runtime is set to Windows host via WSL, but `wsl.exe` is not available from this process. "
                "Run the dashboard on Windows or switch Execution Runtime to Devcontainer / native Linux."
            ) from exc
        raise CopilotIntegrationError(
            "Execution runtime is set to devcontainer, but `bash` is not available from this process."
        ) from exc


def read_wsl_text_file(distro: str, file_path: str, *, max_chars: int = 200000) -> str:
    clean_path = str(file_path or "").strip()
    if not clean_path:
        raise CopilotIntegrationError("A WSL file path is required.")
    if max_chars < 1:
        max_chars = 200000
    result = _run_wsl_script(
        distro,
        (
            f"target={_shell_quote(clean_path)}; "
            'if [ ! -f "$target" ]; then '
            'printf "File not found: %s\\n" "$target" >&2; exit 2; '
            "fi; "
            f"head -c {int(max_chars)} \"$target\""
        ),
        timeout_seconds=30,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise CopilotIntegrationError(detail or f"Could not read WSL file: {clean_path}")
    return result.stdout


def _run_windows_command(
    command: List[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )


def _run_windows_powershell(script: str, *, arguments: List[str] | None = None, timeout_seconds: int = 120) -> subprocess.CompletedProcess[str]:
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    if arguments:
        command.extend(arguments)
    return _run_windows_command(command, timeout_seconds=timeout_seconds)


def _resolve_wsl_home(distro: str) -> str:
    result = _run_wsl_script(distro, 'printf "%s" "$HOME"')
    home_path = result.stdout.strip()
    if result.returncode != 0 or not home_path:
        raise CopilotIntegrationError("Could not resolve the WSL home directory for the configured distro.")
    return home_path


def normalize_wsl_target_path(path_value: str, default_distro: str) -> Tuple[str, str]:
    raw_value = str(path_value or "").strip()
    if not raw_value:
        return default_distro.strip(), ""

    inferred_distro = default_distro.strip()
    normalized_path = raw_value

    unc_match = UNC_WSL_RE.match(raw_value)
    if unc_match:
        inferred_distro = unc_match.group(1).strip() or inferred_distro
        unc_tail = unc_match.group(2).strip().replace("\\", "/").strip("/")
        normalized_path = f"/{unc_tail}" if unc_tail else "/"
    else:
        normalized_path = raw_value.replace("\\", "/")

    if normalized_path == "~":
        home_path = _resolve_wsl_home(inferred_distro)
        normalized_path = home_path
    elif normalized_path.startswith("~/"):
        home_path = _resolve_wsl_home(inferred_distro)
        normalized_path = f"{home_path}/{normalized_path[2:]}"
    elif normalized_path.startswith("/"):
        normalized_path = normalized_path
    elif re.match(r"^[A-Za-z]:/", normalized_path):
        conversion = _run_wsl_script(
            inferred_distro,
            f"wslpath -a {_shell_quote(normalized_path)}",
        )
        converted_path = conversion.stdout.strip()
        if conversion.returncode == 0 and converted_path:
            normalized_path = converted_path
    else:
        normalized_path = f"/{normalized_path.lstrip('/')}"

    return inferred_distro, normalized_path.rstrip("/") or "/"


def build_agent_markdown(*, agent_name: str, model_name: str) -> str:
    frontmatter = [
        "---",
        f"name: {_yaml_string(agent_name)}",
        'description: "Apply documentation updates for a TFS work item on the current branch."',
        'argument-hint: "Run the documentation automation handoff for the prepared work item package."',
        "target: vscode",
        "user-invocable: true",
        'tools: ["changes", "codebase", "editFiles", "fetch", "findTestFiles", "githubRepo", "openSimpleBrowser", "problems", "runCommands", "runNotebooks", "search", "searchResults", "terminalLastCommand", "terminalSelection", "testFailure", "usages", "vscodeAPI"]',
    ]
    if str(model_name or "").strip():
        frontmatter.append(f"model: {_yaml_string(model_name)}")
    frontmatter.extend(
        [
            "---",
            "",
            "# Documentation automation agent",
            "You update documentation only. Stay within the current Git branch and repository.",
            "Use the attached work item package as the source of truth for the requested change.",
            "Respect the workspace instructions from `AGENTS.md`, `.github/copilot-instructions.md`, and any `.agents` materials that are attached to this chat.",
            "Inspect the existing documentation patterns before editing files.",
            "Prefer the smallest accurate change that satisfies the work item.",
            "If no documentation update is needed, explain why instead of forcing edits.",
            "If the work item references specs or `.docx` files, inspect the reference documentation workspace paths listed in the attached context package.",
            "Do not create or update pull requests from chat. The dashboard controls branch and PR workflow.",
            "When you finish, summarize the files changed, the reason for each change, and any remaining reviewer concerns.",
        ]
    )
    return "\n".join(frontmatter) + "\n"


def get_custom_agent_identifier() -> str:
    return CUSTOM_AGENT_FILE_BASENAME


def get_windows_user_agent_directory() -> Path:
    return Path.home() / ".copilot" / "agents"


def get_vscode_user_data_agent_directory() -> Path:
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return Path(appdata) / "Code" / "User" / "prompts"
    return Path.home() / "AppData" / "Roaming" / "Code" / "User" / "prompts"


def _write_windows_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_vscode_agent_files(
    *,
    distro: str,
    wsl_agent_path: str,
    agent_identifier: str,
    agent_content: str,
) -> List[str]:
    written_paths: List[str] = []
    _write_file_via_wsl(distro, wsl_agent_path, agent_content)
    written_paths.append(wsl_agent_path)

    for directory in [get_windows_user_agent_directory(), get_vscode_user_data_agent_directory()]:
        agent_path = directory / f"{agent_identifier}.agent.md"
        try:
            _write_windows_text_file(agent_path, agent_content)
            written_paths.append(str(agent_path))
        except OSError:
            continue

    return written_paths


def _wsl_path_to_unc_path(distro: str, path_value: str) -> str:
    normalized_path = str(path_value or "").strip().replace("\\", "/")
    if not normalized_path.startswith("/"):
        raise CopilotIntegrationError(f"Expected an absolute WSL path, got '{path_value}'.")
    unc_tail = normalized_path.lstrip("/").replace("/", "\\")
    if not unc_tail:
        return f"\\\\wsl.localhost\\{distro}\\"
    return f"\\\\wsl.localhost\\{distro}\\{unc_tail}"


def wsl_path_to_unc_path(distro: str, path_value: str) -> str:
    return _wsl_path_to_unc_path(distro, path_value)


def _resolve_windows_code_command() -> str:
    if current_execution_runtime() == EXECUTION_RUNTIME_DEVCONTAINER and os.name != "nt":
        # Devcontainers often provide a generic `/usr/local/bin/code` wrapper before
        # the VS Code Remote CLI on PATH. The wrapper cannot deliver a chat handoff
        # to the active window, whereas the remote CLI can.
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(directory) / "code"
            if "/remote-cli" in str(candidate).replace("\\", "/") and candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    for command_name in ["code.cmd", "code"]:
        resolved = shutil.which(command_name)
        if resolved:
            return resolved
    raise CopilotIntegrationError("Could not find the VS Code CLI on Windows. Make sure 'code' is available in PATH.")


def _copy_text_to_windows_clipboard(text: str) -> None:
    clean_text = str(text or "")
    if not clean_text:
        return

    script = (
        "[Console]::InputEncoding = [Text.Encoding]::UTF8; "
        "$text = [Console]::In.ReadToEnd(); "
        "Set-Clipboard -Value ([string]$text)"
    )
    result = _run_windows_command(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        input_text=clean_text,
    )
    if result.returncode != 0:
        raise CopilotIntegrationError(
            result.stderr.strip()
            or result.stdout.strip()
            or "Failed to copy the CM GPT prompt to the Windows clipboard."
        )


def _open_windows_url(url: str) -> Dict[str, str]:
    clean_url = str(url or "").strip()
    if not clean_url:
        return {
            "url": "",
            "stdout": "",
            "stderr": "",
        }
    result = _run_windows_command(["cmd.exe", "/c", "start", "", clean_url])
    if result.returncode != 0:
        raise CopilotIntegrationError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"Failed to open '{clean_url}'."
        )
    return {
        "url": clean_url,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _build_wsl_folder_uri(distro: str, workspace_path: str) -> str:
    encoded_distro = quote(str(distro or "").strip(), safe="")
    encoded_path = quote(str(workspace_path or "").strip(), safe="/")
    return f"vscode-remote://wsl+{encoded_distro}{encoded_path}"


def _open_vscode_workspace_from_windows(
    *,
    distro: str,
    workspace_path: str,
    open_wsl_remote: bool,
    window_mode: str,
) -> Dict[str, Any]:
    code_command = _resolve_windows_code_command()
    window_flag = "--new-window" if str(window_mode or "").strip() == "new" else "--reuse-window"
    if open_wsl_remote:
        workspace_target = _build_wsl_folder_uri(distro, workspace_path)
        open_command = [code_command, window_flag, "--folder-uri", workspace_target]
        launch_context = "wsl-remote"
    else:
        workspace_target = _wsl_path_to_unc_path(distro, workspace_path)
        open_command = [code_command, window_flag, workspace_target]
        launch_context = "windows-unc"

    open_result = _run_windows_command(open_command)
    if open_result.returncode != 0:
        raise CopilotIntegrationError(
            open_result.stderr.strip()
            or open_result.stdout.strip()
            or "Failed to open the target workspace in VS Code."
        )
    return {
        "workspace_target": workspace_target,
        "launch_context": launch_context,
        "open_stdout": open_result.stdout.strip(),
        "open_stderr": open_result.stderr.strip(),
    }


def _use_current_devcontainer_workspace(code_command: str) -> bool:
    """Use the VS Code remote CLI when the dashboard itself runs in a devcontainer."""
    if current_execution_runtime() != EXECUTION_RUNTIME_DEVCONTAINER or os.name == "nt":
        return False
    normalized_command = str(code_command or "").replace("\\", "/").lower()
    if "/remote-cli/" in normalized_command:
        return True
    raise CopilotIntegrationError(
        "The dashboard is running in a devcontainer, but the VS Code remote CLI is unavailable. "
        "Start the dashboard from the VS Code task so the current devcontainer workspace can receive the agent handoff."
    )


def _launch_vscode_chat_from_windows(
    *,
    distro: str,
    workspace_path: str,
    agent_identifier: str,
    agent_name: str,
    model_name: str,
    strict_model_safety: bool,
    attached_paths: List[str],
    prompt: str,
    open_wsl_remote: bool,
    window_mode: str,
) -> Dict[str, Any]:
    code_command = _resolve_windows_code_command()
    use_current_workspace = _use_current_devcontainer_workspace(code_command)
    if use_current_workspace:
        # The remote CLI is already connected to the active devcontainer. `/app` is
        # valid there but not in the WSL host, so opening a WSL folder URI causes VS
        # Code to ask for a workspace instead of delivering the chat handoff.
        open_metadata = {
            "workspace_target": workspace_path,
            "launch_context": "devcontainer-current-workspace",
            "open_stdout": "",
            "open_stderr": "",
        }
    else:
        open_metadata = _open_vscode_workspace_from_windows(
            distro=distro,
            workspace_path=workspace_path,
            open_wsl_remote=open_wsl_remote,
            window_mode=window_mode,
        )
        time.sleep(1.0)

    chat_command = [code_command, "chat", "--mode", agent_identifier, "--reuse-window"]
    attached_launch_paths: List[str] = []
    for path in attached_paths:
        launch_path = path if use_current_workspace else _wsl_path_to_unc_path(distro, path)
        attached_launch_paths.append(launch_path)
        chat_command.extend(["--add-file", launch_path])
    configured_agent = str(agent_name or "the configured Settings agent").strip()
    configured_model = str(model_name or "the configured Settings model").strip()
    safety_note = (
        "If the active model is not the configured approved model, stop before reading repository content. "
        if strict_model_safety
        else "If VS Code does not visibly select that agent/model, note it in the result but continue in temporary test mode. "
    )
    bootstrap_prompt = (
        "Run the attached TFS Documentation Automation handoff. "
        f"Use the dashboard Settings agent `{configured_agent}` and model `{configured_model}`. "
        "Read the attached prompt.md completely first, then read the adjacent work item Markdown, JSON, HTML, "
        "and repository instruction package before running commands or editing files. "
        "The full handoff instructions and embedded work item context are also provided via stdin after this bootstrap message. "
        "If attached files are unavailable, use the embedded context in the stdin handoff and the explicit paths listed there. "
        + safety_note
    )
    chat_command.append(bootstrap_prompt)
    chat_command.append("-")

    chat_result = _run_windows_command(chat_command, input_text=prompt)
    if chat_result.returncode != 0:
        raise CopilotIntegrationError(
            chat_result.stderr.strip()
            or chat_result.stdout.strip()
            or "Failed to launch the VS Code chat session."
        )

    return {
        "workspace_target": str(open_metadata.get("workspace_target") or ""),
        "launch_context": str(open_metadata.get("launch_context") or ""),
        "attached_unc_paths": attached_launch_paths,
        "open_stdout": str(open_metadata.get("open_stdout") or ""),
        "open_stderr": str(open_metadata.get("open_stderr") or ""),
        "chat_stdout": chat_result.stdout.strip(),
        "chat_stderr": chat_result.stderr.strip(),
    }


def _render_template(template: str, values: Dict[str, str]) -> str:
    rendered = str(template or "")
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered


def _github_copilot_cli_model_id(model_name: str) -> str:
    """Translate dashboard display names into the model IDs accepted by Copilot CLI."""
    clean_name = str(model_name or "").strip()
    normalized_name = re.sub(r"[^a-z0-9]+", " ", clean_name.lower()).strip()
    aliases = {
        "gpt 5 6 terra": "gpt-5.6-terra",
        "gpt 5 6 luna": "gpt-5.6-luna",
        "gpt 5 6 sol": "gpt-5.6-sol",
        "gpt 5 mini": "gpt-5-mini",
    }
    return aliases.get(normalized_name, clean_name)


def _github_copilot_cli_command(*, prompt_path: str, model_name: str, agent_name: str) -> str:
    """Build the bounded non-interactive command for the GitHub Copilot CLI provider."""
    command = " ; ".join(
        [
            'export NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-$HOME/.npm-global}"',
            'export PATH="$HOME/.local/node/current/bin:$NPM_CONFIG_PREFIX/bin:/usr/local/share/nvm/current/bin:$PATH"',
            'copilot_bin="$(command -v copilot || true)"',
            'if [ -z "$copilot_bin" ] && [ -x "$NPM_CONFIG_PREFIX/bin/copilot" ]; then copilot_bin="$NPM_CONFIG_PREFIX/bin/copilot"; fi',
            'if [ -z "$copilot_bin" ]; then echo "GitHub Copilot CLI executable was not found on PATH." >&2; exit 20; fi',
            '"$copilot_bin" -p "$(cat ' + _shell_quote(prompt_path) + ')" -s --stream=off --mode=autopilot --max-autopilot-continues=10 --no-ask-user '
            '--allow-tool="read,write,shell" '
            '--deny-tool="shell(git commit),shell(git push),shell(git reset),shell(git clean),shell(rm:*)"',
        ]
    )
    cli_model_name = _github_copilot_cli_model_id(model_name)
    if cli_model_name:
        command += " --model=" + _shell_quote(cli_model_name)
    if str(agent_name or "").strip():
        command += " --agent=" + _shell_quote(str(agent_name).strip())
    return command


def _agent_result_recovery_parser_command(*, response_path: str, result_path: str) -> str:
    """Turn a CLI-only JSON response into the result contract consumed by the pipeline."""
    parser = """
import json
import re
import sys
from pathlib import Path

response_path = Path(sys.argv[1])
result_path = Path(sys.argv[2])
text = response_path.read_text(encoding="utf-8", errors="replace") if response_path.exists() else ""

candidates = []
for match in re.finditer(r"```(?:json)?\\s*(\\{.*?\\})\\s*```", text, re.DOTALL | re.IGNORECASE):
    candidates.append(match.group(1))
start = text.find("{")
end = text.rfind("}")
if start >= 0 and end > start:
    candidates.append(text[start:end + 1])

payload = None
for candidate in candidates:
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        continue
    if isinstance(parsed, dict):
        payload = parsed
        break

if payload is None:
    payload = {
        "status": "needs_manual_review",
        "green_light": False,
        "summary": "GitHub Copilot CLI completed the implementation phase but did not return the required machine-readable report.",
        "error": "Automatic result recovery could not parse a JSON response from GitHub Copilot CLI.",
        "changed_files": [],
        "final_report": {},
        "spec_references": [],
        "validation": "",
        "instruction_files_read": [],
        "capture_files_read": [],
        "prs_reviewed": [],
        "diffs_reviewed": [],
        "work_items_reviewed": [],
        "reviewer_notes": "Review the provider log and rerun the reporting step before pushing changes.",
    }

result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
""".strip()
    return "python3 -c " + _shell_quote(parser) + " " + _shell_quote(response_path) + " " + _shell_quote(result_path)


def _github_copilot_cli_result_recovery_prompt(*, result_path: str) -> str:
    """Request the result contract separately when Copilot completed but skipped the file write."""
    return "\n".join(
        [
            "# TFS Documentation Automation Result Recovery",
            "The implementation phase has completed. Do not edit, format, validate, commit, push, or create pull requests.",
            "Inspect the current repository diff and the prepared work item context already available in `.automation-context/copilot/`.",
            "Return only one valid JSON object, without Markdown fences or explanatory text.",
            "The object must contain: `status`, `green_light`, `summary`, `changed_files`, `final_report`, `spec_references`, `validation`, `instruction_files_read`, `capture_files_read`, `prs_reviewed`, `diffs_reviewed`, `work_items_reviewed`, `reviewer_notes`, and optional `error`.",
            "Use repository-relative paths in `changed_files`. Set `green_light` to true only when the current diff is ready for the dashboard validation and push stages.",
            "`instruction_files_read` must list every original repository instruction path you read. `final_report` must explain what changed and why.",
            f"The dashboard will save your JSON response as `{result_path}`.",
        ]
    )


def _launch_cli_agent_in_wsl(
    *,
    distro: str,
    workspace_path: str,
    command_template: str,
    prompt_path: str,
    agent_result_path: str,
    branch_name: str,
    model_name: str,
    agent_name: str,
    provider: str,
    log_path: str,
) -> Dict[str, Any]:
    clean_template = str(command_template or "").strip()
    if provider == "copilot_cli":
        clean_template = _github_copilot_cli_command(
            prompt_path=prompt_path,
            model_name=model_name,
            agent_name=agent_name,
        )
    if not clean_template:
        raise CopilotIntegrationError(
            f"Configure a CLI command template before launching provider '{provider}'."
        )

    command = _render_template(
        clean_template,
        {
            "workspace_path": workspace_path,
            "workspace_unc_path": wsl_path_to_unc_path(distro, workspace_path),
            "prompt_path": prompt_path,
            "agent_result_path": agent_result_path,
            "branch_name": branch_name,
            "model_name": model_name,
            "provider": provider,
        },
    )
    package_directory = str(log_path).rsplit("/", 1)[0]
    wrapper_path = f"{package_directory}/agent-provider.sh"
    recovery_prompt_path = f"{package_directory}/agent-result-recovery-prompt.md"
    recovery_response_path = f"{package_directory}/agent-result-recovery-response.txt"
    recovery_command = ""
    if provider == "copilot_cli":
        recovery_prompt = _github_copilot_cli_result_recovery_prompt(result_path=agent_result_path)
        _write_file_via_wsl(distro, recovery_prompt_path, recovery_prompt)
        recovery_command = _github_copilot_cli_command(
            prompt_path=recovery_prompt_path,
            model_name=model_name,
            agent_name=agent_name,
        )
    recovery_parser_command = _agent_result_recovery_parser_command(
        response_path=recovery_response_path,
        result_path=agent_result_path,
    )
    wrapper = "\n".join(
        [
            "#!/bin/sh",
            "set -eu",
            "echo \"[doc-automation] provider started $(date -Is)\"",
            f"echo \"[doc-automation] provider={provider}\"",
            f"echo \"[doc-automation] workspace={workspace_path}\"",
            f"echo \"[doc-automation] branch={branch_name}\"",
            f"echo \"[doc-automation] result={agent_result_path}\"",
            f"cd {_shell_quote(workspace_path)}",
            "provider_status=0",
            # The wrapper has already selected the work-item worktree. Do not start a
            # login shell here because it can reset the working directory to $HOME.
            f"sh -c {_shell_quote(command)} || provider_status=$?",
            "if [ \"$provider_status\" -eq 0 ] && [ ! -s " + _shell_quote(agent_result_path) + " ] && [ -n " + _shell_quote(recovery_command) + " ]; then",
            "  echo \"[doc-automation] provider completed without agent-result.json; requesting result recovery\"",
            "  rm -f " + _shell_quote(recovery_response_path),
            "  sh -c " + _shell_quote(recovery_command) + " > " + _shell_quote(recovery_response_path) + " 2>&1 || true",
            "  " + recovery_parser_command,
            "fi",
            "exit \"$provider_status\"",
            "",
        ]
    )
    _write_file_via_wsl(distro, wrapper_path, wrapper)

    script = " && ".join(
        [
            f"rm -f {_shell_quote(agent_result_path)} {_shell_quote(log_path)}",
            f"chmod 700 {_shell_quote(wrapper_path)}",
            f"cd {_shell_quote(workspace_path)}",
            f"( nohup {_shell_quote(wrapper_path)} > {_shell_quote(log_path)} 2>&1 & jobs -p | tail -n 1 )",
        ]
    )
    result = _run_wsl_script(distro, script)
    if result.returncode != 0:
        raise CopilotIntegrationError(result.stderr.strip() or result.stdout.strip() or "Failed to launch the CLI agent.")
    process_id = result.stdout.strip()
    if not process_id.isdigit():
        raise CopilotIntegrationError(
            "The CLI agent launch did not return a process id. "
            + (result.stderr.strip() or result.stdout.strip() or "No launch output was captured.")
        )
    return {
        "launch_context": provider,
        "workspace_target": workspace_path,
        "cli_command": command,
        "cli_pid": process_id,
        "cli_log_path": log_path,
    }


def _queue_vscode_bridge_job(
    *,
    distro: str,
    package_directory: str,
    workspace_path: str,
    branch_name: str,
    agent_name: str,
    model_name: str,
    prompt_path: str,
    agent_result_path: str,
    open_new_window: bool,
    dispatcher_workspace_path: str = "",
) -> Dict[str, Any]:
    """Queue a durable VS Code Language Model job for the workspace bridge extension."""
    job_path = f"{package_directory}/bridge-job.json"
    state_path = f"{package_directory}/bridge-job-state.json"
    status_path = f"{package_directory}/bridge-status.json"
    job = {
        "schema_version": 1,
        "provider": "vscode_bridge",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "workspace_path": workspace_path,
        "branch_name": branch_name,
        "agent_name": agent_name,
        "model_name": model_name,
        "prompt_path": prompt_path,
        "agent_result_path": agent_result_path,
        # The target window is opened by a lightweight job in the dispatcher
        # workspace. Once it opens, this target job must execute there directly.
        "open_new_window": False,
    }
    _remove_file_via_wsl(distro, agent_result_path)
    _remove_file_via_wsl(distro, state_path)
    _write_file_via_wsl(distro, job_path, json.dumps(job, ensure_ascii=False, indent=2) + "\n")
    dispatch_workspace = str(dispatcher_workspace_path or "").strip().rstrip("/")
    if open_new_window and dispatch_workspace and dispatch_workspace != workspace_path:
        dispatch_slug = branch_name.replace("/", "-")
        dispatch_directory = f"{dispatch_workspace}/{WORKSPACE_CONTEXT_ROOT}/dispatch-{dispatch_slug}"
        dispatch_job_path = f"{dispatch_directory}/bridge-job.json"
        dispatch_state_path = f"{dispatch_directory}/bridge-job-state.json"
        dispatch_job = {
            "schema_version": 1,
            "provider": "vscode_bridge",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "workspace_path": workspace_path,
            "branch_name": branch_name,
            "dispatch_only": True,
            "open_new_window": True,
        }
        _remove_file_via_wsl(distro, dispatch_state_path)
        _write_file_via_wsl(distro, dispatch_job_path, json.dumps(dispatch_job, ensure_ascii=False, indent=2) + "\n")
    return {
        "launch_context": "vscode_bridge",
        "workspace_target": workspace_path,
        "bridge_job_path": job_path,
        "cli_log_path": (
            f"{dispatch_workspace}/{WORKSPACE_CONTEXT_ROOT}/{VSCODE_BRIDGE_STATUS_FILE}"
            if dispatch_workspace
            else status_path
        ),
    }


def check_agent_provider_prerequisites(
    *,
    distro: str,
    provider: str,
    cli_command_template: str,
    workspace_path: str = "",
    model_name: str = "",
) -> Dict[str, Any]:
    clean_provider = str(provider or "").strip()
    if clean_provider == "vscode_bridge":
        script = r'''
set -eu
extension_root="${HOME}/.vscode-server/extensions"
if [ ! -d "$extension_root" ]; then
  printf '%s\n' "VS Code remote extensions are not available for the current user. Open the repository in a VS Code devcontainer first."
  exit 20
fi
bridge_manifest="$(find "$extension_root" -maxdepth 2 -type f -path '*criticalmanufacturing.cmf-content-ai-pipeline-bridge*/package.json' -print -quit 2>/dev/null || true)"
if [ -z "$bridge_manifest" ]; then
  printf '%s\n' "The Content AI VS Code Copilot bridge is not installed in this devcontainer. Rebuild/reopen the devcontainer so its bootstrap can install the bridge."
  exit 21
fi
printf '%s\n' "$bridge_manifest"
'''
        result = _run_wsl_script(distro, script, timeout_seconds=30)
        if result.returncode != 0:
            return {
                "status": "error",
                "ok": False,
                "message": (result.stdout or result.stderr or "VS Code Copilot bridge preflight failed.").strip(),
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        bridge_status: Dict[str, Any] = {}
        clean_workspace_path = str(workspace_path or "").strip()
        if clean_workspace_path:
            status_result = _run_wsl_script(
                distro,
                f"status_path={_shell_quote(f'{clean_workspace_path}/{WORKSPACE_CONTEXT_ROOT}/{VSCODE_BRIDGE_STATUS_FILE}')}; "
                'if [ -f "$status_path" ]; then cat "$status_path"; fi',
                timeout_seconds=15,
            )
            raw_status = (status_result.stdout or "").strip()
            if raw_status:
                try:
                    bridge_status = json.loads(raw_status)
                except json.JSONDecodeError:
                    bridge_status = {}
            status_value = str(bridge_status.get("status") or "").strip().lower()
            if status_value == "consent_required":
                return {
                    "status": "error",
                    "ok": False,
                    "message": (
                        "VS Code Copilot requires one-time consent for the Content AI bridge. "
                        "In the active devcontainer window, run `Content AI: Enable Copilot Bridge` once, then retry the work item."
                    ),
                    "stdout": raw_status,
                    "stderr": "",
                }
            if not bridge_status:
                return {
                    "status": "error",
                    "ok": False,
                    "message": (
                        "The Content AI VS Code Copilot bridge has not activated for the selected workspace yet. "
                        "Reload/reopen the active devcontainer workspace, then retry after the bridge reports ready."
                    ),
                    "stdout": "",
                    "stderr": "",
                }
            requested_model = re.sub(r"[^a-z0-9]+", "", str(model_name or "").lower())
            available_models = bridge_status.get("available_models")
            if requested_model and isinstance(available_models, list):
                normalized_available = [
                    re.sub(
                        r"[^a-z0-9]+",
                        "",
                        " ".join(
                            str(model.get(key) or "")
                            for key in ["id", "name", "vendor", "family", "version"]
                        ).lower(),
                    )
                    for model in available_models
                    if isinstance(model, dict)
                ]
                if normalized_available and not any(
                    requested_model in candidate or candidate in requested_model
                    for candidate in normalized_available
                    if candidate
                ):
                    return {
                        "status": "error",
                        "ok": False,
                        "message": (
                            f"The configured Copilot model '{model_name}' is not available to the active VS Code bridge. "
                            "Choose one of the models reported by the bridge or adjust the Copilot entitlement."
                        ),
                        "stdout": raw_status,
                        "stderr": "",
                    }
        return {
            "status": "ok",
            "ok": True,
            "message": "The Content AI VS Code Copilot bridge is installed. Queued jobs will run in the active VS Code devcontainer workspace.",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    if clean_provider == "vscode":
        try:
            code_command = _resolve_windows_code_command()
        except CopilotIntegrationError as exc:
            return {
                "status": "error",
                "ok": False,
                "message": str(exc),
            }
        result = _run_windows_command([code_command, "--help"], timeout_seconds=30)
        help_text = "\n".join([result.stdout or "", result.stderr or ""]).lower()
        supports_chat_handoff = result.returncode == 0 and "chat" in help_text and "--mode" in help_text and "--add-file" in help_text
        if not supports_chat_handoff:
            return {
                "status": "error",
                "ok": False,
                "message": (
                    "The installed VS Code CLI does not support the Chat handoff options required by this pipeline "
                    "(`code chat --mode ... --add-file ...`). Update VS Code to a version that supports the Chat CLI, "
                    "or select an automation-capable CLI provider such as Codex CLI."
                ),
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        return {
            "status": "ok",
            "ok": True,
            "message": "VS Code CLI supports automated Copilot Chat handoff for the active workspace.",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }

    if clean_provider not in {"copilot_cli", "codex_cli", "claude_cli", "custom_cli"}:
        return {
            "status": "skipped",
            "ok": True,
            "message": "No CLI provider preflight is required for the selected agent provider.",
        }
    if clean_provider != "copilot_cli" and not str(cli_command_template or "").strip():
        return {
            "status": "error",
            "ok": False,
            "message": f"Configure a CLI command template before launching provider '{clean_provider}'.",
        }
    if clean_provider == "copilot_cli":
        cli_model_name = _github_copilot_cli_model_id(model_name)
        model_option = f" --model={_shell_quote(cli_model_name)}" if cli_model_name else ""
        script = r'''
set -eu
export NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-$HOME/.npm-global}"
export PATH="$HOME/.local/node/current/bin:$NPM_CONFIG_PREFIX/bin:/usr/local/share/nvm/current/bin:$PATH"
copilot_bin="$(command -v copilot || true)"
if [ -z "$copilot_bin" ] && [ -x "$NPM_CONFIG_PREFIX/bin/copilot" ]; then
  copilot_bin="$NPM_CONFIG_PREFIX/bin/copilot"
fi
if [ -z "$copilot_bin" ]; then
  printf '%s\n' 'GitHub Copilot CLI executable was not found on PATH or at $NPM_CONFIG_PREFIX/bin/copilot.'
  exit 20
fi
"$copilot_bin" --version
"$copilot_bin" -p 'Reply with READY only.' -s --no-ask-user''' + model_option + "\n"
        result = _run_wsl_script(distro, script, timeout_seconds=90)
        output = "\n".join([result.stdout or "", result.stderr or ""]).strip()
        authentication_missing = "no authentication information found" in output.lower()
        if result.returncode != 0 or authentication_missing:
            return {
                "status": "error",
                "ok": False,
                "message": (
                    "GitHub Copilot CLI authentication is not available in this runtime. "
                    "Start the device login from Settings and complete the displayed authorization."
                    if authentication_missing
                    else (result.stderr or result.stdout or "GitHub Copilot CLI preflight failed.").strip()
                ),
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        return {
            "status": "ok",
            "ok": True,
            "message": "GitHub Copilot CLI is installed, authenticated, and accepts the configured model in this runtime.",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }

    if clean_provider != "codex_cli":
        return {
            "status": "skipped",
            "ok": True,
            "message": f"No built-in preflight is available yet for provider '{clean_provider}'.",
        }

    script = r'''
set -eu
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
export NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-$HOME/.npm-global}"
export PATH="$HOME/.local/node/current/bin:$NPM_CONFIG_PREFIX/bin:/usr/local/share/nvm/current/bin:$HOME/.npm-global/bin:$PATH"
codex_bin="$(command -v codex || true)"
if [ -z "$codex_bin" ] && [ -x "$NPM_CONFIG_PREFIX/bin/codex" ]; then
  codex_bin="$NPM_CONFIG_PREFIX/bin/codex"
fi
if [ -z "$codex_bin" ] && [ -x "$HOME/.npm-global/bin/codex" ]; then
  codex_bin="$HOME/.npm-global/bin/codex"
fi
if [ -z "$codex_bin" ]; then
  printf '{"overallStatus":"fail","checks":{"installation":{"status":"fail","summary":"Codex CLI executable was not found on PATH or at $NPM_CONFIG_PREFIX/bin/codex"}}}\n'
  exit 20
fi
"$codex_bin" doctor --json
'''
    result = _run_wsl_script(distro, script, timeout_seconds=60)
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        payload = {}
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    auth_check = checks.get("auth.credentials") if isinstance(checks.get("auth.credentials"), dict) else {}
    install_check = checks.get("installation") if isinstance(checks.get("installation"), dict) else {}

    if result.returncode == 20 or str(install_check.get("status") or "").lower() == "fail":
        install_summary = str(install_check.get("summary") or "Codex CLI executable was not found.").strip()
        return {
            "status": "error",
            "ok": False,
            "message": install_summary,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }

    if str(auth_check.get("status") or "").lower() == "fail":
        summary = str(auth_check.get("summary") or "Codex CLI authentication is not ready.").strip()
        remediation = str(auth_check.get("remediation") or "Run codex login inside the devcontainer.").strip()
        auth_file = str(((auth_check.get("details") or {}).get("auth file")) or "$HOME/.codex/auth.json").strip()
        return {
            "status": "error",
            "ok": False,
            "message": f"{summary}. {remediation} Auth file: {auth_file}.",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }

    if result.returncode != 0 and str(payload.get("overallStatus") or "").lower() == "fail":
        return {
            "status": "warning",
            "ok": True,
            "message": "Codex CLI preflight completed with non-auth warnings. Review `codex doctor` if the agent launch still fails.",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }

    return {
        "status": "ok",
        "ok": True,
        "message": "Codex CLI is installed and authentication is available for the configured runtime.",
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(value: str) -> str:
    return ANSI_RE.sub("", str(value or ""))


def start_codex_device_login(*, distro: str) -> Dict[str, Any]:
    script = r'''
set -eu
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
export NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-$HOME/.npm-global}"
export PATH="$HOME/.local/node/current/bin:$NPM_CONFIG_PREFIX/bin:/usr/local/share/nvm/current/bin:$HOME/.npm-global/bin:$PATH"
codex_bin="$(command -v codex || true)"
if [ -z "$codex_bin" ] && [ -x "$NPM_CONFIG_PREFIX/bin/codex" ]; then
  codex_bin="$NPM_CONFIG_PREFIX/bin/codex"
fi
if [ -z "$codex_bin" ] && [ -x "$HOME/.npm-global/bin/codex" ]; then
  codex_bin="$HOME/.npm-global/bin/codex"
fi
if [ -z "$codex_bin" ]; then
  echo "__DOC_AUTOMATION_ERROR__=Codex CLI executable was not found on PATH or at $NPM_CONFIG_PREFIX/bin/codex."
  exit 20
fi
mkdir -p "$CODEX_HOME/login"
log_path="$CODEX_HOME/login/device-login-$(date +%Y%m%d-%H%M%S).log"
nohup "$codex_bin" login --device-auth > "$log_path" 2>&1 &
pid="$!"
i=0
while [ "$i" -lt 30 ]; do
  if [ -f "$log_path" ] && grep -Eq "https://auth.openai.com/codex/device|Enter this one-time code|already logged in|Login successful" "$log_path"; then
    break
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  sleep 0.5
  i=$((i + 1))
done
echo "__DOC_AUTOMATION_PID__=$pid"
echo "__DOC_AUTOMATION_LOG__=$log_path"
if [ -f "$log_path" ]; then
  cat "$log_path"
fi
'''
    result = _run_wsl_script(distro, script, timeout_seconds=30)
    output = _strip_ansi((result.stdout or "") + "\n" + (result.stderr or ""))
    if result.returncode not in {0, 20}:
        raise CopilotIntegrationError(output.strip() or "Failed to start Codex device login.")

    process_id = ""
    log_path = ""
    error = ""
    for line in output.splitlines():
        if line.startswith("__DOC_AUTOMATION_PID__="):
            process_id = line.split("=", 1)[1].strip()
        elif line.startswith("__DOC_AUTOMATION_LOG__="):
            log_path = line.split("=", 1)[1].strip()
        elif line.startswith("__DOC_AUTOMATION_ERROR__="):
            error = line.split("=", 1)[1].strip()

    if error:
        return {
            "status": "error",
            "ok": False,
            "message": error,
            "pid": process_id,
            "log_path": log_path,
            "output": output.strip(),
        }

    login_output = "\n".join(
        line for line in output.splitlines() if not line.startswith("__DOC_AUTOMATION_")
    )
    url_match = re.search(r"https://auth\.openai\.com/codex/device", login_output)
    code_match = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{5}\b", login_output)
    if "Login successful" in output or "already logged in" in output.lower():
        message = "Codex CLI is already authenticated in the configured runtime."
        status = "ok"
    elif url_match and code_match:
        message = (
            f"Open {url_match.group(0)} and enter device code {code_match.group(0)}. "
            "The login process is running in the configured runtime and will complete after authorization."
        )
        status = "pending"
    else:
        message = "Codex device login was started, but the device code was not detected yet. Check the login log."
        status = "pending"

    return {
        "status": status,
        "ok": status == "ok",
        "message": message,
        "url": url_match.group(0) if url_match else "",
        "device_code": code_match.group(0) if code_match else "",
        "pid": process_id,
        "log_path": log_path,
        "output": output.strip(),
    }


def start_github_copilot_device_login(*, distro: str, host: str = "") -> Dict[str, Any]:
    """Start the one-time GitHub Copilot CLI OAuth device flow in the runtime."""
    clean_host = str(host or "").strip() or "https://github.com"
    script = r'''
set -eu
export NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-$HOME/.npm-global}"
export PATH="$HOME/.local/node/current/bin:$NPM_CONFIG_PREFIX/bin:/usr/local/share/nvm/current/bin:$PATH"
copilot_bin="$(command -v copilot || true)"
if [ -z "$copilot_bin" ] && [ -x "$NPM_CONFIG_PREFIX/bin/copilot" ]; then
  copilot_bin="$NPM_CONFIG_PREFIX/bin/copilot"
fi
if [ -z "$copilot_bin" ]; then
  echo "__DOC_AUTOMATION_ERROR__=GitHub Copilot CLI executable was not found on PATH or at $NPM_CONFIG_PREFIX/bin/copilot."
  exit 20
fi
login_directory="${CONTENT_AI_SETTINGS_PATH:-$HOME/.copilot}/copilot-cli/login"
mkdir -p "$login_directory"
chmod 700 "$login_directory"
log_path="$login_directory/device-login-$(date +%Y%m%d-%H%M%S).log"
login_command="stty -echo; exec $copilot_bin login --device-code --host ''' + _shell_quote(clean_host) + r'''"
nohup sh -c 'yes y | script -q -c "$1" /dev/null' copilot-login "$login_command" > "$log_path" 2>&1 &
pid="$!"
i=0
while [ "$i" -lt 30 ]; do
  if [ -f "$log_path" ] && grep -Eqi "github.com/login/device|one-time code|authorization|signed in successfully" "$log_path"; then
    break
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  sleep 0.5
  i=$((i + 1))
done
echo "__DOC_AUTOMATION_PID__=$pid"
echo "__DOC_AUTOMATION_LOG__=$log_path"
if [ -f "$log_path" ]; then
  cat "$log_path"
fi
'''
    result = _run_wsl_script(distro, script, timeout_seconds=30)
    output = _strip_ansi((result.stdout or "") + "\n" + (result.stderr or ""))
    if result.returncode not in {0, 20}:
        raise CopilotIntegrationError(output.strip() or "Failed to start GitHub Copilot CLI device login.")

    process_id = ""
    log_path = ""
    error = ""
    for line in output.splitlines():
        if line.startswith("__DOC_AUTOMATION_PID__="):
            process_id = line.split("=", 1)[1].strip()
        elif line.startswith("__DOC_AUTOMATION_LOG__="):
            log_path = line.split("=", 1)[1].strip()
        elif line.startswith("__DOC_AUTOMATION_ERROR__="):
            error = line.split("=", 1)[1].strip()
    if error:
        return {"status": "error", "ok": False, "message": error, "pid": process_id, "log_path": log_path}

    login_output = "\n".join(line for line in output.splitlines() if not line.startswith("__DOC_AUTOMATION_"))
    url_match = re.search(r"https://[^\s]+/login/device", login_output)
    code_match = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", login_output)
    if "signed in successfully" in login_output.lower():
        message = "GitHub Copilot CLI is already authenticated in the configured runtime."
        status = "ok"
    elif url_match and code_match:
        message = f"Open {url_match.group(0)} and enter device code {code_match.group(0)}. Then save Settings again."
        status = "pending"
    else:
        message = "GitHub Copilot CLI login was started, but the device code was not detected yet. Check the login log."
        status = "pending"
    return {
        "status": status,
        "ok": status == "ok",
        "message": message,
        "url": url_match.group(0) if url_match else "",
        "device_code": code_match.group(0) if code_match else "",
        "pid": process_id,
        "log_path": log_path,
        "output": output.strip(),
    }


def _extract_image_sources(*html_sections: str) -> List[str]:
    image_sources: List[str] = []
    for html_section in html_sections:
        for match in IMG_SRC_RE.findall(str(html_section or "")):
            source = str(match or "").strip()
            if source and source not in image_sources:
                image_sources.append(source)
    return image_sources


def _extract_docx_references(item: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []
    for value in [
        item.get("title", ""),
        item.get("description_html", ""),
        item.get("acceptance_criteria_html", ""),
        item.get("repro_steps_html", ""),
    ]:
        for match in DOCX_RE.findall(str(value or "")):
            if match not in candidates:
                candidates.append(match)
    for relation in list(item.get("attachment_links", []) or []) + list(item.get("hyperlink_links", []) or []):
        name = str(relation.get("name") or "").strip()
        url = str(relation.get("url") or "").strip()
        for candidate in [name, url]:
            for match in DOCX_RE.findall(candidate):
                if match not in candidates:
                    candidates.append(match)
    return candidates


def _reference_doc_slug(path_value: str, index: int) -> str:
    basename = str(path_value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", basename)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(" .-_")
    return f"{index:02d}-{slug or 'reference-doc'}"


def build_work_item_context(
    item: Dict[str, Any],
    portal: Dict[str, Any],
    reference_docs_path: str,
    *,
    model_name: str = "",
    strict_model_safety: bool = True,
) -> str:
    description = _strip_html(item.get("description_html") or item.get("description_preview") or "") or "No description provided."
    acceptance = _strip_html(item.get("acceptance_criteria_html") or item.get("acceptance_preview") or "") or "No acceptance criteria provided."
    repro_steps = _strip_html(item.get("repro_steps_html") or "") or "No repro steps provided."
    image_sources = _extract_image_sources(
        str(item.get("description_html") or ""),
        str(item.get("acceptance_criteria_html") or ""),
        str(item.get("repro_steps_html") or ""),
    )
    docx_references = _extract_docx_references(item)
    attachment_links = list(item.get("attachment_links", []) or [])
    hyperlink_links = list(item.get("hyperlink_links", []) or [])

    repository_instructions = [
        "- The workspace may contain `AGENTS.md`, `.github/copilot-instructions.md`, and `.agents` materials. These are attached when present and must be followed.",
    ]
    configured_model = str(model_name or "").strip() or "the configured agent model"
    repository_instructions.append(
        f"- Use the dashboard-selected model `{configured_model}`. Do not substitute another model or provider."
    )

    lines = [
        f"# Work item {item['id']}",
        "",
        "## Target repository",
        f"- Project: {portal.get('project', '-')}",
        f"- Repository: {portal.get('repository', '-')}",
        f"- Effective branch: {item.get('effective_branch_name') or item.get('branch_name') or '-'}",
        f"- Base branch: {item.get('selected_base_branch') or item.get('inferred_base_branch') or '-'}",
        f"- Workspace path: {portal.get('copilot_workspace_path') or item.get('copilot_workspace_path') or '-'}",
        "",
        "## Work item metadata",
        f"- Work item URL: {item.get('url', '-')}",
        f"- Title: {item.get('title', '-')}",
        f"- Type: {item.get('type', '-')}",
        f"- Parent: {item.get('parent_type') or '-'} / {item.get('parent_title') or '-'}",
        f"- State: {item.get('state', '-')}",
        f"- Iteration: {item.get('iteration_path', '-')}",
        f"- Area: {item.get('area_path', '-')}",
        f"- Tags: {item.get('tags', '-')}",
        f"- Reviewer: {item.get('reviewer_display_name') or '-'}",
        "",
        "## Summary",
        description,
        "",
        "## Acceptance criteria",
        acceptance,
        "",
        "## Repro steps",
        repro_steps,
        "",
        "## Repository instructions",
        *repository_instructions,
    ]

    if reference_docs_path:
        lines.extend(
            [
                "",
                "## Reference documentation workspace",
                f"- Path: {reference_docs_path}",
                "- If the work item refers to specs or `.docx` files, inspect matching documents in this repository before editing the portal.",
            ]
        )
    if docx_references:
        lines.extend(["", "## Referenced spec files"])
        for filename in docx_references:
            lines.append(f"- {filename}")
    if image_sources:
        lines.extend(["", "## Image references"])
        for source in image_sources:
            lines.append(f"- {source}")
    if attachment_links:
        lines.extend(["", "## Attachment relations"])
        for attachment in attachment_links:
            lines.append(f"- {attachment.get('name') or 'Attachment'}: {attachment.get('url') or '-'}")
    if hyperlink_links:
        lines.extend(["", "## Hyperlinks"])
        for hyperlink in hyperlink_links:
            lines.append(f"- {hyperlink.get('name') or 'Link'}: {hyperlink.get('url') or '-'}")

    lines.extend(
        [
            "",
            "## Expected workflow",
            "1. Read the attached Markdown and HTML files for the full work item content.",
            "2. Inspect the current branch and locate the affected documentation pages.",
            "3. Apply the documentation changes directly in this branch.",
            "4. Keep changes focused and consistent with the existing portal style.",
            "5. If lightweight validation commands are obvious from the repository, run them.",
            "6. Finish by summarizing what changed and any remaining reviewer concerns.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_m365_desktop_prompt(
    *,
    agent_name: str,
    branch_name: str,
    workspace_path: str,
    context_text: str,
) -> str:
    return "\n".join(
        [
            f"You are the `{agent_name}` Microsoft 365 Copilot agent.",
            "",
            "Security gate:",
            "- This prompt contains proprietary company information.",
            "- Continue only if this chat is running inside the approved company Microsoft 365 Copilot agent named `CM GPT`.",
            "- If this is not `CM GPT`, stop immediately and do not process the work item.",
            "",
            "Repository context:",
            f"- WSL workspace: `{workspace_path}`",
            f"- Current branch: `{branch_name}`",
            "",
            "Task:",
            "Review the work item context below and propose the smallest accurate documentation update.",
            "If you cannot edit the local repository directly from Microsoft 365 Copilot Desktop, return a focused patch plan with exact file paths and replacement text.",
            "Do not suggest creating PRs. The dashboard controls branch and PR workflow.",
            "",
            "Work item context:",
            "",
            context_text.strip(),
            "",
        ]
    )


def build_work_item_package(
    item: Dict[str, Any],
    portal: Dict[str, Any],
    reference_docs_path: str,
    *,
    model_name: str = "",
    strict_model_safety: bool = True,
) -> Dict[str, str]:
    package_files: Dict[str, str] = {
        "work-item.md": build_work_item_context(
            item,
            portal,
            reference_docs_path,
            model_name=model_name,
            strict_model_safety=strict_model_safety,
        ),
        "work-item.json": json.dumps(
            {
                "id": item.get("id"),
                "url": item.get("url"),
                "title": item.get("title"),
                "type": item.get("type"),
                "state": item.get("state"),
                "tags": item.get("tags"),
                "iteration_path": item.get("iteration_path"),
                "area_path": item.get("area_path"),
                "assigned_to": item.get("assigned_to"),
                "reviewer_display_name": item.get("reviewer_display_name"),
                "parent_id": item.get("parent_id"),
                "parent_type": item.get("parent_type"),
                "parent_title": item.get("parent_title"),
                "attachment_links": item.get("attachment_links", []),
                "hyperlink_links": item.get("hyperlink_links", []),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }
    if str(item.get("description_html") or "").strip():
        package_files["description.html"] = str(item.get("description_html") or "")
    if str(item.get("acceptance_criteria_html") or "").strip():
        package_files["acceptance-criteria.html"] = str(item.get("acceptance_criteria_html") or "")
    if str(item.get("repro_steps_html") or "").strip():
        package_files["repro-steps.html"] = str(item.get("repro_steps_html") or "")
    return package_files


def _write_file_via_wsl(distro: str, file_path: str, content: str) -> None:
    parent_directory = str(file_path).rsplit("/", 1)[0]
    script = " && ".join(
        [
            f"mkdir -p {_shell_quote(parent_directory)}",
            f"cat > {_shell_quote(file_path)}",
        ]
    )
    result = _run_wsl_script(distro, script, input_text=content)
    if result.returncode != 0:
        raise CopilotIntegrationError(result.stderr.strip() or result.stdout.strip() or f"Failed to write '{file_path}'.")


def _ensure_clean_workspace(distro: str, workspace_path: str) -> None:
    repository_check = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} rev-parse --is-inside-work-tree",
    )
    if repository_check.returncode != 0 or repository_check.stdout.strip() != "true":
        raise CopilotIntegrationError("The configured workspace path is not a Git repository.")

    status_result = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} status --porcelain --untracked-files=no",
    )
    if status_result.returncode != 0:
        raise CopilotIntegrationError(status_result.stderr.strip() or status_result.stdout.strip() or "Workspace is not ready for CM GPT.")
    if status_result.stdout.strip():
        short_status = _run_wsl_script(
            distro,
            f"git -C {_shell_quote(workspace_path)} status --short --untracked-files=no",
        )
        raise CopilotIntegrationError(
            "The configured workspace has tracked local changes. Commit or stash them before launching CM GPT.\n"
            + (short_status.stdout.strip() or status_result.stdout.strip())
        )


def _ensure_git_workspace(distro: str, workspace_path: str) -> None:
    repository_check = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} rev-parse --is-inside-work-tree",
    )
    if repository_check.returncode != 0 or repository_check.stdout.strip() != "true":
        raise CopilotIntegrationError("The configured workspace path is not a Git repository.")


def _prepare_isolated_agent_worktree(
    distro: str,
    workspace_path: str,
    branch_name: str,
) -> str:
    """Create or reuse a clean per-branch worktree for an autonomous agent run."""
    source_path = str(workspace_path or "").strip().rstrip("/")
    clean_branch = str(branch_name or "").strip()
    if not source_path or not clean_branch:
        raise CopilotIntegrationError("An existing workspace path and work branch are required to create an isolated worktree.")

    script = "\n".join(
        [
            "set -eu",
            f"source_path={_shell_quote(source_path)}",
            f"branch_name={_shell_quote(clean_branch)}",
            'repository_root="$(git -C \"$source_path\" rev-parse --show-toplevel)"',
            'repository_name="$(basename \"$repository_root\")"',
            'branch_slug="$(printf %s \"$branch_name\" | tr "/\\\\ :" "----" | tr -cs "[:alnum:]._-" "-")"',
            f'worktree_root="{ISOLATED_WORKTREE_ROOT}/$repository_name"',
            'target_path="$worktree_root/$branch_slug"',
            'if [ -d "$target_path" ]; then',
            '  git -C "$target_path" rev-parse --is-inside-work-tree >/dev/null',
            '  current_branch="$(git -C "$target_path" branch --show-current)"',
            '  if [ "$current_branch" != "$branch_name" ]; then',
            '    printf "Existing isolated worktree %s is on branch %s, expected %s\\n" "$target_path" "$current_branch" "$branch_name" >&2',
            '    exit 31',
            '  fi',
            'else',
            '  mkdir -p "$worktree_root"',
            '  git -C "$source_path" fetch origin "$branch_name" --prune',
            '  if git -C "$source_path" show-ref --verify --quiet "refs/heads/$branch_name"; then',
            '    git -C "$source_path" worktree add --force "$target_path" "$branch_name" >&2',
            '  else',
            '    git -C "$source_path" worktree add -b "$branch_name" "$target_path" "origin/$branch_name" >&2',
            '  fi',
            'fi',
            'printf "%s" "$target_path"',
        ]
    )
    result = _run_wsl_script(distro, script, timeout_seconds=180)
    if result.returncode != 0:
        raise CopilotIntegrationError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"Failed to prepare an isolated agent worktree for '{clean_branch}'."
        )
    # Git writes informational worktree messages to stdout on some versions.
    # The final emitted line is the only contract value: the worktree path.
    target_path = (result.stdout.splitlines()[-1] if result.stdout.splitlines() else "").strip()
    target_path = target_path.replace("\\", "/").rstrip("/")
    if not target_path:
        raise CopilotIntegrationError("The isolated VS Code worktree path could not be resolved.")
    return target_path


def remove_isolated_agent_worktree(distro: str, workspace_path: str) -> Dict[str, str]:
    """Remove a completed pipeline worktree without touching a configured workspace."""
    clean_workspace_path = str(workspace_path or "").strip().replace("\\", "/").rstrip("/")
    worktree_root = ISOLATED_WORKTREE_ROOT.rstrip("/")
    if not clean_workspace_path.startswith(worktree_root + "/"):
        return {"status": "skipped", "message": "Workspace is not owned by the automation worktree root."}

    script = "\n".join(
        [
            "set -eu",
            f"target_path={_shell_quote(clean_workspace_path)}",
            'git -C "$target_path" rev-parse --is-inside-work-tree >/dev/null',
            'git -C "$target_path" worktree prune',
            'git -C "$target_path" worktree remove --force "$target_path"',
        ]
    )
    result = _run_wsl_script(distro, script, timeout_seconds=180)
    if result.returncode != 0:
        raise CopilotIntegrationError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"Failed to remove completed automation worktree '{clean_workspace_path}'."
        )
    return {"status": "removed", "message": f"Removed completed automation worktree {clean_workspace_path}."}


def _ensure_workspace_context_excluded(distro: str, workspace_path: str) -> None:
    git_dir_result = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} rev-parse --absolute-git-dir",
    )
    if git_dir_result.returncode != 0:
        raise CopilotIntegrationError(
            git_dir_result.stderr.strip()
            or git_dir_result.stdout.strip()
            or "Failed to locate the workspace Git metadata directory."
        )
    git_dir = git_dir_result.stdout.strip().replace("\\", "/").rstrip("/")
    if not git_dir:
        raise CopilotIntegrationError("Failed to locate the workspace Git metadata directory.")
    exclude_path = f"{git_dir}/info/exclude"
    exclude_parent = f"{git_dir}/info"
    exclude_pattern = "/" + WORKSPACE_CONTEXT_ROOT.split("/", 1)[0] + "/"
    script = "\n".join(
        [
            "set -e",
            f"mkdir -p {_shell_quote(exclude_parent)}",
            f"touch {_shell_quote(exclude_path)}",
            f"grep -qxF {_shell_quote(exclude_pattern)} {_shell_quote(exclude_path)} || printf '\\n%s\\n' {_shell_quote(exclude_pattern)} >> {_shell_quote(exclude_path)}",
        ]
    )
    result = _run_wsl_script(distro, script)
    if result.returncode != 0:
        raise CopilotIntegrationError(
            result.stderr.strip()
            or result.stdout.strip()
            or "Failed to exclude the automation context folder from local Git status."
        )


def _remove_file_via_wsl(distro: str, file_path: str) -> None:
    clean_path = str(file_path or "").strip()
    if not clean_path:
        return
    result = _run_wsl_script(distro, f"rm -f {_shell_quote(clean_path)}")
    if result.returncode != 0:
        raise CopilotIntegrationError(result.stderr.strip() or result.stdout.strip() or f"Failed to remove '{clean_path}'.")


def _checkout_branch(distro: str, workspace_path: str, branch_name: str) -> None:
    script = " ; ".join(
        [
            f"cd {_shell_quote(workspace_path)}",
            f"git fetch origin {_shell_quote(branch_name)} --prune",
            f"git switch {_shell_quote(branch_name)} >/dev/null 2>&1 || git checkout -B {_shell_quote(branch_name)} {_shell_quote('origin/' + branch_name)}",
        ]
    )
    result = _run_wsl_script(distro, script)
    if result.returncode != 0:
        raise CopilotIntegrationError(result.stdout.strip() or result.stderr.strip() or f"Failed to check out '{branch_name}'.")


def _ensure_current_branch(distro: str, workspace_path: str, branch_name: str) -> None:
    result = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} branch --show-current",
    )
    if result.returncode != 0:
        raise CopilotIntegrationError(result.stdout.strip() or result.stderr.strip() or f"Workspace is not on '{branch_name}'.")
    current_branch = result.stdout.strip()
    if current_branch != branch_name:
        raise CopilotIntegrationError(f"Workspace is on branch '{current_branch}', expected '{branch_name}'.")


def _normalize_instruction_relative_path(path_value: str) -> str:
    path = str(path_value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/")


def discover_workspace_instruction_files(distro: str, workspace_path: str) -> List[Dict[str, str]]:
    script = " ; ".join(
        [
            f"cd {_shell_quote(workspace_path)}",
            '{ for file in "AGENTS.md" ".github/copilot-instructions.md"; do [ -f "$file" ] && printf "%s\\n" "$file"; done; if [ -d ".agents" ]; then find ".agents" -type f -name "*.md" | sort | head -n 24; fi; }',
        ]
    )
    result = _run_wsl_script(distro, script)
    if result.returncode != 0:
        return []
    files: List[Dict[str, str]] = []
    workspace_root = str(workspace_path or "").strip().rstrip("/")
    for line in result.stdout.splitlines():
        relative_path = _normalize_instruction_relative_path(line)
        if not relative_path:
            continue
        absolute_path = f"{workspace_root}/{relative_path}" if workspace_root else relative_path
        if not any(existing["relative_path"] == relative_path for existing in files):
            files.append(
                {
                    "path": absolute_path,
                    "relative_path": relative_path,
                }
            )
    return files


def _discover_workspace_context_files(distro: str, workspace_path: str) -> List[str]:
    return [item["path"] for item in discover_workspace_instruction_files(distro, workspace_path)]


def _packaged_instruction_relative_path(relative_path: str) -> str:
    normalized = _normalize_instruction_relative_path(relative_path)
    parts: List[str] = []
    for part in normalized.split("/"):
        clean_part = re.sub(r"[^A-Za-z0-9._-]+", "-", part).strip()
        if not clean_part or clean_part in {".", ".."}:
            continue
        parts.append(clean_part)
    if not parts:
        parts = ["instructions.md"]
    return "repo-instructions/" + "/".join(parts)


def _materialize_instruction_package(
    distro: str,
    *,
    package_directory: str,
    instruction_files: List[Dict[str, str]],
) -> Tuple[str, List[str], List[str]]:
    if not instruction_files:
        return "", [], []

    attached_paths: List[str] = []
    expected_relative_paths: List[str] = []
    index_rows: List[Tuple[str, str]] = []
    for instruction_file in instruction_files:
        original_path = _normalize_instruction_relative_path(instruction_file.get("relative_path", ""))
        source_path = str(instruction_file.get("path") or "").strip()
        if not original_path or not source_path:
            continue
        read_result = _run_wsl_script(distro, f"cat {_shell_quote(source_path)}")
        if read_result.returncode != 0:
            continue
        packaged_relative_path = _packaged_instruction_relative_path(original_path)
        packaged_absolute_path = f"{package_directory}/{packaged_relative_path}"
        _write_file_via_wsl(distro, packaged_absolute_path, read_result.stdout)
        attached_paths.append(packaged_absolute_path)
        expected_relative_paths.append(original_path)
        index_rows.append((original_path, packaged_relative_path))

    if not index_rows:
        return "", [], []

    index_path = f"{package_directory}/repo-instructions/index.md"
    index_lines = [
        "# Repository Instructions",
        "",
        "These files were copied from the target workspace when this agent package was prepared.",
        "Read every file listed below before editing repository content.",
        "",
        "| Original path | Packaged path |",
        "| --- | --- |",
    ]
    for original_path, packaged_relative_path in index_rows:
        index_lines.append(f"| `{original_path}` | `{packaged_relative_path}` |")
    index_lines.extend(
        [
            "",
            "When writing `agent-result.json`, include every original path that you read in `instruction_files_read`.",
        ]
    )
    _write_file_via_wsl(distro, index_path, "\n".join(index_lines) + "\n")
    return index_path, [index_path, *attached_paths], expected_relative_paths


def _discover_reference_docs(
    distro: str,
    reference_docs_path: str,
    docx_references: List[str],
) -> List[Dict[str, Any]]:
    if not reference_docs_path or not docx_references:
        return []

    encoded_references = base64.b64encode(
        json.dumps(docx_references[:24], ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    script = f"""
python3 - <<'PY'
import base64
import json
import os
import re

root = {json.dumps(reference_docs_path)}
references = json.loads(base64.b64decode({json.dumps(encoded_references)}).decode("utf-8"))
allowed_extensions = {{".docx", ".docm", ".doc", ".pdf", ".md", ".markdown", ".txt"}}

def normalize(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

def words(value):
    return [item for item in re.split(r"[^a-z0-9]+", str(value or "").lower()) if item]

if not os.path.isdir(root):
    print(json.dumps([]))
    raise SystemExit(0)

files = []
for current_root, directories, filenames in os.walk(root):
    directories[:] = [
        directory for directory in directories
        if directory not in {{".git", ".venv", "node_modules", "__pycache__"}}
    ]
    for filename in filenames:
        extension = os.path.splitext(filename)[1].lower()
        if extension not in allowed_extensions:
            continue
        path = os.path.join(current_root, filename)
        files.append((path, filename, normalize(filename), set(words(filename))))

matches = []
seen_paths = set()
for query in references:
    query_name = os.path.basename(str(query or "").replace("\\\\", "/"))
    query_stem = os.path.splitext(query_name)[0]
    normalized_query_name = normalize(query_name)
    normalized_query_stem = normalize(query_stem)
    query_words = set(words(query_stem))
    query_numbers = {{part for part in re.findall(r"\\d+", query_stem) if len(part) >= 3}}

    scored = []
    for path, filename, normalized_filename, filename_words in files:
        score = 0
        match_type = ""
        if normalized_filename == normalized_query_name:
            score = 100
            match_type = "exact-name"
        elif normalized_query_stem and normalized_query_stem in normalized_filename:
            score = 92
            match_type = "stem-contained"
        elif normalized_query_name and normalized_query_name in normalized_filename:
            score = 90
            match_type = "name-contained"
        else:
            number_hits = len([number for number in query_numbers if number in normalized_filename])
            word_hits = len(query_words & filename_words)
            if number_hits:
                score = 65 + min(number_hits * 8, 24) + min(word_hits, 8)
                match_type = "numeric-token"
            elif query_words:
                score = min(word_hits * 10, 50)
                match_type = "word-overlap" if score >= 30 else ""

        if score:
            scored.append((score, match_type, path))

    scored.sort(key=lambda item: (-item[0], item[2].lower()))
    if scored and scored[0][0] >= 90:
        selected = [item for item in scored if item[0] >= 90][:3]
    else:
        selected = scored[:3]
    for score, match_type, path in selected:
        key = path.lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        matches.append({{"query": query, "path": path, "score": score, "match_type": match_type}})
        if len(matches) >= 16:
            break
    if len(matches) >= 16:
        break

print(json.dumps(matches, ensure_ascii=False))
PY
""".strip()
    result = _run_wsl_script(distro, script, timeout_seconds=120)
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [
        {
            "query": str(match.get("query") or "").strip(),
            "path": str(match.get("path") or "").strip(),
            "score": int(match.get("score") or 0),
            "match_type": str(match.get("match_type") or "").strip(),
        }
        for match in payload
        if isinstance(match, dict) and str(match.get("path") or "").strip()
    ]


def _extract_reference_doc_text(distro: str, source_path: str) -> str:
    clean_source_path = str(source_path or "").strip()
    extension = clean_source_path.lower().rsplit(".", 1)[-1] if "." in clean_source_path else ""
    if extension in {"md", "markdown", "txt"}:
        result = _run_wsl_script(
            distro,
            f"head -c 200000 {_shell_quote(clean_source_path)}",
            timeout_seconds=60,
        )
        return result.stdout if result.returncode == 0 else ""
    if extension not in {"docx", "docm"}:
        return ""

    script = f"""
python3 - <<'PY'
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

path = {json.dumps(clean_source_path)}
namespaces = {{"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}}

try:
    with zipfile.ZipFile(path) as archive:
        document = archive.read("word/document.xml")
except Exception:
    sys.exit(0)

try:
    root = ET.fromstring(document)
except Exception:
    sys.exit(0)

lines = []
for paragraph in root.findall(".//w:p", namespaces):
    fragments = []
    for text in paragraph.findall(".//w:t", namespaces):
        if text.text:
            fragments.append(text.text)
    line = re.sub(r"\\s+", " ", "".join(fragments)).strip()
    if line:
        lines.append(line)

payload = "\\n".join(lines)
sys.stdout.write(payload[:200000])
PY
""".strip()
    result = _run_wsl_script(distro, script, timeout_seconds=120)
    return result.stdout if result.returncode == 0 else ""


def _materialize_reference_docs_package(
    distro: str,
    *,
    package_directory: str,
    reference_docs_path: str,
    docx_references: List[str],
    matches: List[Dict[str, Any]],
) -> Tuple[str, List[str], List[str]]:
    if not reference_docs_path and not docx_references and not matches:
        return "", [], []

    package_paths: List[str] = []
    source_paths: List[str] = []
    index_rows: List[Tuple[str, str, str, str, str]] = []
    for index, match in enumerate(matches, start=1):
        source_path = str(match.get("path") or "").strip()
        if not source_path:
            continue
        source_paths.append(source_path)
        slug = _reference_doc_slug(source_path, index)
        extract_path = f"{package_directory}/reference-docs/{slug}.txt"
        extracted_text = _extract_reference_doc_text(distro, source_path)
        extract_lines = [
            f"# Reference Extract: {source_path.rsplit('/', 1)[-1]}",
            "",
            f"- Source path: `{source_path}`",
            f"- Matched query: `{str(match.get('query') or '').strip() or '-'}`",
            f"- Match type: {str(match.get('match_type') or '').strip() or '-'}",
            f"- Match score: {int(match.get('score') or 0)}",
            "",
            "## Extracted Text",
            "",
            extracted_text.strip()
            or "No text could be extracted automatically. Inspect the source path directly if this specification is required.",
            "",
        ]
        _write_file_via_wsl(distro, extract_path, "\n".join(extract_lines))
        package_paths.append(extract_path)
        index_rows.append(
            (
                str(match.get("query") or "").strip(),
                source_path,
                f"reference-docs/{slug}.txt",
                str(match.get("match_type") or "").strip(),
                str(int(match.get("score") or 0)),
            )
        )

    index_path = f"{package_directory}/reference-docs/index.md"
    index_lines = [
        "# Reference Documentation Package",
        "",
        f"- Configured source repository/path: `{reference_docs_path or '-'}`",
        "",
        "## Referenced Spec Names",
    ]
    if docx_references:
        index_lines.extend(f"- `{reference}`" for reference in docx_references)
    else:
        index_lines.append("- No `.docx`, `.docm`, or `.doc` names were detected in the work item.")

    index_lines.extend(["", "## Matched Documents"])
    if index_rows:
        index_lines.extend(
            [
                "| Referenced name | Source path | Packaged extract | Match | Score |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for query, source_path, packaged_path, match_type, score in index_rows:
            index_lines.append(f"| `{query or '-'}` | `{source_path}` | `{packaged_path}` | {match_type or '-'} | {score} |")
    else:
        index_lines.append(
            "- No matching reference documents were found. The configured source path may be wrong, the repository may not be cloned, or the spec may use a different name."
        )
    index_lines.extend(
        [
            "",
            "Read the packaged extracts before deciding that a referenced spec is unavailable.",
            "When a binary document could not be extracted, inspect the listed source path directly if the environment allows it.",
            "",
        ]
    )
    _write_file_via_wsl(distro, index_path, "\n".join(index_lines))
    return index_path, [index_path, *package_paths], source_paths


def inspect_workspace_state(
    distro: str,
    workspace_path: str,
) -> Dict[str, Any]:
    branch_result = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} branch --show-current",
    )
    status_result = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} status --short --untracked-files=no",
    )
    if branch_result.returncode != 0 or status_result.returncode != 0:
        return {
            "current_branch": "",
            "tracked_changes": [],
        }

    return {
        "current_branch": branch_result.stdout.strip(),
        "tracked_changes": [line.strip() for line in status_result.stdout.splitlines() if line.strip()],
    }


def read_agent_result(distro: str, result_path: str) -> Dict[str, Any]:
    clean_result_path = str(result_path or "").strip()
    if not clean_result_path:
        return {
            "status": "waiting",
            "green_light": False,
            "summary": "",
            "error": "No agent result path is configured yet.",
            "changed_files": [],
        }

    result = _run_wsl_script(
        distro,
        f"[ -f {_shell_quote(clean_result_path)} ] && cat {_shell_quote(clean_result_path)}",
    )
    if result.returncode != 0:
        return {
            "status": "waiting",
            "green_light": False,
            "summary": "",
            "error": "The agent result file does not exist yet.",
            "changed_files": [],
        }

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {
            "status": "invalid",
            "green_light": False,
            "summary": "",
            "error": f"The agent result file is not valid JSON: {exc}",
            "changed_files": [],
        }

    raw_status = str(payload.get("status") or "").strip().lower()
    has_explicit_green_light = "green_light" in payload or "ready_for_push" in payload
    green_light = bool(payload.get("green_light") or payload.get("ready_for_push"))
    if not has_explicit_green_light and raw_status in {"green", "green_light", "ready", "ready_for_push", "success", "completed"}:
        green_light = True
    if not raw_status:
        raw_status = "green_light" if green_light else "blocked"

    changed_files: List[str] = []
    for candidate in payload.get("changed_files") or payload.get("files") or []:
        path = str(candidate or "").strip().replace("\\", "/")
        if path and path not in changed_files:
            changed_files.append(path)

    return {
        "status": raw_status,
        "green_light": green_light,
        "summary": str(payload.get("summary") or payload.get("message") or "").strip(),
        "error": str(payload.get("error") or payload.get("reason") or "").strip(),
        "changed_files": changed_files,
        "final_report": payload.get("final_report") or payload.get("report") or {},
        "spec_references": payload.get("spec_references") or payload.get("specs_used") or [],
        "capture_files_read": [
            _normalize_instruction_relative_path(candidate)
            for candidate in payload.get("capture_files_read") or payload.get("capture_read") or []
            if _normalize_instruction_relative_path(candidate)
        ],
        "prs_reviewed": payload.get("prs_reviewed") or payload.get("pull_requests_reviewed") or [],
        "diffs_reviewed": payload.get("diffs_reviewed") or [],
        "work_items_reviewed": payload.get("work_items_reviewed") or [],
        "validation": payload.get("validation") or "",
        "instruction_files_read": [
            _normalize_instruction_relative_path(candidate)
            for candidate in payload.get("instruction_files_read") or payload.get("instructions_read") or []
            if _normalize_instruction_relative_path(candidate)
        ],
        "reviewer_notes": payload.get("reviewer_notes") or payload.get("notes") or "",
    }


def inspect_agent_result_file(distro: str, result_path: str) -> Dict[str, Any]:
    clean_result_path = str(result_path or "").strip()
    if not clean_result_path:
        return {
            "exists": False,
            "mtime_epoch": 0.0,
            "size": 0,
            "age_seconds": 0.0,
        }
    result = _run_wsl_script(
        distro,
        f"[ -f {_shell_quote(clean_result_path)} ] && stat -c '%Y %s' {_shell_quote(clean_result_path)}",
        timeout_seconds=30,
    )
    if result.returncode != 0:
        return {
            "exists": False,
            "mtime_epoch": 0.0,
            "size": 0,
            "age_seconds": 0.0,
        }
    parts = result.stdout.strip().split()
    try:
        mtime_epoch = float(parts[0])
        size = int(parts[1]) if len(parts) > 1 else 0
    except (IndexError, TypeError, ValueError):
        return {
            "exists": True,
            "mtime_epoch": 0.0,
            "size": 0,
            "age_seconds": 0.0,
        }
    return {
        "exists": True,
        "mtime_epoch": mtime_epoch,
        "size": size,
        "age_seconds": max(0.0, time.time() - mtime_epoch),
    }


def is_wsl_process_running(distro: str, process_id: str) -> bool:
    clean_process_id = str(process_id or "").strip()
    if not clean_process_id.isdigit():
        return False
    result = _run_wsl_script(
        distro,
        f"kill -0 {_shell_quote(clean_process_id)} >/dev/null 2>&1",
        timeout_seconds=30,
    )
    return result.returncode == 0


def _summarize_provider_log_error(tail: str) -> str:
    clean_tail = str(tail or "").strip()
    if not clean_tail:
        return ""
    lower_tail = clean_tail.lower()
    if "refresh token was already used" in lower_tail:
        return (
            "Codex CLI authentication failed: the refresh token could not be reused. "
            "Sign out and sign in again inside the configured WSL Codex environment, then rerun the item."
        )
    if "token_expired" in lower_tail or "401 unauthorized" in lower_tail or "authentication token is expired" in lower_tail:
        return (
            "Codex CLI authentication failed: the configured token is expired or unauthorized. "
            "Refresh the Codex CLI login inside WSL, then rerun the item."
        )
    if "agent-provider.sh" in lower_tail and "no such file or directory" in lower_tail:
        return (
            "The WSL provider wrapper could not be executed. "
            "Regenerate the agent package by rerunning the item."
        )

    markers = ("error", "failed", "unauthorized", "permission denied", "no such file", "os error", "command not found")
    matching_lines: List[str] = []
    for raw_line in clean_tail.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(marker in line.lower() for marker in markers):
            matching_lines.append(line)
    if not matching_lines:
        matching_lines = [line.strip() for line in clean_tail.splitlines() if line.strip()][-5:]

    summary = " ".join(matching_lines[-8:])
    max_length = 1200
    if len(summary) > max_length:
        summary = summary[:max_length].rstrip() + "..."
    return summary


def read_agent_provider_status(
    distro: str,
    *,
    result_path: str,
    log_path: str = "",
    process_id: str = "",
) -> Dict[str, Any]:
    clean_result_path = str(result_path or "").strip()
    clean_log_path = str(log_path or "").strip()
    if not clean_log_path and clean_result_path:
        clean_log_path = clean_result_path.rsplit("/", 1)[0] + "/agent-provider.log"

    running = is_wsl_process_running(distro, process_id)
    if not clean_log_path:
        return {
            "running": running,
            "has_log": False,
            "terminal_error": False,
            "error": "",
            "tail": "",
        }

    log_result = _run_wsl_script(
        distro,
        f"[ -f {_shell_quote(clean_log_path)} ] && tail -c 8000 {_shell_quote(clean_log_path)}",
        timeout_seconds=30,
    )
    if log_result.returncode != 0:
        if process_id and not running:
            return {
                "running": False,
                "has_log": False,
                "terminal_error": True,
                "error": "The agent provider process exited before writing agent-result.json or agent-provider.log.",
                "tail": "",
            }
        return {
            "running": running,
            "has_log": False,
            "terminal_error": False,
            "error": "",
            "tail": "",
        }

    tail = str(log_result.stdout or "").strip()
    if clean_log_path.endswith(VSCODE_BRIDGE_STATUS_FILE) and tail:
        try:
            bridge_status = json.loads(tail)
        except json.JSONDecodeError:
            bridge_status = {}
        status_value = str(bridge_status.get("status") or "").strip().lower()
        if status_value in {"awaiting_copilot_access", "consent_required", "waiting_for_model"}:
            message = (
                "Waiting for the one-time VS Code Copilot authorization for the Content AI Pipeline Bridge. "
                "Select Allow in VS Code; the queued job will resume automatically."
            )
            return {
                "running": True,
                "has_log": True,
                "terminal_error": False,
                "waiting_for_user_action": True,
                "error": message,
                "tail": tail,
            }
        if status_value == "opening_new_window":
            return {
                "running": True,
                "has_log": True,
                "terminal_error": False,
                "waiting_for_user_action": False,
                "error": "Opening a dedicated VS Code window for the queued agent job.",
                "tail": tail,
            }
    if running or not tail:
        return {
            "running": running,
            "has_log": bool(tail),
            "terminal_error": False,
            "error": "",
            "tail": tail,
        }

    lower_tail = tail.lower()
    failure_markers = [
        "error:",
        "os error",
        "no such file or directory",
        "cannot find the path",
        "command not found",
        "permission denied",
        "unauthorized",
        "authentication",
        "failed",
    ]
    terminal_error = any(marker in lower_tail for marker in failure_markers)
    message = (
        "The agent provider exited before writing agent-result.json."
        if not terminal_error
        else "The agent provider failed before writing agent-result.json."
    )
    log_summary = _summarize_provider_log_error(tail)
    error = f"{message} {log_summary}".strip()
    return {
        "running": False,
        "has_log": True,
        "terminal_error": terminal_error,
        "error": error,
        "tail": tail,
    }


def _validate_commit_file_paths(changed_files: List[str]) -> List[str]:
    safe_files: List[str] = []
    for changed_file in changed_files:
        path = str(changed_file or "").strip().replace("\\", "/").strip("/")
        if not path:
            continue
        if path.startswith("/") or path.startswith("~") or re.match(r"^[A-Za-z]:", path):
            raise CopilotIntegrationError(f"Agent result contains an absolute path that cannot be committed safely: {changed_file}")
        if ".." in path.split("/"):
            raise CopilotIntegrationError(f"Agent result contains an unsafe relative path: {changed_file}")
        if path.startswith(".git/"):
            raise CopilotIntegrationError(f"Agent result contains a Git metadata path that cannot be committed: {changed_file}")
        if path == WORKSPACE_CONTEXT_ROOT.split("/", 1)[0] or path.startswith(WORKSPACE_CONTEXT_ROOT.split("/", 1)[0] + "/"):
            raise CopilotIntegrationError(f"Agent result contains an automation context path that cannot be committed: {changed_file}")
        if path not in safe_files:
            safe_files.append(path)
    return safe_files


def _run_markdown_link_validation(
    *,
    distro: str,
    workspace_path: str,
    files: List[str],
) -> Dict[str, Any]:
    encoded_files = base64.b64encode(json.dumps(files).encode("utf-8")).decode("ascii")
    validation_script = f"""
import base64
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

root = Path.cwd()
files = json.loads(base64.b64decode({json.dumps(encoded_files)}).decode("utf-8"))
errors = []
warnings = []

markdown_link_re = re.compile(r'(!?)\\[[^\\]]*\\]\\(([^)]+)\\)')
mermaid_click_re = re.compile(r'^\\s*click\\s+\\S+\\s+["\\']([^"\\']+)["\\']')
external_re = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*:')

def clean_destination(raw):
    destination = str(raw or "").strip()
    if not destination:
        return ""
    if destination.startswith("<") and ">" in destination:
        destination = destination[1:destination.index(">")]
    else:
        destination = destination.split()[0]
    destination = destination.strip().strip("'\\\"")
    if "#" in destination:
        destination = destination.split("#", 1)[0]
    if "?" in destination:
        destination = destination.split("?", 1)[0]
    return unquote(destination.strip())

def target_exists(source_file, destination):
    if not destination or destination.startswith("#"):
        return True
    if external_re.match(destination):
        return True
    if destination.startswith("{{{{") or destination.startswith("{{%"):
        return True
    if destination.startswith("/"):
        target = root / destination.lstrip("/")
    else:
        target = source_file.parent / destination
    target = target.resolve()
    candidates = [target]
    if not target.suffix:
        candidates.append(target.with_suffix(".md"))
        candidates.append(target / "index.md")
    if target.is_dir():
        candidates.append(target / "index.md")
    return any(candidate.exists() for candidate in candidates)

baseline_broken_links = set()
for relative_file in files:
    source_file = (root / relative_file).resolve()
    try:
        baseline_result = subprocess.run(
            ["git", "show", "HEAD:" + relative_file],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        continue
    if baseline_result.returncode != 0:
        continue
    for line in baseline_result.stdout.splitlines():
        for match in markdown_link_re.finditer(line):
            destination = clean_destination(match.group(2))
            if not target_exists(source_file, destination):
                baseline_broken_links.add((relative_file, destination))
        click_match = mermaid_click_re.search(line)
        if click_match:
            destination = clean_destination(click_match.group(1))
            if not target_exists(source_file, destination):
                baseline_broken_links.add((relative_file, destination))

for relative_file in files:
    source_file = (root / relative_file).resolve()
    if source_file.suffix.lower() != ".md" or not source_file.exists():
        continue
    try:
        text = source_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = source_file.read_text(encoding="utf-8", errors="replace")
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in markdown_link_re.finditer(line):
            destination = clean_destination(match.group(2))
            if not target_exists(source_file, destination) and (relative_file, destination) not in baseline_broken_links:
                errors.append(f"{{relative_file}}:{{line_number}}: local link target not found: {{destination}}")
        click_match = mermaid_click_re.search(line)
        if click_match:
            destination = clean_destination(click_match.group(1))
            if not target_exists(source_file, destination) and (relative_file, destination) not in baseline_broken_links:
                errors.append(f"{{relative_file}}:{{line_number}}: Mermaid click target not found: {{destination}}")

print(json.dumps({{"errors": errors, "warnings": warnings}}, ensure_ascii=False))
sys.exit(1 if errors else 0)
"""
    result = _run_wsl_script(
        distro,
        " && ".join(
            [
                f"cd {_shell_quote(workspace_path)}",
                "python3 - <<'PY'\n" + validation_script + "\nPY",
            ]
        ),
        timeout_seconds=240,
    )
    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        payload = {
            "errors": [result.stderr.strip() or result.stdout.strip() or "Markdown link validation failed."],
            "warnings": [],
        }
    payload["returncode"] = result.returncode
    return payload


def validate_agent_changes_for_push(
    *,
    distro: str,
    workspace_path: str,
    branch_name: str,
    changed_files: List[str],
) -> Dict[str, Any]:
    safe_files = _validate_commit_file_paths(changed_files)
    if not safe_files:
        raise CopilotIntegrationError("The agent gave green light but did not list any changed files to validate.")

    _ensure_current_branch(distro, workspace_path, branch_name)

    quoted_files = " ".join(_shell_quote(path) for path in safe_files)
    checks: List[Dict[str, str]] = []
    diff_check = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} diff --check -- {quoted_files}",
    )
    if diff_check.returncode != 0:
        raise CopilotIntegrationError(
            diff_check.stdout.strip()
            or diff_check.stderr.strip()
            or "Repository diff validation failed."
        )
    checks.append({"name": "git diff --check", "status": "passed"})

    markdown_files = [path for path in safe_files if path.lower().endswith(".md")]
    if markdown_files:
        markdownlint_files = " ".join(_shell_quote(path) for path in markdown_files)
        markdownlint = _run_wsl_script(
            distro,
            " && ".join(
                [
                    f"cd {_shell_quote(workspace_path)}",
                    "if [ -x node_modules/.bin/markdownlint-cli2 ]; then node_modules/.bin/markdownlint-cli2 "
                    + markdownlint_files
                    + "; elif command -v markdownlint-cli2 >/dev/null 2>&1; then markdownlint-cli2 "
                    + markdownlint_files
                    + "; else exit 127; fi",
                ]
            ),
            timeout_seconds=600,
        )
        if markdownlint.returncode == 0:
            checks.append({"name": "markdownlint-cli2", "status": "passed"})
        elif markdownlint.returncode == 127:
            checks.append({"name": "markdownlint-cli2", "status": "skipped: command not available"})
        else:
            raise CopilotIntegrationError(
                markdownlint.stdout.strip()
                or markdownlint.stderr.strip()
                or "Markdown lint validation failed."
            )

        link_validation = _run_markdown_link_validation(
            distro=distro,
            workspace_path=workspace_path,
            files=markdown_files,
        )
        link_errors = list(link_validation.get("errors") or [])
        if link_errors:
            raise CopilotIntegrationError("Local documentation link validation failed:\n" + "\n".join(link_errors[:20]))
        checks.append({"name": "local Markdown and Mermaid link validation", "status": "passed"})

    return {
        "status": "passed",
        "changed_files": safe_files,
        "checks": checks,
    }


def commit_and_push_agent_changes(
    *,
    distro: str,
    workspace_path: str,
    branch_name: str,
    work_item_id: int,
    title: str,
    changed_files: List[str],
    summary: str = "",
) -> Dict[str, Any]:
    safe_files = _validate_commit_file_paths(changed_files)
    if not safe_files:
        raise CopilotIntegrationError("The agent gave green light but did not list any changed files to commit.")

    _ensure_current_branch(distro, workspace_path, branch_name)

    quoted_files = " ".join(_shell_quote(path) for path in safe_files)
    add_result = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} add -- {quoted_files}",
    )
    if add_result.returncode != 0:
        raise CopilotIntegrationError(add_result.stderr.strip() or add_result.stdout.strip() or "Failed to stage agent changes.")

    diff_result = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} diff --cached --quiet",
    )
    if diff_result.returncode == 0:
        expected_subject = f"Docs: update for WI {work_item_id}"
        subject_result = _run_wsl_script(
            distro,
            f"git -C {_shell_quote(workspace_path)} log -1 --format=%s",
        )
        last_subject = subject_result.stdout.strip() if subject_result.returncode == 0 else ""
        if last_subject != expected_subject:
            raise CopilotIntegrationError("No staged changes were found after applying the agent result file list.")

        sha_result = _run_wsl_script(
            distro,
            f"git -C {_shell_quote(workspace_path)} rev-parse HEAD",
        )
        commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""
        push_result = _run_git_push_with_docker_config_fallback(
            distro=distro,
            workspace_path=workspace_path,
            branch_name=branch_name,
        )
        if push_result.returncode != 0:
            raise CopilotIntegrationError(push_result.stderr.strip() or push_result.stdout.strip() or "Failed to push the work branch.")
        return {
            "status": "pushed",
            "commit": commit_sha,
            "changed_files": safe_files,
            "stdout": push_result.stdout.strip(),
            "stderr": push_result.stderr.strip(),
            "already_committed": True,
        }
    if diff_result.returncode not in {0, 1}:
        raise CopilotIntegrationError(diff_result.stderr.strip() or diff_result.stdout.strip() or "Failed to inspect staged changes.")

    title_fragment = re.sub(r"\s+", " ", str(title or "")).strip()
    commit_subject = f"Docs: update for WI {work_item_id}"
    commit_body = "\n".join(
        line
        for line in [
            f"Work item: {work_item_id}",
            f"Title: {title_fragment}" if title_fragment else "",
            "",
            str(summary or "").strip(),
        ]
        if line is not None
    ).strip()
    commit_result = _run_wsl_script(
        distro,
        " ".join(
            [
                f"git -C {_shell_quote(workspace_path)} commit",
                "-m",
                _shell_quote(commit_subject),
                "-m",
                _shell_quote(commit_body or f"Automated documentation update for WI {work_item_id}."),
            ]
        ),
    )
    if commit_result.returncode != 0:
        raise CopilotIntegrationError(commit_result.stderr.strip() or commit_result.stdout.strip() or "Failed to commit agent changes.")

    sha_result = _run_wsl_script(
        distro,
        f"git -C {_shell_quote(workspace_path)} rev-parse HEAD",
    )
    commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""

    push_result = _run_git_push_with_docker_config_fallback(
        distro=distro,
        workspace_path=workspace_path,
        branch_name=branch_name,
    )
    if push_result.returncode != 0:
        raise CopilotIntegrationError(push_result.stderr.strip() or push_result.stdout.strip() or "Failed to push the work branch.")

    return {
        "status": "pushed",
        "commit": commit_sha,
        "changed_files": safe_files,
        "stdout": push_result.stdout.strip(),
        "stderr": push_result.stderr.strip(),
    }


def _git_push_failed_on_docker_credentials(result: subprocess.CompletedProcess[str]) -> bool:
    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return "docker" in output and "error getting credentials" in output


def _remote_branch_matches_local_head(
    *,
    distro: str,
    workspace_path: str,
    branch_name: str,
) -> bool:
    check_script = " && ".join(
        [
            f"local_sha=$(git -C {_shell_quote(workspace_path)} rev-parse HEAD)",
            f"remote_sha=$(git -C {_shell_quote(workspace_path)} ls-remote origin refs/heads/{_shell_quote(branch_name)} | awk '{{print $1}}')",
            '[ -n "$local_sha" ]',
            '[ "$local_sha" = "$remote_sha" ]',
        ]
    )
    result = _run_wsl_script(distro, check_script, timeout_seconds=60)
    return result.returncode == 0


def _push_success_from_remote_match(
    result: subprocess.CompletedProcess[str],
    *,
    distro: str,
    workspace_path: str,
    branch_name: str,
) -> subprocess.CompletedProcess[str]:
    if result.returncode == 0:
        return result
    if not _remote_branch_matches_local_head(
        distro=distro,
        workspace_path=workspace_path,
        branch_name=branch_name,
    ):
        return result
    message = (
        "Git push reported an error, but the remote branch already points to the local HEAD. "
        "Treating the push as successful.\n"
    )
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=0,
        stdout=message + (result.stdout or ""),
        stderr=result.stderr or "",
    )


def _run_git_push_with_docker_config_fallback(
    *,
    distro: str,
    workspace_path: str,
    branch_name: str,
) -> subprocess.CompletedProcess[str]:
    push_script = f"git -C {_shell_quote(workspace_path)} push -u origin {_shell_quote(branch_name)}"
    push_result = _run_wsl_script(distro, push_script, timeout_seconds=600)
    push_result = _push_success_from_remote_match(
        push_result,
        distro=distro,
        workspace_path=workspace_path,
        branch_name=branch_name,
    )
    if push_result.returncode == 0 or not _git_push_failed_on_docker_credentials(push_result):
        return push_result

    fallback_script = " && ".join(
        [
            "fallback_docker_config=$(mktemp -d)",
            'trap \'rm -rf "$fallback_docker_config"\' EXIT',
            f"DOCKER_CONFIG=\"$fallback_docker_config\" {push_script}",
        ]
    )
    fallback_result = _run_wsl_script(distro, fallback_script, timeout_seconds=900)
    fallback_result = _push_success_from_remote_match(
        fallback_result,
        distro=distro,
        workspace_path=workspace_path,
        branch_name=branch_name,
    )
    if fallback_result.returncode == 0:
        fallback_result.stdout = (
            "Retried git push with an isolated DOCKER_CONFIG after the default Docker credential helper failed.\n"
            + (fallback_result.stdout or "")
        )
    return fallback_result


def prepare_cm_gpt_handoff(
    *,
    distro: str,
    workspace_path: str,
    branch_name: str,
    agent_name: str,
    model_name: str,
    item: Dict[str, Any],
    portal: Dict[str, Any],
    provider: str,
    reference_docs_path: str,
    prompt_template: str,
    cli_command_template: str,
    auto_launch: bool,
    desktop_url: str,
    strict_model_safety: bool,
    open_wsl_remote: bool,
    vscode_window_mode: str,
    allow_existing_changes: bool = False,
    capture_package_files: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    clean_branch_name = str(branch_name or "").strip()
    clean_agent_name = str(agent_name or "").strip()
    clean_model_name = str(model_name or "").strip()
    clean_provider = str(provider or "").strip() or "m365_desktop"
    if not clean_branch_name:
        raise CopilotIntegrationError("The work branch is not available yet.")
    if clean_provider == "m365_desktop" and not clean_agent_name:
        raise CopilotIntegrationError("Configure the CM GPT agent name before launching the integration.")
    if clean_provider not in {"m365_desktop", "copilot_cli", "vscode_bridge", "vscode", "codex_cli", "claude_cli", "custom_cli"}:
        raise CopilotIntegrationError(f"Unsupported agent provider '{clean_provider}'.")
    if strict_model_safety and clean_provider in {"copilot_cli", "codex_cli", "claude_cli", "custom_cli"}:
        raise CopilotIntegrationError("Strict CM GPT Safety Mode can only be used with the CM GPT-capable Copilot providers.")
    if strict_model_safety and clean_provider in {"vscode", "vscode_bridge"} and clean_model_name.strip().lower() != "cm gpt":
        raise CopilotIntegrationError("The configured Copilot model must be exactly 'CM GPT' before launching this workflow.")
    if clean_provider == "m365_desktop" and clean_agent_name.strip().lower() != "cm gpt":
        raise CopilotIntegrationError("The Microsoft 365 Copilot Desktop provider must use the approved 'CM GPT' agent.")

    effective_distro, clean_workspace_path = normalize_wsl_target_path(workspace_path, distro)
    if not clean_workspace_path:
        raise CopilotIntegrationError("Configure the Copilot workspace path for this portal before launching CM GPT.")
    dispatcher_workspace_path = clean_workspace_path
    _, normalized_reference_docs_path = normalize_wsl_target_path(reference_docs_path, effective_distro)

    # Autonomous executors must never switch or dirty the dispatcher workspace.
    # A dedicated worktree keeps each work-item branch independent from the
    # dashboard repository and from other in-flight automation runs.
    isolated_worktree_providers = {"copilot_cli", "codex_cli", "claude_cli", "custom_cli"}
    should_use_isolated_worktree = (
        clean_provider in isolated_worktree_providers
        or (clean_provider == "vscode_bridge" and str(vscode_window_mode or "").strip() == "new")
    )
    if should_use_isolated_worktree:
        clean_workspace_path = _prepare_isolated_agent_worktree(
            effective_distro,
            clean_workspace_path,
            clean_branch_name,
        )

    if allow_existing_changes:
        _ensure_git_workspace(effective_distro, clean_workspace_path)
        _ensure_current_branch(effective_distro, clean_workspace_path, clean_branch_name)
    else:
        _ensure_clean_workspace(effective_distro, clean_workspace_path)
        _checkout_branch(effective_distro, clean_workspace_path, clean_branch_name)
        _ensure_current_branch(effective_distro, clean_workspace_path, clean_branch_name)
    _ensure_workspace_context_excluded(effective_distro, clean_workspace_path)

    agent_identifier = get_custom_agent_identifier()
    home_path = _resolve_wsl_home(effective_distro)
    agent_path = f"{home_path}/.copilot/agents/{agent_identifier}.agent.md"
    safe_branch_slug = clean_branch_name.replace("/", "-")
    relative_package_directory = f"{WORKSPACE_CONTEXT_ROOT}/{safe_branch_slug}"
    package_directory = f"{clean_workspace_path}/{relative_package_directory}"
    agent_result_path = f"{package_directory}/agent-result.json"
    relative_agent_result_path = f"{relative_package_directory}/agent-result.json"
    prompt_path = f"{package_directory}/prompt.md"
    relative_prompt_path = f"{relative_package_directory}/prompt.md"
    cli_log_path = f"{package_directory}/agent-provider.log"
    package_files = build_work_item_package(
        item,
        portal,
        normalized_reference_docs_path,
        model_name=clean_model_name,
        strict_model_safety=strict_model_safety,
    )
    for relative_name, file_content in (capture_package_files or {}).items():
        clean_relative_name = str(relative_name or "").strip().replace("\\", "/").lstrip("/")
        if clean_relative_name and clean_relative_name not in package_files:
            package_files[clean_relative_name] = str(file_content or "")

    agent_file_paths: List[str] = []
    if clean_provider == "vscode":
        agent_file_paths = _write_vscode_agent_files(
            distro=effective_distro,
            wsl_agent_path=agent_path,
            agent_identifier=agent_identifier,
            agent_content=build_agent_markdown(agent_name=clean_agent_name, model_name=clean_model_name),
        )
    for relative_name, file_content in package_files.items():
        _write_file_via_wsl(
            effective_distro,
            f"{package_directory}/{relative_name}",
            file_content,
        )

    main_context_path = f"{package_directory}/work-item.md"
    attached_paths = [main_context_path]
    for extra_file in ["work-item.json", "description.html", "acceptance-criteria.html", "repro-steps.html"]:
        candidate_path = f"{package_directory}/{extra_file}"
        if extra_file in package_files:
            attached_paths.append(candidate_path)
    for extra_file in ["capture/INSTRUCTIONS.md", "capture/summary.md", "capture/manifest.json"]:
        candidate_path = f"{package_directory}/{extra_file}"
        if extra_file in package_files:
            attached_paths.append(candidate_path)

    instruction_files = discover_workspace_instruction_files(effective_distro, clean_workspace_path)
    instruction_index_path, instruction_package_paths, expected_instruction_files = _materialize_instruction_package(
        effective_distro,
        package_directory=package_directory,
        instruction_files=instruction_files,
    )
    attached_paths.extend(instruction_package_paths)
    docx_references = _extract_docx_references(item)
    reference_doc_matches = _discover_reference_docs(
        effective_distro,
        normalized_reference_docs_path,
        docx_references,
    )
    reference_docs_index_path, reference_docs_package_paths, reference_docs_source_paths = _materialize_reference_docs_package(
        effective_distro,
        package_directory=package_directory,
        reference_docs_path=normalized_reference_docs_path,
        docx_references=docx_references,
        matches=reference_doc_matches,
    )
    attached_paths.extend(reference_docs_package_paths)
    attached_paths.extend(reference_docs_source_paths)

    unique_attached_paths: List[str] = []
    for path in attached_paths:
        clean_path = str(path or "").strip()
        if clean_path and clean_path not in unique_attached_paths:
            unique_attached_paths.append(clean_path)

    relative_context_path = f"{relative_package_directory}/work-item.md"
    relative_instruction_index_path = (
        f"{relative_package_directory}/repo-instructions/index.md"
        if instruction_index_path
        else ""
    )
    relative_reference_docs_index_path = (
        f"{relative_package_directory}/reference-docs/index.md"
        if reference_docs_index_path
        else ""
    )
    relative_capture_instructions_path = (
        f"{relative_package_directory}/capture/INSTRUCTIONS.md"
        if "capture/INSTRUCTIONS.md" in package_files
        else ""
    )
    relative_capture_summary_path = (
        f"{relative_package_directory}/capture/summary.md"
        if "capture/summary.md" in package_files
        else ""
    )
    work_item_context_text = str(package_files["work-item.md"])
    result_contract = (
        f"When finished, write `{relative_agent_result_path}` as JSON with these fields: "
        "`status`, `green_light`, `summary`, `changed_files`, `final_report`, `spec_references`, `validation`, "
        "`instruction_files_read`, `capture_files_read`, `prs_reviewed`, `diffs_reviewed`, `work_items_reviewed`, "
        "`reviewer_notes`, and optional `error`. "
        "Set `green_light` to true only when the changes are ready to commit and push. "
        "Use repository-relative paths in `changed_files` and do not include this result file. "
        "`final_report` must explain what changed and why it changed. "
        "`spec_references` must list every spec or reference document used, including the spec path/name, section/topic, and how it informed the change. "
        "`instruction_files_read` must list every repository instruction original path read from the instruction package. "
        "`capture_files_read`, `prs_reviewed`, `diffs_reviewed`, and `work_items_reviewed` must identify the captured evidence used to decide and implement the change. "
        "Use an empty array when no spec was used."
    )
    instruction_body = _render_template(
        prompt_template,
        {
            "context_path": relative_context_path,
            "agent_result_path": relative_agent_result_path,
            "prompt_path": relative_prompt_path,
            "workspace_path": clean_workspace_path,
            "branch_name": clean_branch_name,
            "model_name": clean_model_name,
            "provider": clean_provider,
        },
    )
    if relative_instruction_index_path:
        instruction_body = (
            f"{instruction_body.strip()}\n\n"
            "Repository instruction package:\n"
            f"- Before editing, read `{relative_instruction_index_path}` and every instruction file listed there.\n"
            "- Follow those instructions together with the work item context.\n"
            "- In `agent-result.json`, include every original instruction path read in `instruction_files_read`."
        )
    if relative_reference_docs_index_path:
        instruction_body = (
            f"{instruction_body.strip()}\n\n"
            "Reference documentation package:\n"
            f"- Read `{relative_reference_docs_index_path}` before deciding that a referenced spec is unavailable.\n"
            "- Read the packaged text extracts listed there when matches exist.\n"
            "- If a spec was used, record its source path/name and section/topic in `spec_references`."
        )
    if relative_capture_instructions_path:
        instruction_body = (
            f"{instruction_body.strip()}\n\n"
            "Captured work item tree and implementation evidence:\n"
            f"- Start by reading `{relative_capture_instructions_path}`.\n"
            f"- Then read `{relative_capture_summary_path}` and the referenced work item and PR files.\n"
            "- Review PR diffs when they are available before deciding what to change.\n"
            "- Record the capture files, work items, PRs, and diffs used in `agent-result.json`."
        )
    configured_model = clean_model_name or "the configured agent model"
    model_policy = (
        f"Use the dashboard-selected model `{configured_model}` for this automation run. "
        "Do not substitute another model or provider."
    )
    provider_configuration = (
        "Run this request through the configured VS Code Copilot Language Model bridge:\n"
        f"- Provider: `{clean_provider}`\n"
        f"- Model Name: `{clean_model_name or '-'}`\n"
        f"- Agent Profile: `{clean_agent_name}`\n\n"
        "The bridge selects the configured VS Code Copilot model and applies the dashboard context and agent profile as instructions. "
        "It does not attempt to drive the VS Code Chat UI or create pull requests."
        if clean_provider == "vscode_bridge"
        else (
            "Run this request through GitHub Copilot CLI in non-interactive mode:\n"
            f"- Model Name: `{clean_model_name or '-'}`\n"
            f"- Agent Name: `{clean_agent_name or '-'}`\n\n"
            "The CLI receives this complete handoff and executes directly in the work-item branch. "
            "Do not create commits, push branches, or create pull requests; the dashboard performs those stages after validating `agent-result.json`."
            if clean_provider == "copilot_cli"
            else (
            "Run this request using the agent and model configured in the dashboard Settings:\n"
            f"- Agent Name: `{clean_agent_name}`\n"
            f"- Model Name: `{clean_model_name or '-'}`\n"
            f"- VS Code transport mode: `{agent_identifier}`\n\n"
            "The transport mode is only used by the dashboard to deliver the handoff to VS Code. "
            "Do not stop merely because the transport mode name is not visible in the chat UI. "
            "If the configured agent/model cannot be applied, follow the model safety policy below and record the mismatch in `reviewer_notes`."
            )
        )
    )
    prompt = "\n\n".join(
        [
            "# TFS Documentation Automation Handoff",
            provider_configuration,
            model_policy,
            (
                "Read the prepared context package before any repository analysis, build, lint, or edit. "
                "Do not start generic repository work until the work item context and repository instruction package have been read."
            ),
            "Primary instructions:",
            instruction_body.strip(),
            "Result contract:",
            result_contract,
            "Embedded work item context:",
            work_item_context_text.strip(),
        ]
    )
    desktop_prompt = build_m365_desktop_prompt(
        agent_name=clean_agent_name,
        branch_name=clean_branch_name,
        workspace_path=clean_workspace_path,
        context_text=work_item_context_text,
    )
    _write_file_via_wsl(effective_distro, prompt_path, prompt + "\n")
    _write_file_via_wsl(effective_distro, f"{package_directory}/m365-desktop-prompt.md", desktop_prompt + "\n")
    if clean_provider == "vscode":
        unique_attached_paths.insert(0, prompt_path)

    launch_metadata: Dict[str, Any] = {}
    if auto_launch:
        _remove_file_via_wsl(effective_distro, agent_result_path)
        if clean_provider == "m365_desktop":
            launch_metadata = _open_vscode_workspace_from_windows(
                distro=effective_distro,
                workspace_path=clean_workspace_path,
                open_wsl_remote=True,
                window_mode=vscode_window_mode,
            )
            _copy_text_to_windows_clipboard(desktop_prompt)
            if desktop_url:
                desktop_metadata = _open_windows_url(desktop_url)
                launch_metadata["desktop_url"] = str(desktop_metadata.get("url") or "")
                launch_metadata["desktop_stdout"] = str(desktop_metadata.get("stdout") or "")
                launch_metadata["desktop_stderr"] = str(desktop_metadata.get("stderr") or "")
        elif strict_model_safety:
            launch_metadata = _open_vscode_workspace_from_windows(
                distro=effective_distro,
                workspace_path=clean_workspace_path,
                open_wsl_remote=open_wsl_remote,
                window_mode=vscode_window_mode,
            )
        elif clean_provider in {"copilot_cli", "codex_cli", "claude_cli", "custom_cli"}:
            launch_metadata = _launch_cli_agent_in_wsl(
                distro=effective_distro,
                workspace_path=clean_workspace_path,
                command_template=cli_command_template,
                prompt_path=prompt_path,
                agent_result_path=agent_result_path,
                branch_name=clean_branch_name,
                model_name=clean_model_name,
                agent_name=clean_agent_name,
                provider=clean_provider,
                log_path=cli_log_path,
            )
        elif clean_provider == "vscode_bridge":
            launch_metadata = _queue_vscode_bridge_job(
                distro=effective_distro,
                package_directory=package_directory,
                workspace_path=clean_workspace_path,
                branch_name=clean_branch_name,
                agent_name=clean_agent_name,
                model_name=clean_model_name,
                prompt_path=prompt_path,
                agent_result_path=agent_result_path,
                open_new_window=str(vscode_window_mode or "").strip() == "new",
                dispatcher_workspace_path=dispatcher_workspace_path,
            )
        else:
            launch_metadata = _launch_vscode_chat_from_windows(
                distro=effective_distro,
                workspace_path=clean_workspace_path,
                agent_identifier=agent_identifier,
                agent_name=clean_agent_name,
                model_name=clean_model_name,
                strict_model_safety=strict_model_safety,
                attached_paths=unique_attached_paths,
                prompt=prompt,
                open_wsl_remote=open_wsl_remote,
                window_mode=vscode_window_mode,
            )

    workspace_state = inspect_workspace_state(effective_distro, clean_workspace_path)
    if clean_provider == "m365_desktop":
        result_status = "desktop_prepared" if auto_launch else "prepared"
    elif clean_provider in {"copilot_cli", "codex_cli", "claude_cli", "custom_cli", "vscode_bridge"}:
        result_status = "launched" if auto_launch else "prepared"
    else:
        result_status = "prepared" if strict_model_safety else ("launched" if auto_launch else "prepared")
    return {
        "status": result_status,
        "provider": clean_provider,
        "agent_name": clean_agent_name,
        "agent_identifier": agent_identifier,
        "workspace_path": clean_workspace_path,
        "context_path": main_context_path,
        "agent_result_path": agent_result_path,
        "prompt_path": prompt_path,
        "desktop_prompt_path": f"{package_directory}/m365-desktop-prompt.md",
        "branch_name": clean_branch_name,
        "distro": effective_distro,
        "attached_paths": unique_attached_paths,
        "reference_docs_path": normalized_reference_docs_path,
        "reference_docs_index_path": reference_docs_index_path,
        "reference_doc_matches": reference_doc_matches,
        "current_branch": str(workspace_state.get("current_branch") or ""),
        "tracked_changes": list(workspace_state.get("tracked_changes") or []),
        "workspace_target": str(launch_metadata.get("workspace_target") or ""),
        "launch_context": str(launch_metadata.get("launch_context") or ""),
        "desktop_url": str(launch_metadata.get("desktop_url") or ""),
        "cli_log_path": str(launch_metadata.get("cli_log_path") or ""),
        "cli_pid": str(launch_metadata.get("cli_pid") or ""),
        "bridge_job_path": str(launch_metadata.get("bridge_job_path") or ""),
        "instruction_index_path": instruction_index_path,
        "expected_instruction_files": expected_instruction_files,
        "agent_file_paths": agent_file_paths,
        "attached_unc_paths": list(launch_metadata.get("attached_unc_paths") or []),
        "launch_stdout": "\n".join(
            line
            for line in [
                str(launch_metadata.get("open_stdout") or "").strip(),
                str(launch_metadata.get("chat_stdout") or "").strip(),
                str(launch_metadata.get("desktop_stdout") or "").strip(),
            ]
            if line
        ),
        "launch_stderr": "\n".join(
            line
            for line in [
                str(launch_metadata.get("open_stderr") or "").strip(),
                str(launch_metadata.get("chat_stderr") or "").strip(),
                str(launch_metadata.get("desktop_stderr") or "").strip(),
            ]
            if line
        ),
    }
