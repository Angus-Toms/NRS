# Formatting functions for FastAPI routers
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
    
def format_rating(rating: float) -> float:
    # Float required as Jinja logic needs to do comparisons to determine class
    return round(rating, 1)

def format_rating_change(change: float) -> dict:
    """ 
    Format rating change to str and provide css-class based on cardinality
    """
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
            "formatted_str": f"▲{change:.1f}",
            "css_class": "rating-increase"
        }
        
    return {
        "formatted_str": f"▼{-change:.1f}",
        "css_class": "rating-decrease"
    }

def format_1yr_rating_change(change: float) -> dict:
    """
    Format 1 year rating change, different to standard formatting to catch zero changes
    """
    if change == 0:
        return {
            "formatted_str": "▲0 last year",
            "css_class": "neutral"
        }
    
    if change > 0:
        return {
            "formatted_str": f"▲{change:.1f} last year",
            "css_class": "positive"
        }
    
    return {
        "formatted_str": f"▼{-change:.1f} last year",
        "css_class": "negative"
    }