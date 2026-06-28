"""Configuration persistence, the clipboard helper, and the Ctrl-key helper.

A leaf module: depends only on the standard library. Functions that need to
report problems take the `app` struct and log through `app.log`, rather than
reaching a global.
"""

import json
import os
import sys


def KEY_CTRL(key):
    return ord(key) & 0x1F


DEFAULT_CONFIG = {
    'git_log': {'show_commit_id': True, 'show_commit_date': True, 'show_commit_author': True, 'flags': ''},
    'git_diff': {'ignore_whitespace': False},
    'log': {'autoscroll': False},
    'view': {'default_mode': 'fullscreen'},  # fullscreen | side | stacked
}


def get_config_path() -> str:
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        base = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'gitkcli', 'config.json')


def load_config() -> dict:
    cfg = {k: dict(v) for k, v in DEFAULT_CONFIG.items()}
    try:
        with open(get_config_path(), 'r') as f:
            data = json.load(f)
        for section, values in data.items():
            if section in cfg and isinstance(values, dict):
                cfg[section].update({k: v for k, v in values.items() if k in cfg[section]})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


def save_config(cfg: dict, app) -> bool:
    path = get_config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(cfg, f, indent=2)
        return True
    except OSError as e:
        app.log.error(f"Failed to save preferences: {e}")
        return False


def copy_to_clipboard(txt:str, app):
    try:
        import pyperclip
        pyperclip.copy(txt)
    except ImportError:
        app.log.warning("pyperclip module not found. Install with: pip install pyperclip")
    except Exception as e:
        app.log.error(f"Error copying to clipboard: {str(e)}")
