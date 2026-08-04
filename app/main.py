import os
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.routers import index, athlete_search, race_search, athlete_page, race_page, event_page, leaderboard, race_leaderboard, comparison, race_comparison, about, robots, series_page, country_page, upcoming_page, download
from config import RUNTIME_DATA_DIR, STATIC_BASE_URL, ASSET_VERSION
from app.display_helpers import flag, program_label, title_words

BASE_DIR = Path(__file__).resolve().parent.parent # Project root
ALLOWED_HOSTS = {"protridata.com", "www.protridata.com", "127.0.0.1:8000"}

# Sync handlers run in the threadpool, so this is the ceiling on concurrent
# DuckDB queries + Jinja renders. Starlette defaults to 40, which OOMs a small
# instance under crawler load (84k athlete pages, mostly edge-cache misses).
# Env-tunable so it can be adjusted on the Render dashboard without a redeploy.
THREADPOOL_LIMIT = int(os.getenv("THREADPOOL_LIMIT", "4"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    anyio.to_thread.current_default_thread_limiter().total_tokens = THREADPOOL_LIMIT
    yield


app = FastAPI(lifespan=lifespan)

# Long-cache flag SVGs: filenames are alpha3-stable, content effectively immutable.
@app.middleware("http")
async def flag_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/flags/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response

# Edge-cache everything the DB serves: content only changes on rebuild, which
# redeploys the app (purge the Cloudflare cache on deploy). max-age=0 keeps
# browsers revalidating against the edge so a purge takes effect immediately.
# X-Partial requests are skipped: the compare partials share their URL with a
# 302 for direct navigation, and Cloudflare keys its cache on URL alone, so
# caching either variant would serve the wrong response to the other caller.
# NOTE: if user accounts ever land, per-user pages must opt out of this.
@app.middleware("http")
async def edge_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if (
        request.method == "GET"
        and response.status_code == 200
        and "cache-control" not in response.headers
        and not request.headers.get("X-Partial")
    ):
        # Athlete pages only change on the weekly DB deploy but are the
        # bulk of Googlebot traffic (50k+ URLs, so almost every crawl is an
        # edge miss at 1h TTL and pays ~1.2s origin latency). A day of
        # staleness after a deploy is fine; the longer TTL turns crawl
        # misses into edge hits. Historical race pages get an even longer
        # TTL set in their route (a cache-control header here means the
        # route already chose one, and this middleware leaves it alone).
        s_maxage = 86400 if request.url.path.startswith("/athlete/") else 3600
        response.headers["Cache-Control"] = f"public, max-age=0, s-maxage={s_maxage}, stale-while-revalidate=86400"
    return response

# /static/athlete_imgs must be mounted before /static so it takes precedence in dev.
# In prod the CDN serves athlete images; this mount is a no-op there.
athlete_imgs_dir = RUNTIME_DATA_DIR / "athlete_imgs"
athlete_imgs_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/athlete_imgs", StaticFiles(directory=athlete_imgs_dir), name="athlete_imgs")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
RUNTIME_DATA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/data", StaticFiles(directory=RUNTIME_DATA_DIR), name="data")
templates = Jinja2Templates(directory = BASE_DIR / "templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"]   = ASSET_VERSION

templates.env.globals["flag"] = flag
templates.env.globals["program_label"] = program_label
templates.env.globals["title_words"] = title_words

# @app.middleware("http")
# async def enforce_host(request: Request, call_next):
#     host = request.headers.get("host", "").split(":")[0].lower()
#     if host not in ALLOWED_HOSTS:
#         return PlainTextResponse("Forbidden", status_code=403)
#     return await call_next(request)

# Render HTTP errors with a shared template
@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if exc.detail else exc.__class__.__name__
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": exc.status_code,
            "detail": str(detail),
            "active_page": None,
        },
        status_code = exc.status_code,
    )

# URLs that fail path/query validation (e.g. legacy name-based /race/<name>
# URLs where an int is expected) are bad URLs, not bad requests: serve the 404
# page instead of FastAPI's default 422 JSON. Google retries "other 4xx"
# statuses far longer than 404s, so 2.6k legacy URLs were stuck in Search
# Console's "Blocked due to other 4xx issue" bucket.
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": 404,
            "detail": "Page not found",
            "active_page": None,
        },
        status_code = 404,
    )

# Render unexpected errors with the same template
@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": 500,
            "detail": str(exc),
            "active_page": None,
        },
        status_code = 500,
    )

# Include page routers
app.include_router(index.router)
app.include_router(athlete_search.router)
app.include_router(race_search.router)
app.include_router(athlete_page.router)
app.include_router(race_page.router)
app.include_router(event_page.router)
app.include_router(leaderboard.router)
app.include_router(race_leaderboard.router)
app.include_router(comparison.router)
app.include_router(race_comparison.router)
app.include_router(about.router)
app.include_router(robots.router)
app.include_router(series_page.router)
app.include_router(country_page.router)
app.include_router(upcoming_page.router)
app.include_router(download.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host = "0.0.0.0", port = 8000)
