#!/usr/bin/env bash

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

tracked_files="$(mktemp)"
trap 'rm -f "$tracked_files"' EXIT
git ls-files >"$tracked_files"

if [[ ! -s "$tracked_files" ]]; then
  echo "tracked-file audit failed: no files are staged or committed" >&2
  exit 1
fi

denied_paths="$(
  rg \
    '(^|/)(\.env($|\.)|node_modules|build|dist|coverage|playwright-report|test-results|__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.venv|\.canary|internal|raw|tool-responses)(/|$)|\.(sqlite|sqlite-shm|sqlite-wal|db|db-shm|db-wal|pem|key|p12|pfx|pdf|docx|xlsx|xls|zip|tar|tgz|log)$' \
    "$tracked_files" || true
)"

# Public templates are intentionally versioned; all other .env variants are denied.
denied_paths="$(
  printf '%s\n' "$denied_paths" |
    rg -v '(^|/)\.env\.example$' || true
)"

if [[ -n "$denied_paths" ]]; then
  echo "tracked-file audit failed: denied paths are tracked" >&2
  printf '%s\n' "$denied_paths" >&2
  exit 1
fi

secret_files="$(
  git grep -IlE \
    '(-----BEGIN ([A-Z0-9 ]+)?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[A-Za-z0-9-]{20,}|sk_live_[0-9A-Za-z]{16,}|https?://[^/@[:space:]]+:[^/@[:space:]]+@)' \
    -- . || true
)"

if [[ -n "$secret_files" ]]; then
  echo "tracked-file audit failed: high-confidence secret signatures found in:" >&2
  printf '%s\n' "$secret_files" >&2
  exit 1
fi

python3 scripts/scan_secrets.py

oversized_files="$(
  while IFS= read -r path; do
    size="$(wc -c <"$path")"
    if ((size > 5242880)); then
      printf '%s (%s bytes)\n' "$path" "$size"
    fi
  done <"$tracked_files"
)"

if [[ -n "$oversized_files" ]]; then
  echo "tracked-file audit failed: files over 5 MiB require explicit review:" >&2
  printf '%s\n' "$oversized_files" >&2
  exit 1
fi

echo "tracked-file audit passed"
