#!/usr/bin/env python3
"""Demo 3: windows, split layout and the status bar.

A colour list and a list of the open windows in a SplitLayout (`|` or F2
cycles side by side / stacked / off; drag the divider; Ctrl+w switches
panes). Enter, a double-click or F3 opens a colour in its own floating
window - one per colour, packed around its text and cascaded like desktop
windows; choosing a colour whose window is open raises it. In a colour
window, Up/Down keep browsing the colours and Enter opens the next one, so
all five can be open at once. The window list (topmost first) works like a
taskbar: Enter or a double-click raises that window.
Drag a window by the title, resize it by the edges or the bottom corners,
double-click the title to maximize / restore it, [X] closes it.
F4 runs a fake background job showing the working bar, then a flash.

Keys: arrows, Enter, | / F2, F3, F4, Ctrl+w, q closes the top view.
"""

import curses
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tuikit  # noqa: E402
from tuikit import colors  # noqa: E402
from tuikit import keys as K  # noqa: E402

BROWSE_KEYS = (curses.KEY_UP, curses.KEY_DOWN, ord("k"), ord("j"))

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
        return self.get_app().open_window(self)


class WindowItem(tuikit.TextListItem):
    """A row of the window list: Enter / double-click raises its window."""

    def __init__(self, window):
        super().__init__(f"■ {window.color_name}", window.color)
        self.window = window

    def activate(self):
        self.window.show()
        return True


class WindowList(tuikit.ListView):
    """The open colour windows, topmost first - a taskbar."""

    def refresh(self):
        self.clear()
        shown = [
            v
            for v in reversed(self.app.screen.showed_views)
            if isinstance(v, ColorWindow)
        ]
        for window in shown:
            self.append(WindowItem(window))
        if not shown:
            self.append(
                tuikit.TextListItem(
                    "No windows: Enter on a colour opens one.",
                    colors.DIM,
                    is_selectable=False,
                )
            )


class ColorWindow(tuikit.ListView):
    """One colour's own floating window. It is made without a geometry, so
    its first show() packs it around its rows and puts it at the next free
    cascade slot; afterwards it stays where it was moved, also when it is
    closed and opened again."""

    def __init__(self, app, item):
        super().__init__(app, f"color-{item.name}", tuikit.MODE_WINDOW)
        self.color_name, self.color = item.name, item.color
        self.set_header_item(tuikit.WindowTopBarItem(item.name.capitalize()))
        self.append(tuikit.TextListItem(item.blurb, item.color))
        self.append(tuikit.SpacerListItem())
        self.append(tuikit.TextListItem("  sample text", item.color))
        self.append(tuikit.TextListItem("  sample text, dimmed", item.color, dim=True))
        self.append(tuikit.SeparatorItem())
        self.append(tuikit.TextListItem("Drag the title, resize an edge.", colors.DIM))
        self.append(
            tuikit.TextListItem("Double-click the title: maximize.", colors.DIM)
        )

    def handle_key(self, event):
        # Browse the colour list from here: focusing the list itself would
        # raise the split over every floating window.
        if event.key in K.ENTER_KEYS:
            return self.app.open_window(self.app.colors.get_selected())
        if event.key in BROWSE_KEYS:
            return self.app.colors.handle_key(event)
        return super().handle_key(event)

    # Keep the window list in step: opening, raising and closing reorder it.
    def show(self):
        super().show()
        self.app.window_list.refresh()

    def hide(self):
        super().hide()
        self.app.window_list.refresh()


class WindowsDemo(tuikit.App):
    name = "demo-windows"

    def setup(self):
        self.colors = tuikit.ListView(self, "colors")
        # [Split] shows and cycles the split mode, like `|` / F2.
        self.colors.set_header_item(
            tuikit.WindowTopBarItem("Colours", [tuikit.SplitButtonSegment()])
        )
        self.window_list = WindowList(self, "windows")
        self.window_list.set_header_item(tuikit.WindowTopBarItem("Windows"))
        for row in PALETTE:
            self.colors.append(ColorItem(*row))
        self.window_list.refresh()

        self.split = tuikit.SplitLayout(self, self.colors, self.window_list)
        self.windows = {}  # colour name -> its ColorWindow, made on first open
        self.job_until = 0.0

        self.screen.status_bar.set_entries(
            [
                ("F1", "Help", lambda: self.screen.show_flash("| split, F3 window")),
                ("F2", "Split", self.split.cycle_split_view),
                ("F3", "Window", lambda: self.open_window(self.colors.get_selected())),
                ("F4", "Job", self.start_job),
                ("F10", "Quit", self.quit),
            ]
        )
        self.split.set_split_mode(tuikit.SPLIT_SIDE)

    def open_window(self, item):
        """Show `item`'s own window, or raise it when it is already open."""
        if item.name not in self.windows:
            self.windows[item.name] = ColorWindow(self, item)
        self.windows[item.name].show()
        return True

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
            (self.window_list if self.colors.is_active() else self.colors).show()
            return True
        return super().handle_global_key(event)


if __name__ == "__main__":
    WindowsDemo().run()
