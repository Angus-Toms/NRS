
import numpy as np

from datetime import date

from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL, flag

from ptd_data import queries
from app.routers.router_utils import format_time, format_time_behind, format_rating, format_rating_change, format_course_conditions

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION
templates.env.globals["flag"]          = flag
router = APIRouter()

DNF_STATUSES = {"DNF", "DNS", "DQ", "LAP", "NC"}


def _race_cache_headers(race_date):
    """Historical race pages are effectively immutable (only a full ratings
    rebuild touches them), so let the edge hold them for a week instead of
    the default hour - Googlebot recrawls old races constantly and every
    miss pays full origin latency."""
    if race_date and (date.today() - race_date).days > 365:
        return {"Cache-Control": "public, max-age=0, s-maxage=604800, stale-while-revalidate=2592000"}
    return None


def _build_breadcrumb(race, race_id, recurring_meta):
    """Adaptive Series / Event / Year breadcrumb for the race hero.

    Each of the three segments is optional:
      - series : primary series (lowest sort_order) the event belongs to
      - event  : the recurring event group (same venue across years)
      - year   : current race year. Has a dropdown of sibling years when
                 recurring_meta is set; plain text otherwise.
    """
    primary_series = queries.get_series_for_race(race_id)
    series_seg = None
    if primary_series:
        series_seg = {
            'name': primary_series['name'],
            'url':  f"/series/{primary_series['slug']}",
        }

    event_seg = None
    year_options = []
    if recurring_meta:
        event_seg = {
            'name': recurring_meta['name'],
            'url':  f"/recurring/{recurring_meta['slug']}",
        }
        rows = queries.get_year_options_for_recurring(
            recurring_meta['recurring_event_id'],
            race['gender'],
            race.get('sub_category'),
            relay=race.get('distance') == 'relay',
        )
        for r in rows:
            year_options.append({
                'year':           r['year'],
                'race_id':        r['race_id'],
                'race_handle':    r['race_handle'],
                'winner_name':           r['winner_name'],
                'winner_country_alpha3': r['winner_country_alpha3'],
                'overall_s':      format_time(r['overall_s']) if r['overall_s'] else '',
                'is_current':     r['race_id'] == race_id,
            })

    return {
        'series': series_seg,
        'event':  event_seg,
        'year':   race['race_date'].year,
        'years':  year_options,
    }


def _prediction_rows(stored, people, extra=()):
    """Format stored prediction rows (queries.get_race_predictions) for the
    template. people maps athlete_id -> results/start-list dict supplying
    name/country/year_of_birth (+ any `extra` keys). Returns (rows, raw_times)
    with raw_times keyed by discipline for the upcoming-page histograms."""
    DISCS = ['overall', 'swim', 'bike', 'run']
    best = {d: min((r[f'{d}_s'] for r in stored if r[f'{d}_s']), default=None) for d in DISCS}
    rows = []
    raw_times = {d: [] for d in DISCS}
    for r in stored:
        p = people.get(r['athlete_id'])
        if p is None:
            # Field member missing from results/start list - can only happen if
            # the DB changed under a stale prediction build; skip rather than 500.
            continue
        row = {
            'athlete_id':         r['athlete_id'],
            'name':               p['name'],
            'country_alpha3':     p.get('country_alpha3', ''),
            'year_of_birth':      p.get('year_of_birth'),
            'is_low_confidence':  r['is_low_confidence'],
            'predicted_position': r['predicted_position'],
        }
        for k in extra:
            row[k] = p.get(k, '')
        for disc in DISCS:
            raw = r[f'{disc}_s']
            key = 'overall_s' if disc == 'overall' else f'{disc}_s'
            row[key] = format_time(raw)
            b = best[disc]
            if disc == 'overall':
                row['overall_behind_s'] = format_time_behind(raw - b) if (raw and b and raw != b) else ''
            else:
                row[f'{disc}_fastest']  = bool(raw and b and raw == b)
                row[f'{disc}_behind_s'] = format_time_behind(raw - b) if (raw and b and raw != b) else ''
            if raw:
                raw_times[disc].append(raw)
        rows.append(row)
    return rows, raw_times


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


# Legacy URL scheme: upcoming races used to live at /upcoming/race/<id> and
# now share /race/<id>. Google indexed the old URLs (start-list queries earn
# clicks pre-race), so hand that equity to the current URL instead of 404ing.
@router.get("/upcoming/race/{race_id}")
def get_upcoming_race_legacy(race_id: int):
    return RedirectResponse(f"/race/{race_id}", status_code=301)


@router.get("/race/{race_id}", response_class=HTMLResponse)
def get_race(request: Request, race_id: int, partial: bool = False):
    race = queries.get_race_info(race_id)
    if not race:
        upcoming = queries.get_upcoming_race_info(race_id)
        if not upcoming:
            raise HTTPException(status_code=404, detail=f"Race {race_id} not found")
        return _get_upcoming_race(request, upcoming, partial)

    if race["distance"] == "relay":
        return _get_relay_race(request, race, race_id)

    ignored_info = queries.get_race_ignored_info(race_id)
    results      = queries.get_race_results(race_id)
    ratings      = queries.get_race_ratings(race_id)
    standards    = queries.get_race_standards(race_id)
    best_perf    = queries.get_race_best_performances(race_id)
    raw_corrections = queries.get_race_corrections(race_id)

    finish_count = sum(1 for r in results if r["status"] not in DNF_STATUSES)
    # Per-status non-finisher tallies for the race header. Each category is
    # shown separately so LAP/DQ/DNS/NC don't get lumped under "DNFs".
    status_counts = {s: sum(1 for r in results if r["status"] == s) for s in DNF_STATUSES}
    dnf_count     = len(results) - finish_count

    # Add race year for age calculation
    race_year = race["race_date"].year if hasattr(race["race_date"], "year") else int(str(race["race_date"])[:4])
    for r in results + ratings:
        r["age"] = race_year - r["year_of_birth"] if r["year_of_birth"] else None

    # Predictions are precomputed at build time (ptd_data/predictions.py) and
    # served straight from race_predictions / race_course_conditions.
    race_distance = queries.get_race_distance_type(race_id)  # 'sprint' | 'standard' | None
    is_elite = queries.get_race_category(race_id) == 'elite'
    predictions, pos_diffs, course_conditions = None, None, None
    if is_elite:
        stored = queries.get_race_predictions(race_id)
        if stored:
            people = {r["athlete_id"]: r for r in results}
            predictions, _ = _prediction_rows(stored, people)
            pred_pos_map = {r["athlete_id"]: r["predicted_position"] for r in stored}
            pos_diffs = {}
            for r in results:
                if r["status"] in DNF_STATUSES:
                    continue
                predicted = pred_pos_map.get(r["athlete_id"])
                if predicted is not None and r["position"] is not None:
                    pos_diffs[r["athlete_id"]] = predicted - r["position"]  # positive = beat prediction
            course_conditions = format_course_conditions(queries.get_race_course_conditions(race_id))
    if predictions:
        for r in predictions:
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
        "pos_diff":     pos_diffs.get(r["athlete_id"]) if pos_diffs else None,
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

    # Format standards and classify by percentile vs all races of this gender
    race_standards = {d: format_rating(v) for d, v in standards.items()}

    thresholds = queries.get_race_standard_thresholds(race["gender"])
    def _classify(val, t):
        if val is None: return None
        if val >= t["p95"]: return "expert"
        if val >= t["p85"]: return "advanced"
        if val >= t["p60"]: return "intermediate"
        if val >= t["p30"]: return "novice"
        return "beginner"
    race_standard_classes = {d: _classify(standards[d], thresholds[d]) for d in standards}

    # Race rank within (gender, course) for each discipline. Total used for
    # the "X of N" display so users see how the race sorts globally.
    race_rank_row = queries.get_race_rankings(race_id)
    race_rank_total = (queries.get_race_rankings_total(race_rank_row['gender'], race_rank_row['course'])
                       if race_rank_row else 0)
    race_ranks = {}
    for d in ["overall", "swim", "bike", "run", "transition"]:
        race_ranks[d] = race_rank_row.get(f"{d}_rank") if race_rank_row else None

    # Format best performances
    best_performances = {}
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        best_performances[f"{disc}_change"]       = format_rating_change(best_perf[f"{disc}_change"])
        best_performances[f"{disc}_athlete_name"] = best_perf[f"{disc}_athlete_name"]

    # Prediction model data: rating→time pairs with race-count weights for WLS
    # Store full rating record so discipline models can be fitted in JS
    ratings_by_id  = {r["athlete_id"]: r for r in ratings}
    finisher_ids   = [r["athlete_id"] for r in results
                      if r["status"] not in DNF_STATUSES and (r["overall_s"] or 0) > 0]
    race_count_map = queries.get_athlete_race_counts(finisher_ids)
    prediction_data = [
        {
            "rating":      rat["overall_rating"],
            "time":        r["overall_s"],
            "swim_rating": rat["swim_rating"],
            "swim_time":   r["swim_s"] if (r["swim_s"] or 0) > 0 else None,
            "bike_rating": rat["bike_rating"],
            "bike_time":   r["bike_s"] if (r["bike_s"] or 0) > 0 else None,
            "run_rating":  rat["run_rating"],
            "run_time":    r["run_s"] if (r["run_s"] or 0) > 0 else None,
            "w":           max(1, race_count_map.get(r["athlete_id"], 1)),
        }
        for r in results
        if r["status"] not in DNF_STATUSES
        and (r["overall_s"] or 0) > 0
        and (rat := ratings_by_id.get(r["athlete_id"])) is not None
    ]

    # Format corrections: flag which fields changed, format both old and new times
    DISC_PAIRS = [("swim", "swim"), ("t1", "t1"), ("bike", "bike"), ("t2", "t2"), ("run", "run"), ("overall", "overall")]
    corrections_data = []
    for c in raw_corrections:
        fields = []
        for disc, _ in DISC_PAIRS:
            orig = c[f"orig_{disc}"] or 0
            corr_raw = c[f"corr_{disc}"]
            # NULL corr means no correction for this discipline — show the original
            # as-is rather than treating it as a change-to-zero.
            corr = corr_raw if corr_raw is not None else orig
            changed = corr_raw is not None and abs(orig - corr) > 0.5
            fields.append({
                "disc":    disc,
                "changed": changed,
                "orig":    format_time(orig) if orig else "",
                "corr":    format_time(corr) if corr else "",
            })
        corrections_data.append({
            "athlete_id":    c["athlete_id"],
            "name":          c["name"],
            "country_alpha3": c["country_alpha3"],
            "position":      c["position"],
            "status":        c["status"],
            "notes":         c["notes"].strip(),
            "fields":        fields,
            "any_changed":   any(f["changed"] for f in fields),
        })

    # Histograms
    time_hists   = _build_time_histograms(queries.get_race_time_values(race_id))
    rating_hists = _build_rating_histograms(queries.get_race_rating_values(race_id))

    event_id = race.get("event_id")
    event_races = queries.get_races_by_event(event_id) if event_id else []
    series = queries.get_series_for_race(race_id)

    # Other editions at the same venue (same recurring_event). Match on
    # sub_category only (not gender): IM Worlds alternates gender per
    # venue per year, so a strict gender match would hide e.g. the
    # men's 2024 Hawaii edition when viewing the women's 2025 race.
    program = (race.get("sub_category"), None) if race.get("sub_category") else None
    other_editions = queries.get_other_editions_for_event(event_id, program=program) if event_id else []
    for oe in other_editions:
        oe["year"] = oe["race_date"].year
    recurring_meta = queries.get_recurring_event_for_event(event_id) if event_id else None
    recurring_slug = recurring_meta["slug"] if recurring_meta else None

    breadcrumb = _build_breadcrumb(race, race_id, recurring_meta)

    _venue = race["location"]
    race_location = str(_venue).replace('"', '').replace("'", "").strip() if _venue else ""
    race_country  = str(race["country"]).replace('"', '').replace("'", "")

    # Augment race dict with fields the template accesses directly on `race`
    race["date"]          = race["race_date"]   # template uses race.date.strftime(...)
    race["athlete_count"] = len(results)
    race["status_counts"] = status_counts
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        race[f"{disc}_increase_athlete_id"] = best_perf[f"{disc}_athlete_id"] or 0

    template = "race_partial.html" if partial else "race.html"
    return templates.TemplateResponse(template, headers=_race_cache_headers(race["race_date"]), context={
        "request":        request,
        "active_page":    "races",
        "race":           race,
        "race_location":  race_location,
        "race_country":   race_country,
        "event_id":       event_id,
        "event_races":    event_races,
        "finish_count":   finish_count,
        "dnf_count":      dnf_count,
        "ignored_info":          ignored_info,
        "race_standards":        race_standards,
        "race_standard_classes": race_standard_classes,
        "race_ranks":            race_ranks,
        "race_rank_total":       race_rank_total,
        "race_course":           race_rank_row['course'] if race_rank_row else None,
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
        "prediction_data":         prediction_data,
        "predictions":             predictions,
        "has_predictions":         predictions is not None,
        "race_distance":           race_distance,
        "course_conditions":       course_conditions if not ignored_info else None,
        "series":                  series,
        "breadcrumb":              breadcrumb,
        "corrections_data":        corrections_data,
        "other_editions":          other_editions,
        "recurring_slug":           recurring_slug,
        "is_upcoming":             False,
    })


def _get_relay_race(request: Request, race, race_id: int):
    """Mixed team relay race page: team results with leg breakdowns, fastest
    legs, and country rating changes. Rendered as a full page always - the
    partial-swap flow is individual-race-only (relay pills are plain links)."""
    teams = queries.get_relay_teams(race_id)

    finish_count = sum(1 for t in teams if t["status"] == "Finished")
    status_counts = {s: sum(1 for t in teams if t["status"] == s) for s in DNF_STATUSES}
    dnf_count = len(teams) - finish_count

    winner_total = next((t["total_s"] for t in teams
                         if t["position"] == 1 and t["total_s"] > 0), None)

    # Within-leg ranks: same leg number, same gender (anomaly lineups aside,
    # a leg is single-gender anyway). Rank 1 = fastest leg split. Legs are
    # annotated in place (team backref + formatted times below) so the flat
    # fastest-legs list and the nested team view share the same dicts.
    legs_flat = []
    for t in teams:
        for l in t["legs"]:
            l["team"] = t
            if (l["leg_s"] or 0) > 0:
                legs_flat.append(l)
    leg_rank = {}
    leg_fastest_s = {}   # leg_num -> fastest leg split, for the main-row gap annotation
    for leg_num in (1, 2, 3, 4):
        group = sorted((l for l in legs_flat if l["leg_num"] == leg_num),
                       key=lambda l: l["leg_s"])
        if group:
            leg_fastest_s[leg_num] = group[0]["leg_s"]
        for i, l in enumerate(group):
            leg_rank[(l["team"]["team_id"], leg_num)] = i + 1

    for t in teams:
        t["total"] = format_time(t["total_s"]) if t["total_s"] else ""
        t["behind"] = (format_time_behind(t["total_s"] - winner_total)
                       if winner_total and t["total_s"] and t["position"] != 1 else "")
        for l in t["legs"]:
            l["leg"] = format_time(l["leg_s"]) if l["leg_s"] else ""
            for d in ("swim", "t1", "bike", "t2", "run"):
                l[d] = format_time(l[f"{d}_s"]) if l[f"{d}_s"] else ""
            l["rank"] = leg_rank.get((t["team_id"], l["leg_num"]))
            best = leg_fastest_s.get(l["leg_num"])
            l["leg_behind"] = (format_time_behind(l["leg_s"] - best)
                               if best and (l["leg_s"] or 0) > 0 and l["leg_s"] != best else "")

    # Fastest legs, one flat sorted list; the template filters client-side by
    # leg number / gender via data attributes.
    fastest_legs = sorted(legs_flat, key=lambda l: l["leg_s"])

    # Country rating rows, ordered by this race's finish position (best team
    # per country) so the table opens sorted the way the results read; the
    # template's sort headers let the reader re-sort by any rating column.
    team_pos = {}
    for t in teams:
        p = t["position"] if t["position"] is not None else 10_000
        if t["country_full"] not in team_pos or p < team_pos[t["country_full"]]:
            team_pos[t["country_full"]] = p
    country_ratings = [{
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
    } for r in queries.get_relay_country_ratings(race_id)]
    country_ratings.sort(key=lambda r: team_pos.get(r["country_full"], 10_000))

    event_id = race.get("event_id")
    event_races = queries.get_races_by_event(event_id) if event_id else []
    recurring_meta = queries.get_recurring_event_for_event(event_id) if event_id else None
    breadcrumb = _build_breadcrumb(race, race_id, recurring_meta)
    series = queries.get_series_for_race(race_id)

    _venue = race["location"]
    race_location = str(_venue).replace('"', '').replace("'", "").strip() if _venue else ""
    race_country = str(race["country"]).replace('"', '').replace("'", "")

    race["date"] = race["race_date"]
    race["team_count"] = len(teams)
    race["status_counts"] = status_counts

    return templates.TemplateResponse("relay_race.html", headers=_race_cache_headers(race["race_date"]), context={
        "request":        request,
        "active_page":    "races",
        "race":           race,
        "race_location":  race_location,
        "race_country":   race_country,
        "event_id":       event_id,
        "event_races":    event_races,
        "finish_count":   finish_count,
        "dnf_count":      dnf_count,
        "teams":          teams,
        "fastest_legs":   fastest_legs,
        "country_ratings": country_ratings,
        "series":         series,
        "breadcrumb":     breadcrumb,
        "is_upcoming":    False,
    })


def _get_upcoming_race(request: Request, race, partial: bool):
    from datetime import date
    race_id    = race['race_id']
    entries    = queries.get_upcoming_race_entries(race_id)
    upcoming_distance = queries.get_upcoming_race_distance_type(race_id)
    # Two distinct notions of "course" here:
    #  - rating_course ('short'|'long') scopes which ratings feed the standards,
    #    keyed off the actual distance. Passed to the rating/standard queries,
    #    which map it to distance enums (short/long only - not 'ag').
    #  - rank_bucket adds 'ag' on top: race_rankings buckets AG races separately
    #    so the rank reflects peer competition, not the elite short-course field.
    rating_course = queries.course_for_distance(upcoming_distance) or 'short'
    rank_bucket   = 'ag' if race.get('category') == 'ag' else rating_course
    standards  = queries.get_upcoming_race_standards(race_id, course=rating_course)
    is_elite   = race.get('category') == 'elite'

    # Live race rank vs the existing race_rankings universe for this
    # (gender, course). No table row exists for the upcoming race yet, so we
    # compute by counting historic races with a higher standard.
    upcoming_race_ranks = queries.get_upcoming_race_ranks(
        race['gender'], rank_bucket, standards,
    )
    upcoming_rank_total = queries.get_race_rankings_total(race['gender'], rank_bucket)

    # Predictions precomputed at build time; join names/images from entries.
    predictions, pred_raw_times = None, {}
    if is_elite:
        stored = queries.get_race_predictions(race_id)
        if stored:
            people = {e['athlete_id']: e for e in entries}
            predictions, pred_raw_times = _prediction_rows(stored, people, extra=('profile_img',))

    current_year = date.today().year
    for e in entries:
        e['age'] = current_year - e['year_of_birth'] if e['year_of_birth'] else None
    if predictions:
        for r in predictions:
            r['age'] = current_year - r['year_of_birth'] if r['year_of_birth'] else None

    # Ratings table ordered by predicted position (or start_num if no predictions)
    pred_order = {r['athlete_id']: r['predicted_position'] for r in predictions} if predictions else {}
    entries_by_id = {e['athlete_id']: e for e in entries}
    upcoming_ratings_data = sorted(
        [
            {
                **e,
                'is_debut':          e['overall_rating'] is None,
                'overall_rating':    format_rating(e['overall_rating']) if e['overall_rating'] is not None else 1500,
                'swim_rating':       format_rating(e['swim_rating'])    if e['swim_rating']    is not None else 1500,
                'bike_rating':       format_rating(e['bike_rating'])    if e['bike_rating']    is not None else 1500,
                'run_rating':        format_rating(e['run_rating'])     if e['run_rating']     is not None else 1500,
                'transition_rating': format_rating(e['transition_rating']) if e['transition_rating'] is not None else 1500,
            }
            for e in entries
        ],
        key=lambda e: pred_order.get(e['athlete_id'], e['start_num'] or 999),
    )

    race_standards = {d: format_rating(v) for d, v in standards.items()}
    thresholds = queries.get_race_standard_thresholds(race['gender'])
    def _classify(val, t):
        if val is None: return None
        if val >= t['p95']: return 'expert'
        if val >= t['p85']: return 'advanced'
        if val >= t['p60']: return 'intermediate'
        if val >= t['p30']: return 'novice'
        return 'beginner'
    race_standard_classes = {d: _classify(standards[d], thresholds[d]) for d in standards}

    time_hists   = _build_time_histograms(pred_raw_times)
    rating_hists = _build_rating_histograms({
        disc: [e[f'{disc}_rating'] for e in entries if e.get(f'{disc}_rating')]
        for disc in ['overall', 'swim', 'bike', 'run', 'transition']
    })

    race_date = race['race_date']
    days_until = (race_date - date.today()).days if hasattr(race_date, 'year') else None

    _venue = race['location']
    race_location = str(_venue).replace('"', '').replace("'", "").strip() if _venue else ""
    race_country  = str(race['country']).replace('"', '').replace("'", "")

    race['date']          = race['race_date']
    race['athlete_count'] = len(entries)

    template = "race_partial.html" if partial else "race.html"
    return templates.TemplateResponse(template, {
        "request":               request,
        "active_page":           "races",
        "race":                  race,
        "race_location":         race_location,
        "race_country":          race_country,
        "event_id":              race['event_id'],
        "event_races":           queries.get_upcoming_races_by_event(race['event_id']),
        "finish_count":          0,
        "dnf_count":             0,
        "ignored_info":          None,
        "race_standards":        race_standards,
        "race_standard_classes": race_standard_classes,
        "race_ranks":            upcoming_race_ranks,
        "race_rank_total":       upcoming_rank_total,
        "race_course":           rank_bucket,
        "splits_data":           [],
        "predictions":           predictions,
        "has_predictions":       predictions is not None,
        "upcoming_ratings_data": upcoming_ratings_data,
        "overall_time_hist":     time_hists.get("overall", {}),
        "swim_time_hist":        time_hists.get("swim", {}),
        "bike_time_hist":        time_hists.get("bike", {}),
        "run_time_hist":         time_hists.get("run", {}),
        "overall_rating_hist":   rating_hists.get("overall", {}),
        "swim_rating_hist":      rating_hists.get("swim", {}),
        "bike_rating_hist":      rating_hists.get("bike", {}),
        "run_rating_hist":       rating_hists.get("run", {}),
        "transition_rating_hist": rating_hists.get("transition", {}),
        "race_distance":         queries.get_upcoming_race_distance_type(race_id),
        "days_until":            days_until,
        "entry_count":           len(entries),
        "is_upcoming":           True,
        "series":                None,
        "breadcrumb":            _build_breadcrumb(race, race_id,
                                                   queries.get_recurring_event_for_event(race.get('event_id'))
                                                   if race.get('event_id') else None),
        "other_editions":        [],
        "recurring_slug":        None,
    })
