"""
Key handling functionality
"""
import curses

class KeyHandler:
    """Handles key input and mapping to commands"""
    
    @staticmethod
    def get_key_descriptions(view_type=None):
        """
        Get descriptions of key bindings for help display
        
        Args:
            view_type: Type of view to get descriptions for ('commit', 'diff', etc.)
            
        Returns:
            dict: Mapping of key names to descriptions
        """
        # Common key descriptions
        common_keys = {
            'q': 'Quit',
            'H': 'Show help',
            'j/DOWN': 'Move down',
            'k/UP': 'Move up',
            'h/LEFT': 'Scroll left',
            'l/RIGHT': 'Scroll right',
            'g': 'Go to top',
            'G': 'Go to bottom',
            'd/PgDn': 'Page down',
            'u/PgUp': 'Page up'
        }
        
        # View-specific key descriptions
        if view_type == 'commit':
            extra_keys = {
                'ENTER': 'Show diff for selected commit',
                'c': 'Copy commit ID to clipboard',
                'r': 'Refresh commit list',
                '/': 'Start search',
                'Tab': 'Cycle search types',
                'n': 'Next search result',
                'N': 'Previous search result',
                'ESC': 'Cancel search'
            }
            return {**common_keys, **extra_keys}
            
        elif view_type == 'diff':
            extra_keys = {
                'ENTER': 'Return to commit list',
                'b': 'Show origin of line at cursor',
                '/': 'Search in diff',
                'n': 'Next search result',
                'N': 'Previous search result',
                'ESC': 'Cancel search'
            }
            return {**common_keys, **extra_keys}
            
        # Default to common keys
        return common_keys
    
    @staticmethod
    def get_key_code(key_name):
        """
        Get the key code for a key name
        
        Args:
            key_name: Name of the key
            
        Returns:
            int: Key code
        """
        key_map = {
            'q': ord('q'),
            'H': ord('H'),
            'j': ord('j'),
            'k': ord('k'),
            'h': ord('h'),
            'l': ord('l'),
            'g': ord('g'),
            'G': ord('G'),
            'd': ord('d'),
            'u': ord('u'),
            'DOWN': curses.KEY_DOWN,
            'UP': curses.KEY_UP,
            'LEFT': curses.KEY_LEFT,
            'RIGHT': curses.KEY_RIGHT,
            'PgDn': curses.KEY_NPAGE,
            'PgUp': curses.KEY_PPAGE,
            'ENTER': 10,
            'ESC': 27,
            'Tab': 9,
            'c': ord('c'),
            'r': ord('r'),
            'b': ord('b'),
            '/': ord('/'),
            'n': ord('n'),
            'N': ord('N'),
            'RESIZE': curses.KEY_RESIZE,
            'BACKSPACE': 127,
            'DELETE': curses.KEY_DC
        }
        
        return key_map.get(key_name)
