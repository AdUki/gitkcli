# GitkCLI

**gitk, in your terminal.**

GitkCLI brings the beloved [gitk](https://git-scm.com/docs/gitk) history-browsing experience to the terminal — no X11, no display server, no GUI. Just your history and its graph in a fast, mouse-aware, keyboard-driven curses interface that runs anywhere a shell does.

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

### The graph: gitk's own algorithm

The hard part of any gitk clone is the commit graph layout. With `--graph`, GitkCLI lays it out the way gitk does (`gitk/graph.py` is a port of gitk's "arc" model, checked row for row against gitk's own Tcl code):

- **No `--topo-order` wait.** git streams plain `git log` output, and each commit is filed into its proper place as it arrives - the first screen shows up at once even for `--all` on the Linux kernel (1.8M commits, where `git log --graph` first sits silent for ~25 s).
- **Few lanes.** A line whose parent is far below is cut short (`┆`) and resumes just above the parent, so `--all` on a huge repository stays a handful of lanes wide. Double-click a `┆` to jump to the line's other end (`<-` jumps back).
- **Starts at HEAD.** The view opens with HEAD (and any uncommitted changes) at the top while the rest streams in around it.
- **Incremental.** New commits (F5 after a commit, cherry-pick, ...) are inserted into the loaded graph instead of reloading it, and the cursor stays on its commit. The "uncommitted changes" rows are nodes in the graph, right above HEAD.
- **Lazy.** Only the rows on screen are laid out; jumping to the end of a million-commit log is instant.

Commits are drawn as `*` beside box-drawing lanes - `*┤` a commit, `*┬─┐` a merge, `*┼─┘` a branch joining back. Set `"graph_dot"` in the `git_log` section of `~/.config/gitkcli/config.json` to use another glyph (e.g. `"●"`).

## Features

- **Browse commit history** with a real graph view (`--graph`), Vim-like navigation, and smooth scrolling
- **View commit diffs** with customizable context size and whitespace handling
- **Advanced search** with regex support, case sensitivity, and multiple search types (commit ID, message, diff, file paths)
- **Git references** - browse and manage branches, tags, and remote branches
- **Blame integration** - trace line origins across commits with `ENTER` on diff lines
- **Commit operations** - cherry-pick, revert, reset (soft/hard), create branches/tags
- **Uncommitted changes** display with quick access to staged/unstaged modifications
- **Jump history** - step back and forth through your exploration with `<---` / `--->`, across both panes
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

Every action key below is a default: rebind or unbind it in the
[shortcut editor](#keyboard-shortcuts). The context menus show each action's
current key on the right, and pressing it while a menu is open runs that entry
on the row the menu was opened for.

### Commit Operations (git-log view)
- `ENTER`: Show commit diff
- `b`: Create new branch from commit
- `t`: Create new tag on commit
- `c`: Cherry-pick commit
- `v`: Revert commit
- `r`: Reset current branch to commit (pick Soft / Mixed / Hard in the dialog)
- `d` / `D`: Diff commit --> marked commit / marked commit --> commit
- `m`: Mark commit
- `M`: Jump to marked commit

### References view
- `ENTER`: Jump to the ref's commit in the log
- `o`: Check out branch
- `R`: Rename branch
- `p`: Push branch / tag to a remote
- `a`: Show tag annotation
- `y`: Copy ref name
- `Del`: Remove branch / tag / remote branch (tags and remote branches ask first)

### Clipboard (git log, git diff and Logs views)
- `y`: Copy the selected line
- `Y`: Copy the whole list

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
- `ENTER`: Show blame/origin of selected line (on a stat row: jump to the file)
- `+` / `-`: Increase/decrease context lines
- `w` / `[Ignore whitespace]`: toggle whitespace-insensitive diffing

### Navigation History
- `CTRL+LEFT` / `CTRL-O` / `<---`: Navigate back in jump history
- `CTRL+RIGHT` / `CTRL-I` / `--->`: Navigate forward in jump history

The jump list works like Vim's: only real jumps go on it - opening a commit's
diff, jump-to-file, blame-to-origin, `g`/`G`, a search match, `M`, or opening a
ref - while walking the cursor and switching panes do not. Each entry remembers
its pane, so stepping back out of a diff you opened from the log returns to the
log row you opened it from, and stepping forward returns to the diff at the line
you left.

### Split view
- `|`: Cycle split view (off / side by side / stacked)
- `CTRL-W`: Switch between the split panes

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
- **Keyboard shortcuts** (`[Edit]` opens the shortcut editor, below)

Preferences are saved to a JSON config file and reloaded on the next launch:

- Linux: `$XDG_CONFIG_HOME/gitkcli/config.json` (default `~/.config/gitkcli/config.json`)
- macOS: `~/Library/Application Support/gitkcli/config.json`
- Windows: `%APPDATA%\gitkcli\config.json`

### Keyboard shortcuts

Preferences > Keyboard shortcuts `[Edit]` lists every rebindable action by
where it works (everywhere, git log, git diff, git references, the copy keys).
`ENTER` on a row waits for the new key (`Esc` cancels); `Backspace` unbinds it.
A key another action already uses where this one works moves over to it (the
bottom bar says from where). Navigation and search keys (`j k h l g G / n N q`,
arrows, `Enter`, `Esc`, `F1`-`F10`, ...) cannot be bound. Changes apply at once;
`[Save]` stores them, `[Defaults]` restores the built-in keys. The config file
keeps only the changed ones, under `"keys"`:

```json
"keys": { "log.cherry_pick": "Ctrl+X", "log.revert": "" }
```

Keys are written as `c`, `D`, `Del`, `Ins`, `Space`, `F11`, `Shift+F5` or
`Ctrl+W`; `""` unbinds the action.

Other adjustments:

- Diff **context size**: `+` / `-` or the `[+]` / `[-]` buttons in the diff view (default 3)
- `rename_limit` (git rename-detection limit, default 1570) and the log
  verbosity level are currently set in code.

## Project structure

The UI is a separate, git-agnostic library, **`tuikit/`**: a z-ordered deck of
fullscreen and floating (draggable, resizable) views, split layouts, list views
with search, rows built from segments (buttons, toggles, choices), dialogs and
menus, a function-key bar, a 256/8/mono colour palette, and clean key/mouse
events with local coordinates, drag grabs and the event loop. Its module map and
a minimal example are in `tuikit/__init__.py`; `demos/` has runnable examples
(`python3 demos/launcher.py`).

The application lives in the `gitk/` package, built on tuikit; `gitkcli.py` at
the repo root is a thin launch shim (`from gitk.main import main`). The module
map and the design (an injected `App` struct - a `tuikit.App` - reached via
`self.app` / `get_app()`, no globals) are documented in `gitk/__init__.py`.

Tests are in `test/`:

- `python3 test/run.py` runs the pty golden-screen suite (renders the real app
  on a fixed-size terminal and diffs each frame against `test/cases/*/golden/`).
  The `demo_*` cases run the tuikit demos the same way.
- `python3 -m pytest test/` runs that suite plus the fast pure-logic unit tests
  in `test/test_units.py`.

## License

MIT
