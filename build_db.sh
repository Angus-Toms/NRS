#!/bin/bash
# build_db.sh - full DB population pipeline
#
# Steps (in order):
#   1. ingest       - fetch from World Triathlon API, upsert races/athletes/results
#   2. ignored      - auto-detect subset/oversized races, then apply manual ignored.csv overrides
#   3. series       - populate race_series membership from series_data.py definitions
#   4. ratings      - load corrections.csv, recompute ELO ratings + rankings
#
# Usage:
#   ./build_db.sh                     # run all steps
#   ./build_db.sh --skip-ingest       # skip API fetch (e.g. data already ingested)
#   ./build_db.sh --skip-ignored      # skip ignored-race detection
#   ./build_db.sh --skip-series       # skip series membership rebuild
#   ./build_db.sh --skip-ratings      # skip ratings/rankings recompute
#   ./build_db.sh --ratings-only      # shortcut: only recompute ratings + rankings

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'; BOLD='\033[1m'
step()    { echo -e "\n${GREEN}${BOLD}==> $*${RESET}"; }
note()    { echo -e "${YELLOW}    $*${RESET}"; }
elapsed() { echo -e "    done in ${BOLD}$(( SECONDS - $1 ))s${RESET}"; }

DO_INGEST=true; DO_IGNORED=true; DO_SERIES=true; DO_RATINGS=true

for arg in "$@"; do
    case $arg in
        --skip-ingest)   DO_INGEST=false ;;
        --skip-ignored)  DO_IGNORED=false ;;
        --skip-series)   DO_SERIES=false ;;
        --skip-ratings)  DO_RATINGS=false ;;
        --ratings-only)  DO_INGEST=false; DO_IGNORED=false; DO_SERIES=false ;;
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

# ── 2. Ignored races ──────────────────────────────────────────────────────────
if $DO_IGNORED; then
    step "Ignored races - auto-detect subsets/oversized + manual ignored.csv"
    T=$SECONDS
    python3 -m ptd_data.ignored
    elapsed $T
fi

# ── 3. Series membership ──────────────────────────────────────────────────────
if $DO_SERIES; then
    step "Series - rebuild race_series membership"
    T=$SECONDS
    python3 -m ptd_data.series_data
    elapsed $T
fi

# ── 4. Ratings + Rankings ─────────────────────────────────────────────────────
if $DO_RATINGS; then
    step "Ratings + Rankings - load corrections.csv, recompute ELO + rankings"
    T=$SECONDS
    python3 -m ptd_data.ratings
    elapsed $T
fi

echo -e "\n${GREEN}${BOLD}All done in $(( SECONDS - TOTAL_START ))s${RESET}"
