# GitkCLI

**gitk, in your terminal.**

GitkCLI brings the beloved [gitk](https://git-scm.com/docs/gitk) history-browsing experience to the terminal — no X11, no display server, no GUI. Just `git log --graph` rendered into a fast, mouse-aware, keyboard-driven curses interface that runs anywhere a shell does.

## Why GitkCLI?

gitk is fantastic — it's part of git itself, rock-solid, and so feature-complete it barely needs changing. There's just one thing it can't do: **run without a graphical display.**

The moment you're on a remote box over SSH, inside tmux, or on a headless server, gitk is out of reach. GitkCLI is for exactly those moments — the gitk experience, where gitk can't go.

| | gitk | GitkCLI |
|---|:---:|:---:|
| The gitk graph history experience | ✅ | ✅ |
| Runs in a terminal / over SSH / headless | ❌ needs X11 | ✅ |
| Mouse, drag, scroll, context menus | ✅ | ✅ |
| Commit ops (cherry-pick, revert, reset, branch/tag) | ✅ | ✅ |
| Blame / trace line origin | ✅ | ✅ |

### The lazy trick that makes it work

The hard part of any gitk clone is the commit graph layout. GitkCLI doesn't reinvent it — it **piggybacks on `git log --graph`** and renders git's own output. Almost zero lines of graph-drawing code, and it stays correct because git does the math.

## Features

- **Browse commit history** with a real graph view, Vim-like navigation, and smooth scrolling
- **View commit diffs** with customizable context size and whitespace handling
- **Advanced search** with regex support, case sensitivity, and multiple search types (commit ID, message, diff, file paths)
- **Git references** - browse and manage branches, tags, and remote branches
- **Blame integration** - trace line origins across commits with `ENTER` on diff lines
- **Commit operations** - cherry-pick, revert, reset (soft/hard), create branches/tags
- **Uncommitted changes** display with quick access to staged/unstaged modifications
- **Jump history** - navigate back and forth through your exploration with `<---` / `--->` buttons
- **Context-sensitive menus** - right-click for operations on commits, branches, tags, and more
- **Mouse support** - click to navigate, drag floating windows, scroll with wheel

## Installation

```bash
# From source
git clone https://github.com/AdUki/gitkcli.git
cd gitkcli
pip install .

# Or once published
pip install gitkcli
```

## Usage

```bash
gitkcli
# Same arguments as 'git log' are supported
gitkcli --grep fix
gitkcli origin/main..HEAD
```

## Key Bindings

### Navigation
- `j` / `DOWN`: Move down
- `k` / `UP`: Move up
- `h` / `LEFT`: Scroll left
- `l` / `RIGHT`: Scroll right
- `Shift+h` / `Shift+LEFT`: Scroll left by 10
- `Shift+l` / `Shift+RIGHT`: Scroll right by 10
- `g`: Go to top
- `G`: Go to bottom
- `CTRL-B` / `Page Up`: Page up
- `CTRL-F` / `Page Down`: Page down

### Commit Operations (git-log view)
- `ENTER`: Show commit diff
- `b`: Create new branch from commit
- `c`: Cherry-pick commit
- `v`: Revert commit
- `r` / `R`: Reset current branch to commit (pick Soft / Mixed / Hard in the dialog)
- `m`: Mark commit
- `M`: Jump to marked commit

### Search
- `/`: Open search dialog
- `n`: Search next
- `N`: Search previous

### Function-key bar
- `F1`: Show git log
- `F2`: Show git references
- `F3`: Show git diff
- `F4`: Show logs (debug)
- `F5`: Refresh head
- `Shift+F5`: Reload all commits
- `F6`: Open search (same as `/`)
- `F7`: Open the context menu at the selected row
- `F9`: Open Preferences (see [Configuration](#configuration))
- `F10`: Quit

### Diff View
- `ENTER`: Show blame/origin of selected line
- `+` / `-`: Increase/decrease context lines
- `[Ignore whitespace]`: title-bar button to toggle whitespace-insensitive diffing

### Navigation History
- `CTRL+LEFT` / `<---`: Navigate back in jump history
- `CTRL+RIGHT` / `--->`: Navigate forward in jump history

### General
- `q`: Quit current view or application
- Right-click: Context menu for operations

## Search Features

Open search with `/`. The search dialog supports multiple modes:

- **[Txt]**: Simple text search in commit messages
- **[ID]**: Search by commit hash
- **[Message]**: Search in commit messages with regex support
- **[Filepaths]**: Search by file paths changed in commits
- **[Diff]**: Search within diff content

Use `TAB` to cycle between search types. Additional flags:

- `<Case>`: Case-sensitive matching
- `<Regexp>`: Enable regex patterns

## Context Menus

Right-click on items for context-sensitive operations:

- **Commits**: Cherry-pick, revert, reset, create branches/tags, diff operations, mark/return to marked
- **Branches**: Checkout, rename, push, remove
- **Tags**: Push, remove, show annotation
- **Remote branches**: Remove
- **Diff lines**: Show origin of line, copy to clipboard

## Requirements

- Python 3.7+
- A terminal with curses support
- `pyperclip` (optional, for clipboard operations — `pip install gitkcli[clipboard]`)

## Configuration

Press **F9** to open Preferences and toggle:

- Commit **ID**, **date**, and **author** columns in the log
- **Ignore whitespace** in diffs
- Log view **autoscroll**
- **Default view mode** (fullscreen, or a side / stacked split)
- Extra `git log` **flags**

Preferences are saved to a JSON config file and reloaded on the next launch:

- Linux: `$XDG_CONFIG_HOME/gitkcli/config.json` (default `~/.config/gitkcli/config.json`)
- macOS: `~/Library/Application Support/gitkcli/config.json`
- Windows: `%APPDATA%\gitkcli\config.json`

Other adjustments:

- Diff **context size**: the `[+]` / `[-]` buttons in the diff view (default 3)
- `rename_limit` (git rename-detection limit, default 1570) and the log
  verbosity level are currently set in code.

## Project structure

The application lives in the `gitk/` package; `gitkcli.py` at the repo root is a
thin launch shim (`from gitk.main import main`). Modules are layered bottom-up
(config/ids/input/screen → segments → items → views → app → main); the whole map
and the design (an injected `App` struct reached via `self.app` / `get_app()`,
no globals) is documented in `gitk/__init__.py`.

Tests are in `test/`:

- `python3 test/run.py` runs the pty golden-screen suite (renders the real app
  on a fixed-size terminal and diffs each frame against `test/cases/*/golden/`).
- `python3 -m pytest test/` runs that suite plus the fast pure-logic unit tests
  in `test/test_units.py`.

## License

MIT
