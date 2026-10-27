#!/bin/sh
# Pre-push gate: reproduce what CI does, on the interpreters CI uses.
#
# Why this exists: a commit that was green on this box went red on GitHub, because the tests assumed a
# binary that is installed here (LibreOffice) and not on a runner — and a missing dependency
# (`PyJWT[crypto]`) was invisible until a clean install. Both classes are caught below in ~2 minutes.
#
#   tests/preflight.sh          # clean venv + container runs on 3.12 and 3.13
#   tests/preflight.sh --fast   # clean venv only (no docker)
#
# Exits non-zero on the first failure. Safe to run repeatedly; uses throwaway dirs.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
FAST=0
[ "${1:-}" = "--fast" ] && FAST=1
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

fail() { printf '\nFAIL: %s\n' "$*" >&2; exit 1; }
step() { printf '==> %s\n' "$*"; }

# ---------------------------------------------------------------- 1. declared dependencies only
step "clean venv from requirements.txt (catches undeclared imports)"
python3 -m venv "$TMP/venv"
"$TMP/venv/bin/pip" install -q --upgrade pip
"$TMP/venv/bin/pip" install -q -r "$ROOT/requirements.txt" || fail "requirements.txt does not install"
# Judge by exit status, not by scraping the tail: ResourceWarnings print *after* the summary line.
if ! (cd "$ROOT" && "$TMP/venv/bin/python" -m unittest discover -s tests >"$TMP/unit.log" 2>&1); then
  tail -20 "$TMP/unit.log" >&2
  fail "suite fails in a clean venv (undeclared dependency, or a test needing the host)"
fi

# ---------------------------------------------------------------- 2. no stray secrets / debug flags
step "no credential material in tracked files"
if git -C "$ROOT" ls-files | grep -qiE '(^|/)\.env$|\.pem$|\.key$'; then
  fail "an env/key file is tracked by git"
fi
# Long hex strings are how a real token or shared secret looks when it leaks into source.
# (capture first: `git grep | head` always exits 0, so the pipeline status would lie)
LEAKS=$(git -C "$ROOT" grep -nE '\b[0-9a-f]{40,}\b' -- . 2>/dev/null || true)
if [ -n "$LEAKS" ]; then
  printf '%s\n' "$LEAKS" | head -3 >&2
  fail "a long hex string (token/secret shaped) is committed"
fi

# ---------------------------------------------------------------- 3. the interpreters CI uses
if [ "$FAST" = "1" ]; then
  printf '\npreflight: clean venv OK (container checks skipped by --fast)\n'
  exit 0
fi
command -v docker >/dev/null 2>&1 || { printf '\npreflight: docker unavailable, skipping 3.12/3.13 (NOT a pass)\n' >&2; exit 0; }
for version in 3.12 3.13; do
  step "python:$version, CI-shaped (no LibreOffice, no services)"
  if ! docker run --rm -v "$ROOT:/app" -w /app "python:$version" sh -c \
      'pip install -q -r requirements.txt >/dev/null && python -m unittest discover -s tests >/tmp/unit.log 2>&1 || { tail -20 /tmp/unit.log; exit 1; }'; then
    fail "suite fails on python:$version"
  fi
done

printf '\npreflight: OK — clean venv, no leaked secrets, 3.12 and 3.13\n'
