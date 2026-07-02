"""The View base class and ListView.

A View is one curses window/panel in the screen deck; ListView adds the
scrollable list of items, selection, search, and copy. Views hold the App
struct (`self.app`, injected at construction) and reach the screen, sibling
views, and items through it. Imports are limited to the colour palette (Screen),
config helpers, and the item types these base classes instantiate directly.
"""

from __future__ import annotations

import curses
import typing

from gitk.screen import Screen
from gitk.segmented_items import WindowTopBarItem

# Neutral grey for the divider between split panes — fixed so the line never
# looks like it belongs to whichever pane happens to be focused.
SPLIT_DIVIDER_COLOR = 18


class View:
    def __init__(
        self,
        app,
        id: str,
        view_mode: str = "fullscreen",
        x: typing.Optional[int] = None,
        y: typing.Optional[int] = None,
        height: typing.Optional[int] = None,
        width: typing.Optional[int] = None,
    ):

        # The App struct, injected at construction. Views use `self.app.<x>`
        # to reach screen / sibling views / services.
        self.app = app

        self.id: str = id
        self.view_mode: str = view_mode
        self.header_item: typing.Any = None
        self.is_popup: bool = False

        # coordinates and sizes when view is 'window'
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width

        self.dirty: bool = True
        # When only the header line changed (e.g. a live counter in the title),
        # redraw just row 0 instead of the whole body. A full redraw subsumes it.
        self.header_dirty: bool = False
        self.resize_mode: str = ""

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
        if not (
            self.app.split.split_active()
            and self in (self.app.git_log, self.app.git_diff)
        ):
            return None
        if self.app.split.split_mode == "side" and self is self.app.git_log:
            return {"right"}
        return set()

    def _calculate_dimensions(self, lines=None, cols=None):
        if lines is None or cols is None:
            lines, cols = self.app.screen.getmaxyx()

        # fullscreen dimensions
        win_height = lines
        win_width = cols
        win_y = 0
        win_x = 0

        if self.view_mode == "window":
            win_height = min(
                lines,
                self.fixed_height if self.fixed_height is not None else int(lines / 2),
            )
            win_width = min(
                cols,
                self.fixed_width if self.fixed_width is not None else int(cols / 2),
            )
            win_y = min(
                lines - win_height,
                int((lines - win_height) / 2) if self.fixed_y is None else self.fixed_y,
            )
            win_x = min(
                cols - win_width,
                int((cols - win_width) / 2) if self.fixed_x is None else self.fixed_x,
            )

        self.y = 0
        self.x = 0
        self.width = win_width
        self.height = win_height

        sides = self.split_border_sides()
        if sides is not None:
            # split pane: title row on top, plus only the requested thin lines
            self.height -= 1
            self.y += 1
            if "bottom" in sides:
                self.height -= 1
            if "left" in sides:
                self.x += 1
                self.width -= 1
            if "right" in sides:
                self.width -= 1
            return win_height, win_width, win_y, win_x

        # Window-mode views (floating popups and floated main views) draw a full
        # box. Fullscreen main views are borderless apart from their title line.
        box = self.view_mode == "window"

        if self.header_item or box:
            # subtract header line or box top
            self.height -= 1
            self.y += 1

        if box:
            # subtract box bottom, then box sides
            self.height -= 1
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

    def set_view_mode(self, view_mode: str):
        if self.view_mode == view_mode:
            return
        stdscr_height, stdscr_width = self.app.screen.getmaxyx()
        self.view_mode = view_mode
        height, width, y, x = self._calculate_dimensions(stdscr_height, stdscr_width)
        self._set_geometry(height, width, y, x)

    def set_tiled(self, x, y, height, width):
        """Place this view as a non-overlapping pane (used by split view)."""
        self.view_mode = "window"
        self.set_dimensions(x, y, height, width)

    def set_fullscreen(self):
        self.set_view_mode("fullscreen")

    def toggle_window_mode(self):
        # In split view the log/diff panes are managed by the split layout;
        # toggling a pane "maximizes" it by leaving split view altogether.
        if self.app.split.split_active() and self in (
            self.app.git_log,
            self.app.git_diff,
        ):
            self.app.split.set_split_mode("off")
            return
        self.set_view_mode("fullscreen" if self.view_mode == "window" else "window")

    def set_dimensions(self, x, y, height, width):
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width
        height, width, y, x = self._calculate_dimensions()
        self._set_geometry(height, width, y, x)

    def _start_split_resize(self, x: int, y: int) -> bool:
        """Arm a drag of the split divider when the grab is on the shared edge."""
        win_y, win_x = self.win.getbegyx()
        win_height, win_width = self.win.getmaxyx()
        is_log = self is self.app.git_log
        if self.app.split.split_mode == "side":
            # divider is the right edge of the log pane / left edge of the diff pane
            on_divider = (x >= win_x + win_width - 1) if is_log else (x <= win_x)
        else:  # stacked: there is no line, so the bottom pane's title bar is the grip
            on_divider = (not is_log) and (y <= win_y)
        if on_divider:
            self.resize_mode = "split"
            return True
        return False

    def start_resize(self, x: int, y: int) -> bool:
        self.resize_mode = ""
        # Split panes are fixed in place; only the shared divider can be dragged.
        if self.app.split.split_active() and self in (
            self.app.git_log,
            self.app.git_diff,
        ):
            return self._start_split_resize(x, y)
        if self.view_mode != "window":
            return False
        win_y, win_x = self.win.getbegyx()
        if y <= win_y:
            self.resize_mode = "m"
            return True
        if self.is_popup:
            return False
        win_height, win_width = self.win.getmaxyx()
        if x >= win_x + win_width - 1:
            self.resize_mode += "e"
        if x <= win_x:
            self.resize_mode += "w"
        if y >= win_y + win_height - 1:
            self.resize_mode += "s"
        return bool(self.resize_mode)

    def stop_resize(self) -> bool:
        if self.resize_mode:
            self.resize_mode = ""
            return True
        return False

    def handle_resize(self):
        if self.resize_mode == "split":
            lines, cols = self.app.screen.getmaxyx()
            if self.app.split.split_mode == "side":
                ratio = self.app.mouse.screen_x / max(1, cols)
            else:
                ratio = self.app.mouse.screen_y / max(1, lines)
            self.app.split.split_ratio = min(0.85, max(0.15, ratio))
            self.app.split.apply_split_layout()
            return
        stdscr_height, stdscr_width = self.app.screen.getmaxyx()
        win_y, win_x = self.win.getbegyx()
        win_height, win_width = self.win.getmaxyx()

        if "m" in self.resize_mode:
            new_x = max(0, min(win_x + self.app.mouse.rel_x, stdscr_width - win_width))
            new_y = max(
                0, min(win_y + self.app.mouse.rel_y, stdscr_height - win_height)
            )
            if new_x == win_x and new_y == win_y:
                return
            self.panel.move(new_y, new_x)
            self.dirty = True
        else:
            new_x = win_x
            new_y = win_y
            new_width = win_width
            new_height = win_height
            if "w" in self.resize_mode:
                # Dragging the left edge keeps the right edge fixed. Clamp new_x
                # to [0, right-5] so the window keeps a >=5 min width (matching
                # the 'e'/'s' branches) instead of collapsing to 0/negative,
                # which would jump to half-screen or raise curses.error.
                right = win_x + win_width
                new_x = max(0, min(win_x + self.app.mouse.rel_x, right - 5))
                new_width = right - new_x
            if "e" in self.resize_mode:
                new_width = max(
                    5, min(stdscr_width - new_x, win_width + self.app.mouse.rel_x)
                )
            if "s" in self.resize_mode:
                new_height = max(
                    5, min(stdscr_height - new_y, win_height + self.app.mouse.rel_y)
                )
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
            if "left" in sides:
                self.win.vline(0, 0, curses.ACS_VLINE, h)
            if "right" in sides:
                self.win.vline(0, w - 1, curses.ACS_VLINE, h)
            if "bottom" in sides:
                self.win.hline(h - 1, 0, curses.ACS_HLINE, w)
        elif self.view_mode == "window":
            self.win.attrset(self.border_color())
            self.win.box()

        self.draw_header(sides)

        parent = None
        try:
            index = self.app.screen.showed_views.index(self)
            if index > 0:
                parent = self.app.screen.showed_views[index - 1]
        except ValueError:
            pass
        if self != self.app.log.view and parent != self.app.log.view:
            self.app.log.debug(f"Draw view {self.id}")

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
                label = f" {title} "[: max(0, cols - 4)]
                if label:
                    self.win.move(0, 2)
                    self.win.addstr(label, self.border_color() | curses.A_BOLD)
        else:
            # Rule-line title bar (main views). Columns [left, right) the
            # title may paint; the divider / box-corner columns are left for
            # the vline (split) or box corners (┌─ Title ──[X]─┐, floated).
            if isinstance(self.header_item, WindowTopBarItem) and hasattr(
                self, "items"
            ):
                current = self._selected + 1 if self.items else 0
                self.header_item.set_counter(current, len(self.items))
            left, right = 0, cols
            if sides is not None:
                if "left" in sides:
                    left = 1
                if "right" in sides:
                    right = cols - 1
            elif self.view_mode == "window":
                left, right = 1, cols - 1
            self.win.move(0, left)
            self.header_item.draw_line(
                self.win, 0, right - left, self.is_active(), False, False
            )

    def on_activated(self):
        self.app.log.debug(f"View {self.id} activated")

    def on_deactivated(self):
        self.app.log.debug(f"View {self.id} deactivated")

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == "left-release":
            self.stop_resize()
        if mouse.event_type == "left-move" and self.resize_mode:
            self.handle_resize()
            return True
        if self.win.enclose(self.app.mouse.screen_y, self.app.mouse.screen_x):
            if (
                mouse.y == 0
                and self.header_item
                and self.header_item.handle_mouse_input(mouse)
            ):
                if (
                    "left-click" == mouse.event_type
                    or "double-click" == mouse.event_type
                ):
                    self.app.mouse.clicked_item = self.header_item
                return True
            if mouse.event_type == "left-click" and self.start_resize(
                self.app.mouse.screen_x, self.app.mouse.screen_y
            ):
                return True
        elif self.is_popup and "click" in mouse.event_type:
            self.hide()
            return True
        return False

    def handle_input(self, keyboard) -> bool:
        return False

    def is_active(self) -> bool:
        return (
            len(self.app.screen.showed_views) > 0
            and self.app.screen.showed_views[-1] == self
        )

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
            if self not in self.app.screen.showed_views:
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
