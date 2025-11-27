# GitkCLI

A terminal-based Git repository viewer with advanced features for exploring commit history, diffs, and references.

## Features

- **Browse commit history** with Vim-like navigation and smooth scrolling
- **View commit diffs** with customizable context size and whitespace handling
- **Advanced search** with regex support, case sensitivity, and multiple search types (commit ID, message, diff, file paths)
- **Git references** - browse and manage branches, tags, and remote branches
- **Blame integration** - trace line origins across commits with `ENTER` on diff lines
- **Commit operations** - cherry-pick, revert, reset (soft/hard), create branches/tags
- **Uncommitted changes** display with quick access to staged/unstaged modifications
- **Jump history** - navigate back and forth through your exploration with `<---` / `--->` buttons
- **Context-sensitive menus** - right-click for operations on commits, branches, tags, and more
- **Real-time logging** - adjustable log levels for debugging
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
- `g`: Go to top
- `G`: Go to bottom
- `CTRL-B` / `Page Up`: Page up
- `CTRL-F` / `Page Down`: Page down

### Commit Operations (git-log view)
- `ENTER`: Show commit diff
- `b`: Create new branch from commit
- `c`: Cherry-pick commit
- `v`: Revert commit
- `r`: Soft reset to commit
- `R`: Hard reset to commit
- `m`: Mark commit
- `M`: Jump to marked commit

### Search
- `/`: Open search dialog
- `n`: Search next
- `N`: Search previous

### View Selection
- `F1`: Show git log
- `F2`: Show git references
- `F3`: Show git diff
- `F4`: Show logs (debug)
- `F5`: Refresh head
- `Shift+F5`: Reload all commits

### Diff View
- `ENTER`: Show blame/origin of selected line
- `+` / `-`: Increase/decrease context lines
- `[Ignore space change]`: Toggle whitespace ignoring

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

- Python 3.6+
- A terminal with curses support
- `pyperclip` (optional, for clipboard operations)

## Configuration

Adjust these settings in the code:

- `context_size`: Lines of context in diffs (default: 3)
- `rename_limit`: Git rename similarity limit (default: 1570)
- `log_level`: Debug logging verbosity 0-5 (default: 4)
- `ignore_whitespace`: Ignore whitespace in diffs (default: False)

## License

MIT
