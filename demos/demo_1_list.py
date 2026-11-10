#!/usr/bin/env python3
"""Demo 1: a list view.

A ListView with a title bar (live [n/total] counter, [X] closes), coloured
rows and separators, search (`/`, then n/N), a right-click / F7 context menu,
and the function-key bar. Enter (or a double-click) on a row flashes its name.

Keys: arrows / j k, PgUp/PgDn, g/G, `/` search, F7 menu, F10 or q quit.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tuikit  # noqa: E402
from tuikit import colors  # noqa: E402

PLANETS = [
    ("Mercury", "rocky", colors.DIM),
    ("Venus", "rocky", colors.WARNING),
    ("Earth", "rocky", colors.ACCENT),
    ("Mars", "rocky", colors.ERROR),
    None,
    ("Jupiter", "gas giant", colors.WARNING),
    ("Saturn", "gas giant", colors.WARNING),
    None,
    ("Uranus", "ice giant", colors.SUCCESS),
    ("Neptune", "ice giant", colors.ACCENT),
]


class PlanetItem(tuikit.TextListItem):
    def __init__(self, name, kind, color):
        super().__init__(f"{name:<10} {kind}", color)
        self.name = name

    def activate(self):
        self.get_app().screen.show_flash(f"You picked {self.name}")
        return True


class ListDemo(tuikit.App):
    name = "demo-list"

    def setup(self):
        self.planets = tuikit.ListView(self, "planets")
        self.planets.set_header_item(
            tuikit.WindowTopBarItem("Planets", title_color=colors.ACCENT)
        )
        for row in PLANETS:
            if row is None:
                self.planets.append(tuikit.SeparatorItem())
            else:
                self.planets.append(PlanetItem(*row))
        self.planets.set_search_dialog(tuikit.SearchDialog(self, "planets-search"))

        self.menu = tuikit.Menu(self)

        self.screen.status_bar.set_entries(
            [
                ("F1", "Help", lambda: self.screen.show_flash("/ search, F7 menu")),
                ("F6", "Search", self.planets.open_search),
                ("F7", "Menu", self.menu_at_selection),
                ("F10", "Quit", self.quit),
            ]
        )
        self.planets.show()

    def menu_at_selection(self):
        # From the keyboard: open just below the selected row.
        x, y = self.planets.row_screen_position()
        self.show_context_menu(self.planets.get_selected(), at=(max(0, x), y + 1))

    def show_context_menu(self, item, view=None, at=None):
        # Called by a right-click on any row (at the mouse), or by F7.
        if not isinstance(item, PlanetItem):
            return False
        x, y = at or self.mouse.position
        self.menu.popup(
            [
                # shortcut = the hint drawn on the right, key = what runs it
                # while the menu is open
                tuikit.MenuItem(
                    f"Visit {item.name}", item.activate, shortcut="v", key=ord("v")
                ),
                tuikit.MenuItem(
                    "Copy name",
                    self.copy_to_clipboard,
                    [item.name],
                    shortcut="y",
                    key=ord("y"),
                ),
                tuikit.SeparatorItem(),
                tuikit.MenuItem("Close menu", lambda: None, shortcut="Esc"),
            ],
            x,
            y,
        )
        return True


if __name__ == "__main__":
    ListDemo().run()
