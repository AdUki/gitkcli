"""The View base class and ListView.

A View is one curses window/panel in the screen deck; ListView adds the
scrollable list of items, selection, search, and copy. Views hold the App
struct (`self.app`, injected at construction) and reach the screen, sibling
views, and items through it. Imports are limited to the colour palette (Screen),
config helpers, and the item types these base classes instantiate directly.
"""

from __future__ import annotations

import curses
import re
import typing

from gitk.config import KEY_CTRL, copy_to_clipboard
from gitk.screen import Screen
from gitk.items import WindowTopBarItem, SpacerListItem, TextListItem

HORIZONTAL_OFFSET_JUMP = 1

# Neutral grey for the divider between split panes — fixed so the line never
# looks like it belongs to whichever pane happens to be focused.
SPLIT_DIVIDER_COLOR = 18

class View:

    def __init__(self, app, id:str,
                 view_mode:str = 'fullscreen',
                 x:typing.Optional[int] = None, y:typing.Optional[int] = None,
                 height:typing.Optional[int] = None, width:typing.Optional[int] = None):

        # The App struct, injected at construction. Views use `self.app.<x>`
        # to reach screen / sibling views / services.
        self.app = app

        self.id:str = id
        self.view_mode:str = view_mode
        self.header_item:typing.Any = None
        self.is_popup:bool = False

        # coordinates and sizes when view is 'window'
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width

        self.dirty:bool = True
        # When only the header line changed (e.g. a live counter in the title),
        # redraw just row 0 instead of the whole body. A full redraw subsumes it.
        self.header_dirty:bool = False
        self.resize_mode:str = ''
        
        height, width, y, x = self._calculate_dimensions()
        self.win = curses.newwin(height, width, y, x)
        # Each view is a panel in the screen's z-ordered deck. The panel library
        # composites overlapping windows (occlusion, vacated-region cleanup) for
        # us; we only mark content dirty and let update_panels() do the rest.
        # Hidden until show() raises it.
        self.panel = curses.panel.new_panel(self.win)
        self.panel.hide()

        self.app.screen.add_view(id, self)

    def split_border_sides(self):
        """Border lines this view draws while it is a tiled split pane, as a
        subset of {'left', 'right', 'bottom'} (the top row is always the title).
        Returns None when the view is not a split pane, meaning use a full box.

        Side-by-side: only the log (left) pane draws a right divider; the diff
        (right) pane is borderless. Stacked: both panes are borderless and the
        bottom pane's title bar doubles as the draggable divider.
        """
        if not (self.app.split_active() and self in (self.app.git_log, self.app.git_diff)):
            return None
        if self.app.split_mode == 'side' and self is self.app.git_log:
            return {'right'}
        return set()

    def _calculate_dimensions(self, lines = None, cols = None):
        if lines is None or cols is None:
            lines, cols = self.app.screen.getmaxyx()

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

        sides = self.split_border_sides()
        if sides is not None:
            # split pane: title row on top, plus only the requested thin lines
            self.height -= 1
            self.y += 1
            if 'bottom' in sides:
                self.height -= 1
            if 'left' in sides:
                self.x += 1
                self.width -= 1
            if 'right' in sides:
                self.width -= 1
            return win_height, win_width, win_y, win_x

        # Window-mode views (floating popups and floated main views) draw a full
        # box. Fullscreen main views are borderless apart from their title line.
        box = self.view_mode == 'window'

        if self.header_item or box:
            # substract header line or box top
            self.height -= 1
            self.y += 1

        if box:
            # substract box bottom
            self.height -= 1

        if box:
            # substract box sides
            self.x += 1
            self.width -= 2

        return win_height, win_width, win_y, win_x

    def _set_geometry(self, height, width, y, x):
        """Resize+reposition the window and mark it dirty. A panel resized in
        place does NOT re-expose what it shrinks away from (curses only uncovers
        the new, smaller footprint), so if it is shown we hide it first - which
        uncovers its full OLD footprint - then move to a valid origin, resize,
        move to the target, and restore it: to the top if it was the active view,
        otherwise back into stack order. The actual repaint is a single
        update_panels()/doupdate() per frame, so the hide/show is invisible."""
        was_top = self.is_active()
        shown = not self.panel.hidden()
        if shown:
            self.panel.hide()
        self.panel.move(0, 0)  # an origin valid for any size, before resizing
        self.win.resize(height, width)
        self.panel.move(y, x)
        if shown:
            self.panel.show()
            if was_top:
                self.panel.top()
            else:
                self.app.screen._restack()
        self.dirty = True

    def set_header_item(self, item):
        self.header_item = item
        item._view = self
        self._calculate_dimensions()

    def set_view_mode(self, view_mode:str):
        if self.view_mode == view_mode:
            return
        stdscr_height, stdscr_width = self.app.screen.getmaxyx()
        self.view_mode = view_mode
        height, width, y, x = self._calculate_dimensions(stdscr_height, stdscr_width)
        self._set_geometry(height, width, y, x)

    def set_tiled(self, x, y, height, width):
        """Place this view as a non-overlapping pane (used by split view)."""
        self.view_mode = 'window'
        self.set_dimensions(x, y, height, width)

    def set_fullscreen(self):
        self.set_view_mode('fullscreen')

    def toggle_window_mode(self):
        # In split view the log/diff panes are managed by the split layout;
        # toggling a pane "maximizes" it by leaving split view altogether.
        if self.app.split_active() and self in (self.app.git_log, self.app.git_diff):
            self.app.set_split_mode('off')
            return
        self.set_view_mode('fullscreen' if self.view_mode == 'window' else 'window')

    def set_dimensions(self, x, y, height, width):
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width
        height, width, y, x = self._calculate_dimensions()
        self._set_geometry(height, width, y, x)

    def _start_split_resize(self, x:int, y:int) -> bool:
        """Arm a drag of the split divider when the grab is on the shared edge."""
        win_y, win_x = self.win.getbegyx()
        win_height, win_width = self.win.getmaxyx()
        is_log = self is self.app.git_log
        if self.app.split_mode == 'side':
            # divider is the right edge of the log pane / left edge of the diff pane
            on_divider = (x >= win_x + win_width - 1) if is_log else (x <= win_x)
        else:  # stacked: there is no line, so the bottom pane's title bar is the grip
            on_divider = (not is_log) and (y <= win_y)
        if on_divider:
            self.resize_mode = 'split'
            return True
        return False

    def start_resize(self, x:int, y:int) -> bool:
        self.resize_mode = ''
        # Split panes are fixed in place; only the shared divider can be dragged.
        if self.app.split_active() and self in (self.app.git_log, self.app.git_diff):
            return self._start_split_resize(x, y)
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
        if self.resize_mode:
            self.resize_mode = ''
            return True
        return False

    def handle_resize(self):
        if self.resize_mode == 'split':
            lines, cols = self.app.screen.getmaxyx()
            if self.app.split_mode == 'side':
                ratio = self.app.mouse.screen_x / max(1, cols)
            else:
                ratio = self.app.mouse.screen_y / max(1, lines)
            self.app.split_ratio = min(0.85, max(0.15, ratio))
            self.app.apply_split_layout()
            return
        stdscr_height, stdscr_width = self.app.screen.getmaxyx()
        win_y, win_x = self.win.getbegyx()
        win_height, win_width = self.win.getmaxyx()

        if 'm' in self.resize_mode:
            new_x = max(0, min(win_x + self.app.mouse.rel_x, stdscr_width - win_width))
            new_y = max(0, min(win_y + self.app.mouse.rel_y, stdscr_height - win_height))
            if new_x == win_x and new_y == win_y:
                return
            self.panel.move(new_y, new_x)
            self.dirty = True
        else:
            new_x = win_x
            new_y = win_y
            new_width = win_width
            new_height = win_height
            if 'w' in self.resize_mode:
                new_x = max(0, win_x + self.app.mouse.rel_x)
                new_width = win_width - (new_x - win_x)
            if 'e' in self.resize_mode:
                new_width = max(5, min(stdscr_width - new_x, win_width + self.app.mouse.rel_x))
            if 's' in self.resize_mode:
                new_height = max(5, min(stdscr_height - new_y, win_height + self.app.mouse.rel_y))
            self.set_dimensions(new_x, new_y, new_height, new_width)

    def screen_size_changed(self, lines, cols):
        self.dirty = True
        height, width, y, x = self._calculate_dimensions(lines, cols)
        self.win.resize(height, width)
        self.panel.move(y, x)

    def redraw(self, force=False):
        # Draw content into the window buffer; the screen's update_panels() +
        # doupdate() pass composites it. force=True re-touches the whole window so
        # it is re-emitted even when its content is unchanged (used on full redraw).
        if self.dirty or force:
            self.dirty = False
            self.header_dirty = False
            if force:
                self.win.touchwin()
            self.draw()
        elif self.header_dirty:
            self.header_dirty = False
            self.draw_header(self.split_border_sides())

    def border_color(self):
        return Screen.color(5 if self.is_active() else 18)

    def draw(self):
        sides = self.split_border_sides()
        if sides is not None:
            # The divider between split panes belongs to neither pane, so it is
            # drawn in a fixed neutral colour (not the owning pane's active /
            # inactive border colour).
            self.win.attrset(Screen.color(SPLIT_DIVIDER_COLOR))
            h, w = self.win.getmaxyx()
            # Full height (row 0 included) so the divider runs the whole way up
            # between the two title bars, not just the body rows.
            if 'left' in sides:
                self.win.vline(0, 0, curses.ACS_VLINE, h)
            if 'right' in sides:
                self.win.vline(0, w - 1, curses.ACS_VLINE, h)
            if 'bottom' in sides:
                self.win.hline(h - 1, 0, curses.ACS_HLINE, w)
        elif self.view_mode == 'window':
            self.win.attrset(self.border_color())
            self.win.box()

        self.draw_header(sides)

        if self != self.app.log.view and self.get_parent() != self.app.log.view:
            self.app.log.debug(f'Draw view {self.id}')

    def draw_header(self, sides):
        """Draw only the header line (row 0). Called by the full draw() and, on
        its own, when header_dirty is set so a title change (e.g. a live counter)
        repaints without re-rendering the body."""
        if not self.header_item:
            return
        _, cols = self.win.getmaxyx()
        if self.is_popup:
            # New style: the title sits inset in the box's top border
            # (┌─ Title ───────┐), no banner and no [X]. Drawn in the box's
            # own colour (red for warning/error dialogs).
            title = self.header_item.get_text().strip()
            if title:
                label = f' {title} '[:max(0, cols - 4)]
                if label:
                    self.win.move(0, 2)
                    self.win.addstr(label, self.border_color() | curses.A_BOLD)
        else:
            # Rule-line title bar (main views). Columns [left, right) the
            # title may paint; the divider / box-corner columns are left for
            # the vline (split) or box corners (┌─ Title ──[X]─┐, floated).
            if isinstance(self.header_item, WindowTopBarItem) and hasattr(self, 'items'):
                current = self._selected + 1 if self.items else 0
                self.header_item.set_counter(current, len(self.items))
            left, right = 0, cols
            if sides is not None:
                if 'left' in sides:
                    left = 1
                if 'right' in sides:
                    right = cols - 1
            elif self.view_mode == 'window':
                left, right = 1, cols - 1
            self.win.move(0, left)
            self.header_item.draw_line(self.win, 0, right - left, self.is_active(), False, False)

    def on_activated(self):
        self.app.log.debug(f'View {self.id} activated')

    def on_deactivated(self):
        self.app.log.debug(f'View {self.id} deactivated')

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'left-release':
            self.stop_resize()
        if mouse.event_type == 'left-move' and self.resize_mode:
            self.handle_resize()
            return True
        if self.win.enclose(self.app.mouse.screen_y, self.app.mouse.screen_x):
            if mouse.y == 0 and self.header_item and self.header_item.handle_mouse_input(mouse):
                if 'left-click' == mouse.event_type or 'double-click' == mouse.event_type:
                    self.app.mouse.clicked_item = self.header_item
                return True
            if mouse.event_type == 'left-click' and self.start_resize(self.app.mouse.screen_x, self.app.mouse.screen_y):
                return True
        elif self.is_popup and 'click' in mouse.event_type:
            self.hide()
            return True
        return False

    def handle_input(self, keyboard) -> bool:
        return False

    def get_parent(self):
        try:
            index = self.app.screen.showed_views.index(self)
            if index > 0:
                return self.app.screen.showed_views[index - 1]
        except ValueError:
            pass
        return None
    
    def is_active(self) -> bool:
        return len(self.app.screen.showed_views) > 0 and self.app.screen.showed_views[-1] == self

    def show(self):
        if self.is_active():
            return
        prev_view = self.app.screen.get_active_view()
        if self in self.app.screen.showed_views:
            self.app.screen.showed_views.remove(self)
        self.app.screen.showed_views.append(self)
        self.panel.show()
        self.panel.top()
        self.dirty = True
        if prev_view:
            # The outgoing top view must repaint to drop its active border/title
            # colour - active state keys off z-order, not overlap with us.
            prev_view.dirty = True
            prev_view.on_deactivated()
        self.on_activated()

    def hide(self):
        if len(self.app.screen.showed_views) > 0:
            if not self in self.app.screen.showed_views:
                return
            deactivated = self.app.screen.showed_views[-1] == self
            # Hiding the panel uncovers whatever was underneath; update_panels()
            # repaints it for us, no manual footprint cleanup needed.
            self.panel.hide()
            self.app.screen.showed_views.remove(self)
            if deactivated:
                self.on_deactivated()
                # Repaint the newly-exposed top view with its active styling.
                new_active = self.app.screen.get_active_view()
                if new_active:
                    new_active.dirty = True

class ListView(View):
    def __init__(self, app, id:str, view_mode:str = 'fullscreen',
                 x:typing.Optional[int] = None, y:typing.Optional[int] = None,
                 height:typing.Optional[int] = None, width:typing.Optional[int] = None):

        super().__init__(app, id, view_mode, x, y, height, width)
        self.items = []
        self._selected:int = 0
        self._offset_y:int = 0
        self._offset_x:int = 0
        self.autoscroll:bool = False
        self._search_dialog:typing.Optional[SearchDialogPopup] = None

    def set_search_dialog(self, search_dialog:"SearchDialogPopup"):
        self._search_dialog = search_dialog
        self._search_dialog.parent_list_view = self

    def _resize_centered(self, height, width):
        """Resize to height x width and re-centre on screen. Used by popups that
        size themselves to their content; fixed_x/y = None centres it."""
        self.set_dimensions(None, None, height, width)

    def _focus_button_row(self, focus = 'first'):
        """Make only self._button_row navigable (Left/Right pick a button, Enter
        activates it) and select it. focus='last' defaults to the final button -
        used for destructive confirmations so a bare Enter lands on [Cancel]."""
        for item in self.items:
            item.is_selectable = False
        self._button_row.is_selectable = True
        (self._button_row.focus_last if focus == 'last' else self._button_row.reset_focus)()
        self._selected = len(self.items) - 1

    def _show_message_box(self, lines, button_row_item, focus = 'first'):
        """Lay out a content-sized popup and show it: a spacer, the message
        `lines` (each a str or (text, color) tuple, indented two spaces), a
        spacer, then the button row - the only navigable item. Sizes to the
        widest of the header, the lines and the button row, then centres."""
        self.clear()
        self.append(SpacerListItem())
        content = len(self.header_item.get_text())
        for line in lines:
            text, color = line if isinstance(line, tuple) else (line, 1)
            self.append(TextListItem('  ' + text, color, selectable = False))
            content = max(content, len(text) + 2)  # + 2 for the left indent
        self.append(SpacerListItem())
        self._button_row = button_row_item
        self.append(button_row_item)
        content = max(content, len(button_row_item.get_text()))
        self._focus_button_row(focus)
        # content + 2 (right margin so text doesn't touch the border) + 2 (box sides)
        self._resize_centered(len(self.items) + 2, max(40, content + 4))
        self.show()

    def copy_text_to_clipboard(self):
        text = "\n".join(item.get_text() for item in self.items)
        if text:
            copy_to_clipboard(text, self.app)

    def copy_text_range_to_clipboard(self, to_item):
        text = ""
        found = False
        for i, item in enumerate(self.items):
            if not found and item == to_item:
                found = True
            if found or i >= self._selected:
                text += "\n" + item.get_text()
            if found and i >= self._selected:
                break
        copy_to_clipboard(text, self.app)

    def append(self, item):
        """Add item to end of list"""
        item._view = self
        self.items.append(item)
        if len(self.items) - self._offset_y < self.height:
            self.dirty = True
        else:
            # The new row is off-screen, so the body need not be redrawn — but
            # the header's "[current/total]" counter changed, so request a cheap
            # header-only redraw to keep it current while items stream in.
            self.header_dirty = True
        if self.autoscroll:
            self._offset_y = max(0, len(self.items) - self.height)
        
    def clear(self):
        self.app.log.debug(f'Clear view {self.id}')
        self.items = []
        self.set_selected(0)
        self._offset_y = 0
        self._offset_x = 0
        self.dirty = True

    def set_selected(self, what:int|str|re.Pattern, visible_mode = 'center') -> bool:
        new_index = None

        if isinstance(what, int):
            if (0 <= what < len(self.items)) or (what <= 0 and len(self.items) == 0):
                new_index = what
        elif isinstance(what, (str, re.Pattern)):
            test = (lambda t: what in t) if isinstance(what, str) else (lambda t: what.match(t))
            for i, item in enumerate(self.app.git_diff.items):
                if test(item.get_text()):
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
        if 0 <= self._selected < len(self.items):
            return self.items[self._selected]
        else:
            return None

    def search(self, backward:bool = False, repeat:bool = False):
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

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'wheel-up':
            self._offset_y = max(0, self._offset_y - 5)
            return True
        if mouse.event_type == 'wheel-down':
            self._offset_y = min(self._offset_y + 5, max(0, len(self.items) - self.height))
            return True

        if not self.resize_mode:
            view_x = mouse.x - self.x
            view_y = mouse.y - self.y
            index = self._offset_y + view_y

            if 0 <= view_y < self.height and 0 <= view_x < self.width and 0 <= index < len(self.items):
                selected = False
                if 'move' in mouse.event_type:
                    if self._selected == index:
                        return False # do not redraw when hovering over same item
                if mouse.event_type == 'left-click' or mouse.event_type == 'double-click' or ('move' in mouse.event_type and self in self.app.mouse.movement_capture):
                    if self.items[index].is_selectable:
                        self.set_selected(index)
                        selected = True
                item = self.items[index]
                # hand the item its own coordinates, then restore the view-relative
                # ones so a fall-through to super() still sees the right position
                saved_x, saved_y = mouse.x, mouse.y
                mouse.x = view_x + self._offset_x
                mouse.y = index
                handled = item.handle_mouse_input(mouse)
                if handled and ('left-click' == mouse.event_type or 'double-click' == mouse.event_type):
                    self.app.mouse.clicked_item = item
                if selected or handled:
                    return True
                mouse.x, mouse.y = saved_x, saved_y

        return super().handle_mouse_input(mouse)

    def handle_input(self, keyboard):
        key = keyboard.key
        if not self.items:
            return super().handle_input(keyboard)

        selected_item = self.get_selected()
        if selected_item and selected_item.handle_input(keyboard):
            self.dirty = True
            return True

        if key == curses.KEY_UP or key == ord('k'):
            self.set_selected(self._selected - 1, visible_mode = 'top')
        elif key == curses.KEY_DOWN or key == ord('j'):
            self.set_selected(self._selected + 1, visible_mode = 'bottom')
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
            self.search(backward = True)
        else:
            return super().handle_input(keyboard)

        return True

    def draw(self):
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
            # Joins onto the neutral split divider use its colour, not the pane's.
            join = Screen.color(SPLIT_DIVIDER_COLOR)
            sides = self.split_border_sides()
            for pair in separator_items:
                i, width = pair
                if sides is not None:
                    # split pane: join only the borders that are actually drawn
                    if 'left' in sides:
                        self.win.move(self.y + i, self.x - 1)
                        self.win.addstr('├', join)
                    else:
                        self.win.move(self.y + i, self.x)
                    self.win.addstr('─' * width, Screen.color(color))
                    if 'right' in sides:
                        self.win.addstr('┤', join)
                elif self.view_mode == 'window':
                    self.win.move(self.y + i, self.x-1)
                    self.win.addstr('├', Screen.color(color))
                    self.win.addstr('─' * width, Screen.color(color))
                    self.win.addstr('┤', Screen.color(color))
                else:
                    self.win.move(self.y + i, self.x)
                    self.win.addstr('─' * width, Screen.color(color))

def _raise_split_sibling(view, sibling):
    """Keep both split panes adjacent on top of the stack with `view` focused.

    Focusing a pane (click, F1, F3, ...) goes through View.show(); in split view
    we first raise the sibling so the side-by-side / stacked layout is restored
    even after a fullscreen view (logs, refs) temporarily covered it.
    """
    if not view.app.split_active() or view.app._raising_split_sibling:
        return
    views = view.app.screen.showed_views
    if len(views) >= 2 and views[-1] is view and views[-2] is sibling:
        return  # already the top two in the right order
    view.app._raising_split_sibling = True
    try:
        sibling.show()
    finally:
        view.app._raising_split_sibling = False
