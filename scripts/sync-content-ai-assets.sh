#!/usr/bin/env bash
set -euo pipefail

TARGET_WORKSPACE="${1:-${CONTENT_AI_TARGET_WORKSPACE:-$PWD}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTENT_AI_ROOT="${CONTENT_AI_REPO_PATH:-$(cd "$PROJECT_ROOT/../.." && pwd)}"
DESTINATION="$TARGET_WORKSPACE/.agents/content-ai"
TMP_DESTINATION="$TARGET_WORKSPACE/.agents/.content-ai.tmp"

if [ ! -d "$TARGET_WORKSPACE" ]; then
  echo "Target workspace was not found: $TARGET_WORKSPACE" >&2
  exit 1
fi

if [ ! -f "$CONTENT_AI_ROOT/AGENTS.md" ]; then
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

copy_if_exists "$CONTENT_AI_ROOT/AGENTS.md" "$TMP_DESTINATION/AGENTS.md"
copy_if_exists "$CONTENT_AI_ROOT/ai/manifest.json" "$TMP_DESTINATION/manifest.json"
copy_if_exists "$CONTENT_AI_ROOT/ai/CHANGELOG.md" "$TMP_DESTINATION/CHANGELOG.md"
copy_if_exists "$CONTENT_AI_ROOT/ai/skills" "$TMP_DESTINATION/skills"
copy_if_exists "$CONTENT_AI_ROOT/ai/agents" "$TMP_DESTINATION/agents"
copy_if_exists "$CONTENT_AI_ROOT/ai/instructions" "$TMP_DESTINATION/instructions"

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
  grep -qxF "/.agents/content-ai/" "$git_dir/info/exclude" || printf "\n/.agents/content-ai/\n" >> "$git_dir/info/exclude"
fi

echo "Content AI assets synced to $DESTINATION"
