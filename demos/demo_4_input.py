#!/usr/bin/env python3
"""Demo 4: text input.

A chat-style log with an inline text field as its last row: type, Enter
sends. The field supports the usual editing keys plus Ctrl+Left/Right (word
jumps), Ctrl+Backspace (delete word), Ctrl+Del (clear) and click-to-place
the cursor. Rows starting with "/" are commands: /find opens a search
prompt (n/N repeat), /clear empties the log, /quit exits.

Keys: type and Enter; Up/Down move off the field (to select a message),
`/`-search works when a message is selected.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tuikit  # noqa: E402
from tuikit import colors  # noqa: E402


class ChatInput(tuikit.UserInputListItem):
    def activate(self):
        text = self.txt.strip()
        self.clear()
        if text:
            self.get_app().send(text)
        return True


class ChatDemo(tuikit.App):
    name = "demo-input"
    status_bar = False  # no function keys: the log gets the whole screen

    def setup(self):
        self.chat = tuikit.ListView(self, "chat")
        self.chat.set_header_item(
            tuikit.WindowTopBarItem("Chat", title_color=colors.ACCENT)
        )
        self.chat.set_search_dialog(tuikit.SearchDialog(self, "chat-search"))
        self.input = ChatInput(colors.SUCCESS)
        self.chat.append(self.input)
        for line in (
            "Welcome! Type a message and press Enter.",
            "Try /find or /clear.",
        ):
            self.post(line, colors.DIM)
        self.chat.set_selected(len(self.chat.items) - 1)
        self.chat.show()

    def post(self, text, color=colors.NORMAL):
        # Insert above the input row, which always stays last (and selected,
        # so typing goes to it).
        self.chat.insert(len(self.chat.items) - 1, tuikit.TextListItem(text, color))
        self.chat.set_selected(len(self.chat.items) - 1, "bottom")

    def send(self, text):
        if text == "/clear":
            self.chat.clear()
            self.chat.append(self.input)
        elif text == "/find":
            self.chat.open_search()
        elif text == "/quit":
            self.quit()
        elif text.startswith("/"):
            self.post(f"unknown command {text}", colors.ERROR)
        else:
            self.post(f"me: {text}")
            self.post(f"echo: {text[::-1]}", colors.WARNING)


if __name__ == "__main__":
    ChatDemo().run()
