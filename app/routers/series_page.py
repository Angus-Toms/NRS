from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from config import ASSET_VERSION, STATIC_BASE_URL
from ptd_data import queries
from app.routers.router_utils import format_time, format_time_behind

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION
router = APIRouter()

_DISCS = ["overall", "swim", "bike", "run", "transition"]

# Human labels for program tab bar
_SUB_LABELS = {"elite": "Elite", "u23": "U23", "junior": "Junior", "youth": "Youth", "ag": "AG"}
_GENDER_LABELS = {"male": "Men", "female": "Women", "mixed": "Mixed"}

# Tier order + labels for the index page groupings
_TIER_ORDER = [
    "olympic-games", "championship", "im-worlds", "im-703-worlds", "t100",
    "wtcs", "world-cup", "commonwealth-games", "fisu-games",
    "continental-championship", "continental-cup", "ag-championship", "custom",
]
_TIER_LABELS = {
    "olympic-games":            "Olympic Games",
    "championship":             "World Championships",
    "im-worlds":                "Ironman World Championships",
    "im-703-worlds":            "Ironman 70.3 World Championships",
    "t100":                     "T100 Triathlon World Tour",
    "wtcs":                     "WTCS",
    "world-cup":                "World Cup",
    "commonwealth-games":       "Commonwealth Games",
    "fisu-games":               "World University Championships",
    "continental-championship": "Continental Championships",
    "continental-cup":          "Continental Cups",
    "ag-championship":          "Age-Group World Championships",
    "custom":                   "Other",
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


def _races_to_json(races):
    """Strip the SQL-shaped race rows down to a JSON-safe shape for the
    page's JS. Keeps just what the template needs (podium splits, gaps,
    standards, venue/country) and converts dates to ISO strings."""
    out = []
    for r in races:
        rdate = r.get("race_date")
        out.append({
            "race_id":        r["race_id"],
            "race_date":      rdate.isoformat() if rdate else None,
            "year":           rdate.year if rdate else None,
            "race_handle":    r.get("race_handle"),
            "event_name":     r.get("event_name"),
            "race_title":     r.get("race_title"),
            "venue":          r.get("venue"),
            "country":        r.get("country"),
            "is_multi_stage": r.get("is_multi_stage"),
            "podium": [
                {
                    "position":      p["position"],
                    "athlete_id":    p["athlete_id"],
                    "name":          p["name"],
                    "country_emoji": p["country_emoji"],
                    "profile_img":   bool(p.get("profile_img")),
                    "overall_s":     p.get("overall_s"),
                    "swim_s":        p.get("swim_s"),
                    "t1_s":          p.get("t1_s"),
                    "bike_s":        p.get("bike_s"),
                    "t2_s":          p.get("t2_s"),
                    "run_s":         p.get("run_s"),
                    "gap":           p.get("gap"),
                    "time_fmt":      p.get("time_fmt"),
                    "swim_fmt":      p.get("swim_fmt"),
                    "t1_fmt":        p.get("t1_fmt"),
                    "bike_fmt":      p.get("bike_fmt"),
                    "t2_fmt":        p.get("t2_fmt"),
                    "run_fmt":       p.get("run_fmt"),
                    "gap_fmt":       p.get("gap_fmt"),
                }
                for p in r.get("podium", [])
            ],
            "standards":        r.get("standards"),
            "standard_classes": r.get("standard_classes"),
        })
    return out


def _resolve_program(program_options, program_slug):
    """Pick the active program tuple (sub, gender, prog_name) from the request
    slug + the available options. Defaults:

      - prefer Elite Men, then any non-AG with male gender
      - for AG-only series, pick the densest popular age band (25-39 male
        standard distance, then 40-44, then any AG male)
      - fall back to the first option
    """
    if not program_options:
        return None
    if program_slug:
        for o in program_options:
            if _program_slug_for_option(o) == program_slug:
                return (o["sub_category"], o["gender"], o.get("prog_name"))
    for sub in ("elite", "u23", "junior", "youth"):
        for o in program_options:
            if o["sub_category"] == sub and o["gender"] == "male":
                return (sub, "male", None)
    # AG fallback: pick the densest male program in the popular 25-44 range,
    # standard distance preferred. Same ranking as `_build_program_tabs` so
    # the resolved default matches the male pinned tab.
    ag_male = [o for o in program_options
               if o["sub_category"] == "ag" and o["gender"] == "male"]
    if ag_male:
        ag_male.sort(key=_ag_program_rank)
        o = ag_male[0]
        return ("ag", "male", o.get("prog_name"))
    o = program_options[0]
    return (o["sub_category"], o["gender"], o.get("prog_name"))


_AG_PREFERRED_BANDS = {"25-29", "30-34", "35-39", "40-44"}


def _ag_program_rank(o):
    """Sort key for AG programs: in-popular-band first, standard before
    sprint, then by count desc. Lower is better."""
    pn = o.get("prog_name") or ""
    band = pn.split(" ", 1)[0] if pn else ""
    return (band not in _AG_PREFERRED_BANDS, "Sprint" in pn, -o.get("count", 0))


def _build_program_tabs(program_options, active_slug):
    """Returns (pinned, overflow). For non-AG series, all programs are
    pinned. For AG series we pin the densest male + densest female age band
    (preferring 25-44 standard distance) and put the rest in overflow."""
    tabs = [
        {
            "slug":       _program_slug_for_option(o),
            "label":      _program_label_for_option(o),
            "count":      o["count"],
            "sub":        o["sub_category"],
            "gender":     o["gender"],
            "prog_name":  o.get("prog_name"),
            "active":     _program_slug_for_option(o) == active_slug,
        }
        for o in program_options
    ]
    if not any(t["sub"] == "ag" for t in tabs):
        return tabs, []
    pinned = []
    for gender in ("male", "female"):
        candidates = sorted(
            [t for t in tabs if t["sub"] == "ag" and t["gender"] == gender],
            key=lambda t: _ag_program_rank({"prog_name": t["prog_name"], "count": t["count"]}),
        )
        if candidates:
            pinned.append(candidates[0])
    # Always pin whatever is currently active so the user sees their selection.
    active = next((t for t in tabs if t["active"]), None)
    if active and active not in pinned:
        pinned.append(active)
    overflow = [t for t in tabs if t not in pinned]
    return pinned, overflow


def _build_program_payload(races, leaders, perf_history, medal_table,
                            winners_age, standards_hist, prog):
    """Shared post-processing for /series/{slug} and /recurring/{slug}.
    Mutates `races` and `perf_history` in place (date stringification,
    formatted splits) and returns the JSON-friendly payload that both the
    Jinja template and the JSON endpoints consume."""
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
            race["standards"]        = {d: (round(raw[d]) if raw.get(d) is not None else None) for d in _DISCS}
            race["standard_classes"] = {d: _classify(raw[d], thresh) for d in _DISCS}
        else:
            race["standards"]        = None
            race["standard_classes"] = None

    for row in perf_history:
        row["year"]      = row["race_date"].year
        row["race_date"] = row["race_date"].isoformat()

    years = {r["race_date"].year for r in races if r.get("race_date")}
    year_span = ""
    if years:
        y0, y1 = min(years), max(years)
        year_span = f"{y0}" if y0 == y1 else f"{y0}-{y1}"

    youngest = sorted(winners_age, key=lambda w: (w["age"],  -w["race_date"].toordinal()))[:5]
    oldest   = sorted(winners_age, key=lambda w: (-w["age"], -w["race_date"].toordinal()))[:5]

    return {
        "active_program":       _program_slug(*prog) if prog else None,
        "active_program_label": _program_label(*prog) if prog else "",
        "hero_stats":           {"editions": len(races), "year_span": year_span},
        "races_json":           _races_to_json(races),
        "leaders":              leaders,
        "medal_table":          medal_table,
        "youngest_winners":     _winners_age_to_json(youngest),
        "oldest_winners":       _winners_age_to_json(oldest),
        "perf_history":         perf_history,
        "standards_history":    [
            {**r, "year": r["race_date"].year, "race_date": r["race_date"].isoformat()}
            for r in standards_hist
        ],
    }


def _winners_age_to_json(rows):
    """Convert winners_with_age rows to JSON-friendly dicts."""
    return [
        {
            "athlete_id":    w["athlete_id"],
            "name":          w["name"],
            "country_emoji": w["country_emoji"],
            "profile_img":   bool(w.get("profile_img")),
            "race_id":       w["race_id"],
            "year":          w["race_date"].year if w.get("race_date") else None,
            "edition_short": w.get("edition_short"),
            "age":           w["age"],
        }
        for w in rows
    ]


_GENDER_SLUG = {"male": "men", "female": "women", "mixed": "mixed"}


def _program_label(sub, gender, prog_name=None):
    """Display label for a program. AG programs surface the age band + sprint
    flag (e.g. "AG Men 30-34", "AG Men 30-34 Sprint"). Non-AG mirrors the
    pre-AG behaviour: "Elite Men", "U23 Women" etc."""
    # Long-course pro fields are filed under sub_category='ag' in the data
    # (e.g. Ironman 70.3 Worlds prog_name='Pro Men'); render them as-is.
    if prog_name in ("Pro Men", "Pro Women"):
        return prog_name
    base = f"{_SUB_LABELS.get(sub, sub.title())} {_GENDER_LABELS.get(gender, gender.title())}"
    if sub == "ag" and prog_name:
        parts = prog_name.split()
        band = parts[0] if parts else ""
        suffix = " ".join(p for p in parts[1:] if p not in ("AG", "Male", "Female"))
        return f"{base} {band}{(' ' + suffix) if suffix else ''}".strip()
    return base


def _program_slug_for_option(o):
    """Compact slug for a program option. AG slugs include the prog_name so
    individual age bands round-trip cleanly through the URL; non-AG keeps the
    legacy `{sub}-{gender_word}` form for stable bookmarks."""
    sub, gender, prog_name = o["sub_category"], o["gender"], o.get("prog_name")
    if sub == "ag" and prog_name:
        return prog_name.lower().replace(" ", "-")
    return f"{sub}-{_GENDER_SLUG.get(gender, gender)}"


def _program_label_for_option(o):
    return _program_label(o["sub_category"], o["gender"], o.get("prog_name"))


def _program_slug(sub, gender, prog_name=None):
    """Tuple-style slug builder used by route handlers that already hold a
    program tuple. Mirrors `_program_slug_for_option`."""
    return _program_slug_for_option({"sub_category": sub, "gender": gender, "prog_name": prog_name})


@router.get("/series/list")
async def series_list():
    """Lightweight list of all series for the global search modal."""
    return JSONResponse([
        {"name": s["name"], "slug": s["slug"], "race_count": s["race_count"]}
        for s in queries.get_all_series()
    ])


@router.get("/recurring/list")
async def recurring_list():
    """Lightweight list of recurring events for the global search modal."""
    return JSONResponse(queries.get_all_recurring_events())


@router.get("/series", response_class=HTMLResponse)
async def series_index(request: Request):
    series_list = queries.get_all_series()
    sids = [s["series_id"] for s in series_list]
    highlights = queries.get_series_index_highlights(sids)
    records    = queries.get_series_index_records(sids)

    # Standard-rating thresholds keyed by gender, used to classify the
    # strongest-field records into a beginner/.../expert pill.
    thresholds = {g: queries.get_race_standard_thresholds(g) for g in ("male", "female")}

    for s in series_list:
        h = highlights.get(s["series_id"], {})
        s["editions"]   = h.get("editions") or []
        s["top_male"]   = h.get("top_male") or []
        s["top_female"] = h.get("top_female") or []
        s["is_ag_only"] = h.get("is_ag_only", False)
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

        # Classify the strongest-field ratings using the per-gender
        # thresholds; race_handle ("short") is already attached upstream.
        recs = records.get(s["series_id"]) or {"male": {}, "female": {}}
        for gender_key, db_gender in (("male", "male"), ("female", "female")):
            bucket = recs.get(gender_key, {})
            for k in ("strongest_overall", "strongest_swim", "strongest_bike", "strongest_run"):
                if k in bucket:
                    bucket[k]["classification"] = _classify(bucket[k]["value"], thresholds[db_gender])
        s["records"] = recs

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
    program_options = queries.get_program_options_for_series(sid)
    # AG age-band programs only make sense for AG worlds / continental
    # championship tiers. Elsewhere, expose pro/elite/u23/junior only --
    # "Pro Men"/"Pro Women" are filed under sub_category='ag' in the data
    # but are pro fields and stay visible.
    if not (series.get("tier") or "").startswith("ag-"):
        program_options = [
            o for o in program_options
            if o["sub_category"] != "ag" or o.get("prog_name") in ("Pro Men", "Pro Women")
        ]
    prog = _resolve_program(program_options, program)
    active_slug = _program_slug(*prog) if prog else None
    program_tabs, program_overflow = _build_program_tabs(program_options, active_slug)

    payload = _build_program_payload(
        races          = queries.get_series_races(sid, program=prog),
        leaders        = queries.get_series_all_time_leaders(sid, program=prog),
        perf_history   = queries.get_series_performance_history(sid, program=prog),
        medal_table    = queries.get_series_medal_table(sid, program=prog),
        winners_age    = queries.get_series_winners_with_age(sid, program=prog),
        standards_hist = queries.get_series_standards_history(sid, program=prog),
        prog           = prog,
    )

    return templates.TemplateResponse("series.html", {
        "request":      request,
        "active_page":  "races",
        "series":       series,
        "program_tabs":     program_tabs,
        "program_overflow": program_overflow,
        **payload,
    })


@router.get("/series/{slug}/data")
async def series_data(slug: str, program: str | None = None):
    """JSON sibling of /series/{slug} - returns the program-scoped payload
    so the page can switch programs without a full reload."""
    series = queries.get_series_by_slug(slug)
    if not series:
        raise HTTPException(status_code=404)
    sid = series["series_id"]
    prog = _resolve_program(queries.get_program_options_for_series(sid), program)
    return JSONResponse(_build_program_payload(
        races          = queries.get_series_races(sid, program=prog),
        leaders        = queries.get_series_all_time_leaders(sid, program=prog),
        perf_history   = queries.get_series_performance_history(sid, program=prog),
        medal_table    = queries.get_series_medal_table(sid, program=prog),
        winners_age    = queries.get_series_winners_with_age(sid, program=prog),
        standards_hist = queries.get_series_standards_history(sid, program=prog),
        prog           = prog,
    ))


@router.get("/recurring/{slug}", response_class=HTMLResponse)
async def recurring_detail(request: Request, slug: str, program: str | None = None):
    """Page for one recurring event group (e.g. all editions of Kona).

    Reuses the series template — same data shape (races, leaders, medals,
    perf history, map) — but scopes by recurring_event_id rather than
    series_id.
    """
    rec = queries.get_recurring_event_by_slug(slug)
    if not rec:
        raise HTTPException(status_code=404)

    rid = rec["recurring_event_id"]
    program_options = queries.get_program_options_for_recurring(rid)
    prog = _resolve_program(program_options, program)
    active_slug = _program_slug(*prog) if prog else None
    program_tabs, program_overflow = _build_program_tabs(program_options, active_slug)

    payload = _build_program_payload(
        races          = queries.get_recurring_races(rid, program=prog),
        leaders        = queries.get_recurring_all_time_leaders(rid, program=prog),
        perf_history   = queries.get_recurring_performance_history(rid, program=prog),
        medal_table    = queries.get_recurring_medal_table(rid, program=prog),
        winners_age    = queries.get_recurring_winners_with_age(rid, program=prog),
        standards_hist = queries.get_recurring_standards_history(rid, program=prog),
        prog           = prog,
    )

    series_like = {
        "name":        rec["name"],
        "slug":        rec["slug"],
        "description": rec.get("description") or f"All editions of {rec['name']}.",
    }

    return templates.TemplateResponse("series.html", {
        "request":      request,
        "active_page":  "races",
        "series":       series_like,
        "is_recurring": True,
        "program_tabs":     program_tabs,
        "program_overflow": program_overflow,
        **payload,
    })


@router.get("/recurring/{slug}/data")
async def recurring_data(slug: str, program: str | None = None):
    """JSON sibling of /recurring/{slug}."""
    rec = queries.get_recurring_event_by_slug(slug)
    if not rec:
        raise HTTPException(status_code=404)
    rid = rec["recurring_event_id"]
    prog = _resolve_program(queries.get_program_options_for_recurring(rid), program)
    return JSONResponse(_build_program_payload(
        races          = queries.get_recurring_races(rid, program=prog),
        leaders        = queries.get_recurring_all_time_leaders(rid, program=prog),
        perf_history   = queries.get_recurring_performance_history(rid, program=prog),
        medal_table    = queries.get_recurring_medal_table(rid, program=prog),
        winners_age    = queries.get_recurring_winners_with_age(rid, program=prog),
        standards_hist = queries.get_recurring_standards_history(rid, program=prog),
        prog           = prog,
    ))
