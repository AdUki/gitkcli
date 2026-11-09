"""SplitLayout: tile two views as side-by-side or stacked panes.

    split = SplitLayout(app, left_or_top_view, right_or_bottom_view)
    split.set_split_mode(SPLIT_SIDE)

While split, the panes are fixed (only the shared divider drags, adjusting
`split_ratio`), focusing either pane raises the other right below it, closing
one ([X]) leaves the split with the other fullscreen, and double-clicking a
pane's title "maximizes" it the same way. On a terminal too small to tile,
both panes fall back to fullscreen (the split *intent* is kept and re-applied
on resize). Subclass and override `on_split()` to react to entering a split.
"""

from __future__ import annotations

from tuikit.view import MODE_WINDOW

SPLIT_OFF = "off"  # single fullscreen views
SPLIT_SIDE = "side"  # first pane left, second right
SPLIT_STACKED = "stacked"  # first pane on top, second below

_NEXT_MODE = {
    SPLIT_OFF: SPLIT_SIDE,
    SPLIT_SIDE: SPLIT_STACKED,
    SPLIT_STACKED: SPLIT_OFF,
}


class SplitLayout:
    MIN_WIDTH = 12  # smallest pane width a side-by-side split tiles into
    MIN_HEIGHT = 4  # smallest pane height a stacked split tiles into
    RATIO_RANGE = (0.15, 0.85)  # how far the divider can be dragged

    def __init__(self, app, first=None, second=None):
        self.app = app
        self.split_mode = SPLIT_OFF
        self.split_ratio = 0.5  # fraction of the screen given to the first pane
        self.first = self.second = None
        self._raising_sibling = False
        if first is not None and second is not None:
            self.attach(first, second)

    def attach(self, first, second):
        """Make `first` (left / top) and `second` (right / bottom) the panes."""
        self.first, self.second = first, second
        first.split = second.split = self
        app_screen = self.app.screen
        if self not in app_screen.layouts:
            app_screen.layouts.append(self)

    @property
    def panes(self):
        return (self.first, self.second)

    def sibling(self, view):
        return self.second if view is self.first else self.first

    def split_active(self) -> bool:
        """True only while the split is actually shown as two tiled panes.
        `split_mode` is the user's intent; on a terminal too small to tile the
        panes fall back to fullscreen. Behaviour that only makes sense with a
        visible split (divider drag, close/maximize semantics, pane pairing)
        keys off this, not off `split_mode` alone."""
        return (
            self.split_mode != SPLIT_OFF
            and self.first is not None
            and self.first.view_mode == MODE_WINDOW
        )

    # -- mode -----------------------------------------------------------------

    def cycle_split_view(self):
        """off -> side -> stacked -> off."""
        self.set_split_mode(_NEXT_MODE[self.split_mode])
        return True

    def set_split_mode(self, mode):
        self.split_mode = mode
        self.apply_split_layout()
        if mode != SPLIT_OFF:
            self.on_split()

    def unsplit(self):
        self.set_split_mode(SPLIT_OFF)

    def on_split(self):
        """Called after entering a split mode. Default: focus the first pane
        (which raises the second with it)."""
        self.first.show()

    def close_pane(self, view):
        """Closing a tiled pane leaves the split and brings the other pane up
        fullscreen (rather than leaving a hole with no backdrop)."""
        other = self.sibling(view)
        self.unsplit()
        other.show()

    def raise_sibling(self, view):
        """Keep both panes adjacent on top of the deck with `view` focused, so
        the tiled layout is restored even after a fullscreen view covered it.
        Called by View.show() on a pane."""
        if not self.split_active() or self._raising_sibling:
            return
        sibling = self.sibling(view)
        views = self.app.screen.showed_views
        if len(views) >= 2 and views[-1] is view and views[-2] is sibling:
            return  # already the top two, in the right order
        self._raising_sibling = True
        try:
            sibling.show()
        finally:
            self._raising_sibling = False

    # -- geometry -------------------------------------------------------------

    def border_sides(self, view):
        """Side-by-side: only the first (left) pane draws a divider on its right;
        the second is borderless. Stacked: both are borderless and the bottom
        pane's title bar doubles as the draggable divider."""
        if self.split_mode == SPLIT_SIDE and view is self.first:
            return {"right"}
        return set()

    def on_divider(self, view, x, y) -> bool:
        """Whether a press at screen (x, y) on `view` grabs the divider."""
        win_y, win_x = view.win.getbegyx()
        _, win_width = view.win.getmaxyx()
        if self.split_mode == SPLIT_SIDE:
            # the right edge of the first pane / the left edge of the second
            if view is self.first:
                return x >= win_x + win_width - 1
            return x <= win_x
        # stacked: no line, so the bottom pane's title bar is the grip
        return view is self.second and y <= win_y

    def drag_divider(self, screen_x, screen_y):
        lines, cols = self.app.screen.getmaxyx()
        if self.split_mode == SPLIT_SIDE:
            ratio = screen_x / max(1, cols)
        else:
            ratio = screen_y / max(1, lines)
        lo, hi = self.RATIO_RANGE
        self.split_ratio = min(hi, max(lo, ratio))
        self.apply_split_layout()

    def on_screen_resized(self):
        if self.split_mode != SPLIT_OFF:
            self.apply_split_layout()

    def apply_split_layout(self):
        """Position the panes for the current mode."""
        lines, cols = self.app.screen.getmaxyx()
        min_w, min_h = self.MIN_WIDTH, self.MIN_HEIGHT
        # Both axes must clear their minimum, or a pane would be tiled into a
        # degenerate (<= 0 content) window.
        fits = (
            self.split_mode == SPLIT_SIDE and cols >= 2 * min_w and lines >= min_h
        ) or (self.split_mode == SPLIT_STACKED and lines >= 2 * min_h and cols >= min_w)
        if self.split_mode != SPLIT_OFF and fits:
            if self.split_mode == SPLIT_SIDE:
                w = max(min_w, min(cols - min_w, int(round(cols * self.split_ratio))))
                self.first.set_tiled(0, 0, lines, w)
                self.second.set_tiled(w, 0, lines, cols - w)
            else:
                h = max(min_h, min(lines - min_h, int(round(lines * self.split_ratio))))
                self.first.set_tiled(0, 0, h, cols)
                self.second.set_tiled(0, h, lines - h, cols)
        else:
            # Split off, or too small to tile: both panes go fullscreen. Drop
            # the tiled geometry so a later toggle_window_mode floats a centred
            # window again instead of reusing the last pane rectangle.
            for v in self.panes:
                v.fixed_x = v.fixed_y = v.fixed_width = v.fixed_height = None
                v.set_fullscreen()
                v.dirty = True
