import re

from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()


def _pair_races(races):
    """
    Split races into female/male pairs sorted by normalized prog_name.
    Female goes left, male goes right. Unmatched races get None on the other side.
    """
    def norm(name):
        return re.sub(r'\b(female|male|women|men|woman|man)\b', '', name, flags=re.IGNORECASE).strip()

    female = [r for r in races if r["gender"] == "female"]
    male   = [r for r in races if r["gender"] == "male"]
    other  = [r for r in races if r["gender"] not in ("female", "male")]

    female.sort(key=lambda r: norm(r["prog_name"]))
    male.sort(key=lambda r: norm(r["prog_name"]))

    male_by_norm = {norm(r["prog_name"]): r for r in male}
    seen_male = set()
    pairs = []

    for f in female:
        key = norm(f["prog_name"])
        m = male_by_norm.get(key)
        if m:
            seen_male.add(id(m))
            pairs.append((f, m))
        else:
            pairs.append((f, None))

    for m in male:
        if id(m) not in seen_male:
            pairs.append((None, m))

    for r in other:
        pairs.append((r, None))

    return pairs


@router.get("/event/{event_id}", response_class=HTMLResponse)
async def get_event(request: Request, event_id: int):
    event = queries.get_event_info(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    races = queries.get_event_races_detail(event_id)

    return templates.TemplateResponse("event.html", {
        "request": request,
        "active_page": "races",
        "event": event,
        "race_pairs": _pair_races(races),
    })
