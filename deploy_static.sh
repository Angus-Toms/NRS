#!/bin/bash
set -e

BUCKET="ptd-static-assets"
BASE="/Users/mungo/Personal/PTD/static"

echo "Uploading CSS..."
for f in "$BASE"/css/*.css; do
    key="css/$(basename $f)"
    echo "  $key"
    wrangler r2 object put "$BUCKET/$key" --file "$f" --remote
done

echo "Uploading JS..."
for f in "$BASE"/js/*.js; do
    key="js/$(basename $f)"
    echo "  $key"
    wrangler r2 object put "$BUCKET/$key" --file "$f" --remote
done

echo "Done. Remember to purge Cloudflare cache if needed."
