#!/usr/bin/env bash
set -euo pipefail

TARGET_WORKSPACE="${1:-${CONTENT_AI_TARGET_WORKSPACE:-$PWD}}"
# Shared assets (skills, subagents, managed rules) come from the sibling
# CM-AI-Content-Skills checkout, not from this repository.
CONTENT_AI_ROOT="${CONTENT_AI_REPO_PATH:-/workspaces/CM-AI-Content-Skills}"
DESTINATION="$TARGET_WORKSPACE/.agents/content-ai"
TMP_DESTINATION="$TARGET_WORKSPACE/.agents/.content-ai.tmp"
SYNC_ROOT_AGENTS="${CONTENT_AI_SYNC_ROOT_AGENTS:-true}"
ROOT_AGENTS_SOURCE="${CONTENT_AI_ROOT_AGENTS_SOURCE:-$CONTENT_AI_ROOT/instructions/AGENTS.md}"

if [ ! -f "$ROOT_AGENTS_SOURCE" ]; then
  ROOT_AGENTS_SOURCE="$CONTENT_AI_ROOT/AGENTS.md"
fi

if [ ! -d "$TARGET_WORKSPACE" ]; then
  echo "Target workspace was not found: $TARGET_WORKSPACE" >&2
  exit 1
fi

if [ ! -d "$CONTENT_AI_ROOT/skills" ]; then
  echo "Shared Content AI assets were not found at $CONTENT_AI_ROOT (expected a CM-AI-Content-Skills checkout). Set CONTENT_AI_REPO_PATH." >&2
  exit 1
fi

if [ ! -f "$ROOT_AGENTS_SOURCE" ]; then
  echo "Content AI asset repository was not found at: $CONTENT_AI_ROOT" >&2
  echo "Set CONTENT_AI_REPO_PATH to the CM-AI-Content-Skills checkout." >&2
  exit 1
fi

case "$DESTINATION" in
  */.agents/content-ai) ;;
  *)
    echo "Refusing to sync outside the managed .agents/content-ai destination." >&2
    exit 1
    ;;
esac

rm -rf "$TMP_DESTINATION"
mkdir -p "$TMP_DESTINATION"

copy_if_exists() {
  local source_path="$1"
  local destination_path="$2"
  if [ -e "$source_path" ]; then
    mkdir -p "$(dirname "$destination_path")"
    cp -a "$source_path" "$destination_path"
  fi
}

copy_if_exists "$ROOT_AGENTS_SOURCE" "$TMP_DESTINATION/AGENTS.md"
copy_if_exists "$CONTENT_AI_ROOT/manifest.json" "$TMP_DESTINATION/manifest.json"
copy_if_exists "$CONTENT_AI_ROOT/CHANGELOG.md" "$TMP_DESTINATION/CHANGELOG.md"
copy_if_exists "$CONTENT_AI_ROOT/skills" "$TMP_DESTINATION/skills"
copy_if_exists "$CONTENT_AI_ROOT/agents" "$TMP_DESTINATION/agents"
copy_if_exists "$CONTENT_AI_ROOT/instructions" "$TMP_DESTINATION/instructions"

{
  echo "{"
  echo "  \"source\": \"${CONTENT_AI_ROOT//\\/\\\\}\","
  echo "  \"destination\": \"${DESTINATION//\\/\\\\}\","
  echo "  \"generated_at_utc\": \"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\","
  echo "  \"files\": ["
  first_file=true
  while IFS= read -r file_path; do
    relative_path="${file_path#$TMP_DESTINATION/}"
    checksum="$(sha256sum "$file_path" | awk '{print $1}')"
    if [ "$first_file" = true ]; then
      first_file=false
    else
      echo ","
    fi
    printf "    {\"path\": \"%s\", \"sha256\": \"%s\"}" "$relative_path" "$checksum"
  done < <(find "$TMP_DESTINATION" -type f ! -name install-manifest.json | sort)
  echo
  echo "  ]"
  echo "}"
} > "$TMP_DESTINATION/install-manifest.json"

rm -rf "$DESTINATION"
mkdir -p "$(dirname "$DESTINATION")"
mv "$TMP_DESTINATION" "$DESTINATION"

if git -C "$TARGET_WORKSPACE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git_dir="$(git -C "$TARGET_WORKSPACE" rev-parse --absolute-git-dir)"
  mkdir -p "$git_dir/info"
  touch "$git_dir/info/exclude"
  for exclude_entry in "/.agents/" "/AGENTS.md" "/.claude/skills" "/.claude/agents" "/.codex/" "agents.lock"; do
    grep -qxF "$exclude_entry" "$git_dir/info/exclude" || printf "\n%s\n" "$exclude_entry" >> "$git_dir/info/exclude"
  done
fi

if [ "$SYNC_ROOT_AGENTS" = "true" ] && [ -f "$ROOT_AGENTS_SOURCE" ]; then
  root_agents_path="$TARGET_WORKSPACE/AGENTS.md"
  if [ -f "$root_agents_path" ] && ! cmp -s "$ROOT_AGENTS_SOURCE" "$root_agents_path"; then
    mkdir -p "$DESTINATION/backups"
    cp -a "$root_agents_path" "$DESTINATION/backups/AGENTS.md.before-content-ai-sync"
  fi
  cp -a "$ROOT_AGENTS_SOURCE" "$root_agents_path"
  if git -C "$TARGET_WORKSPACE" ls-files --error-unmatch AGENTS.md >/dev/null 2>&1; then
    git -C "$TARGET_WORKSPACE" update-index --skip-worktree AGENTS.md || true
    echo "Root AGENTS.md is tracked; marked skip-worktree after managed Content AI sync."
  fi
fi

# --- dotagents: standard skill/subagent layout ------------------------------------
# The namespaced .agents/content-ai/ copy above feeds the pipeline's context packages.
# Interactive and CLI agents (Claude Code, Codex, Copilot CLI / VS Code bridge) load
# skills from the dotagents-standard locations instead: .agents/skills/ with tool
# symlinks. Install them with @sentry/dotagents so a portal that commits its own
# agents.toml gets exactly its pinned versions, and a bare target repository gets a
# generated manifest wired to the .agents/content-ai copy this script just synced
# (dotagents only accepts path sources inside the project root).
CONTENT_AI_DOTAGENTS="${CONTENT_AI_DOTAGENTS:-true}"
CONTENT_AI_DOTAGENTS_VERSION="${CONTENT_AI_DOTAGENTS_VERSION:-3.0.1}"

install_dotagents_assets() {
  if [ "$CONTENT_AI_DOTAGENTS" != "true" ]; then
    echo "dotagents install disabled (CONTENT_AI_DOTAGENTS=$CONTENT_AI_DOTAGENTS)."
    return 0
  fi
  if ! command -v npx >/dev/null 2>&1; then
    echo "npx not found; skipping dotagents skill install. Agents fall back to .agents/content-ai/skills." >&2
    return 0
  fi

  local manifest="$TARGET_WORKSPACE/agents.toml"
  local generated=false

  if [ ! -f "$manifest" ]; then
    generated=true
    {
      echo "# Generated by sync-content-ai-assets.sh — not committed (see .git/info/exclude)."
      echo "# A portal that manages its own skills should commit an agents.toml pinned to a"
      echo "# CM-AI-Content-Skills release tag instead; this generated file then stops being written."
      echo "version = 1"
      echo "agents = [\"claude\", \"codex\", \"vscode\"]"
      local skill_dir skill_name
      for skill_dir in "$DESTINATION"/skills/*/; do
        [ -f "$skill_dir/SKILL.md" ] || continue
        skill_name="$(basename "$skill_dir")"
        echo ""
        echo "[[skills]]"
        echo "name = \"$skill_name\""
        echo "source = \"path:./.agents/content-ai/skills/$skill_name\""
      done
      local agent_file agent_name
      for agent_file in "$DESTINATION"/agents/*.md; do
        [ -f "$agent_file" ] || continue
        agent_name="$(basename "$agent_file" .md)"
        [ "$agent_name" = "README" ] && continue
        echo ""
        echo "[[subagents]]"
        echo "name = \"$agent_name\""
        echo "source = \"path:./.agents/content-ai\""
        echo "targets = [\"claude\", \"codex\"]"
      done
    } > "$manifest"
    if git -C "$TARGET_WORKSPACE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      local exclude_file
      exclude_file="$(git -C "$TARGET_WORKSPACE" rev-parse --absolute-git-dir)/info/exclude"
      grep -qxF "/agents.toml" "$exclude_file" 2>/dev/null || printf "\n/agents.toml\n" >> "$exclude_file"
    fi
  fi

  if (cd "$TARGET_WORKSPACE" && npx --yes "@sentry/dotagents@$CONTENT_AI_DOTAGENTS_VERSION" --project install); then
    echo "dotagents installed shared skills into $TARGET_WORKSPACE/.agents/skills"
    return 0
  fi

  # Safety net: if a future dotagents version rejects the generated manifest, retry
  # once without subagents so skills always land.
  if [ "$generated" = true ] && grep -q '^\[\[subagents\]\]' "$manifest"; then
    echo "dotagents install failed; retrying generated manifest without subagents." >&2
    sed -i '/^\[\[subagents\]\]/,$d' "$manifest"
    if (cd "$TARGET_WORKSPACE" && npx --yes "@sentry/dotagents@$CONTENT_AI_DOTAGENTS_VERSION" --project install); then
      echo "dotagents installed shared skills (subagents skipped) into $TARGET_WORKSPACE/.agents/skills"
      return 0
    fi
  fi
  echo "dotagents install failed; agents fall back to .agents/content-ai/skills." >&2
  return 0
}

install_dotagents_assets

echo "Content AI assets synced to $DESTINATION"
