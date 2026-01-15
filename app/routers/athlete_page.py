import pickle
from functools import lru_cache
from typing import List
from collections import OrderedDict
import pandas as pd
import numpy as np

from stats.athlete import Athlete
from stats.cache import get_race_lookup

from config import RUNTIME_ATHLETES_DIR

from app.routers.router_utils import format_time, format_time_behind, format_rating_change, format_1yr_rating_change

from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import STATIC_BASE_URL

templates = Jinja2Templates(directory = "templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
router = APIRouter()

class RenameUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        # Redirect old module references to the correct one
        if module == 'athlete':
            module = 'stats.athlete'
        return super().find_class(module, name)
    
def load_athlete(athlete_id: int) -> Athlete:
    """ Load athlete data from pickle file """
    file_path = RUNTIME_ATHLETES_DIR / f"{athlete_id}.pkl"
    
    if not file_path.exists():
        # TODO: Add 404 page
        raise HTTPException(status_code = 404, detail = f"Athlete {athlete_id} not found")
    
    try:
        with open(file_path, 'rb') as f:
            return RenameUnpickler(f).load()
            
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Error loading athlete data: {str(e)}")

@lru_cache(maxsize=32)
def load_athlete_cached(athlete_id: int) -> Athlete:
    return load_athlete(athlete_id)

def format_ranking(rank: int) -> str:
    """ Format global ranking """
    return f"#{rank} all time" if rank > 0 else "No Ranking"

def format_ordinal(n: int) -> str:
    """ Convert int position to ordinal string """
    try:
        n = int(n)
    except Exception as e:
        return "***"

    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

# TODO: Hacky int-ing for now, remove when reloading and positions are actually int
def format_olympic_position(position: int) -> str:
    position = int(position)
    if position == 1: return "Olympic Champion"
    if position == 2: return "Olympic Silver"
    if position == 3: return "Olympic Bronze"
    return f"Olympic Games, {format_ordinal(position)}"

def format_world_champs_position(position: int) -> str:
    position = int(position)
    if position == 1: return "World Champion"
    if position == 2: return "World Championship Silver"
    if position == 3: return "World Championship Bronze"
    return f"World Championships, {format_ordinal(position)}"

def format_race_position(race_type: str, position: int) -> str:
    position = int(position)
    if position == 1: return f"{race_type} Win"
    if position == 2: return f"{race_type} Silver"
    if position == 3: return f"{race_type} Bronze"
    return f"{race_type}, {format_ordinal(position)}"

def format_notable_results(athlete: Athlete, race_lookup: dict) -> List[dict]:
    categories = [
        athlete.notable_results_olympic,
        athlete.notable_results_world_champs,
        athlete.notable_results_wtcs,
        athlete.notable_results_wc,
        athlete.notable_results_cc
    ]
    formatters = [
        format_olympic_position,
        format_world_champs_position,
        lambda pos: format_race_position("WTCS", pos),
        lambda pos: format_race_position("World Cup", pos),
        lambda pos: format_race_position("Continental Cup", pos)
    ]

    formatted_results: List[dict] = []

    for results, formatter in zip(categories, formatters):
        grouped_results: OrderedDict[str, dict] = OrderedDict()
        for rid, pos in sorted(results, key = lambda x: int(x[1])):
            race_info = race_lookup.get(rid, [])
            race_handle = race_info[2] if len(race_info) > 2 else f"Race {rid}"
            description = formatter(pos)

            entry = grouped_results.setdefault(description, {
                "description": description,
                "races": [],
                "count": 0
            })
            entry["races"].append({
                "race_id": rid,
                "race_name": race_handle
            })
            entry["count"] += 1

        # Limit to results per event type
        for entry in list(grouped_results.values())[:3]:
            description = entry["description"]
            if entry["count"] > 1:
                if description.endswith("Win"):
                    description = f"{entry['count']} x {description}s" 
                else:
                    description = f"{entry['count']} x {description}"

            formatted_results.append({
                "description": description,
                "races": entry["races"]
            })

    return formatted_results[:10]

def get_current_ratings(athlete: Athlete) -> dict:
    return {
        "overall_rating": round(athlete.overall_rating),
        "overall_rank": format_ranking(athlete.overall_rank),
        "swim_rating": round(athlete.swim_rating),
        "swim_rank": format_ranking(athlete.swim_rank),
        "bike_rating": round(athlete.bike_rating),
        "bike_rank": format_ranking(athlete.bike_rank),
        "run_rating": round(athlete.run_rating),
        "run_rank": format_ranking(athlete.run_rank),
        "transition_rating": round(athlete.transition_rating),
        "transition_rank": format_ranking(athlete.transition_rank)
    }

def get_rating_changes_1yr(athlete: Athlete) -> dict:
    return {
        "overall_change_1yr": format_1yr_rating_change(athlete.overall_change_1yr),
        "swim_change_1yr": format_1yr_rating_change(athlete.swim_change_1yr),
        "bike_change_1yr": format_1yr_rating_change(athlete.bike_change_1yr),
        "run_change_1yr": format_1yr_rating_change(athlete.run_change_1yr),
        "transition_change_1yr": format_1yr_rating_change(athlete.transition_change_1yr)
    }

def get_best_ratings(athlete: Athlete, race_lookup: dict) -> dict:
    return {
        "max_overall": round(athlete.max_overall),
        "max_overall_race": race_lookup.get(athlete.max_overall_race_id, ['', '', ''])[2],
        "max_swim": round(athlete.max_swim),
        "max_swim_race": race_lookup.get(athlete.max_swim_race_id, ['', '', ''])[2],
        "max_bike": round(athlete.max_bike),
        "max_bike_race": race_lookup.get(athlete.max_bike_race_id, ['', '', ''])[2],
        "max_run": round(athlete.max_run),
        "max_run_race": race_lookup.get(athlete.max_run_race_id, ['', '', ''])[2],
        "max_transition": round(athlete.max_transition),
        "max_transition_race": race_lookup.get(athlete.max_transition_race_id, ['', '', ''])[2],
    }
    
# Format best performance data
def get_best_performances(athlete: Athlete, race_lookup: dict) -> dict:
    valid_overall_best = athlete.overall_increase_race_id != 0
    valid_swim_best = athlete.swim_increase_race_id != 0
    valid_bike_best = athlete.bike_increase_race_id != 0
    valid_run_best = athlete.run_increase_race_id != 0
    valid_transition_best = athlete.transition_increase_race_id != 0

    # Match formatting of rating change
    no_best = {
        "formatted_str": "-",
        "css_class": "no-best-performance"
    }

    return {
        "overall_change": format_rating_change(athlete.overall_increase) if valid_overall_best else no_best,
        "overall_race": race_lookup.get(athlete.overall_increase_race_id, ['', '', ''])[2] if valid_overall_best else "",
        "swim_change": format_rating_change(athlete.swim_increase) if valid_swim_best else no_best,
        "swim_race": race_lookup.get(athlete.swim_increase_race_id, ['', '', ''])[2] if valid_swim_best else "",
        "bike_change": format_rating_change(athlete.bike_increase) if valid_bike_best else no_best,
        "bike_race": race_lookup.get(athlete.bike_increase_race_id, ['', '', ''])[2] if valid_bike_best else "",
        "run_change": format_rating_change(athlete.run_increase) if valid_run_best else no_best,
        "run_race": race_lookup.get(athlete.run_increase_race_id, ['', '', ''])[2] if valid_run_best else "",
        "transition_change": format_rating_change(athlete.transition_increase) if valid_transition_best else no_best,
        "transition_race": race_lookup.get(athlete.transition_increase_race_id, ['', '', ''])[2] if valid_transition_best else ""
    }

def get_race_history(athlete: Athlete, race_lookup: dict) -> List[dict]:
    formatted_splits = []
    for result in sorted(athlete.race_results, key = lambda x : x.race_date, reverse = True):
        race_title = race_lookup.get(int(result.race_id), ['', ''])[1]
        race_program = race_lookup.get(int(result.race_id), ['', '', '', '', '', ''])[4]

        formatted_splits.append({
            "race_id": result.race_id,
            "race_title": race_title,
            "race_date": result.race_date, # Format in template to allow for sorting by numeric date
            "program": race_program,
            "position": result.position,
            "overall": format_time(result.overall_s),
            "overall_behind": format_time_behind(result.overall_behind_s) if result.overall_behind_s is not None else "",
            "swim": format_time(result.swim_s),
            "swim_behind": format_time_behind(result.swim_behind_s) if result.swim_behind_s is not None else "",
            "t1": format_time(result.t1_s),
            "t1_behind": format_time_behind(result.t1_behind_s) if result.t1_behind_s is not None else "",
            "bike": format_time(result.bike_s),
            "bike_behind": format_time_behind(result.bike_behind_s) if result.bike_behind_s is not None else "",
            "t2": format_time(result.t2_s),
            "t2_behind": format_time_behind(result.t2_behind_s) if result.t2_behind_s is not None else "",
            "run": format_time(result.run_s),
            "run_behind": format_time_behind(result.run_behind_s) if result.run_behind_s is not None else ""
        })
            
    return formatted_splits

def get_rating_history(athlete: Athlete, race_lookup: dict) -> List[dict]:
    formatted_ratings = []
    results = sorted(athlete.race_results, key = lambda x: x.race_date, reverse = True)
    ratings = sorted(athlete.rating_history, key = lambda x: x.race_date, reverse = True)
    
    for result, rating in zip(results, ratings):
        race_title = race_lookup.get(int(rating.race_id), ['', ''])[1]
        race_program = race_lookup.get(int(rating.race_id), ['', '', '', '', ''])[4]

        formatted_ratings.append({
            "race_id": rating.race_id,
            "race_date": rating.race_date, # Format in template to allow for sorting by numeric date
            "race_title": race_title,
            "race_program": race_program,
            "position": result.position,
            "overall_rating": round(rating.overall_rating),
            "swim_rating": round(rating.swim_rating),
            "bike_rating": round(rating.bike_rating),
            "run_rating": round(rating.run_rating),
            "transition_rating": round(rating.transition_rating),
            "overall_change": format_rating_change(rating.overall_change),
            "swim_change": format_rating_change(rating.swim_change),
            "bike_change": format_rating_change(rating.bike_change),
            "run_change": format_rating_change(rating.run_change),
            "transition_change": format_rating_change(rating.transition_change)
        })
        
    return formatted_ratings

def get_pct_behind_leaders_chart(athlete: Athlete, race_lookup: dict) -> dict:
    times_df: pd.DataFrame = athlete.get_times_df()
    
    dates = times_df['race_date'].dt.strftime('%Y-%m-%d').tolist()
    race_ids = times_df['race_id'].tolist()
    race_names = [
        race_lookup.get(race_id, ['', ''])[1] for race_id in race_ids
    ]
    
    overall_pcts = times_df['overall_pct_behind'].astype(float).replace({np.nan: None}).tolist()
    swim_pcts = times_df['swim_pct_behind'].astype(float).replace({np.nan: None}).tolist()
    bike_pcts = times_df['bike_pct_behind'].astype(float).replace({np.nan: None}).tolist()
    run_pcts = times_df['run_pct_behind'].astype(float).replace({np.nan: None}).tolist()
    
    return {
        "overall": {
            "datasets": [
                {
                    "label": "Overall % Behind Leader",
                    "data": [
                        {"x": date, "y": round(pct * 100, 1), "race_name": race_name}
                        for date, pct, race_name in zip(dates, overall_pcts, race_names)
                        if pct is not None
                    ],
                    "borderColor": "#4CAF50",
                    "backgroundColor": "rgba(76, 175, 80, 0.1)",
                    "borderWidth": 2,
                    "pointRadius": 3
                }
            ]
        },
        "swim": {
            "datasets": [
                {
                    "label": "Swim % Behind Leader",
                    "data": [
                    {"x": date, "y": round(pct * 100, 1), "race_name": race_name}
                    for date, pct, race_name in zip(dates, swim_pcts, race_names)
                    if pct is not None
                ],
                "borderColor": "#357ABD",
                "backgroundColor": "rgba(53, 122, 189, 0.1)",
                "borderWidth": 2,
                "pointRadius": 3
                }
            ]
        },
        "bike": {
            "datasets": [
                {
                    "label": "Bike % Behind Leader",
                "data": [
                    {"x": date, "y": round(pct * 100, 1), "race_name": race_name}
                    for date, pct, race_name in zip(dates, bike_pcts, race_names)
                    if pct is not None
                ],
                "borderColor": "#FF9800",
                "backgroundColor": "rgba(255, 152, 0, 0.1)",
                "borderWidth": 2,
                "pointRadius": 3
                }
            ]
        },
        "run": {
            "datasets": [
                {
                    "label": "Run % Behind Leader",
                    "data": [
                    {"x": date, "y": round(pct * 100, 1), "race_name": race_name}
                    for date, pct, race_name in zip(dates, run_pcts, race_names)
                    if pct is not None
                ],
                "borderColor": "#4CAF50",
                "backgroundColor": "rgba(76, 175, 80, 0.1)",
                "borderWidth": 2,
                "pointRadius": 3
                }
            ]
        }
    }

def get_splits_chart(athlete: Athlete, race_lookup: dict) -> dict:
    times_df: pd.DataFrame = athlete.get_times_df()
    
    # Prepare date strings and race names once
    dates = times_df['race_date'].dt.strftime('%Y-%m-%d').tolist()
    race_ids = times_df['race_id'].tolist()
    race_names = [
        race_lookup.get(race_id, ['', ''])[1] for race_id in race_ids
    ]
    
    swim_times = times_df['swim_s'].astype(int).tolist()
    bike_times = times_df['bike_s'].astype(int).tolist()
    run_times = times_df['run_s'].astype(int).tolist()
    
    return {
        "swim": {
            "datasets": [
                {
                    "label": "Sprint Swim Times",
                    "data": [
                        {"x": date, "y": time, "race_name": race_name}
                        for date, time, race_name in zip(dates, swim_times, race_names)
                        if time < 960 and time != 0
                    ],
                    "borderColor": "#357ABD",
                    "backgroundColor": "rgba(53, 122, 189, 0.1)",
                    "borderWidth": 2,
                    "pointRadius": 3,
                },
                {
                    "label": "Standard Swim Times",
                    "data": [
                        {"x": date, "y": time, "race_name": race_name}
                        for date, time, race_name in zip(dates, swim_times, race_names)
                        if time >= 960 and time != 0
                    ],
                    "borderColor": "#E91E63",
                    "backgroundColor": "rgba(233, 30, 99, 0.1)",
                    "borderWidth": 2,
                    "pointRadius": 3
                }
            ]
        },
        "bike": {
            "datasets": [
                {
                    "label": "Sprint Bike Times",
                    "data": [
                        {"x": date, "y": time, "race_name": race_name}
                        for date, time, race_name in zip(dates, bike_times, race_names)
                        if time <= 2700 and time != 0
                    ],
                    "borderColor": "#357ABD",
                    "backgroundColor": "rgba(53, 122, 189, 0.1)",
                    "borderWidth": 2,
                    "pointRadius": 3
                },
                {
                    "label": "Standard Bike Times",
                    "data": [
                        {"x": date, "y": time, "race_name": race_name}
                        for date, time, race_name in zip(dates, bike_times, race_names)
                        if time > 2700 and time != 0
                    ],
                    "borderColor": "#E91E63",
                    "backgroundColor": "rgba(233, 30, 99, 0.1)",
                    "borderWidth": 2,
                    "pointRadius": 3
                }
            ]
        },
        "run": {
            "datasets": [
                {
                    "label": "Sprint Run Times",
                    "data": [
                        {"x": date, "y": time, "race_name": race_name}
                        for date, time, race_name in zip(dates, run_times, race_names)
                        if time <= 1560 and time != 0
                    ],
                    "borderColor": "#357ABD",
                    "backgroundColor": "rgba(53, 122, 189, 0.1)",
                    "borderWidth": 2,
                    "pointRadius": 3,
                },
                {
                    "label": "Standard Run Times",
                    "data": [
                        {"x": date, "y": time, "race_name": race_name}
                        for date, time, race_name in zip(dates, run_times, race_names)
                        if time > 1560 and time != 0
                    ],
                    "borderColor": "#E91E63",
                    "backgroundColor": "rgba(233, 30, 99, 0.1)",
                    "borderWidth": 2,
                    "pointRadius": 3
                }
            ]
        }
    }

def get_ratings_chart(athlete: Athlete, race_lookup: dict) -> dict:
    """
    Prepare historical rating data for Chart.js.
    """
    ratings_df: pd.DataFrame = athlete.get_ratings_df()

    # Get race IDs and look up race names
    race_ids = ratings_df['race_id'].tolist()
    race_names = [
        race_lookup.get(rid, ['', ''])[1] for rid in race_ids
    ]
    # Ensure race_date is a datetime series before using .dt accessor
    # TODO: Check, this was being weird
    if not pd.api.types.is_datetime64_any_dtype(ratings_df['race_date']):
        ratings_df['race_date'] = pd.to_datetime(ratings_df['race_date'], errors='coerce')
    race_dates = ratings_df['race_date'].dt.strftime('%Y-%m-%d').tolist()
    
    return {
        "datasets": [
            {
                "label": "Overall Rating",
                "data": [
                    {"x": date, "y": rating, "race_name": race_name}
                    for date, rating, race_name in zip(
                        race_dates,
                        ratings_df['overall_rating'].astype(int),
                        race_names
                    )
                ],
                "borderColor": "#357ABD",
                "backgroundColor": "rgba(53, 122, 189, 0.1)",
                "borderWidth": 2,
                "pointRadius": 3,
            },
            {
                "label": "Swim Rating",
                "data": [
                    {"x": date, "y": rating, "race_name": race_name}
                    for date, rating, race_name in zip(
                        race_dates,
                        ratings_df['swim_rating'].astype(int),
                        race_names
                    )
                ],
                "borderColor": "#4CAF50",
                "backgroundColor": "rgba(76, 175, 80, 0.1)",
                "borderWidth": 2,
                "pointRadius": 3,
            },
            {
                "label": "Bike Rating",
                "data": [
                    {"x": date, "y": rating, "race_name": race_name}
                    for date, rating, race_name in zip(
                        race_dates,
                        ratings_df['bike_rating'].astype(int),
                        race_names
                    )
                ],
                "borderColor": "#FF9800",
                "backgroundColor": "rgba(255, 152, 0, 0.1)",
                "borderWidth": 2,
                "pointRadius": 3,
            },
            {
                "label": "Run Rating",
                "data": [
                    {"x": date, "y": rating, "race_name": race_name}
                    for date, rating, race_name in zip(
                        race_dates,
                        ratings_df['run_rating'].astype(int),
                        race_names
                    )
                ],
                "borderColor": "#E91E63",
                "backgroundColor": "rgba(233, 30, 99, 0.1)",
                "borderWidth": 2,
                "pointRadius": 3,
            },
            {
                "label": "Transition Rating",
                "data": [
                    {"x": date, "y": rating, "race_name": race_name}
                    for date, rating, race_name in zip(
                        race_dates,
                        ratings_df['transition_rating'].astype(int),
                        race_names
                    )
                ],
                "borderColor": "#9C27B0",
                "backgroundColor": "rgba(156, 39, 176, 0.1)",
                "borderWidth": 2,
                "pointRadius": 3,
            }
        ]
    }

@router.get("/athlete/{athlete_id}", response_class = HTMLResponse)
async def get_athlete(request: Request, athlete_id: int):
    """
    Prepare athlete information for display as HTML.
    """
    athlete: Athlete = load_athlete_cached(athlete_id)
    
    race_lookup: dict = get_race_lookup()

    # Format notable results
    notable_results = format_notable_results(athlete, race_lookup)

    # Details for key rating cards
    current_ratings = get_current_ratings(athlete)
    rating_changes_1yr = get_rating_changes_1yr(athlete)
    rating_peaks = get_best_ratings(athlete, race_lookup)
    best_performances = get_best_performances(athlete, race_lookup)

    # Get jsons for rating and times charts
    ratings_chart = get_ratings_chart(athlete, race_lookup)
    splits_chart = get_splits_chart(athlete, race_lookup)
    
    # Pct behind leader chart
    pct_behind_leaders_chart = get_pct_behind_leaders_chart(athlete, race_lookup)

    # Format race splits and ratings for display
    race_history = get_race_history(athlete, race_lookup)
    rating_history = get_rating_history(athlete, race_lookup)
    
    return templates.TemplateResponse(
        "athlete.html",
        {
            "request": request, 
            "active_page": "athletes",
            "athlete": athlete,
            "notable_results": notable_results,
            "current_ratings": current_ratings,
            "rating_changes_1yr": rating_changes_1yr,
            "rating_peaks": rating_peaks,
            "best_performances": best_performances,
            "race_history": race_history,
            "rating_history": rating_history,
            "ratings_chart": ratings_chart,
            "overall_pct_behind_chart": pct_behind_leaders_chart["overall"],
            "swim_pct_behind_chart": pct_behind_leaders_chart["swim"],
            "bike_pct_behind_chart": pct_behind_leaders_chart["bike"],
            "run_pct_behind_chart": pct_behind_leaders_chart["run"],
            "swim_times_chart": splits_chart["swim"],
            "bike_times_chart": splits_chart["bike"],
            "run_times_chart": splits_chart["run"],
        }
    )
