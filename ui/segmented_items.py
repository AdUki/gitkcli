"""
Composite items built from segments
"""
from typing import List, Callable, Optional
from .items import Item
from .segments import Segment, FillerSegment, TextSegment, ButtonSegment
from .context import UIContext


class SegmentedListItem(Item):
    """
    A list item composed of multiple segments.

    Segments can be text, buttons, toggles, or filler spaces, allowing for
    complex interactive list items with multiple clickable regions.

    Attributes:
        segments: List of segments to display
        segment_separator: String to place between segments
        bg_color: Background color code
        filler_width: Calculated width for filler segments
        clicked_segment: Currently clicked segment for mouse tracking
    """

    def __init__(self, segments: List[Segment] = None, bg_color: int = 1, ui_context: UIContext = None):
        """
        Initialize a segmented list item.

        Args:
            segments: List of segments to compose this item
            bg_color: Background color code
            ui_context: UI context for accessing screen, logging, and callbacks
        """
        super().__init__(ui_context)
        self.segment_separator = ' '
        self.segments = segments or []
        self.filler_width = 0
        self.bg_color = bg_color
        self.clicked_segment = None

    def get_segments(self) -> List[Segment]:
        """Get the list of segments."""
        return self.segments

    def get_text(self) -> str:
        """
        Get the text content of all segments combined.

        Returns:
            Combined text of all segments separated by segment_separator
        """
        text = ''
        first = True
        for segment in self.get_segments():
            if first:
                first = False
            elif self.segment_separator:
                text += self.segment_separator
            text += segment.get_text()

        return text

    def get_segment_on_offset(self, offset: int) -> Segment:
        """
        Find which segment is at a given x offset.

        Args:
            offset: Horizontal offset to check

        Returns:
            Segment at that offset, or empty Segment if none found
        """
        segment_pos = 0
        for segment in self.get_segments():
            if isinstance(segment, FillerSegment):
                length = self.fill_width
            else:
                length = len(segment.get_text())
            if segment_pos <= offset < segment_pos + length:
                return segment
            segment_pos += length + len(self.segment_separator)
        return Segment(self._ui_context)

    def handle_mouse_input(self, event_type: str, x: int, y: int) -> bool:
        """
        Handle mouse input by routing to the appropriate segment.

        Args:
            event_type: Type of mouse event
            x: Mouse x coordinate
            y: Mouse y coordinate

        Returns:
            True if input was handled
        """
        segment = self.clicked_segment or self.get_segment_on_offset(x)
        if 'left-click' == event_type or 'double-click' == event_type:
            self.clicked_segment = segment
        elif self.clicked_segment:
            if 'release' in event_type:
                self.clicked_segment = None
            if 'move-in' in event_type and self.clicked_segment != self.get_segment_on_offset(x):
                event_type = event_type.replace('in', 'out')
        if segment and segment.handle_mouse_input(event_type, x, y):
            return True
        return super().handle_mouse_input(event_type, x, y)

    def get_fill_txt(self, width: int) -> str:
        """
        Calculate the fill text for filler segments.

        Args:
            width: Total available width

        Returns:
            String of spaces to fill, or empty string if no fillers
        """
        fillers_count = 0
        for segment in self.get_segments():
            if isinstance(segment, FillerSegment):
                fillers_count += 1
        if fillers_count:
            self.fill_width = int((width - len(self.get_text())) / fillers_count)
            return self.fill_width * ' '
        return ''

    def draw_line(self, win, offset: int, width: int, selected: bool, matched: bool, marked: bool):
        """
        Draw all segments to a curses window.

        Args:
            win: Curses window to draw to
            offset: Horizontal scroll offset
            width: Available width for drawing
            selected: Whether this item is currently selected
            matched: Whether this item matches a search
            marked: Whether this item is marked
        """
        draw_separator = False
        remaining_width = width
        for segment in self.get_segments():
            if draw_separator and self.segment_separator:
                draw_separator = False
                remaining_width -= len(self.segment_separator)
                win.addstr(self.segment_separator,
                          self._ui_context.screen.color(self.bg_color, selected, marked, matched))
            if isinstance(segment, FillerSegment):
                txt = self.get_fill_txt(width)
                win.addstr(txt, self._ui_context.screen.color(self.bg_color, selected, marked, matched, matched))
                length = len(txt)
            else:
                length = segment.draw(win, offset, remaining_width, selected, matched, marked)
                txt = segment.get_text()
            draw_separator = length > 0
            remaining_width -= length
            if remaining_width <= 0:
                break
            offset -= len(txt) - length

        if remaining_width > 0:
            if selected or marked:
                win.addstr(' ' * remaining_width,
                          self._ui_context.screen.color(self.bg_color, selected, marked, matched))
            else:
                win.clrtoeol()


class WindowTopBarItem(SegmentedListItem):
    """
    A window title bar with menu button, title, and close button.

    Supports additional custom segments (e.g., navigation buttons).
    Handles double-click to toggle window mode.

    Attributes:
        title_segment: The title text segment
        on_double_click: Optional callback for double-click on title bar
    """

    def __init__(self, title: str, additional_segments: List[Segment] = None, color: int = 30,
                 ui_context: UIContext = None, on_menu_click: Callable = None,
                 on_close_click: Callable = None, on_double_click: Callable = None):
        """
        Initialize a window top bar.

        Args:
            title: Window title text
            additional_segments: Optional additional segments to insert before close button
            color: Color code for the bar
            ui_context: UI context for accessing screen, logging, and callbacks
            on_menu_click: Callback for menu button click (should return bool)
            on_close_click: Callback for close button click (should return bool)
            on_double_click: Callback for double-click on title bar (should return bool)
        """
        self.title_segment = TextSegment(title, color, ui_context)
        self.on_double_click = on_double_click

        # Create segments list
        menu_btn = ButtonSegment('[Menu]', on_menu_click or (lambda: False), color, ui_context)
        segments = [menu_btn, self.title_segment, FillerSegment(ui_context)]

        # Add any additional segments
        if additional_segments:
            segments.extend(additional_segments)

        # Add close button
        close_btn = ButtonSegment("[X]", on_close_click or (lambda: False), color, ui_context)
        segments.append(close_btn)

        super().__init__(segments, color, ui_context)

    def set_title(self, txt: str):
        """
        Set the window title.

        Args:
            txt: New title text
        """
        self.title_segment.set_text(txt)

    def handle_mouse_input(self, event_type: str, x: int, y: int) -> bool:
        """
        Handle mouse input on the title bar.

        Handles segment clicks and double-click for window mode toggle.

        Args:
            event_type: Type of mouse event
            x: Mouse x coordinate
            y: Mouse y coordinate

        Returns:
            True if input was handled
        """
        handled = super().handle_mouse_input(event_type, x, y)
        if handled:
            return True
        if 'double-click' == event_type:
            if self.on_double_click:
                return self.on_double_click()
        return False
