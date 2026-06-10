# Data download endpoints (CSV/JSON) backing the table download buttons.
# All endpoints reuse queries.py; responses carry attribution and an
# hour-long cache header (data updates weekly).

import csv
import io
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from ptd_data import queries
from app.routers.router_utils import format_time

router = APIRouter()

CACHE_HEADERS = {"Cache-Control": "public, max-age=3600"}

# Mirrors the site footer wording (templates/base.html).
ATTRIBUTION = {
    "source":      "protridata",
    "license":     "free for non-commercial use with attribution",
    "attribution": "Results and data provided by World Triathlon and the PTO. "
                   "Pro Tri Data is in no way affiliated with World Triathlon or PTO.",
}


def _json(payload: dict) -> JSONResponse:
    return JSONResponse(content=jsonable_encoder({**ATTRIBUTION, **payload}), headers=CACHE_HEADERS)


def _csv(rows: list[dict], filename: str) -> Response:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={**CACHE_HEADERS, "Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _add_formatted_times(rows: list[dict]) -> list[dict]:
    """For every *_s seconds column add a sibling h:mm:ss string column.
    Raw seconds stay primary; formatted strings are convenience only."""
    for row in rows:
        formatted = {}
        for key, value in row.items():
            if key.endswith("_s"):
                formatted[key[:-2] + "_formatted"] = format_time(value) if value else ""
        row.update(formatted)
    return rows


_CATEGORY = Query("elite", regex="^(elite|ag)$")
_COURSE   = Query("short", regex="^(short|long)$")
_FORMAT   = Query("json", regex="^(json|csv)$")


def _require_athlete(athlete_id: int) -> dict:
    info = queries.get_athlete_info(athlete_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Athlete {athlete_id} not found")
    info.pop("profile_img", None)
    return info


# ---------------------------------------------------------------- athletes ---

@router.get("/api/v1/athletes/{athlete_id}/results")
async def api_athlete_results(athlete_id: int, category: str = _CATEGORY,
                              course: str = _COURSE, format: str = _FORMAT):
    info = _require_athlete(athlete_id)
    rows = queries.get_athlete_race_history(athlete_id, category, course=course)
    rows = _add_formatted_times(rows)
    if format == "csv":
        return _csv(rows, f"athlete-{athlete_id}-results.csv")
    return _json({
        "athlete_id": athlete_id,
        "name":       info["name"],
        "category":   category,
        "course":     course,
        "count":      len(rows),
        "results":    rows,
    })


@router.get("/api/v1/athletes/{athlete_id}/ratings")
async def api_athlete_ratings(athlete_id: int, category: str = _CATEGORY,
                              course: str = _COURSE, format: str = _FORMAT):
    info = _require_athlete(athlete_id)
    rows = queries.get_athlete_rating_history(athlete_id, category, course=course)
    if format == "csv":
        return _csv(rows, f"athlete-{athlete_id}-ratings.csv")
    return _json({
        "athlete_id": athlete_id,
        "name":       info["name"],
        "category":   category,
        "course":     course,
        "count":      len(rows),
        "ratings":    rows,
    })


# ------------------------------------------------------------------- races ---

@router.get("/api/v1/races/{race_id}/results")
async def api_race_results(race_id: int, format: str = _FORMAT):
    race = queries.get_race_info(race_id)
    if not race:
        raise HTTPException(status_code=404, detail=f"Race {race_id} not found")
    rows = queries.get_race_results(race_id)
    for row in rows:
        row.pop("profile_img", None)
    rows = _add_formatted_times(rows)
    if format == "csv":
        return _csv(rows, f"race-{race_id}-results.csv")
    return _json({
        "race_id":    race_id,
        "race_title": race["race_title"],
        "race_date":  race["race_date"],
        "gender":     race["gender"],
        "count":      len(rows),
        "results":    rows,
    })


# ------------------------------------------------------------- leaderboard ---

@router.get("/api/v1/leaderboard")
async def api_leaderboard(
    gender:      str           = Query("female", regex="^(male|female)$"),
    disc:        str           = Query("overall", regex="^(overall|swim|bike|run|transition)$"),
    order:       str           = Query("top", regex="^(top|hot)$"),
    country:     str           = Query("all"),
    yob_start:   Optional[int] = Query(1930, ge=1930, le=2010),
    yob_end:     Optional[int] = Query(2010, ge=1930, le=2010),
    active_only: bool          = Query(True),
    category:    str           = _CATEGORY,
    course:      str           = _COURSE,
    limit:       int           = Query(50, ge=1, le=200),
    offset:      int           = Query(0, ge=0),
    format:      str           = _FORMAT,
):
    rows = queries.get_leaderboard(
        gender=gender, disc=disc, order=order,
        country=country, yob_start=yob_start, yob_end=yob_end,
        active_only=active_only, offset=offset, limit=limit,
        category=category, course=course,
    )
    for i, row in enumerate(rows):
        row.pop("profile_img", None)
        row["rank"] = offset + i + 1
    if format == "csv":
        return _csv(rows, f"leaderboard-{gender}-{disc}.csv")
    return _json({
        "gender":   gender,
        "disc":     disc,
        "order":    order,
        "country":  country,
        "category": category,
        "course":   course,
        "limit":    limit,
        "offset":   offset,
        "count":    len(rows),
        "athletes": rows,
    })
