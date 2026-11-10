"""tuikit - a small curses toolkit for keyboard-and-mouse terminal apps.

What it gives you on top of plain curses:

- a z-ordered deck of views: fullscreen views, and floating boxed windows you
  can drag by the title and resize by the edges; modal popups;
- split layouts tiling two views side by side or stacked, with a draggable
  divider;
- list views with selection, scrolling (also horizontal), search and match
  highlighting;
- rows built from segments: buttons with press/drag-off/release semantics,
  toggles, on/off switches, choices, multi-colour text, fillers for alignment;
- dialogs: message boxes, confirmations, text prompts with history, a search
  prompt, popup menus;
- a Midnight-Commander-style function-key bar with flash and progress states;
- a three-tier palette (256 colours / 8 colours / monochrome) chosen from the
  terminal, honouring NO_COLOR;
- clean input events: `KeyEvent`, and `MouseEvent`s with handler-local
  coordinates, double-click detection, drag grabs and motion capture.

Layering (each module imports only those above it):

    keys, mouse, colors        leaves
    segments, items            rows and their pieces
    segmented                  rows of segments (buttons, title bar)
    view, list_view            views
    split, dialogs, status_bar
    screen                     curses lifecycle, deck, routing
    app                        root object + main loop

Minimal app:

    import tuikit

    class Hello(tuikit.App):
        def setup(self):
            view = tuikit.ListView(self, "main")
            view.set_header_item(tuikit.WindowTopBarItem("Hello"))
            for word in ("alpha", "beta", "gamma"):
                view.append(tuikit.TextListItem(word))
            view.show()

    Hello().run()

See demos/ for more.
"""

from tuikit import colors, keys, mouse
from tuikit.app import App, StdLog
from tuikit.dialogs import (
    ConfirmDialog,
    Dialog,
    ErrorDialog,
    InputDialog,
    Menu,
    MessageBox,
    SearchDialog,
)
from tuikit.items import (
    Item,
    MenuItem,
    SeparatorItem,
    SpacerListItem,
    TextListItem,
    UserInputListItem,
)
from tuikit.keys import KeyEvent, ctrl
from tuikit.list_view import ListView
from tuikit.mouse import MouseEvent
from tuikit.screen import Screen
from tuikit.segmented import (
    ButtonRowItem,
    LabeledRow,
    SegmentedListItem,
    WindowTopBarItem,
    button_row,
)
from tuikit.segments import (
    ButtonSegment,
    ChoiceSegment,
    DynamicTextSegment,
    FillerSegment,
    HighlightButtonSegment,
    OnOffToggleSegment,
    RadioSegment,
    RunsSegment,
    Segment,
    TextSegment,
    ToggleSegment,
)
from tuikit.split import SPLIT_OFF, SPLIT_SIDE, SPLIT_STACKED, SplitLayout
from tuikit.status_bar import StatusBar
from tuikit.view import MODE_FULLSCREEN, MODE_WINDOW, View

__version__ = "0.1.0"

__all__ = [
    "App",
    "ButtonRowItem",
    "ButtonSegment",
    "ChoiceSegment",
    "ConfirmDialog",
    "Dialog",
    "DynamicTextSegment",
    "ErrorDialog",
    "FillerSegment",
    "HighlightButtonSegment",
    "InputDialog",
    "Item",
    "KeyEvent",
    "LabeledRow",
    "ListView",
    "MODE_FULLSCREEN",
    "MODE_WINDOW",
    "Menu",
    "MenuItem",
    "MessageBox",
    "MouseEvent",
    "OnOffToggleSegment",
    "RadioSegment",
    "RunsSegment",
    "SPLIT_OFF",
    "SPLIT_SIDE",
    "SPLIT_STACKED",
    "Screen",
    "SearchDialog",
    "Segment",
    "SegmentedListItem",
    "SeparatorItem",
    "SpacerListItem",
    "SplitLayout",
    "StatusBar",
    "StdLog",
    "TextListItem",
    "TextSegment",
    "ToggleSegment",
    "UserInputListItem",
    "View",
    "WindowTopBarItem",
    "button_row",
    "colors",
    "ctrl",
    "keys",
    "mouse",
]
