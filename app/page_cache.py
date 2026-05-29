"""
In-process page cache for hot, read-only routes.

Why this exists: scrapers walk /athlete/<id> and /race/<id> across the entire
ID space. Each render fans out to ~30 DuckDB queries; the per-query LRU caches
in queries.py have ~0% hit rate against random IDs and so don't help.
A page-level cache keyed by full path+query collapses each scraped URL to a
single dict lookup after the first hit.
"""

import gzip
import re
import threading
import time
from collections import OrderedDict

from starlette.responses import Response


# Paths we cache. Only canonical id-routes, no query-free landing pages.
# /athlete/<id> and /race/<id>, with optional query string (course, category, etc.).
_CACHEABLE_PATH = re.compile(r"^/(athlete|race)/\d+$")

TTL_SECONDS = 30 * 60       # 30 min - data only changes after a ratings rebuild
MAX_ENTRIES = 1000
MAX_BYTES_PER_ENTRY = 500_000        # uncompressed safety cap
MAX_COMPRESSED_BYTES_PER_ENTRY = 100_000  # ~10x worst-case observed
GZIP_LEVEL = 6


class _PageCache:
    def __init__(self, max_entries=MAX_ENTRIES, ttl=TTL_SECONDS):
        self._max = max_entries
        self._ttl = ttl
        self._store = OrderedDict()  # key -> (expires_at, status, headers, body, media_type)
        self._lock = threading.Lock()

    def get(self, key):
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry[0] < now:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return entry

    def set(self, key, status, headers, body, media_type):
        expires_at = time.monotonic() + self._ttl
        with self._lock:
            self._store[key] = (expires_at, status, headers, body, media_type)
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    def clear(self):
        with self._lock:
            self._store.clear()


page_cache = _PageCache()


def is_cacheable_request(request):
    if request.method != "GET":
        return False
    if not _CACHEABLE_PATH.match(request.url.path):
        return False
    # Don't cache partials (race_page supports ?partial=true for HTMX swaps).
    if request.query_params.get("partial"):
        return False
    return True


def cache_key(request):
    # Include sorted query string so ?a=1&b=2 and ?b=2&a=1 share an entry.
    qs = "&".join(f"{k}={v}" for k, v in sorted(request.query_params.multi_items()))
    return f"{request.url.path}?{qs}" if qs else request.url.path


async def page_cache_middleware(request, call_next):
    if not is_cacheable_request(request):
        return await call_next(request)

    key = cache_key(request)
    hit = page_cache.get(key)
    if hit is not None:
        _, status, headers, gz_body, media_type = hit
        headers = dict(headers)
        headers["X-Page-Cache"] = "HIT"
        body = gzip.decompress(gz_body)
        return Response(content=body, status_code=status, headers=headers, media_type=media_type)

    response = await call_next(request)

    # Only cache successful HTML responses. 404s and errors stay uncached
    # (cheap to produce, and we want them to update if data changes).
    if response.status_code != 200:
        return response

    body_chunks = []
    async for chunk in response.body_iterator:
        body_chunks.append(chunk)
    body = b"".join(body_chunks)

    headers = dict(response.headers)
    # Strip the original content-length; Response will set it fresh from body.
    headers.pop("content-length", None)

    # Store gzipped to keep memory pressure low on small instances; uncompressed
    # check below is the safety limit (skip pages that won't fit even compressed).
    if len(body) <= MAX_BYTES_PER_ENTRY:
        gz_body = gzip.compress(body, compresslevel=GZIP_LEVEL)
        if len(gz_body) <= MAX_COMPRESSED_BYTES_PER_ENTRY:
            page_cache.set(key, response.status_code, headers, gz_body, response.media_type)

    headers["X-Page-Cache"] = "MISS"
    return Response(
        content=body,
        status_code=response.status_code,
        headers=headers,
        media_type=response.media_type,
    )
