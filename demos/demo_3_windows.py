#!/usr/bin/env python3
"""
Demo 3: Windowed Views
Demonstrates multiple windows, resizing, and window management
"""
import curses
import sys
sys.path.insert(0, '..')

from ui import (UIContext, Screen, Mouse, ListView, TextListItem, WindowTopBarItem,
                SegmentedListItem, TextSegment, ButtonSegment, FillerSegment)


class WindowDemo:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.screen = Screen(stdscr)
        self.mouse = Mouse(self.screen)
        self.ui_context = UIContext(self.screen, mouse_handler=self.mouse)

        self.setup_ui()

    def setup_ui(self):
        lines, cols = self.screen.getmaxyx()

        # Background view (fullscreen)
        self.bg_view = ListView('background', view_mode='fullscreen', ui_context=self.ui_context)
        self.bg_view.show()

        self.bg_view.append(TextListItem("═" * cols, color=5, selectable=False, ui_context=self.ui_context))
        self.bg_view.append(TextListItem("  🪟  DEMO 3: Multiple Windows & Resizing", color=5, selectable=False, ui_context=self.ui_context))
        self.bg_view.append(TextListItem("  Click window edges to resize | Drag title bar to move | Double-click title to toggle fullscreen", color=18, selectable=False, ui_context=self.ui_context))
        self.bg_view.append(TextListItem("  Press 1-4 to show different windows | Press 'q' to quit", color=18, selectable=False, ui_context=self.ui_context))
        self.bg_view.append(TextListItem("═" * cols, color=5, selectable=False, ui_context=self.ui_context))

        for i in range(6, 25):
            self.bg_view.append(TextListItem(f"Background row {i}", color=18, selectable=False, ui_context=self.ui_context))

        # Window 1: Top-left
        self.win1 = ListView('window1', view_mode='window',
                            x=5, y=3, width=40, height=12, ui_context=self.ui_context)
        header1 = WindowTopBarItem("Window 1: Red Theme",
                                   color=30,
                                   ui_context=self.ui_context,
                                   on_close_click=lambda: self.close_window(self.win1),
                                   on_double_click=lambda: self.toggle_window(self.win1))
        self.win1.set_header_item(header1)

        for i in range(20):
            self.win1.append(TextListItem(f"Red item {i+1}", color=2, ui_context=self.ui_context))

        # Window 2: Top-right
        self.win2 = ListView('window2', view_mode='window',
                            x=cols-45, y=3, width=40, height=12, ui_context=self.ui_context)
        header2 = WindowTopBarItem("Window 2: Green Theme",
                                   color=30,
                                   ui_context=self.ui_context,
                                   on_close_click=lambda: self.close_window(self.win2),
                                   on_double_click=lambda: self.toggle_window(self.win2))
        self.win2.set_header_item(header2)

        for i in range(20):
            self.win2.append(TextListItem(f"Green item {i+1}", color=3, ui_context=self.ui_context))

        # Window 3: Bottom-left
        self.win3 = ListView('window3', view_mode='window',
                            x=5, y=17, width=40, height=12, ui_context=self.ui_context)
        header3 = WindowTopBarItem("Window 3: Blue Theme",
                                   color=30,
                                   ui_context=self.ui_context,
                                   on_close_click=lambda: self.close_window(self.win3),
                                   on_double_click=lambda: self.toggle_window(self.win3))
        self.win3.set_header_item(header3)

        for i in range(20):
            self.win3.append(TextListItem(f"Blue item {i+1}", color=5, ui_context=self.ui_context))

        # Window 4: Center (with control panel)
        self.win4 = ListView('window4', view_mode='window',
                            x=int(cols/2)-25, y=8, width=50, height=15, ui_context=self.ui_context)
        header4 = WindowTopBarItem("Control Panel",
                                   color=30,
                                   ui_context=self.ui_context,
                                   on_close_click=lambda: self.close_window(self.win4),
                                   on_double_click=lambda: self.toggle_window(self.win4))
        self.win4.set_header_item(header4)

        self.win4.append(TextListItem("", selectable=False, ui_context=self.ui_context))
        self.win4.append(TextListItem("  Window Controls:", color=4, selectable=False, ui_context=self.ui_context))
        self.win4.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        # Add control buttons
        btn1 = SegmentedListItem([
            TextSegment("  Show Window 1: ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Show ]", lambda: self.show_window(self.win1), color=2, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.win4.append(btn1)

        btn2 = SegmentedListItem([
            TextSegment("  Show Window 2: ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Show ]", lambda: self.show_window(self.win2), color=3, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.win4.append(btn2)

        btn3 = SegmentedListItem([
            TextSegment("  Show Window 3: ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Show ]", lambda: self.show_window(self.win3), color=5, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.win4.append(btn3)

        self.win4.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        btn_all = SegmentedListItem([
            TextSegment("  Show All:      ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Show All Windows ]", self.show_all_windows, color=6, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.win4.append(btn_all)

    def show_window(self, window):
        window.show()
        return True

    def close_window(self, window):
        window.hide()
        return True

    def toggle_window(self, window):
        window.toggle_window_mode()
        return True

    def show_all_windows(self):
        self.win1.show()
        self.win2.show()
        self.win3.show()
        return True

    def run(self):
        # Show control panel initially
        self.win4.show()

        while True:
            self.screen.draw_visible_views()

            key = self.stdscr.getch()

            if key == ord('q'):
                break
            elif key == ord('1'):
                self.win1.show()
            elif key == ord('2'):
                self.win2.show()
            elif key == ord('3'):
                self.win3.show()
            elif key == ord('4'):
                self.win4.show()
            elif key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()

                    self.mouse.mouse_rel_x = mx - self.mouse.mouse_x
                    self.mouse.mouse_rel_y = my - self.mouse.mouse_y
                    self.mouse.mouse_x = mx
                    self.mouse.mouse_y = my

                    if bstate & curses.BUTTON1_PRESSED:
                        event_type = 'left-click'
                        self.mouse.mouse_left_pressed = True
                    elif bstate & curses.BUTTON1_RELEASED:
                        event_type = 'left-release'
                        self.mouse.mouse_left_pressed = False
                    elif bstate & curses.BUTTON1_DOUBLE_CLICKED:
                        event_type = 'double-click'
                    elif bstate & (curses.REPORT_MOUSE_POSITION | curses.BUTTON1_PRESSED):
                        event_type = 'left-move'
                    else:
                        continue

                    active_view = self.screen.get_active_view()
                    if active_view:
                        self.mouse.process_mouse_event(event_type, active_view)

                except curses.error:
                    pass
            else:
                active_view = self.screen.get_active_view()
                if active_view:
                    active_view.handle_input(key)


def main(stdscr):
    demo = WindowDemo(stdscr)
    demo.run()


if __name__ == '__main__':
    curses.wrapper(main)
