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
import sys

# Make the repo root importable regardless of how pytest is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

from gitk.config import (KEY_CTRL, DEFAULT_CONFIG, load_config, save_config,
                         get_config_path)
from gitk.segments import ref_color_and_title, TextSegment, ButtonSegment, FillerSegment
from gitk.segmented_items import SegmentedListItem, ButtonRowItem
from gitk.views.git_log import GitLogView
from gitk.list_view import ListView
from gitk.jobs import GitLogJob, Job, _CONTROL_CHARS
from gitk.items import UserInputListItem, StatListItem
from gitk.screen import Screen
from gitk.dialogs import SearchDialogPopup


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
