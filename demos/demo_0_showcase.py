#!/usr/bin/env python3
"""
Demo 0: UI Showcase
A visual gallery of all UI components and features - great starting point!
"""
import curses
import sys
sys.path.insert(0, '..')

from ui import (UIContext, Screen, Mouse, ListView, TextListItem, SeparatorItem, SpacerListItem,
                SegmentedListItem, TextSegment, ButtonSegment, ToggleSegment, FillerSegment,
                UserInputListItem)


class UIShowcase:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.screen = Screen(stdscr)
        self.mouse = Mouse(self.screen)
        self.ui_context = UIContext(self.screen, mouse_handler=self.mouse)

        self.button_clicks = 0
        self.toggle_states = {}

        self.setup_ui()

    def setup_ui(self):
        # Create main list view
        self.list_view = ListView('showcase', view_mode='fullscreen', ui_context=self.ui_context)
        self.list_view.show()

        # ═══════════════════════════════════════════════════════════════
        # HEADER
        # ═══════════════════════════════════════════════════════════════
        self.add_header()

        # ═══════════════════════════════════════════════════════════════
        # SECTION 1: COLORS
        # ═══════════════════════════════════════════════════════════════
        self.add_section_title("1. Color Palette")
        self.list_view.append(TextListItem("  Available colors for text styling:", color=18, selectable=False, ui_context=self.ui_context))
        self.list_view.append(SpacerListItem(self.ui_context))

        colors = [
            (1, "Color 1: White (default text)"),
            (2, "Color 2: Red (errors)"),
            (3, "Color 3: Green (success)"),
            (4, "Color 4: Yellow (warnings)"),
            (5, "Color 5: Blue (data)"),
            (6, "Color 6: Green (authors)"),
            (10, "Color 10: Cyan (info)"),
            (18, "Color 18: Gray (debug/dim)"),
        ]

        for color, text in colors:
            self.list_view.append(TextListItem(f"    {text}", color=color, ui_context=self.ui_context))

        # ═══════════════════════════════════════════════════════════════
        # SECTION 2: TEXT ITEMS
        # ═══════════════════════════════════════════════════════════════
        self.add_section_title("2. Text List Items")

        self.list_view.append(TextListItem("    Normal text item (selectable)", color=1, ui_context=self.ui_context))
        self.list_view.append(TextListItem("    Expanded text item (fills width)", color=5, expand=True, ui_context=self.ui_context))
        self.list_view.append(TextListItem("    Dim text item", color=1, dim=True, ui_context=self.ui_context))
        self.list_view.append(TextListItem("    Non-selectable item", color=18, selectable=False, ui_context=self.ui_context))

        # ═══════════════════════════════════════════════════════════════
        # SECTION 3: SEPARATORS & SPACERS
        # ═══════════════════════════════════════════════════════════════
        self.add_section_title("3. Separators & Spacers")

        self.list_view.append(TextListItem("    Items above separator", color=1, ui_context=self.ui_context))
        self.list_view.append(SeparatorItem(self.ui_context))
        self.list_view.append(TextListItem("    Items below separator", color=1, ui_context=self.ui_context))
        self.list_view.append(SpacerListItem(self.ui_context))
        self.list_view.append(TextListItem("    Item after spacer", color=1, ui_context=self.ui_context))

        # ═══════════════════════════════════════════════════════════════
        # SECTION 4: BUTTONS
        # ═══════════════════════════════════════════════════════════════
        self.add_section_title("4. Interactive Buttons")

        self.status_item = TextListItem("    Status: Click a button below!", color=3, selectable=False, ui_context=self.ui_context)
        self.list_view.append(self.status_item)
        self.list_view.append(SpacerListItem(self.ui_context))

        # Single button
        btn1 = SegmentedListItem([
            TextSegment("    Single button: ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Click Me ]", lambda: self.on_button_click("Primary"), color=5, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(btn1)

        # Multiple buttons
        btn2 = SegmentedListItem([
            TextSegment("    Multiple:      ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Info ]", lambda: self.on_button_click("Info"), color=10, ui_context=self.ui_context),
            ButtonSegment("[ Success ]", lambda: self.on_button_click("Success"), color=3, ui_context=self.ui_context),
            ButtonSegment("[ Warning ]", lambda: self.on_button_click("Warning"), color=4, ui_context=self.ui_context),
            ButtonSegment("[ Error ]", lambda: self.on_button_click("Error"), color=2, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(btn2)

        # Aligned button
        btn3 = SegmentedListItem([
            TextSegment("    Right aligned: ", color=1, ui_context=self.ui_context),
            FillerSegment(self.ui_context),
            ButtonSegment("[ →→→ ]", lambda: self.on_button_click("Right"), color=6, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(btn3)

        # ═══════════════════════════════════════════════════════════════
        # SECTION 5: TOGGLES
        # ═══════════════════════════════════════════════════════════════
        self.add_section_title("5. Toggle Switches")

        toggle1 = SegmentedListItem([
            TextSegment("    Dark Mode:     ", color=1, ui_context=self.ui_context),
            ToggleSegment("[ ON  ]", toggled=True, callback=lambda t: self.on_toggle("Dark Mode", t), color=3, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(toggle1)

        toggle2 = SegmentedListItem([
            TextSegment("    Notifications: ", color=1, ui_context=self.ui_context),
            ToggleSegment("[ OFF ]", toggled=False, callback=lambda t: self.on_toggle("Notifications", t), color=2, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(toggle2)

        toggle3 = SegmentedListItem([
            TextSegment("    Auto-save:     ", color=1, ui_context=self.ui_context),
            ToggleSegment("[ ON  ]", toggled=True, callback=lambda t: self.on_toggle("Auto-save", t), color=3, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(toggle3)

        # ═══════════════════════════════════════════════════════════════
        # SECTION 6: TEXT INPUT
        # ═══════════════════════════════════════════════════════════════
        self.add_section_title("6. Text Input Field")

        self.list_view.append(TextListItem("    Type below (click or navigate to input field):", color=18, selectable=False, ui_context=self.ui_context))
        self.list_view.append(SpacerListItem(self.ui_context))

        input_label = SegmentedListItem([
            TextSegment("    Input: ", color=1, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        input_label.is_selectable = False
        self.list_view.append(input_label)

        self.input_field = UserInputListItem(color=5, ui_context=self.ui_context)
        self.input_field.set_text("Edit me with keyboard or mouse!")
        self.list_view.append(self.input_field)

        # ═══════════════════════════════════════════════════════════════
        # SECTION 7: COMPLEX LAYOUTS
        # ═══════════════════════════════════════════════════════════════
        self.add_section_title("7. Complex Segmented Layouts")

        # Status bar style
        status = SegmentedListItem([
            TextSegment("    Status:", color=3, ui_context=self.ui_context),
            TextSegment(" Online", color=3, ui_context=self.ui_context),
            FillerSegment(self.ui_context),
            TextSegment("Users: 42", color=5, ui_context=self.ui_context),
            TextSegment(" | ", color=18, ui_context=self.ui_context),
            TextSegment("CPU: 45%", color=4, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        status.is_selectable = False
        self.list_view.append(status)

        # Menu bar style
        menu = SegmentedListItem([
            ButtonSegment("[ File ]", lambda: self.on_button_click("File"), color=1, ui_context=self.ui_context),
            ButtonSegment("[ Edit ]", lambda: self.on_button_click("Edit"), color=1, ui_context=self.ui_context),
            ButtonSegment("[ View ]", lambda: self.on_button_click("View"), color=1, ui_context=self.ui_context),
            ButtonSegment("[ Help ]", lambda: self.on_button_click("Help"), color=1, ui_context=self.ui_context),
            FillerSegment(self.ui_context),
            TextSegment("v1.0.0", color=18, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(menu)

        # ═══════════════════════════════════════════════════════════════
        # FOOTER
        # ═══════════════════════════════════════════════════════════════
        self.add_section_title("Navigation & Controls")

        self.list_view.append(TextListItem("    Keyboard:", color=4, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("      ↑/↓ or j/k  - Navigate items", color=18, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("      PgUp/PgDn   - Page up/down", color=18, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("      Home/End    - Jump to start/end", color=18, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("      q           - Quit demo", color=18, selectable=False, ui_context=self.ui_context))
        self.list_view.append(SpacerListItem(self.ui_context))
        self.list_view.append(TextListItem("    Mouse:", color=4, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("      Click       - Select items, press buttons", color=18, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("      Scroll      - Scroll the list", color=18, selectable=False, ui_context=self.ui_context))

        self.list_view.append(SeparatorItem(self.ui_context))
        self.click_counter = TextListItem(f"    Total interactions: {self.button_clicks}", color=6, selectable=False, ui_context=self.ui_context)
        self.list_view.append(self.click_counter)

        # Set initial selection
        self.list_view.set_selected(8)

    def add_header(self):
        """Add showcase header"""
        self.list_view.append(TextListItem("╔════════════════════════════════════════════════════════════════════════╗", color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("║                                                                        ║", color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("║              🎨  TERMINAL UI FRAMEWORK - COMPONENT SHOWCASE            ║", color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("║                                                                        ║", color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("║    Explore all available UI components, colors, and interactions!     ║", color=18, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("║                                                                        ║", color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("╚════════════════════════════════════════════════════════════════════════╝", color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(SpacerListItem(self.ui_context))

    def add_section_title(self, title):
        """Add a section separator with title"""
        self.list_view.append(SpacerListItem(self.ui_context))
        self.list_view.append(SeparatorItem(self.ui_context))
        self.list_view.append(TextListItem(f"  {title}", color=4, selectable=False, ui_context=self.ui_context))
        self.list_view.append(SeparatorItem(self.ui_context))
        self.list_view.append(SpacerListItem(self.ui_context))

    def on_button_click(self, name):
        """Handle button clicks"""
        self.button_clicks += 1
        self.status_item.set_text(f"    Status: '{name}' button clicked! (click #{self.button_clicks})")
        self.click_counter.set_text(f"    Total interactions: {self.button_clicks}")
        self.list_view.dirty = True
        return True

    def on_toggle(self, name, toggle_segment):
        """Handle toggle changes"""
        self.button_clicks += 1
        state = "ON" if toggle_segment.toggled else "OFF"
        self.toggle_states[name] = state
        self.status_item.set_text(f"    Status: '{name}' toggled {state} (interaction #{self.button_clicks})")
        self.click_counter.set_text(f"    Total interactions: {self.button_clicks}")
        self.list_view.dirty = True

    def run(self):
        """Main event loop"""
        while True:
            self.screen.draw_visible_views()

            key = self.stdscr.getch()

            if key == ord('q'):
                break
            elif key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()

                    self.mouse.mouse_rel_x = mx - self.mouse.mouse_x
                    self.mouse.mouse_rel_y = my - self.mouse.mouse_y
                    self.mouse.mouse_x = mx
                    self.mouse.mouse_y = my

                    # Determine event type
                    if bstate & curses.BUTTON1_PRESSED:
                        event_type = 'left-click'
                        self.mouse.mouse_left_pressed = True
                    elif bstate & curses.BUTTON1_RELEASED:
                        event_type = 'left-release'
                        self.mouse.mouse_left_pressed = False
                    elif bstate & curses.BUTTON1_DOUBLE_CLICKED:
                        event_type = 'double-click'
                    elif bstate & curses.BUTTON4_PRESSED:
                        event_type = 'wheel-up'
                    elif bstate & (2097152):  # BUTTON5_PRESSED
                        event_type = 'wheel-down'
                    else:
                        continue

                    # Process mouse event
                    self.mouse.process_mouse_event(event_type, self.list_view)

                except curses.error:
                    pass
            else:
                self.list_view.handle_input(key)


def main(stdscr):
    showcase = UIShowcase(stdscr)
    showcase.run()


if __name__ == '__main__':
    curses.wrapper(main)
