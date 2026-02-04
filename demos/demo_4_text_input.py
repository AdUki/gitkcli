#!/usr/bin/env python3
"""
Demo 4: Text Input
Demonstrates UserInputListItem with cursor support and text editing
"""
import curses
import sys
sys.path.insert(0, '..')

from ui import (UIContext, Screen, ListView, TextListItem, UserInputListItem,
                SegmentedListItem, TextSegment, ButtonSegment, SeparatorItem)


class TextInputDemo:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.screen = Screen(stdscr)
        self.ui_context = UIContext(self.screen)

        self.messages = []
        self.setup_ui()

    def setup_ui(self):
        # Create list view
        self.list_view = ListView('main', view_mode='fullscreen', ui_context=self.ui_context)
        self.list_view.show()

        # Header
        self.list_view.append(TextListItem("═" * 60, color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("  ⌨️   DEMO 4: Text Input & Chat Interface", color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("  Type in the input field below | Press Enter to send | Press 'q' to quit", color=18, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("═" * 60, color=5, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        # Chat messages area
        self.list_view.append(TextListItem("Chat Messages:", color=4, selectable=False, ui_context=self.ui_context))
        self.list_view.append(SeparatorItem(self.ui_context))

        # Welcome message
        self.list_view.append(TextListItem("  💬  System: Welcome to the chat demo!", color=10, selectable=False, ui_context=self.ui_context))
        self.list_view.append(TextListItem("  💬  System: Type a message and press Enter to send", color=10, selectable=False, ui_context=self.ui_context))
        self.messages_start_index = len(self.list_view.items)

        # Empty line
        self.list_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        self.list_view.append(SeparatorItem(self.ui_context))

        # Input field with label
        input_label = TextListItem("  Enter message:", color=6, selectable=False, ui_context=self.ui_context)
        self.list_view.append(input_label)

        self.input_field = UserInputListItem(color=1, ui_context=self.ui_context)
        self.list_view.append(self.input_field)

        # Submit button (for demonstration)
        submit_btn = SegmentedListItem([
            TextSegment("  ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Send Message ]", self.send_message, color=3, ui_context=self.ui_context),
            TextSegment("  or press Enter", color=18, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.list_view.append(submit_btn)

        # Stats
        self.list_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))
        self.stats_item = TextListItem(f"  Messages sent: 0", color=18, selectable=False, ui_context=self.ui_context)
        self.list_view.append(self.stats_item)

        # Set selection to input field
        self.list_view.set_selected(len(self.list_view.items) - 3)

    def send_message(self):
        text = self.input_field.get_text()
        if text.strip():
            # Add message to chat
            msg_item = TextListItem(f"  👤  You: {text}", color=6, selectable=False, ui_context=self.ui_context)
            self.list_view.items.insert(self.messages_start_index, msg_item)
            self.messages.append(text)
            self.messages_start_index += 1

            # Bot response
            response = self.get_bot_response(text)
            bot_item = TextListItem(f"  🤖  Bot: {response}", color=3, selectable=False, ui_context=self.ui_context)
            self.list_view.items.insert(self.messages_start_index, bot_item)
            self.messages_start_index += 1

            # Update stats
            self.stats_item.set_text(f"  Messages sent: {len(self.messages)}")

            # Clear input
            self.input_field.clear()

            # Scroll to show new messages
            self.list_view.dirty = True

        return True

    def get_bot_response(self, message):
        """Simple bot responses"""
        msg_lower = message.lower()

        if 'hello' in msg_lower or 'hi' in msg_lower:
            return "Hello! How are you today?"
        elif 'bye' in msg_lower:
            return "Goodbye! Have a great day!"
        elif 'how are you' in msg_lower:
            return "I'm doing great, thanks for asking!"
        elif 'what' in msg_lower and ('name' in msg_lower or 'called' in msg_lower):
            return "I'm a demo chatbot built with the UI framework!"
        elif '?' in message:
            return "That's an interesting question! I'm still learning."
        elif len(message) > 50:
            return "Wow, that's a long message! I read every word."
        elif len(message) < 5:
            return "Short and sweet! I like it."
        else:
            return f"You said: '{message}'. That's nice!"

    def run(self):
        while True:
            self.screen.draw_visible_views()

            key = self.stdscr.getch()

            if key == ord('q'):
                # Don't quit if typing in input field
                if self.list_view.get_selected() != self.input_field:
                    break

            # Handle Enter key
            if key == ord('\n') or key == curses.KEY_ENTER:
                self.send_message()
            else:
                self.list_view.handle_input(key)


def main(stdscr):
    demo = TextInputDemo(stdscr)
    demo.run()


if __name__ == '__main__':
    curses.wrapper(main)
