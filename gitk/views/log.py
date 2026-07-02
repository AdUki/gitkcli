"""LogView: the application log pane."""

from __future__ import annotations

from gitk.dialogs import SearchDialogPopup
from gitk.ids import ID_LOG, ID_LOG_SEARCH
from gitk.list_view import ListView
from gitk.screen import Screen
from gitk.segmented_items import WindowTopBarItem
from gitk.segments import (
    ButtonSegment,
    DynamicTextSegment,
    HighlightToggleSegment,
    TextSegment,
)


class LogView(ListView):
    def __init__(self, app):
        super().__init__(app, ID_LOG, "fullscreen")

        self.set_header_item(
            WindowTopBarItem(
                "Logs",
                [
                    ButtonSegment("[Clear]", lambda: self.clear(), Screen.C_TITLE),
                    HighlightToggleSegment(
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

        self.set_search_dialog(SearchDialogPopup(app, ID_LOG_SEARCH))

    def change_log_level(self, value):
        self.app.log.level = max(0, min(5, self.app.log.level + value))
        self.dirty = True

    def toggle_autoscroll(self):
        self.autoscroll = not self.autoscroll
        if self.autoscroll:
            self._offset_y = max(0, len(self.items) - self.height)
        self.dirty = True
