#!/usr/bin/env python3
"""
Demo Launcher - Interactive menu to run any demo
"""
import curses
import sys
import subprocess
sys.path.insert(0, '..')

from ui import UIContext, Screen, ListView, TextListItem, SeparatorItem


DEMOS = [
    {
        'file': 'demo_0_showcase.py',
        'title': '🌟 UI Showcase (START HERE!)',
        'desc': 'Complete visual gallery of all components',
        'color': 6,
    },
    {
        'file': 'demo_1_basic_list.py',
        'title': '📚 Basic List View',
        'desc': 'Scrolling, selection, and colored items',
        'color': 5,
    },
    {
        'file': 'demo_2_interactive_segments.py',
        'title': '🎮 Interactive Segments',
        'desc': 'Buttons, toggles, and mouse interactions',
        'color': 3,
    },
    {
        'file': 'demo_3_windows.py',
        'title': '🪟 Multiple Windows',
        'desc': 'Window management, dragging, and resizing',
        'color': 10,
    },
    {
        'file': 'demo_4_text_input.py',
        'title': '⌨️  Text Input',
        'desc': 'Text editing with cursor and chat interface',
        'color': 4,
    },
    {
        'file': 'demo_5_dashboard.py',
        'title': '📊 Dashboard',
        'desc': 'Complete app with real-time updates',
        'color': 2,
    },
]


def create_menu(stdscr):
    screen = Screen(stdscr)
    ui_context = UIContext(screen)

    list_view = ListView('menu', view_mode='fullscreen', ui_context=ui_context)
    list_view.show()

    # Header
    list_view.append(TextListItem("╔═══════════════════════════════════════════════════════════════════╗", color=5, selectable=False, ui_context=ui_context))
    list_view.append(TextListItem("║                                                                   ║", color=5, selectable=False, ui_context=ui_context))
    list_view.append(TextListItem("║          🎨  TERMINAL UI FRAMEWORK - DEMO LAUNCHER                ║", color=5, selectable=False, ui_context=ui_context))
    list_view.append(TextListItem("║                                                                   ║", color=5, selectable=False, ui_context=ui_context))
    list_view.append(TextListItem("║    Select a demo to run | ↑↓ or j/k to navigate | Enter to run   ║", color=18, selectable=False, ui_context=ui_context))
    list_view.append(TextListItem("║                                                                   ║", color=5, selectable=False, ui_context=ui_context))
    list_view.append(TextListItem("╚═══════════════════════════════════════════════════════════════════╝", color=5, selectable=False, ui_context=ui_context))
    list_view.append(TextListItem("", selectable=False, ui_context=ui_context))

    # Demo items
    for i, demo in enumerate(DEMOS):
        list_view.append(TextListItem(f"  {demo['title']}", color=demo['color'], ui_context=ui_context))
        list_view.append(TextListItem(f"    └─ {demo['desc']}", color=18, selectable=False, ui_context=ui_context))
        if i < len(DEMOS) - 1:
            list_view.append(TextListItem("", selectable=False, ui_context=ui_context))

    list_view.append(TextListItem("", selectable=False, ui_context=ui_context))
    list_view.append(SeparatorItem(ui_context))
    list_view.append(TextListItem("  Press 'q' to quit", color=18, selectable=False, ui_context=ui_context))

    # Set selection to first demo
    list_view.set_selected(8)

    return screen, list_view


def main(stdscr):
    screen, list_view = create_menu(stdscr)

    while True:
        screen.draw_visible_views()

        key = stdscr.getch()

        if key == ord('q'):
            break
        elif key == ord('\n') or key == curses.KEY_ENTER:
            # Get selected demo
            selected = list_view.get_selected()
            if selected:
                selected_text = selected.get_text().strip()

                # Find matching demo
                for demo in DEMOS:
                    if demo['title'] in selected_text:
                        # Exit curses temporarily
                        curses.endwin()

                        # Run the demo
                        print(f"\n\n{'='*70}")
                        print(f"Running: {demo['title']}")
                        print(f"File: {demo['file']}")
                        print(f"{'='*70}\n")

                        try:
                            subprocess.run(['python3', demo['file']])
                        except KeyboardInterrupt:
                            pass
                        except Exception as e:
                            print(f"\nError running demo: {e}")

                        # Return to launcher
                        print(f"\n{'='*70}")
                        print("Press Enter to return to launcher...")
                        input()

                        # Reinitialize curses
                        stdscr.clear()
                        stdscr.refresh()
                        screen, list_view = create_menu(stdscr)
                        break
        else:
            list_view.handle_input(key)


if __name__ == '__main__':
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        print("\n\nLauncher exited.")
