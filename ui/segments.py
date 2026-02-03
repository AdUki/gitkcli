"""
Segment components - building blocks for composite items
"""
from typing import Callable
from .context import UIContext


class Segment:
    """
    Base class for segments that can be composed into SegmentedListItems.

    Segments are horizontal components that can be combined to create
    complex list items with multiple interactive regions.
    """

    def __init__(self, ui_context: UIContext):
        """
        Initialize a segment.

        Args:
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        self._ui_context = ui_context

    def get_text(self) -> str:
        """
        Get the text content of this segment.

        Returns:
            Text content, empty string by default
        """
        return ''

    def set_text(self, txt: str):
        """
        Set the text content of this segment.

        Args:
            txt: New text content
        """
        pass

    def draw(self, win, offset: int, width: int, selected: bool, matched: bool, marked: bool) -> int:
        """
        Draw this segment to a curses window.

        Args:
            win: Curses window to draw to
            offset: Horizontal offset within this segment's text
            width: Maximum width available
            selected: Whether parent item is selected
            matched: Whether parent item matches search
            marked: Whether parent item is marked

        Returns:
            Number of characters actually drawn
        """
        return 0

    def handle_mouse_input(self, event_type: str, x: int, y: int) -> bool:
        """
        Handle mouse input on this segment.

        Args:
            event_type: Type of mouse event
            x: Mouse x coordinate relative to this segment
            y: Mouse y coordinate

        Returns:
            True if input was handled, False otherwise
        """
        return False


class FillerSegment(Segment):
    """
    A segment that expands to fill remaining space.

    Used to push segments to the right or create spacing between segments.
    """

    def __init__(self, ui_context: UIContext):
        """
        Initialize a filler segment.

        Args:
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        super().__init__(ui_context)


class TextSegment(Segment):
    """
    A simple text segment with color.

    Attributes:
        txt: Text content to display
        color: Color code for rendering
    """

    def __init__(self, txt: str, color: int = 1, ui_context: UIContext = None):
        """
        Initialize a text segment.

        Args:
            txt: Text content to display
            color: Color code for rendering
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        super().__init__(ui_context)
        self.txt = txt
        self.color = color

    def get_text(self) -> str:
        """Get the text content of this segment."""
        return self.txt

    def set_text(self, txt: str):
        """Set the text content of this segment."""
        self.txt = txt

    def draw(self, win, offset: int, width: int, selected: bool, matched: bool, marked: bool) -> int:
        """
        Draw this text segment.

        Args:
            win: Curses window to draw to
            offset: Horizontal offset within this segment's text
            width: Maximum width available
            selected: Whether parent item is selected
            matched: Whether parent item matches search
            marked: Whether parent item is marked

        Returns:
            Number of characters actually drawn
        """
        visible_txt = self.get_text()[offset:width]
        win.addstr(visible_txt, self._ui_context.screen.color(self.color, selected, marked, matched))
        return len(visible_txt)


class ButtonSegment(TextSegment):
    """
    A clickable button segment.

    Shows visual feedback when pressed and executes a callback when released.

    Attributes:
        callback: Function to call when button is clicked
        is_pressed: Whether button is currently pressed
    """

    def __init__(self, txt: str, callback: Callable, color: int = 1, ui_context: UIContext = None):
        """
        Initialize a button segment.

        Args:
            txt: Button text
            callback: Function to call when clicked (should return bool)
            color: Color code for rendering
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        super().__init__(txt, color, ui_context)
        self.callback = callback
        self.is_pressed = False

    def handle_mouse_input(self, event_type: str, x: int, y: int) -> bool:
        """
        Handle mouse input - track press/release state.

        Args:
            event_type: Type of mouse event
            x: Mouse x coordinate
            y: Mouse y coordinate

        Returns:
            True if input was handled
        """
        if event_type == 'left-click' or event_type == 'double-click' or event_type == 'left-move-in':
            self.is_pressed = True
            return True

        if event_type == 'left-move-out':
            self.is_pressed = False
            return True

        if event_type == 'left-release':
            self.is_pressed = False
            return self.callback()
        else:
            return super().handle_mouse_input(event_type, x, y)

    def draw(self, win, offset: int, width: int, selected: bool, matched: bool, marked: bool) -> int:
        """
        Draw this button segment with pressed state.

        Args:
            win: Curses window to draw to
            offset: Horizontal offset within this segment's text
            width: Maximum width available
            selected: Whether parent item is selected
            matched: Whether parent item matches search
            marked: Whether parent item is marked

        Returns:
            Number of characters actually drawn
        """
        if self.is_pressed:
            visible_txt = self.get_text()[offset:width]
            dim = False
            bold = False
            if not selected:
                selected = True
            else:
                bold = True
                dim = True
            win.addstr(visible_txt, self._ui_context.screen.color(self.color, selected, marked, bold=bold, dim=dim))
            return len(visible_txt)
        return super().draw(win, offset, width, selected, matched, marked)


class ToggleSegment(TextSegment):
    """
    A toggle button segment that can be on/off.

    Shows toggled state visually and executes a callback when clicked.

    Attributes:
        callback: Function to call when toggled (receives self)
        toggled: Whether toggle is currently on
        enabled: Whether toggle is enabled for interaction
    """

    def __init__(self, txt: str, toggled: bool = False, callback: Callable = lambda val: None,
                 color: int = 1, ui_context: UIContext = None):
        """
        Initialize a toggle segment.

        Args:
            txt: Toggle text
            toggled: Initial toggle state
            callback: Function to call when toggled (receives self)
            color: Color code for rendering
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        super().__init__(txt, color, ui_context)
        self.callback = callback
        self.toggled = toggled
        self.enabled = True

    def toggle(self):
        """Toggle the state."""
        self.toggled = not self.toggled

    def handle_mouse_input(self, event_type: str, x: int, y: int) -> bool:
        """
        Handle mouse input - toggle on click.

        Args:
            event_type: Type of mouse event
            x: Mouse x coordinate
            y: Mouse y coordinate

        Returns:
            True if input was handled
        """
        if event_type == 'left-click' or event_type == 'double-click':
            self.toggle()
            self.callback(self)
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def draw(self, win, offset: int, width: int, selected: bool, matched: bool, marked: bool) -> int:
        """
        Draw this toggle segment showing toggled state.

        Args:
            win: Curses window to draw to
            offset: Horizontal offset within this segment's text
            width: Maximum width available
            selected: Whether parent item is selected
            matched: Whether parent item matches search
            marked: Whether parent item is marked

        Returns:
            Number of characters actually drawn
        """
        visible_txt = self.txt[offset:width]
        win.addstr(visible_txt, self._ui_context.screen.color(self.color, selected, self.toggled, dim=not self.enabled))
        return len(visible_txt)
