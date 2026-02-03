"""
View base class - window and view management
"""
import curses
import typing
from .context import UIContext


class View:
    """
    Base class for all views/windows in the application.

    Provides window management, resizing, header/footer items, and event handling.

    Attributes:
        id: Unique identifier for this view
        view_mode: Display mode ('fullscreen' or 'window')
        header_item: Optional header item to display at top
        footer_item: Optional footer item to display at bottom
        is_popup: Whether this view is a popup (hides on outside click)
        dirty: Whether view needs redrawing
        resized: Whether view was resized
        resize_mode: Current resize mode ('m' for move, 'e'/'w'/'s' for edges)
        win: Curses window object
        x, y: Content area position within window
        width, height: Content area dimensions
        fixed_x, fixed_y, fixed_width, fixed_height: Fixed dimensions for window mode
    """

    def __init__(self, id: str, view_mode: str = 'fullscreen',
                 x: typing.Optional[int] = None, y: typing.Optional[int] = None,
                 height: typing.Optional[int] = None, width: typing.Optional[int] = None,
                 ui_context: UIContext = None):
        """
        Initialize a view.

        Args:
            id: Unique identifier for this view
            view_mode: Display mode ('fullscreen' or 'window')
            x: Fixed x position for window mode
            y: Fixed y position for window mode
            height: Fixed height for window mode
            width: Fixed width for window mode
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        self._ui_context = ui_context
        self.id: str = id
        self.view_mode: str = view_mode
        self.header_item: typing.Any = None
        self.footer_item: typing.Any = None
        self.is_popup: bool = False

        # coordinates and sizes when view is 'window'
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width

        self.dirty: bool = True
        self.resized: bool = False
        self.resize_mode: str = ''

        height, width, y, x = self._calculate_dimensions()
        self.win = curses.newwin(height, width, y, x)

        self._ui_context.screen.add_view(id, self)

    def _calculate_dimensions(self, lines=None, cols=None):
        """
        Calculate window dimensions based on view mode and screen size.

        Args:
            lines: Screen height (defaults to current screen height)
            cols: Screen width (defaults to current screen width)

        Returns:
            Tuple of (height, width, y, x) for the window
        """
        if lines is None or cols is None:
            lines, cols = self._ui_context.screen.getmaxyx()

        # fullscreen dimensions
        win_height = lines
        win_width = cols
        win_y = 0
        win_x = 0

        if self.view_mode == 'window':
            win_height = min(lines, self.fixed_height if self.fixed_height else int(lines / 2))
            win_width = min(cols, self.fixed_width if self.fixed_width else int(cols / 2))
            win_y = min(lines - win_height, int((lines - win_height) / 2) if self.fixed_y is None else self.fixed_y)
            win_x = min(cols - win_width, int((cols - win_width) / 2) if self.fixed_x is None else self.fixed_x)

        self.y = 0
        self.x = 0
        self.width = win_width
        self.height = win_height

        if self.header_item or self.view_mode == 'window':
            # substract header or window "box"
            self.height -= 1
            self.y += 1

        if self.footer_item or self.view_mode == 'window':
            # substract footer or window "box"
            self.height -= 1

        if self.view_mode == 'window':
            # substract window "box" width
            self.x += 1
            self.width -= 2

        return win_height, win_width, win_y, win_x

    def get_rect(self):
        """
        Get the window rectangle.

        Returns:
            Tuple of (y, x, y+h, x+w)
        """
        y, x = self.win.getbegyx()
        h, w = self.win.getmaxyx()
        return (y, x, y + h, x + w)

    def set_header_item(self, item):
        """
        Set the header item and recalculate dimensions.

        Args:
            item: Header item to display
        """
        self.header_item = item
        self._calculate_dimensions()

    def set_footer_item(self, item):
        """
        Set the footer item and recalculate dimensions.

        Args:
            item: Footer item to display
        """
        self.footer_item = item
        self._calculate_dimensions()

    def set_view_mode(self, view_mode: str):
        """
        Change the view mode (fullscreen, window, left, right, top, bottom).

        Args:
            view_mode: New view mode
        """
        if self.view_mode == view_mode:
            return
        stdscr_height, stdscr_width = self._ui_context.screen.getmaxyx()
        if view_mode == 'left':
            view_mode = 'window'
            self.set_dimensions(0, 0, stdscr_height, int(stdscr_width/2))
        if view_mode == 'right':
            view_mode = 'window'
            self.set_dimensions(int(stdscr_width/2), 0, stdscr_height, int(stdscr_width/2))
        if view_mode == 'top':
            view_mode = 'window'
            self.set_dimensions(0, 0, int(stdscr_height/2), stdscr_width)
        if view_mode == 'bottom':
            view_mode = 'window'
            self.set_dimensions(0, int(stdscr_height/2), int(stdscr_height/2), stdscr_width)
        self.view_mode = view_mode
        self.dirty = True
        if self.view_mode == 'window':
            self.resized = True
        height, width, y, x = self._calculate_dimensions(stdscr_height, stdscr_width)
        self.win.resize(height, width)
        self.win.mvwin(y, x)

    def toggle_window_mode(self):
        """Toggle between fullscreen and window mode."""
        self.set_view_mode('fullscreen' if self.view_mode == 'window' else 'window')

    def set_dimensions(self, x, y, height, width):
        """
        Set custom window dimensions.

        Args:
            x: X position
            y: Y position
            height: Window height
            width: Window width
        """
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width
        self.dirty = True
        self.resized = True
        height, width, y, x = self._calculate_dimensions()
        self.win.resize(height, width)
        self.win.mvwin(y, x)

    def start_resize(self, x: int, y: int) -> bool:
        """
        Start window resizing based on mouse position.

        Args:
            x: Mouse x coordinate
            y: Mouse y coordinate

        Returns:
            True if resize mode was started
        """
        self.resize_mode = ''
        if self.view_mode != 'window':
            return False
        win_y, win_x = self.win.getbegyx()
        if y <= win_y:
            self.resize_mode = 'm'
            return True
        if self.is_popup:
            return False
        win_height, win_width = self.win.getmaxyx()
        if x >= win_x + win_width - 1:
            self.resize_mode += 'e'
        if x <= win_x:
            self.resize_mode += 'w'
        if y >= win_y + win_height - 1:
            self.resize_mode += 's'
        return bool(self.resize_mode)

    def stop_resize(self) -> bool:
        """
        Stop window resizing.

        Returns:
            True if resize mode was active
        """
        if self.resize_mode:
            self.resize_mode = ''
            return True
        return False

    def handle_resize(self):
        """Handle window resizing based on mouse movement."""
        stdscr_height, stdscr_width = self._ui_context.screen.getmaxyx()
        win_y, win_x = self.win.getbegyx()
        win_height, win_width = self.win.getmaxyx()

        if 'm' in self.resize_mode:
            new_x = max(0, min(win_x + self._ui_context.mouse.mouse_rel_x, stdscr_width - win_width))
            new_y = max(0, min(win_y + self._ui_context.mouse.mouse_rel_y, stdscr_height - win_height))
            self.win.mvwin(new_y, new_x)
            self.dirty = True
            self.resized = True
        else:
            new_x = win_x
            new_y = win_y
            new_width = win_width
            new_height = win_height
            if 'w' in self.resize_mode:
                new_x = max(0, win_x + self._ui_context.mouse.mouse_rel_x)
                new_width = win_width - (new_x - win_x)
            if 'e' in self.resize_mode:
                new_width = max(5, min(stdscr_width - new_x, win_width + self._ui_context.mouse.mouse_rel_x))
            if 's' in self.resize_mode:
                new_height = max(5, min(stdscr_height - new_y, win_height + self._ui_context.mouse.mouse_rel_y))
            self.set_dimensions(new_x, new_y, new_height, new_width)

    def screen_size_changed(self, lines, cols):
        """
        Handle terminal resize.

        Args:
            lines: New screen height
            cols: New screen width
        """
        self.dirty = True
        self.resized = True
        height, width, y, x = self._calculate_dimensions(lines, cols)
        self.win.resize(height, width)
        self.win.mvwin(y, x)

    def redraw(self, force=False):
        """
        Redraw the view if dirty.

        Args:
            force: Force redraw even if not dirty

        Returns:
            True if view was redrawn
        """
        if self.dirty or force:
            self.dirty = False
            self.resized = False
            self.draw()
            return True
        else:
            return False

    def draw(self):
        """Draw the view (window box, header, footer)."""
        if self.view_mode == 'window':
            self.win.attrset(curses.color_pair(5 if self.is_active() else 18))
            self.win.box()

        # draw header
        if self.header_item:
            _, cols = self.win.getmaxyx()
            self.win.move(0, 0)
            self.header_item.draw_line(self.win, 0, cols, self.is_active(), False, False)

        # draw footer
        if self.footer_item:
            rows, cols = self.win.getmaxyx()
            self.win.move(rows - 1, 0)
            self.footer_item.draw_line(self.win, 0, cols, self.is_active(), False, False)

        self.win.refresh()
        # Log draw event (skip if this is the log view to avoid recursion)
        if self._ui_context.log and hasattr(self._ui_context.log, 'debug'):
            parent = self.get_parent()
            log_view = getattr(self._ui_context.log, 'view', None)
            if self != log_view and parent != log_view:
                self._ui_context.log.debug(f'Draw view {self.id}')

    def on_activated(self):
        """Called when this view becomes active."""
        if self._ui_context.log and hasattr(self._ui_context.log, 'debug'):
            self._ui_context.log.debug(f'View {self.id} activated')

    def on_deactivated(self):
        """Called when this view is deactivated."""
        if self._ui_context.log and hasattr(self._ui_context.log, 'debug'):
            self._ui_context.log.debug(f'View {self.id} deactivated')

    def handle_mouse_input(self, event_type: str, x: int, y: int) -> bool:
        """
        Handle mouse input on this view.

        Args:
            event_type: Type of mouse event
            x: Mouse x coordinate
            y: Mouse y coordinate

        Returns:
            True if input was handled
        """
        if event_type == 'left-release':
            self.stop_resize()
        if event_type == 'left-move' and self.resize_mode:
            self.handle_resize()
            return True
        if self.win.enclose(self._ui_context.mouse.mouse_y, self._ui_context.mouse.mouse_x):
            if y == 0 and self.header_item and self.header_item.handle_mouse_input(event_type, x, y):
                if 'left-click' == event_type or 'double-click' == event_type:
                    self._ui_context.mouse.clicked_item = self.header_item
                return True
            if y == self.y + self.height - 1 and self.footer_item and self.footer_item.handle_mouse_input(event_type, x, y):
                if 'left-click' == event_type or 'double-click' == event_type:
                    self._ui_context.mouse.clicked_item = self.footer_item
                return True
            if event_type == 'left-click' and self.start_resize(self._ui_context.mouse.mouse_x, self._ui_context.mouse.mouse_y):
                return True
        elif self.is_popup and 'click' in event_type:
            self.hide()
            return True
        return False

    def handle_input(self, key) -> bool:
        """
        Handle keyboard input.

        Args:
            key: Curses key code

        Returns:
            True if input was handled
        """
        return False

    def get_parent(self):
        """
        Get the parent view in the view stack.

        Returns:
            Parent view or None
        """
        try:
            index = self._ui_context.screen.showed_views.index(self)
            if index > 0:
                return self._ui_context.screen.showed_views[index - 1]
        except ValueError:
            pass
        return None

    def is_active(self) -> bool:
        """
        Check if this view is the active view.

        Returns:
            True if this view is active
        """
        return len(self._ui_context.screen.showed_views) > 0 and self._ui_context.screen.showed_views[-1] == self

    def show(self):
        """Show this view (add to view stack and activate)."""
        if self.is_active():
            return
        prev_view = self._ui_context.screen.get_active_view()
        if self in self._ui_context.screen.showed_views:
            self._ui_context.screen.showed_views.remove(self)
        self._ui_context.screen.showed_views.append(self)
        self.dirty = True
        self.resized = True
        if prev_view:
            prev_view.on_deactivated()
        self.on_activated()

    def hide(self):
        """Hide this view (remove from view stack)."""
        if len(self._ui_context.screen.showed_views) > 0:
            if not self in self._ui_context.screen.showed_views:
                return
            deactivated = self._ui_context.screen.showed_views[-1] == self
            self._ui_context.screen.showed_views.remove(self)
            active_view = self._ui_context.screen.get_active_view()
            if active_view:
                active_view.dirty = True
                active_view.resized = True
            if deactivated:
                self.on_deactivated()
