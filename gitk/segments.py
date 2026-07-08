"""UI segments: the inline pieces a SegmentedListItem renders on one row.

Segment hierarchy (text, fillers, buttons, toggles, choices). Segments reach
the App struct through their owning item (`get_app()` -> item -> view -> app),
so this module needs no view imports; it depends only on Screen (for the colour
palette) and the pure `ref_color_and_title` helper.
"""

from __future__ import annotations

from gitk.ids import ID_GIT_REFS
from gitk.screen import Screen


def ref_color_and_title(ref, head_branch=""):
    """Colour pair and display title for a git ref record. Pure: depends only on
    the ref dict and (for the current HEAD) the branch name. Used by RefSegment
    and RefListItem so neither has to reach into GitRefsView."""
    title = f"({ref['name']})"
    color = Screen.C_REF_LOCAL
    if ref["type"] == "head":
        color = Screen.C_HEAD
        if head_branch:
            title += " ->"
    elif ref["type"] == "heads":
        title = f"[{ref['name']}]"
    elif ref["type"] == "remotes":
        color = Screen.C_REF_REMOTE
        title = f"{{{ref['name']}}}"
    elif ref["type"] == "tags":
        color = Screen.C_TAG
        title = f"<{ref['name']}>"
    elif ref["type"] == "stash":
        color = Screen.C_STASH
    return color, title


class Segment:
    # Back-reference to the owning SegmentedListItem, set in its __init__.
    # Lets a segment reach the App struct (segment -> item -> view -> app).
    _item = None

    def get_app(self):
        """The App struct this segment belongs to, reached through its item."""
        return self._item.get_app() if self._item is not None else None

    def get_text(self) -> str:
        return ""

    def get_context_menu(self):
        """The context menu this segment opens, as a (menu_item, view_id) pair,
        or None if it has none. Used both by a right-click on the segment and by
        F7 keyboard cycling (SegmentedListItem.get_context_menu_targets)."""
        return None

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return 0

    def _draw_text(self, win, offset, width, color) -> int:
        """Draw the segment's text from horizontal-scroll `offset`, clipped to
        `width` columns, in `color`; return how many cells it consumed. `width`
        is a column COUNT (remaining space), not an absolute end index — so the
        right bound is offset+width. Shared by the simple draw() variants."""
        visible_txt = self.get_text()[offset : offset + width]
        win.addstr(visible_txt, color)
        return len(visible_txt)

    def handle_mouse_input(self, mouse) -> bool:
        return False


class FillerSegment(Segment):
    pass


class TextSegment(Segment):
    def __init__(self, txt, color=Screen.C_NORMAL):
        super().__init__()
        self.txt = txt
        self.color = color

    def get_text(self):
        return self.txt

    def set_text(self, txt: str):
        self.txt = txt

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return self._draw_text(
            win, offset, width, Screen.color(self.color, selected, marked, matched)
        )


class RefSegment(TextSegment):
    def __init__(self, ref, head_branch=""):
        self.ref = ref
        color, txt = ref_color_and_title(ref, head_branch)
        super().__init__(txt, color)

    def get_context_menu(self):
        from gitk.items import RefListItem  # late import: avoids segments<->items cycle

        return (RefListItem(self.ref), ID_GIT_REFS)

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == "right-click":
            return self.get_app().context_menu.show_context_menu(
                *self.get_context_menu()
            )
        elif mouse.event_type == "double-click" and "tag_id" in self.ref:
            self.get_app().git_diff.show_tag_annotation(self.ref["tag_id"])
            return True
        else:
            return super().handle_mouse_input(mouse)


class ButtonSegment(TextSegment):
    def __init__(self, txt, callback, color=Screen.C_NORMAL):
        super().__init__(txt, color)
        self.callback = callback
        self.is_pressed = False

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type in ("left-click", "double-click", "left-move-in"):
            self.is_pressed = True
            return True

        if mouse.event_type == "left-move-out":
            self.is_pressed = False
            return True

        if mouse.event_type == "left-release":
            self.is_pressed = False
            return self.callback()
        else:
            return super().handle_mouse_input(mouse)

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
                Screen.color(self.color, True, marked, bold=bold, dim=dim),
            )
        return super().draw(win, offset, width, selected, matched, marked)


class ToggleSegment(TextSegment):
    def __init__(
        self, txt, toggled=False, callback=lambda val: None, color=Screen.C_NORMAL
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

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type in ("left-click", "double-click"):
            self.toggle()
            self.callback(self)
            return True
        else:
            return super().handle_mouse_input(mouse)

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return self._draw_text(
            win,
            offset,
            width,
            Screen.color(self.color, selected, self.toggled, dim=not self.enabled),
        )


class SplitButtonSegment(ButtonSegment):
    """Header button that shows the current split-view state and cycles it."""

    _LABELS = {"off": "[Split]", "side": "[Split |]", "stacked": "[Split =]"}

    def __init__(self, color=Screen.C_TITLE):
        # Defer the action: at construction the segment isn't wired to its item
        # yet, so reach the app lazily (the button is clicked long after wiring).
        super().__init__("", lambda: self.get_app().split.cycle_split_view(), color)

    def get_text(self):
        return self._LABELS.get(self.get_app().split.split_mode, "[Split]")


class DynamicTextSegment(TextSegment):
    """TextSegment whose text is recomputed by a getter on every draw."""

    def __init__(self, getter, color=Screen.C_NORMAL):
        super().__init__("", color)
        self.getter = getter

    def get_text(self):
        return str(self.getter())


class HighlightToggleSegment(ButtonSegment):
    """Header button with a fixed label, highlighted while its state is on."""

    def __init__(self, label, is_active, on_toggle, color=Screen.C_TITLE):
        super().__init__(label, on_toggle, color)
        self._is_active = is_active

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return super().draw(win, offset, width, selected, matched, self._is_active())


class OnOffToggleSegment(ToggleSegment):
    def __init__(self, toggled=False, color=Screen.C_NORMAL):
        super().__init__("", toggled, color=color)
        self.set_toggled(toggled)

    def set_toggled(self, value):
        self.toggled = value
        # Display form: active side in CAPS, inactive side lowercase
        self.txt = "[ON|off]" if self.toggled else "[on|OFF]"

    def toggle(self):
        self.set_toggled(not self.toggled)

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        # Chunks: (text, is_active_side). The active side is highlighted (blue) and CAPS.
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
                Screen.color(
                    self.color, selected, highlighted, marked, dim=not self.enabled
                ),
            )
            drawn += len(sub)
        return drawn


class ChoiceSegment(ButtonSegment):
    """Button that cycles through a fixed list of (value, label) options."""

    def __init__(self, options, value, color=Screen.C_NORMAL):
        self.options = options
        self.value = value
        super().__init__("", self._cycle, color)

    def _cycle(self):
        values = [v for v, _ in self.options]
        i = values.index(self.value) if self.value in values else 0
        self.value = values[(i + 1) % len(values)]
        return True

    def set_value(self, value):
        self.value = value

    def get_text(self):
        return "<" + dict(self.options).get(self.value, self.value) + ">"
