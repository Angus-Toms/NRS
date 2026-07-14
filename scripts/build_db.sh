#!/bin/bash
# build_db.sh - full DB population pipeline
#
# Steps (in order):
#   1. ingest       - fetch from World Triathlon API, upsert races/athletes/results
#   2. startlist    - fetch upcoming events + start lists for /upcoming page
#   3. pto          - scrape stats.protriathletes.org for long-course results
#   4. merges       - apply manual athlete merges from data/athlete_merges.csv
#   5. ignored      - auto-detect subset/oversized races, then apply manual ignored.csv overrides
#   6. stages       - flag combined rows of multi-stage events (heats/semis/A-B finals);
#                     must run after ignored (writes to ignored_races)
#   7. series       - load series.csv, apply rules, apply event_series.csv overrides
#   8. recurring    - fuzzy-name fallback recurring-event detection for events
#                     uncaught by series rules
#   9. autocorr     - detect mechanical anomalies, write auto-correction rows
#  10. ratings      - load corrections.csv, recompute ELO ratings + rankings
#  11. compact      - rewrite the DB file to reclaim space from wiped/recomputed tables
#
# Usage:
#   ./build_db.sh                     # run all steps
#   ./build_db.sh --skip-ingest       # skip WT API fetch (e.g. data already ingested)
#   ./build_db.sh --skip-startlist    # skip upcoming-race/start-list fetch
#   ./build_db.sh --skip-pto          # skip PTO scrape
#   ./build_db.sh --skip-merges       # skip manual athlete merges
#   ./build_db.sh --skip-stages       # skip multi-stage flagging
#   ./build_db.sh --skip-ignored      # skip ignored-race detection
#   ./build_db.sh --skip-series       # skip series membership rebuild
#   ./build_db.sh --skip-recurring    # skip fuzzy-name recurring fallback
#   ./build_db.sh --skip-autocorr     # skip auto-correction pass
#   ./build_db.sh --skip-ratings      # skip ratings/rankings recompute
#   ./build_db.sh --skip-compact      # skip post-build DB compaction
#   ./build_db.sh --ratings-only      # shortcut: auto-corr + recompute ratings + rankings
#   ./build_db.sh --extend            # ratings/rankings: wipe from earliest new race date
#                                       forward and rebuild from there, instead of full
#                                       clear+recompute. Combine with --ratings-only for
#                                       a fast post-ingest top-up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'; BOLD='\033[1m'
step()    { echo -e "\n${GREEN}${BOLD}==> $*${RESET}"; }
note()    { echo -e "${YELLOW}    $*${RESET}"; }
elapsed() { echo -e "    done in ${BOLD}$(( SECONDS - $1 ))s${RESET}"; }

DO_INGEST=true; DO_STARTLIST=true; DO_PTO=true; DO_MERGES=true; DO_STAGES=true; DO_IGNORED=true; DO_SERIES=true; DO_RECURRING=true; DO_AUTOCORR=true; DO_RATINGS=true; DO_COMPACT=true
EXTEND=false

for arg in "$@"; do
    case $arg in
        --skip-ingest)    DO_INGEST=false ;;
        --skip-startlist) DO_STARTLIST=false ;;
        --skip-pto)       DO_PTO=false ;;
        --skip-merges)    DO_MERGES=false ;;
        --skip-stages)    DO_STAGES=false ;;
        --skip-ignored)   DO_IGNORED=false ;;
        --skip-series)    DO_SERIES=false ;;
        --skip-recurring) DO_RECURRING=false ;;
        --skip-autocorr)  DO_AUTOCORR=false ;;
        --skip-ratings)   DO_RATINGS=false ;;
        --skip-compact)   DO_COMPACT=false ;;
        --ratings-only)   DO_INGEST=false; DO_STARTLIST=false; DO_PTO=false; DO_MERGES=false; DO_STAGES=false; DO_IGNORED=false; DO_SERIES=false; DO_RECURRING=false ;;
        --extend)         EXTEND=true ;;
    esac
done

TOTAL_START=$SECONDS

# ── 1. Ingest ─────────────────────────────────────────────────────────────────
if $DO_INGEST; then
    step "Ingest - fetch from World Triathlon API"
    T=$SECONDS
    python3 -m ptd_data.ingest
    elapsed $T
fi

# ── 2. Start lists ────────────────────────────────────────────────────────────
# Fetches upcoming events and their start lists for the /upcoming page.
# Must run after ingest so _purge_completed() can drop upcoming rows whose
# races have since been ingested as completed.
if $DO_STARTLIST; then
    step "Start lists - fetch upcoming events (next 90 days) + entries"
    T=$SECONDS
    python3 -m ptd_data.ingest --start-lists
    elapsed $T
fi

# ── 3. PTO scrape ─────────────────────────────────────────────────────────────
if $DO_PTO; then
    if $EXTEND; then
        step "PTO - scrape stats.protriathletes.org (current year only)"
    else
        step "PTO - scrape stats.protriathletes.org for long-course results"
    fi
    T=$SECONDS
    if $EXTEND; then
        python3 -m ptd_data.pto_ingest --recent 1
    else
        python3 -m ptd_data.pto_ingest
    fi
    elapsed $T
fi

# ── 4. Manual athlete merges ──────────────────────────────────────────────────
# Collapse duplicate rows the auto-matchers couldn't bridge. Runs after PTO so
# any new merge entries land before downstream rating/series passes.
if $DO_MERGES; then
    step "Merges - apply data/athlete_merges.csv"
    T=$SECONDS
    python3 -c "from ptd_data import db; conn = db.get_conn(read_only=False); db.apply_athlete_merges(conn); conn.close()"
    elapsed $T
fi

# ── 5. Ignored races ──────────────────────────────────────────────────────────
if $DO_IGNORED; then
    step "Ignored races - auto-detect subsets/oversized + manual ignored.csv"
    T=$SECONDS
    python3 -m ptd_data.ignored
    elapsed $T
fi

# ── 7. Multi-stage flagging ───────────────────────────────────────────────────
# Runs AFTER ignored: stages.py adds stage rows to ignored_races.
if $DO_STAGES; then
    step "Stages - flag multi-round events + ignore their stage rows"
    T=$SECONDS
    python3 -m ptd_data.stages
    elapsed $T
fi

# ── 8. Series membership ──────────────────────────────────────────────────────
if $DO_SERIES; then
    step "Series - load series.csv, apply rules, apply CSV overrides"
    T=$SECONDS
    python3 -m ptd_data.series_rules
    elapsed $T
fi

# ── 8b. Recurring fallback ────────────────────────────────────────────────────
if $DO_RECURRING; then
    step "Recurring fallback - fuzzy-cluster orphan events into recurring groups"
    T=$SECONDS
    python3 -m ptd_data.recurring_events
    elapsed $T
fi

# ── 9. Auto corrections ───────────────────────────────────────────────────────
if $DO_AUTOCORR; then
    step "Auto-corrections - detect mechanical anomalies, write auto rows"
    T=$SECONDS
    python3 -m ptd_data.auto_corrections
    elapsed $T
fi

# ── 10. Ratings + Rankings ────────────────────────────────────────────────────
if $DO_RATINGS; then
    if $EXTEND; then
        step "Ratings + Rankings - extend from earliest new race date"
    else
        step "Ratings + Rankings - load corrections.csv, recompute ELO + rankings"
    fi
    T=$SECONDS
    if $EXTEND; then
        python3 -m ptd_data.ratings --extend
    else
        python3 -m ptd_data.ratings
    fi
    elapsed $T
fi

# ── 11. Compact ────────────────────────────────────────────────────────────────
# Ratings/rankings/form are wiped and recomputed every run above; DuckDB never
# shrinks the file in place, so the stranded blocks from the old versions just
# accumulate. Rewrite into a fresh file so the copy to Render stays small.
if $DO_COMPACT; then
    step "Compact - rewrite DB to reclaim space from wiped/recomputed tables"
    T=$SECONDS
    python3 -m ptd_data.compact
    elapsed $T
fi

echo -e "\n${GREEN}${BOLD}All done in $(( SECONDS - TOTAL_START ))s${RESET}"
