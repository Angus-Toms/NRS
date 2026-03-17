import numpy as np

from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

from ptd_data import queries
from app.routers.router_utils import format_time, format_time_behind, format_rating, format_rating_change

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()

DNF_STATUSES = {"DNF", "DNS", "DQ", "LAP", "NC"}


def _build_time_histograms(time_values, bins=20):
    """
    Build Chart.js histogram data from raw time arrays.
    time_values: dict of discipline -> list of seconds values.
    """
    discipline_details = {
        "overall": {"background": "#357ABD", "display_name": "Overall"},
        "swim":    {"background": "#4CAF50", "display_name": "Swim"},
        "bike":    {"background": "#FF9800", "display_name": "Bike"},
        "run":     {"background": "#E91E63", "display_name": "Run"},
        "t1":      {"background": "#9C27B0", "display_name": "Transition 1"},
        "t2":      {"background": "#673AB7", "display_name": "Transition 2"},
    }
    chart_data = {}
    for disc, values in time_values.items():
        if not values:
            chart_data[disc] = {}
            continue
        counts, bin_edges = np.histogram(values, bins=bins)
        bin_centers = [(bin_edges[i] + bin_edges[i + 1]) / 2 for i in range(len(counts))]
        bin_labels = []
        for i in range(len(bin_edges) - 1):
            if round(bin_edges[i + 1]) - round(bin_edges[i]) <= 1:
                bin_labels.append(format_time(round(bin_edges[i])))
            else:
                bin_labels.append(f"{format_time(int(bin_edges[i]))} - {format_time(int(bin_edges[i + 1]))}")
        chart_data[disc] = {
            "labels": bin_centers,
            "datasets": [{
                "label": discipline_details[disc]["display_name"],
                "data": [
                    {"x": c, "y": int(n), "label": lbl}
                    for c, n, lbl in zip(bin_centers, counts, bin_labels)
                ],
                "backgroundColor": discipline_details[disc]["background"],
                "borderWidth": 0,
                "barPercentage": 1.0,
            }],
        }
    return chart_data


def _build_rating_histograms(rating_values, bins=20):
    """Build Chart.js histogram data from raw rating arrays."""
    discipline_details = {
        "overall":    {"background": "#357ABD"},
        "swim":       {"background": "#4CAF50"},
        "bike":       {"background": "#FF9800"},
        "run":        {"background": "#E91E63"},
        "transition": {"background": "#9C27B0"},
    }
    chart_data = {}
    for disc, values in rating_values.items():
        if not values:
            chart_data[disc] = {}
            continue
        counts, bin_edges = np.histogram(values, bins=bins)
        bin_centers = [(bin_edges[i] + bin_edges[i + 1]) / 2 for i in range(len(counts))]
        bin_labels = [
            f"{int(bin_edges[i])} - {int(bin_edges[i + 1])}"
            for i in range(len(bin_edges) - 1)
        ]
        chart_data[disc] = {
            "labels": bin_centers,
            "datasets": [{
                "label": disc.capitalize(),
                "data": [
                    {"x": c, "y": int(n), "label": lbl}
                    for c, n, lbl in zip(bin_centers, counts, bin_labels)
                ],
                "backgroundColor": discipline_details[disc]["background"],
                "borderWidth": 0,
                "barPercentage": 1.0,
            }],
        }
    return chart_data


@router.get("/race/{race_id}", response_class=HTMLResponse)
async def get_race(request: Request, race_id: int, partial: bool = False):
    race = queries.get_race_info(race_id)
    if not race:
        raise HTTPException(status_code=404, detail=f"Race {race_id} not found")

    results   = queries.get_race_results(race_id)
    ratings   = queries.get_race_ratings(race_id)
    standards = queries.get_race_standards(race_id)
    best_perf = queries.get_race_best_performances(race_id)

    finish_count = sum(1 for r in results if r["status"] not in DNF_STATUSES)
    dnf_count    = len(results) - finish_count

    # Add race year for age calculation
    race_year = race["race_date"].year if hasattr(race["race_date"], "year") else int(str(race["race_date"])[:4])
    for r in results + ratings:
        r["age"] = race_year - r["year_of_birth"] if r["year_of_birth"] else None

    # Format splits data
    splits_data = [{
        **r,
        "overall_s":        format_time(r["overall_s"]),
        "overall_behind_s": format_time_behind(r["overall_behind_s"]),
        "swim_s":           format_time(r["swim_s"]),
        "swim_behind_s":    format_time_behind(r["swim_behind_s"]),
        "bike_s":           format_time(r["bike_s"]),
        "bike_behind_s":    format_time_behind(r["bike_behind_s"]),
        "run_s":            format_time(r["run_s"]),
        "run_behind_s":     format_time_behind(r["run_behind_s"]),
        "t1_s":             format_time(r["t1_s"]),
        "t1_behind_s":      format_time_behind(r["t1_behind_s"]),
        "t2_s":             format_time(r["t2_s"]),
        "t2_behind_s":      format_time_behind(r["t2_behind_s"]),
        # fastest flags: behind == 0 AND the split was actually recorded
        "swim_fastest": r["swim_behind_s"] == 0 and (r["swim_s"] or 0) > 0,
        "bike_fastest": r["bike_behind_s"] == 0 and (r["bike_s"] or 0) > 0,
        "run_fastest":  r["run_behind_s"]  == 0 and (r["run_s"]  or 0) > 0,
        "t1_fastest":   r["t1_behind_s"]   == 0 and (r["t1_s"]   or 0) > 0,
        "t2_fastest":   r["t2_behind_s"]   == 0 and (r["t2_s"]   or 0) > 0,
    } for r in results]

    # Format ratings data
    ratings_data = [{
        **r,
        "overall_rating":    format_rating(r["overall_rating"]),
        "swim_rating":       format_rating(r["swim_rating"]),
        "bike_rating":       format_rating(r["bike_rating"]),
        "run_rating":        format_rating(r["run_rating"]),
        "transition_rating": format_rating(r["transition_rating"]),
        "overall_change":    format_rating_change(r["overall_change"]),
        "swim_change":       format_rating_change(r["swim_change"]),
        "bike_change":       format_rating_change(r["bike_change"]),
        "run_change":        format_rating_change(r["run_change"]),
        "transition_change": format_rating_change(r["transition_change"]),
    } for r in ratings]

    # Format standards
    race_standards = {d: format_rating(v) for d, v in standards.items()}

    # Format best performances
    best_performances = {}
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        best_performances[f"{disc}_change"]       = format_rating_change(best_perf[f"{disc}_change"])
        best_performances[f"{disc}_athlete_name"] = best_perf[f"{disc}_athlete_name"]

    # Histograms
    time_hists   = _build_time_histograms(queries.get_race_time_values(race_id))
    rating_hists = _build_rating_histograms(queries.get_race_rating_values(race_id))

    event_id = race.get("event_id")
    event_races = queries.get_races_by_event(event_id) if event_id else []

    _venue = race["location"]
    race_location = str(_venue).replace('"', '').replace("'", "").strip() if _venue else ""
    race_country  = str(race["country"]).replace('"', '').replace("'", "")

    # Augment race dict with fields the template accesses directly on `race`
    race["date"]          = race["race_date"]   # template uses race.date.strftime(...)
    race["athlete_count"] = finish_count
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        race[f"{disc}_increase_athlete_id"] = best_perf[f"{disc}_athlete_id"] or 0

    template = "race_partial.html" if partial else "race.html"
    return templates.TemplateResponse(template, {
        "request":        request,
        "active_page":    "races",
        "race":           race,
        "race_location":  race_location,
        "race_country":   race_country,
        "event_id":       event_id,
        "event_races":    event_races,
        "finish_count":   finish_count,
        "dnf_count":      dnf_count,
        "race_standards": race_standards,
        "best_performances": best_performances,
        "splits_data":    splits_data,
        "ratings_data":   ratings_data,
        "overall_time_hist":       time_hists.get("overall", {}),
        "swim_time_hist":          time_hists.get("swim", {}),
        "bike_time_hist":          time_hists.get("bike", {}),
        "run_time_hist":           time_hists.get("run", {}),
        "t1_time_hist":            time_hists.get("t1", {}),
        "t2_time_hist":            time_hists.get("t2", {}),
        "overall_rating_hist":     rating_hists.get("overall", {}),
        "swim_rating_hist":        rating_hists.get("swim", {}),
        "bike_rating_hist":        rating_hists.get("bike", {}),
        "run_rating_hist":         rating_hists.get("run", {}),
        "transition_rating_hist":  rating_hists.get("transition", {}),
    })
