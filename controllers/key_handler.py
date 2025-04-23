"""
Key handling functionality
"""
import curses

class KeyHandler:
    """Handles key input and mapping to commands"""
    
    @staticmethod
    def get_common_bindings():
        """
        Get common key bindings for all views
        
        Returns:
            dict: Mapping of key codes to command names
        """
        return {
            ord('q'): 'quit',
            ord('H'): 'help',
            curses.KEY_RESIZE: 'resize'
        }
    
    @staticmethod
    def get_navigation_bindings():
        """
        Get navigation key bindings
        
        Returns:
            dict: Mapping of key codes to command names
        """
        return {
            ord('j'): 'down',
            ord('k'): 'up',
            ord('h'): 'left',
            ord('l'): 'right',
            curses.KEY_DOWN: 'down',
            curses.KEY_UP: 'up',
            curses.KEY_LEFT: 'left',
            curses.KEY_RIGHT: 'right',
            ord('g'): 'top',
            ord('G'): 'bottom',
            ord('d'): 'page_down',
            ord('u'): 'page_up'
        }
        
    @staticmethod
    def get_commit_view_bindings():
        """
        Get key bindings specific to commit view
        
        Returns:
            dict: Mapping of key codes to command names
        """
        bindings = KeyHandler.get_common_bindings()
        bindings.update(KeyHandler.get_navigation_bindings())
        bindings.update({
            10: 'show_diff',     # Enter key
            ord('c'): 'copy_id',
            ord('r'): 'refresh',
            ord('f'): 'find'
        })
        return bindings
        
    @staticmethod
    def get_diff_view_bindings():
        """
        Get key bindings specific to diff view
        
        Returns:
            dict: Mapping of key codes to command names
        """
        bindings = KeyHandler.get_common_bindings()
        bindings.update(KeyHandler.get_navigation_bindings())
        bindings.update({
            10: 'back',          # Enter key
            ord('b'): 'blame'
        })
        return bindings
        
    @staticmethod
    def get_blame_view_bindings():
        """
        Get key bindings specific to blame view
        
        Returns:
            dict: Mapping of key codes to command names
        """
        bindings = KeyHandler.get_common_bindings()
        bindings.update(KeyHandler.get_navigation_bindings())
        bindings.update({
            10: 'back'           # Enter key
        })
        return bindings
        
    @staticmethod
    def get_help_view_bindings():
        """
        Get key bindings specific to help view
        
        Returns:
            dict: Mapping of key codes to command names
        """
        # Any key returns from help
        return {
            curses.KEY_RESIZE: 'resize'
        }
