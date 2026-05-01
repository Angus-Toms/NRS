#!/bin/bash
# deploy.sh - commit/push, upload static assets to R2, copy DB to Render
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

DO_GIT=true; DO_STATIC=true; DO_DB=true
for arg in "$@"; do
    case $arg in
        --no-git)    DO_GIT=false ;;
        --no-static) DO_STATIC=false ;;
        --no-db)     DO_DB=false ;;
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
        note "Nothing staged - skipping commit."
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
    # Content-Type matters: without it, Cloudflare won't apply Brotli (which
    # is gated on compressible MIME types) and won't cache via the default
    # static-asset rules. Wrangler doesn't auto-detect, so we map by extension.
    # Content-Type matters: without it, Cloudflare won't apply Brotli (which
    # is gated on compressible MIME types) and won't cache via the default
    # static-asset rules. Wrangler doesn't auto-detect, so we map by extension.
    content_type_for() {
        case "$1" in
            *.css)             echo "text/css; charset=utf-8" ;;
            *.js)              echo "application/javascript; charset=utf-8" ;;
            *.json)            echo "application/json; charset=utf-8" ;;
            *.svg)             echo "image/svg+xml" ;;
            *.webp)            echo "image/webp" ;;
            *.png)             echo "image/png" ;;
            *.jpg|*.jpeg)      echo "image/jpeg" ;;
            *.gif)             echo "image/gif" ;;
            *.ico)             echo "image/x-icon" ;;
            *.woff2)           echo "font/woff2" ;;
            *.woff)            echo "font/woff" ;;
            *.ttf)             echo "font/ttf" ;;
            *.txt)             echo "text/plain; charset=utf-8" ;;
            *.xml)             echo "application/xml; charset=utf-8" ;;
            *)                 echo "application/octet-stream" ;;
        esac
    }
    # Cache-Control tells Cloudflare (and browsers) how long to cache the
    # asset. Fonts + images have content-hashed or otherwise-stable URLs so
    # they get the long-lived immutable header. CSS/JS are still served from
    # plain /css/base.css etc. so we use a 1-hour max-age to avoid serving
    # stale styles for too long after a deploy + cache purge.
    cache_control_for() {
        case "$1" in
            *.woff2|*.woff|*.ttf|*.webp|*.png|*.jpg|*.jpeg|*.gif|*.ico|*.svg)
                echo "public, max-age=31536000, immutable" ;;
            *)
                echo "public, max-age=3600" ;;
        esac
    }
    for dir in css js imgs fonts/plus-jakarta-sans; do
        echo "  $dir/"
        for f in "$STATIC_DIR/$dir"/*; do
            [ -f "$f" ] || continue
            key="$dir/$(basename "$f")"
            ct=$(content_type_for "$f")
            cc=$(cache_control_for "$f")
            printf "    %-60s" "$key"
            wrangler r2 object put "$BUCKET/$key" --file "$f" \
                --content-type "$ct" --cache-control "$cc" --remote > /dev/null 2>&1 \
                && echo "ok" || echo "FAILED"
        done
    done
    note "If assets look stale, purge the Cloudflare cache for static.protridata.com."
fi

# ── 3. DB → Render (atomic swap via tmp file) ─────────────────────────────────
if $DO_DB; then
    DB_SIZE=$(du -sh "$DB_LOCAL" | cut -f1)
    step "Render: copying DB ($DB_SIZE)"
    # Upload to a temp path first, then mv - avoids a window where the file is half-written
    scp "$DB_LOCAL" "$RENDER_SSH:${DB_REMOTE}.new"
    ssh "$RENDER_SSH" "mv '${DB_REMOTE}.new' '${DB_REMOTE}'"
    echo "  Copied."
    note "Restart the Render service to pick up the new DB."
fi

step "Done"
