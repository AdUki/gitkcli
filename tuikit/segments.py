"""Segments: the inline pieces a `SegmentedListItem` lays out on one row -
text, fillers, buttons, toggles, choices.

A segment reaches its row through `_item` (set when the row is built) and the
app through `get_app()`. `draw()` writes the segment at the cursor, clipped to
`width` columns after skipping `offset` columns of horizontal scroll, and
returns the number of cells written.
"""

from __future__ import annotations

import curses

from tuikit import colors
from tuikit import keys as K
from tuikit import mouse as M

# Keys that step a control's value back / forward when its row is selected.
PREV_KEYS = (curses.KEY_LEFT, ord("h"), curses.KEY_BTAB)
NEXT_KEYS = (curses.KEY_RIGHT, ord("l"), K.KEY_TAB)


class Segment:
    # Back-reference to the owning SegmentedListItem, set when the row is built.
    _item = None

    def get_app(self):
        """The app this segment belongs to, reached through its row."""
        return self._item.get_app() if self._item is not None else None

    def get_text(self) -> str:
        return ""

    def natural_width(self) -> int:
        """Columns the segment needs at its widest (see ListView.pack)."""
        return len(self.get_text())

    def get_context_menu(self):
        """The context menu this segment opens, as a (menu_item, view_id) pair,
        or None. Used by a right-click on the segment and by keyboard cycling
        through a row's menus (SegmentedListItem.get_context_menu_targets)."""
        return None

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return 0

    def _draw_text(self, win, offset, width, attr) -> int:
        """Draw the text from scroll `offset`, clipped to `width` columns (a
        COUNT of remaining space, not an end index), in curses `attr`."""
        visible_txt = self.get_text()[offset : offset + width]
        win.addstr(visible_txt, attr)
        return len(visible_txt)

    def handle_mouse(self, event) -> bool:
        """`event.x` is the column inside this segment."""
        return False

    def handle_key(self, event) -> bool:
        """Keys a row forwards to its control (see LabeledRow)."""
        return False


class FillerSegment(Segment):
    """Stretches to share the row's spare width with the other fillers: one
    leading filler right-aligns what follows, one on each side centres it."""


class TextSegment(Segment):
    def __init__(self, txt, color=colors.NORMAL):
        super().__init__()
        self.txt = txt
        self.color = color

    def get_text(self):
        return self.txt

    def set_text(self, txt: str):
        self.txt = txt

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return self._draw_text(
            win, offset, width, colors.attr(self.color, selected, marked, matched)
        )


class DynamicTextSegment(TextSegment):
    """TextSegment whose text is recomputed by a getter on every draw."""

    def __init__(self, getter, color=colors.NORMAL):
        super().__init__("", color)
        self.getter = getter

    def get_text(self):
        return str(self.getter())


class RunsSegment(Segment):
    """Text painted in runs of different colours, as one segment (so no
    separator splits it). `runs` is [(text, color), ...]. `on_double_click(x)`
    is called with the column inside the segment; True when handled."""

    def __init__(self, runs, on_double_click=None):
        super().__init__()
        self.runs = runs
        self.txt = "".join(t for t, _ in runs)
        self.on_double_click = on_double_click

    def get_text(self):
        return self.txt

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        drawn = 0
        pos = 0
        for text, color in self.runs:
            lo = max(offset - pos, 0)
            pos += len(text)
            if lo >= len(text) or drawn >= width:
                continue
            piece = text[lo : lo + width - drawn]
            win.addstr(piece, colors.attr(color, selected, marked, False))
            drawn += len(piece)
        return drawn

    def handle_mouse(self, event) -> bool:
        if event.type == M.DOUBLE_CLICK and self.on_double_click:
            return bool(self.on_double_click(event.x))
        return False


class ButtonSegment(TextSegment):
    """Clickable text. Pressing shows it pressed; it fires `callback()` on a
    release over it (dragging off un-presses it), or on `activate()` (Enter in
    a ButtonRowItem)."""

    def __init__(self, txt, callback, color=colors.NORMAL):
        super().__init__(txt, color)
        self.callback = callback
        self.is_pressed = False

    def handle_mouse(self, event) -> bool:
        if event.type in (M.LEFT_CLICK, M.DOUBLE_CLICK, M.LEFT_MOVE_IN):
            self.is_pressed = True
            return True
        if event.type == M.LEFT_MOVE_OUT:
            self.is_pressed = False
            return True
        if event.is_release:
            # Only a release over the button fires it; released elsewhere (or
            # the gesture cancelled) it just pops back up.
            was_pressed, self.is_pressed = self.is_pressed, False
            if event.type == M.LEFT_RELEASE and was_pressed:
                return self.callback()
            return was_pressed
        return super().handle_mouse(event)

    def activate(self) -> bool:
        return self.callback()

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        if self.is_pressed:
            # Pressed: highlight if not already selected, else go bold+dim.
            bold = dim = selected
            return self._draw_text(
                win,
                offset,
                width,
                colors.attr(self.color, True, marked, bold=bold, dim=dim),
            )
        return super().draw(win, offset, width, selected, matched, marked)


class HighlightButtonSegment(ButtonSegment):
    """Button with a fixed label, highlighted while `is_active()` is true (a
    header toggle like [Autoscroll])."""

    def __init__(self, label, is_active, on_click, color=colors.TITLE):
        super().__init__(label, on_click, color)
        self._is_active = is_active

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return super().draw(win, offset, width, selected, matched, self._is_active())


class ToggleSegment(TextSegment):
    """Text that toggles on click / activate(); highlighted while on. Calls
    `callback(self)` after each toggle. `enabled=False` dims it."""

    def __init__(
        self, txt, toggled=False, callback=lambda seg: None, color=colors.NORMAL
    ):
        super().__init__(txt, color)
        self.callback = callback
        self.toggled = toggled
        self.enabled = True

    def toggle(self):
        self.toggled = not self.toggled

    def activate(self) -> bool:
        self.toggle()
        self.callback(self)
        return True

    def handle_mouse(self, event) -> bool:
        if event.is_left_press:
            return self.activate()
        return super().handle_mouse(event)

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return self._draw_text(
            win,
            offset,
            width,
            colors.attr(self.color, selected, self.toggled, dim=not self.enabled),
        )


class OnOffToggleSegment(ToggleSegment):
    """A `[ON|off]` switch: the active side in CAPS and highlighted."""

    def __init__(self, toggled=False, color=colors.NORMAL):
        super().__init__("", toggled, color=color)
        self.set_toggled(toggled)

    def set_toggled(self, value):
        self.toggled = value
        self.txt = "[ON|off]" if self.toggled else "[on|OFF]"

    def toggle(self):
        self.set_toggled(not self.toggled)

    def handle_key(self, event) -> bool:
        # Left/Right pick the side drawn there: [ON|off].
        if event.key in PREV_KEYS or event.key in NEXT_KEYS:
            on = event.key in PREV_KEYS
            if self.toggled != on:
                self.activate()
            return True
        return False

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        # Chunks: (text, is_active_side).
        chunks = [
            ("[", None),
            ("ON" if self.toggled else "on", True),
            ("|", None),
            ("off" if self.toggled else "OFF", False),
            ("]", None),
        ]
        drawn = 0
        pos = 0
        for txt, side in chunks:
            seg_start = pos
            seg_end = pos + len(txt)
            pos = seg_end
            # Intersect this chunk with the visible window [offset, width)
            s = max(seg_start, offset)
            e = min(seg_end, width)
            if s >= e:
                continue
            sub = txt[s - seg_start : e - seg_start]
            highlighted = (side is True) if self.toggled else (side is False)
            win.addstr(
                sub,
                colors.attr(
                    self.color, selected, highlighted, marked, dim=not self.enabled
                ),
            )
            drawn += len(sub)
        return drawn


class ChoiceSegment(ButtonSegment):
    """`<Label>` button cycling through a fixed list of (value, label) options."""

    def __init__(self, options, value, color=colors.NORMAL):
        self.options = options
        self.value = value
        super().__init__("", self._cycle, color)

    def _cycle(self, step=1):
        values = [v for v, _ in self.options]
        i = values.index(self.value) if self.value in values else 0
        self.value = values[(i + step) % len(values)]
        return True

    def handle_key(self, event) -> bool:
        if event.key in PREV_KEYS:
            return self._cycle(-1)
        if event.key in NEXT_KEYS:
            return self._cycle(1)
        return False

    def set_value(self, value):
        self.value = value

    def get_text(self):
        return "<" + dict(self.options).get(self.value, self.value) + ">"

    def natural_width(self) -> int:
        # Room for the longest option, so cycling never outgrows the row.
        return max((len(label) + 2 for _, label in self.options), default=2)


class RadioSegment(Segment):
    """A radio group drawn inline, ``<S> <M> <L>``, the chosen option
    highlighted. A click picks the option under it, Left/Right (h/l,
    Shift+Tab/Tab) step through the options and Enter moves to the next.
    `options` is [(value, label), ...]; `callback(value)` runs after each
    change."""

    def __init__(self, options, value, callback=None, color=colors.NORMAL):
        super().__init__()
        self.options = list(options)
        self.value = value
        self.callback = callback
        self.color = color
        self.enabled = True

    def get_text(self):
        return " ".join(label for _, label in self.options)

    def _index(self):
        values = [v for v, _ in self.options]
        return values.index(self.value) if self.value in values else 0

    def set_value(self, value):
        """Choose `value` without running the callback."""
        self.value = value

    def select(self, value) -> bool:
        if value != self.value:
            self.value = value
            if self.callback:
                self.callback(value)
        return True

    def step(self, direction) -> bool:
        if not self.options:
            return False
        i = (self._index() + direction) % len(self.options)
        return self.select(self.options[i][0])

    def activate(self) -> bool:
        return self.step(1)

    def option_at(self, x):
        """The value of the option drawn at column `x`, or None (a gap)."""
        pos = 0
        for value, label in self.options:
            if pos <= x < pos + len(label):
                return value
            pos += len(label) + 1
        return None

    def handle_key(self, event) -> bool:
        if event.key in PREV_KEYS:
            return self.step(-1)
        if event.key in NEXT_KEYS:
            return self.step(1)
        return False

    def handle_mouse(self, event) -> bool:
        if event.is_left_press:
            value = self.option_at(event.x)
            if value is not None and self.enabled:
                self.select(value)
            return True
        return False

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        drawn = 0
        pos = 0
        for i, (value, label) in enumerate(self.options):
            chunks = [(" ", False)] if i else []
            chunks.append((label, value == self.value))
            for txt, chosen in chunks:
                lo = max(offset - pos, 0)
                pos += len(txt)
                if lo >= len(txt) or drawn >= width:
                    continue
                piece = txt[lo : lo + width - drawn]
                win.addstr(
                    piece,
                    colors.attr(
                        self.color, selected, chosen or marked, dim=not self.enabled
                    ),
                )
                drawn += len(piece)
        return drawn
