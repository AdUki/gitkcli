"""
ListView - scrollable list view with items
"""
import curses
import typing
import re
from .view import View
from .context import UIContext

# Constants for keyboard shortcuts
HORIZONTAL_OFFSET_JUMP = 10


def KEY_CTRL(c):
    """Get control key code for character."""
    return ord(c) & 0x1f


class ListView(View):
    """
    A scrollable list view that displays items.

    Supports vertical and horizontal scrolling, selection, searching,
    and keyboard/mouse navigation.

    Attributes:
        items: List of items to display
        autoscroll: Whether to automatically scroll to bottom when items are added
    """

    def __init__(self, id: str, view_mode: str = 'fullscreen',
                 x: typing.Optional[int] = None, y: typing.Optional[int] = None,
                 height: typing.Optional[int] = None, width: typing.Optional[int] = None,
                 ui_context: UIContext = None):
        """
        Initialize a list view.

        Args:
            id: Unique identifier for this view
            view_mode: Display mode ('fullscreen' or 'window')
            x: Fixed x position for window mode
            y: Fixed y position for window mode
            height: Fixed height for window mode
            width: Fixed width for window mode
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        super().__init__(id, view_mode, x, y, height, width, ui_context)
        self.items = []
        self._selected: int = 0
        self._offset_y: int = 0
        self._offset_x: int = 0
        self.autoscroll: bool = False
        self._search_dialog: typing.Optional[typing.Any] = None

    def toggle_autoscroll(self):
        """Toggle automatic scrolling to bottom."""
        self.autoscroll = not self.autoscroll

    def set_search_dialog(self, search_dialog):
        """
        Set the search dialog for this list view.

        Args:
            search_dialog: SearchDialogPopup instance
        """
        self._search_dialog = search_dialog
        self._search_dialog.parent_list_view = self

    def copy_text_to_clipboard(self):
        """Copy all items' text to clipboard."""
        text = "\n".join(item.get_text() for item in self.items)
        if text:
            self._ui_context.copy_to_clipboard(text)

    def copy_text_range_to_clipboard(self, to_item):
        """
        Copy a range of items to clipboard.

        Args:
            to_item: End item for the range
        """
        text = ""
        found = False
        for i, item in enumerate(self.items):
            if not found and item == to_item:
                found = True
            if found or i >= self._selected:
                text += "\n" + item.get_text()
            if found and i >= self._selected:
                break
        self._ui_context.copy_to_clipboard(text)

    def append(self, item):
        """
        Add item to end of list.

        Args:
            item: Item to append
        """
        self.items.append(item)
        if len(self.items) - self._offset_y < self.height:
            self.dirty = True
        if self.autoscroll:
            self._offset_y = max(0, len(self.items) - self.height)

    def insert(self, item, position=None):
        """
        Insert item at position or selected position.

        Args:
            item: Item to insert
            position: Optional position to insert at (defaults to selected position)
        """
        pos = position if position is not None else self._selected
        self.items.insert(pos, item)
        if pos <= self._selected:
            self._selected += 1
        if pos <= self._offset_y:
            self._offset_y += 1
        self.dirty = True

    def clear(self):
        """Clear all items from the list."""
        if self._ui_context.log:
            self._ui_context.log.debug(f'Clear view {self.id}')
        self.items = []
        self.set_selected(0)
        self._offset_y = 0
        self._offset_x = 0
        self.dirty = True

    def set_selected(self, what: int | str | re.Pattern, visible_mode: str = 'center') -> bool:
        """
        Set the selected item.

        Args:
            what: Index, string to search for, or regex pattern to match
            visible_mode: How to position the selected item ('center', 'top', or 'bottom')

        Returns:
            True if selection was changed
        """
        new_index = None

        if isinstance(what, int):
            if (0 <= what < len(self.items)) or (what <= 0 and len(self.items) == 0):
                new_index = what
        elif isinstance(what, str):
            # FIX: Was incorrectly referencing Gitkcli.git_diff.items
            for i, item in enumerate(self.items):
                if what in item.get_text():
                    new_index = i
                    break
        elif isinstance(what, re.Pattern):
            # FIX: Was incorrectly referencing Gitkcli.git_diff.items
            for i, item in enumerate(self.items):
                if what.match(item.get_text()):
                    new_index = i
                    break

        if new_index is not None:
            if self._selected != new_index:

                # skip non-selectable items
                direction = 1 if new_index > self._selected else -1
                if 0 <= new_index < len(self.items) and not self.items[new_index].is_selectable:
                    for dir in [direction, -direction]:
                        i = new_index + dir
                        while 0 <= i < len(self.items) and i != self._selected:
                            if self.items[i].is_selectable:
                                new_index = i
                                break
                            i += dir
                    if not self.items[new_index].is_selectable:
                        return False

                self._selected = new_index
                self.dirty = True

                if self._offset_y <= self._selected < self._offset_y + self.height:
                    # do not change view offset when item is already visible
                    return True

                if visible_mode == 'center':
                    self._offset_y = max(0, min(self._selected - int(self.height / 2), len(self.items) - self.height))
                elif visible_mode == 'top':
                    self._offset_y = max(0, self._selected)
                elif visible_mode == 'bottom':
                    self._offset_y = max(0, self._selected - self.height + 1)
            return True

        return False

    def get_selected(self) -> typing.Any:
        """
        Get the currently selected item.

        Returns:
            Selected item or None
        """
        if 0 <= self._selected < len(self.items):
            return self.items[self._selected]
        else:
            return None

    def search(self, backward: bool = False, repeat: bool = False):
        """
        Search for items matching the search dialog criteria.

        Args:
            backward: Search backwards from current selection
            repeat: Wrap around to beginning/end when reaching the end/beginning
        """
        if not self._search_dialog:
            return

        ranges = []
        if not backward:
            ranges.append(range(self._selected + 1, len(self.items)))
            if repeat:
                ranges.append(range(0, self._selected + 1))
        else:
            ranges.append(range(self._selected - 1, -1, -1))
            if repeat:
                ranges.append(range(len(self.items) - 1, self._selected - 1, -1))

        for search_range in ranges:
            for i in search_range:
                if self._search_dialog.matches(self.items[i]):
                    self.set_selected(i)
                    return

    def handle_mouse_input(self, event_type: str, x: int, y: int) -> bool:
        """
        Handle mouse input on the list.

        Args:
            event_type: Type of mouse event
            x: Mouse x coordinate
            y: Mouse y coordinate

        Returns:
            True if input was handled
        """
        if event_type == 'wheel-up':
            self._offset_y -= 5
            if self._offset_y < 0:
                self._offset_y = 0
            return True
        if event_type == 'wheel-down':
            self._offset_y += 5
            if self._offset_y >= len(self.items) - self.height:
                self._offset_y = max(0, len(self.items) - self.height)
            return True

        if not self.resize_mode:
            view_x = x - self.x
            view_y = y - self.y
            index = self._offset_y + view_y

            if 0 <= view_y < self.height and 0 <= view_x < self.width and 0 <= index < len(self.items):
                selected = False
                if 'move' in event_type:
                    if self._selected == index:
                        return False  # do not redraw when hovering over same item
                if event_type == 'left-click' or event_type == 'double-click' or \
                   ('move' in event_type and self in self._ui_context.mouse.mouse_movement_capture):
                    if self.items[index].is_selectable:
                        self.set_selected(index)
                        selected = True
                item = self.items[index]
                handled = item.handle_mouse_input(event_type, view_x + self._offset_x, index)
                if handled and ('left-click' == event_type or 'double-click' == event_type):
                    self._ui_context.mouse.clicked_item = item
                if selected or handled:
                    return True

        return super().handle_mouse_input(event_type, x, y)

    def handle_input(self, key) -> bool:
        """
        Handle keyboard input.

        Supports:
        - Arrow keys/vim keys: Navigation
        - Page Up/Down: Scroll by page
        - Home/End: Jump to start/end
        - /: Open search dialog
        - n/N: Next/previous search match

        Args:
            key: Curses key code

        Returns:
            True if input was handled
        """
        if not self.items:
            return super().handle_input(key)

        selected_item = self.get_selected()
        if selected_item and selected_item.handle_input(key):
            self.dirty = True
            return True

        if key == curses.KEY_UP or key == ord('k'):
            self.set_selected(self._selected - 1, visible_mode='top')
        elif key == curses.KEY_DOWN or key == ord('j'):
            self.set_selected(self._selected + 1, visible_mode='bottom')
        elif key == curses.KEY_LEFT or key == ord('h'):
            if self._offset_x - HORIZONTAL_OFFSET_JUMP >= 0:
                self._offset_x -= HORIZONTAL_OFFSET_JUMP
            else:
                self._offset_x = 0
        elif key == curses.KEY_RIGHT or key == ord('l'):
            max_length = 0
            for i in range(self._offset_y, min(self._offset_y + self.height, len(self.items))):
                length = len(self.items[i].get_text())
                if length > max_length:
                    max_length = length
            if self._offset_x + self.width < max_length:
                self._offset_x += HORIZONTAL_OFFSET_JUMP
        elif key == curses.KEY_PPAGE or key == KEY_CTRL('b'):
            self._offset_y = max(0, self._offset_y - self.height)
            self.set_selected(max(0, self._selected - self.height))
        elif key == curses.KEY_NPAGE or key == KEY_CTRL('f'):
            self._offset_y = min(self._offset_y + self.height, max(0, len(self.items) - self.height))
            self.set_selected(min(self._selected + self.height, max(0, len(self.items) - 1)))
        elif key == curses.KEY_HOME or key == ord('g'):
            self.set_selected(0)
        elif key == curses.KEY_END or key == ord('G'):
            self.set_selected(max(0, len(self.items) - 1))
        elif key == ord('/'):
            if self._search_dialog:
                self._search_dialog.clear()
                self._search_dialog.show()
        elif key == ord('n'):
            self.search()
        elif key == ord('N'):
            self.search(backward=True)
        else:
            return super().handle_input(key)

        return True

    def draw(self):
        """Draw the list items and separators."""
        separator_items = []
        for i in range(0, min(self.height, len(self.items) - self._offset_y)):
            idx = i + self._offset_y
            item = self.items[idx]
            selected = idx == self._selected
            matched = self._search_dialog.matches(item) if self._search_dialog else False

            # curses throws exception if you want to write a character in bottom left corner
            width = self.width
            if i == self.height - 1:
                width -= 1

            if item.is_separator:
                separator_items.append((i, width))
            else:
                self.win.move(self.y + i, self.x)
                item.draw_line(self.win, self._offset_x, width, selected, matched, False)

        self.win.clrtobot()
        super().draw()

        if separator_items:
            color = 5 if self.is_active() else 16
            for pair in separator_items:
                i, width = pair
                if self.view_mode == 'window':
                    self.win.move(self.y + i, self.x-1)
                    self.win.addstr('├', self._ui_context.screen.color(color))
                    self.win.addstr('─' * width, self._ui_context.screen.color(color))
                    self.win.addstr('┤', self._ui_context.screen.color(color))
                else:
                    self.win.move(self.y + i, self.x)
                    self.win.addstr('─' * width, self._ui_context.screen.color(color))
            self.win.refresh()
