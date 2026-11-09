#!/usr/bin/env python3
"""Demo 2: widgets - segments, button rows and dialogs.

A settings form built from segmented rows: an on/off switch, a choice, a
radio group of toggles, a live (dynamic) text segment and a multi-colour
segment; a keyboard-navigable button row (Left/Right/Tab, Enter) that opens
the input, confirm and error dialogs. Everything is also clickable.

Keys: Up/Down pick a row, Enter / click activates, q quits.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tuikit  # noqa: E402
from tuikit import colors  # noqa: E402


class WidgetsDemo(tuikit.App):
    name = "demo-widgets"

    def setup(self):
        self.user = "guest"
        self.saves = 0

        form = self.form = tuikit.ListView(self, "form")
        form.set_header_item(tuikit.WindowTopBarItem("Settings"))

        self.notify = tuikit.OnOffToggleSegment(True)
        self.theme = tuikit.ChoiceSegment(
            [("dark", "Dark"), ("light", "Light"), ("auto", "Auto")], "dark"
        )
        form.append(tuikit.LabeledRow("Notifications", self.notify))
        form.append(tuikit.LabeledRow("Theme", self.theme))

        # A radio group: toggles whose callback un-toggles the others.
        self.sizes = [
            tuikit.ToggleSegment(f"<{s}>", s == "M", callback=self.pick_size)
            for s in ("S", "M", "L", "XL")
        ]
        form.append(
            tuikit.SegmentedListItem(
                [tuikit.TextSegment("  Size:", colors.ACCENT), *self.sizes]
            )
        )
        form.append(tuikit.SeparatorItem())

        form.append(
            tuikit.SegmentedListItem(
                [
                    tuikit.TextSegment("  Signed in as", colors.DIM),
                    tuikit.DynamicTextSegment(lambda: self.user, colors.SUCCESS),
                    tuikit.FillerSegment(),
                    tuikit.DynamicTextSegment(lambda: f"saved {self.saves}x"),
                    tuikit.TextSegment(" "),
                ]
            )
        )
        form.append(
            tuikit.SegmentedListItem(
                [
                    tuikit.TextSegment("  Status:", colors.DIM),
                    tuikit.RunsSegment(
                        [
                            ("●", colors.SUCCESS),
                            ("●", colors.SUCCESS),
                            ("●", colors.WARNING),
                            ("○", colors.DIM),
                        ]
                    ),
                ]
            )
        )
        form.append(tuikit.SpacerListItem())
        form.append(
            tuikit.button_row(
                tuikit.ButtonSegment("[Save]", self.save, colors.SUCCESS),
                tuikit.TextSegment(" "),
                tuikit.ButtonSegment("[Rename]", self.rename),
                tuikit.TextSegment(" "),
                tuikit.ButtonSegment("[Reset]", self.reset, colors.ERROR),
                tuikit.TextSegment(" "),
                tuikit.ButtonSegment("[Fail]", self.fail),
            )
        )

        self.rename_dialog = RenameDialog(self)
        self.confirm = tuikit.ConfirmDialog(self)
        self.errors = tuikit.ErrorDialog(self)
        form.show()

    def pick_size(self, chosen):
        for seg in self.sizes:
            seg.toggled = seg is chosen

    def save(self):
        self.saves += 1
        self.screen.show_flash(
            f"Saved: notifications {'on' if self.notify.toggled else 'off'}, "
            f"theme {self.theme.value}"
        )
        return True

    def rename(self):
        self.rename_dialog.open(self.user)
        return True

    def reset(self):
        self.confirm.confirm(
            " Reset settings",
            [
                "All settings go back to their defaults.",
                ("This cannot be undone.", colors.ERROR),
            ],
            self.do_reset,
            confirm_label="[Reset]",
        )
        return True

    def do_reset(self):
        self.notify.set_toggled(True)
        self.theme.set_value("dark")
        self.pick_size(self.sizes[1])
        self.form.dirty = True
        self.screen.show_flash("Settings reset")

    def fail(self):
        self.errors.show_error("Could not reach the server.\nTry again later.")
        return True


class RenameDialog(tuikit.InputDialog):
    """A prompt with history: Enter renames, Up/Down recall earlier names."""

    def __init__(self, app):
        super().__init__(
            app,
            "rename",
            " Rename",
            tuikit.SegmentedListItem([tuikit.TextSegment("New user name:")]),
            width=40,
        )

    def open(self, current):
        self.clear()
        self.input.set_text(current)
        self.show()

    def execute(self):
        if self.input.txt.strip():
            self.app.user = self.input.txt.strip()
            self.app.form.dirty = True
        super().execute()  # records the history


if __name__ == "__main__":
    WidgetsDemo().run()
