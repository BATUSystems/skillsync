#!/usr/bin/env bash
# Quick smoke test of the generator (K13): --help, `layers list --json`, `link --dry-run`.
# Everything (config, layer, project) lives in a temporary directory under $TMPDIR; nothing else is touched.
# Usage: scripts/smoke.sh        Exit code: 0 = PASS, otherwise the first failing step.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="${PYTHON:-python3}"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/skillsync-smoke.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

# Isolated machine state: HOME and config point into $tmp.
export HOME="$tmp/home" XDG_CACHE_HOME="$tmp/cache" XDG_DATA_HOME="$tmp/data" XDG_CONFIG_HOME="$tmp/config"
export SKILLSYNC_CONFIG="$tmp/config.yaml"
mkdir -p "$HOME" "$tmp/core/instructions" "$tmp/core/skills/feature" "$tmp/proj/.github"

printf 'schema: 1\nid: core\nkind: core\nversion: 1.0.0\ncore_min: 0.9.0\n' > "$tmp/core/layer.yaml"
printf '# core\n' > "$tmp/core/README.md"
printf '# Changelog\n' > "$tmp/core/CHANGELOG.md"
printf 'base\n' > "$tmp/core/instructions/_base.md"
printf -- '---\nname: feature\ndescription: smoke\n---\nsmoke\n' > "$tmp/core/skills/feature/SKILL.md"
cat > "$tmp/config.yaml" <<YAML
layers:
  - {id: core, kind: core, path: $tmp/core, source: $tmp/core}
layer_rules:
  - {match: ["**"], use: [core]}
discover_paths: []
tags: {}
python_command: $py
YAML
: > "$tmp/proj/.github/.skillsync.yaml"

echo "[smoke] --help"
"$py" "$root/skillsync" --help > /dev/null

echo "[smoke] layers list --json"
"$py" "$root/skillsync" layers list --project "$tmp/proj" --json > "$tmp/layers.json"
"$py" -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d.get("status") != "BLOCKED", d' "$tmp/layers.json"
grep -q '"core"' "$tmp/layers.json"

echo "[smoke] link --dry-run"
"$py" "$root/skillsync" link "$tmp/proj" --dry-run
if [ -n "$(find "$tmp/proj" -mindepth 1 -not -path "$tmp/proj/.github" -not -path "$tmp/proj/.github/.skillsync.yaml")" ]; then
  echo "[smoke] FAIL: --dry-run wrote into the project" >&2
  exit 1
fi

echo "[smoke] PASS"
