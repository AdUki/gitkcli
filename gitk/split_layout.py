"""SplitLayout: the split-view state and tiling logic, extracted from App.

Holds the user's split intent (mode / ratio) and the default opening layout, and
positions the git-log / git-diff panes. Reached as `app.split`; it needs the App
only to get at the screen and the two panes (`self.app.screen` / `git_log` /
`git_diff`).
"""

from __future__ import annotations


class SplitLayout:
    def __init__(self, app):
        self.app = app

        # Split view tiles the git-log and git-diff panes side by side.
        #   'off'     - normal single-view behaviour
        #   'side'    - git-log left, git-diff right
        #   'stacked' - git-log top, git-diff bottom
        self.split_mode = "off"
        self.split_ratio = 0.5  # fraction of the screen given to the git-log pane
        self._raising_split_sibling = False

        # Layout the app opens in: 'fullscreen' (single view), 'side' or 'stacked'.
        self.default_view_mode = "fullscreen"

    def split_active(self):
        """True only when the split is currently shown as two tiled panes.

        `split_mode` is the user's intent; on a terminal too small to tile, the
        panes fall back to fullscreen (view_mode != 'window'). Behaviours that
        only make sense with a visible split (Esc/q stepping, divider drag,
        pane focus pairing) key off this, not off `split_mode` alone.
        """
        return self.split_mode != "off" and self.app.git_log.view_mode == "window"

    def cycle_split_view(self):
        self.set_split_mode(
            {"off": "side", "side": "stacked", "stacked": "off"}[self.split_mode]
        )
        return True

    def set_split_mode(self, mode):
        self.split_mode = mode
        self.apply_split_layout()
        if mode != "off":
            # Seed the diff pane from the current selection if it has no content yet.
            if not self.app.git_diff.items:
                item = self.app.git_log.get_selected()
                if item and hasattr(item, "load_to_view"):
                    item.load_to_view()
            self.app.git_log.show()  # focus the log pane (raises the diff pane with it)

    def apply_split_layout(self):
        """Position the git-log/git-diff panes for the current split mode."""
        lines, cols = self.app.screen.getmaxyx()
        min_w, min_h = 12, 4
        # Both axes must clear their minimum, otherwise a pane would be tiled
        # into a degenerate (<=0 content) window.
        fits = (self.split_mode == "side" and cols >= 2 * min_w and lines >= min_h) or (
            self.split_mode == "stacked" and lines >= 2 * min_h and cols >= min_w
        )
        if self.split_mode != "off" and fits:
            if self.split_mode == "side":
                log_w = max(
                    min_w, min(cols - min_w, int(round(cols * self.split_ratio)))
                )
                self.app.git_log.set_tiled(0, 0, lines, log_w)
                self.app.git_diff.set_tiled(log_w, 0, lines, cols - log_w)
            else:
                log_h = max(
                    min_h, min(lines - min_h, int(round(lines * self.split_ratio)))
                )
                self.app.git_log.set_tiled(0, 0, log_h, cols)
                self.app.git_diff.set_tiled(0, log_h, lines - log_h, cols)
        else:
            # split off, or terminal too small to tile: both panes go fullscreen.
            # Clear the tiled geometry so a later toggle_window_mode floats a
            # centered window again instead of reusing the last pane rect.
            for v in (self.app.git_log, self.app.git_diff):
                v.fixed_x = v.fixed_y = v.fixed_width = v.fixed_height = None
                v.set_fullscreen()
                v.dirty = True
