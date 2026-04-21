from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import STATIC_BASE_URL
from ptd_data import queries
from ptd_data.queries import _location_from_name
from app.routers.router_utils import format_time, format_time_behind

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()

_DISCS = ["overall", "swim", "bike", "run", "transition"]

# Human labels for program tab bar
_SUB_LABELS = {"elite": "Elite", "u23": "U23", "junior": "Junior", "youth": "Youth", "ag": "AG"}
_GENDER_LABELS = {"male": "Men", "female": "Women", "mixed": "Mixed"}

# Tier order + labels for the index page groupings
_TIER_ORDER = [
    "championship", "olympic-games", "wtcs", "world-cup",
    "commonwealth-games", "fisu-games", "continental-cup",
    "ag-championship", "custom",
]
_TIER_LABELS = {
    "championship":        "World Championships",
    "olympic-games":       "Olympic Games",
    "commonwealth-games":  "Commonwealth Games",
    "fisu-games":          "World University Championships",
    "wtcs":                "WTCS",
    "world-cup":           "World Cup",
    "continental-cup":     "Continental Cups",
    "ag-championship":     "Age-Group World Championships",
    "custom":              "Other",
}


def _classify(val, thresholds):
    if val is None:
        return "beginner"
    t = thresholds["overall"]
    if val >= t["p95"]: return "expert"
    if val >= t["p85"]: return "advanced"
    if val >= t["p60"]: return "intermediate"
    if val >= t["p30"]: return "novice"
    return "beginner"


def _program_label(sub, gender):
    return f"{_SUB_LABELS.get(sub, sub.title())} {_GENDER_LABELS.get(gender, gender.title())}"


_GENDER_SLUG = {"male": "men", "female": "women", "mixed": "mixed"}


def _program_slug(sub, gender):
    return f"{sub}-{_GENDER_SLUG.get(gender, gender)}"


def _parse_program_slug(slug):
    """'elite-men' -> ('elite','male'). Returns None if malformed."""
    if not slug or "-" not in slug:
        return None
    sub, _, g = slug.rpartition("-")
    gender = {"men": "male", "women": "female", "mixed": "mixed"}.get(g)
    if not gender or sub not in _SUB_LABELS:
        return None
    return (sub, gender)


@router.get("/series", response_class=HTMLResponse)
async def series_index(request: Request):
    series_list = queries.get_all_series()
    highlights  = queries.get_series_index_highlights([s["series_id"] for s in series_list])

    for s in series_list:
        h = highlights.get(s["series_id"], {})
        s["editions"]   = h.get("editions") or []
        s["top_male"]   = h.get("top_male") or []
        s["top_female"] = h.get("top_female") or []
        for ed in s["editions"]:
            for key in ("male_podium", "female_podium"):
                podium = ed[key]
                winner_s = podium[0]["overall_s"] if podium and podium[0].get("overall_s") else None
                for p in podium:
                    p["time_fmt"] = format_time(p["overall_s"]) if p.get("overall_s") else ""
                    if winner_s and p["position"] != 1 and p.get("overall_s") is not None:
                        diff = p["overall_s"] - winner_s
                        p["gap_fmt"] = f"+{format_time(diff)}" if diff > 0 else "+0:00"
                    else:
                        p["gap_fmt"] = ""

        # Short edition labels for top athletes ("Yokohama '24").
        for leader_list in (s["top_male"], s["top_female"]):
            for leader in leader_list:
                for ed in leader["editions"]:
                    venue = (ed.get("venue") or "").strip() or _location_from_name(ed.get("event_name", ""))
                    yr = ed["race_date"].year % 100 if ed.get("race_date") else None
                    ed["short"] = f"{venue} '{yr:02d}" if yr is not None else venue

    groups = {t: [] for t in _TIER_ORDER}
    for s in series_list:
        tier = s.get("tier") or "custom"
        groups.setdefault(tier, []).append(s)

    tier_blocks = [
        {"tier": t, "label": _TIER_LABELS.get(t, t.title()), "series": groups[t]}
        for t in _TIER_ORDER if groups.get(t)
    ]

    return templates.TemplateResponse("series_index.html", {
        "request":     request,
        "active_page": "races",
        "tier_blocks": tier_blocks,
    })


@router.get("/series/{slug}", response_class=HTMLResponse)
async def series_detail(request: Request, slug: str, program: str | None = None):
    series = queries.get_series_by_slug(slug)
    if not series:
        raise HTTPException(status_code=404)

    sid = series["series_id"]

    # Resolve program: from query param if valid, else default to first available
    program_options = queries.get_program_options_for_series(sid)
    available = {(o["sub_category"], o["gender"]) for o in program_options}

    prog = _parse_program_slug(program) if program else None
    if prog and prog not in available:
        prog = None
    if prog is None and program_options:
        # Prefer elite-male, else first
        prog = ("elite", "male") if ("elite", "male") in available else (
            program_options[0]["sub_category"], program_options[0]["gender"])

    # Attach UI metadata to tabs
    active_slug = _program_slug(*prog) if prog else None
    program_tabs = [
        {
            "slug":   _program_slug(o["sub_category"], o["gender"]),
            "label":  _program_label(o["sub_category"], o["gender"]),
            "count":  o["count"],
            "active": _program_slug(o["sub_category"], o["gender"]) == active_slug,
        }
        for o in program_options
    ]

    races            = queries.get_series_races(sid, program=prog)
    leaders          = queries.get_series_all_time_leaders(sid, program=prog)
    perf_history     = queries.get_series_performance_history(sid, program=prog)
    recurring_groups = queries.get_recurring_groups_for_series(sid, program=prog)

    # Thresholds keyed by gender (for race-standard classification)
    thresholds_by_gender = {
        g: queries.get_race_standard_thresholds(g)
        for g in {r["gender"] for r in races if r.get("gender")}
    }

    def _fmt_split(v):
        return format_time(v) if v and v > 0 else ""

    for race in races:
        for p in race["podium"]:
            p["time_fmt"] = format_time(p["overall_s"])
            gap = p.get("gap")
            if gap is None:
                p["gap_fmt"] = ""
            elif gap == 0:
                p["gap_fmt"] = "+0:00"
            else:
                p["gap_fmt"] = format_time_behind(gap)
            p["swim_fmt"] = _fmt_split(p.get("swim_s"))
            p["t1_fmt"]   = _fmt_split(p.get("t1_s"))
            p["bike_fmt"] = _fmt_split(p.get("bike_s"))
            p["t2_fmt"]   = _fmt_split(p.get("t2_s"))
            p["run_fmt"]  = _fmt_split(p.get("run_s"))

        raw = race.pop("standards_raw", None)
        thresh = thresholds_by_gender.get(race.get("gender"))
        if raw and thresh:
            race["standards"]        = {d: round(raw[d]) for d in _DISCS}
            race["standard_classes"] = {d: _classify(raw[d], thresh) for d in _DISCS}
        else:
            race["standards"]        = None
            race["standard_classes"] = None

    # Map pins - races with non-zero coordinates
    map_locations = [
        {
            "lat":     r["latitude"],
            "lng":     r["longitude"],
            "label":   r["event_name"],
            "year":    r["race_date"].year,
            "race_id": r["race_id"],
        }
        for r in races
        if r.get("latitude") and r.get("longitude")
           and abs(r["latitude"]) > 0.001 and abs(r["longitude"]) > 0.001
    ]

    for row in perf_history:
        row["year"] = row["race_date"].year
        row["race_date"] = row["race_date"].isoformat()

    active_program_label = _program_label(*prog) if prog else ""

    # Stats scoped to the active program
    years = {r["race_date"].year for r in races if r.get("race_date")}
    unique_winners = {
        p["athlete_id"]
        for r in races
        for p in r["podium"]
        if p["position"] == 1
    }
    year_span = ""
    if years:
        y0, y1 = min(years), max(years)
        year_span = f"{y0}" if y0 == y1 else f"{y0}–{y1}"

    hero_stats = {
        "editions":       len(races),
        "unique_winners": len(unique_winners),
        "year_span":      year_span,
    }

    return templates.TemplateResponse("series.html", {
        "request":              request,
        "active_page":          "races",
        "series":               series,
        "races":                races,
        "leaders":              leaders,
        "perf_history":         perf_history,
        "map_locations":        map_locations,
        "program_tabs":         program_tabs,
        "active_program":       active_slug,
        "active_program_label": active_program_label,
        "hero_stats":           hero_stats,
        "recurring_groups":     recurring_groups,
    })
