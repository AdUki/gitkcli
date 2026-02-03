"""
UI Library - Reusable curses-based UI components

This library provides base classes for building terminal user interfaces with curses.
It uses dependency injection via UIContext to decouple from application-specific code.
"""

from .context import UIContext
from .items import Item, SeparatorItem, SpacerListItem, TextListItem, UserInputListItem
from .segments import Segment, FillerSegment, TextSegment, ButtonSegment, ToggleSegment
from .segmented_items import SegmentedListItem, WindowTopBarItem
from .view import View
from .list_view import ListView
from .screen import Screen, Mouse

__all__ = [
    'UIContext',
    'Item',
    'SeparatorItem',
    'SpacerListItem',
    'TextListItem',
    'UserInputListItem',
    'Segment',
    'FillerSegment',
    'TextSegment',
    'ButtonSegment',
    'ToggleSegment',
    'SegmentedListItem',
    'WindowTopBarItem',
    'View',
    'ListView',
    'Screen',
    'Mouse',
]

__version__ = '0.1.0'
