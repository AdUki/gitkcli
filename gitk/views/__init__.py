"""The concrete views package.

Each view is its own module; this package re-exports them so importers can use
`from gitk.views import GitLogView` etc.
"""

from gitk.views.git_log import GitLogView
from gitk.views.git_diff import GitDiffView
from gitk.views.git_refs import GitRefsView
from gitk.views.log import LogView
from gitk.views.context_menu import ContextMenu
