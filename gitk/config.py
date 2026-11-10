"""Configuration persistence.

A leaf module: depends only on the standard library. save_config takes the
`app` struct to report a failure through `app.log`.
"""

import json
import os
import sys


DEFAULT_CONFIG = {
    "git_log": {
        "show_commit_id": True,
        "show_commit_date": True,
        "show_commit_author": True,
        "flags": "",
        # The glyph marking a commit in --graph mode (*┤). Any single-cell
        # glyph works, e.g. "●", "⬤", "◉", "•" if the font draws it well.
        "graph_dot": "*",
    },
    "git_diff": {"ignore_whitespace": False},
    "log": {"autoscroll": False},
    "view": {"default_mode": "fullscreen"},  # fullscreen | side | stacked
    # Shortcut overrides, {action id: key spec} - see gitk/keymap.py. Only the
    # actions rebound from their default are kept; "" unbinds one.
    "keys": {},
}


def get_config_path() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
    return os.path.join(base, "gitkcli", "config.json")


def load_config() -> dict:
    cfg = {k: dict(v) for k, v in DEFAULT_CONFIG.items()}
    try:
        with open(get_config_path(), "r") as f:
            data = json.load(f)
        # Valid JSON that is not an object (null, a list, a bare string/number)
        # has no .items(); ignore it and fall back to defaults rather than crash.
        if isinstance(data, dict):
            for section, values in data.items():
                if section == "keys" and isinstance(values, dict):
                    # Free-form: Keymap drops what it does not know.
                    cfg[section].update(
                        {k: v for k, v in values.items() if isinstance(v, str)}
                    )
                elif section in cfg and isinstance(values, dict):
                    cfg[section].update(
                        {k: v for k, v in values.items() if k in cfg[section]}
                    )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


def update_config(sections: dict, app) -> bool:
    """Save `sections` over the config file's current contents, so that each
    dialog persists only its own settings (Preferences its toggles, the
    shortcut editor the keys) and never what the other left unsaved."""
    cfg = load_config()
    cfg.update(sections)
    return save_config(cfg, app)


def save_config(cfg: dict, app) -> bool:
    path = get_config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(cfg, f, indent=2)
        return True
    except OSError as e:
        app.log.error(f"Failed to save preferences: {e}")
        return False
