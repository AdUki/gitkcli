#!/usr/bin/env python3
"""
Demo 1: Basic List View
Demonstrates basic list view with scrolling, selection, and colored items
"""
import curses
import sys
sys.path.insert(0, '..')

from ui import UIContext, Screen, ListView, TextListItem, SeparatorItem, SpacerListItem


def main(stdscr):
    # Initialize screen
    screen = Screen(stdscr)
    ui_context = UIContext(screen)

    # Create a fullscreen list view
    list_view = ListView('main', view_mode='fullscreen', ui_context=ui_context)
    list_view.show()

    # Add a header
    header = TextListItem(  "╔═══════════════════════════════════════════════╗", color=5, ui_context=ui_context)
    title = TextListItem(   "║         📚  DEMO 1: Basic List View           ║", color=5, ui_context=ui_context)
    subtitle = TextListItem("║       Navigate: ↑↓ or j/k  |  Quit: q         ║", color=18, ui_context=ui_context)
    footer = TextListItem(  "╚═══════════════════════════════════════════════╝", color=5, ui_context=ui_context)

    header.is_selectable = False
    list_view.append(header)

    title.is_selectable = False
    list_view.append(title)

    subtitle.is_selectable = False
    list_view.append(subtitle)

    footer.is_selectable = False
    list_view.append(footer)

    list_view.append(SpacerListItem(ui_context))

    # Add different colored items
    list_view.append(TextListItem("⚪ White text (default)", color=1, ui_context=ui_context))
    list_view.append(TextListItem("🔴 Red text for errors", color=2, ui_context=ui_context))
    list_view.append(TextListItem("🟢 Green text for success", color=3, ui_context=ui_context))
    list_view.append(TextListItem("🟡 Yellow text for warnings", color=4, ui_context=ui_context))
    list_view.append(TextListItem("🔵 Blue text for data", color=5, ui_context=ui_context))
    list_view.append(TextListItem("🟣 Cyan text for info", color=10, ui_context=ui_context))

    list_view.append(SeparatorItem(ui_context))

    # Add many items to show scrolling
    for i in range(50):
        color = [1, 2, 3, 4, 5, 6][i % 6]
        list_view.append(TextListItem(f"Item #{i+1:03d} - This is a scrollable list item", color=color, ui_context=ui_context))

    list_view.append(SeparatorItem(ui_context))
    list_view.append(TextListItem("O End of list - Press 'q' to quit", color=3, ui_context=ui_context))

    # Set initial selection
    list_view.set_selected(5)

    # Main loop
    while True:
        screen.draw_visible_views()

        key = stdscr.getch()

        if key == ord('q'):
            break

        list_view.handle_input(key)


if __name__ == '__main__':
    curses.wrapper(main)
