#!/usr/bin/env python3
"""Demo 3: windows, split layout and the status bar.

A colour list and a detail pane in a SplitLayout (`|` or F2 cycles side by
side / stacked / off; drag the divider; Ctrl+w switches panes). F3 opens
floating note windows: drag them by the title, resize by the edges or the
bottom corners, double-click the title to toggle fullscreen, [X] closes.
F4 runs a fake background job showing the working bar, then a flash.

Keys: arrows, Enter, | / F2, F3, F4, Ctrl+w, q closes the top view.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tuikit  # noqa: E402
from tuikit import colors  # noqa: E402

PALETTE = [
    ("red", colors.ERROR, "Warm, loud; used for errors and the close button."),
    ("green", colors.SUCCESS, "Calm and positive; used for success flashes."),
    ("yellow", colors.WARNING, "Attention; used for warnings and search hits."),
    ("blue", colors.ACCENT, "The active border and title line."),
    ("grey", colors.DIM, "Inactive borders, dividers and disabled text."),
]


class ColorItem(tuikit.TextListItem):
    def __init__(self, name, color, blurb):
        super().__init__(f"■ {name}", color)
        self.name, self.blurb = name, blurb

    def activate(self):
        self.get_app().detail.show()  # focus the detail pane
        return True


class ColorList(tuikit.ListView):
    def set_selected(self, what, visible_mode="center"):
        changed = super().set_selected(what, visible_mode)
        self.app.detail.show_color(self.get_selected())
        return changed


class Detail(tuikit.ListView):
    def show_color(self, item):
        self.clear()
        if item is not None:
            self.header_item.set_title(item.name.capitalize())
            self.append(tuikit.TextListItem(item.blurb, item.color))
            self.append(tuikit.SpacerListItem())
            self.append(tuikit.TextListItem("  sample text", item.color))
            self.append(
                tuikit.TextListItem("  sample text, dimmed", item.color, dim=True)
            )


class WindowsDemo(tuikit.App):
    name = "demo-windows"

    def setup(self):
        self.colors = ColorList(self, "colors")
        self.colors.set_header_item(tuikit.WindowTopBarItem("Colours"))
        self.detail = Detail(self, "detail")
        self.detail.set_header_item(tuikit.WindowTopBarItem(""))
        for row in PALETTE:
            self.colors.append(ColorItem(*row))

        self.split = tuikit.SplitLayout(self, self.colors, self.detail)
        self.notes = 0
        self.job_until = 0.0

        self.screen.status_bar.set_entries(
            [
                ("F1", "Help", lambda: self.screen.show_flash("| split, F3 note")),
                ("F2", "Split", self.split.cycle_split_view),
                ("F3", "Note", self.new_note),
                ("F4", "Job", self.start_job),
                ("F10", "Quit", self.quit),
            ]
        )
        self.detail.show_color(self.colors.get_selected())
        self.split.set_split_mode(tuikit.SPLIT_SIDE)

    def new_note(self):
        self.notes += 1
        n = self.notes
        note = tuikit.ListView(
            self,
            f"note-{n}",
            tuikit.MODE_WINDOW,
            x=4 + 3 * n,
            y=2 + n,
            height=8,
            width=34,
        )
        note.set_header_item(tuikit.WindowTopBarItem(f"Note {n}"))
        note.append(tuikit.TextListItem("Drag me by the title."))
        note.append(tuikit.TextListItem("Resize me by an edge."))
        note.append(tuikit.SeparatorItem())
        note.append(tuikit.TextListItem("Double-click the title", colors.DIM))
        note.append(tuikit.TextListItem("to go fullscreen.", colors.DIM))
        note.show()

    def start_job(self):
        # A pretend background job: the loop polls process_background().
        self.job_until = time.time() + 1.5
        self.screen.show_working("Working: crunching numbers ...", dim=False)

    def process_background(self):
        if self.job_until and time.time() >= self.job_until:
            self.job_until = 0.0
            self.screen.clear_working()
            self.screen.show_flash("Job done")
        return bool(self.job_until)

    def handle_global_key(self, event):
        if event.key == ord("|"):
            return self.split.cycle_split_view()
        if event.key == tuikit.ctrl("w") and self.split.split_active():
            (self.detail if self.colors.is_active() else self.colors).show()
            return True
        return super().handle_global_key(event)


if __name__ == "__main__":
    WindowsDemo().run()
