"""View: one curses window in the screen's z-ordered panel deck.

A view is either fullscreen (borderless, filling the screen above the status
bar) or a *window* (boxed, sized and positioned, draggable by its title row and
resizable by its edges). A popup (`is_popup`) is a modal window: it captures
all mouse input while on top, and a click outside closes it.

The content area is `(self.y, self.x, self.height, self.width)` inside the
window (below the header row, inside the box). Subclasses draw it in `draw()`
and call `super().draw()` for the border and header.

Lifecycle: `show()` raises the view to the top (the *active* view), `hide()`
takes it off the deck, `close()` is what the [X] button, q and Esc do (hide,
unless the view is a split pane - see SplitLayout). `dirty = True` requests a
redraw on the next frame; `header_dirty` only the header row.
"""

from __future__ import annotations

import curses
import curses.panel
import typing

from tuikit import colors
from tuikit import mouse as M

# The view_mode values: a borderless view filling the whole screen, or a boxed,
# independently sized/positioned floating window.
MODE_FULLSCREEN = "fullscreen"
MODE_WINDOW = "window"

MIN_WINDOW_SIZE = 5  # smallest width/height an edge drag can shrink a window to

# Where a window with no geometry of its own opens (place_window): sized to
# its content with some air, at most this share of the screen, at a spot
# chosen by Screen.window_position.
WINDOW_PADX, WINDOW_PADY = 4, 1
WINDOW_MAX_SHARE = 0.75
MIN_FLOAT_WIDTH = 20


class View:
    # Log a debug line per draw (off for a view that displays the log itself,
    # which would otherwise feed back into its own redraws).
    debug_draws = True

    def __init__(
        self,
        app,
        id: str,
        view_mode: str = MODE_FULLSCREEN,
        x: typing.Optional[int] = None,
        y: typing.Optional[int] = None,
        height: typing.Optional[int] = None,
        width: typing.Optional[int] = None,
    ):
        self.app = app
        self.id: str = id
        self.view_mode: str = view_mode
        self.header_item: typing.Any = None
        self.is_popup: bool = False
        # The SplitLayout this view is a pane of (set by SplitLayout), or None.
        self.split = None

        # Requested geometry while in window mode; None = centred / half-screen.
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width

        self.dirty: bool = True
        # Only the header row changed (e.g. a live counter in the title):
        # redraw just row 0. A full redraw subsumes it.
        self.header_dirty: bool = False
        # The drag in progress: "m" (move), "split" (a split divider), or any
        # combination of the edge letters "e"/"w"/"s" (resize).
        self.resize_mode: str = ""

        height, width, y, x = self._calculate_dimensions()
        self.win = curses.newwin(height, width, y, x)
        # The panel library composites overlapping windows (occlusion,
        # uncovered regions); views only mark content dirty. Hidden until show().
        self.panel = curses.panel.new_panel(self.win)
        self.panel.hide()

        self.app.screen.add_view(id, self)

    # -- geometry -------------------------------------------------------------

    def border_sides(self) -> typing.Optional[typing.Set[str]]:
        """The thin border lines this view draws as a tiled split pane, as a
        subset of {'left', 'right', 'bottom'} (the top row is always the
        title), or None when it is not a tiled pane (draw a full box in window
        mode, nothing when fullscreen)."""
        if self.split is not None and self.split.split_active():
            return self.split.border_sides(self)
        return None

    def _calculate_dimensions(self, lines=None, cols=None):
        if lines is None or cols is None:
            lines, cols = self.app.screen.getmaxyx()

        win_height, win_width, win_y, win_x = lines, cols, 0, 0
        if self.view_mode == MODE_WINDOW:
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

        sides = self.border_sides()
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

        box = self.view_mode == MODE_WINDOW
        if self.header_item or box:
            self.height -= 1  # header line or box top
            self.y += 1
        if box:
            self.height -= 1  # box bottom
            self.x += 1  # box sides
            self.width -= 2
        return win_height, win_width, win_y, win_x

    def _set_geometry(self, height, width, y, x):
        """Resize+reposition the window and mark it dirty. A panel resized in
        place does NOT re-expose what it shrinks away from, so a shown one is
        hidden first (uncovering its old footprint), moved to a valid origin,
        resized, moved to the target and restored - to the top if it was
        active, otherwise back into deck order. The repaint is a single
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
                self.app.screen.restack()
        self.dirty = True

    def set_header_item(self, item):
        self.header_item = item
        item._view = self
        self._calculate_dimensions()

    def has_geometry(self) -> bool:
        """Was a window position or size ever set (or dragged) for this view?"""
        return any(
            v is not None
            for v in (self.fixed_x, self.fixed_y, self.fixed_height, self.fixed_width)
        )

    def window_size(self, lines, cols):
        """(height, width) a window opens at when it has no geometry yet. Base:
        half the screen; a ListView packs around its rows instead."""
        return lines // 2, cols // 2

    def place_window(self):
        """Give the view an OS-like window geometry: window_size(), placed on
        the first free space in reading order, or cascaded down-right of the
        windows already open when the screen is full (Screen.window_position)."""
        lines, cols = self.app.screen.getmaxyx()
        height, width = self.window_size(lines, cols)
        x, y = self.app.screen.window_position(self, height, width)
        self.view_mode = MODE_WINDOW
        self.set_dimensions(x, y, height, width)

    def _needs_placing(self) -> bool:
        return (
            self.view_mode == MODE_WINDOW
            and not self.is_popup  # popups centre themselves
            and not self.has_geometry()  # (tiled split panes always have one)
        )

    def _is_floating(self) -> bool:
        return (
            self.view_mode == MODE_WINDOW
            and not self.is_popup
            and not (self.split is not None and self.split.split_active())
        )

    def _keep_clear(self):
        """A floating window reopened, or restored from fullscreen, comes back
        where it was - unless another window took that place meanwhile: then
        it moves to the nearest free spot (and stays put when there is no
        room)."""
        (y, x), (height, width) = self.win.getbegyx(), self.win.getmaxyx()
        spot = self.app.screen.free_spot(self, height, width, near=(x, y))
        if spot is not None and spot != (x, y):
            self.set_dimensions(spot[0], spot[1], height, width)

    def set_view_mode(self, view_mode: str):
        if self.view_mode == view_mode:
            return
        lines, cols = self.app.screen.getmaxyx()
        self.view_mode = view_mode
        if self._needs_placing():
            self.place_window()  # first time floated: no geometry to restore
            return
        self._set_geometry(*self._calculate_dimensions(lines, cols))
        if self._is_floating():
            self._keep_clear()  # restored: a window may have opened there

    def set_fullscreen(self):
        self.set_view_mode(MODE_FULLSCREEN)

    def set_tiled(self, x, y, height, width):
        """Place this view as a non-overlapping pane (used by SplitLayout)."""
        self.view_mode = MODE_WINDOW
        self.set_dimensions(x, y, height, width)

    def set_dimensions(self, x, y, height, width):
        """Window-mode geometry; None for x/y centres the window."""
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width
        self._set_geometry(*self._calculate_dimensions())

    def toggle_window_mode(self):
        """Fullscreen <-> floating window (a double-click on the title bar). A
        split pane instead "maximizes" by leaving the split."""
        if self.split is not None and self.split.split_active():
            self.split.unsplit()
            return
        self.set_view_mode(
            MODE_FULLSCREEN if self.view_mode == MODE_WINDOW else MODE_WINDOW
        )

    def screen_size_changed(self, lines, cols):
        self.dirty = True
        height, width, y, x = self._calculate_dimensions(lines, cols)
        self.win.resize(height, width)
        self.panel.move(y, x)

    # -- mouse drag: move / resize --------------------------------------------

    def start_resize(self, x: int, y: int) -> bool:
        """Arm a drag from screen position (x, y) if it grabs the title row
        (move) or an edge (resize). True when a drag was armed."""
        self.resize_mode = ""
        if self.split is not None and self.split.split_active():
            # Split panes are fixed in place; only the divider can be dragged.
            if self.split.on_divider(self, x, y):
                self.resize_mode = "split"
                return True
            return False
        if self.view_mode != MODE_WINDOW:
            return False
        win_y, win_x = self.win.getbegyx()
        if y <= win_y:
            self.resize_mode = "m"
            return True
        if self.is_popup:
            return False  # popups move but do not resize
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

    def handle_resize(self, event):
        if self.resize_mode == "split":
            self.split.drag_divider(event.screen_x, event.screen_y)
        elif "m" in self.resize_mode:
            self._move_window(event.rel_x, event.rel_y)
        else:
            self._resize_window_edges(event.rel_x, event.rel_y)

    def _move_window(self, dx, dy):
        lines, cols = self.app.screen.getmaxyx()
        win_y, win_x = self.win.getbegyx()
        win_height, win_width = self.win.getmaxyx()
        new_x = max(0, min(win_x + dx, cols - win_width))
        new_y = max(0, min(win_y + dy, lines - win_height))
        if new_x == win_x and new_y == win_y:
            return
        self.panel.move(new_y, new_x)
        if not self.is_popup:
            # Remembered, so maximize + restore (or a resize) brings the
            # window back where it was dragged. Popups re-centre each open.
            self.fixed_x, self.fixed_y = new_x, new_y
        self.dirty = True

    def _resize_window_edges(self, dx, dy):
        lines, cols = self.app.screen.getmaxyx()
        win_y, win_x = self.win.getbegyx()
        win_height, win_width = self.win.getmaxyx()
        new_x, new_y = win_x, win_y
        new_width, new_height = win_width, win_height
        if "w" in self.resize_mode:
            # The left edge moves, the right edge stays: clamp new_x so the
            # window keeps the minimum width instead of collapsing.
            right = win_x + win_width
            new_x = max(0, min(win_x + dx, right - MIN_WINDOW_SIZE))
            new_width = right - new_x
        if "e" in self.resize_mode:
            new_width = max(MIN_WINDOW_SIZE, min(cols - new_x, win_width + dx))
        if "s" in self.resize_mode:
            new_height = max(MIN_WINDOW_SIZE, min(lines - new_y, win_height + dy))
        self.set_dimensions(new_x, new_y, new_height, new_width)

    # -- drawing --------------------------------------------------------------

    def redraw(self, force=False):
        """Draw into the window buffer if dirty (the screen composites the deck
        afterwards). force=True re-touches the whole window so it is re-emitted
        even when unchanged (a full redraw after a resize)."""
        if self.dirty or force:
            self.dirty = False
            self.header_dirty = False
            if force:
                self.win.touchwin()
            self.draw()
        elif self.header_dirty:
            self.header_dirty = False
            self.draw_header(self.border_sides())

    def border_color(self):
        return colors.attr(colors.ACCENT if self.is_active() else colors.DIM)

    def draw(self):
        sides = self.border_sides()
        if sides is not None:
            # The divider between split panes belongs to neither pane: a fixed
            # neutral colour, not the owning pane's active/inactive colour. Full
            # height (row 0 included) so it runs up between the title bars.
            self.win.attrset(colors.attr(colors.DIM))
            h, w = self.win.getmaxyx()
            if "left" in sides:
                self.win.vline(0, 0, curses.ACS_VLINE, h)
            if "right" in sides:
                self.win.vline(0, w - 1, curses.ACS_VLINE, h)
            if "bottom" in sides:
                self.win.hline(h - 1, 0, curses.ACS_HLINE, w)
        elif self.view_mode == MODE_WINDOW:
            self.win.attrset(self.border_color())
            self.win.box()

        self.draw_header(sides)

        if self.debug_draws:
            self.app.log.debug(f"Draw view {self.id}")

    def _header_span(self, sides, cols):
        """Columns [left, right) the header row may paint: the divider / box
        corner columns are left to the vline or the box."""
        left, right = 0, cols
        if sides is not None:
            if "left" in sides:
                left = 1
            if "right" in sides:
                right = cols - 1
        elif self.view_mode == MODE_WINDOW:
            left, right = 1, cols - 1
        return left, right

    def update_header(self):
        """Hook run before the header is drawn (e.g. refresh a live counter)."""

    def draw_header(self, sides):
        """Draw only the header row (row 0)."""
        if not self.header_item:
            return
        _, cols = self.win.getmaxyx()
        if self.is_popup:
            # The title sits inset in the box's top border (┌─ Title ─────┐),
            # in the box's own colour (red for warning/error dialogs).
            title = self.header_item.get_text().strip()
            if title:
                label = f" {title} "[: max(0, cols - 4)]
                if label:
                    self.win.move(0, 2)
                    self.win.addstr(label, self.border_color() | curses.A_BOLD)
            return
        self.update_header()
        left, right = self._header_span(sides, cols)
        self.win.move(0, left)
        self.header_item.draw_line(
            self.win, 0, right - left, self.is_active(), False, False
        )

    # -- input ----------------------------------------------------------------

    def handle_mouse(self, event) -> bool:
        """`event.x/y` are relative to the window's top-left corner."""
        if event.type in (M.LEFT_RELEASE, M.CANCEL):
            self.stop_resize()
        if event.type == M.LEFT_MOVE and self.resize_mode:
            self.handle_resize(event)
            return True
        if self.win.enclose(event.screen_y, event.screen_x):
            if event.y == 0 and self.header_item:
                # Hand the header its own coordinates (column 0 = where it
                # starts drawing, past a box corner or split divider).
                left, _ = self._header_span(self.border_sides(), self.win.getmaxyx()[1])
                header_event = event.at(event.x - left, 0)
                if self.header_item.handle_mouse(header_event):
                    if event.is_left_press:
                        self.app.mouse.grab(self.header_item, header_event)
                    return True
            if event.type == M.LEFT_CLICK and self.start_resize(
                event.screen_x, event.screen_y
            ):
                return True
        elif self.is_popup and event.is_press:
            self.close()  # a click outside a popup dismisses it
            return True
        return False

    def handle_key(self, event) -> bool:
        return False

    # -- deck -----------------------------------------------------------------

    def is_active(self) -> bool:
        return self.app.screen.get_active_view() is self

    def is_shown(self) -> bool:
        return self in self.app.screen.showed_views

    def on_activated(self):
        self.app.log.debug(f"View {self.id} activated")

    def on_deactivated(self):
        self.app.log.debug(f"View {self.id} deactivated")

    def show(self):
        """Raise to the top of the deck and make active."""
        if self.split is not None:
            # A split pane keeps its sibling directly below it, so the tiled
            # layout survives a fullscreen view having covered it.
            self.split.raise_sibling(self)
        if self.is_active():
            return
        if self._needs_placing():
            self.place_window()  # a window opened without a geometry
        elif not self.is_shown() and self._is_floating():
            self._keep_clear()
        screen = self.app.screen
        prev_view = screen.get_active_view()
        if self in screen.showed_views:
            screen.showed_views.remove(self)
        screen.showed_views.append(self)
        self.panel.show()
        self.panel.top()
        self.dirty = True
        if prev_view:
            # The outgoing top view repaints to drop its active border/title
            # colour - active state keys off z-order, not overlap.
            prev_view.dirty = True
            prev_view.on_deactivated()
        self.on_activated()

    def hide(self):
        """Take the view off the deck (update_panels repaints what it covered)."""
        screen = self.app.screen
        if self not in screen.showed_views:
            return
        deactivated = screen.get_active_view() is self
        self.panel.hide()
        screen.showed_views.remove(self)
        if deactivated:
            self.on_deactivated()
            # Repaint the newly exposed top view with its active styling.
            new_active = screen.get_active_view()
            if new_active:
                new_active.dirty = True

    def close(self):
        """What [X] / q / Esc do: hide - or, for a tiled split pane, leave the
        split with the other pane fullscreen."""
        if self.split is not None and self.split.split_active():
            self.split.close_pane(self)
            return
        self.hide()
