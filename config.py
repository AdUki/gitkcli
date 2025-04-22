"""
Configuration settings for GitkCLI
"""

# Default CLI arguments
DEFAULT_ARGS = {
    'all': True,          # Show all branches by default
    'max_count': None,    # No limit by default
    'merges': False,      # Include merge commits
    'no_merges': False,   # Don't exclude merge commits
    'first_parent': False # Don't follow only first parent
}

# Graph display settings
GRAPH_SETTINGS = {
    'enabled': True,      # Enable graph display
    'width': 15,          # Width allocated for commit graph
    'symbols': {
        'commit': '*',
        'merge': '+',
        'branch': '|',
        'turn': '/',
        'join': '\\'
    }
}

# UI settings
UI_SETTINGS = {
    'refresh_rate': 0.05,      # Refresh rate in seconds
    'page_size': None,         # Page size (None = auto based on screen size)
    'author_width': 15,        # Width for author column
    'date_format': '%Y-%m-%d', # Date format
    'show_refs': True,         # Show references
    'trim_message': True       # Trim message to fit screen
}
