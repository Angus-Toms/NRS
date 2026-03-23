from pathlib import Path
import json

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL

BASE_DIR = Path(__file__).resolve().parents[2]
QA_PATH = BASE_DIR / "static" / "about" / "qa.json"
BLOG_DIR = BASE_DIR / "static" / "about" / "blogs"

def load_qa_items() -> list[dict]:
    try:
        raw = json.loads(QA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    items = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question", "")).strip()
            answer = str(item.get("answer", "")).strip()
            if question and answer:
                items.append({"question": question, "answer": answer})
    return items


def load_blogs() -> list[dict]:
    posts = []
    for path in sorted(BLOG_DIR.glob("*.html")):
        meta_path = path.with_suffix(".json")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        body = path.read_text(encoding="utf-8")
        # Teaser: first non-tag content up to the first full stop
        import re
        plain = re.sub(r"<[^>]+>", "", body).strip()
        teaser = (plain.split(".")[0] + ".") if plain else "More on this soon."
        posts.append({
            "slug":     path.stem,
            "title":    meta.get("title", path.stem.replace("-", " ").title()),
            "teaser":   meta.get("teaser", teaser),
            "body_html": body,
        })
    return posts


def load_blog_by_slug(slug: str) -> dict | None:
    path = BLOG_DIR / f"{slug}.html"
    meta_path = path.with_suffix(".json")
    if not path.exists() or not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    body = path.read_text(encoding="utf-8")
    return {
        "slug":      slug,
        "title":     meta.get("title", slug.replace("-", " ").title()),
        "body_html": body,
    }


@router.get("/about")
async def about(request: Request):
    context = {
        "request": request,
        "active_page": "about",
        "qa_items": load_qa_items(),
        "blogs": load_blogs(),
    }
    return templates.TemplateResponse("about.html", context)


@router.get("/about/blog/{slug}")
async def blog_detail(request: Request, slug: str):
    post = load_blog_by_slug(slug)

    context = {
        "request": request,
        "active_page": "about",
        "post": post,
    }
    return templates.TemplateResponse("blog_detail.html", context)
