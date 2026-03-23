from datetime import date
from urllib.parse import quote
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response
from ptd_data import db

router = APIRouter()


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt(request: Request) -> str:
    base_url = str(request.base_url).rstrip("/")
    return f"""User-agent: *

# Comparison result pages generate too many URL combinations
Disallow: /compare/

# Static assets & internal paths
Disallow: /static/
Disallow: /favicon.ico

# Sitemap index
Sitemap: {base_url}/sitemap.xml
"""


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


@router.get("/sitemap.xml")
async def sitemap_index(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    today = date.today().isoformat()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'  <sitemap><loc>{base}/sitemap-static.xml</loc><lastmod>{today}</lastmod></sitemap>\n'
        f'  <sitemap><loc>{base}/sitemap-athletes.xml</loc><lastmod>{today}</lastmod></sitemap>\n'
        f'  <sitemap><loc>{base}/sitemap-races.xml</loc><lastmod>{today}</lastmod></sitemap>\n'
        '</sitemapindex>\n'
    )
    return Response(content=xml, media_type="application/xml")


@router.get("/sitemap-static.xml")
async def sitemap_static(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    today = date.today().isoformat()
    urls = [
        _url(f"{base}/",           today, "daily",   1.0),
        _url(f"{base}/leaderboard", today, "daily",  0.9),
        _url(f"{base}/races",       today, "daily",  0.9),
        _url(f"{base}/athletes",    today, "weekly", 0.8),
        _url(f"{base}/compare",     today, "monthly", 0.5),
        _url(f"{base}/about",       today, "monthly", 0.5),
    ]
    return Response(content=_wrap_urlset(urls), media_type="application/xml")


@router.get("/sitemap-athletes.xml")
async def sitemap_athletes(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    conn = db.get_conn(read_only=True)
    # All athletes with at least one rating, ordered by overall rating desc.
    # Priority tiers: top 500 → 0.9, top 2000 → 0.8, top 10000 → 0.7, rest → 0.5
    rows = conn.execute("""
        SELECT a.athlete_id, MAX(r.overall) AS peak_rating
        FROM athletes a
        JOIN ratings r ON a.athlete_id = r.athlete_id
        GROUP BY a.athlete_id
        ORDER BY peak_rating DESC
    """).fetchall()
    conn.close()

    urls = []
    for rank, (athlete_id, _) in enumerate(rows, start=1):
        if rank <= 500:
            priority = 0.9
        elif rank <= 2000:
            priority = 0.8
        elif rank <= 10000:
            priority = 0.7
        else:
            priority = 0.5
        urls.append(_url(f"{base}/athlete/{athlete_id}", changefreq="weekly", priority=priority))

    return Response(content=_wrap_urlset(urls), media_type="application/xml")


@router.get("/sitemap-races.xml")
async def sitemap_races(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    today = date.today()
    conn = db.get_conn(read_only=True)
    rows = conn.execute("""
        SELECT DISTINCT race_handle, MAX(race_date) AS race_date
        FROM races
        WHERE category = 'elite'
          AND race_date >= '2000-01-01'
        GROUP BY race_handle
        ORDER BY race_date DESC
    """).fetchall()
    conn.close()

    urls = []
    for handle, race_date in rows:
        if race_date is None:
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
        encoded = quote(handle, safe="")
        urls.append(_url(
            f"{base}/race/{_xml_escape(encoded)}",
            lastmod=race_date.isoformat(),
            changefreq=changefreq,
            priority=priority,
        ))

    return Response(content=_wrap_urlset(urls), media_type="application/xml")
