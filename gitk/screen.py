"""The Screen: terminal/curses lifecycle, the colour palette, the z-ordered
panel deck, and the bottom function-key bar.

Holds the `app` struct (injected at construction) and is the root through which
views reach shared state. References to view/item objects are duck-typed at
runtime, so this module needs no UI imports.
"""

from __future__ import annotations

import curses
import curses.panel
import os
import time
import typing


class Screen:
    FLASH_DURATION = 2.0  # seconds a success flash replaces the bottom bar

    # Colour-capability tier, detected in __init__ from the terminal:
    #   0   = monochrome (vt200/vt220, NO_COLOR, --no-color): video attributes only
    #   8   = basic      (screen, xterm, linux): the 8 ANSI colours; the cursor row
    #         and search-highlighted row use A_REVERSE / A_BOLD, not 256-index bgs
    #   256 = full       (xterm-256color, ...): the original palette, unchanged
    color_depth = 256
    # Set True (by --no-color / NO_COLOR) to force the monochrome tier.
    force_mono = False
    # Background meaning "terminal default"; COLOR_BLACK if use_default_colors fails.
    _default_bg = -1

    # Pair numbers for the solid status bars. Kept low (< 64) so they fit the
    # 8-colour tier's COLOR_PAIRS limit; they sit clear of the base (1-31) and
    # 256-tier variant (50/100/150 + number) ranges.
    BAR_FLASH_PAIR = 40  # success flash: black on green
    BAR_LABEL_PAIR = 41  # bottom-bar F-key label cells: black on cyan

    @classmethod
    def _to_pal(cls, c: int) -> int:
        """Map a palette index to one the active tier can render. 256-only indices
        (greys 245/247, blue 20, ...) collapse to white below the full tier; the
        -1 'default' sentinel becomes the tier's default background."""
        if c < 0:
            return cls._default_bg
        if cls.color_depth >= 256 or c < 8:
            return c
        return curses.COLOR_WHITE

    @classmethod
    def _init_color(
        cls,
        pair_number: int,
        nfg: int,
        nbg: int = -1,
        hfg: int = -1,
        hbg: int = -1,
        sfg: int = -1,
        sbg: int = -1,
        shfg: int = -1,
        shbg: int = -1,
    ) -> None:
        if cls.color_depth == 0:
            return  # monochrome: no colour pairs exist
        # normal
        curses.init_pair(pair_number, cls._to_pal(nfg), cls._to_pal(nbg))
        if cls.color_depth < 256:
            # Selection / search highlight are rendered with video attributes in
            # color(); the 256-index background variants below don't exist here.
            return
        fg = nfg
        bg = nbg
        # highlighted
        if hfg >= 0:
            fg = hfg
        if hbg >= 0:
            bg = hbg
        else:
            bg = 20
        curses.init_pair(50 + pair_number, fg, bg)
        # selected
        if sfg >= 0:
            fg = sfg
        if sbg >= 0:
            bg = sbg
        else:
            bg = 235
        curses.init_pair(100 + pair_number, fg, bg)
        # selected+highlighted
        if shfg >= 0:
            fg = shfg
        if shbg >= 0:
            bg = shbg
        else:
            bg = 21
        curses.init_pair(150 + pair_number, fg, bg)

    @classmethod
    def bar_color(cls, pair_number):
        """Colour for a solid status bar (success flash / F-key label cells).
        Falls back to reverse video on terminals with no colour pairs."""
        if cls.color_depth == 0:
            return curses.A_REVERSE
        return curses.color_pair(pair_number)

    @classmethod
    def color(
        cls,
        number,
        selected=False,
        highlighted=False,
        matched=False,
        bold=None,
        dim=False,
    ):
        if matched:
            bold = True
            if number == 1:
                number = 16
            elif number == 18:
                number = 16
                dim = True
        if cls.color_depth >= 256:
            if selected and highlighted:
                color = curses.color_pair(150 + number)
            elif selected:
                color = curses.color_pair(100 + number)
            elif highlighted:
                color = curses.color_pair(50 + number)
            else:
                color = curses.color_pair(number)
        elif cls.color_depth >= 8:
            # No background-variant pairs on this terminal: paint the cursor row in
            # reverse video and the search-highlighted row in bold, over the base.
            color = curses.color_pair(number)
            if selected:
                color = color | curses.A_REVERSE
            if highlighted:
                color = color | curses.A_BOLD
        else:
            # Monochrome: every semantic colour collapses to a video attribute.
            color = curses.A_DIM if number == 18 else curses.A_NORMAL
            if number == 16:
                color = curses.A_BOLD
            if selected:
                color = color | curses.A_REVERSE
            if highlighted:
                color = color | curses.A_BOLD
            if matched:
                color = color | curses.A_UNDERLINE
        if bold or (selected and bold is None):
            color = color | curses.A_BOLD
        if dim:
            color = color | curses.A_DIM
        return color

    def __init__(self, app, stdscr: curses.window):

        # The App struct this screen belongs to, injected at construction.
        # Views read it as `self.app`; items/segments reach it by walking up
        # to their view.
        self.app = app

        # Pick a colour-rendering tier from the terminal's capability. vt200/vt220
        # report no colour; NO_COLOR / --no-color force the same monochrome tier;
        # screen/xterm/linux give 8 colours; xterm-256color gives the full palette.
        no_color_env = os.environ.get("NO_COLOR") not in (None, "")
        if Screen.force_mono or no_color_env or not curses.has_colors():
            Screen.color_depth = 0
        else:
            curses.start_color()
            try:
                curses.use_default_colors()
            except curses.error:
                Screen._default_bg = curses.COLOR_BLACK
            Screen.color_depth = 256 if curses.COLORS >= 256 else 8

        Screen._init_color(1, curses.COLOR_WHITE)  # Normal text
        Screen._init_color(2, curses.COLOR_RED)  # Error text
        Screen._init_color(3, curses.COLOR_GREEN)  # Status text
        Screen._init_color(4, curses.COLOR_YELLOW)  # Git ID
        Screen._init_color(5, curses.COLOR_BLUE)  # Data
        Screen._init_color(6, curses.COLOR_GREEN)  # Author
        Screen._init_color(8, curses.COLOR_RED)  # diff -
        Screen._init_color(9, curses.COLOR_GREEN)  # diff +
        Screen._init_color(10, curses.COLOR_CYAN)  # diff ranges
        Screen._init_color(11, curses.COLOR_GREEN)  # local ref
        Screen._init_color(12, curses.COLOR_YELLOW)  # tag
        Screen._init_color(13, curses.COLOR_BLUE)  # head
        Screen._init_color(14, curses.COLOR_CYAN)  # stash
        Screen._init_color(15, curses.COLOR_RED)  # remote ref
        Screen._init_color(16, curses.COLOR_YELLOW)  # search match
        Screen._init_color(17, curses.COLOR_BLUE)  # diff info lines
        Screen._init_color(18, 245)  # debug text

        Screen._init_color(
            30,
            curses.COLOR_BLACK,
            245,
            -1,
            247,  # Inactive window title
            curses.COLOR_WHITE,
            curses.COLOR_BLUE,
            -1,
            20,
        )  # Active window title

        Screen._init_color(
            31,
            curses.COLOR_WHITE,
            curses.COLOR_RED,
            -1,
            curses.COLOR_RED,  # Warning title bar
            curses.COLOR_WHITE,
            curses.COLOR_RED,
        )  # (white on red)

        if Screen.color_depth:  # solid bar pairs; monochrome uses reverse video instead
            curses.init_pair(
                Screen.BAR_LABEL_PAIR, curses.COLOR_BLACK, curses.COLOR_CYAN
            )  # Bottom-bar label block (Midnight Commander style)
            curses.init_pair(
                Screen.BAR_FLASH_PAIR, curses.COLOR_BLACK, curses.COLOR_GREEN
            )  # Success flash over the bottom bar

        try:
            curses.curs_set(0)  # Hide cursor (some minimal terminals lack civis)
        except curses.error:
            pass
        stdscr.timeout(5)
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(20)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.mouseinterval(0)

        self.stdscr = stdscr
        self.showed_views = []
        self.views = {}
        # Set on terminal resize: every window changed, so clear the background and
        # re-touch every panel for a full recomposition next frame.
        self._full_redraw = False

        # Midnight-Commander-style function-key panel pinned to the bottom row.
        # Each entry is (key label, name, callback); callbacks reach the app's
        # views lazily via self.app, so they are safe to define before those
        # views exist.
        self.bottom_bar_entries = [
            ("F1", "Git Log", lambda: self.app.git_log.show()),
            ("F2", "Git Refs", lambda: self.app.git_refs.show()),
            ("F3", "Git Diff", lambda: self.app.git_diff.show()),
            ("F4", "Logs", lambda: self.app.log.view.show()),
            ("F5", "Refresh", lambda: self.app.refresh_all()),
            ("F6", "Search", lambda: self.app.open_search()),
            ("F7", "Context", lambda: self.app.open_context_menu(at_selection=False)),
            ("F9", "Config", lambda: self.app.preferences.show()),
            ("F10", "Quit", lambda: self.app.exit_program()),
        ]
        # Same bindings driven from the keyboard (single source of truth with the
        # bar). F7 is special-cased in the main loop - from the keyboard it opens
        # at the selected row, so it never reaches this table.
        self.fkey_actions = {
            getattr(curses, "KEY_" + label): cb
            for label, name, cb in self.bottom_bar_entries
        }
        # Filled by draw_bottom_bar each frame: (x_start, x_end, callback) ranges
        # used to route clicks on the bottom row to the right entry.
        self.bar_hitmap = []

        # Success "flash": for FLASH_DURATION seconds after a command succeeds the
        # whole bottom bar is replaced by this message on a green background, then
        # reverts to the function-key panel. Empty when no flash is showing.
        self.flash_message = ""
        self.flash_time = 0.0

        stdscr.clear()
        stdscr.refresh()

    def getmaxyx(self) -> tuple[int, int]:
        y, x = self.stdscr.getmaxyx()
        return y - 1, x  # subtract status bar

    def add_view(self, id, view):
        self.views[id] = view

    def get_active_view(self) -> typing.Any:
        if len(self.showed_views) > 0:
            return self.showed_views[-1]
        return None

    def _restack(self):
        """Re-assert the panel deck order to match showed_views (bottom -> top),
        after an op (e.g. resizing a non-active window) perturbed it."""
        for view in self.showed_views:
            view.panel.top()

    def hide_active_view(self):
        if len(self.showed_views) > 0:
            # Closing a split pane (the [X] button) leaves split view and brings
            # the *other* pane up fullscreen, instead of popping a single pane
            # and leaving a gap with no backdrop.
            closing = self.showed_views[-1]
            if self.app.split.split_active() and closing in (
                self.app.git_log,
                self.app.git_diff,
            ):
                other = (
                    self.app.git_log
                    if closing is self.app.git_diff
                    else self.app.git_diff
                )
                self.app.split.set_split_mode("off")
                other.show()
                return
            # Same as closing any top view: blank its footprint (damage-based
            # redraw repaints what was underneath) and restyle the new top view.
            closing.hide()

    def is_view_visible(self, view) -> bool:
        """True if `view` is on screen and not fully hidden by a fullscreen view
        stacked above it. The panel deck handles pixel-level occlusion; this only
        decides whether keeping a window's content live is worth the work."""
        views = self.showed_views
        if view not in views:
            return False
        return not any(
            v.view_mode == "fullscreen" for v in views[views.index(view) + 1 :]
        )

    def show_flash(self, message: str):
        """Replace the bottom bar with `message` on green for FLASH_DURATION."""
        self.flash_message = message.splitlines()[0] if message else ""
        self.flash_time = time.time()

    def flash_active(self) -> bool:
        """True while a flash should keep the main loop redrawing the bottom bar
        (either still showing, or set-but-expired and awaiting one clearing draw)."""
        return bool(self.flash_message)

    def draw_bottom_bar(self, stdscr):
        """Draw the global function-key panel on the bottom row and rebuild the
        click hit-map. Midnight-Commander style: a 2-wide key number followed by
        the label on a cyan block, with the entries spread evenly across the full
        width (e.g. `` 1Log        2Refs       …``). Written to stdscr (the bottom
        of the panel deck); composited under the panels by update_panels().

        While a success flash is active the whole row is drawn green instead."""
        lines, cols = stdscr.getmaxyx()
        if cols < 2 or lines < 1:
            return
        y = lines - 1

        if self.flash_message:
            if time.time() - self.flash_time < self.FLASH_DURATION:
                # cols - 1: the bottom-right cell raises addwstr() ERR (see below).
                stdscr.addstr(
                    y,
                    0,
                    self.flash_message[: cols - 1].ljust(cols - 1),
                    Screen.bar_color(Screen.BAR_FLASH_PAIR),
                )
                self.bar_hitmap = []  # the F-key cells are hidden, so swallow clicks
                return
            self.flash_message = ""  # expired: fall through and redraw the F-key bar

        num_attr = Screen.color(1)  # key number: light text on default bg
        label_attr = Screen.bar_color(
            Screen.BAR_LABEL_PAIR
        )  # black on cyan (reverse if monochrome)
        # cols - 1: writing the bottom-right cell advances the cursor off-screen
        # and raises addwstr() ERR.
        stdscr.addstr(y, 0, " " * (cols - 1), num_attr)

        # Spread the entries evenly over the whole width: equal cells, with the
        # remainder handed to the leftmost cells. Each cell is a 2-wide key
        # number then the label padded out on cyan to fill the cell.
        self.bar_hitmap = []
        entries = self.bottom_bar_entries
        total = cols - 1
        n = len(entries)
        x = 0
        for i, (key, name, callback) in enumerate(entries):
            cell_w = total // n + (1 if i < total % n else 0)
            if cell_w < 2:
                break
            num = (key[1:] if key.startswith("F") else key).rjust(2)  # ' 1', '10'
            label = name[: cell_w - len(num)].ljust(cell_w - len(num))
            stdscr.addstr(y, x, num, num_attr)
            if label:
                stdscr.addstr(y, x + len(num), label, label_attr)
            self.bar_hitmap.append((x, x + cell_w, callback))
            x += cell_w

    def draw_visible_views(self):
        # Refresh only the content of windows whose content changed; the panel
        # deck handles occlusion and uncovered regions. On a full redraw (terminal
        # resize) clear the background and re-touch every panel so the whole stack
        # is recomposed. Then push the background (with the bottom bar), composite
        # the panels over it, and flush - all in a single doupdate().
        force = self._full_redraw
        if force:
            self._full_redraw = False
            self.stdscr.clear()

        for view in self.showed_views:
            view.redraw(force)

        self.draw_bottom_bar(self.stdscr)
        self.stdscr.noutrefresh()
        curses.panel.update_panels()
        curses.doupdate()
