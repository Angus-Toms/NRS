#!/bin/bash
# weekly.sh - unattended ingest + ratings extend + DB sync to Render.
# Fired by launchd (see ~/Library/LaunchAgents/com.angus.ptd.weekly.plist)
# at 00:00 on Sun, Mon, Tue, Thu.
#
# Sends a macOS notification on non-zero exit. All output appended to
# scripts/weekly.log so you can tail it live or grep through past runs.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG="$SCRIPT_DIR/weekly.log"

# launchd jobs start with an almost-empty PATH. Add the usual suspects so
# python3, wrangler, ssh, scp all resolve.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# Without a TTY, Python defaults to block-buffering stdout/stderr, which
# makes the log appear seconds/minutes behind reality when tailed.
# Force unbuffered so each print/tqdm update hits disk immediately.
export PYTHONUNBUFFERED=1

cd "$PROJECT_ROOT"

# Activate project venv (otherwise build_db's `python3 -m ptd_data.*` calls hit
# system python and immediately ModuleNotFoundError on pandas/duckdb/etc).
# shellcheck source=/dev/null
source "$PROJECT_ROOT/.venv/bin/activate"

# Redirect everything to the log from here on. Append mode so history accrues.
exec >> "$LOG" 2>&1

notify_fail() {
    osascript -e "display notification \"$1\" with title \"PTD weekly FAILED\" sound name \"Basso\"" >/dev/null 2>&1 || true
}

echo ""
echo "================================================================"
echo "  Run started: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "================================================================"
START=$SECONDS

"$SCRIPT_DIR/build_db.sh" --extend
rc=$?
if [ $rc -ne 0 ]; then
    echo "[FAIL] build_db.sh exited $rc"
    notify_fail "build_db.sh exited $rc. tail scripts/weekly.log"
    exit $rc
fi

"$SCRIPT_DIR/deploy.sh" --no-git --no-static
rc=$?
if [ $rc -ne 0 ]; then
    echo "[FAIL] deploy.sh exited $rc"
    notify_fail "deploy.sh exited $rc. tail scripts/weekly.log"
    exit $rc
fi

echo "[OK] Run completed in $(( SECONDS - START ))s at $(date '+%Y-%m-%d %H:%M:%S %Z')"
