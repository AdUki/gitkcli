"""gitk's split view: the git-log and git-diff panes tiled with tuikit's
SplitLayout. Reached as `app.split`; panes attached in launch_curses.
"""

from __future__ import annotations

from tuikit.split import SPLIT_OFF, SPLIT_SIDE, SPLIT_STACKED, SplitLayout
from tuikit.view import MODE_FULLSCREEN

__all__ = ["SPLIT_OFF", "SPLIT_SIDE", "SPLIT_STACKED", "GitSplitLayout"]


class GitSplitLayout(SplitLayout):
    """first = the git-log pane, second = the git-diff pane."""

    def __init__(self, app):
        super().__init__(app)
        # Layout the app opens in: fullscreen (single view), side, or stacked.
        self.default_view_mode = MODE_FULLSCREEN

    def on_split(self):
        # Seed the diff pane from the log selection if it has no content yet,
        # then focus the log pane (which raises the diff pane with it).
        log, diff = self.first, self.second
        if not diff.items:
            item = log.get_selected()
            if item and hasattr(item, "load_to_view"):
                item.load_to_view()
        log.show()
