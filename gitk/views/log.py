"""LogView: the application log pane."""

from __future__ import annotations

from tuikit.dialogs import SearchDialog
from tuikit.list_view import ListView
from tuikit.segmented import WindowTopBarItem
from tuikit.segments import (
    ButtonSegment,
    DynamicTextSegment,
    HighlightButtonSegment,
    TextSegment,
)

from gitk.ids import ID_LOG, ID_LOG_SEARCH
from gitk.keymap import handle_copy_key
from gitk.screen import Screen


class LogView(ListView):
    # Logging each draw of the log view would append to it, and redraw it.
    debug_draws = False

    def __init__(self, app):
        super().__init__(app, ID_LOG, "fullscreen")

        self.set_header_item(
            WindowTopBarItem(
                "Logs",
                [
                    ButtonSegment("[Clear]", lambda: self.clear(), Screen.C_TITLE),
                    HighlightButtonSegment(
                        "[Autoscroll]",
                        lambda: self.autoscroll,
                        self.toggle_autoscroll,
                        Screen.C_TITLE,
                    ),
                    TextSegment("  Log level:", Screen.C_TITLE),
                    DynamicTextSegment(lambda: self.app.log.level, Screen.C_TITLE),
                    ButtonSegment(
                        "[+]", lambda: self.change_log_level(+1), Screen.C_TITLE
                    ),
                    ButtonSegment(
                        "[-]", lambda: self.change_log_level(-1), Screen.C_TITLE
                    ),
                ],
                title_color=Screen.C_DATA,
            )
        )

        self.set_search_dialog(SearchDialog(app, ID_LOG_SEARCH))

    def change_log_level(self, value):
        self.app.log.level = max(0, min(5, self.app.log.level + value))
        self.dirty = True

    def toggle_autoscroll(self):
        self.autoscroll = not self.autoscroll
        if self.autoscroll:
            self._offset_y = max(0, len(self.items) - self.height)
        self.dirty = True

    def handle_key(self, event):
        return handle_copy_key(self, event.key) or super().handle_key(event)
