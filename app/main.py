from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import PlainTextResponse

from app.routers import index, athlete_search, race_search, athlete_page, race_page, event_page, leaderboard, comparison, about, robots
from config import RUNTIME_DATA_DIR, STATIC_BASE_URL

BASE_DIR = Path(__file__).resolve().parent.parent # Project root
ALLOWED_HOSTS = {"protridata.com", "www.protridata.com", "127.0.0.1:8000"}

app = FastAPI()
# /static/athlete_imgs must be mounted before /static so it takes precedence in dev.
# In prod the CDN serves athlete images; this mount is a no-op there.
app.mount("/static/athlete_imgs", StaticFiles(directory = RUNTIME_DATA_DIR / "athlete_imgs"), name = "athlete_imgs")
app.mount("/static", StaticFiles(directory = BASE_DIR / "static"), name = "static")
app.mount("/data", StaticFiles(directory = RUNTIME_DATA_DIR), name = "data")
templates = Jinja2Templates(directory = BASE_DIR / "templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL

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
app.include_router(comparison.router)
app.include_router(about.router)
app.include_router(robots.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host = "0.0.0.0", port = 8000)
