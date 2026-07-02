"""Parse the line-oriented test-spec format and map keys to terminal bytes.

A spec is a sequence of directives, one per line:

    size      120x40        # pty resolution (set before launch)
    config    default       # which config/<NAME> template to use
    launch                  # start the app (implicitly settles)
    key       <F2>          # send key tokens
    key       <Down>*3 j    # *N repeats a token; bare chars are literal
    text      "fix"         # type a literal string (e.g. into search)
    mouse     click 20 9    # click|dblclick|rclick at COL ROW (1-based)
    wait      stable        # settle, or `wait 2.5` for a fixed pause
    resize    100x30        # change pty size mid-test
    capture   refs_view     # snapshot -> golden/refs_view.txt
    expect-exit             # assert the app has exited

Blank lines and `#` comments are ignored. Key byte sequences are the
application-mode (DECCKM/keypad) forms, which is what curses enables.
"""

import dataclasses
import re
import shlex


class SpecError(Exception):
    pass


# token name (lower-case, inside <...>) -> bytes, application-mode forms.
NAMED = {
    "up": b"\x1bOA",
    "down": b"\x1bOB",
    "right": b"\x1bOC",
    "left": b"\x1bOD",
    "home": b"\x1b[1~",
    "end": b"\x1b[4~",
    "enter": b"\r",
    "return": b"\r",
    "tab": b"\t",
    "space": b" ",
    "bspace": b"\x7f",
    "backspace": b"\x7f",
    "esc": b"\x1b",
    "escape": b"\x1b",
    "pgup": b"\x1b[5~",
    "pageup": b"\x1b[5~",
    "pgdn": b"\x1b[6~",
    "pagedown": b"\x1b[6~",
    "ins": b"\x1b[2~",
    "insert": b"\x1b[2~",
    "del": b"\x1b[3~",
    "delete": b"\x1b[3~",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f9": b"\x1b[20~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
    "s-f5": b"\x1b[15;2~",
    "c-left": b"\x1b[1;5D",
    "c-right": b"\x1b[1;5C",
    "c-del": b"\x1b[3;5~",
    "c-delete": b"\x1b[3;5~",
}

_COUNT_RE = re.compile(r"^(?P<base>.+)\*(?P<n>\d+)$")
_SIZE_RE = re.compile(r"^(?P<w>\d+)x(?P<h>\d+)$")


# Gap after each mouse byte report. Sent back-to-back, ncurses coalesces a
# press+release at one spot and the release cancels the click; a small pause
# lets the press register first (and still keeps a double-click within 0.3s).
MOUSE_GAP = 0.08


@dataclasses.dataclass
class KeyEvent:
    data: bytes
    is_esc: bool
    post_delay: float = 0.0  # pause after sending this event's bytes


@dataclasses.dataclass
class Directive:
    op: str  # size|config|launch|run|key|text|wait|resize|capture|expect-exit
    lineno: int
    # op-specific payload
    rows: int = 0
    cols: int = 0
    name: str = ""  # capture/config name, or shell command for `run`
    seconds: float = 0.0  # for wait <seconds>; 0 means "stable"
    keys: list = dataclasses.field(default_factory=list)  # for key/text: [KeyEvent]
    args: list = dataclasses.field(default_factory=list)  # extra argv for `launch`


def _token_bytes(token: str) -> bytes:
    """Bytes for a single key token (no count suffix)."""
    low = token.lower()
    if token.startswith("<") and token.endswith(">") and len(token) > 2:
        name = token[1:-1].lower()
        if name in NAMED:
            return NAMED[name]
        raise SpecError(f"unknown key <{token[1:-1]}>")
    if low in NAMED:  # also accept C-Left etc. without brackets
        return NAMED[low]
    if low.startswith("c-") and len(token) == 3 and token[2].isalpha():
        return bytes([ord(token[2].lower()) & 0x1F])
    if len(token) == 1:
        return token.encode("utf-8")
    raise SpecError(f"cannot interpret key token {token!r}")


def _parse_key_line(rest: str) -> list:
    events = []
    for token in rest.split():
        base, count = token, 1
        m = _COUNT_RE.match(token)
        if m:
            try:
                _token_bytes(m.group("base"))  # validate; raises if not a key
                base, count = m.group("base"), int(m.group("n"))
            except SpecError:
                pass  # not a real key*N; fall through to literal handling
        data = _token_bytes(base)
        is_esc = data == b"\x1b"
        events.extend(KeyEvent(data, is_esc) for _ in range(count))
    if not events:
        raise SpecError("empty key directive")
    return events


def _parse_size(arg: str, lineno: int):
    m = _SIZE_RE.match(arg.strip())
    if not m:
        raise SpecError(f"line {lineno}: expected WxH, got {arg!r}")
    return int(m.group("h")), int(m.group("w"))  # rows, cols


def _parse_mouse(rest: str, lineno: int) -> "Directive":
    """`mouse <click|dblclick|rclick> COL ROW` -> X10 mouse byte events.

    COL/ROW are 1-based screen coordinates (ROW == golden line number). Encoded
    as classic X10 mouse reports (`ESC [ M  Cb Cx Cy`, each value + 32), which is
    the only protocol ncurses decodes in a pty here (mode 1000; no motion/wheel,
    so no drag). Emitted as a normal key directive so the runner just writes the
    bytes. rclick is press-only so the context menu stays open for a capture.
    """
    parts = rest.split()
    if len(parts) != 3:
        raise SpecError(f"line {lineno}: mouse needs '<verb> COL ROW'")
    verb, sx, sy = parts
    try:
        x, y = int(sx), int(sy)
    except ValueError:
        raise SpecError(f"line {lineno}: mouse COL ROW must be integers")
    if not (1 <= x <= 223 and 1 <= y <= 223):
        raise SpecError(f"line {lineno}: mouse COL ROW out of X10 range (1..223)")

    def ev(button):  # X10: left press=0, right press=2, release=3
        return KeyEvent(
            b"\x1b[M" + bytes([32 + button, 32 + x, 32 + y]), False, MOUSE_GAP
        )

    verb = verb.lower()
    if verb == "click":
        seq = [ev(0), ev(3)]
    elif verb == "rclick":
        seq = [ev(2)]
    elif verb == "dblclick":
        seq = [ev(0), ev(3), ev(0), ev(3)]  # two presses < 0.3s -> double-click
    else:
        raise SpecError(f"line {lineno}: unknown mouse verb {verb!r}")
    return Directive("key", lineno, keys=seq)


def parse_spec(text: str):
    """Parse spec text into a list[Directive]. Raises SpecError on problems."""
    directives = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        head, _, rest = line.strip().partition(" ")
        rest = rest.strip()
        op = head.lower()

        if op == "size":
            rows, cols = _parse_size(rest, lineno)
            directives.append(Directive("size", lineno, rows=rows, cols=cols))
        elif op == "config":
            directives.append(Directive("config", lineno, name=rest or "default"))
        elif op == "launch":
            directives.append(Directive("launch", lineno, args=rest.split()))
        elif op == "run":
            if not rest:
                raise SpecError(f"line {lineno}: run needs a shell command")
            directives.append(Directive("run", lineno, name=rest))
        elif op == "key":
            directives.append(Directive("key", lineno, keys=_parse_key_line(rest)))
        elif op == "mouse":
            directives.append(_parse_mouse(rest, lineno))
        elif op == "text":
            try:
                parts = shlex.split(rest)
            except ValueError as e:
                raise SpecError(f"line {lineno}: bad text literal ({e})")
            literal = "".join(parts) if parts else ""
            directives.append(
                Directive(
                    "text", lineno, keys=[KeyEvent(literal.encode("utf-8"), False)]
                )
            )
        elif op == "wait":
            if rest in ("", "stable"):
                directives.append(Directive("wait", lineno, seconds=0.0))
            else:
                try:
                    directives.append(Directive("wait", lineno, seconds=float(rest)))
                except ValueError:
                    raise SpecError(f"line {lineno}: wait expects 'stable' or seconds")
        elif op == "resize":
            rows, cols = _parse_size(rest, lineno)
            directives.append(Directive("resize", lineno, rows=rows, cols=cols))
        elif op == "capture":
            if not rest:
                raise SpecError(f"line {lineno}: capture needs a name")
            directives.append(Directive("capture", lineno, name=rest))
        elif op == "expect-exit":
            directives.append(Directive("expect-exit", lineno))
        else:
            raise SpecError(f"line {lineno}: unknown directive {head!r}")
    return directives
