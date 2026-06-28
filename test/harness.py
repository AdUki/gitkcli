"""Drive gitkcli under a fixed-size pseudo-terminal and read the rendered screen.

The real curses app is launched on a pty whose window size is pinned *before* the
child starts, so curses initialises at exactly the resolution a test asks for.
All output bytes are fed to a `pyte` VT100 emulator; `capture_ansi()` then renders
the emulated screen back to raw terminal format (text + inline ANSI colour) -- the
thing goldens are compared against (so colour, e.g. the selection highlight, is
asserted). `capture()` is the plain-text variant.

Determinism hinges on `settle()`: it waits until the pty has produced no output
for a quiet window, which is how we know the async commit stream has finished,
the one-shot scroll-to-HEAD has fired, and the screen has stopped changing.
"""

import codecs
import errno
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

import pyte

# Quiet window (s) that means "the screen has stopped changing". Must comfortably
# exceed the app's 100ms idle getch tick. Bumped a bit for headroom on busy hosts.
QUIET = 0.5
# Hard ceiling for a single settle (s). The startup commit stream is the worst
# case (~300 commits) and still completes well under this.
TIMEOUT = 20.0
# Pause (s) after an <Esc> before the next key, so the app's manual escape parser
# (set_escdelay 20ms) sees a lone Esc instead of merging it into the next key.
ESC_GAP = 0.10


def _set_winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


_NAMED = {"black": 0, "red": 1, "green": 2, "brown": 3, "yellow": 3,
          "blue": 4, "magenta": 5, "cyan": 6, "white": 7}


def _color_sgr(color, fg):
    """One SGR colour parameter for a pyte fg/bg value (name / bright / hex)."""
    if not color or color == "default":
        return "39" if fg else "49"
    if color in _NAMED:
        return str((30 if fg else 40) + _NAMED[color])
    if color.startswith("bright") and color[6:] in _NAMED:
        return str((90 if fg else 100) + _NAMED[color[6:]])
    if len(color) == 6:
        try:
            r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
            return ("38" if fg else "48") + ";2;%d;%d;%d" % (r, g, b)
        except ValueError:
            pass
    return "39" if fg else "49"


def _sgr(state):
    """ANSI SGR for a (fg, bg, bold, reverse, italics, underscore) cell state.
    Starts with a reset so each coloured run is self-contained."""
    fg, bg, bold, rev, ital, under = state
    codes = ["0"]
    for on, code in ((bold, "1"), (ital, "3"), (under, "4"), (rev, "7")):
        if on:
            codes.append(code)
    codes.append(_color_sgr(fg, True))
    codes.append(_color_sgr(bg, False))
    return "\x1b[" + ";".join(codes) + "m"


class Harness:
    """A gitkcli process on a pty plus its emulated screen."""

    def __init__(self, argv, cwd, env, rows, cols):
        self.rows = rows
        self.cols = cols
        self.pid = None
        self.master = -1
        self._alive = False
        self._status = None

        self.screen = pyte.Screen(cols, rows)
        # Decode UTF-8 ourselves (incrementally, so multi-byte chars split across
        # reads survive) and feed a text Stream with use_utf8=False. That keeps
        # pyte's DEC-special-graphics charset mapping active -- curses draws popup
        # borders with the alternate charset (ESC(0 q -> "─"), which a ByteStream
        # in UTF-8 mode would render as raw letters.
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.stream = pyte.Stream(self.screen)
        self.stream.use_utf8 = False
        # Raw output bytes kept so --live can replay them verbatim to the real
        # terminal -- they carry the actual SGR colours and the alt-screen /
        # cursor / mouse mode sets that pyte's plain-text grid throws away.
        self._raw = bytearray()

        master, slave = pty.openpty()
        # Pin the size on the tty before the child execs so curses' first
        # getmaxyx() already reports (rows, cols).
        _set_winsize(slave, rows, cols)

        pid = os.fork()
        if pid == 0:
            # --- child ---
            try:
                os.close(master)
                os.login_tty(slave)  # setsid + controlling tty + dup to 0/1/2
                os.chdir(cwd)
                os.execvpe(argv[0], argv, env)
            except BaseException:
                os._exit(127)

        # --- parent ---
        os.close(slave)
        self.master = master
        self.pid = pid
        self._alive = True

    # -- lifecycle ---------------------------------------------------------

    def _reap(self):
        """Non-blocking child status check; marks the harness dead on exit."""
        if self.pid is None:
            return
        try:
            wpid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            self._alive = False
            return
        if wpid == self.pid:
            self._alive = False
            self._status = status

    def is_alive(self):
        self._reap()
        return self._alive

    def wait_exit(self, timeout=5.0):
        """Block until the child exits (e.g. after F10). Returns True on exit."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Keep draining so the pty buffer never blocks the child on write.
            self._drain_once(0.05)
            self._reap()
            if not self._alive:
                return True
        return False

    def close(self):
        """Terminate the child and release the pty."""
        if self.pid is not None and self._alive:
            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except OSError:
                pass
            self._alive = False
        if self.master >= 0:
            try:
                os.close(self.master)
            except OSError:
                pass
            self.master = -1

    # -- io ----------------------------------------------------------------

    def _drain_once(self, timeout):
        """Read at most one chunk of pty output. Returns True if bytes were read."""
        if self.master < 0:
            return False
        try:
            r, _, _ = select.select([self.master], [], [], timeout)
        except (OSError, ValueError):
            self._alive = False
            return False
        if not r:
            return False
        try:
            data = os.read(self.master, 65536)
        except OSError as e:
            # EIO is the normal signal that the child closed its side.
            if e.errno != errno.EIO:
                raise
            data = b""
        if not data:
            self._alive = False
            return False
        self._raw += data
        text = self._decoder.decode(data)
        if text:
            self.stream.feed(text)
        return True

    def send(self, data: bytes):
        if self.master >= 0:
            os.write(self.master, data)

    def settle(self, quiet=QUIET, timeout=TIMEOUT, require_output=False):
        """Drain output until the screen goes quiet for `quiet` seconds.

        Returns when no output arrives for the quiet window (settled), the
        overall timeout elapses, or the child exits. With `require_output`, a
        quiet window before *any* output is ignored -- used at launch, where the
        child may take a moment (python import + curses init) before its first
        paint, so we must not mistake that startup latency for a stable screen.
        """
        deadline = time.monotonic() + timeout
        got_any = False
        while time.monotonic() < deadline:
            if self._drain_once(quiet):
                got_any = True
                continue
            # Quiet window elapsed with no bytes.
            if require_output and not got_any and self._alive:
                continue  # still waiting for the first paint
            return
        # Timed out: best-effort final drain so capture sees the latest bytes.
        while self._drain_once(0.0):
            pass

    # -- screen ------------------------------------------------------------

    def capture(self):
        """Return the emulated screen as a list of plain-text rows.

        Each row is right-trimmed; trailing all-blank rows are dropped so goldens
        stay compact and a change at the bottom still shows up as added lines.
        """
        lines = [row.rstrip() for row in self.screen.display]
        while lines and lines[-1] == "":
            lines.pop()
        return lines

    def capture_ansi(self):
        """The settled screen in raw terminal format: text with inline ANSI SGR
        colour codes, reconstructed from pyte's per-cell attributes. Open a
        golden with `less -R` to see it in colour. This is what goldens store, so
        colour-only state (selection background, ref/diff colours, ...) is
        asserted. Deterministic given the pinned pyte/terminfo palette.

        Trailing cells that are a plain default-coloured space are trimmed (like
        capture() trims trailing spaces); a selection highlighted to the screen
        edge keeps its coloured trailing cells.
        """
        buf = self.screen.buffer
        default = ("default", "default", False, False, False, False)

        def st(c):
            return (c.fg, c.bg, bool(c.bold), bool(c.reverse),
                    bool(c.italics), bool(c.underscore))

        lines = []
        for y in range(self.rows):
            last = -1
            for x in range(self.cols):
                c = buf[y][x]
                if c.data != " " or st(c) != default:
                    last = x
            if last < 0:
                lines.append("")
                continue
            out, cur = [], default
            for x in range(last + 1):
                c = buf[y][x]
                s = st(c)
                if s != cur:
                    out.append(_sgr(s))
                    cur = s
                out.append(c.data)
            if cur != default:
                out.append("\x1b[0m")
            lines.append("".join(out))
        while lines and lines[-1] == "":
            lines.pop()
        return lines

    def resize(self, rows, cols):
        self.rows = rows
        self.cols = cols
        if self.master >= 0:
            _set_winsize(self.master, rows, cols)
        if self.pid is not None and self._alive:
            try:
                os.kill(self.pid, signal.SIGWINCH)
            except OSError:
                pass
        self.screen.resize(rows, cols)

    # -- live debugging ----------------------------------------------------

    def bridge(self):
        """Hand the real terminal to the child until it exits (for --live).

        Copies real-stdin -> pty and pty -> real-stdout with stdin in raw mode,
        so the user can drive the app from the exact replayed end-state. The pty
        keeps the test's fixed size, faithfully reproducing the captured frame.
        """
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            sys.stderr.write("--live needs a real terminal (stdin/stdout are "
                             "not a tty); nothing to hand over.\n")
            return
        stdin_fd = sys.stdin.fileno()
        old = termios.tcgetattr(stdin_fd)
        try:
            import tty

            tty.setraw(stdin_fd)
            # Replay the app's real output (colours + alt-screen/cursor/mouse
            # mode sets) rather than pyte's colourless text grid, so the live
            # frame matches -- selection is only a background colour, so a
            # text-only repaint would hide the cursor row entirely.
            buf = bytes(self._raw)
            while buf:
                buf = buf[os.write(sys.stdout.fileno(), buf):]
            while self.is_alive() and self.master >= 0:
                try:
                    r, _, _ = select.select([self.master, stdin_fd], [], [], 0.2)
                except OSError:
                    break
                if self.master in r:
                    try:
                        data = os.read(self.master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    os.write(sys.stdout.fileno(), data)
                if stdin_fd in r:
                    data = os.read(stdin_fd, 65536)
                    if data:
                        self.send(data)
        finally:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old)
