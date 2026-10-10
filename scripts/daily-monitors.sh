#!/bin/sh
# Check every saved monitor, publish runs/ to main, and leave Slack to monitor check.
# Crontab (9:00 local time):
# 0 9 * * * /Users/poboisvert/Desktop/GIT/mrk-agent/server/scripts/daily-monitors.sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
CLI="$ROOT/.venv/bin/facebook-marketplace"
LOCK="$ROOT/tmp/daily-monitors.lock"
LOG="${HOME:?HOME is not set}/Library/Logs/fb-marketplace-daily.log"

mkdir -p "$ROOT/tmp" "$(dirname "$LOG")"
exec >> "$LOG" 2>&1

echo "---- $(date '+%Y-%m-%d %H:%M:%S') ----"

if ! mkdir "$LOCK" 2>/dev/null; then
  holder=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    echo "already running"
    exit 0
  fi
  echo "removing stale lock"
  rm -rf "$LOCK"
  mkdir "$LOCK"
fi
echo $$ > "$LOCK/pid"

cleanup() {
  rm -rf "$LOCK"
}
trap cleanup EXIT

if [ ! -x "$CLI" ]; then
  echo "missing $CLI"
  exit 1
fi

branch=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
if [ "$branch" != "main" ]; then
  echo "expected branch main, found $branch"
  exit 1
fi

set +e
check_out=$("$CLI" monitor check 2>&1)
check_status=$?
set -e
printf '%s\n' "$check_out"

if [ "$check_status" -ne 0 ]; then
  if printf '%s\n' "$check_out" | grep -q "No monitors saved."; then
    exit 0
  fi
  echo "monitor check failed ($check_status)"
  exit "$check_status"
fi

git -C "$ROOT" add -A -- runs
if git -C "$ROOT" diff --cached --quiet; then
  echo "no listing changes"
  exit 0
fi

git -C "$ROOT" commit -m "$(cat <<'EOF'
Update saved monitor listings.

EOF
)"
git -C "$ROOT" push fb-marketplace HEAD:main
echo "published runs to main"
