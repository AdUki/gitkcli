# 🚀 Quick Start Guide

Welcome to the Terminal UI Framework demos! This guide will get you started in seconds.

## Fastest Way to Start

### Option 1: Interactive Launcher (Recommended)
```bash
cd demos
python3 launcher.py
```

Use arrow keys to select a demo and press Enter to run it!

### Option 2: Run the Showcase
```bash
cd demos
python3 demo_0_showcase.py
```

The showcase contains **all** UI components in one interactive gallery.

### Option 3: Run Any Demo Directly
```bash
cd demos
python3 demo_1_basic_list.py
# or demo_2_interactive_segments.py
# or demo_3_windows.py
# ... etc
```

## What to Try First

1. **Brand New?** → Start with `demo_0_showcase.py`
   - See all components at once
   - Learn navigation and controls
   - Test your terminal compatibility

2. **Want Interactivity?** → Try `demo_2_interactive_segments.py`
   - Click buttons and toggles
   - See real-time feedback
   - Experience mouse support

3. **Advanced Features?** → Jump to `demo_5_dashboard.py`
   - Complete application example
   - Real-time updates
   - Multiple panels
   - Rich interactions

## Common Controls (All Demos)

### Keyboard
- `↑` / `↓` or `j` / `k` - Navigate up/down
- `PgUp` / `PgDn` - Page up/down
- `Home` / `End` - Jump to start/end
- `q` - Quit demo

### Mouse
- **Click** - Select items, press buttons
- **Scroll wheel** - Scroll lists
- **Drag** - Move windows (demo 3 & 5)

## Demo Overview

| Demo | Focus | Best For |
|------|-------|----------|
| **0 - Showcase** | All components | First-time exploration |
| **1 - Basic List** | Scrolling & navigation | Understanding fundamentals |
| **2 - Segments** | Buttons & toggles | Interactive elements |
| **3 - Windows** | Window management | Multi-view layouts |
| **4 - Text Input** | Text editing | Input handling |
| **5 - Dashboard** | Complete app | Real-world example |

## Troubleshooting

### Colors look wrong?
Your terminal might not support 256 colors. Try:
- iTerm2 (macOS)
- GNOME Terminal (Linux)
- Windows Terminal (Windows)

### Mouse not working?
Enable mouse support in your terminal settings.

### Demo crashes immediately?
- Check terminal size (minimum 80x24 recommended)
- Update Python to 3.6+
- Verify curses library is available

## Next Steps

After exploring the demos:
1. Read the full `README.md` for detailed documentation
2. Check the source code - demos are heavily commented
3. Explore the `ui/` directory to see framework internals
4. Build your own application using the framework!

## Quick Examples

### Create a Simple List
```python
from ui import UIContext, Screen, ListView, TextListItem

list_view = ListView('my-list', ui_context=context)
list_view.append(TextListItem("Item 1", color=5, ui_context=context))
list_view.show()
```

### Add a Button
```python
from ui import SegmentedListItem, TextSegment, ButtonSegment

def on_click():
    print("Clicked!")
    return True

button_item = SegmentedListItem([
    TextSegment("Label: ", color=1, ui_context=context),
    ButtonSegment("[ Click Me ]", on_click, color=5, ui_context=context)
], ui_context=context)
```

### Create a Window
```python
from ui import ListView, WindowTopBarItem

window = ListView('my-window', view_mode='window',
                 x=10, y=5, width=50, height=20,
                 ui_context=context)

header = WindowTopBarItem("My Window", ui_context=context,
                          on_close_click=lambda: window.hide())
window.set_header_item(header)
window.show()
```

---

**Happy exploring! 🎉**

Questions or issues? Check the main README or explore the source code.
