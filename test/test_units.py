"""Fast, pure unit tests for the isolated pieces of the gitk package.

These complement the pty golden suite (run.py): they exercise pure logic
(config parsing, key helpers, ref formatting) directly, with no terminal, so a
regression in that logic is caught quickly and pinpointed. The golden suite
remains the behavioural oracle.
"""

import json
import os
import sys

# Make the repo root importable regardless of how pytest is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitk.config import (KEY_CTRL, DEFAULT_CONFIG, load_config, save_config,
                         get_config_path)
from gitk.segments import ref_color_and_title


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
