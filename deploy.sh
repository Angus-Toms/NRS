#!/bin/bash
# deploy.sh — commit/push, upload static assets to R2, copy DB to Render
#
# Usage:
#   ./deploy.sh                   # run all three steps
#   ./deploy.sh --no-git          # skip git step
#   ./deploy.sh --no-static       # skip Cloudflare R2 upload
#   ./deploy.sh --no-db           # skip DB copy
#
# Requires:
#   - wrangler (npm i -g wrangler) logged in

set -euo pipefail

# ── Config (values pulled from config.py) ────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATIC_DIR="$SCRIPT_DIR/static"
DB_LOCAL="$SCRIPT_DIR/ptd_data/ptd.duckdb"
_py() { python3 -c "import sys; sys.path.insert(0,'$SCRIPT_DIR'); from config import $1; print($1)"; }
BUCKET=$(    _py CF_BUCKET)
RENDER_SSH=$(  _py RENDER_SSH)
DB_REMOTE=$(   _py RENDER_DB)
# ─────────────────────────────────────────────────────────────────────────────

DO_GIT=true; DO_STATIC=true; DO_DB=true; DO_WORKER=true
for arg in "$@"; do
    case $arg in
        --no-git)    DO_GIT=false ;;
        --no-static) DO_STATIC=false ;;
        --no-db)     DO_DB=false ;;
        --no-worker) DO_WORKER=false ;;
    esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
step() { echo -e "\n${GREEN}==> $*${RESET}"; }
note() { echo -e "${YELLOW}    $*${RESET}"; }

cd "$SCRIPT_DIR"

# ── 1. Git commit + push ──────────────────────────────────────────────────────
if $DO_GIT; then
    step "Git"
    git add -A
    if git diff --cached --quiet; then
        note "Nothing staged — skipping commit."
    else
        git diff --cached --stat
        echo ""
        read -rp "  Commit message: " msg
        git commit -m "$msg"
    fi

    # Push if there are unpushed commits
    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse "@{u}" 2>/dev/null || echo "")
    if [ "$LOCAL" != "$REMOTE" ]; then
        git push
        echo "  Pushed."
    else
        note "Already up to date with remote."
    fi
fi

# ── 2. Static assets → Cloudflare R2 ─────────────────────────────────────────
if $DO_STATIC; then
    step "Cloudflare R2: uploading static assets"
    for dir in css js imgs; do
        echo "  $dir/"
        for f in "$STATIC_DIR/$dir"/*; do
            [ -f "$f" ] || continue
            key="$dir/$(basename "$f")"
            printf "    %-40s" "$key"
            wrangler r2 object put "$BUCKET/$key" --file "$f" --remote > /dev/null 2>&1 \
                && echo "ok" || echo "FAILED"
        done
    done
    note "If assets look stale, purge the Cloudflare cache for static.protridata.com."
fi

# ── 3. Cloudflare Worker ──────────────────────────────────────────────────────
if $DO_WORKER; then
    step "Cloudflare Worker: deploying ptd-static"
    (cd "$SCRIPT_DIR/cf-worker" && wrangler deploy)
fi

# ── 3. DB → Render (atomic swap via tmp file) ─────────────────────────────────
if $DO_DB; then
    DB_SIZE=$(du -sh "$DB_LOCAL" | cut -f1)
    step "Render: copying DB ($DB_SIZE)"
    # Upload to a temp path first, then mv — avoids a window where the file is half-written
    scp "$DB_LOCAL" "$RENDER_SSH:${DB_REMOTE}.new"
    ssh "$RENDER_SSH" "mv '${DB_REMOTE}.new' '${DB_REMOTE}'"
    echo "  Copied."
    note "Restart the Render service to pick up the new DB."
fi

step "Done"
