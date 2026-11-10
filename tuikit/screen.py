"""Screen: the curses lifecycle, the z-ordered deck of views, drawing, and
mouse routing.

The deck (`showed_views`, bottom -> top) is the stacking order; the last view
is *active* and receives keys. Each frame `draw_visible_views()` redraws the
dirty views, the status bar, and composites everything in one doupdate().

Mouse routing (`dispatch_mouse`): the status bar gets the first look at a
click on its row; otherwise the event goes to the topmost view under the
cursor (a popup counts as covering the whole screen, so it sees outside clicks
too). A left press focuses that view and grabs the drag; moves and the
release go to the grab, or to the grabbed item in its own coordinates.

Subclass to add an application palette (`define_colors`).
"""

from __future__ import annotations

import curses
import curses.panel
import subprocess
import sys
import typing

from tuikit import colors
from tuikit import mouse as M
from tuikit.status_bar import StatusBar
from tuikit.view import MODE_FULLSCREEN, MODE_WINDOW

# Window placement (window_position): new windows fill free space in reading
# order (left to right, then the next row down), at least CASCADE_ORIGIN from
# the top-left corner, and cascade only when the screen has no room left: from
# the first slot's corner, a step per window - one row down so every title
# stays visible, a few columns right.
CASCADE_ORIGIN = (4, 2)  # x, y
CASCADE_STEP = (3, 1)

MOUSE_MASK = curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION


class Screen:
    # Set True (e.g. by a --no-color flag) before construction to force the
    # monochrome tier; the NO_COLOR environment variable does the same.
    force_mono = False

    def __init__(self, app, stdscr, status_bar: bool = True):
        self.app = app
        self.stdscr = stdscr

        colors.init(force_mono=self.force_mono)
        self.define_colors()

        try:
            curses.curs_set(0)  # some minimal terminals lack civis
        except curses.error:
            pass
        stdscr.timeout(5)
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(20)
        curses.mousemask(MOUSE_MASK)
        curses.mouseinterval(0)

        self.showed_views = []  # the deck, bottom -> top
        self.views = {}  # every view by id
        # Things to re-lay out after a terminal resize (SplitLayouts register).
        self.layouts = []
        # Set on a terminal resize: clear the background and re-touch every
        # panel for a full recomposition next frame.
        self._full_redraw = False
        self.status_bar = StatusBar(self) if status_bar else None

        stdscr.clear()
        stdscr.refresh()

    def define_colors(self):
        """Hook: define the application's colour roles with colors.define()."""

    def getmaxyx(self) -> typing.Tuple[int, int]:
        """Size of the area views live in (the screen minus the status bar)."""
        y, x = self.stdscr.getmaxyx()
        return (y - 1 if self.status_bar else y), x

    # -- deck -----------------------------------------------------------------

    def add_view(self, id, view):
        self.views[id] = view

    def get_view(self, id):
        return self.views.get(id)

    def floating_windows(self, exclude=None):
        """The shown floating windows (no popups, no tiled split panes),
        bottom -> top."""
        return [
            v
            for v in self.showed_views
            if v is not exclude
            and v.view_mode == MODE_WINDOW
            and not v.is_popup
            and not (v.split is not None and v.split.split_active())
        ]

    def window_position(self, view, height, width) -> typing.Tuple[int, int]:
        """(x, y) for a new `height` x `width` window, placed the way a
        desktop does it: the first free spot (overlapping no other floating
        window) in reading order - rows from the top, each left to right - so
        windows fill a row, then wrap below. With no free space left it
        cascades: the first slot no other window has its corner on (closed
        windows free theirs), slot 0 when none fits."""
        lines, cols = self.getmaxyx()
        corners = {
            (v.win.getbegyx()[1], v.win.getbegyx()[0])
            for v in self.floating_windows(exclude=view)
        }
        (x0, y0), (dx, dy) = CASCADE_ORIGIN, CASCADE_STEP
        slot, k = (x0, y0), 0
        while x0 + k * dx + width <= cols and y0 + k * dy + height <= lines:
            if (x0 + k * dx, y0 + k * dy) not in corners:
                slot = (x0 + k * dx, y0 + k * dy)
                break
            k += 1
        return self.free_spot(view, height, width) or slot

    def free_spot(self, view, height, width, near=None):
        """Where a `height` x `width` window fits on screen without
        overlapping any other floating window, or None when there is no such
        room. With `near` (x, y): the free spot closest to it. Without: the
        first in reading order, keeping CASCADE_ORIGIN's margin from the
        screen's top-left corner when there is room for that."""
        lines, cols = self.getmaxyx()
        rects = [
            (*v.win.getbegyx(), *v.win.getmaxyx())
            for v in self.floating_windows(exclude=view)
        ]

        def blocker(x, y):
            """The window a spot at (x, y) would overlap, or None."""
            for ry, rx, rh, rw in rects:
                if x < rx + rw and rx < x + width and y < ry + rh and ry < y + height:
                    return rx, rw
            return None

        if near is None:
            for top, left in (CASCADE_ORIGIN[::-1], (0, 0)):
                for y in range(top, lines - height + 1):
                    x = left
                    while x <= cols - width:
                        hit = blocker(x, y)
                        if hit is None:
                            return x, y
                        x = hit[0] + hit[1]  # skip past the window in the way
            return None

        wx, wy = near
        if 0 <= wx <= cols - width and 0 <= wy <= lines - height:
            if blocker(wx, wy) is None:
                return near
        spots = [
            (abs(x - wx) + abs(y - wy), y, x)
            for y in range(lines - height + 1)
            for x in range(cols - width + 1)
            if blocker(x, y) is None
        ]
        if not spots:
            return None
        _, y, x = min(spots)
        return x, y

    def get_active_view(self) -> typing.Any:
        return self.showed_views[-1] if self.showed_views else None

    def restack(self):
        """Re-assert the panel order to match showed_views (after an operation,
        e.g. resizing a non-active window, perturbed it)."""
        for view in self.showed_views:
            view.panel.top()

    def hide_active_view(self):
        """Close the top view (q / Esc)."""
        view = self.get_active_view()
        if view:
            view.close()

    def is_view_visible(self, view) -> bool:
        """True if `view` is on the deck and not wholly covered by a fullscreen
        view above it (worth keeping its content live)."""
        views = self.showed_views
        if view not in views:
            return False
        return not any(
            v.view_mode == MODE_FULLSCREEN for v in views[views.index(view) + 1 :]
        )

    def view_at(self, screen_x: int, screen_y: int):
        """The topmost view containing the point (a popup counts as covering
        everything), or None over empty space."""
        for view in reversed(self.showed_views):
            if view.is_popup or view.win.enclose(screen_y, screen_x):
                return view
        return None

    # -- status bar facade ----------------------------------------------------

    @property
    def working_message(self) -> str:
        return self.status_bar.working if self.status_bar else ""

    @working_message.setter
    def working_message(self, message: str):
        if self.status_bar:
            self.status_bar.working = message

    @property
    def dimmed(self) -> bool:
        return colors.dimmed

    @dimmed.setter
    def dimmed(self, value: bool):
        colors.dimmed = value

    def show_flash(self, message: str):
        if self.status_bar:
            self.status_bar.flash(message)

    def flash_active(self) -> bool:
        return bool(self.status_bar) and self.status_bar.flash_active()

    def show_working(self, message: str, dim: bool = True):
        """Show `message` on the status bar and repaint NOW: the caller is about
        to block the UI thread, so this forced draw is the only chance to show
        it. `dim` dims the rest of the screen meanwhile ("busy, input
        ignored"); pass dim=False when the event loop keeps running and the bar
        is just a progress hint. Undo with clear_working()."""
        self.working_message = message
        colors.dimmed = dim
        try:
            self.draw_visible_views()
        except curses.error:
            pass  # a failed paint must not abort the work it announces

    def clear_working(self):
        """Drop the working bar and undim; the next frame repaints."""
        self.working_message = ""
        colors.dimmed = False

    # -- drawing --------------------------------------------------------------

    def needs_redraw(self) -> bool:
        """Whether a shown view has changes waiting for the next frame."""
        return self._full_redraw or any(
            v.dirty or v.header_dirty for v in self.showed_views
        )

    def draw_visible_views(self):
        """Redraw dirty views (the panel deck handles occlusion and uncovered
        regions), the status bar, and flush in one doupdate(). After a resize,
        clear and re-touch everything for a full recomposition."""
        force = self._full_redraw
        if force:
            self._full_redraw = False
            self.stdscr.clear()
        for view in self.showed_views:
            view.redraw(force)
        if self.status_bar:
            self.status_bar.draw(self.stdscr)
        self.stdscr.noutrefresh()
        curses.panel.update_panels()
        curses.doupdate()

    def resize(self):
        """The terminal was resized: re-lay out every view, then the layouts."""
        self._full_redraw = True
        lines, cols = self.getmaxyx()
        for view in self.views.values():
            view.screen_size_changed(lines, cols)
        for layout in self.layouts:
            layout.on_screen_resized()

    # -- mouse ----------------------------------------------------------------

    def dispatch_mouse(self, event):
        tracker = self.app.mouse
        tracker.update_capture_for(event)
        event = tracker.adjust_for_grab(event)

        if self.status_bar and self.status_bar.handle_mouse(event):
            return

        under = self.view_at(event.screen_x, event.screen_y)
        if under and event.type == M.LEFT_CLICK:
            tracker.grabbed_view = under
            active = self.get_active_view()
            if under is not active:
                under.show()
                under.dirty = True
                if active:
                    active.dirty = True

        view = under
        item = None
        if event.is_move or event.is_release:
            if tracker.grabbed_view is not None:
                view = tracker.grabbed_view
            item = tracker.grabbed_item

        if view is not None:
            if item is not None:
                handled = item.handle_mouse(tracker.item_event(event))
            else:
                begin_y, begin_x = view.win.getbegyx()
                handled = view.handle_mouse(
                    event.at(event.screen_x - begin_x, event.screen_y - begin_y)
                )
            if handled:
                view.dirty = True

        if event.is_release:
            tracker.end_drag()

    # -- shelling out ---------------------------------------------------------

    def run_interactive(self, args, label=None) -> typing.Optional[int]:
        """Suspend curses, run `args` on the real terminal, then resume.

        The child gets our real stdin/stdout/stderr, so an editor, a prompt or
        a pager just works. When it exits the user presses Enter (so they can
        read its output) and the screen is repainted. The exit line is tagged
        with `label` (default: the app's name). Returns the exit code, or None
        if it could not be spawned (logged as an error)."""
        curses.def_prog_mode()  # save curses' terminal modes for the resume
        curses.endwin()

        printable = " ".join(args)
        label = self.app.name if label is None else label
        tag = f"[{label}] " if label else ""
        rc = None
        spawn_error = None
        try:
            sys.stdout.write(f"\r\n$ {printable}\r\n")
            sys.stdout.flush()
            rc = subprocess.run(args).returncode
            # Raw ANSI (curses is suspended): green for success, red for failure.
            color = "\x1b[32m" if rc == 0 else "\x1b[31m"
            sys.stdout.write(
                f"\r\n{color}{tag}`{printable}` exited with {rc}. "
                "Press Enter to return...\x1b[0m"
            )
            sys.stdout.flush()
            sys.stdin.readline()
        except OSError as e:
            spawn_error = str(e)

        # Resume: restore the saved modes, re-assert what endwin() dropped
        # (hidden cursor, mouse + keypad reporting), discard keys typed
        # meanwhile, and force a full repaint.
        curses.reset_prog_mode()
        self.stdscr.keypad(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        curses.mousemask(MOUSE_MASK)
        curses.mouseinterval(0)
        curses.flushinp()
        self._full_redraw = True
        try:
            self.draw_visible_views()
        except curses.error:
            pass

        if spawn_error:
            self.app.log.error(f"Could not run: {printable}: {spawn_error}")
        return rc
