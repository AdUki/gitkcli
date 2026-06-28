# gitkcli tests

Golden-screen tests: each case feeds keystrokes to the real curses app on a
fixed-size pty and diffs the screen against a raw-ANSI snapshot (`less -R` a
golden to see colour, e.g. the selected row's background highlight).

## Run

    bash test/create_test_repo.sh          # build the fixture repo (once)
    pip install -r test/requirements.txt   # pyte (only dep)
    python3 test/run.py                    # run all   (-h = flags + cheat sheet)
    python3 test/run.py --list             # list cases
    python3 test/run.py -u                  # (re)generate goldens after a UI change
    python3 test/run.py --live diff_view    # replay a case, then drop you into it
    pytest test/                            # same suite, as per-case nodes

## Write a case

`cases/<name>/spec.txt` drives the app; each `capture` saves/compares
`cases/<name>/golden/<capture>.txt`. Directives (also in `run.py -h`):

    size 120x40           pty size, before launch
    config default        config/<name> template (pins preferences)
    launch [args]         start app; args go to it (e.g. launch --graph)
    run <shell>           shell cmd in the work repo (setup / external change)
    key <tokens>          send keys; *N repeats; bare chars literal
    text "literal"        type a string (e.g. into search)
    mouse VERB COL ROW    click | dblclick | rclick at 1-based screen coords
    wait stable | <secs>  settle, or fixed pause (wait 2.5 clears the 2s flash)
    resize WxH            resize mid-test
    capture <name>        snapshot the screen
    expect-exit           assert the app exited

Keys: bare chars or <Up> <Down> <Enter> <Esc> <Tab> <PgUp> <PgDn> <F1>..<F12>
<S-F5>, ctrl as C-w / C-Left; repeat with *N.

## Notes

Each case runs on a fresh fixture copy + isolated config → deterministic; diff/refs goldens track the git version (`ENVIRONMENT`), regenerate with `-u`.
Mouse is press/release only (no pty motion): clicks/dbl/right-clicks work, no drag or wheel.
