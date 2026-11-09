"""The colour palette and its terminal-capability tiers.

Colours are addressed by *role numbers*: small ints naming a colour pair. The
library owns roles 1-9 (below); an app defines its own roles in
APP_FIRST..APP_LAST with `define()` (typically from `Screen.define_colors`).

    attr(role, selected=..., highlighted=..., matched=...)

returns the curses attribute to draw with. Every role has four variants -
normal, highlighted (a marked row / toggled segment), selected (the cursor row)
and selected+highlighted - rendered differently per tier:

    0   monochrome (vt220, NO_COLOR, --no-color): video attributes only
    8   basic (screen, xterm, linux): the 8 ANSI colours; selection and
        highlight become A_REVERSE / A_BOLD
    256 full (xterm-256color, ...): distinct background colours per variant

Module-level state (the active tier, the screen-wide dim) is set once by
`init()`; the palette is per-process, like curses itself.
"""

from __future__ import annotations

import curses
import os

# -- library roles ------------------------------------------------------------
NORMAL = 1  # ordinary text
ERROR = 2  # red: errors, destructive buttons, the close button
SUCCESS = 3  # green
WARNING = 4  # yellow
ACCENT = 5  # blue: the active border / title line
MATCH = 6  # search-match text (rows matching a search are drawn in it)
DIM = 7  # neutral grey: inactive borders, disabled text, dividers
TITLE = 8  # dialog title bars and header buttons (grey; blue when selected)
BANNER = 9  # warning title bar: white on red

APP_FIRST = 10  # first role number free for an application
APP_LAST = 39  # last one (variant pairs are 50/100/150 + role)

# Solid status-bar pairs (no variants).
BAR_FLASH = 40  # success flash: black on green
BAR_LABEL = 41  # function-key bar label cells: black on cyan
BAR_WORK = 42  # in-progress bar: white on blue

# -- state ----------------------------------------------------------------------
depth = 256  # active tier: 0, 8 or 256
# When True every attr() gets A_DIM, so the whole screen renders dimmed (the
# status bar is exempt: it is painted with bar_attr()). Used while a blocking
# command freezes the UI thread.
dimmed = False
_default_bg = -1  # "terminal default"; COLOR_BLACK if use_default_colors fails


def _to_pal(c: int) -> int:
    """Map a palette index to one the active tier can render. 256-only indices
    (greys 245/247, blue 20, ...) collapse to white below the full tier; the -1
    'default' sentinel becomes the tier's default background."""
    if c < 0:
        return _default_bg
    if depth >= 256 or c < 8:
        return c
    return curses.COLOR_WHITE


def define(
    role: int,
    nfg: int,
    nbg: int = -1,
    hfg: int = -1,
    hbg: int = -1,
    sfg: int = -1,
    sbg: int = -1,
    shfg: int = -1,
    shbg: int = -1,
) -> None:
    """Define `role` as foreground/background pairs for its four variants:
    normal (n), highlighted (h), selected (s) and selected+highlighted (sh).
    A -1 keeps the previous variant's colour (or the default background)."""
    if depth == 0:
        return  # monochrome: no colour pairs exist
    curses.init_pair(role, _to_pal(nfg), _to_pal(nbg))
    if depth < 256:
        # Selection / highlight are rendered with video attributes in attr();
        # the background-variant pairs below need the 256-colour palette.
        return
    fg, bg = nfg, nbg
    if hfg >= 0:
        fg = hfg
    bg = hbg if hbg >= 0 else 20
    curses.init_pair(50 + role, fg, bg)
    if sfg >= 0:
        fg = sfg
    bg = sbg if sbg >= 0 else 235
    curses.init_pair(100 + role, fg, bg)
    if shfg >= 0:
        fg = shfg
    bg = shbg if shbg >= 0 else 21
    curses.init_pair(150 + role, fg, bg)


def init(force_mono: bool = False) -> None:
    """Pick the tier from the terminal and define the library roles. Called by
    Screen after curses is initialized."""
    global depth, _default_bg
    no_color_env = os.environ.get("NO_COLOR") not in (None, "")
    if force_mono or no_color_env or not curses.has_colors():
        depth = 0
    else:
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            _default_bg = curses.COLOR_BLACK
        depth = 256 if curses.COLORS >= 256 else 8

    define(NORMAL, curses.COLOR_WHITE)
    define(ERROR, curses.COLOR_RED)
    define(SUCCESS, curses.COLOR_GREEN)
    define(WARNING, curses.COLOR_YELLOW)
    define(ACCENT, curses.COLOR_BLUE)
    define(MATCH, curses.COLOR_YELLOW)
    define(DIM, 245)
    define(
        TITLE,
        curses.COLOR_BLACK,
        245,  # normal: black on grey
        -1,
        247,  # highlighted: lighter grey
        curses.COLOR_WHITE,
        curses.COLOR_BLUE,  # selected: white on blue
        -1,
        20,
    )
    define(
        BANNER,
        curses.COLOR_WHITE,
        curses.COLOR_RED,
        -1,
        curses.COLOR_RED,
        curses.COLOR_WHITE,
        curses.COLOR_RED,
    )
    if depth:  # monochrome uses reverse video for the bars instead
        curses.init_pair(BAR_LABEL, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(BAR_FLASH, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(
            BAR_WORK,
            curses.COLOR_WHITE,
            # the highlighted-row blue (the 256-tier highlight bg in define())
            20 if depth >= 256 else curses.COLOR_BLUE,
        )


def bar_attr(pair: int) -> int:
    """Attribute for a solid status-bar pair; reverse video when monochrome."""
    if depth == 0:
        return curses.A_REVERSE
    return curses.color_pair(pair)


def attr(
    role: int,
    selected: bool = False,
    highlighted: bool = False,
    matched: bool = False,
    bold=None,
    dim: bool = False,
) -> int:
    """Curses attribute for `role` in the given state. `highlighted` is the
    generic emphasis-background slot: rows pass their `marked` flag, toggle
    segments their `toggled` state. `matched` (a search hit) turns plain and
    dim text into MATCH. `bold=None` means "bold when selected"."""
    if matched:
        bold = True
        if role == NORMAL:
            role = MATCH
        elif role == DIM:
            role = MATCH
            dim = True
    if depth >= 256:
        if selected and highlighted:
            color = curses.color_pair(150 + role)
        elif selected:
            color = curses.color_pair(100 + role)
        elif highlighted:
            color = curses.color_pair(50 + role)
        else:
            color = curses.color_pair(role)
    elif depth >= 8:
        # No background-variant pairs on this terminal: paint the cursor row in
        # reverse video and a highlighted row in bold, over the base pair.
        color = curses.color_pair(role)
        if selected:
            color |= curses.A_REVERSE
        if highlighted:
            color |= curses.A_BOLD
    else:
        # Monochrome: every semantic colour collapses to a video attribute.
        color = curses.A_DIM if role == DIM else curses.A_NORMAL
        if role == MATCH:
            color = curses.A_BOLD
        if selected:
            color |= curses.A_REVERSE
        if highlighted:
            color |= curses.A_BOLD
        if matched:
            color |= curses.A_UNDERLINE
    if bold or (selected and bold is None):
        color |= curses.A_BOLD
    if dim or dimmed:
        color |= curses.A_DIM
    return color
