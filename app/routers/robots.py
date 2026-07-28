from datetime import date
from functools import lru_cache
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from ptd_data import db

router = APIRouter()

# Google caps sitemaps at 50,000 URLs / 50MB. Use a smaller shard size
# so each file stays well under both limits.
ATHLETES_PER_SITEMAP = 25_000

def _get_conn():
    return db.get_read_cursor()


@lru_cache(maxsize=1)
def _athlete_rows():
    """Indexable athlete IDs ordered by peak rating. Cached for process
    lifetime — the ratings table only changes on rebuild, which restarts the
    app. Only athletes passing queries.ATHLETE_INDEXABLE_SQL are submitted;
    single-result age-groupers are noindex'd thin pages we keep out of the
    sitemap so Google spends its crawl budget on pages that can rank."""
    from ptd_data.queries import ATHLETE_INDEXABLE_SQL
    return _get_conn().execute(f"""
        SELECT a.athlete_id, MAX(r.overall) AS peak_rating
        FROM athletes a
        JOIN ratings r ON a.athlete_id = r.athlete_id
        WHERE a.athlete_id IN ({ATHLETE_INDEXABLE_SQL})
        GROUP BY a.athlete_id
        ORDER BY peak_rating DESC
    """).fetchall()


def _athlete_count() -> int:
    return len(_athlete_rows())


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt(request: Request) -> str:
    base_url = str(request.base_url).rstrip("/")
    return f"""User-agent: *

# Comparison result pages generate too many URL combinations. The deep
# routes 302 to ?a1=/?r1= query URLs on the landing pages, so block those
# query forms too (path-only Disallow doesn't match them).
Disallow: /compare/
Disallow: /athlete-compare/
Disallow: /race-compare/
Disallow: /*?*a1=
Disallow: /*?*r1=

# Course/category mode variants serve different content under a single
# path-only canonical. Crawling them wastes budget and lands them in
# "Crawled - currently not indexed"; the bare canonical paths are in the
# sitemap, so block the parameterised variants and the AJAX partials.
Disallow: /*?*course=
Disallow: /*?*category=
Disallow: /*?*partial=

# Data download endpoints back the table download buttons via JS; they are
# not pages to index.
Disallow: /download/

# Static assets & internal paths
Disallow: /static/
Disallow: /favicon.ico

# Sitemap index
Sitemap: {base_url}/sitemap.xml
"""


def _url(loc: str, lastmod: str | None = None, changefreq: str | None = None,
         priority: float | None = None) -> str:
    parts = [f"  <url>", f"    <loc>{loc}</loc>"]
    if lastmod:
        parts.append(f"    <lastmod>{lastmod}</lastmod>")
    if changefreq:
        parts.append(f"    <changefreq>{changefreq}</changefreq>")
    if priority is not None:
        parts.append(f"    <priority>{priority:.1f}</priority>")
    parts.append("  </url>")
    return "\n".join(parts)


def _wrap_urlset(urls: list[str]) -> str:
    inner = "\n".join(urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{inner}\n"
        "</urlset>\n"
    )


# Cache sitemaps for an hour at the edge so Google's crawler hits Cloudflare
# rather than the origin DB. Stale-while-revalidate keeps responses warm
# without blocking on a slow rebuild.
_SITEMAP_CACHE_HEADERS = {
    "Cache-Control": "public, max-age=3600, s-maxage=3600, stale-while-revalidate=86400"
}


def _xml_response(content: str) -> Response:
    return Response(content=content, media_type="application/xml", headers=_SITEMAP_CACHE_HEADERS)


@router.get("/sitemap.xml")
def sitemap_index(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    today = date.today().isoformat()
    athlete_count = _athlete_count()
    num_athlete_shards = max(1, (athlete_count + ATHLETES_PER_SITEMAP - 1) // ATHLETES_PER_SITEMAP)

    entries = [
        f'  <sitemap><loc>{base}/sitemap-static.xml</loc><lastmod>{today}</lastmod></sitemap>',
        f'  <sitemap><loc>{base}/sitemap-races.xml</loc><lastmod>{today}</lastmod></sitemap>',
        f'  <sitemap><loc>{base}/sitemap-upcoming.xml</loc><lastmod>{today}</lastmod></sitemap>',
        f'  <sitemap><loc>{base}/sitemap-countries.xml</loc><lastmod>{today}</lastmod></sitemap>',
        f'  <sitemap><loc>{base}/sitemap-series.xml</loc><lastmod>{today}</lastmod></sitemap>',
        f'  <sitemap><loc>{base}/sitemap-recurring.xml</loc><lastmod>{today}</lastmod></sitemap>',
    ]
    for i in range(1, num_athlete_shards + 1):
        entries.append(
            f'  <sitemap><loc>{base}/sitemap-athletes-{i}.xml</loc><lastmod>{today}</lastmod></sitemap>'
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries) + "\n"
        '</sitemapindex>\n'
    )
    return _xml_response(xml)


@router.get("/sitemap-static.xml")
def sitemap_static(request: Request) -> Response:
    from app.routers.about import load_blogs
    base = str(request.base_url).rstrip("/")
    today = date.today().isoformat()
    urls = [
        _url(f"{base}/",           today, "daily",   1.0),
        _url(f"{base}/athlete-leaderboard", today, "daily",  0.9),
        _url(f"{base}/race-leaderboard",    today, "daily",  0.8),
        _url(f"{base}/races",       today, "daily",  0.9),
        _url(f"{base}/recent",      today, "daily",  0.8),
        _url(f"{base}/upcoming",    today, "daily",  0.8),
        _url(f"{base}/athletes",    today, "weekly", 0.8),
        _url(f"{base}/countries",   today, "weekly", 0.7),
        _url(f"{base}/series",      today, "weekly", 0.7),
        _url(f"{base}/athlete-compare", today, "monthly", 0.5),
        _url(f"{base}/race-compare",    today, "monthly", 0.5),
        _url(f"{base}/about",       today, "monthly", 0.5),
    ]
    for blog in load_blogs():
        urls.append(_url(f"{base}/about/blog/{blog['slug']}", today, "monthly", 0.5))
    return _xml_response(_wrap_urlset(urls))


@router.get("/sitemap-athletes-{shard}.xml")
def sitemap_athletes(request: Request, shard: int) -> Response:
    base = str(request.base_url).rstrip("/")
    rows = _athlete_rows()
    total = len(rows)
    num_shards = max(1, (total + ATHLETES_PER_SITEMAP - 1) // ATHLETES_PER_SITEMAP)
    if shard < 1 or shard > num_shards:
        raise HTTPException(status_code=404, detail="Sitemap shard out of range")

    start = (shard - 1) * ATHLETES_PER_SITEMAP
    end = min(start + ATHLETES_PER_SITEMAP, total)

    # Priority tiers based on global rank: top 500 → 0.9, top 2000 → 0.8,
    # top 10000 → 0.7, rest → 0.5.
    urls = []
    for idx in range(start, end):
        athlete_id, _ = rows[idx]
        rank = idx + 1
        if rank <= 500:
            priority = 0.9
        elif rank <= 2000:
            priority = 0.8
        elif rank <= 10000:
            priority = 0.7
        else:
            priority = 0.5
        urls.append(_url(f"{base}/athlete/{athlete_id}", changefreq="weekly", priority=priority))

    return _xml_response(_wrap_urlset(urls))


@lru_cache(maxsize=1)
def _country_rows():
    return _get_conn().execute("""
        SELECT n.alpha3, COUNT(a.athlete_id) AS athlete_count
        FROM nationalities n
        LEFT JOIN athletes a ON a.country_full = n.country_full
        GROUP BY n.alpha3
        ORDER BY athlete_count DESC, n.alpha3
    """).fetchall()


@router.get("/sitemap-countries.xml")
def sitemap_countries(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    today = date.today().isoformat()
    rows = _country_rows()

    urls = []
    for alpha3, count in rows:
        # Countries with athletes get higher priority than empty ones.
        priority = 0.8 if count > 50 else 0.7 if count > 0 else 0.4
        urls.append(_url(
            f"{base}/country/{alpha3}",
            lastmod=today,
            changefreq="weekly",
            priority=priority,
        ))

    return _xml_response(_wrap_urlset(urls))


@lru_cache(maxsize=1)
def _race_rows():
    return _get_conn().execute(
        "SELECT race_id, race_date FROM races ORDER BY race_date DESC"
    ).fetchall()


@router.get("/sitemap-races.xml")
def sitemap_races(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    today = date.today()
    # Every race (elite, AG, short and long course) for all time, keyed by race_id.
    rows = _race_rows()

    urls = []
    for race_id, race_date in rows:
        if race_date is None:
            urls.append(_url(f"{base}/race/{race_id}", changefreq="never", priority=0.4))
            continue
        days_ago = (today - race_date).days
        if days_ago <= 30:
            priority, changefreq = 0.9, "weekly"
        elif days_ago <= 365:
            priority, changefreq = 0.8, "monthly"
        elif days_ago <= 365 * 3:
            priority, changefreq = 0.7, "yearly"
        else:
            priority, changefreq = 0.5, "never"
        urls.append(_url(
            f"{base}/race/{race_id}",
            lastmod=race_date.isoformat(),
            changefreq=changefreq,
            priority=priority,
        ))

    return _xml_response(_wrap_urlset(urls))


@lru_cache(maxsize=1)
def _upcoming_race_ids():
    return [r[0] for r in _get_conn().execute(
        "SELECT race_id FROM upcoming_races ORDER BY race_date"
    ).fetchall()]


@router.get("/sitemap-upcoming.xml")
def sitemap_upcoming(request: Request) -> Response:
    """Upcoming races (served at /race/<id> from the upcoming_races table).
    Start-list queries spike in the days before a race, so these need to be
    submitted the moment they exist - waiting for the race to land in the
    races sitemap misses the demand window entirely."""
    base = str(request.base_url).rstrip("/")
    today = date.today().isoformat()
    urls = [_url(f"{base}/race/{rid}", lastmod=today, changefreq="daily", priority=0.9)
            for rid in _upcoming_race_ids()]
    return _xml_response(_wrap_urlset(urls))


@lru_cache(maxsize=1)
def _series_slugs():
    return [r[0] for r in _get_conn().execute(
        "SELECT slug FROM series ORDER BY sort_order, name"
    ).fetchall()]


@router.get("/sitemap-series.xml")
def sitemap_series(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    today = date.today().isoformat()
    urls = [_url(f"{base}/series/{quote(s, safe='')}", lastmod=today, changefreq="weekly", priority=0.7)
            for s in _series_slugs()]
    return _xml_response(_wrap_urlset(urls))


@lru_cache(maxsize=1)
def _recurring_slugs():
    return [r[0] for r in _get_conn().execute("""
        SELECT re.slug
        FROM recurring_events re
        JOIN event_recurring er ON er.recurring_event_id = re.recurring_event_id
        JOIN events e ON e.event_id = er.event_id
        GROUP BY re.recurring_event_id, re.slug
        ORDER BY MAX(e.start_date) DESC
    """).fetchall()]


@router.get("/sitemap-recurring.xml")
def sitemap_recurring(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    today = date.today().isoformat()
    urls = [_url(f"{base}/recurring/{quote(slug, safe='')}", lastmod=today, changefreq="yearly", priority=0.6)
            for slug in _recurring_slugs()]
    return _xml_response(_wrap_urlset(urls))
