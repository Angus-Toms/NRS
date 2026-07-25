# Formatting functions for FastAPI routers

# SVG chevron arrows - stroke-based so they scale cleanly with font size
# and align geometrically rather than relying on Unicode glyph metrics.
_SVG_UP   = ('<svg class="chg-arrow" viewBox="0 0 10 8" fill="none" '
             'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
             'stroke-linejoin="round" aria-hidden="true">'
             '<polyline points="1,6.5 5,1.5 9,6.5"/></svg>')
_SVG_DOWN = ('<svg class="chg-arrow" viewBox="0 0 10 8" fill="none" '
             'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
             'stroke-linejoin="round" aria-hidden="true">'
             '<polyline points="1,1.5 5,6.5 9,1.5"/></svg>')
def format_time(seconds: int) -> str:
    """Convert seconds to HH:MM:SS or MM:SS format.""" 
    if seconds == 0: return ""
       
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    else:
        return f"{mins:02d}:{secs:02d}"
 
def format_time_behind(seconds_behind: int) -> str:
    if seconds_behind is None:
        return ""

    if seconds_behind == 0:
        return ""
    
    time_fmt = format_time(seconds_behind)
    return f"+{time_fmt}"
    
def format_rating(rating):
    # Debut athletes (e.g. on an upcoming-race start list) have no rating yet;
    # pass None through so templates render a blank/dash rather than crashing.
    if rating is None:
        return None
    return int(round(rating))

def format_rating_change(change: float) -> dict:
    """
    Format rating change to str and provide css-class based on cardinality.
    `raw` is included so templates can expose the numeric value (e.g. via a
    data attribute) for sorting on change instead of value.
    """
    if change is None or change == float('-inf'):
        return {"formatted_str": "", "css_class": "no-data",     "raw": None}

    if change == 0:
        return {"formatted_str": "",                              "css_class": "rating-neutral",  "raw": 0}

    if change > 0:
        return {"formatted_str": f"{_SVG_UP}{int(round(change))}", "css_class": "rating-increase", "raw": change}

    return     {"formatted_str": f"{_SVG_DOWN}{int(round(-change))}", "css_class": "rating-decrease", "raw": change}

def format_1yr_rating_change(change: float) -> dict:
    """
    Format 1 year rating change, different to standard formatting to catch zero changes
    """
    if change is None:
        return {"formatted_str": "", "css_class": ""}

    if change == 0:
        return {
            "formatted_str": "",
            "css_class": ""
        }

    if change > 0:
        return {
            "formatted_str": f"{_SVG_UP}{int(round(change))}",
            "css_class": "positive"
        }

    return {
        "formatted_str": f"{_SVG_DOWN}{int(round(-change))}",
        "css_class": "negative"
    }

def format_course_conditions(raw):
    """Format stored course conditions (queries.get_race_course_conditions)
    for display: disc -> {formatted: ±mm:ss, category}. diff_s is positive
    when the course ran faster than predicted, so it renders with a minus."""
    out = {}
    for disc, v in raw.items():
        sign = '-' if v["diff_s"] >= 0 else '+'
        mins, secs = divmod(abs(round(v["diff_s"])), 60)
        out[disc] = {"formatted": f"{sign}{mins:02d}:{secs:02d}", "category": v["category"]}
    return out
