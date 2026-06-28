"""The Log: the app's logger and its backing LogView.

Levels 0–5 gate debug/info/success/warning/error. success() flashes the bottom
bar; error() raises the modal error dialog — both guarded for start-up before
the screen/dialog exist. Reached as `app.log`.
"""

from __future__ import annotations

import datetime

from gitk.items import TextListItem
from gitk.views import LogView


class Log:
    def __init__(self, app):
        self.app = app
        self.view = LogView(app)
        self.level = 4

    def debug(self, txt):
        if self.level > 4:
            self.log(18, txt)

    def info(self, txt):
        if self.level > 3:
            self.log(1, txt)

    def success(self, txt):
        if self.level > 2:
            self.log(1, txt)
            # Flash the message green over the bottom bar (guarded: success can
            # fire during start-up before the screen exists).
            screen = getattr(self.app, "screen", None)
            if screen is not None:
                screen.show_flash(txt)

    def warning(self, txt):
        if self.level > 1:
            self.log(12, txt)

    def error(self, txt):
        if self.level > 0:
            self.log(2, txt)
            # Surface errors as a modal red dialog (the status bar is gone).
            # Guarded: errors can fire during start-up before the dialog exists.
            dialog = getattr(self.app, "error_dialog", None)
            if dialog is not None:
                dialog.show_error(txt)

    def log(self, color, txt):
        now = datetime.datetime.now()
        for line in txt.splitlines():
            self.view.append(TextListItem(f"{now} {line}", color))
