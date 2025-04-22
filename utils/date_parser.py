"""
Date parsing utilities
"""
import re
from datetime import datetime, timedelta

def parse_date(date_str):
    """
    Parse date string to timestamp
    
    Args:
        date_str (str): Date string to parse
        
    Returns:
        int: Unix timestamp
        
    Raises:
        ValueError: If date string couldn't be parsed
    """
    # Handle relative dates like "1 week ago", "2 days ago", etc.
    relative_match = re.match(r'(\d+)\s+(day|week|month|year)s?\s+ago', date_str, re.IGNORECASE)
    if relative_match:
        return _parse_relative_date(relative_match)
    
    # Try to parse as ISO format date: YYYY-MM-DD
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return int(dt.timestamp())
    except ValueError:
        pass
        
    # Try to parse with more formats
    formats = ['%Y-%m-%d %H:%M:%S', '%Y/%m/%d', '%d.%m.%Y']
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue
            
    raise ValueError(f"Couldn't parse date: {date_str}")

def _parse_relative_date(match):
    """
    Parse relative date from regex match
    
    Args:
        match: Regex match object
        
    Returns:
        int: Unix timestamp
    """
    num, unit = match.groups()
    num = int(num)
    now = datetime.now()
    
    if unit.lower() == 'day':
        delta = timedelta(days=num)
    elif unit.lower() == 'week':
        delta = timedelta(weeks=num)
    elif unit.lower() == 'month':
        delta = timedelta(days=num*30)  # Approximation
    elif unit.lower() == 'year':
        delta = timedelta(days=num*365)  # Approximation
        
    past_date = now - delta
    return int(past_date.timestamp())
