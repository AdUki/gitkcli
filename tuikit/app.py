"""App: the root object every view, item and segment reaches (`view.app`,
`item.get_app()`), and the main loop.

    class MyApp(tuikit.App):
        def setup(self):
            self.main = MyListView(self)   # views take the app first
            self.main.show()

    MyApp().run()          # curses.wrapper + loop; returns when the last view
                           # closes or quit() is called

Event loop, per iteration: run `process_background()` (poll worker threads,
timers; return True while busy so the loop polls fast instead of every
IDLE_POLL_MS), redraw if anything changed (input, background work, a view
marked dirty), then read one key event and dispatch it:

1. mouse reports go to `screen.dispatch_mouse()`,
2. a terminal resize to `screen.resize()`,
3. any other key to the active view's `handle_key()`; if it is not consumed,
   to `handle_global_key()` (default: q/Esc close the top view, status-bar
   function keys run their entry).

Override hooks: `setup`, `process_background`, `handle_global_key`,
`show_context_menu`. `log` is replaceable by anything with
debug/info/success/warning/error(str).
"""

from __future__ import annotations

import curses
import logging
import traceback

from tuikit.keys import KeyReader
from tuikit.mouse import MouseTracker
from tuikit.screen import Screen

FAST_POLL_MS = 5  # getch timeout while background work or a flash is live
IDLE_POLL_MS = 100


class StdLog:
    """Default app log: forwards to the stdlib `logging` module. Silent unless
    the program configures a handler (logging's last-resort handler would
    print warnings to stderr, i.e. over the curses screen)."""

    def __init__(self, name="tuikit"):
        self._log = logging.getLogger(name)
        if not self._log.handlers:
            self._log.addHandler(logging.NullHandler())

    def debug(self, txt):
        self._log.debug(txt)

    def info(self, txt):
        self._log.info(txt)

    def success(self, txt):
        self._log.info(txt)

    def warning(self, txt):
        self._log.warning(txt)

    def error(self, txt):
        self._log.error(txt)


class App:
    name = "tuikit"  # shown when shelling out (Screen.run_interactive)
    screen_class = Screen
    status_bar = True  # reserve the bottom row for the function-key bar

    def __init__(self):
        self.running = True
        self.screen: Screen = None
        self.mouse = MouseTracker()
        self.keyboard = KeyReader()
        self.log = StdLog(self.name)

    @property
    def log(self):
        return self._log

    @log.setter
    def log(self, log):
        # The input readers log raw events (at debug level) to the same sink.
        self._log = log
        self.mouse.log = log
        self.keyboard.log = log

    # -- hooks ----------------------------------------------------------------

    def setup(self):
        """Build the views and show the first one. Called by run() once the
        screen exists."""

    def process_background(self) -> bool:
        """Poll background work; True while there is more to do soon."""
        return False

    def handle_global_key(self, event) -> bool:
        """A key no view consumed."""
        key = event.key
        if key in (ord("q"), curses.KEY_EXIT):
            self.screen.hide_active_view()
            return True
        bar = self.screen.status_bar
        action = bar.action_for_key(key) if bar else None
        if action is not None:
            action()
            return True
        return False

    def show_context_menu(self, item, view=None) -> bool:
        """A right-click on `item` (or the keyboard menu key). True if a menu
        was shown."""
        return False

    def copy_to_clipboard(self, text: str):
        try:
            import pyperclip

            pyperclip.copy(text)
        except ImportError:
            self.log.warning(
                "pyperclip module not found. Install with: pip install pyperclip"
            )
        except Exception as e:
            self.log.error(f"Error copying to clipboard: {str(e)}")

    def quit(self):
        self.running = False

    # -- running --------------------------------------------------------------

    def run(self):
        """Start curses, set up, and run the loop until done."""
        return curses.wrapper(self._run_in_curses)

    def _run_in_curses(self, stdscr):
        self.screen = self.screen_class(self, stdscr, status_bar=self.status_bar)
        self.setup()
        self.main_loop()

    def main_loop(self):
        stdscr = self.screen.stdscr
        try:
            user_input = True
            while self.running:
                busy = self.process_background()
                # A flash auto-expires on a timer: keep redrawing while it shows.
                flash = self.screen.flash_active()
                if busy or user_input or flash or self.screen.needs_redraw():
                    try:
                        self.screen.draw_visible_views()
                    except curses.error as e:
                        self.log.warning(
                            f"Curses exception: {str(e)}\n{traceback.format_exc()}"
                        )

                if not self.screen.get_active_view():
                    break

                stdscr.timeout(FAST_POLL_MS if busy or flash else IDLE_POLL_MS)
                event = self.keyboard.read(stdscr)
                user_input = event is not None
                if event is not None:
                    self.dispatch_key(event)
        except KeyboardInterrupt:
            pass

    def dispatch_key(self, event):
        if event.key == curses.KEY_MOUSE:
            for mouse_event in self.mouse.read():
                self.screen.dispatch_mouse(mouse_event)
        elif event.key == curses.KEY_RESIZE:
            self.screen.resize()
        else:
            active = self.screen.get_active_view()
            if active is not None and active.handle_key(event):
                active.dirty = True
            else:
                self.handle_global_key(event)
