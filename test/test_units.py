"""Fast, pure unit tests for the isolated pieces of the gitk package.

These complement the pty golden suite (run.py): they exercise pure logic
(config parsing, key helpers, ref formatting) directly, with no terminal, so a
regression in that logic is caught quickly and pinpointed. The golden suite
remains the behavioural oracle.
"""

import datetime
import json
import os
import sys

# Make the repo root importable regardless of how pytest is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

from gitk.config import (KEY_CTRL, DEFAULT_CONFIG, load_config, save_config,
                         get_config_path)
from gitk.segments import ref_color_and_title, TextSegment
from gitk.views.git_log import GitLogView
from gitk.list_view import ListView
from gitk.jobs import GitLogJob


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
