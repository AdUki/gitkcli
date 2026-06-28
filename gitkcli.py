#!/usr/bin/python
"""Entry-point shim. The application now lives in the `gitk` package; this file
stays as the historical `python3 gitkcli.py` launch path and console-script
target."""

from gitk.main import main

if __name__ == "__main__":
    main()
