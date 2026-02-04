#!/usr/bin/env python3
"""
Demo 2: Interactive Segments
Demonstrates buttons, toggles, and segmented list items
"""
import curses
import sys
sys.path.insert(0, '..')

from ui import (UIContext, Screen, Mouse, ListView, TextListItem, SeparatorItem,
                SegmentedListItem, TextSegment, ButtonSegment, ToggleSegment, FillerSegment)


class InteractiveDemo:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.screen = Screen(stdscr)
        self.mouse = Mouse(self.screen)
        self.ui_context = UIContext(self.screen, mouse_handler=self.mouse)

        self.click_count = 0
        self.status_text = "Click a button or toggle to interact!"

        self.setup_ui()

    def setup_ui(self):
        # Create list view
        self.list_view = ListView('main', view_mode='fullscreen', ui_context=self.ui_context)
        self.list_view.show()

        # Header
        self.list_view.append(TextListItem("═" * 60, color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("  🎮  DEMO 2: Interactive Segments (Buttons & Toggles)", color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("  Use mouse to click buttons and toggles | Press 'q' to quit", color=18, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("═" * 60, color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        # Status line (will be updated)
        self.status_item = TextListItem(f"Status: {self.status_text}", color=3, ui_context=self.ui_context)
        self.list_view.append(self.status_item)

        self.list_view.append(SeparatorItem(self.ui_context))

        # Button examples
        self.list_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("BUTTONS:", color=4, selectable=False, ui_context=self.ui_context))

        # Simple button
        btn1 = SegmentedListItem([
            TextSegment("  Click me: ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Button 1 ]", lambda: self.on_button_click(1), color=5, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(btn1)

        # Multiple buttons
        btn2 = SegmentedListItem([
            TextSegment("  Multiple: ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Red ]", lambda: self.on_color_click("Red"), color=2, ui_context=self.ui_context),
            ButtonSegment("[ Green ]", lambda: self.on_color_click("Green"), color=3, ui_context=self.ui_context),
            ButtonSegment("[ Blue ]", lambda: self.on_color_click("Blue"), color=5, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(btn2)

        # Button with filler
        btn3 = SegmentedListItem([
            TextSegment("  Aligned:  ", color=1, ui_context=self.ui_context),
            FillerSegment(self.ui_context),
            ButtonSegment("[ Right Button ]", lambda: self.on_button_click(3), color=10, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(btn3)

        self.list_view.append(SeparatorItem(self.ui_context))

        # Toggle examples
        self.list_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("TOGGLES:", color=4, selectable=False, ui_context=self.ui_context))

        # Simple toggles
        toggle1 = SegmentedListItem([
            TextSegment("  Feature A: ", color=1, ui_context=self.ui_context),
            ToggleSegment("[ ON  ]", toggled=True, callback=lambda t: self.on_toggle("A", t), color=3, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(toggle1)

        toggle2 = SegmentedListItem([
            TextSegment("  Feature B: ", color=1, ui_context=self.ui_context),
            ToggleSegment("[ OFF ]", toggled=False, callback=lambda t: self.on_toggle("B", t), color=2, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(toggle2)

        # Complex item with multiple toggles
        toggle3 = SegmentedListItem([
            TextSegment("  Options:  ", color=1, ui_context=self.ui_context),
            ToggleSegment("[1]", toggled=True, callback=lambda t: self.on_toggle("1", t), color=5, ui_context=self.ui_context),
            ToggleSegment("[2]", toggled=False, callback=lambda t: self.on_toggle("2", t), color=5, ui_context=self.ui_context),
            ToggleSegment("[3]", toggled=True, callback=lambda t: self.on_toggle("3", t), color=5, ui_context=self.ui_context),
            FillerSegment(self.ui_context),
            TextSegment(f"Clicks: {self.click_count}", color=18, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(toggle3)

        self.list_view.append(SeparatorItem(self.ui_context))

        # Counter display
        self.click_counter_item = TextListItem(f"  Total clicks: {self.click_count}", color=6, ui_context=self.ui_context)
        self.list_view.append(self.click_counter_item)

        self.list_view.set_selected(6)

    def on_button_click(self, btn_id):
        self.click_count += 1
        self.status_text = f"Button {btn_id} clicked! (#{self.click_count})"
        self.update_display()
        return True

    def on_color_click(self, color):
        self.click_count += 1
        self.status_text = f"{color} button clicked! (#{self.click_count})"
        self.update_display()
        return True

    def on_toggle(self, name, toggle_segment):
        self.click_count += 1
        state = "ON" if toggle_segment.toggled else "OFF"
        self.status_text = f"Toggle {name} is now {state} (#{self.click_count})"
        self.update_display()

    def update_display(self):
        self.status_item.set_text(f"Status: {self.status_text}")
        self.click_counter_item.set_text(f"  Total clicks: {self.click_count}")
        self.list_view.dirty = True

    def run(self):
        while True:
            self.screen.draw_visible_views()

            key = self.stdscr.getch()

            if key == ord('q'):
                break
            elif key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()

                    # Update mouse position
                    self.mouse.mouse_rel_x = mx - self.mouse.mouse_x
                    self.mouse.mouse_rel_y = my - self.mouse.mouse_y
                    self.mouse.mouse_x = mx
                    self.mouse.mouse_y = my

                    # Determine event type
                    if bstate & curses.BUTTON1_PRESSED:
                        event_type = 'left-click'
                        self.mouse.mouse_left_pressed = True
                        self.mouse.mouse_click_x = self.mouse.mouse_x
                        self.mouse.mouse_click_y = self.mouse.mouse_y
                    elif bstate & curses.BUTTON1_RELEASED:
                        event_type = 'left-release'
                        self.mouse.mouse_left_pressed = False
                    elif bstate & curses.BUTTON1_DOUBLE_CLICKED:
                        event_type = 'double-click'
                    else:
                        continue

                    # Process mouse event
                    self.mouse.process_mouse_event(event_type, self.list_view)

                except curses.error:
                    pass
            else:
                self.list_view.handle_input(key)


def main(stdscr):
    demo = InteractiveDemo(stdscr)
    demo.run()


if __name__ == '__main__':
    curses.wrapper(main)
