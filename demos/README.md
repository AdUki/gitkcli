# tuikit demos

Small apps built on [`tuikit`](../tuikit/__init__.py), the curses UI library
gitkcli is made of. Run them from anywhere (they find the library next to
this folder):

    python3 demos/launcher.py           # pick one from a list
    python3 demos/demo_1_list.py

| demo | shows |
|------|-------|
| `demo_1_list.py` | `ListView`, `WindowTopBarItem`, separators, `SearchDialog` (`/`, n/N), a `Menu` on right-click / F7, the function-key bar and flash messages |
| `demo_2_widgets.py` | segmented rows: `OnOffToggleSegment`, `ChoiceSegment`, a radio group of `ToggleSegment`s, `DynamicTextSegment`, `RunsSegment`; a keyboard-navigable `button_row`; `InputDialog` (with history), `ConfirmDialog`, `ErrorDialog` |
| `demo_3_windows.py` | `SplitLayout` (side / stacked, drag the divider), floating `MODE_WINDOW` views (drag, resize, double-click to maximize, [X]), `show_working` + `process_background` |
| `demo_4_input.py` | an inline `UserInputListItem` (word motion, word delete, click to place the cursor), `ListView.insert` |
| `demo_5_dashboard.py` | live updates from `process_background`, rows that rebuild their segments on every draw |
| `launcher.py` | `Screen.run_interactive`: suspend the UI, run a child on the real terminal, resume |

Demos 1-4 also run in the golden-screen suite (`test/cases/demo_*`), which
keeps the library's public API working.

## The pieces

    App            root object: owns screen, mouse, keyboard, log; runs the loop
    Screen         curses lifecycle, the z-ordered deck of views, mouse routing
    View           a window: fullscreen or floating (drag/resize), or a popup
    ListView       scrollable, selectable rows, search, copy
    Item           a row: TextListItem, SeparatorItem, UserInputListItem, ...
    SegmentedListItem   a row made of Segments: TextSegment, ButtonSegment, ...
    Dialog, Menu, InputDialog, SearchDialog, ConfirmDialog, ErrorDialog
    SplitLayout    two views tiled side by side or stacked
    StatusBar      the function-key bar (flash / working states)

Input is delivered as events: `handle_key(KeyEvent)` and
`handle_mouse(MouseEvent)` return True when they consumed it. A mouse event's
`x`/`y` are local to the receiver (a row gets its text column and row index);
a press a row handles grabs the drag, so the release comes back to it.
