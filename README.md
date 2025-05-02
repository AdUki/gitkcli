# GitkCLI

A terminal-based Git repository viewer, inspired by gitk.

## Features

- Browse commit history with navigation similar to Vim
- Search commit messages
- View commit diffs
- Blame files to see who changed each line

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

Same as `git log`

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
- `/`: Find (search) in commit messages
- `r`: Refresh commit list
- `b`: Blame file at cursor (in diff view)
- `q`: Quit

## Requirements

- Python 3.6+
- A terminal with curses support

## License

MIT

