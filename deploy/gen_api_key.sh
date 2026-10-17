#!/usr/bin/env bash
# Generate a strong jury API key into secrets/jury_api_key (0600). Prints NO secret material.
# Usage: bash deploy/gen_api_key.sh [--force]
# Override target with CASHWATCH_API_KEY_FILE (must not be inside a git-tracked directory).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${CASHWATCH_API_KEY_FILE:-$ROOT/secrets/jury_api_key}"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

if [[ -e "$TARGET" && $FORCE -ne 1 ]]; then
  echo "refusing: key file already exists at $TARGET (use --force to rotate; distribute the new key privately)" >&2
  exit 1
fi
umask 077
DIR="$(dirname "$TARGET")"
mkdir -p "$DIR"
chmod 700 "$DIR"
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 && \
   git -C "$ROOT" ls-files --error-unmatch "$TARGET" >/dev/null 2>&1; then
  echo "refusing: $TARGET is tracked by git" >&2
  exit 1
fi
TMP="$(mktemp "$DIR/.jury_api_key.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
# 32 random bytes from the OS CSPRNG -> 64 hex chars (no shell-unsafe characters).
if command -v openssl >/dev/null 2>&1; then
  openssl rand -hex 32 | tr -d '\n' > "$TMP"
else
  od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$TMP"
fi
LEN=$(wc -c < "$TMP" | tr -d ' ')
if [[ "$LEN" -ne 64 ]]; then
  echo "key generation failed (unexpected length)" >&2
  exit 1
fi
chmod 600 "$TMP"
mv -f "$TMP" "$TARGET"
trap - EXIT
echo "wrote jury API key to $TARGET (64 hex chars = 32 random bytes, mode 0600)."
echo "Deliver it to the organizer only through their private channel; never commit or paste it."
