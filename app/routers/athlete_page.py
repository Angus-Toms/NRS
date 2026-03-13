from collections import OrderedDict
from datetime import date, timedelta

from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries
from app.routers.router_utils import (
    format_time, format_time_behind, format_rating_change, format_1yr_rating_change,
)

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()

_TIER_LABELS = {
    "olympic":         "Olympic",
    "world_champs":    "World Championships",
    "wtcs":            "WTCS",
    "world_cup":       "World Cup",
    "continental_cup": "Continental Cup",
}


# --- Formatting helpers that previously lived on the Athlete object ---

def format_ranking(rank):
    return f"#{rank} all time" if rank and rank > 0 else "No Ranking"


def format_ordinal(n):
    try:
        n = int(n)
    except Exception:
        return "***"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _format_position(tier, pos):
    pos = int(pos)
    label = _TIER_LABELS.get(tier, tier)
    if tier == "olympic":
        if pos == 1: return "Olympic Champion"
        if pos == 2: return "Olympic Silver"
        if pos == 3: return "Olympic Bronze"
        return f"Olympic Games, {format_ordinal(pos)}"
    if tier == "world_champs":
        if pos == 1: return "World Champion"
        if pos == 2: return "World Championship Silver"
        if pos == 3: return "World Championship Bronze"
        return f"World Championships, {format_ordinal(pos)}"
    if pos == 1: return f"{label} Win"
    if pos == 2: return f"{label} Silver"
    if pos == 3: return f"{label} Bronze"
    return f"{label}, {format_ordinal(pos)}"


def _build_notable_results(notable_raw):
    """Group notable results by description, collapse multiples, cap per tier."""
    tier_order = ["olympic", "world_champs", "wtcs", "world_cup", "continental_cup"]
    formatted = []

    for tier in tier_order:
        tier_results = [r for r in notable_raw if r["tier"] == tier]
        grouped = OrderedDict()
        for r in sorted(tier_results, key=lambda x: x["position"]):
            desc = _format_position(tier, r["position"])
            entry = grouped.setdefault(desc, {"description": desc, "races": [], "count": 0})
            entry["races"].append({"race_id": r["race_id"], "race_name": r["race_handle"]})
            entry["count"] += 1

        for i, entry in enumerate(grouped.values()):
            if i >= 3:
                break
            desc = entry["description"]
            if entry["count"] > 1:
                desc = f"{entry['count']} x {desc}s" if desc.endswith("Win") else f"{entry['count']} x {desc}"
            formatted.append({"description": desc, "races": entry["races"]})

    return formatted[:10]


def _build_ratings_chart(ratings_data, race_name_map):
    """Build Chart.js ratings history dataset from query results."""
    colors = {
        "overall":    ("#357ABD", "rgba(53, 122, 189, 0.1)"),
        "swim":       ("#4CAF50", "rgba(76, 175, 80, 0.1)"),
        "bike":       ("#FF9800", "rgba(255, 152, 0, 0.1)"),
        "run":        ("#E91E63", "rgba(233, 30, 99, 0.1)"),
        "transition": ("#9C27B0", "rgba(156, 39, 176, 0.1)"),
    }
    datasets = []
    for disc, (border, bg) in colors.items():
        datasets.append({
            "label": f"{disc.capitalize()} Rating",
            "data": [
                {"x": str(r["race_date"])[:10],
                 "y": int(r[f"{disc}_rating"]),
                 "race_name": r["race_title"]}
                for r in ratings_data
            ],
            "borderColor": border, "backgroundColor": bg,
            "pointBackgroundColor": border,
            "borderWidth": 2, "pointRadius": 3,
        })
    return {"datasets": datasets}


def _build_pct_behind_chart(times_data):
    """Build Chart.js % behind leader charts from times query results."""
    disc_colors = {
        "overall": ("#4CAF50", "rgba(76, 175, 80, 0.1)"),
        "swim":    ("#357ABD", "rgba(53, 122, 189, 0.1)"),
        "bike":    ("#FF9800", "rgba(255, 152, 0, 0.1)"),
        "run":     ("#4CAF50", "rgba(76, 175, 80, 0.1)"),
    }
    charts = {}
    for disc, (border, bg) in disc_colors.items():
        charts[disc] = {
            "datasets": [{
                "label": f"{disc.capitalize()} % Behind Leader",
                "data": [
                    {"x": str(r["race_date"])[:10],
                     "y": round(r[f"{disc}_pct_behind"] * 100, 1),
                     "race_name": r["race_title"]}
                    for r in times_data
                    if r[f"{disc}_pct_behind"] is not None
                ],
                "borderColor": border, "backgroundColor": bg,
                "pointBackgroundColor": border,
                "borderWidth": 2, "pointRadius": 3,
            }]
        }
    return charts


def _build_rankings_charts(rankings_data):
    """Build separate world and national Chart.js ranking charts per discipline."""
    world    = {}
    national = {}
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        world[disc] = {"datasets": [{
            "label": "World Ranking",
            "data": [{"x": str(r["race_date"])[:10], "y": r[f"world_{disc}"], "race_name": r["race_title"]}
                     for r in rankings_data if r[f"world_{disc}"]],
            "borderColor": "#357ABD", "backgroundColor": "rgba(53,122,189,0.1)",
            "pointBackgroundColor": "#357ABD", "borderWidth": 2, "pointRadius": 3,
        }]}
        national[disc] = {"datasets": [{
            "label": "National Ranking",
            "data": [{"x": str(r["race_date"])[:10], "y": r[f"national_{disc}"], "race_name": r["race_title"]}
                     for r in rankings_data if r[f"national_{disc}"]],
            "borderColor": "#FF9800", "backgroundColor": "rgba(255,152,0,0.1)",
            "pointBackgroundColor": "#FF9800", "borderWidth": 2, "pointRadius": 3,
        }]}
    return world, national


def _build_splits_chart(times_data):
    """Build Chart.js split times charts from times query results."""
    charts = {}
    for disc, short_thresh, long_thresh, c_short, c_long in [
        ("swim", 960,  960,  "#357ABD", "#E91E63"),
        ("bike", 2700, 2700, "#357ABD", "#E91E63"),
        ("run",  1560, 1560, "#357ABD", "#E91E63"),
    ]:
        charts[disc] = {
            "datasets": [
                {
                    "label": f"Sprint {disc.capitalize()} Times",
                    "data": [
                        {"x": str(r["race_date"])[:10], "y": int(r[f"{disc}_s"]),
                         "race_name": r["race_title"]}
                        for r in times_data
                        if r[f"{disc}_s"] and 0 < r[f"{disc}_s"] <= short_thresh
                    ],
                    "borderColor": c_short, "backgroundColor": c_short,
                    "pointBackgroundColor": c_short,
                    "borderWidth": 2, "pointRadius": 3,
                },
                {
                    "label": f"Standard {disc.capitalize()} Times",
                    "data": [
                        {"x": str(r["race_date"])[:10], "y": int(r[f"{disc}_s"]),
                         "race_name": r["race_title"]}
                        for r in times_data
                        if r[f"{disc}_s"] and r[f"{disc}_s"] > long_thresh
                    ],
                    "borderColor": c_long, "backgroundColor": c_long,
                    "pointBackgroundColor": c_long,
                    "borderWidth": 2, "pointRadius": 3,
                },
            ]
        }
    return charts


@router.get("/athlete/{athlete_id}", response_class=HTMLResponse)
async def get_athlete(request: Request, athlete_id: int):
    info = queries.get_athlete_info(athlete_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Athlete {athlete_id} not found")

    current  = queries.get_athlete_current_ratings(athlete_id)
    changes  = queries.get_athlete_1yr_changes(athlete_id)
    peaks    = queries.get_athlete_peak_ratings(athlete_id)
    best     = queries.get_athlete_best_performances(athlete_id)
    stats    = queries.get_athlete_stats(athlete_id)
    notable_raw  = queries.get_athlete_notable_results(athlete_id)
    race_hist    = queries.get_athlete_race_history(athlete_id)
    rating_hist  = queries.get_athlete_rating_history(athlete_id)
    times_data    = queries.get_athlete_times_data(athlete_id)
    ratings_data  = queries.get_athlete_ratings_data(athlete_id)
    rankings_data = queries.get_athlete_rankings_data(athlete_id)

    # --- current ratings card ---
    current_ratings = {}
    if current:
        for disc in ["overall", "swim", "bike", "run", "transition"]:
            current_ratings[f"{disc}_rating"] = round(current[f"{disc}_rating"])

    # --- current rankings card ---
    def _make_ranking(rank):
        if not rank or rank <= 0:
            return None
        n = int(rank)
        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return {"n": n, "suffix": suffix}

    current_rankings = {}
    if current:
        for disc in ["overall", "swim", "bike", "run", "transition"]:
            current_rankings[f"world_{disc}"]    = _make_ranking(current.get(f"world_{disc}"))
            current_rankings[f"national_{disc}"] = _make_ranking(current.get(f"national_{disc}"))

    # --- 1yr changes card ---
    rating_changes_1yr = {}
    if changes:
        for disc in ["overall", "swim", "bike", "run", "transition"]:
            rating_changes_1yr[f"{disc}_change_1yr"] = format_1yr_rating_change(
                changes[f"{disc}_change_1yr"]
            )

    # --- peak ratings card ---
    rating_peaks = {}
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        rating_peaks[f"max_{disc}"]      = round(peaks[f"max_{disc}"]) if peaks[f"max_{disc}"] else 0
        rating_peaks[f"max_{disc}_race"] = peaks[f"max_{disc}_race"]

    # --- best performances card ---
    no_best = {"formatted_str": "-", "css_class": "no-best-performance"}
    best_performances = {}
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        change = best[f"{disc}_change"]
        best_performances[f"{disc}_change"] = format_rating_change(change) if change else no_best
        best_performances[f"{disc}_race"]   = best[f"{disc}_race"]

    # --- athlete dict: merge info + stats + computed fields expected by template ---
    race_starts = stats["race_starts"]
    wins        = stats["wins"]
    podiums     = stats["podiums"]
    last_date   = stats["last_race_date"]
    active      = bool(last_date and last_date >= (date.today() - timedelta(days=365)))
    athlete_dict = {
        **info,
        "race_starts":   race_starts,
        "wins":          wins,
        "podiums":       podiums,
        "win_count":     wins,
        "podium_count":  podiums,
        "win_pct":       wins   / max(race_starts, 1),
        "podium_pct":    podiums / max(race_starts, 1),
        "active":        active,
    }
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        athlete_dict[f"max_{disc}_race_id"]       = peaks[f"max_{disc}_race_id"]
        athlete_dict[f"{disc}_increase_race_id"]  = best[f"{disc}_race_id"] or 0

    # --- notable results ---
    notable_results = _build_notable_results(notable_raw)

    # --- race history table ---
    race_history = [
        {
            "race_id":       r["race_id"],
            "race_title":    r["race_title"],
            "race_date":     r["race_date"],
            "program":       r["program"],
            "position":      r["position"],
            "status":        r["status"],
            "overall":       format_time(r["overall_s"]),
            "overall_behind": format_time_behind(r["overall_behind_s"]),
            "swim":          format_time(r["swim_s"]),
            "swim_behind":   format_time_behind(r["swim_behind_s"]),
            "t1":            format_time(r["t1_s"]),
            "t1_behind":     format_time_behind(r["t1_behind_s"]),
            "bike":          format_time(r["bike_s"]),
            "bike_behind":   format_time_behind(r["bike_behind_s"]),
            "t2":            format_time(r["t2_s"]),
            "t2_behind":     format_time_behind(r["t2_behind_s"]),
            "run":           format_time(r["run_s"]),
            "run_behind":    format_time_behind(r["run_behind_s"]),
        }
        for r in race_hist
    ]

    # --- rating history table ---
    rating_history = [
        {
            "race_id":           r["race_id"],
            "race_date":         r["race_date"],
            "race_title":        r["race_title"],
            "race_program":      r["race_program"],
            "position":          r["position"],
            "status":            r["status"],
            "overall_rating":    round(r["overall_rating"]),
            "swim_rating":       round(r["swim_rating"]),
            "bike_rating":       round(r["bike_rating"]),
            "run_rating":        round(r["run_rating"]),
            "transition_rating": round(r["transition_rating"]),
            "overall_change":    format_rating_change(r["overall_change"]),
            "swim_change":       format_rating_change(r["swim_change"]),
            "bike_change":       format_rating_change(r["bike_change"]),
            "run_change":        format_rating_change(r["run_change"]),
            "transition_change": format_rating_change(r["transition_change"]),
        }
        for r in rating_hist
    ]

    # --- charts ---
    pct_behind      = _build_pct_behind_chart(times_data)
    splits          = _build_splits_chart(times_data)
    ratings_chart   = _build_ratings_chart(ratings_data, {})
    world_rankings_charts, national_rankings_charts = _build_rankings_charts(rankings_data)

    return templates.TemplateResponse("athlete.html", {
        "request":        request,
        "active_page":    "athletes",
        "athlete":        athlete_dict,
        "notable_results":     notable_results,
        "current_ratings":     current_ratings,
        "current_rankings":    current_rankings,
        "rating_changes_1yr":  rating_changes_1yr,
        "rating_peaks":        rating_peaks,
        "best_performances":   best_performances,
        "race_history":        race_history,
        "rating_history":      rating_history,
        "ratings_chart":       ratings_chart,
        "overall_pct_behind_chart": pct_behind["overall"],
        "swim_pct_behind_chart":    pct_behind["swim"],
        "bike_pct_behind_chart":    pct_behind["bike"],
        "run_pct_behind_chart":     pct_behind["run"],
        "swim_times_chart":  splits["swim"],
        "bike_times_chart":  splits["bike"],
        "run_times_chart":   splits["run"],
        "overall_world_rankings_chart":    world_rankings_charts["overall"],
        "swim_world_rankings_chart":       world_rankings_charts["swim"],
        "bike_world_rankings_chart":       world_rankings_charts["bike"],
        "run_world_rankings_chart":        world_rankings_charts["run"],
        "transition_world_rankings_chart": world_rankings_charts["transition"],
        "overall_national_rankings_chart":    national_rankings_charts["overall"],
        "swim_national_rankings_chart":       national_rankings_charts["swim"],
        "bike_national_rankings_chart":       national_rankings_charts["bike"],
        "run_national_rankings_chart":        national_rankings_charts["run"],
        "transition_national_rankings_chart": national_rankings_charts["transition"],
    })
