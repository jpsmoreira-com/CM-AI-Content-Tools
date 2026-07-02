#!/usr/bin/env bash
set -euo pipefail

TARGET_WORKSPACE="${CONTENT_AI_TARGET_WORKSPACE:-$PWD}"
CONTENT_AI_REPO_PATH="${CONTENT_AI_REPO_PATH:-/workspaces/CM-AI-Content-Skills}"
CONTENT_AI_REPO_URL="${CONTENT_AI_REPO_URL:-}"
CONTENT_AI_BRANCH="${CONTENT_AI_BRANCH:-main}"
CONTENT_AI_TFS_HOST="${CONTENT_AI_TFS_HOST:-tfs-product.cmf.criticalmanufacturing.com}"
PIPELINE_PROJECT_PATH="$CONTENT_AI_REPO_PATH/projects/tfs-doc-automation-mvp"
PIPELINE_VENV="${TFS_AUTONOMOUS_PIPELINE_VENV:-$HOME/.venvs/tfs-doc-automation-mvp}"
PIPELINE_PORT="${TFS_AUTONOMOUS_PIPELINE_PORT:-7000}"
CONTENT_AI_SETTINGS_PATH="${CONTENT_AI_SETTINGS_PATH:-/workspaces/.content-ai-settings/tfs-doc-automation-mvp}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-$HOME/.npm-global}"
export CODEX_HOME
export NPM_CONFIG_PREFIX
export PATH="$NPM_CONFIG_PREFIX/bin:/usr/local/share/nvm/current/bin:$PATH"

ensure_settings_path() {
  if ! mkdir -p "$CONTENT_AI_SETTINGS_PATH" 2>/dev/null; then
    if command -v sudo >/dev/null 2>&1; then
      sudo mkdir -p "$CONTENT_AI_SETTINGS_PATH"
      sudo chown -R "$(id -u):$(id -g)" "$CONTENT_AI_SETTINGS_PATH"
    else
      echo "Could not create CONTENT_AI_SETTINGS_PATH: $CONTENT_AI_SETTINGS_PATH" >&2
      exit 1
    fi
  fi
  if [ ! -w "$CONTENT_AI_SETTINGS_PATH" ] && command -v sudo >/dev/null 2>&1; then
    sudo chown -R "$(id -u):$(id -g)" "$CONTENT_AI_SETTINGS_PATH"
  fi
}

set_git_store_helper() {
  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi
  git config --global --unset-all credential.helper >/dev/null 2>&1 || true
  git config --global credential.helper store
  git config --global credential.useHttpPath true
}

restore_persisted_git_credentials() {
  persisted_credentials_path="$CONTENT_AI_SETTINGS_PATH/git-credentials"
  if [ ! -f "$persisted_credentials_path" ]; then
    return 0
  fi
  cp "$persisted_credentials_path" "$HOME/.git-credentials"
  chmod 600 "$HOME/.git-credentials" || true
  set_git_store_helper
  echo "Restored persisted TFS Git credentials from CONTENT_AI_SETTINGS_PATH."
}

mirror_git_credentials_to_settings() {
  credential_path="$HOME/.git-credentials"
  if [ ! -f "$credential_path" ]; then
    return 0
  fi
  ensure_settings_path
  cp "$credential_path" "$CONTENT_AI_SETTINGS_PATH/git-credentials"
  chmod 600 "$CONTENT_AI_SETTINGS_PATH/git-credentials" || true
}

infer_target_repository() {
  if [ -n "${CONTENT_AI_TARGET_REPOSITORY:-}" ]; then
    printf "%s" "$CONTENT_AI_TARGET_REPOSITORY"
    return
  fi
  if git -C "$TARGET_WORKSPACE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    remote_url="$(git -C "$TARGET_WORKSPACE" config --get remote.origin.url || true)"
    remote_name="${remote_url##*/}"
    remote_name="${remote_name%.git}"
    if [ -n "$remote_name" ]; then
      printf "%s" "$remote_name"
      return
    fi
  fi
  basename "$TARGET_WORKSPACE"
}

git_credentials_are_available() {
  set_git_store_helper
  credential_output="$(printf "url=https://%s/\n\n" "$CONTENT_AI_TFS_HOST" | git credential fill 2>/dev/null || true)"
  printf "%s\n" "$credential_output" | grep -q '^password='
}

configure_tfs_git_credentials() {
  if ! command -v git >/dev/null 2>&1; then
    echo "Git was not found; skipping TFS Git credential preflight." >&2
    return 0
  fi
  if git_credentials_are_available; then
    echo "TFS Git credentials are already available for $CONTENT_AI_TFS_HOST."
    return 0
  fi

  if [ -n "${CONTENT_AI_HOST_GIT_CREDENTIALS_PATH:-}" ]; then
    if [ -f "$CONTENT_AI_HOST_GIT_CREDENTIALS_PATH" ]; then
      cp "$CONTENT_AI_HOST_GIT_CREDENTIALS_PATH" "$HOME/.git-credentials"
      chmod 600 "$HOME/.git-credentials" || true
      set_git_store_helper
      mirror_git_credentials_to_settings
      echo "Copied Git credentials from CONTENT_AI_HOST_GIT_CREDENTIALS_PATH."
    else
      echo "Configured CONTENT_AI_HOST_GIT_CREDENTIALS_PATH was not found: $CONTENT_AI_HOST_GIT_CREDENTIALS_PATH" >&2
    fi
  elif [ -n "${CONTENT_AI_TFS_GIT_USERNAME:-}" ] && { [ -n "${CONTENT_AI_TFS_GIT_PASSWORD:-}" ] || [ -n "${CONTENT_AI_TFS_GIT_TOKEN:-}" ]; }; then
    CONTENT_AI_TFS_HOST="$CONTENT_AI_TFS_HOST" \
    CONTENT_AI_TFS_GIT_USERNAME="$CONTENT_AI_TFS_GIT_USERNAME" \
    CONTENT_AI_TFS_GIT_PASSWORD_VALUE="${CONTENT_AI_TFS_GIT_PASSWORD:-${CONTENT_AI_TFS_GIT_TOKEN:-}}" \
    python3 - <<'PY'
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote, urlparse

host = os.environ["CONTENT_AI_TFS_HOST"]
username = os.environ["CONTENT_AI_TFS_GIT_USERNAME"]
password = os.environ["CONTENT_AI_TFS_GIT_PASSWORD_VALUE"]
credential_path = Path.home() / ".git-credentials"
credential_path.parent.mkdir(parents=True, exist_ok=True)
existing = credential_path.read_text(encoding="utf-8", errors="replace").splitlines() if credential_path.exists() else []

def matches_host(line: str) -> bool:
    parsed = urlparse(line.strip())
    if not parsed.netloc:
        return False
    return parsed.netloc.rsplit("@", 1)[-1].lower() == host.lower()

retained = [line for line in existing if not matches_host(line)]
retained.append(f"https://{quote(username, safe='')}:{quote(password, safe='')}@{host}")
credential_path.write_text("\n".join(retained).rstrip() + "\n", encoding="utf-8")
credential_path.chmod(0o600)
PY
    set_git_store_helper
    mirror_git_credentials_to_settings
    echo "Configured TFS Git credentials from CONTENT_AI_TFS_GIT_USERNAME and token environment variables."
  else
    echo "TFS Git credentials are not available in this devcontainer yet." >&2
    echo "Set CONTENT_AI_HOST_GIT_CREDENTIALS_PATH, or CONTENT_AI_TFS_GIT_USERNAME plus CONTENT_AI_TFS_GIT_PASSWORD/CONTENT_AI_TFS_GIT_TOKEN, before rebuilding." >&2
    return 0
  fi

  if git_credentials_are_available; then
    echo "TFS Git credentials validated for $CONTENT_AI_TFS_HOST."
  else
    echo "TFS Git credential setup ran, but Git still cannot resolve credentials for $CONTENT_AI_TFS_HOST." >&2
  fi
}

ensure_node_runtime() {
  if command -v npm >/dev/null 2>&1; then
    return 0
  fi
  if [ -s "/usr/local/share/nvm/nvm.sh" ]; then
    # shellcheck disable=SC1091
    . "/usr/local/share/nvm/nvm.sh"
    nvm install --lts
    nvm alias default 'lts/*' >/dev/null 2>&1 || true
    nvm use --lts >/dev/null
  fi
}

ensure_codex_cli() {
  if [ "${TFS_AUTONOMOUS_INSTALL_CODEX_CLI:-true}" != "true" ]; then
    return 0
  fi
  mkdir -p "$NPM_CONFIG_PREFIX/bin" "$CODEX_HOME"
  ensure_node_runtime
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm was not found; skipping Codex CLI install. The dashboard will report a provider preflight warning if Codex CLI is selected." >&2
    return 0
  fi
  if command -v codex >/dev/null 2>&1 || [ -x "$NPM_CONFIG_PREFIX/bin/codex" ]; then
    echo "Codex CLI is already available."
    return 0
  fi
  echo "Installing Codex CLI into $NPM_CONFIG_PREFIX..."
  npm install -g @openai/codex
}

ensure_settings_path
restore_persisted_git_credentials
configure_tfs_git_credentials
ensure_codex_cli

if [ ! -d "$CONTENT_AI_REPO_PATH/.git" ]; then
  if [ -z "$CONTENT_AI_REPO_URL" ]; then
    echo "CONTENT_AI_REPO_URL is required when $CONTENT_AI_REPO_PATH is not already cloned." >&2
    exit 1
  fi
  if [ -d "$CONTENT_AI_REPO_PATH" ] && [ ! -w "$CONTENT_AI_REPO_PATH" ] && command -v sudo >/dev/null 2>&1; then
    sudo chown -R "$(id -u):$(id -g)" "$CONTENT_AI_REPO_PATH"
  fi
  mkdir -p "$(dirname "$CONTENT_AI_REPO_PATH")"
  git clone --branch "$CONTENT_AI_BRANCH" "$CONTENT_AI_REPO_URL" "$CONTENT_AI_REPO_PATH"
else
  git -C "$CONTENT_AI_REPO_PATH" fetch origin "$CONTENT_AI_BRANCH" --prune
  git -C "$CONTENT_AI_REPO_PATH" checkout "$CONTENT_AI_BRANCH"
  git -C "$CONTENT_AI_REPO_PATH" pull --ff-only origin "$CONTENT_AI_BRANCH"
fi

if [ ! -f "$PIPELINE_PROJECT_PATH/requirements.txt" ]; then
  echo "TFS Autonomous Pipeline project was not found at $PIPELINE_PROJECT_PATH." >&2
  exit 1
fi

if [ ! -x "$PIPELINE_VENV/bin/python" ]; then
  python3 -m venv "$PIPELINE_VENV"
fi

"$PIPELINE_VENV/bin/python" -m pip install --upgrade pip
"$PIPELINE_VENV/bin/python" -m pip install -r "$PIPELINE_PROJECT_PATH/requirements.txt"

CONTENT_AI_PIPELINE_PROJECT_PATH="$PIPELINE_PROJECT_PATH" \
CONTENT_AI_SETTINGS_PATH="$CONTENT_AI_SETTINGS_PATH" \
CONTENT_AI_TARGET_REPOSITORY="$(infer_target_repository)" \
CONTENT_AI_TARGET_WORKSPACE="$TARGET_WORKSPACE" \
TFS_AUTONOMOUS_PIPELINE_PORT="$PIPELINE_PORT" \
"$PIPELINE_VENV/bin/python" - <<'PY'
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


project_path = Path(os.environ["CONTENT_AI_PIPELINE_PROJECT_PATH"])
settings_path = Path(os.environ["CONTENT_AI_SETTINGS_PATH"])
target_workspace = os.environ["CONTENT_AI_TARGET_WORKSPACE"]
target_repository = os.environ["CONTENT_AI_TARGET_REPOSITORY"]
pipeline_port = os.environ.get("TFS_AUTONOMOUS_PIPELINE_PORT", "7000")
settings_path.mkdir(parents=True, exist_ok=True)

env_path = project_path / ".env"
env_example_path = project_path / ".env.example"
persisted_env_path = settings_path / ".env"
if not env_path.exists():
    if persisted_env_path.exists():
        shutil.copyfile(persisted_env_path, env_path)
    elif env_example_path.exists():
        shutil.copyfile(env_example_path, env_path)


def read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def write_env_values(path: Path, values: dict[str, str]) -> None:
    lines = read_env_lines(path)
    seen: set[str] = set()
    updated_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in values:
            updated_lines.append(f"{key}={json.dumps(values[key])}")
            seen.add(key)
        else:
            updated_lines.append(line)
    for key, value in values.items():
        if key not in seen:
            updated_lines.append(f"{key}={json.dumps(value)}")
    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


write_env_values(
    env_path,
    {
        "DOC_AUTOMATION_SERVER_HOST": "0.0.0.0",
        "DOC_AUTOMATION_SERVER_PORT": pipeline_port,
        "DOC_AUTOMATION_SERVER_AUTO_PORT": "false",
        "DOC_AUTOMATION_TFS_VERIFY_SSL": os.environ.get("CONTENT_AI_TFS_VERIFY_SSL", "false"),
        "DOC_AUTOMATION_TFS_CA_BUNDLE_PATH": os.environ.get("CONTENT_AI_TFS_CA_BUNDLE_PATH", ""),
        "DOC_AUTOMATION_FINAL_REPORTS_PATH": f"{target_workspace}/.automation-reports",
        "DOC_AUTOMATION_CONTEXT_CAPTURE_WORKSPACE_SCAN_ROOTS_JSON": json.dumps([target_workspace]),
    },
)
shutil.copyfile(env_path, persisted_env_path)

base_config_path = project_path / "config" / "tfs_dashboard.json"
local_config_path = project_path / "config" / "tfs_dashboard.local.json"
persisted_config_path = settings_path / "tfs_dashboard.local.json"
if not local_config_path.exists() and persisted_config_path.exists():
    local_config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(persisted_config_path, local_config_path)
source_config_path = local_config_path if local_config_path.exists() else base_config_path
config = json.loads(source_config_path.read_text(encoding="utf-8"))
portals = config.get("portals") or []
matched_portal = ""
for portal in portals:
    if str(portal.get("repository") or "").strip() == target_repository:
        portal["copilot_workspace_path"] = target_workspace
        portal["auth_mode"] = "Git Credentials"
        matched_portal = target_repository
        break
if not matched_portal and portals:
    portals[0]["copilot_workspace_path"] = target_workspace
    portals[0]["auth_mode"] = "Git Credentials"
    matched_portal = str(portals[0].get("repository") or "")
if matched_portal:
    config["DEFAULT_PORTAL"] = matched_portal
local_config_path.parent.mkdir(parents=True, exist_ok=True)
local_config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
shutil.copyfile(local_config_path, persisted_config_path)

try:
    subprocess.run(
        ["git", "-C", target_workspace, "rev-parse", "--is-inside-work-tree"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    git_dir = subprocess.run(
        ["git", "-C", target_workspace, "rev-parse", "--absolute-git-dir"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
except (OSError, subprocess.CalledProcessError):
    git_dir = ""
if git_dir:
    exclude_path = Path(git_dir) / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8", errors="replace") if exclude_path.exists() else ""
    additions = ["/.agents/content-ai/", "/.automation-context/", "/.automation-reports/"]
    with exclude_path.open("a", encoding="utf-8") as handle:
        for addition in additions:
            if addition not in existing:
                handle.write(f"\n{addition}\n")
PY

bash "$PIPELINE_PROJECT_PATH/scripts/sync-content-ai-assets.sh" "$TARGET_WORKSPACE"

mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/tfs-autonomous-pipeline" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export CONTENT_AI_SETTINGS_PATH="\${CONTENT_AI_SETTINGS_PATH:-$CONTENT_AI_SETTINGS_PATH}"
export CODEX_HOME="\${CODEX_HOME:-$CODEX_HOME}"
export NPM_CONFIG_PREFIX="\${NPM_CONFIG_PREFIX:-$NPM_CONFIG_PREFIX}"
export PATH="\$NPM_CONFIG_PREFIX/bin:/usr/local/share/nvm/current/bin:\$PATH"
cd "$PIPELINE_PROJECT_PATH"
case "\${1:-dashboard}" in
  dashboard)
    exec "$PIPELINE_VENV/bin/python" -m uvicorn main:app --host "\${TFS_AUTONOMOUS_PIPELINE_HOST:-0.0.0.0}" --port "\${TFS_AUTONOMOUS_PIPELINE_PORT:-7000}"
    ;;
  worker)
    exec "$PIPELINE_VENV/bin/python" run_worker.py
    ;;
  stop)
    pkill -f "uvicorn main:app" || true
    pkill -f "$PIPELINE_PROJECT_PATH/run_worker.py" || true
    ;;
  sync-assets)
    exec bash "$PIPELINE_PROJECT_PATH/scripts/sync-content-ai-assets.sh" "\${2:-$TARGET_WORKSPACE}"
    ;;
  *)
    echo "Usage: tfs-autonomous-pipeline {dashboard|worker|stop|sync-assets}" >&2
    exit 2
    ;;
esac
EOF
chmod +x "$HOME/.local/bin/tfs-autonomous-pipeline"

echo "TFS Autonomous Pipeline bootstrap completed for $TARGET_WORKSPACE"
