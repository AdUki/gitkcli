"""
================================================================================
 READ THIS BEFORE ADDING ANYTHING TO THIS FILE
================================================================================

This project tests with GOLDEN-SCREEN INTEGRATION TESTS (test/cases/*). Those
render the real app on a pty and diff the actual screen the user sees. They are
the oracle. They survive refactors (you can rewrite the internals freely as long
as the screen is unchanged), and they catch real, user-visible regressions.

Unit tests are the EXCEPTION, not the rule. They couple tests to implementation
details, rot fast, and tax every future change for little benefit. We deleted a
big pile of them on purpose.

==> DO NOT ADD A UNIT TEST UNLESS ALL THREE ARE TRUE: <==
    1. A golden-screen test genuinely CANNOT cover it — the screen literally
       cannot reveal the logic (e.g. a text-field cursor column, a colour-tier
       mapping the pty can't render, a coordinate->object hit-test).
    2. It tests stable, PURE logic — no I/O, no mocks of internals, and it will
       NOT break when you harmlessly refactor.
    3. It can actually catch a real regression the golden suite would miss.

If you are about to assert an exact argv, a call order, or some internal call
shape: STOP. Write a golden-screen test instead. When in doubt: golden screen.

The handful below are the only things that pass that bar. Keep it that way.
================================================================================
"""

import contextlib
import curses
import os
import sys

# Make the repo root importable regardless of how pytest is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

from gitk.screen import Screen
from gitk.segments import TextSegment
from gitk.segmented_items import SegmentedListItem
from gitk.items import UserInputListItem


# --- Screen._to_pal colour-tier degradation ----------------------------------
# Golden-impossible: the pty harness renders in ONE colour configuration, so it
# can never show how a colour index maps in the 8-colour / monochrome tiers.
# (color_depth / _default_bg are class attributes; save+restore so the chosen
# tier doesn't leak into other tests.)

@contextlib.contextmanager
def _screen_tier(depth, default_bg=-1):
    odepth, obg = Screen.color_depth, Screen._default_bg
    Screen.color_depth, Screen._default_bg = depth, default_bg
    try:
        yield
    finally:
        Screen.color_depth, Screen._default_bg = odepth, obg

def test_to_pal_full_tier_passes_through():
    with _screen_tier(256):
        assert Screen._to_pal(5) == 5
        assert Screen._to_pal(20) == 20       # 256-only index preserved
        assert Screen._to_pal(247) == 247

def test_to_pal_8_and_mono_collapse_high_indices_to_white():
    for depth in (8, 0):
        with _screen_tier(depth):
            assert Screen._to_pal(5) == 5      # the 8 base ANSI colours survive
            assert Screen._to_pal(20) == curses.COLOR_WHITE
            assert Screen._to_pal(247) == curses.COLOR_WHITE

def test_to_pal_negative_is_the_default_bg():
    with _screen_tier(8, default_bg=-1):
        assert Screen._to_pal(-1) == -1
    with _screen_tier(0, default_bg=curses.COLOR_BLACK):
        assert Screen._to_pal(-1) == curses.COLOR_BLACK


# --- UserInputListItem word navigation (Ctrl-Left / Ctrl-Right) ---------------
# Golden-impossible: word-jump moves the field's cursor WITHOUT changing its
# text, and the cursor column is not reliably present in the captured grid.
# Pure arithmetic on (txt, cursor_pos); the two-phase skip (over spaces, then
# over the word) is the part that actually breaks.

def _input(txt, cursor):
    return SimpleNamespace(txt=txt, cursor_pos=cursor)

def test_prev_word_pos_from_mid_word_goes_to_word_start():
    # "foo bar baz", cursor at 6 (inside "bar") -> 4 (start of "bar")
    assert UserInputListItem.prev_word_pos(_input("foo bar baz", 6)) == 4

def test_prev_word_pos_from_word_start_skips_over_space_to_previous_word():
    # cursor at 4 (start of "bar", preceded by a space) -> 0 (start of "foo")
    assert UserInputListItem.prev_word_pos(_input("foo bar baz", 4)) == 0

def test_next_word_pos_from_word_start_goes_to_word_end():
    # cursor at 0 ("foo") -> 3 (the space after "foo")
    assert UserInputListItem.next_word_pos(_input("foo bar baz", 0)) == 3

def test_next_word_pos_skips_leading_spaces_then_word():
    # cursor at 3 (the space) -> 7 (end of "bar")
    assert UserInputListItem.next_word_pos(_input("foo bar baz", 3)) == 7


# --- SegmentedListItem.get_segment_on_offset (mouse-click hit-test) -----------
# Golden-impossible: this maps an absolute column to WHICH segment object lives
# there (and the one-column gap between segments maps to no segment). The screen
# shows the rendered row but cannot reveal that column->segment mapping, which
# the click routing and draw_line must agree on under horizontal scroll.

def test_get_segment_on_offset_maps_columns_to_segments():
    a, b, c = TextSegment('ab'), TextSegment('cd'), TextSegment('ef')
    it = SegmentedListItem([a, b, c])          # get_text() == 'ab cd ef'
    assert it.get_segment_on_offset(0) is a
    assert it.get_segment_on_offset(1) is a
    assert it.get_segment_on_offset(3) is b     # after 'ab' + separator column
    assert it.get_segment_on_offset(4) is b
    assert it.get_segment_on_offset(6) is c

def test_get_segment_on_offset_separator_column_hits_no_segment():
    a, b = TextSegment('ab'), TextSegment('cd')
    it = SegmentedListItem([a, b])             # 'ab cd'; column 2 is the separator gap
    hit = it.get_segment_on_offset(2)
    assert hit is not a and hit is not b        # the gap maps to a fresh empty Segment
    assert hit.get_text() == ''
