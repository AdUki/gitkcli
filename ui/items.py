"""
Standard item implementations - Item base class and concrete item types
"""
import curses
from .context import UIContext


class Item:
    """
    Base class for all list items that can be displayed and interacted with.

    Attributes:
        is_selectable: Whether this item can be selected by the user
        is_separator: Whether this item is a visual separator
    """

    def __init__(self, ui_context: UIContext):
        """
        Initialize an item.

        Args:
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        self._ui_context = ui_context
        self.is_selectable = True
        self.is_separator = False

    def get_text(self) -> str:
        """
        Get the text content of this item.

        Returns:
            Text content, empty string by default
        """
        return ''

    def copy_text_to_clipboard(self):
        """Copy this item's text to the clipboard."""
        self._ui_context.copy_to_clipboard(self.get_text())

    def set_text(self, txt: str):
        """
        Set the text content of this item.

        Args:
            txt: New text content
        """
        pass

    def draw_line(self, win, offset, width, selected, matched, marked):
        """
        Draw this item to a curses window.

        Args:
            win: Curses window to draw to
            offset: Horizontal scroll offset
            width: Available width for drawing
            selected: Whether this item is currently selected
            matched: Whether this item matches a search
            marked: Whether this item is marked
        """
        pass

    def handle_input(self, key) -> bool:
        """
        Handle keyboard input.

        Args:
            key: Curses key code

        Returns:
            True if input was handled, False otherwise
        """
        return False

    def handle_mouse_input(self, event_type: str, x: int, y: int) -> bool:
        """
        Handle mouse input.

        Args:
            event_type: Type of mouse event (click, double-click, right-click, etc.)
            x: Mouse x coordinate
            y: Mouse y coordinate

        Returns:
            True if input was handled, False otherwise
        """
        if event_type == 'right-click':
            return self._ui_context.show_context_menu(self)
        return False


class SeparatorItem(Item):
    """
    A non-selectable separator line in lists.

    Used to visually separate groups of items.
    """

    def __init__(self, ui_context: UIContext):
        """
        Initialize a separator item.

        Args:
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        super().__init__(ui_context)
        self.is_selectable = False
        self.is_separator = True


class SpacerListItem(Item):
    """
    An empty non-selectable item for spacing.

    Used to add vertical spacing between items.
    """

    def __init__(self, ui_context: UIContext):
        """
        Initialize a spacer item.

        Args:
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        super().__init__(ui_context)
        self.is_selectable = False

    def draw_line(self, win, offset, width, selected, matched, marked):
        """
        Draw an empty line (just clear to end of line).

        Args:
            win: Curses window to draw to
            offset: Horizontal scroll offset (unused)
            width: Available width for drawing (unused)
            selected: Whether this item is currently selected (unused)
            matched: Whether this item matches a search (unused)
            marked: Whether this item is marked (unused)
        """
        win.clrtoeol()


class TextListItem(Item):
    """
    A simple text list item with optional color and formatting.

    Attributes:
        txt: The text content
        color: Color code for rendering
        expand: Whether to expand to full width
        dim: Whether to render with dim attribute
    """

    def __init__(self, txt: str, color: int = 1, expand: bool = False, selectable: bool = True,
                 dim: bool = False, ui_context: UIContext = None):
        """
        Initialize a text list item.

        Args:
            txt: Text content to display
            color: Color code for rendering
            expand: Whether to expand line to full width
            selectable: Whether this item can be selected
            dim: Whether to render with dim attribute
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        super().__init__(ui_context)
        self.txt = txt
        self.color = color
        self.expand = expand
        self.is_selectable = selectable
        self.dim = dim

    def get_text(self) -> str:
        """Get the text content of this item."""
        return self.txt

    def set_text(self, txt: str):
        """Set the text content of this item."""
        self.txt = txt

    def draw_line(self, win, offset, width, selected, matched, marked):
        """
        Draw this text item to a curses window.

        Args:
            win: Curses window to draw to
            offset: Horizontal scroll offset
            width: Available width for drawing
            selected: Whether this item is currently selected
            matched: Whether this item matches a search
            marked: Whether this item is marked
        """
        line = self.get_text()[offset:]
        clear = True
        if selected or marked or self.expand:
            line += ' ' * (width - len(line))
            clear = False
        if len(line) >= width:
            line = line[:width]
            clear = False

        win.addstr(line, self._ui_context.screen.color(self.color, selected, marked, matched, dim=self.dim))
        if clear:
            win.clrtoeol()


class UserInputListItem(Item):
    """
    A text input field item with cursor support.

    Supports keyboard editing with cursor positioning, backspace/delete,
    arrow keys, home/end, and printable characters.
    """

    def __init__(self, color: int = 1, ui_context: UIContext = None):
        """
        Initialize a user input item.

        Args:
            color: Color code for rendering
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        super().__init__(ui_context)
        self.txt = ''
        self.offset = 0
        self.cursor_pos = 0
        self.color = color

    def clear(self):
        """Clear the input text and reset cursor."""
        self.txt = ''
        self.offset = 0
        self.cursor_pos = 0

    def get_text(self) -> str:
        """Get the current input text."""
        return self.txt

    def set_text(self, txt: str):
        """
        Set the input text and move cursor to end.

        Args:
            txt: New text content
        """
        self.txt = txt
        self.offset = 0
        self.cursor_pos = len(txt)

    def handle_input(self, key) -> bool:
        """
        Handle keyboard input for text editing.

        Supports:
        - Backspace/Delete: Remove characters
        - Arrow keys: Move cursor
        - Home/End: Jump to start/end
        - Printable characters: Insert text

        Args:
            key: Curses key code

        Returns:
            True if input was handled
        """
        if key == curses.KEY_BACKSPACE or key == 127:  # Backspace
            if self.cursor_pos > 0:
                self.txt = self.txt[:self.cursor_pos-1] + self.txt[self.cursor_pos:]
                self.cursor_pos -= 1

        elif key == curses.KEY_DC:  # Delete key
            if self.cursor_pos < len(self.txt):
                self.txt = self.txt[:self.cursor_pos] + self.txt[self.cursor_pos+1:]

        elif key == curses.KEY_LEFT:  # Left arrow
            if self.cursor_pos > 0:
                self.cursor_pos -= 1

        elif key == curses.KEY_RIGHT:  # Right arrow
            if self.cursor_pos < len(self.txt):
                self.cursor_pos += 1

        elif key == curses.KEY_HOME:  # Home key
            self.cursor_pos = 0

        elif key == curses.KEY_END:  # End key
            self.cursor_pos = len(self.txt)

        elif 32 <= key <= 126:  # Printable characters
            self.txt = self.txt[:self.cursor_pos] + chr(key) + self.txt[self.cursor_pos:]
            self.cursor_pos += 1

        else:
            return super().handle_input(key)

        return True

    def handle_mouse_input(self, event_type: str, x: int, y: int) -> bool:
        """
        Handle mouse input - click to position cursor.

        Args:
            event_type: Type of mouse event
            x: Mouse x coordinate
            y: Mouse y coordinate

        Returns:
            True if input was handled
        """
        if event_type == 'left-click' or event_type == 'double-click':
            self.cursor_pos = x if x < len(self.txt) else len(self.txt)
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def draw_line(self, win, offset, width, selected, matched, marked):
        """
        Draw the input field with cursor.

        Args:
            win: Curses window to draw to
            offset: Horizontal scroll offset
            width: Available width for drawing
            selected: Whether this item is currently selected
            matched: Whether this item matches a search
            marked: Whether this item is marked
        """
        # TODO: update self.offset according to offset so that cursor is always visible

        left_txt = self.txt[self.offset:self.offset+self.cursor_pos]
        right_txt = self.txt[self.offset+self.cursor_pos:self.offset+width-1]

        win.addstr(left_txt, self._ui_context.screen.color(self.color, selected, marked, matched))
        win.addch(ord(' '), curses.A_REVERSE | curses.A_BLINK)
        win.addstr(right_txt, self._ui_context.screen.color(self.color, selected, marked, matched))
        win.addstr(' ' * (width - len(left_txt) - len(right_txt) - 1),
                   self._ui_context.screen.color(self.color, selected, marked, matched))
