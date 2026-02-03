# UI Framework Demos

This directory contains interactive demonstrations of the terminal UI framework capabilities.

## 🎯 Available Demos

### 🌟 Demo 0: UI Showcase (START HERE!)
**File:** `demo_0_showcase.py`

**⭐ RECOMMENDED FIRST DEMO ⭐**

A comprehensive visual gallery of ALL UI components in one place:
- Complete color palette demonstration
- All text item types and styles
- Interactive buttons with click tracking
- Toggle switches with state management
- Text input fields with cursor
- Complex segmented layouts (status bars, menus)
- Separators and spacers
- Both keyboard and mouse interactions

**Run:** `python3 demo_0_showcase.py`

**Perfect for:**
- First-time exploration of the framework
- Quick reference for available components
- Understanding component capabilities
- Testing terminal compatibility

---

### Demo 1: Basic List View
**File:** `demo_1_basic_list.py`

Demonstrates the fundamental list view component with:
- Scrollable list with many items
- Colored text items
- Keyboard navigation (arrow keys, vim keys, page up/down)
- Separators and visual styling
- Selection highlighting

**Run:** `python3 demo_1_basic_list.py`

---

### Demo 2: Interactive Segments
**File:** `demo_2_interactive_segments.py`

Shows interactive components:
- **Buttons** - Clickable buttons with visual feedback
- **Toggles** - On/off switches with state
- **Segments** - Composable UI components
- **Filler segments** - Flexible spacing and alignment
- Mouse interaction and click tracking

**Run:** `python3 demo_2_interactive_segments.py`

---

### Demo 3: Multiple Windows
**File:** `demo_3_windows.py`

Demonstrates window management:
- Multiple overlapping windows
- Window dragging and resizing
- Window title bars with buttons
- Window stacking and focus
- Toggle between windowed and fullscreen modes
- Control panel for window management

**Controls:**
- Drag title bar to move windows
- Click window edges to resize
- Double-click title bar to toggle fullscreen
- Press 1-4 to show different windows

**Run:** `python3 demo_3_windows.py`

---

### Demo 4: Text Input
**File:** `demo_4_text_input.py`

Features text input capabilities:
- Text input field with cursor
- Keyboard editing (backspace, delete, arrow keys)
- Simple chatbot interface
- Enter to submit messages
- Interactive conversation

**Run:** `python3 demo_4_text_input.py`

---

### Demo 5: Dashboard
**File:** `demo_5_dashboard.py`

A complete dashboard application showing:
- Real-time data updates
- Progress bars (CPU, Memory, Disk)
- Network statistics
- Notification system
- Auto-update toggle
- Multiple panels (main + sidebar)
- Complex layouts with segments
- Interactive controls

**Features:**
- Auto-updates every 2 seconds (can be toggled)
- Simulated system metrics
- Event notifications with severity levels
- Interactive buttons for testing

**Run:** `python3 demo_5_dashboard.py`

---

## 🚀 Quick Start

1. Make sure you're in the project root directory
2. Run any demo:
   ```bash
   cd demos
   python3 demo_1_basic_list.py
   ```
3. Press 'q' to quit any demo

## 🎨 Features Demonstrated

### Core Components
- ✅ `View` - Base window/view management
- ✅ `ListView` - Scrollable list with selection
- ✅ `Screen` - Screen management and color schemes

### Items
- ✅ `TextListItem` - Simple colored text
- ✅ `UserInputListItem` - Text input with cursor
- ✅ `SeparatorItem` - Visual separators
- ✅ `SpacerListItem` - Empty spacing

### Segments
- ✅ `TextSegment` - Text with color
- ✅ `ButtonSegment` - Clickable buttons
- ✅ `ToggleSegment` - Toggle switches
- ✅ `FillerSegment` - Flexible spacing
- ✅ `SegmentedListItem` - Composite items
- ✅ `WindowTopBarItem` - Window title bars

### Interactions
- ✅ Mouse support (clicking, dragging, scrolling)
- ✅ Keyboard navigation (arrows, vim keys, page keys)
- ✅ Window management (move, resize, focus)
- ✅ Text editing with cursor
- ✅ Button press feedback
- ✅ Toggle state management

## 📋 Requirements

- Python 3.6+
- curses library (included in Python on Unix/Linux/macOS)
- Terminal with mouse support recommended

## 💡 Tips

- **Terminal size:** Most demos work best with at least 80x24 terminal size
- **Mouse support:** Enable mouse support in your terminal for best experience
- **Color support:** Use a terminal with 256-color support for best visuals
- **Font:** Use a monospace font for proper alignment

## 🏗️ Framework Architecture

The UI framework uses:
- **Dependency Injection:** `UIContext` provides services to components
- **View Stack:** Multiple views can be layered and managed
- **Event System:** Mouse and keyboard events propagate through views
- **Composition:** Complex items built from simple segments
- **Color System:** Rich color palette with selection/highlight states

## 🎓 Learning Path

1. Start with **Demo 1** to understand basic lists and navigation
2. Try **Demo 2** to see interactive segments and buttons
3. Explore **Demo 3** for window management concepts
4. Test **Demo 4** for text input functionality
5. Study **Demo 5** to see everything combined in a real application

Each demo's source code is heavily commented to explain the concepts!

## 🤝 Contributing

These demos are part of the gitkcli project. Feel free to:
- Add more demos showcasing other features
- Improve existing demos with better examples
- Report issues or suggestions

---

Enjoy exploring the UI framework! 🎉
