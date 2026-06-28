"""Stable string identifiers for views, dialogs, and jobs.

A leaf module (no imports) so any layer — jobs, views, dialogs, main — can use
these IDs without reaching back into the entry module.
"""

ID_LOG = 'log'
ID_LOG_SEARCH = 'log-search'
ID_GIT_LOG = 'git-log'
ID_GIT_LOG_SEARCH = 'git-log-search'
ID_NEW_GIT_REF = 'git-log-ref'
ID_GIT_DIFF = 'git-diff'
ID_GIT_DIFF_SEARCH = 'git-diff-search'
ID_GIT_REFS = 'git-refs'
ID_GIT_REFS_SEARCH = 'git-refs-search'
ID_GIT_REF_PUSH = 'git-ref-push'
ID_CONTEXT_MENU = 'context-menu'
ID_CONFIRM_DIALOG = 'confirm-dialog'
ID_ERROR_DIALOG = 'error-dialog'
ID_GIT_REFRESH_HEAD = 'git-refresh-head'
ID_GIT_SEARCH = 'git-search'
ID_PREFERENCES = 'preferences'
ID_GIT_RESET = 'git-reset'
