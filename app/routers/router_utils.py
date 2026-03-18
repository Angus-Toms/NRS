# Formatting functions for FastAPI routers

# SVG chevron arrows — stroke-based so they scale cleanly with font size
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
    
def format_rating(rating: float) -> int:
    return int(round(rating))

def format_rating_change(change: float) -> dict:
    """
    Format rating change to str and provide css-class based on cardinality
    """
    if change is None: return {
        "formatted_str": "",
        "css_class": "no-data"
    }

    # For races, returned when there is no split data for particular leg
    if change == float('-inf'): return {
        "formatted_str": "",
        "css_class": "no-data"
    }
    
    if change == 0: return {
        "formatted_str": "",
        "css_class": "rating-neutral"
    }
    
    if change > 0:
        return {
            "formatted_str": f"{_SVG_UP}{int(round(change))}",
            "css_class": "rating-increase"
        }

    return {
        "formatted_str": f"{_SVG_DOWN}{int(round(-change))}",
        "css_class": "rating-decrease"
    }

def format_1yr_rating_change(change: float) -> dict:
    """
    Format 1 year rating change, different to standard formatting to catch zero changes
    """
    if change == 0:
        return {
            "formatted_str": "",
            "css_class": ""
        }

    if change > 0:
        return {
            "formatted_str": f"{_SVG_UP}{change:.1f} last year",
            "css_class": "positive"
        }

    return {
        "formatted_str": f"{_SVG_DOWN}{-change:.1f} last year",
        "css_class": "negative"
    }