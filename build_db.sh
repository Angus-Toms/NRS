#!/bin/bash
# build_db.sh - full DB population pipeline
#
# Steps (in order):
#   1. ingest       - fetch from World Triathlon API, upsert races/athletes/results
#   2. pto          - scrape stats.protriathletes.org for long-course results
#   3. ignored      - auto-detect subset/oversized races, then apply manual ignored.csv overrides
#   4. stages       - flag combined rows of multi-stage events (heats/semis/A-B finals);
#                     must run after ignored (writes to ignored_races)
#   5. series       - load series.csv, apply rules, apply event_series.csv overrides
#   6. autocorr     - detect mechanical anomalies, write auto-correction rows
#   7. ratings      - load corrections.csv, recompute ELO ratings + rankings
#
# Usage:
#   ./build_db.sh                     # run all steps
#   ./build_db.sh --skip-ingest       # skip WT API fetch (e.g. data already ingested)
#   ./build_db.sh --skip-pto          # skip PTO scrape
#   ./build_db.sh --skip-stages       # skip multi-stage flagging
#   ./build_db.sh --skip-ignored      # skip ignored-race detection
#   ./build_db.sh --skip-series       # skip series membership rebuild
#   ./build_db.sh --skip-autocorr     # skip auto-correction pass
#   ./build_db.sh --skip-ratings      # skip ratings/rankings recompute
#   ./build_db.sh --ratings-only      # shortcut: auto-corr + recompute ratings + rankings

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'; BOLD='\033[1m'
step()    { echo -e "\n${GREEN}${BOLD}==> $*${RESET}"; }
note()    { echo -e "${YELLOW}    $*${RESET}"; }
elapsed() { echo -e "    done in ${BOLD}$(( SECONDS - $1 ))s${RESET}"; }

DO_INGEST=true; DO_PTO=true; DO_STAGES=true; DO_IGNORED=true; DO_SERIES=true; DO_AUTOCORR=true; DO_RATINGS=true

for arg in "$@"; do
    case $arg in
        --skip-ingest)   DO_INGEST=false ;;
        --skip-pto)      DO_PTO=false ;;
        --skip-stages)   DO_STAGES=false ;;
        --skip-ignored)  DO_IGNORED=false ;;
        --skip-series)   DO_SERIES=false ;;
        --skip-autocorr) DO_AUTOCORR=false ;;
        --skip-ratings)  DO_RATINGS=false ;;
        --ratings-only)  DO_INGEST=false; DO_PTO=false; DO_STAGES=false; DO_IGNORED=false; DO_SERIES=false ;;
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

# ── 2. PTO scrape ─────────────────────────────────────────────────────────────
if $DO_PTO; then
    step "PTO - scrape stats.protriathletes.org for long-course results"
    T=$SECONDS
    python3 -m ptd_data.pto_ingest
    elapsed $T
fi

# ── 3. Ignored races ──────────────────────────────────────────────────────────
if $DO_IGNORED; then
    step "Ignored races - auto-detect subsets/oversized + manual ignored.csv"
    T=$SECONDS
    python3 -m ptd_data.ignored
    elapsed $T
fi

# ── 4. Multi-stage flagging ───────────────────────────────────────────────────
# Runs AFTER ignored: stages.py adds stage rows to ignored_races.
if $DO_STAGES; then
    step "Stages - flag multi-round events + ignore their stage rows"
    T=$SECONDS
    python3 -m ptd_data.stages
    elapsed $T
fi

# ── 5. Series membership ──────────────────────────────────────────────────────
if $DO_SERIES; then
    step "Series - load series.csv, apply rules, apply CSV overrides"
    T=$SECONDS
    python3 -m ptd_data.series_rules
    elapsed $T
fi

# ── 6. Auto corrections ───────────────────────────────────────────────────────
if $DO_AUTOCORR; then
    step "Auto-corrections - detect mechanical anomalies, write auto rows"
    T=$SECONDS
    python3 -m ptd_data.auto_corrections
    elapsed $T
fi

# ── 7. Ratings + Rankings ─────────────────────────────────────────────────────
if $DO_RATINGS; then
    step "Ratings + Rankings - load corrections.csv, recompute ELO + rankings"
    T=$SECONDS
    python3 -m ptd_data.ratings
    elapsed $T
fi

echo -e "\n${GREEN}${BOLD}All done in $(( SECONDS - TOTAL_START ))s${RESET}"
