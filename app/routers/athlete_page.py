import math
from collections import OrderedDict
from datetime import date, timedelta

from fastapi import HTTPException, Query, Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import ASSET_VERSION, STATIC_BASE_URL, flag

from ptd_data import queries
from ptd_data.ratings import SCALE
from app.routers.race_page import _anchor_time, LOW_CONF_STARTS
from app.routers.router_utils import (
    format_time, format_time_behind, format_rating_change, format_1yr_rating_change,
)

templates = Jinja2Templates(directory="templates")
templates.env.globals["STATIC_BASE_URL"] = STATIC_BASE_URL
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION
templates.env.globals["flag"]          = flag
router = APIRouter()

_TIER_LABELS = {
    "olympic":               "Olympic",
    "world_champs":          "World Championships",
    "ag_world_champs":       "AG World Championships",
    "ag_continental_champs": "AG Continental Championships",
    "ag_continental_champs": "AG Continental Championships",
    "wtcs":                  "WTCS",
    "world_cup":             "World Cup",
    "continental_cup":       "Continental Cup",
    # Long-course tiers
    "im_world_champs":       "Ironman World Championships",
    "im_703_world_champs":   "Ironman 70.3 World Championships",
    "im":                    "Ironman",
    "t100":                  "T100",
    "im_703":                "Ironman 70.3",
    "challenge":             "Challenge",
}

# Worst finish position that still qualifies for palmares display. Tiers not
# listed are uncapped. The four "worlds"-tier categories (world_champs,
# ag_world_champs, im_world_champs, im_703_world_champs) are capped at 30
# so a 31st-place at Worlds doesn't crowd out higher-finishes elsewhere.
_TIER_POS_CAPS = {
    "olympic":               40,
    "world_champs":          30,
    "ag_world_champs":       30,
    "ag_continental_champs": 15,
    "im_world_champs":       30,
    "im_703_world_champs":   30,
    "wtcs":                  25,
    "world_cup":             20,
    "continental_cup":       15,
    "im":                    20,
    "t100":                  20,
    "im_703":                20,
    "challenge":             15,
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


def _format_position(tier, pos, age_group=None):
    pos = int(pos)
    label = _TIER_LABELS.get(tier, tier)
    if tier == "olympic":
        if pos == 1: return "Olympic Champion"
        if pos == 2: return "Olympic Silver"
        if pos == 3: return "Olympic Bronze"
        return f"Olympic Games, {format_ordinal(pos)}"
    if tier == "world_champs":
        # age_group is "U23" or "Junior" for non-Elite categories, None for Elite
        prefix = f"{age_group} " if age_group else ""
        if pos == 1: return f"{prefix}World Champion"
        if pos == 2: return f"{prefix}World Championship Silver"
        if pos == 3: return f"{prefix}World Championship Bronze"
        return f"{prefix}World Championships, {format_ordinal(pos)}"
    if tier == "ag_world_champs":
        if pos == 1: return "AG World Champion"
        if pos == 2: return "AG World Championship Silver"
        if pos == 3: return "AG World Championship Bronze"
        return f"AG World Championships, {format_ordinal(pos)}"
    if tier == "ag_continental_champs":
        if pos == 1: return "AG Continental Champion"
        if pos == 2: return "AG Continental Silver"
        if pos == 3: return "AG Continental Bronze"
        return f"AG Continental Championships, {format_ordinal(pos)}"
    # Long-course worlds mirror the short-course worlds formatting with an
    # explicit "Ironman" / "Ironman 70.3" prefix so "Ironman World Champion"
    # reads naturally.
    if tier in ("im_world_champs", "im_703_world_champs"):
        prefix = "Ironman 70.3 " if tier == "im_703_world_champs" else "Ironman "
        if pos == 1: return f"{prefix}World Champion"
        if pos == 2: return f"{prefix}World Championship Silver"
        if pos == 3: return f"{prefix}World Championship Bronze"
        return f"{prefix}World Championships, {format_ordinal(pos)}"
    if pos == 1: return f"{label} Win"
    if pos == 2: return f"{label} Silver"
    if pos == 3: return f"{label} Bronze"
    return f"{label}, {format_ordinal(pos)}"


def _build_notable_results(notable_raw, tier_order=None):
    """Group notable results by description, collapse multiples, cap per tier.

    Position caps come from `_TIER_POS_CAPS`: tiers listed there require a
    finish at or above the given position to qualify (e.g. Olympics top 40,
    WTCS top 25). Tiers without an entry are uncapped.
    """
    if tier_order is None:
        tier_order = ["olympic", "world_champs", "wtcs", "world_cup", "continental_cup"]
    formatted = []

    for tier in tier_order:
        cap = _TIER_POS_CAPS.get(tier)
        tier_results = [r for r in notable_raw
                        if r["tier"] == tier and (cap is None or r["position"] <= cap)]
        grouped = OrderedDict()
        for r in sorted(tier_results, key=lambda x: x["position"]):
            desc = _format_position(tier, r["position"], r.get("age_group"))
            entry = grouped.setdefault(desc, {"description": desc, "races": [], "count": 0})
            entry["races"].append({"race_id": r["race_id"], "race_name": r["race_handle"], "race_date": r["race_date"]})
            entry["count"] += 1

        for i, entry in enumerate(grouped.values()):
            if i >= 2:
                break
            desc = entry["description"]
            if entry["count"] > 1:
                desc = f"{entry['count']} x {desc}s" if desc.endswith("Win") else f"{entry['count']} x {desc}"
            races_sorted = sorted(entry["races"], key=lambda x: x["race_date"] or "", reverse=True)
            formatted.append({"description": desc, "races": races_sorted})

    return formatted[:10]


def _build_ratings_chart(ratings_data):
    """Build per-discipline rating history data for the athlete ratings chart."""
    disciplines = ["overall", "swim", "bike", "run", "transition"]
    result = {}
    for disc in disciplines:
        prev_wr = None
        points = []
        for r in ratings_data:
            wr = r.get(f"world_{disc}")
            # World rank change: positive = moved up (lower number is better)
            wr_chg = (prev_wr - wr) if (wr is not None and prev_wr is not None) else None
            prev_wr = wr
            points.append({
                "x":          str(r["race_date"])[:10],
                "y":          int(r[f"{disc}_rating"]),
                "change":     round(r[f"{disc}_change"]) if r[f"{disc}_change"] is not None else None,
                "race_name":  r["race_title"],
                "race_id":    r["race_id"],
                "status":     r.get("status"),
                "time_s":     r.get(f"{disc}_s"),   # None for transition
                "diff_s":     r.get(f"{disc}_diff"), # None for transition
                "t1_s":       r.get("t1_s"),
                "t2_s":       r.get("t2_s"),
                "t1_diff":    r.get("t1_diff"),
                "t2_diff":    r.get("t2_diff"),
                "world_rank": wr,
                "world_rank_chg": wr_chg,
            })
        result[disc] = points
    return result


def _build_pct_behind_chart(times_data):
    """Build flat per-discipline % behind leader data for the new ratings-style chart."""
    result = {}
    for disc in ["overall", "swim", "bike", "run"]:
        result[disc] = [
            {
                "x":         str(r["race_date"])[:10],
                "y":         round(r[f"{disc}_pct_behind"] * 100, 2),
                "race_name": r["race_title"],
                "race_id":   r["race_id"],
            }
            for r in times_data
            if r[f"{disc}_pct_behind"] is not None
        ]
    return result


def _build_rankings_charts(rankings_data):
    """Build per-discipline ranking history for world and national charts."""
    world    = {}
    national = {}
    for disc in ["overall", "swim", "bike", "run", "transition"]:
        prev_world = prev_nat = None
        world_pts  = []
        nat_pts    = []
        for r in rankings_data:
            wr  = r.get(f"world_{disc}")
            nat = r.get(f"national_{disc}")
            wr_chg  = (prev_world - wr)  if (wr  is not None and prev_world is not None) else None
            nat_chg = (prev_nat   - nat) if (nat is not None and prev_nat   is not None) else None
            prev_world = wr
            prev_nat   = nat
            base = {
                "x":        str(r["race_date"])[:10],
                "race_name": r["race_title"],
                "race_id":   r["race_id"],
                "status":    r.get("status"),
                "time_s":    r.get(f"{disc}_s"),   # None for transition
                "diff_s":    r.get(f"{disc}_diff"), # None for transition
                "t1_s":      r.get("t1_s"),
                "t2_s":      r.get("t2_s"),
                "t1_diff":   r.get("t1_diff"),
                "t2_diff":   r.get("t2_diff"),
            }
            if wr  is not None:
                world_pts.append({**base, "y": wr,  "rank_chg": wr_chg})
            if nat is not None:
                nat_pts.append(  {**base, "y": nat, "rank_chg": nat_chg})
        world[disc]    = world_pts
        national[disc] = nat_pts
    return world, national



@router.get("/athlete/{athlete_id}", response_class=HTMLResponse)
async def get_athlete(request: Request, athlete_id: int,
                      category: str = Query('elite'),
                      course:   str | None = Query(None)):
    info = queries.get_athlete_info(athlete_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Athlete {athlete_id} not found")

    # Resolve course (short vs long).
    # - Explicit `course=` from the user wins, with a fallback if the athlete
    #   has no data for that course.
    # - Otherwise pick the athlete's most recently active course: whichever has
    #   a race within 18 months wins; ties broken by most-recent-race date.
    #   Falls through to the first available course or 'short' if no data.
    available_courses = queries.get_athlete_courses(athlete_id)
    if course is not None:
        if available_courses and course not in available_courses:
            course = available_courses[0]
        elif not available_courses:
            course = 'short'
    else:
        last_per = queries.get_athlete_last_race_per_course(athlete_id)
        cutoff = date.today() - timedelta(days=int(18 * 30.44))
        active = {c: d for c, d in last_per.items() if d and d >= cutoff}
        candidates = active or last_per
        if candidates:
            course = max(candidates, key=lambda c: candidates[c])
        elif available_courses:
            course = available_courses[0]
        else:
            course = 'short'

    # Detect available categories *within the chosen course* and resolve the requested one
    available_categories = queries.get_athlete_categories(athlete_id, course=course)
    has_ratings = bool(available_categories)
    if has_ratings:
        if category not in available_categories:
            category = 'elite' if 'elite' in available_categories else available_categories[0]
    else:
        category = 'elite'  # default for race history query; no ratings will be shown

    current  = queries.get_athlete_current_ratings(athlete_id, category, course=course) if has_ratings else None
    changes  = queries.get_athlete_1yr_changes(athlete_id, category, course=course)     if has_ratings else None
    peaks    = queries.get_athlete_peak_ratings(athlete_id, category, course=course)    if has_ratings else None
    best     = queries.get_athlete_best_performances(athlete_id, category, course=course) if has_ratings else None
    stats    = queries.get_athlete_stats(athlete_id, category, course=course)
    notable_raw        = queries.get_athlete_notable_results(athlete_id)
    ag_notable_raw     = queries.get_athlete_ag_notable_results(athlete_id)
    long_notable_raw   = queries.get_athlete_long_course_notable_results(athlete_id)
    race_hist    = queries.get_athlete_race_history(athlete_id, category, course=course)
    # Mixed relay legs appear in the short-course elite history alongside
    # individual races (they update the same ratings, damped).
    if category == 'elite' and course == 'short':
        relay_hist = queries.get_athlete_relay_history(athlete_id)
        for r in relay_hist:
            r["is_relay"] = True
        if relay_hist:
            race_hist = sorted(race_hist + relay_hist,
                               key=lambda r: (r["race_date"], r["race_id"]), reverse=True)
    rating_hist  = queries.get_athlete_rating_history(athlete_id, category, course=course) if has_ratings else []
    times_data    = queries.get_athlete_times_data(athlete_id)                              if has_ratings else []
    ratings_data  = queries.get_athlete_ratings_data(athlete_id, category, course=course)  if has_ratings else []
    rankings_data = queries.get_athlete_rankings_data(athlete_id, category, course=course) if has_ratings else []

    # --- current ratings card ---
    current_ratings = {}
    if current:
        for disc in ["overall", "swim", "bike", "run", "transition"]:
            current_ratings[f"{disc}_rating"] = round(current[f"{disc}_rating"])

    # --- current rankings card (active athletes only) ---
    def _make_ranking(rank):
        if not rank or rank <= 0:
            return None
        n = int(rank)
        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return {"n": n, "suffix": suffix}

    # Compute active using category-filtered race_hist (already fetched above).
    # stats["last_race_date"] is category-agnostic so would incorrectly show rankings
    # for e.g. a retired elite who still races AG.
    _cat_last = race_hist[0]["race_date"] if race_hist else None
    _active   = bool(has_ratings and _cat_last and _cat_last >= (date.today() - timedelta(days=int(18 * 30.44))))

    current_rankings = {}
    peak_rankings    = {}
    if _active:
        active_ranks = queries.get_athlete_active_rankings(athlete_id, category, course=course)
        if active_ranks:
            for disc in ["overall", "swim", "bike", "run", "transition"]:
                current_rankings[f"world_{disc}"]    = _make_ranking(active_ranks.get(f"world_{disc}"))
                current_rankings[f"national_{disc}"] = _make_ranking(active_ranks.get(f"national_{disc}"))
    elif has_ratings:
        # Retired athletes: show their best-ever world ranking instead of a (now empty) current rank.
        peak_ranks = queries.get_athlete_peak_rankings(athlete_id, category, course=course)
        if peak_ranks:
            for disc in ["overall", "swim", "bike", "run", "transition"]:
                peak_rankings[f"world_{disc}"] = _make_ranking(peak_ranks.get(f"world_{disc}"))

    # --- current form card ---
    # Equivalent race-day splits from the form model (ptd_data/form.py): the
    # athlete's blended form mapped through the typical recent split for each
    # distance. Elite only, and only disciplines where the athlete is both
    # established (>= FORM_MIN_STARTS observed splits) and current (raced the
    # course within 18 months) - e.g. Blummenfelt gets long-course form but
    # his dormant short-course profile shows none.
    FORM_MIN_STARTS = 5
    current_form = None
    if category == 'elite':
        form = queries.get_athlete_form(athlete_id, course)
        refs = queries.get_form_reference_times(course)
        form_cutoff = date.today() - timedelta(days=int(18 * 30.44))
        if course == 'short':
            form_cols = [('Sprint', 'sprint'), ('Standard', 'standard')]
            form_discs = ('swim', 'run')
            legs = {('sprint', 'swim'): '750m', ('standard', 'swim'): '1500m',
                    ('sprint', 'run'): '5km', ('standard', 'run'): '10km'}
        else:
            form_cols = [('70.3', 'middle'), ('140.6', 'long')]
            form_discs = ('swim', 'bike', 'run')
            legs = {('middle', 'swim'): '1.9km', ('long', 'swim'): '3.8km',
                    ('middle', 'bike'): '90km', ('long', 'bike'): '180km',
                    ('middle', 'run'): '21.1km', ('long', 'run'): '42.2km'}
        form_rows = []
        as_of = None
        for disc in form_discs:
            f = form.get(disc)
            if not f or f['n_obs'] < FORM_MIN_STARTS or f['last_race_date'] < form_cutoff:
                continue
            cells = []
            for _, dist in form_cols:
                ref = refs.get((info['gender'], dist, disc))
                cells.append({
                    'leg':  legs[(dist, disc)],
                    'time': format_time(round(ref * math.exp(f['form_rel']))) if ref else '-',
                })
            form_rows.append({'label': disc.capitalize(), 'cells': cells})
            as_of = max(as_of, f['last_race_date']) if as_of else f['last_race_date']
        if form_rows:
            current_form = {'cols': [label for label, _ in form_cols],
                            'rows': form_rows, 'as_of': as_of}

    # --- 1yr changes card ---
    rating_changes_1yr = {}
    if changes:
        for disc in ["overall", "swim", "bike", "run", "transition"]:
            rating_changes_1yr[f"{disc}_change_1yr"] = format_1yr_rating_change(
                changes[f"{disc}_change_1yr"]
            )

    # --- peak ratings card ---
    rating_peaks = {}
    if peaks:
        for disc in ["overall", "swim", "bike", "run", "transition"]:
            rating_peaks[f"max_{disc}"]      = round(peaks[f"max_{disc}"]) if peaks[f"max_{disc}"] else 0
            rating_peaks[f"max_{disc}_race"] = peaks[f"max_{disc}_race"]

    # --- best performances card ---
    best_performances = {}
    if best:
        no_best = {"formatted_str": "-", "css_class": "no-best-performance"}
        for disc in ["overall", "swim", "bike", "run", "transition"]:
            change = best[f"{disc}_change"]
            best_performances[f"{disc}_change"] = format_rating_change(change) if change else no_best
            best_performances[f"{disc}_race"]   = best[f"{disc}_race"]

    # --- 1y sparklines per discipline ---
    # Inline SVG polyline normalised into a 100x28 viewBox. To stay consistent
    # with the "1y change" number, the sparkline window is anchored on the
    # same reference race that change uses: the most recent race at least 365
    # days old. We include that anchor as the first point so the line starts
    # at the same rating the change calculation starts from. If no race that
    # old exists, fall back to the full history.
    sparklines: dict = {}
    if rating_hist:
        cutoff = date.today() - timedelta(days=365)
        # rating_hist is DESC by race_date. Walk it newest -> oldest, keeping
        # races up to and including the first race at/before the cutoff.
        recent = []
        for r in rating_hist:
            recent.append(r)
            if r["race_date"] <= cutoff:
                break
        if len(recent) >= 2:
            chrono = list(reversed(recent))  # ASC: anchor first, current last
            n = len(chrono)
            W, H, PAD = 100.0, 28.0, 3.0
            for disc in ["overall", "swim", "bike", "run", "transition"]:
                vals = [r[f"{disc}_rating"] for r in chrono]
                lo, hi = min(vals), max(vals)
                span = (hi - lo) or 1.0
                pts = []
                for i, v in enumerate(vals):
                    x = (i / (n - 1)) * W
                    y = PAD + (1 - (v - lo) / span) * (H - 2 * PAD)
                    pts.append(f"{x:.1f},{y:.1f}")
                sparklines[disc] = " ".join(pts)

    # --- athlete dict: merge info + stats + computed fields expected by template ---
    race_starts = stats["race_starts"]
    wins        = stats["wins"]
    podiums     = stats["podiums"]
    active      = _active  # category-specific 18-month window, consistent with rankings
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
        "is_low_confidence": race_starts < LOW_CONF_STARTS,
    }
    if peaks and best:
        for disc in ["overall", "swim", "bike", "run", "transition"]:
            athlete_dict[f"max_{disc}_race_id"]       = peaks[f"max_{disc}_race_id"]
            athlete_dict[f"{disc}_increase_race_id"]  = best[f"{disc}_race_id"] or 0

    # --- notable results: three parallel streams (short-course elite, AG, long-course) ---
    notable_results      = _build_notable_results(notable_raw)
    ag_notable_results   = _build_notable_results(ag_notable_raw, tier_order=["ag_world_champs", "ag_continental_champs"])
    long_notable_results = _build_notable_results(
        long_notable_raw,
        tier_order=["im_world_champs", "im_703_world_champs",
                    "im", "t100", "im_703", "challenge"],
    )

    def _split_columns(results):
        # Split into two display columns balanced by visual height.
        # Height model (×2 scaled): label row ≈ 1 line + each race row ≈ 0.5 lines → 2 + ceil(races/4)
        heights = [2 + (len(r["races"]) + 3) // 4 for r in results]
        total = sum(heights)
        best_split, best_diff, cumulative = 1, float("inf"), 0
        for i, h in enumerate(heights):
            cumulative += h
            if abs(cumulative - total / 2) <= best_diff:
                best_diff = abs(cumulative - total / 2)
                best_split = i + 1
        return results[:best_split], results[best_split:]

    notable_col1,      notable_col2      = _split_columns(notable_results)
    ag_notable_col1,   ag_notable_col2   = _split_columns(ag_notable_results)
    long_notable_col1, long_notable_col2 = _split_columns(long_notable_results)

    # --- race history table ---
    # Fetch percentile thresholds once for this athlete's gender (cached per process).
    # Build a race_id→overall_std map here so the rating history table reuses the same
    # values rather than re-querying with a different formula.
    _gender = next((r["gender"] for r in race_hist if r.get("gender") in ("male", "female")), "male")
    _thresholds = queries.get_race_standard_thresholds(_gender, course=course)
    _std_map = {r["race_id"]: r.get("overall_std") for r in race_hist}

    def _std_class(std, disc="overall"):
        if std is None:
            return "beginner"
        t = _thresholds[disc]
        if std >= t["p95"]: return "expert"
        if std >= t["p85"]: return "advanced"
        if std >= t["p60"]: return "intermediate"
        if std >= t["p30"]: return "novice"
        return "beginner"

    def _fmt_race(r):
        return {
            "race_id":        r["race_id"],
            "event_id":       r["event_id"],
            "is_multi_stage": bool(r.get("is_multi_stage")),
            "is_relay":       bool(r.get("is_relay")),
            "leg_num":        r.get("leg_num"),
            "race_title":     r["race_title"],
            "race_date":      r["race_date"],
            "program":        r["program"],
            "position":       r["position"],
            "status":         r["status"],
            # Relays have no race_rankings standard; suppress the pill rather
            # than defaulting to "beginner".
            "standard_class": None if r.get("is_relay") else _std_class(r.get("overall_std")),
            "overall":        format_time(r["overall_s"]),
            "overall_behind": format_time_behind(r["overall_behind_s"]),
            "swim":           format_time(r["swim_s"]),
            "swim_behind":    format_time_behind(r["swim_behind_s"]),
            "swim_fastest":   r["swim_behind_s"] == 0 and (r["swim_s"] or 0) > 0,
            "t1":             format_time(r["t1_s"]),
            "t1_behind":      format_time_behind(r["t1_behind_s"]),
            "t1_fastest":     r["t1_behind_s"] == 0 and (r["t1_s"] or 0) > 0,
            "bike":           format_time(r["bike_s"]),
            "bike_behind":    format_time_behind(r["bike_behind_s"]),
            "bike_fastest":   r["bike_behind_s"] == 0 and (r["bike_s"] or 0) > 0,
            "t2":             format_time(r["t2_s"]),
            "t2_behind":      format_time_behind(r["t2_behind_s"]),
            "t2_fastest":     r["t2_behind_s"] == 0 and (r["t2_s"] or 0) > 0,
            "run":            format_time(r["run_s"]),
            "run_behind":     format_time_behind(r["run_behind_s"]),
            "run_fastest":    r["run_behind_s"] == 0 and (r["run_s"] or 0) > 0,
        }

    # Group ignored sub-races under their parent row.
    # Build sub-race map keyed by parent_race_id, then stitch together.
    _sub_map: dict = {}
    _main: list = []
    for r in race_hist:
        if r.get("is_ignored") and r.get("parent_race_id"):
            _sub_map.setdefault(r["parent_race_id"], []).append(_fmt_race(r))
        else:
            _main.append(r)

    # Patch standard_class on sub-races: ignored races have no ratings rows so
    # overall_std is NULL from the main query; fetch via one bulk query.
    _sub_ids = [sub["race_id"] for subs in _sub_map.values() for sub in subs]
    _sub_standards = queries.get_race_standards_bulk(_sub_ids) if _sub_ids else {}
    for subs in _sub_map.values():
        for sub in subs:
            std = _sub_standards.get(sub["race_id"], {}).get("overall")
            sub["standard_class"] = _std_class(std)

    race_history = []
    for r in _main:
        entry = _fmt_race(r)
        subs = list(reversed(_sub_map.get(r["race_id"], [])))
        # A sub-race is a true stage (semifinal/final/heat/...) only when its
        # parent is a multi-stage rollup row. Subset sub-events bundled in
        # under a parent (e.g. national champs results extracted from a
        # continental cup, or AG results pulled from a combined elite+AG
        # field) shouldn't be numbered "Stage N".
        stage_counter = 0
        parent_is_multi_stage = bool(entry.get("is_multi_stage"))
        for sub in subs:
            is_stage = parent_is_multi_stage
            sub["is_stage"] = is_stage
            if is_stage:
                stage_counter += 1
                sub["stage_num"] = stage_counter
        entry["sub_races"] = subs
        race_history.append(entry)

    # --- rating history table ---
    rating_history = [
        {
            "race_id":           r["race_id"],
            "race_date":         r["race_date"],
            "race_title":        r["race_title"],
            "race_program":      r["race_program"],
            "position":          r["position"],
            "status":            r["status"],
            "standard_class":    _std_class(_std_map.get(r["race_id"])),
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

    # --- upcoming races with predictions ---
    START_RATING = 1500
    upcoming_raw = queries.get_athlete_upcoming_races(athlete_id)
    upcoming_races = []
    if upcoming_raw:
        models = queries.get_prediction_models()
        disc_col = {'overall': 'overall_rating', 'swim': 'swim_rating',
                    'bike': 'bike_rating', 'run': 'run_rating'}
        _upcoming_ids = [r['race_id'] for r in upcoming_raw]
        _entries_by_race   = queries.get_upcoming_race_entries_bulk(_upcoming_ids)
        _distance_by_race  = queries.get_upcoming_race_distance_types_bulk(_upcoming_ids)
        _standards_by_race = queries.get_upcoming_race_standards_bulk(_upcoming_ids)
        for race in upcoming_raw:
            entries  = _entries_by_race.get(race['race_id'], [])
            distance = _distance_by_race.get(race['race_id'])

            # Standard pill classification
            std_class = None
            standards = _standards_by_race.get(race['race_id'], {})
            if standards and standards.get('overall'):
                t = queries.get_race_standard_thresholds(race['gender'])['overall']
                v = standards['overall']
                if   v >= t['p95']: std_class = 'expert'
                elif v >= t['p85']: std_class = 'advanced'
                elif v >= t['p60']: std_class = 'intermediate'
                elif v >= t['p30']: std_class = 'novice'
                else:               std_class = 'beginner'

            pred_pos, splits, behinds = None, {}, {}
            if distance and models:
                overall_ratings = {e['athlete_id']: e['overall_rating'] or START_RATING for e in entries}
                my_overall = overall_ratings.get(athlete_id, START_RATING)
                pred_pos   = sum(1 for r in overall_ratings.values() if r > my_overall) + 1

                # Use the same anchor logic as the race page so predictions on
                # an athlete's profile match those on /race/<id>. Previously
                # this recomputed inline with plain slope*rating+intercept and
                # drifted from the race page's pool-anchor+year-term version.
                target_year = race['race_date'].year if race.get('race_date') else None
                for disc in ['overall', 'swim', 'bike', 'run']:
                    m = models.get((race['gender'], distance, disc))
                    if not m:
                        continue
                    col = disc_col[disc]
                    field = {e['athlete_id']: e[col] or START_RATING for e in entries}
                    leader_rating = max(field.values())
                    anchor        = _anchor_time(field, leader_rating, distance, disc, m,
                                                 target_year=target_year)
                    my_rating     = field.get(athlete_id, START_RATING)
                    my_time       = anchor * (10 ** ((leader_rating - my_rating) / SCALE))
                    leader_time   = anchor  # at leader rating, ELO factor = 1
                    splits[disc]  = format_time(round(my_time))
                    diff = round(my_time) - round(leader_time)
                    behinds[disc] = 'fastest' if diff == 0 else format_time_behind(diff)

            upcoming_races.append({
                'race_id':    race['race_id'],
                'event_name': race['event_name'],
                'event_id':   race['event_id'],
                'prog_name':  race['prog_name'],
                'race_date':  race['race_date'],
                'country':    race['country'],
                'pred_pos':   pred_pos,
                'pred_overall':        splits.get('overall'),
                'pred_overall_behind': behinds.get('overall'),
                'pred_swim':           splits.get('swim'),
                'pred_swim_behind':    behinds.get('swim'),
                'pred_bike':           splits.get('bike'),
                'pred_bike_behind':    behinds.get('bike'),
                'pred_run':            splits.get('run'),
                'pred_run_behind':     behinds.get('run'),
                'has_pred':     bool(splits),
                'std_class':    std_class,
            })

    # --- charts ---
    pct_behind      = _build_pct_behind_chart(times_data) if has_ratings else None
    ratings_chart   = _build_ratings_chart(ratings_data)  if has_ratings else None
    world_rankings_charts, national_rankings_charts = (
        _build_rankings_charts(rankings_data) if has_ratings else ({}, {})
    )

    # --- mode switcher summaries: elite-short | elite-long | ag -------------
    # Each entry: {label, course, category, overall_rating, world_overall, key}
    # key is the URL-safe identifier used by the switcher (matches active_mode).
    mode_summaries = []
    active_mode = None
    cutoff_active = date.today() - timedelta(days=int(18 * 30.44))

    def _summary(c, cat, label, key):
        cr = queries.get_athlete_current_ratings(athlete_id, cat, course=c)
        if not cr:
            return None
        h = queries.get_athlete_race_history(athlete_id, cat, course=c)
        last = h[0]["race_date"] if h else None
        rank = None
        if last and last >= cutoff_active:
            ar = queries.get_athlete_active_rankings(athlete_id, cat, course=c)
            if ar:
                rank = _make_ranking(ar.get("world_overall"))
        return {
            "key":            key,
            "label":          label,
            "course":         c,
            "category":       cat,
            "overall_rating": round(cr["overall_rating"]),
            "world_overall":  rank,
        }

    # Elite entries for each course the athlete has ratings in
    for c, lbl in (('short', 'Short Course'), ('long', 'Long Course')):
        if c not in available_courses:
            continue
        c_cats = queries.get_athlete_categories(athlete_id, course=c)
        if 'elite' not in c_cats:
            continue
        s = _summary(c, 'elite', lbl, f'elite-{c}')
        if s:
            mode_summaries.append(s)

    # AG entry: pick whichever course has AG results (prefer current course, else short, else long)
    ag_course = None
    for c in (course, 'short', 'long'):
        if c in available_courses and 'ag' in queries.get_athlete_categories(athlete_id, course=c):
            ag_course = c
            break
    if ag_course:
        s = _summary(ag_course, 'ag', 'Age Group', 'ag')
        if s:
            mode_summaries.append(s)

    # Resolve active_mode from current (category, course)
    if category == 'ag':
        active_mode = 'ag'
    else:
        active_mode = f'elite-{course}'

    return templates.TemplateResponse("athlete.html", {
        "request":        request,
        "active_page":    "athletes",
        "athlete":        athlete_dict,
        "has_ratings":          has_ratings,
        "show_charts":          has_ratings and race_starts > 1,
        "show_rankings":        bool(current_rankings) or bool(peak_rankings),
        "is_active_athlete":    _active,
        "category":             category,
        "course":               course,
        "available_courses":    available_courses,
        "mode_summaries":       mode_summaries,
        "active_mode":          active_mode,
        "has_elite":            'elite' in available_categories,
        "has_ag":               'ag' in available_categories,
        "notable_col1":        notable_col1,
        "notable_col2":        notable_col2,
        "ag_notable_col1":     ag_notable_col1,
        "ag_notable_col2":     ag_notable_col2,
        "long_notable_col1":   long_notable_col1,
        "long_notable_col2":   long_notable_col2,
        "current_form":        current_form,
        "current_ratings":     current_ratings,
        "current_rankings":    current_rankings,
        "peak_rankings":       peak_rankings,
        "rating_changes_1yr":  rating_changes_1yr,
        "rating_peaks":        rating_peaks,
        "best_performances":   best_performances,
        "sparklines":          sparklines,
        "nationality_history": queries.get_athlete_nationality_history(athlete_id),
        "upcoming_races":      upcoming_races,
        "rivals":              queries.get_athlete_rivals(athlete_id, category, course) if has_ratings else [],
        "race_history":        race_history,
        "rating_history":      rating_history,
        "ratings_chart":           ratings_chart,
        "pct_behind_chart":        pct_behind,
        "world_rankings_chart":    world_rankings_charts,
        "national_rankings_chart": national_rankings_charts,
    })
