"""gitkcli's internal package.

The application is being split out of the historical single-file `gitkcli.py`
into cohesive modules here. During the migration, `gitkcli.py` re-exports the
moved names (`from gitk.<mod> import *`) so not-yet-moved code keeps resolving.
"""
