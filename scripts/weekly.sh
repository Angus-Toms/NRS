#!/bin/bash
# weekly.sh - ingest + ratings extend + DB sync to Render. Run by hand.
#
# Sends a macOS notification on non-zero exit. Two log files:
#   weekly.latest.log   — current run only, cleared on start. Filtered down
#                         to step headers + key summary lines so a quick
#                         tail shows what stage the run is in without
#                         drowning in per-athlete noise.
#   weekly.verbose.log  — current run only, cleared on start. Unfiltered
#                         output for when something fails and you need
#                         the per-athlete detail.
#   weekly.history.csv  — append-only. One row per run with start/finish
#                         timestamps, duration, status, and the per-table
#                         net deltas (new events / races / athletes /
#                         results) so long-term progress is grep-able.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LATEST_LOG="$SCRIPT_DIR/weekly.latest.log"
VERBOSE_LOG="$SCRIPT_DIR/weekly.verbose.log"
HISTORY_CSV="$SCRIPT_DIR/weekly.history.csv"

# launchd jobs start with an almost-empty PATH. Add the usual suspects so
# python3, wrangler, ssh, scp all resolve.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# Without a TTY, Python defaults to block-buffering stdout/stderr, which
# makes the log appear seconds/minutes behind reality when tailed.
# Force unbuffered so each print/tqdm update hits disk immediately.
export PYTHONUNBUFFERED=1

cd "$PROJECT_ROOT"

# shellcheck source=/dev/null
source "$PROJECT_ROOT/.venv/bin/activate"

# Truncate both per-run logs on entry — each invocation starts fresh.
: > "$LATEST_LOG"
: > "$VERBOSE_LOG"

# History CSV header (only written if file is missing or empty).
if [ ! -s "$HISTORY_CSV" ]; then
    echo "started_at,finished_at,duration_s,status,new_events,new_races,new_athletes,new_results" > "$HISTORY_CSV"
fi

# Quick read-only count snapshot via duckdb. Read-only so it can run while
# another writer is active (e.g. if a previous run is still finishing up).
db_count() {
    local table="$1"
    python3 - "$table" <<'PY' 2>/dev/null || echo 0
import duckdb, sys
con = duckdb.connect('ptd_data/ptd.duckdb', read_only=True)
print(con.execute(f"select count(*) from {sys.argv[1]}").fetchone()[0])
PY
}

# Lines worth keeping in the condensed `weekly.latest.log`. Step headers
# from build_db.sh start with "==>"; ingest progress prints "Done." /
# "Checked"; explicit OK/FAIL markers from this script; the start/end
# banners; rebuild-step summary lines that already contain counts.
LATEST_FILTER='^(==>|====|  Run |  Baseline|  Final|  Net:|\[OK\]|\[FAIL\]|Done\.|Checked |Ingested |Loaded |Rule-based |Recurring fallback|Rebuilding |Wrote |Skipped |Compacted DB)'

notify_fail() {
    osascript -e "display notification \"$1\" with title \"PTD weekly FAILED\" sound name \"Basso\"" >/dev/null 2>&1 || true
}

notify_warn() {
    osascript -e "display notification \"$1\" with title \"PTD weekly WARNING\"" >/dev/null 2>&1 || true
}

START_ISO=$(date '+%Y-%m-%dT%H:%M:%S%z')
START_TS=$SECONDS

EVENTS_BEFORE=$(db_count events)
RACES_BEFORE=$(db_count races)
ATHLETES_BEFORE=$(db_count athletes)
RESULTS_BEFORE=$(db_count results)

# Run-stage header is written to both logs so they each open with context.
{
    echo "================================================================"
    echo "  Run started: $START_ISO"
    echo "  Baseline: ${EVENTS_BEFORE} events / ${RACES_BEFORE} races / ${ATHLETES_BEFORE} athletes / ${RESULTS_BEFORE} results"
    echo "================================================================"
} | tee "$LATEST_LOG" "$VERBOSE_LOG" >/dev/null

# Run a pipeline step. Stream stdout/stderr unfiltered into the verbose
# log and a filtered subset into the latest log. Returns the rc of the
# underlying command, not the tee.
#   $1: human label  $2..: command + args
run_step() {
    local label="$1"; shift
    {
        echo ""
        echo "==> ${label}"
    } | tee -a "$LATEST_LOG" "$VERBOSE_LOG" >/dev/null

    # `set -o pipefail` is already on; we want the exit code of the leftmost
    # command (the actual work), not the tee/grep at the right end.
    "$@" 2>&1 \
        | tee -a "$VERBOSE_LOG" \
        | grep --line-buffered -E "$LATEST_FILTER" \
        | tee -a "$LATEST_LOG" >/dev/null
    return ${PIPESTATUS[0]}
}

STATUS=success

run_step "build_db --extend" "$SCRIPT_DIR/build_db.sh" --extend
rc=$?
if [ $rc -ne 0 ]; then
    STATUS="build_db:$rc"
    {
        echo "[FAIL] build_db.sh exited $rc"
    } | tee -a "$LATEST_LOG" "$VERBOSE_LOG" >/dev/null
    notify_fail "build_db.sh exited $rc. tail $LATEST_LOG"
fi

if [ "$STATUS" = "success" ]; then
    run_step "deploy --no-git --no-static" "$SCRIPT_DIR/deploy.sh" --no-git --no-static
    rc=$?
    if [ $rc -ne 0 ]; then
        STATUS="deploy:$rc"
        {
            echo "[FAIL] deploy.sh exited $rc"
        } | tee -a "$LATEST_LOG" "$VERBOSE_LOG" >/dev/null
        notify_fail "deploy.sh exited $rc. tail $LATEST_LOG"
    fi

    # deploy.sh's drift check writes "CODE DRIFT" lines to the verbose log when
    # the live site's prediction code is behind local. A --no-git deploy ships
    # the DB but not the code, so this catches the exact case where the site
    # and the social posts (rendered locally) would silently disagree. Copy the
    # detail into the condensed log and fire a notification - the whole point is
    # that this drift is otherwise invisible.
    if grep -q "CODE DRIFT" "$VERBOSE_LOG"; then
        grep "CODE DRIFT" "$VERBOSE_LOG" | tee -a "$LATEST_LOG" >/dev/null
        notify_warn "Prediction code drift: live site is behind local. Run ./deploy.sh (with git)."
    fi
fi

# Publish pending social posts. A failure here doesn't fail the overall run
# (data ingestion + deploy are the load-bearing pieces) — just logs and
# notifies. The scheduler is idempotent, so anything missed retries next run.
if [ "$STATUS" = "success" ]; then
    run_step "social.scheduler" python -m social.scheduler
    rc=$?
    if [ $rc -ne 0 ]; then
        {
            echo "[WARN] social.scheduler exited $rc"
        } | tee -a "$LATEST_LOG" "$VERBOSE_LOG" >/dev/null
        notify_fail "social.scheduler exited $rc. tail $LATEST_LOG"
    fi
fi

# Pull the week's Search Console rows and refresh growth/gsc_report.md. Runs
# regardless of deploy status - it only reads the GSC API, and a failed deploy
# is exactly a week you still want the search numbers for. Non-fatal: a Google
# API blip must not mark the data pipeline as failed.
run_step "gsc_query_miner" python scripts/gsc_query_miner.py
rc=$?
if [ $rc -ne 0 ]; then
    {
        echo "[WARN] gsc_query_miner.py exited $rc"
    } | tee -a "$LATEST_LOG" "$VERBOSE_LOG" >/dev/null
    notify_warn "gsc_query_miner.py exited $rc. tail $LATEST_LOG"
fi

EVENTS_AFTER=$(db_count events)
RACES_AFTER=$(db_count races)
ATHLETES_AFTER=$(db_count athletes)
RESULTS_AFTER=$(db_count results)

DURATION=$(( SECONDS - START_TS ))
FINISH_ISO=$(date '+%Y-%m-%dT%H:%M:%S%z')

NEW_EVENTS=$(( EVENTS_AFTER - EVENTS_BEFORE ))
NEW_RACES=$(( RACES_AFTER - RACES_BEFORE ))
NEW_ATHLETES=$(( ATHLETES_AFTER - ATHLETES_BEFORE ))
NEW_RESULTS=$(( RESULTS_AFTER - RESULTS_BEFORE ))

# Summary footer goes to both per-run logs so a quick tail tells the
# whole story.
{
    echo ""
    echo "================================================================"
    echo "  Run finished: $FINISH_ISO"
    echo "  Status: $STATUS in ${DURATION}s"
    echo "  Net: ${NEW_EVENTS} events / ${NEW_RACES} races / ${NEW_ATHLETES} athletes / ${NEW_RESULTS} results"
    echo "================================================================"
} | tee -a "$LATEST_LOG" "$VERBOSE_LOG" >/dev/null

# One-row CSV append — easy to load into a sheet for long-term tracking.
echo "${START_ISO},${FINISH_ISO},${DURATION},${STATUS},${NEW_EVENTS},${NEW_RACES},${NEW_ATHLETES},${NEW_RESULTS}" >> "$HISTORY_CSV"

[ "$STATUS" = "success" ] || exit 1
exit 0
