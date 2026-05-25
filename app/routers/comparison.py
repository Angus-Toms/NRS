from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL

from ptd_data import queries
from app.routers import router_utils
from app.routers.router_utils import format_1yr_rating_change, format_rating

# Consistent athlete colours used across all charts and tables
A1_COLOR = "#357ABD"  # blue
A2_COLOR = "#E91E63"  # red

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION


def _format_h2h_times(time1_s, time2_s):
    """Return [{formatted_str, css_class}, {formatted_str, css_class}] for the two athletes."""
    times = [{"formatted_str": "", "css_class": ""}, {"formatted_str": "", "css_class": ""}]

    if time1_s == 0 and time2_s == 0:
        times[0]["css_class"] = times[1]["css_class"] = "h2h-dnf"
        return times

    if time1_s == 0:
        times[0] = {"formatted_str": "",                              "css_class": "h2h-dnf"}
        times[1] = {"formatted_str": router_utils.format_time(time2_s), "css_class": "h2h-winner"}
        return times

    if time2_s == 0:
        times[0] = {"formatted_str": router_utils.format_time(time1_s), "css_class": "h2h-winner"}
        times[1] = {"formatted_str": "",                              "css_class": "h2h-dnf"}
        return times

    times[0]["formatted_str"] = router_utils.format_time(time1_s)
    times[1]["formatted_str"] = router_utils.format_time(time2_s)
    if time1_s < time2_s:
        times[0]["css_class"] = "h2h-winner"
    elif time2_s < time1_s:
        times[1]["css_class"] = "h2h-winner"
    return times


def _get_time_behind(time1_s, time2_s):
    if time1_s == 0 or time2_s == 0:
        return ["", ""]
    if time1_s < time2_s:
        return ["", router_utils.format_time_behind(time2_s - time1_s)]
    return [router_utils.format_time_behind(time1_s - time2_s), ""]


# Program -> (course, category). AG is always short-course.
_PROGRAMS = {
    'elite-short': ('short', 'elite'),
    'elite-long':  ('long',  'elite'),
    'ag':          ('short', 'ag'),
}


def _parse_program(program: str):
    return _PROGRAMS.get(program, _PROGRAMS['elite-short'])


@router.get("/athlete-compare", response_class=HTMLResponse)
async def compare_page(request: Request):
    return templates.TemplateResponse("comparison.html", {
        "request": request, "active_page": "athletes",
    })


@router.get("/athlete-compare/search")
async def search_athletes_for_compare(q: str = "", gender: str = "", programs: str = ""):
    if not q or len(q.strip()) < 2:
        return JSONResponse([])
    require_programs = [p for p in programs.split(",") if p in _PROGRAMS] or None
    results = queries.search_athletes(
        q.strip(), gender=gender or None, require_programs=require_programs
    )
    for r in results:
        r["country_name"] = r.pop("country_full")
    return JSONResponse(results)


@router.get("/athlete-compare/athlete/{athlete_id}")
async def get_athlete_for_compare(athlete_id: int, program: str | None = None):
    info = queries.get_athlete_info(athlete_id)
    if not info:
        return JSONResponse({"error": "Not found"}, status_code=404)
    programs = queries.get_athlete_programs(athlete_id)
    # If caller passed ?program=, honour it (falling back if the athlete doesn't
    # have that program). Otherwise pick the first available — favours
    # elite-short, then elite-long, then ag per get_athlete_programs ordering.
    if program in _PROGRAMS and program in programs:
        active_program = program
    elif programs:
        active_program = programs[0]
    else:
        active_program = 'elite-short'
    course, category = _parse_program(active_program)
    ratings = queries.get_athlete_current_ratings(athlete_id, category=category, course=course)
    stats   = queries.get_athlete_stats(athlete_id, category=category, course=course)
    return JSONResponse({
        "athlete_id":     info["athlete_id"],
        "name":           info["name"],
        "gender":         info["gender"],
        "country_emoji":  info["country_emoji"],
        "country_name":   info["country_full"],
        "country_alpha3": info["country_alpha3"],
        "year_of_birth":  info["year_of_birth"] or "",
        "overall_rating": format_rating(ratings["overall_rating"]) if ratings else None,
        "swim_rating":    int(round(ratings["swim_rating"])) if ratings and ratings.get("swim_rating") else None,
        "bike_rating":    int(round(ratings["bike_rating"])) if ratings and ratings.get("bike_rating") else None,
        "run_rating":     int(round(ratings["run_rating"]))  if ratings and ratings.get("run_rating")  else None,
        # world_overall comes from the rankings table, which only carries
        # currently-ranked athletes — no need for a separate active check.
        "world_rank":     ratings.get("world_overall") if ratings else None,
        "wins":           stats["wins"] if stats else None,
        "programs":       programs,
        "active_program": active_program,
    })


@router.get("/athlete-compare/{athlete1_id}/{athlete2_id}", response_class=HTMLResponse)
async def get_comparison_html(request: Request, athlete1_id: int, athlete2_id: int,
                              program: str = "elite-short"):
    if program not in _PROGRAMS:
        program = "elite-short"
    course, category = _parse_program(program)
    # Direct navigation - redirect to the full compare page which auto-loads via JS
    if not request.headers.get("X-Partial"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(
            url=f"/athlete-compare?a1={athlete1_id}&a2={athlete2_id}&program={program}",
            status_code=302,
        )
    info1    = queries.get_athlete_info(athlete1_id)
    info2    = queries.get_athlete_info(athlete2_id)
    if not info1:
        raise HTTPException(status_code=404, detail=f"Athlete {athlete1_id} not found")
    if not info2:
        raise HTTPException(status_code=404, detail=f"Athlete {athlete2_id} not found")
    stats1   = queries.get_athlete_stats(athlete1_id, category=category, course=course)
    stats2   = queries.get_athlete_stats(athlete2_id, category=category, course=course)
    ratings1 = queries.get_athlete_current_ratings(athlete1_id, category=category, course=course)
    ratings2 = queries.get_athlete_current_ratings(athlete2_id, category=category, course=course)
    changes1 = queries.get_athlete_1yr_changes(athlete1_id, category=category, course=course)
    changes2 = queries.get_athlete_1yr_changes(athlete2_id, category=category, course=course)

    common = queries.get_common_races(athlete1_id, athlete2_id, course=course, category=category)

    # Head-to-head race list (per-discipline times so the UI can swap between
    # overall, swim, bike, run with a radio toggle)
    head_to_head = []
    disc_wins = {"overall": [0, 0], "swim": [0, 0], "bike": [0, 0], "run": [0, 0]}
    for race in common:
        disc_cells = {}
        for disc in ("overall", "swim", "bike", "run"):
            t1 = race[f"a1_{disc}_s"]
            t2 = race[f"a2_{disc}_s"]
            a1_time, a2_time = _format_h2h_times(t1, t2)
            a1_behind, a2_behind = _get_time_behind(t1, t2)
            if a1_time["css_class"] == "h2h-winner":
                disc_wins[disc][0] += 1
            elif a2_time["css_class"] == "h2h-winner":
                disc_wins[disc][1] += 1
            disc_cells[disc] = {
                "a1_time": a1_time, "a1_behind": a1_behind,
                "a2_time": a2_time, "a2_behind": a2_behind,
            }
        head_to_head.append({
            "race_id":           race["race_id"],
            "race_name":         race["race_title"],
            "race_date":         race["race_date"],
            "athlete1_position": race["a1_position"],
            "athlete1_status":   race["a1_status"],
            "athlete2_position": race["a2_position"],
            "athlete2_status":   race["a2_status"],
            "disc_cells":        disc_cells,
        })
    a1_wins, a2_wins = disc_wins["overall"]

    athlete1_data = {
        "id":      info1["athlete_id"],
        "name":    info1["name"],
        "country": info1["country_emoji"],
        "country_alpha3": info1["country_alpha3"],
        "year_of_birth":  info1["year_of_birth"],
        "stats": {
            "total_races": stats1["race_starts"],
            "podiums":     stats1["podiums"],
            "wins":        stats1["wins"],
            "h2h_wins":    a1_wins,
        },
    }
    athlete2_data = {
        "id":      info2["athlete_id"],
        "name":    info2["name"],
        "country": info2["country_emoji"],
        "country_alpha3": info2["country_alpha3"],
        "year_of_birth":  info2["year_of_birth"],
        "stats": {
            "total_races": stats2["race_starts"],
            "podiums":     stats2["podiums"],
            "wins":        stats2["wins"],
            "h2h_wins":    a2_wins,
        },
    }

    discs = ["overall", "swim", "bike", "run", "transition"]
    head_to_head_ratings = [
        {
            "label":   d.capitalize(),
            "rating1": format_rating(ratings1[f"{d}_rating"]) if ratings1 else 0,
            "rating2": format_rating(ratings2[f"{d}_rating"]) if ratings2 else 0,
            "change1": format_1yr_rating_change(changes1[f"{d}_change_1yr"]) if changes1 else None,
            "change2": format_1yr_rating_change(changes2[f"{d}_change_1yr"]) if changes2 else None,
        }
        for d in discs
    ]

    # Rating charts - chronological data for both athletes
    ratings_data1 = queries.get_athlete_ratings_data(athlete1_id, category=category, course=course)
    ratings_data2 = queries.get_athlete_ratings_data(athlete2_id, category=category, course=course)

    h2h_ratings_chart = {}
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        h2h_ratings_chart[disc] = {
            "datasets": [
                {
                    "label": info1["name"],
                    "data": [
                        {"x": str(r["race_date"])[:10], "y": int(r[f"{disc}_rating"]),
                         "race_name": r["race_title"], "race_id": r["race_id"],
                         "change": r[f"{disc}_change"]}
                        for r in ratings_data1
                    ],
                    "borderColor": A1_COLOR, "backgroundColor": A1_COLOR + "20",
                    "pointBackgroundColor": A1_COLOR,
                    "borderWidth": 2, "pointRadius": 3,
                },
                {
                    "label": info2["name"],
                    "data": [
                        {"x": str(r["race_date"])[:10], "y": int(r[f"{disc}_rating"]),
                         "race_name": r["race_title"], "race_id": r["race_id"],
                         "change": r[f"{disc}_change"]}
                        for r in ratings_data2
                    ],
                    "borderColor": A2_COLOR, "backgroundColor": A2_COLOR + "20",
                    "pointBackgroundColor": A2_COLOR,
                    "borderWidth": 2, "pointRadius": 3,
                },
            ]
        }

    # Rankings charts - chronological world ranking data for both athletes
    rankings_data1 = queries.get_athlete_rankings_data(athlete1_id, category=category, course=course)
    rankings_data2 = queries.get_athlete_rankings_data(athlete2_id, category=category, course=course)

    def _with_rank_changes(rows, col):
        """Annotate each ranking row with the change from the previous ranked race."""
        points = [r for r in rows if r[col] is not None]
        result = []
        for i, r in enumerate(points):
            prev = points[i - 1][col] if i > 0 else None
            # Positive rank_chg = improved (moved up, lower number)
            rank_chg = (prev - r[col]) if prev is not None else None
            result.append({"x": str(r["race_date"])[:10], "y": r[col],
                           "race_name": r["race_title"], "race_id": r["race_id"],
                           "rank_chg": rank_chg})
        return result

    h2h_rankings_chart = {}
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        col = f"world_{disc}"
        h2h_rankings_chart[disc] = {
            "datasets": [
                {
                    "label": info1["name"],
                    "data": _with_rank_changes(rankings_data1, col),
                    "borderColor": A1_COLOR, "backgroundColor": A1_COLOR + "20",
                    "pointBackgroundColor": A1_COLOR,
                    "borderWidth": 2, "pointRadius": 3,
                },
                {
                    "label": info2["name"],
                    "data": _with_rank_changes(rankings_data2, col),
                    "borderColor": A2_COLOR, "backgroundColor": A2_COLOR + "20",
                    "pointBackgroundColor": A2_COLOR,
                    "borderWidth": 2, "pointRadius": 3,
                },
            ]
        }

    return templates.TemplateResponse("partials/comparison_results.html", {
        "request":             request,
        "athlete1":            athlete1_data,
        "athlete2":            athlete2_data,
        "head_to_head":        head_to_head,
        "head_to_head_ratings": head_to_head_ratings,
        "h2h_disc_wins":       disc_wins,
        "overall_ratings_chart":    h2h_ratings_chart["overall"],
        "swim_ratings_chart":       h2h_ratings_chart["swim"],
        "bike_ratings_chart":       h2h_ratings_chart["bike"],
        "run_ratings_chart":        h2h_ratings_chart["run"],
        "transition_ratings_chart": h2h_ratings_chart["transition"],
        "overall_rankings_chart":    h2h_rankings_chart["overall"],
        "swim_rankings_chart":       h2h_rankings_chart["swim"],
        "bike_rankings_chart":       h2h_rankings_chart["bike"],
        "run_rankings_chart":        h2h_rankings_chart["run"],
        "transition_rankings_chart": h2h_rankings_chart["transition"],
    })
