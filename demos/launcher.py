#!/usr/bin/env python3
"""Demo launcher: pick a demo and run it.

Each demo runs as a child process with this UI suspended
(`Screen.run_interactive`) - the same way gitkcli hands the terminal to an
editor - and the launcher comes back when it exits.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tuikit  # noqa: E402
from tuikit import colors  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

DEMOS = [
    ("demo_1_list", "List view: search, context menu, function-key bar"),
    ("demo_2_widgets", "Widgets: toggles, choices, button rows, dialogs"),
    ("demo_3_windows", "Floating windows, split layout, working bar"),
    ("demo_4_input", "Text input: an inline field, commands, search"),
    ("demo_5_dashboard", "Live dashboard: background updates, meters"),
]


class DemoItem(tuikit.SegmentedListItem):
    def __init__(self, name, blurb):
        super().__init__(
            [
                tuikit.TextSegment(f"  {name:<18}", colors.ACCENT),
                tuikit.TextSegment(blurb),
            ]
        )
        self.name = name

    def activate(self):
        app = self.get_app()
        rc = app.screen.run_interactive(
            [sys.executable, os.path.join(HERE, self.name + ".py")]
        )
        app.screen.show_flash(f"{self.name} exited with {rc}")
        return True


class Launcher(tuikit.App):
    name = "tuikit demos"

    def setup(self):
        view = tuikit.ListView(self, "launcher")
        view.set_header_item(
            tuikit.WindowTopBarItem("tuikit demos", title_color=colors.ACCENT)
        )
        for name, blurb in DEMOS:
            view.append(DemoItem(name, blurb))
        view.append(tuikit.SpacerListItem())
        view.append(
            tuikit.TextListItem(
                "  Enter or double-click runs a demo; q quits.",
                colors.DIM,
                is_selectable=False,
            )
        )
        self.screen.status_bar.set_entries([("F10", "Quit", self.quit)])
        view.show()


if __name__ == "__main__":
    Launcher().run()
