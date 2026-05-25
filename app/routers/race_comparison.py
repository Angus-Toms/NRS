import numpy as np

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL

from ptd_data import queries
from app.routers import race_page
from app.routers.router_utils import format_time, format_time_behind, format_rating, format_rating_change

# Consistent race colours across all charts and tables. Same blue/pink as the
# athlete-compare page so users see one visual language for "thing 1 vs thing 2".
R1_COLOR = "#357ABD"
R2_COLOR = "#E91E63"

DNF_STATUSES = race_page.DNF_STATUSES

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION


def _classify_standard(val, thresholds):
    if val is None: return None
    if val >= thresholds["p95"]: return "expert"
    if val >= thresholds["p85"]: return "advanced"
    if val >= thresholds["p60"]: return "intermediate"
    if val >= thresholds["p30"]: return "novice"
    return "beginner"


def _race_summary_payload(race_id: int):
    """Lightweight summary used by the picker widget."""
    s = queries.get_race_compare_summary(race_id)
    if not s:
        return None
    return {
        "race_id":    s["race_id"],
        "race_title": s["race_title"],
        "prog_name":  s["prog_name"],
        "race_date":  s["race_date"].isoformat() if s["race_date"] else None,
        "gender":     s["gender"],
        "distance":   s["distance"],
        "course":     s["course"],
        "venue":      s["venue"] or "",
        "country":    s["country"] or "",
        "athletes":   s["athletes"],
        "finishers":  s["finishers"],
        "dnfs":       s["dnfs"],
    }


@router.get("/race-compare", response_class=HTMLResponse)
async def race_compare_page(request: Request):
    return templates.TemplateResponse("race_comparison.html", {
        "request": request, "active_page": "races",
    })


@router.get("/race-compare/search")
async def search_races_for_compare(q: str = "", course: str = "", gender: str = ""):
    if not q or len(q.strip()) < 2:
        return JSONResponse([])
    results = queries.search_races_for_compare(
        q.strip(),
        course=course or None,
        gender=gender or None,
    )
    for r in results:
        r["race_date"] = r["race_date"].isoformat() if r["race_date"] else None
    return JSONResponse(results)


@router.get("/race-compare/race/{race_id}")
async def get_race_for_compare(race_id: int):
    payload = _race_summary_payload(race_id)
    if not payload:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(payload)


def _build_compare_histogram(values1, values2, label1, label2, bins=20, fmt="time"):
    """Build a shared-bin overlay histogram for the two races."""
    arr1 = np.asarray(values1, dtype=float)
    arr2 = np.asarray(values2, dtype=float)
    pool = np.concatenate([arr1, arr2]) if (len(arr1) or len(arr2)) else np.array([])
    if pool.size == 0:
        return {}
    counts1, edges = np.histogram(arr1, bins=bins, range=(float(pool.min()), float(pool.max())))
    counts2, _     = np.histogram(arr2, bins=edges)
    centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(counts1))]
    if fmt == "time":
        bin_labels = []
        for i in range(len(edges) - 1):
            if round(edges[i + 1]) - round(edges[i]) <= 1:
                bin_labels.append(format_time(round(edges[i])))
            else:
                bin_labels.append(f"{format_time(int(edges[i]))} - {format_time(int(edges[i + 1]))}")
    else:
        bin_labels = [f"{int(edges[i])} - {int(edges[i + 1])}" for i in range(len(edges) - 1)]

    def _ds(counts, label, color):
        return {
            "label": label,
            "data": [{"x": c, "y": int(n), "label": lbl}
                     for c, n, lbl in zip(centers, counts, bin_labels)],
            # Low-opacity fill so overlapping distributions remain readable
            # through each other; the solid border keeps each curve legible.
            "backgroundColor":         color + "33",
            "borderColor":             color,
            "borderWidth":             2,
            "fill":                    True,
            "tension":                 0.2,
            "pointRadius":             0,
            "pointHoverRadius":        5,
            "pointHoverBackgroundColor": color,
            "pointHoverBorderColor":   color,
            "pointHoverBorderWidth":   0,
        }

    return {
        "labels": centers,
        "datasets": [
            _ds(counts1, label1, R1_COLOR),
            _ds(counts2, label2, R2_COLOR),
        ],
    }


def _race_card_data(race_id: int):
    """Bundle of values needed in the comparison partial for one race."""
    race      = queries.get_race_info(race_id)
    if not race:
        return None
    results   = queries.get_race_results(race_id)
    standards = queries.get_race_standards(race_id)
    best_perf = queries.get_race_best_performances(race_id)
    time_vals = queries.get_race_time_values(race_id)
    rate_vals = queries.get_race_rating_values(race_id)

    finishers = sum(1 for r in results if r["status"] not in DNF_STATUSES)
    dnfs      = len(results) - finishers

    # Predictions / course conditions (elite races only)
    is_elite = queries.get_race_category(race_id) == 'elite'
    course_conditions = None
    if is_elite:
        _, _, course_conditions = race_page._compute_race_predictions(
            race_id, race, results, queries.get_prediction_models()
        )

    thresholds = queries.get_race_standard_thresholds(
        race["gender"],
        course=queries.course_for_distance(queries.get_race_distance_type(race_id)) or 'short',
    )
    classes = {d: _classify_standard(standards[d], thresholds[d]) for d in standards}

    race_ranks_row = queries.get_race_rankings(race_id)
    race_ranks = {d: (race_ranks_row.get(f"{d}_rank") if race_ranks_row else None)
                  for d in ["overall", "swim", "bike", "run", "transition"]}
    race_ranks_total = (queries.get_race_rankings_total(race_ranks_row['gender'], race_ranks_row['course'])
                        if race_ranks_row else 0)

    winner_s = next((r["overall_s"] for r in results
                     if r.get("position") == 1 and r.get("overall_s")), None)
    podium = []
    for r in results[:3]:
        if r.get("position") in (1, 2, 3):
            overall_s = r.get("overall_s")
            gap = None
            if r["position"] > 1 and winner_s and overall_s:
                # format_time_behind returns "" for a zero delta; show "+0:00"
                # explicitly so non-winners always read as a diff vs the winner.
                gap = format_time_behind(overall_s - winner_s) or "+0:00"
            podium.append({
                "position":      r["position"],
                "athlete_id":    r["athlete_id"],
                "name":          r["name"],
                "country_emoji": r.get("country_emoji", ""),
                "profile_img":   r.get("profile_img"),
                "time":          format_time(overall_s) if overall_s else "",
                "gap":           gap,
            })

    return {
        "race":         race,
        "results":      results,
        "standards":    {d: format_rating(v) for d, v in standards.items()},
        "raw_standards": standards,
        "standard_classes": classes,
        "best_performances": best_perf,
        "course_conditions": course_conditions,
        "athletes":     len(results),
        "finishers":    finishers,
        "dnfs":         dnfs,
        "time_values":  time_vals,
        "rating_values": rate_vals,
        "podium":       podium,
        "distance":     queries.get_race_distance_type(race_id),
        "race_ranks":   race_ranks,
        "race_ranks_total": race_ranks_total,
        "race_course":  race_ranks_row['course'] if race_ranks_row else None,
    }


@router.get("/race-compare/{race1_id}/{race2_id}", response_class=HTMLResponse)
async def get_race_comparison_html(request: Request, race1_id: int, race2_id: int):
    if not request.headers.get("X-Partial"):
        return RedirectResponse(
            url=f"/race-compare?r1={race1_id}&r2={race2_id}",
            status_code=302,
        )

    c1 = _race_card_data(race1_id)
    c2 = _race_card_data(race2_id)
    if c1 is None:
        raise HTTPException(status_code=404, detail=f"Race {race1_id} not found")
    if c2 is None:
        raise HTTPException(status_code=404, detail=f"Race {race2_id} not found")

    # Same course + gender is enforced by the search filter; double-check here
    # for direct-URL navigation so bad pairings give a clear error rather than
    # nonsense charts.
    course1 = queries.course_for_distance(c1["distance"])
    course2 = queries.course_for_distance(c2["distance"])
    if course1 != course2 or c1["race"]["gender"] != c2["race"]["gender"]:
        raise HTTPException(
            status_code=400,
            detail="Race comparisons require the same course and gender.",
        )

    # ---- Head-to-head info card ----
    DISCS = ["overall", "swim", "bike", "run", "transition"]

    def _race_info_block(c):
        r = c["race"]
        return {
            "race_id":      r["race_id"],
            "race_title":   r["race_title"],
            "prog_name":    r["prog_name"],
            "race_date":    r["race_date"],
            "gender":       r["gender"],
            "distance":     c["distance"],
            "venue":        r["location"] or "",
            "country":      r["country"] or "",
            "athletes":     c["athletes"],
            "finishers":    c["finishers"],
            "dnfs":         c["dnfs"],
            "podium":       c["podium"],
        }

    race1_info = _race_info_block(c1)
    race2_info = _race_info_block(c2)

    # ---- Standards rows (overall + 4 disciplines) ----
    standard_rows = []
    for d, label in [("overall", "Overall"), ("swim", "Swim"), ("bike", "Bike"),
                     ("run", "Run"), ("transition", "Transition")]:
        standard_rows.append({
            "disc":    d,
            "label":   label,
            "value1":  c1["standards"][d],
            "value2":  c2["standards"][d],
            "class1":  c1["standard_classes"][d],
            "class2":  c2["standard_classes"][d],
            "raw1":    c1["raw_standards"][d],
            "raw2":    c2["raw_standards"][d],
            "rank1":   c1["race_ranks"].get(d),
            "rank2":   c2["race_ranks"].get(d),
            "rank_total1": c1["race_ranks_total"],
            "rank_total2": c2["race_ranks_total"],
            "course1": c1["race_course"],
            "course2": c2["race_course"],
        })

    # ---- Course conditions rows ----
    cc1 = c1["course_conditions"] or {}
    cc2 = c2["course_conditions"] or {}
    has_course_conditions = bool(cc1 or cc2)
    cc_rows = []
    if has_course_conditions:
        for d, label in [("overall", "Overall"), ("swim", "Swim"),
                         ("bike", "Bike"), ("run", "Run")]:
            r1 = cc1.get(d)
            r2 = cc2.get(d)
            cc_rows.append({
                "disc":  d,
                "label": label,
                "value1": r1["formatted"] if r1 else "",
                "value2": r2["formatted"] if r2 else "",
                "cat1":   r1["category"] if r1 else "",
                "cat2":   r2["category"] if r2 else "",
            })

    # ---- Best performances rows ----
    bp_rows = []
    for d, label in [("overall", "Overall"), ("swim", "Swim"), ("bike", "Bike"),
                     ("run", "Run"), ("transition", "Transition")]:
        b1 = c1["best_performances"]
        b2 = c2["best_performances"]
        bp_rows.append({
            "disc":   d,
            "label":  label,
            "change1": format_rating_change(b1[f"{d}_change"]) if b1[f"{d}_change"] else None,
            "change2": format_rating_change(b2[f"{d}_change"]) if b2[f"{d}_change"] else None,
            "name1":   b1[f"{d}_athlete_name"],
            "name2":   b2[f"{d}_athlete_name"],
            "athlete1_id": b1[f"{d}_athlete_id"],
            "athlete2_id": b2[f"{d}_athlete_id"],
        })

    # ---- Distribution charts (Chart.js JSON) ----
    label1 = f"{c1['race']['race_title']} ({c1['race']['race_date'].year})"
    label2 = f"{c2['race']['race_title']} ({c2['race']['race_date'].year})"

    time_hists = {}
    for disc in ("overall", "swim", "bike", "run"):
        time_hists[disc] = _build_compare_histogram(
            c1["time_values"].get(disc, []),
            c2["time_values"].get(disc, []),
            label1, label2, fmt="time",
        )
    rating_hists = {}
    for disc in ("overall", "swim", "bike", "run", "transition"):
        rating_hists[disc] = _build_compare_histogram(
            c1["rating_values"].get(disc, []),
            c2["rating_values"].get(disc, []),
            label1, label2, fmt="rating",
        )

    # ---- Common athletes table ----
    raw_common = queries.get_common_athletes_in_races(race1_id, race2_id)
    common_athletes = []
    r1_wins = r2_wins = 0
    DISCS = ("overall", "swim", "bike", "run")

    def _per_disc(c):
        """Per-discipline {time, gap, winner} for each side. Gap is the
        +M:SS string shown beside the slower split; the faster side has an
        empty gap and gets winner=True so the template can highlight it."""
        out = {}
        for d in DISCS:
            s1 = c[f"r1_{d}_s"] or 0
            s2 = c[f"r2_{d}_s"] or 0
            r1 = {"time": format_time(s1) if s1 else "", "gap": "", "winner": False}
            r2 = {"time": format_time(s2) if s2 else "", "gap": "", "winner": False}
            if s1 and s2:
                if s1 < s2:
                    r1["winner"] = True
                    r2["gap"]    = format_time_behind(s2 - s1) or "+0:00"
                elif s2 < s1:
                    r2["winner"] = True
                    r1["gap"]    = format_time_behind(s1 - s2) or "+0:00"
            out[d] = {"r1": r1, "r2": r2}
        return out

    for c in raw_common:
        pos1 = c["r1_position"]
        pos2 = c["r2_position"]
        beat = None  # None = tie / no result
        if c["r1_status"] not in DNF_STATUSES and c["r2_status"] not in DNF_STATUSES \
                and pos1 is not None and pos2 is not None:
            if pos1 < pos2:
                beat = 1; r1_wins += 1
            elif pos2 < pos1:
                beat = 2; r2_wins += 1
        common_athletes.append({
            "athlete_id":    c["athlete_id"],
            "name":          c["name"],
            "country_emoji": c["country_emoji"],
            "r1_position":   pos1,
            "r1_status":     c["r1_status"],
            "r2_position":   pos2,
            "r2_status":     c["r2_status"],
            "splits":        _per_disc(c),
            "beat":          beat,
        })

    # Per-discipline win counts. The wins-summary at the top of the common
    # athletes table swaps to match whichever discipline the radio is on, so
    # the number on each side always reads "athletes with the faster split".
    disc_summary = {
        d: {
            "r1_wins":   sum(1 for a in common_athletes if a["splits"][d]["r1"]["winner"]),
            "r2_wins":   sum(1 for a in common_athletes if a["splits"][d]["r2"]["winner"]),
            "with_both": sum(1 for a in common_athletes
                             if a["splits"][d]["r1"]["time"] and a["splits"][d]["r2"]["time"]),
        }
        for d in DISCS
    }

    return templates.TemplateResponse("partials/race_comparison_results.html", {
        "request":            request,
        "race1":              race1_info,
        "race2":              race2_info,
        "standard_rows":      standard_rows,
        "course_condition_rows": cc_rows,
        "has_course_conditions": has_course_conditions,
        "best_perf_rows":     bp_rows,
        "common_athletes":    common_athletes,
        "common_total":       len(common_athletes),
        "disc_summary":       disc_summary,
        "r1_wins":            r1_wins,
        "r2_wins":            r2_wins,
        "overall_time_hist":  time_hists["overall"],
        "swim_time_hist":     time_hists["swim"],
        "bike_time_hist":     time_hists["bike"],
        "run_time_hist":      time_hists["run"],
        "overall_rating_hist": rating_hists["overall"],
        "swim_rating_hist":    rating_hists["swim"],
        "bike_rating_hist":    rating_hists["bike"],
        "run_rating_hist":     rating_hists["run"],
        "transition_rating_hist": rating_hists["transition"],
        "r1_color":           R1_COLOR,
        "r2_color":           R2_COLOR,
    })
