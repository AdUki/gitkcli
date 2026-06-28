"""Fast, pure unit tests for the isolated pieces of the gitk package.

These complement the pty golden suite (run.py): they exercise pure logic
(config parsing, key helpers, ref formatting) directly, with no terminal, so a
regression in that logic is caught quickly and pinpointed. The golden suite
remains the behavioural oracle.
"""

import contextlib
import curses
import datetime
import json
import os
import queue
import re
import sys

# Make the repo root importable regardless of how pytest is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

from gitk.config import (KEY_CTRL, DEFAULT_CONFIG, load_config, save_config,
                         get_config_path, copy_to_clipboard)
from gitk.segments import ref_color_and_title, TextSegment, ButtonSegment, FillerSegment
from gitk.segmented_items import SegmentedListItem, ButtonRowItem
from gitk.views.git_log import GitLogView
from gitk.views.git_diff import GitDiffView
from gitk.app import App
from gitk.list_view import ListView
from gitk.jobs import GitLogJob, GitDiffJob, Job, _CONTROL_CHARS
from gitk.items import UserInputListItem, StatListItem, TextListItem, ContextMenuItem
from gitk.screen import Screen
from gitk.dialogs import SearchDialogPopup, RefPushDialogPopup, NewRefDialogPopup
from gitk.input import KeyboardState, KEY_CTRL_BACKSPACE, KEY_CTRL_DEL
from gitk.views.context_menu import ContextMenu


# --- KEY_CTRL ---------------------------------------------------------------

def test_key_ctrl_maps_to_control_codes():
    assert KEY_CTRL('a') == 1
    assert KEY_CTRL('A') == 1          # case-insensitive (masks to low 5 bits)
    assert KEY_CTRL('w') == 23
    assert KEY_CTRL('o') == ord('o') & 0x1F


# --- DEFAULT_CONFIG / get_config_path ---------------------------------------

def test_default_config_shape():
    assert set(DEFAULT_CONFIG) == {'git_log', 'git_diff', 'log', 'view'}
    assert DEFAULT_CONFIG['git_log']['show_commit_id'] is True
    assert DEFAULT_CONFIG['git_diff']['ignore_whitespace'] is False
    assert DEFAULT_CONFIG['view']['default_mode'] == 'fullscreen'


def test_get_config_path_ends_with_gitkcli_config():
    path = get_config_path()
    assert path.endswith(os.path.join('gitkcli', 'config.json'))


# --- load_config ------------------------------------------------------------

def test_load_config_returns_defaults_copy_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr('gitk.config.get_config_path',
                        lambda: str(tmp_path / 'nope.json'))
    cfg = load_config()
    assert cfg == {k: dict(v) for k, v in DEFAULT_CONFIG.items()}
    # mutating the result must not leak into the module default
    cfg['git_log']['show_commit_id'] = False
    assert DEFAULT_CONFIG['git_log']['show_commit_id'] is True


def test_load_config_merges_known_keys_only(monkeypatch, tmp_path):
    p = tmp_path / 'config.json'
    p.write_text(json.dumps({
        'git_log': {'show_commit_id': False, 'bogus': 1},
        'unknown_section': {'x': 1},
    }))
    monkeypatch.setattr('gitk.config.get_config_path', lambda: str(p))
    cfg = load_config()
    assert cfg['git_log']['show_commit_id'] is False     # known key overridden
    assert cfg['git_log']['show_commit_date'] is True     # default preserved
    assert 'bogus' not in cfg['git_log']                  # unknown key dropped
    assert 'unknown_section' not in cfg                   # unknown section dropped


def test_load_config_tolerates_corrupt_json(monkeypatch, tmp_path):
    p = tmp_path / 'config.json'
    p.write_text('{ not valid json')
    monkeypatch.setattr('gitk.config.get_config_path', lambda: str(p))
    assert load_config() == {k: dict(v) for k, v in DEFAULT_CONFIG.items()}

def test_load_config_tolerates_valid_json_that_is_not_an_object(monkeypatch, tmp_path):
    # null / list / bare string have no .items(); must fall back, not crash.
    p = tmp_path / 'config.json'
    monkeypatch.setattr('gitk.config.get_config_path', lambda: str(p))
    for content in ('null', '[1, 2, 3]', '"hello"', '42'):
        p.write_text(content)
        assert load_config() == {k: dict(v) for k, v in DEFAULT_CONFIG.items()}


def test_save_config_roundtrip(monkeypatch, tmp_path):
    # save into a not-yet-existing subdir to also cover makedirs.
    p = tmp_path / 'sub' / 'config.json'
    monkeypatch.setattr('gitk.config.get_config_path', lambda: str(p))
    cfg = load_config()
    cfg['view']['default_mode'] = 'side'
    cfg['git_diff']['ignore_whitespace'] = True
    assert save_config(cfg, app=None) is True   # success path never touches app
    reloaded = load_config()
    assert reloaded['view']['default_mode'] == 'side'
    assert reloaded['git_diff']['ignore_whitespace'] is True


# --- ref_color_and_title ----------------------------------------------------

def test_ref_color_and_title_by_type():
    assert ref_color_and_title({'name': 'main', 'type': 'heads'}) == (11, '[main]')
    assert ref_color_and_title({'name': 'v1.0', 'type': 'tags'}) == (12, '<v1.0>')
    assert ref_color_and_title({'name': 'origin/main', 'type': 'remotes'}) == (15, '{origin/main}')
    color, title = ref_color_and_title({'name': 'stash@{0}', 'type': 'stash'})
    assert color == 14


def test_ref_color_and_title_head_arrow_depends_on_branch():
    assert ref_color_and_title({'name': 'HEAD', 'type': 'head'}) == (13, '(HEAD)')
    assert ref_color_and_title({'name': 'HEAD', 'type': 'head'},
                               head_branch='main') == (13, '(HEAD) ->')


# --- GitLogView.add_to_jump_list (pure: only touches jump_list/jump_index) --
# Regression for the dedup early-return that used to leave jump_index stale.

def _jl(entries, index):
    # Drive the method against a stand-in: it reads/writes only these two attrs.
    return SimpleNamespace(jump_list=list(entries), jump_index=index)

def test_add_to_jump_list_dedup_resets_index_to_zero():
    # Navigated back to index 2 ('c'); re-adding the current entry must dedup
    # AND leave jump_index pointing at the (now sole) entry, not stay stale.
    ns = _jl([('a', None, None), ('b', None, None), ('c', None, None)], 2)
    GitLogView.add_to_jump_list(ns, 'c')
    assert ns.jump_list == [('c', None, None)]
    assert ns.jump_index == 0

def test_add_to_jump_list_truncates_forward_history_and_prepends():
    ns = _jl([('a', None, None), ('b', None, None)], 1)   # at 'b', 'a' is forward
    GitLogView.add_to_jump_list(ns, 'z', 5, 3)
    assert ns.jump_list == [('z', 5, 3), ('b', None, None)]
    assert ns.jump_index == 0


# --- ListView.set_selected non-selectable skip (int target; no app needed) --

class _Row:
    def __init__(self, selectable):
        self.is_selectable = selectable

def _listview(selectables, selected):
    # Stand-in carrying just the attributes set_selected's int path touches.
    return SimpleNamespace(items=[_Row(s) for s in selectables], _selected=selected,
                           _offset_y=0, height=10, dirty=False)

def test_set_selected_skips_nonselectable_in_travel_direction():
    # cursor at 4, target index 2 is non-selectable; selectable rows on BOTH
    # sides (1 below-travel, 3 opposite). Travelling UP (4->2) must land on 1
    # (continue past the target), NOT 3 (back toward the start).
    lv = _listview([True, True, False, True, True], selected=4)
    assert ListView.set_selected(lv, 2) is True
    assert lv._selected == 1

def test_set_selected_falls_back_to_opposite_when_travel_blocked():
    # cursor at 0, target 2 non-selectable; nothing selectable beyond it in the
    # travel direction (3,4 also non-selectable), so fall back toward the start.
    lv = _listview([True, True, False, False, False], selected=0)
    assert ListView.set_selected(lv, 2) is True
    assert lv._selected == 1

def test_set_selected_selectable_target_is_unchanged():
    lv = _listview([True, True, True, True], selected=0)
    assert ListView.set_selected(lv, 2) is True
    assert lv._selected == 2


# --- Segment._draw_text clipping (offset is scroll, width is a column COUNT) --

class _FakeWin:
    def __init__(self):
        self.drawn = []
    def addstr(self, txt, color):
        self.drawn.append(txt)

def test_draw_text_offset_zero_takes_width_columns():
    win = _FakeWin()
    n = TextSegment('abcdefgh')._draw_text(win, 0, 4, color=0)
    assert win.drawn == ['abcd'] and n == 4

def test_draw_text_with_offset_clips_offset_plus_width():
    # The historical bug used text[offset:width]; with offset=2,width=3 that
    # wrongly yielded 'c' (1 col). Correct is text[2:5] = 'cde' (3 cols).
    win = _FakeWin()
    n = TextSegment('abcdefgh')._draw_text(win, 2, 3, color=0)
    assert win.drawn == ['cde'] and n == 3

def test_draw_text_width_beyond_text_is_clamped():
    win = _FakeWin()
    n = TextSegment('abc')._draw_text(win, 1, 10, color=0)
    assert win.drawn == ['bc'] and n == 2


# --- GitLogJob.process_line (pure parser; does not use self) -----------------
# Lines come from `git log --format=#%H#%P#%aI#%an#%s`, i.e.
# <prefix>#<hash>#<parents>#<iso-date>#<author>#<subject>.

def test_process_line_parses_a_commit():
    line = "#abc123#p1 p2#2024-10-10T12:31:35+00:00#Alice#Fix the bug"
    id, commit = GitLogJob.process_line(None, line)
    assert id == 'abc123'
    assert commit['prefix'] == ''
    assert commit['parents'] == ['p1', 'p2']
    assert commit['author'] == 'Alice'
    assert commit['title'] == 'Fix the bug'
    assert isinstance(commit['date'], datetime.datetime)

def test_process_line_subject_may_contain_hashes():
    # split('#', 5) caps the split so '#' in the subject is preserved.
    _, commit = GitLogJob.process_line(None,
        "#id#p#2024-10-10T12:31:35+00:00#Bob#subject with # hash")
    assert commit['title'] == 'subject with # hash'

def test_process_line_keeps_graph_prefix():
    _, commit = GitLogJob.process_line(None,
        "* #id#p#2024-10-10T12:31:35+00:00#Carol#msg")
    assert commit['prefix'] == '* '

def test_process_line_nonmatching_returns_raw_string():
    assert GitLogJob.process_line(None, "just some text") == "just some text"


# --- _CONTROL_CHARS: control-char display hygiene applied to streamed text -----
# (curses renders control bytes as caret notation, e.g. "^[", not raw escapes;
# stripping them just avoids that clutter.)

def test_control_chars_strips_escape_sequences_leaving_inert_text():
    # the ESC byte (and bell) are removed; the bracket text is left inert
    assert _CONTROL_CHARS.sub('', 'pre\x1b[31mRED\x1b[0m\x07post') == 'pre[31mRED[0mpost'

def test_control_chars_strips_c0_and_c1_ranges():
    assert _CONTROL_CHARS.sub('', 'a\x00\x08\x0b\x1f\x7f\x9fb') == 'ab'

def test_control_chars_keeps_printable_and_wide_unicode():
    assert _CONTROL_CHARS.sub('', '日本語 🎉 box─│ abc') == '日本語 🎉 box─│ abc'

def test_control_chars_keeps_tab_and_newline():
    # tab is expanded and CR/LF handled upstream, so this regex must leave them
    assert _CONTROL_CHARS.sub('', 'a\tb\nc') == 'a\tb\nc'


# --- UserInputListItem word navigation (pure: uses txt + cursor_pos only) ----

def _input(txt, cursor):
    return SimpleNamespace(txt=txt, cursor_pos=cursor)

def test_prev_word_pos_from_mid_word_goes_to_word_start():
    # "foo bar baz", cursor at 6 (inside "bar") -> 4 (start of "bar")
    assert UserInputListItem.prev_word_pos(_input("foo bar baz", 6)) == 4

def test_prev_word_pos_from_word_start_skips_to_previous_word():
    # cursor at 4 (start of "bar", preceded by a space) -> 0 (start of "foo")
    assert UserInputListItem.prev_word_pos(_input("foo bar baz", 4)) == 0

def test_prev_word_pos_at_start_is_zero():
    assert UserInputListItem.prev_word_pos(_input("foo bar", 0)) == 0

def test_next_word_pos_from_word_start_goes_to_word_end():
    # cursor at 0 ("foo") -> 3 (the space after "foo")
    assert UserInputListItem.next_word_pos(_input("foo bar baz", 0)) == 3

def test_next_word_pos_skips_leading_spaces_then_word():
    # cursor at 3 (the space) -> 7 (end of "bar")
    assert UserInputListItem.next_word_pos(_input("foo bar baz", 3)) == 7

def test_next_word_pos_at_end_is_length():
    assert UserInputListItem.next_word_pos(_input("foo bar", 7)) == 7


# --- Job queue helpers: _empty_queue (static) and _drain (uses self.stop) ----

def test_empty_queue_drains_everything():
    q = queue.Queue()
    for i in range(5):
        q.put(i)
    Job._empty_queue(q)
    assert q.empty()

def test_drain_dispatches_truthy_items_until_falsy_sentinel():
    q = queue.Queue()
    for x in ['a', 'b', None, 'c']:
        q.put(x)
    got = []
    processed = Job._drain(SimpleNamespace(stop=False), q, got.append)
    assert processed is True
    assert got == ['a', 'b']            # the None sentinel breaks the loop
    assert q.get_nowait() == 'c'         # items after the sentinel are left queued

def test_drain_consumes_but_does_not_dispatch_while_stopped():
    q = queue.Queue()
    for x in ['a', 'b']:
        q.put(x)
    got = []
    processed = Job._drain(SimpleNamespace(stop=True), q, got.append)
    assert processed is False            # nothing dispatched while stopped
    assert got == []
    assert q.empty()                     # but the queue was still drained


# --- SegmentedListItem.get_segment_on_offset (click hit-test maps an absolute
# get_text() offset -> segment; separators occupy a column between segments).
# This is the coordinate space draw_line must agree with under horizontal scroll.

def test_get_segment_on_offset_maps_columns_to_segments():
    a, b, c = TextSegment('ab'), TextSegment('cd'), TextSegment('ef')
    it = SegmentedListItem([a, b, c])          # get_text() == 'ab cd ef'
    assert it.get_segment_on_offset(0) is a
    assert it.get_segment_on_offset(1) is a
    assert it.get_segment_on_offset(3) is b     # after 'ab' + separator
    assert it.get_segment_on_offset(4) is b
    assert it.get_segment_on_offset(6) is c
    assert it.get_segment_on_offset(7) is c

def test_get_segment_on_offset_separator_column_hits_no_segment():
    a, b = TextSegment('ab'), TextSegment('cd')
    it = SegmentedListItem([a, b])             # 'ab cd'; column 2 is the separator
    hit = it.get_segment_on_offset(2)
    assert hit is not a and hit is not b        # the gap maps to a fresh empty Segment
    assert hit.get_text() == ''


# --- ButtonRowItem focus navigation (buttons = segments with `activate`) ------
# Non-button segments (text/filler) are skipped; focus wraps with Left/Right.

def _button_row():
    # indices of the actual buttons are 1 and 3 (0=text, 2=filler).
    return ButtonRowItem([TextSegment('lbl'), ButtonSegment('[A]', lambda: None),
                          FillerSegment(), ButtonSegment('[B]', lambda: None)])

def test_button_row_indices_and_default_focus():
    r = _button_row()
    assert r._button_indices() == [1, 3]
    assert r.focused == 1            # __init__ focuses the first button

def test_button_row_focus_last_and_reset():
    r = _button_row()
    r.focus_last()
    assert r.focused == 3            # rightmost button (safe default for confirms)
    r.reset_focus()
    assert r.focused == 1

def test_button_row_move_focus_wraps_and_skips_nonbuttons():
    r = _button_row()                # focus=1
    r._move_focus(1)
    assert r.focused == 3            # skips the filler at index 2
    r._move_focus(1)
    assert r.focused == 1            # wraps forward
    r._move_focus(-1)
    assert r.focused == 3            # wraps backward

def test_button_row_no_buttons_focus_zero():
    r = ButtonRowItem([TextSegment('only text')])
    assert r._button_indices() == []
    assert r.focused == 0
    r._move_focus(1)                 # no-op, must not raise
    assert r.focused == 0


# --- Screen._to_pal colour-tier degradation (pure: arithmetic on the index) ---
# color_depth/_default_bg are class attributes; save/restore so we don't leak
# state into other tests.

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


# --- SearchDialogPopup.matches: regex search must not crash on bad patterns ----
# matches() runs on every redraw and keystroke, so a half-typed/invalid regex
# (e.g. "[") must yield "no match", never raise re.error into the draw loop.

def _search(txt, regexp=False, case=False):
    return SimpleNamespace(input=SimpleNamespace(txt=txt),
                           use_regexp=SimpleNamespace(toggled=regexp),
                           case_sensitive=SimpleNamespace(toggled=case))

def _item(text):
    return SimpleNamespace(get_text=lambda: text)

def test_matches_invalid_regex_returns_no_match_instead_of_raising():
    for bad in ('[', '(', '*', '(?P<', 'a{2,1}'):
        assert not SearchDialogPopup.matches(_search(bad, regexp=True), _item('anything'))

def test_matches_valid_regex_finds_hit_case_insensitive_by_default():
    assert SearchDialogPopup.matches(_search('fi.', regexp=True), _item('a FIX b'))
    assert not SearchDialogPopup.matches(_search('zzz', regexp=True), _item('a fix b'))

def test_matches_regex_respects_case_sensitivity_toggle():
    assert not SearchDialogPopup.matches(_search('FIX', regexp=True, case=True), _item('a fix b'))
    assert SearchDialogPopup.matches(_search('fix', regexp=True, case=True), _item('a fix b'))

def test_matches_plain_substring_paths_unaffected_by_regex_guard():
    assert SearchDialogPopup.matches(_search('[lit]', regexp=False), _item('has [lit] bracket'))
    assert SearchDialogPopup.matches(_search('FIX', regexp=False, case=False), _item('a fix b'))
    assert not SearchDialogPopup.matches(_search('FIX', regexp=False, case=True), _item('a fix b'))

def test_matches_empty_query_never_matches():
    assert not SearchDialogPopup.matches(_search('', regexp=True), _item('anything'))


# --- StatListItem.jump_to_file: file paths must be regex-escaped --------------
# The diff stat row builds an `re.compile(f'diff.*{path}')` to jump to the file's
# hunk. Git paths can contain regex metachars, so the path must be escaped: an
# unescaped "[" crashes re.compile, and "." would wildcard-match the wrong line.

def _jump_env(stat_file_path):
    captured = {}
    diff = SimpleNamespace(commit_id='c', _selected=0, _offset_y=0,
                           set_selected=lambda pat, mode: captured.__setitem__('pat', pat))
    app = SimpleNamespace(git_diff=diff,
                          git_log=SimpleNamespace(add_to_jump_list=lambda *a: None))
    item = SimpleNamespace(get_app=lambda: app, stat_file_path=stat_file_path)
    StatListItem.jump_to_file(item)          # must not raise
    return captured['pat']

def test_jump_to_file_does_not_crash_on_metachar_path():
    pat = _jump_env('test[1].txt')           # unescaped this raises re.error
    assert pat.match('diff --git a/test[1].txt b/test[1].txt')

def test_jump_to_file_treats_dot_as_literal_not_wildcard():
    pat = _jump_env('a.txt')
    assert pat.match('diff --git a/a.txt b/a.txt')   # the real path still matches
    assert not pat.match('diff --git a/aXtxt b/aXtxt')  # '.' is literal, no wildcard


# --- ListView.copy_text_range_to_clipboard: no leading blank line -------------
# The range copy must join lines (like copy_text_to_clipboard), not prepend
# "\n" to each, which left a spurious blank first line in the clipboard.

def _clip_row(text):
    return SimpleNamespace(get_text=lambda text=text: text)

def _copy_range(monkeypatch, texts, selected, to_index):
    import gitk.list_view as lv_mod
    captured = {}
    monkeypatch.setattr(lv_mod, 'copy_to_clipboard', lambda txt, app: captured.__setitem__('txt', txt))
    rows = [_clip_row(t) for t in texts]
    lv = SimpleNamespace(items=rows, _selected=selected, app=None)
    ListView.copy_text_range_to_clipboard(lv, rows[to_index])
    return captured['txt']

def test_copy_range_has_no_leading_newline_downward(monkeypatch):
    # selection at 1, range down to index 3 -> lines 1..3 joined, no leading "\n"
    txt = _copy_range(monkeypatch, ['a', 'b', 'c', 'd', 'e'], selected=1, to_index=3)
    assert txt == "b\nc\nd"

def test_copy_range_covers_upward_selection(monkeypatch):
    # to_item above the selection: range is to_item..selected inclusive
    txt = _copy_range(monkeypatch, ['a', 'b', 'c', 'd', 'e'], selected=3, to_index=1)
    assert txt == "b\nc\nd"

def test_copy_range_single_row(monkeypatch):
    txt = _copy_range(monkeypatch, ['a', 'b', 'c'], selected=2, to_index=2)
    assert txt == "c"


# --- ListView.append on-screen dirty check (off-by-one regression) ------------
# The appended row is at index len-1, i.e. screen row (len-1)-offset_y, so it is
# visible when len-offset_y <= height. A '<' there left the row that exactly
# fills the last visible line marked off-screen -> bottom row blank until a
# later full redraw. Boundary: with height H and offset 0, the H-th append must
# set dirty; the (H+1)-th must not (it scrolls off, header-only redraw).

def _appendable(height, offset=0, n_existing=0):
    return SimpleNamespace(items=[object() for _ in range(n_existing)],
                           _offset_y=offset, height=height,
                           dirty=False, header_dirty=False, autoscroll=False)

def test_append_marks_dirty_for_row_on_the_last_visible_line():
    lv = _appendable(height=5, n_existing=4)   # appending makes len == height
    ListView.append(lv, SimpleNamespace())
    assert lv.dirty is True                    # the H-th row is on-screen

def test_append_below_screen_is_header_only_not_body_dirty():
    lv = _appendable(height=5, n_existing=5)   # appending makes len == height+1
    ListView.append(lv, SimpleNamespace())
    assert lv.dirty is False                   # scrolled off the bottom
    assert lv.header_dirty is True             # but the counter still updates

def test_append_marks_dirty_for_a_clearly_visible_row():
    lv = _appendable(height=5, n_existing=1)   # len becomes 2, well on-screen
    ListView.append(lv, SimpleNamespace())
    assert lv.dirty is True


# --- ListView.append autoscroll follows the tail (dirty + offset) -------------
# With autoscroll on and the list overflowing, appending must scroll to keep the
# newest row visible AND mark the body dirty (the on-screen check ran against the
# pre-scroll offset, so without this the autoscrolled view went stale).

def test_append_autoscroll_overflow_scrolls_and_marks_dirty():
    lv = _appendable(height=5, offset=0, n_existing=5)  # full screen, at top
    lv.autoscroll = True
    ListView.append(lv, SimpleNamespace())              # len -> 6, overflows
    assert lv._offset_y == 1                            # scrolled to show the tail
    assert lv.dirty is True                             # body redraw requested

def test_append_autoscroll_within_screen_keeps_offset_zero():
    lv = _appendable(height=5, offset=0, n_existing=1)
    lv.autoscroll = True
    ListView.append(lv, SimpleNamespace())              # len -> 2, fits
    assert lv._offset_y == 0
    assert lv.dirty is True                             # on-screen row -> dirty anyway


# --- GitDiffJob.process_line stat-vs-message classification -------------------
# Before the first hunk, diffstat lines (1-space indent " f | 5 ++") become
# clickable StatListItems; commit-message body lines (4-space indent) must NOT,
# even when they contain a "| N +-" that looks like a stat (e.g. a md table).

def _diffjob():
    # process_line only touches these attributes / the two compiled patterns.
    return SimpleNamespace(
        old_file_line=-1, new_file_line=-1, line_count=0,
        line_pattern=re.compile(r'^(?:( )|(?:\+\+\+ b/(.*))|(?:--- a/(.*))|(\+\+\+|---|diff|index)|(\+)|(-)|(@@ -(\d+),\d+ \+(\d+),\d+ @@))'),
        stat_pattern=re.compile(r' (?:\.\.\.)?(?:.* => )?(.*?)}? +\| +\d+ \+*-*'),
        _stat_file_path=GitDiffJob._stat_file_path)

def test_process_line_diffstat_line_becomes_stat_item():
    item = GitDiffJob.process_line(_diffjob(), ' src/app.py | 5 +++--')
    assert isinstance(item, StatListItem)
    assert item.stat_file_path == 'src/app.py'

def test_process_line_message_line_with_bar_is_not_a_stat():
    # 4-space-indented commit message line that looks like a stat must stay text.
    item = GitDiffJob.process_line(_diffjob(), '    col_a | 5 vs col_b')
    assert isinstance(item, TextListItem)
    assert not isinstance(item, StatListItem)

def test_process_line_message_plain_line_is_text():
    item = GitDiffJob.process_line(_diffjob(), '    just a normal message line')
    assert isinstance(item, TextListItem)
    assert not isinstance(item, StatListItem)


# --- GitDiffJob._stat_file_path: reconstruct the new path for renames ---------
# Used by jump-to-file; must match the diff header's b/<path>. The stat regex's
# group(1) gets this wrong for directory renames (stray '}' / dropped prefix).

def test_stat_file_path_plain():
    assert GitDiffJob._stat_file_path(' src/app.py | 5 +++--') == 'src/app.py'

def test_stat_file_path_brace_rename_same_dir():
    assert GitDiffJob._stat_file_path(' src/{old.py => new.py} | 2 +-') == 'src/new.py'

def test_stat_file_path_brace_rename_dir_change():
    # The regex group(1) yielded the broken 'lib}/app.py' here.
    assert GitDiffJob._stat_file_path(' {src => lib}/app.py | 4 ++--') == 'lib/app.py'

def test_stat_file_path_bare_rename():
    assert GitDiffJob._stat_file_path(' old_name.py => new_name.py | 6 ++++++') == 'new_name.py'

def test_stat_file_path_brace_prefix_and_suffix():
    assert GitDiffJob._stat_file_path(' a/{b => c}/d.py | 3 +++') == 'a/c/d.py'


# --- RefPushDialogPopup.push_ref: don't push to an empty remote ---------------
# On a repo with no configured remote, self.remote is '' and pushing would run
# `git push "" <ref>` -> a cryptic fatal error dialog. Guard it.

def _push_env(remote):
    pushed = []
    warned = []
    return SimpleNamespace(
        remote=remote, ref_name='feature', force=SimpleNamespace(toggled=False),
        app=SimpleNamespace(log=SimpleNamespace(warning=warned.append)),
        _do_push=lambda r, n, f: pushed.append((r, n, f))), pushed, warned

def test_push_ref_skips_when_no_remote():
    me, pushed, warned = _push_env('')
    RefPushDialogPopup.push_ref(me)
    assert pushed == []          # no git push attempted
    assert len(warned) == 1      # warned instead

def test_push_ref_pushes_when_remote_selected():
    me, pushed, warned = _push_env('origin')
    RefPushDialogPopup.push_ref(me)
    assert pushed == [('origin', 'feature', False)]
    assert warned == []


# --- NewRefDialogPopup.create_ref: refuse an empty target commit --------------
# 'b' on an uncommitted pseudo-row passes '' (get_selected_commit_id), which
# would later run `git branch <name> ''` -> fatal. create_ref must refuse.

def test_create_ref_empty_commit_warns_and_does_not_open():
    events = []
    me = SimpleNamespace(
        app=SimpleNamespace(log=SimpleNamespace(warning=lambda m: events.append(('warn', m)))),
        show=lambda: events.append(('show',)))
    NewRefDialogPopup.create_ref(me, '')
    assert any(e[0] == 'warn' for e in events)
    assert not any(e[0] == 'show' for e in events)   # dialog never opened


# --- UserInputListItem text editing (insert / delete / cursor) ----------------
# Core interactive editing had no unit coverage (only the word-pos helpers did).
# Drives handle_input directly; the item needs no app/curses screen.

def _field(txt='', cursor=None):
    it = UserInputListItem()
    it.txt = txt
    it.cursor_pos = len(txt) if cursor is None else cursor
    return it

def test_input_insert_printable_at_cursor():
    it = _field('ac', cursor=1)
    it.handle_input(KeyboardState(ord('b')))
    assert it.txt == 'abc' and it.cursor_pos == 2

def test_input_backspace_deletes_before_cursor():
    it = _field('abc', cursor=2)
    it.handle_input(KeyboardState(curses.KEY_BACKSPACE))
    assert it.txt == 'ac' and it.cursor_pos == 1

def test_input_backspace_at_start_is_noop():
    it = _field('abc', cursor=0)
    it.handle_input(KeyboardState(curses.KEY_BACKSPACE))
    assert it.txt == 'abc' and it.cursor_pos == 0

def test_input_delete_removes_char_at_cursor_keeping_position():
    it = _field('abc', cursor=1)
    it.handle_input(KeyboardState(curses.KEY_DC))
    assert it.txt == 'ac' and it.cursor_pos == 1

def test_input_delete_at_end_is_noop():
    it = _field('abc', cursor=3)
    it.handle_input(KeyboardState(curses.KEY_DC))
    assert it.txt == 'abc' and it.cursor_pos == 3

def test_input_ctrl_backspace_deletes_previous_word():
    it = _field('foo bar', cursor=7)
    it.handle_input(KeyboardState(KEY_CTRL_BACKSPACE))
    assert it.txt == 'foo ' and it.cursor_pos == 4

def test_input_ctrl_del_clears_all():
    it = _field('foo bar', cursor=3)
    it.handle_input(KeyboardState(KEY_CTRL_DEL))
    assert it.txt == '' and it.cursor_pos == 0

def test_input_home_and_end_move_cursor():
    it = _field('abc', cursor=1)
    it.handle_input(KeyboardState(curses.KEY_HOME))
    assert it.cursor_pos == 0
    it.handle_input(KeyboardState(curses.KEY_END))
    assert it.cursor_pos == 3

def test_input_left_right_clamp_at_bounds():
    it = _field('ab', cursor=0)
    it.handle_input(KeyboardState(curses.KEY_LEFT))   # already at start
    assert it.cursor_pos == 0
    it.handle_input(KeyboardState(curses.KEY_RIGHT))
    it.handle_input(KeyboardState(curses.KEY_RIGHT))
    it.handle_input(KeyboardState(curses.KEY_RIGHT))  # past end
    assert it.cursor_pos == 2

def test_input_non_ascii_key_is_not_inserted():
    # Documents the current ASCII-only limitation (key > 126 falls through).
    it = _field('ab', cursor=2)
    handled = it.handle_input(KeyboardState(233))      # 'é' as a single code
    assert it.txt == 'ab'                              # not inserted


# --- ListView.search wrap-around + n/N wiring ---------------------------------
# 'next'/'previous' must wrap (repeat=True) so they cycle through matches like
# less/vim/gitk, rather than dead-ending at the last/first match.

def _searchable(item_texts, query, selected):
    items = [SimpleNamespace(get_text=(lambda t=t: t)) for t in item_texts]
    me = SimpleNamespace(_search_dialog=SimpleNamespace(matches=lambda it: query in it.get_text()),
                         items=items, _selected=selected, _offset_y=0, height=10)
    me.set_selected = lambda i, visible_mode='center': (setattr(me, '_selected', i), True)[1]
    return me

def test_search_forward_wraps_past_last_match():
    # 'x' matches indices 1 and 3; from the last match (3), wrap -> 1.
    me = _searchable(['a', 'x1', 'b', 'x2', 'c'], 'x', selected=3)
    ListView.search(me, backward=False, repeat=True)
    assert me._selected == 1

def test_search_forward_without_repeat_stops_at_end():
    me = _searchable(['a', 'x1', 'b', 'x2', 'c'], 'x', selected=3)
    ListView.search(me, backward=False, repeat=False)
    assert me._selected == 3      # no forward match, no wrap -> unchanged

def test_search_backward_wraps_before_first_match():
    me = _searchable(['a', 'x1', 'b', 'x2', 'c'], 'x', selected=1)
    ListView.search(me, backward=True, repeat=True)
    assert me._selected == 3      # wrapped to the last match

def _nav_listview(recorder):
    # Minimal stand-in to reach the n/N branches of handle_input.
    me = SimpleNamespace(items=[object()],
                         get_selected=lambda: SimpleNamespace(handle_input=lambda kb: False),
                         dirty=False)
    me.search = lambda **kw: recorder.append(kw)
    return me

def test_n_key_searches_with_wrap():
    calls = []
    ListView.handle_input(_nav_listview(calls), KeyboardState(ord('n')))
    assert calls == [{'repeat': True}]

def test_shift_n_key_searches_backward_with_wrap():
    calls = []
    ListView.handle_input(_nav_listview(calls), KeyboardState(ord('N')))
    assert calls == [{'backward': True, 'repeat': True}]


# --- NewRefDialogPopup rename routing (was a copy, not a rename) --------------
# "Rename this branch" used to call create_ref -> `git branch <new> <old>`, a
# COPY leaving the original. It must route to a real `git branch -m old new`.

class _RenameProbe(NewRefDialogPopup):
    # Bypass the real __init__ (needs app/curses); set only what execute touches.
    # Inherits the real NewRefDialogPopup.execute, whose super().execute() resolves
    # to UserInputDialogPopup.execute (add_query_to_history) - harmless here.
    def __init__(self, rename_from, commit_id='', txt='newname'):
        self.input = SimpleNamespace(txt=txt)
        self.force = SimpleNamespace(toggled=False)
        self.rename_from = rename_from
        self.commit_id = commit_id
        self.ref_type = 'branch'
        self.history_queries = []
        self.created = []
        self.renamed = []
    def _create_ref(self, *a):
        self.created.append(a)
    def _rename_branch(self, *a):
        self.renamed.append(a)

def test_execute_routes_to_rename_when_rename_from_set():
    p = _RenameProbe(rename_from='oldname')
    p.execute()
    assert p.renamed == [('oldname', 'newname', False)]
    assert p.created == []

def test_execute_routes_to_create_when_not_renaming():
    p = _RenameProbe(rename_from='', commit_id='deadbeef')
    p.execute()
    assert p.created == [('branch', 'newname', 'deadbeef', False)]
    assert p.renamed == []

def test_rename_branch_builds_git_branch_move():
    args_seen = []
    me = SimpleNamespace(app=SimpleNamespace(run_git=lambda args, **kw: args_seen.append(args)))
    NewRefDialogPopup._rename_branch(me, 'old', 'new', False)
    assert args_seen[0][:4] == ['git', 'branch', '-m', 'old']
    args_seen.clear()
    NewRefDialogPopup._rename_branch(me, 'old', 'new', True)   # forced overwrite
    assert args_seen[0][:3] == ['git', 'branch', '-M']


# --- ContextMenu "Copy <ref> name" must pass app to copy_to_clipboard ---------
# copy_to_clipboard(txt, app) needs two args; the refs-view copy items used to
# pass only the name -> TypeError -> crash on click. Build the item the way the
# menu does ([name, app]) and activate it.

def test_context_menu_copy_item_activates_without_crash(monkeypatch):
    import pyperclip
    copied = []
    monkeypatch.setattr(pyperclip, 'copy', lambda s: copied.append(s))
    fake_app = SimpleNamespace(
        screen=SimpleNamespace(hide_active_view=lambda: None),
        log=SimpleNamespace(warning=lambda m: None, error=lambda m: None))
    item = ContextMenuItem("Copy branch name", copy_to_clipboard, ['main', fake_app])
    item.get_app = lambda: fake_app
    assert item.activate() is True          # the missing-app TypeError would raise here
    assert copied == ['main']

def test_copy_to_clipboard_requires_app_arg():
    # Documents the signature the menu wiring must satisfy.
    import inspect
    params = list(inspect.signature(copy_to_clipboard).parameters)
    assert params == ['txt', 'app']


# --- SearchDialogPopup.do_search wraps (button parity with n/N keys) ----------
# The [Search Next]/[Search Previous] buttons route through do_search; it must
# pass repeat=True so they wrap like the keys / initial Enter.

class _SearchProbe(SearchDialogPopup):
    def __init__(self, calls):
        self.parent_list_view = SimpleNamespace(
            search=lambda *a, **kw: calls.append((a, kw)), dirty=False)
        self.dirty = False
        self.input = SimpleNamespace(txt='q')
        self.history_queries = []

def test_do_search_forward_wraps():
    calls = []
    _SearchProbe(calls).do_search(backward=False)
    assert calls == [((False,), {'repeat': True})]

def test_do_search_backward_wraps():
    calls = []
    _SearchProbe(calls).do_search(backward=True)
    assert calls == [((True,), {'repeat': True})]


# --- ContextMenu.remove_branch: safe delete + force-confirm (was bare -D) -----
# Other force ops confirm before forcing; remove_branch used to run `git branch
# -D` with no confirm, silently force-deleting unmerged commits. It must use -d
# by default and only -D after the "not fully merged" force-confirm.

def _remove_env():
    seen = []
    me = SimpleNamespace(app=SimpleNamespace(run_git=lambda args, **kw: seen.append((args, kw))))
    return me, seen

def test_remove_branch_uses_safe_delete_and_offers_force():
    me, seen = _remove_env()
    ContextMenu.remove_branch(me, 'feature')
    args, kw = seen[0]
    assert args == ['git', 'branch', '-d', 'feature']   # safe delete
    assert kw['reasons'] == ('not fully merged',)
    assert kw['force'] is False
    assert callable(kw['retry'])

def test_remove_branch_force_uses_capital_D():
    me, seen = _remove_env()
    ContextMenu.remove_branch(me, 'feature', force=True)
    args, kw = seen[0]
    assert args == ['git', 'branch', '-D', 'feature']   # forced after confirm
    assert kw['force'] is True


# --- git-command builders: assert the exact argv each op runs ----------------
# These were verified by hand during bug-hunting but lacked regression tests.
# Job.run_job / app.run_git are stubbed so nothing touches a real repo.

def _result(returncode=0, stdout='', stderr=''):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

def test_cherry_pick_aborts_then_picks(monkeypatch):
    jobs, gits = [], []
    monkeypatch.setattr(Job, 'run_job', lambda app, args: jobs.append(args) or _result())
    me = SimpleNamespace(app=SimpleNamespace(run_git=lambda args, **kw: gits.append(args)))
    GitLogView.cherry_pick(me, 'abc1234')
    assert jobs == [['git', 'cherry-pick', '--abort']]
    assert gits == [['git', 'cherry-pick', '-m', '1', 'abc1234']]

def test_cherry_pick_without_commit_warns_and_runs_nothing():
    warned, gits = [], []
    me = SimpleNamespace(get_selected_commit_id=lambda: '',
        app=SimpleNamespace(run_git=lambda *a, **k: gits.append(a),
                            log=SimpleNamespace(warning=warned.append)))
    GitLogView.cherry_pick(me)
    assert warned and gits == []

def test_revert_builds_no_edit_command():
    gits = []
    me = SimpleNamespace(app=SimpleNamespace(run_git=lambda args, **kw: gits.append(args)))
    GitLogView.revert(me, 'def5678')
    assert gits == [['git', 'revert', '--no-edit', '-m', '1', 'def5678']]

def test_reset_builds_mode_and_commit():
    gits = []
    me = SimpleNamespace(app=SimpleNamespace(run_git=lambda args, **kw: gits.append(args)))
    GitLogView.reset(me, '--hard', 'cafe123')
    assert gits == [['git', 'reset', '--hard', 'cafe123']]

def test_confirm_reset_refuses_local_pseudo_row():
    warned, opened = [], []
    me = SimpleNamespace(get_selected_commit_id=lambda: 'local-working',
        view_reset=SimpleNamespace(open=lambda cid: opened.append(cid)),
        app=SimpleNamespace(log=SimpleNamespace(warning=warned.append)))
    GitLogView.confirm_reset(me)
    assert warned and opened == []

def test_checkout_branch_safe_then_force():
    gits = []
    me = SimpleNamespace(app=SimpleNamespace(run_git=lambda args, **kw: gits.append((args, kw.get('force')))))
    ContextMenu.checkout_branch(me, 'feature')
    assert gits[-1] == (['git', 'checkout', 'feature'], False)
    ContextMenu.checkout_branch(me, 'feature', force=True)
    assert gits[-1] == (['git', 'checkout', '-f', 'feature'], True)

def test_remove_remote_ref_splits_remote_and_branch():
    gits = []
    me = SimpleNamespace(app=SimpleNamespace(run_git=lambda args, **kw: gits.append(args)))
    ContextMenu.remove_remote_ref(me, 'origin/feature/login')
    assert gits == [['git', 'push', '--delete', 'origin', 'feature/login']]

def test_remove_tag_deletes_local_and_every_remote(monkeypatch):
    seen = []
    def fake(app, args):
        seen.append(args)
        if args == ['git', 'remote']:
            return _result(stdout='origin\nupstream\n')
        return _result()
    monkeypatch.setattr(Job, 'run_job', fake)
    me = SimpleNamespace(app=SimpleNamespace(
        git_refs=SimpleNamespace(reload_refs=lambda: None),
        log=SimpleNamespace(success=lambda m: None, error=lambda m: None)))
    ContextMenu.remove_tag(me, 'v1.0')
    assert ['git', 'tag', '-d', 'v1.0'] in seen
    assert ['git', 'push', '--delete', 'origin', 'v1.0'] in seen
    assert ['git', 'push', '--delete', 'upstream', 'v1.0'] in seen

def test_remove_tag_local_only_when_no_remotes(monkeypatch):
    seen = []
    monkeypatch.setattr(Job, 'run_job',
        lambda app, args: seen.append(args) or _result(stdout=''))
    me = SimpleNamespace(app=SimpleNamespace(
        git_refs=SimpleNamespace(reload_refs=lambda: None),
        log=SimpleNamespace(success=lambda m: None, error=lambda m: None)))
    ContextMenu.remove_tag(me, 'v1.0')
    assert seen == [['git', 'remote'], ['git', 'tag', '-d', 'v1.0']]   # no push


# --- GitDiffView.change_context clamps at 0 and reloads -----------------------

def test_change_context_increments_and_reloads():
    reloads = []
    me = SimpleNamespace(context_size=3, _reload_diff=lambda: reloads.append(1))
    GitDiffView.change_context(me, +1)
    assert me.context_size == 4 and reloads == [1]

def test_change_context_clamps_at_zero():
    me = SimpleNamespace(context_size=0, _reload_diff=lambda: None)
    GitDiffView.change_context(me, -1)
    assert me.context_size == 0          # never negative
    me.context_size = 1
    GitDiffView.change_context(me, -1)
    assert me.context_size == 0


# --- blame "show origin" parse regex (jump_to_origin, items.py) ---------------
# `git blame -lsfn -L N,N <rev> -- <file>` yields e.g.
# "<sha> <orig-file> <orig-line> <final-line>) <code>"; the inline regex must
# pull (sha, file, orig-line). This pins that fragile extraction.

_BLAME_RE = re.compile(r'^(\S+) ([^)]+) ([0-9]+) ')

def test_blame_regex_extracts_sha_file_line():
    m = _BLAME_RE.search('a42cadebfe42d85cbf36f4887be166b34077b3e2 test.txt 1 1) aaa')
    assert m and m.group(1) == 'a42cadebfe42d85cbf36f4887be166b34077b3e2'
    assert m.group(3) == '1'

def test_blame_regex_handles_boundary_sha_caret():
    # a boundary (initial) commit is prefixed with '^'
    m = _BLAME_RE.search('^1af87e6c2614c1aea4a81476df0deb8206d5489 file.py 451 451) code')
    assert m and m.group(1).startswith('^')


# --- App.run_git: force-confirm retry arming ---------------------------------
# On a forceable rejection (retry set, not already forcing, a `reasons` substring
# in stderr) it arms the confirm dialog with the retry; otherwise it just logs.

def test_run_git_arms_force_confirm_on_rejection(monkeypatch):
    monkeypatch.setattr(Job, 'run_job', lambda app, args: _result(returncode=1, stderr='fatal: already exists'))
    seen = []
    app = App()
    app.confirm_dialog = SimpleNamespace(
        confirm=lambda title, lines, on_confirm, confirm_label: seen.append((title, on_confirm, confirm_label)))
    app.log = SimpleNamespace(error=lambda m: seen.append(('ERROR', m)))
    retry = lambda: 'retried'
    app.run_git(['git', 'branch', 'x', 'y'], err='E',
                reasons=('already exists',), retry=retry, title='T', label='[Overwrite]')
    assert len(seen) == 1
    assert seen[0][0] == 'T' and seen[0][1] is retry and seen[0][2] == '[Overwrite]'

def test_run_git_logs_error_when_already_forcing(monkeypatch):
    monkeypatch.setattr(Job, 'run_job', lambda app, args: _result(returncode=1, stderr='already exists'))
    seen = []
    app = App()
    app.confirm_dialog = SimpleNamespace(confirm=lambda *a, **k: seen.append('CONFIRMED'))
    app.log = SimpleNamespace(error=lambda m: seen.append(('error', m)))
    app.run_git(['git', 'branch', '-f', 'x', 'y'], err='E', force=True,
                reasons=('already exists',), retry=lambda: None, title='T', label='[X]')
    assert seen == [('error', 'E: already exists')]   # logged, not re-confirmed

def test_run_git_success_runs_requested_refreshes(monkeypatch):
    monkeypatch.setattr(Job, 'run_job', lambda app, args: _result(returncode=0))
    calls = []
    app = App()
    app.git_log = SimpleNamespace(refresh_head=lambda: calls.append('refresh_head'),
                                  check_uncommitted_changes=lambda: calls.append('check'))
    app.git_refs = SimpleNamespace(reload_refs=lambda: calls.append('reload_refs'))
    app.log = SimpleNamespace(success=lambda m: calls.append(('ok', m)))
    app.run_git(['git', 'whatever'], ok='done', refresh_head=True, reload_refs=True, check_uncommitted=True)
    assert calls == ['refresh_head', 'reload_refs', 'check', ('ok', 'done')]


# --- SplitLayout (extracted from App): split_active + mode cycling -----------

def test_split_active_requires_mode_and_window_pane():
    from gitk.split_layout import SplitLayout
    sl = SplitLayout(SimpleNamespace(git_log=SimpleNamespace(view_mode='window')))
    sl.split_mode = 'off'
    assert sl.split_active() is False                 # intent off
    sl.split_mode = 'side'
    assert sl.split_active() is True                  # tiled (window) panes
    sl.app.git_log.view_mode = 'fullscreen'
    assert sl.split_active() is False                 # too small -> fell back to fullscreen

def test_cycle_split_view_advances_off_side_stacked_off():
    from gitk.split_layout import SplitLayout
    sl = SplitLayout(SimpleNamespace())
    sl.set_split_mode = lambda m: setattr(sl, 'split_mode', m)   # skip the layout side effects
    sl.split_mode = 'off'
    sl.cycle_split_view(); assert sl.split_mode == 'side'
    sl.cycle_split_view(); assert sl.split_mode == 'stacked'
    sl.cycle_split_view(); assert sl.split_mode == 'off'
