"""
UIContext - Dependency injection container for UI components
"""
from typing import Optional, Callable, Any


class UIContext:
    """
    Dependency injection container for UI components.

    Provides access to screen management, logging, mouse handling, and callbacks
    for context menus and clipboard operations without hard-coding dependencies.
    """

    def __init__(self, screen_manager, logger=None, mouse_handler=None):
        """
        Initialize UI context.

        Args:
            screen_manager: Object providing screen operations (color, getmaxyx, add_view, etc.)
            logger: Optional logger for debug/info messages
            mouse_handler: Optional mouse handler for reading mouse state
        """
        self.screen = screen_manager
        self.log = logger or self._default_logger()
        self.mouse = mouse_handler
        self._context_menu_handler: Optional[Callable] = None
        self._clipboard_handler: Optional[Callable] = None

    def set_context_menu_handler(self, handler: Callable):
        """
        Set the context menu handler callback.

        Args:
            handler: Function accepting (item, view_id=None) to show context menu
        """
        self._context_menu_handler = handler

    def set_clipboard_handler(self, handler: Callable):
        """
        Set the clipboard handler callback.

        Args:
            handler: Function accepting (text) to copy text to clipboard
        """
        self._clipboard_handler = handler

    def show_context_menu(self, item, view_id=None) -> bool:
        """
        Show context menu for an item.

        Args:
            item: The item to show context menu for
            view_id: Optional view ID context

        Returns:
            True if context menu was shown, False otherwise
        """
        if self._context_menu_handler:
            return self._context_menu_handler(item, view_id)
        return False

    def copy_to_clipboard(self, text: str):
        """
        Copy text to clipboard.

        Args:
            text: Text to copy to clipboard
        """
        if self._clipboard_handler:
            self._clipboard_handler(text)

    @staticmethod
    def _default_logger():
        """
        Provide a fallback logger for standalone usage.

        Returns:
            A basic logger instance
        """
        import logging
        logger = logging.getLogger('ui')
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.WARNING)
        return logger
