#!/usr/bin/env python3
"""Demo 5: a live dashboard.

Values change in the background (`process_background()` ticks a fake sampler
twice a second) and the rows redraw themselves: DynamicTextSegments pull fresh
text on every draw, RunsSegments paint multi-colour bars. [Pause] toggles
the sampler, [Reset] zeroes the counters, and the clock in the title bar
shows the loop keeping a single view live without user input.

Keys: Space pauses, r resets, q quits.
"""

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tuikit  # noqa: E402
from tuikit import colors  # noqa: E402

BAR_WIDTH = 30
TICK_SECONDS = 0.5


def bar(fraction):
    """A meter as colour runs: green, then yellow past 60%, red past 85%."""
    filled = round(max(0.0, min(1.0, fraction)) * BAR_WIDTH)
    runs = []
    for i in range(BAR_WIDTH):
        if i >= filled:
            runs.append(("·", colors.DIM))
        elif i >= 0.85 * BAR_WIDTH:
            runs.append(("█", colors.ERROR))
        elif i >= 0.6 * BAR_WIDTH:
            runs.append(("█", colors.WARNING))
        else:
            runs.append(("█", colors.SUCCESS))
    return runs


class MeterRow(tuikit.SegmentedListItem):
    """`  label  ██████····  42%` - its segments are rebuilt on every draw."""

    def __init__(self, label, read):
        super().__init__()
        self.label, self.read = label, read

    def get_segments(self):
        value = self.read()
        return self.adopt(
            [
                tuikit.TextSegment(f"  {self.label:<8}", colors.ACCENT),
                tuikit.RunsSegment(bar(value)),
                tuikit.TextSegment(f"{value * 100:4.0f}%"),
            ]
        )


class Dashboard(tuikit.App):
    name = "demo-dashboard"

    def setup(self):
        self.t = 0.0
        self.paused = False
        self.requests = 0
        self.next_tick = time.time()

        view = self.view = tuikit.ListView(self, "dashboard")
        view.set_header_item(
            tuikit.WindowTopBarItem(
                "Dashboard",
                [
                    tuikit.DynamicTextSegment(
                        lambda: time.strftime("%H:%M:%S"), colors.TITLE
                    ),
                    tuikit.HighlightButtonSegment(
                        "[Pause]", lambda: self.paused, self.toggle_pause
                    ),
                    tuikit.ButtonSegment("[Reset]", self.reset, colors.TITLE),
                ],
            )
        )
        view.append(MeterRow("cpu", lambda: 0.5 + 0.45 * math.sin(self.t)))
        view.append(MeterRow("memory", lambda: 0.4 + 0.1 * math.sin(self.t / 3)))
        view.append(MeterRow("disk", lambda: (self.t / 60) % 1.0))
        view.append(tuikit.SeparatorItem())
        view.append(
            tuikit.SegmentedListItem(
                [
                    tuikit.TextSegment("  requests", colors.ACCENT),
                    tuikit.DynamicTextSegment(lambda: f"{self.requests:>8}"),
                    tuikit.FillerSegment(),
                    tuikit.DynamicTextSegment(
                        lambda: "paused" if self.paused else "sampling",
                        colors.DIM,
                    ),
                    tuikit.TextSegment(" "),
                ]
            )
        )
        self.screen.status_bar.set_entries(
            [
                ("F1", "Pause", self.toggle_pause),
                ("F2", "Reset", self.reset),
                ("F10", "Quit", self.quit),
            ]
        )
        view.show()

    def toggle_pause(self):
        self.paused = not self.paused
        self.view.dirty = True
        return True

    def reset(self):
        self.t = 0.0
        self.requests = 0
        self.view.dirty = True
        return True

    def process_background(self):
        now = time.time()
        if now >= self.next_tick:
            self.next_tick = now + TICK_SECONDS
            if not self.paused:
                self.t += TICK_SECONDS
                self.requests += int(40 + 30 * math.sin(self.t * 1.7))
            self.view.dirty = True  # the clock ticks even while paused
        # Nothing urgent: the loop's idle poll (100 ms) is plenty for 2 Hz.
        return False

    def handle_global_key(self, event):
        if event.key == ord(" "):
            return self.toggle_pause()
        if event.key == ord("r"):
            return self.reset()
        return super().handle_global_key(event)


if __name__ == "__main__":
    Dashboard().run()
