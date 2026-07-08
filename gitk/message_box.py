"""Modal red message-box popups: the shared base plus the confirm and error
dialogs.

Like the other dialogs these are small ListViews reached through the App struct
at runtime (`self.app`); they depend only on the base view, items, segments and
helpers.
"""

from __future__ import annotations

import curses

from gitk.ids import ID_CONFIRM_DIALOG, ID_ERROR_DIALOG
from gitk.input import ENTER_KEYS
from gitk.items import TextListItem
from gitk.list_view import ListView
from gitk.screen import Screen
from gitk.segmented_items import button_row
from gitk.segments import ButtonSegment, TextSegment


class _RedMessageBoxPopup(ListView):
    """Modal red message box: a red banner header and matching red border,
    sized to its content. Base for the confirm and error dialogs."""

    def __init__(self, app, id, banner):
        super().__init__(app, id, "window")
        self.set_header_item(
            TextListItem(banner, Screen.C_BANNER, expand=True)
        )  # red banner
        self.is_popup = True

    def border_color(self):
        return Screen.color(Screen.C_ERROR)


class ConfirmDialogPopup(_RedMessageBoxPopup):
    """Generic yes/no popup. Used to offer a forced retry after a git
    operation is rejected (ref already exists, non-fast-forward push, ...)."""

    def __init__(self, app):
        super().__init__(app, ID_CONFIRM_DIALOG, "")
        self._on_confirm = lambda: None

    def confirm(
        self, title, lines, on_confirm, confirm_label="[Yes]", cancel_label="[Cancel]"
    ):
        # Each entry in `lines` is either a string or a (text, color) tuple
        # (color 4 = yellow, 2 = red) for emphasis. These are destructive
        # force/overwrite confirmations, so default focus to [Cancel].
        self._on_confirm = on_confirm
        self.header_item.set_text(title)
        self._show_message_box(
            lines,
            button_row(
                ButtonSegment(confirm_label, self._confirm, Screen.C_ERROR),
                TextSegment("   "),
                ButtonSegment(cancel_label, self.hide),
            ),
            focus="last",
        )

    def _confirm(self):
        self.hide()
        self._on_confirm()
        return True

    def handle_input(self, keyboard):
        key = keyboard.key
        if key in (ord("y"), ord("Y")):
            self._confirm()
        elif key in (curses.KEY_EXIT, ord("n"), ord("N"), ord("q")):
            self.hide()
        else:
            # Left/Right move focus between buttons; Enter activates the focused
            # button. Default focus is [Cancel], so a bare Enter cancels; the
            # user Left-arrows to the confirm button to proceed. (y/Y always
            # confirms regardless of focus.)
            super().handle_input(keyboard)
        # Modal: swallow every other key. Otherwise global shortcuts (F1-F5,
        # Ctrl+o/i) would fall through and could bury this popup behind a
        # fullscreen view while its force callback is still armed.
        return True


class ErrorDialogPopup(_RedMessageBoxPopup):
    """Modal red alert with a single [Ok] button. Replaces the old status-bar
    error line: Log.error() pops this with the message. Errors that arrive while
    it is still open (e.g. a job emitting several stderr lines) are coalesced
    into the same dialog instead of stacking a new popup per line."""

    MAX_LINES = 12

    def __init__(self, app):
        super().__init__(app, ID_ERROR_DIALOG, " Error")
        self._lines = []

    def show_error(self, message):
        incoming = [line for line in message.splitlines() if line.strip()] or [message]
        if not self.is_active():
            self._lines = []
        for line in incoming:
            if len(self._lines) < self.MAX_LINES:
                self._lines.append(line)
        self._render()

    def _render(self):
        self._show_message_box(
            [(line, Screen.C_ERROR) for line in self._lines],
            button_row(ButtonSegment("[Ok]", self.hide, Screen.C_ERROR)),
        )

    def handle_input(self, keyboard):
        # Any of Enter / Esc / o / q dismisses; Left/Right keep focus on [Ok].
        if keyboard.key in ENTER_KEYS or keyboard.key in (
            curses.KEY_EXIT,
            ord("o"),
            ord("O"),
            ord("q"),
        ):
            self.hide()
        else:
            super().handle_input(keyboard)
        return True  # modal: swallow every other key
