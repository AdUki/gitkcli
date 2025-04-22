# GitkCLI

A terminal-based Git repository viewer, inspired by gitk.

## Features

- Browse commit history with navigation similar to Vim
- View commit diffs
- Blame files to see who changed each line
- Search commit messages
- Copy commit IDs to clipboard
- Support for common git log filters (author, date range, message search)

## Installation

```bash
# From source
git clone https://github.com/yourusername/gitkcli.git
cd gitkcli
pip install -e .

# Or once published
pip install gitkcli
```

## Usage

```bash
# Basic usage (shows all branches)
gitkcli

# Show only current branch
gitkcli --no-all

# Filter by author
gitkcli --author="John Doe"

# Show commits after a certain date
gitkcli --since="2 weeks ago"

# Limit number of commits
gitkcli -n 100

# Show only files in a specific directory
gitkcli -- path/to/directory
```

## Key Bindings

### Navigation
- `j` or `DOWN`: Move down
- `k` or `UP`: Move up
- `g`: Go to top
- `G`: Go to bottom
- `d`: Page down
- `u`: Page up

### Actions
- `ENTER`: Show/hide diff for selected commit
- `c`: Copy commit ID to clipboard
- `f`: Find (search) in commit messages
- `r`: Refresh commit list
- `b`: Blame file at cursor (in diff view)

### Views
- `h`: Show/hide help
- `q`: Quit

## Requirements

- Python 3.6+
- pygit2
- A terminal with curses support

## License

MIT
