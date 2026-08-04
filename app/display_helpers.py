"""Presentation helpers exposed to Jinja as globals.

Registered by main.py and by each router that needs them (routers build their own
Jinja2Templates instance, so globals have to be set per-instance). These live here
rather than in config.py so the data layer, which imports config for DB_PATH and
the WT API key, cannot reach template rendering.
"""
from markupsafe import Markup

from config import STATIC_BASE_URL


def flag(code, country="", cls=""):
    if not code:
        return ""
    cls_attr = "flag" + (f" {cls}" if cls else "")
    return Markup(
        f'<img src="{STATIC_BASE_URL}flags/{code}.svg" alt="{country or code}" '
        f'class="{cls_attr}" loading="lazy">'
    )


# Sanctioning-body acronyms that WT puts in every race title. A word-frequency
# pass over GSC queries scores all of them at 0-3 impressions, so in a <title>
# they only eat into the ~60 characters Google actually displays.
_TITLE_NOISE = frozenset({"itu", "etu", "atu", "astc", "otu", "patco", "camtri", "cism"})


def title_words(race_title):
    """Race title with the sanctioning-body acronyms dropped, for <title> use only.

    The visible H1 keeps the official name; this is purely to buy back characters
    in the search snippet. No attempt is made to shorten the names themselves:
    the words left in it (venue, year, "triathlon", the level) are the ones that
    get searched, and the ordering is WT's, which puts the venue last often enough
    that trimming from either end loses something that matters.
    """
    return " ".join(w for w in str(race_title).split() if w.lower() not in _TITLE_NOISE)


_SUB_LABEL = {"u23": "U23", "junior": "Junior", "youth": "Youth", "ag": "Age Group"}

# The prog_names common enough to be worth rephrasing gender-first, to match how
# people search ("men's results", not "elite men"). Elite is dropped: it matches
# no search query and costs 6 of the ~60 characters Google shows.
_CANONICAL_PROGS = {
    "elite men": "Men's",        "elite women": "Women's",
    "pro men": "Men's",          "pro women": "Women's",
    "u23 men": "Men's U23",      "u23 women": "Women's U23",
    "junior men": "Men's Junior", "junior women": "Women's Junior",
    "youth men": "Men's Youth",  "youth women": "Women's Youth",
}


def program_label(race):
    """Which program of an event this page is, e.g. "Men's U23" or "18-19 Male AG Sprint".

    Every program of an event needs a distinguishable label or their titles collide
    and compete for the same query. Deriving one from gender and category is not
    enough on two counts: category only ever holds elite or ag, so junior/U23/youth
    programs all look elite through it, and an event can run several programs that
    share a gender and category anyway (age-group brackets, heats and finals).

    So prefer prog_name, which is unique within an event and already readable, and
    only rephrase the handful of forms that recur across every event. Falls back to
    gender plus category for the rare row with no prog_name.
    """
    prog = str(race.get("prog_name") or "").strip()
    if prog:
        return _CANONICAL_PROGS.get(prog.lower(), prog)

    gender = {"male": "Men's", "female": "Women's"}.get(race["gender"], "Mixed")
    label = _SUB_LABEL.get(race.get("sub_category") or race.get("category"))
    return f"{gender} {label}" if label else gender
