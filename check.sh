#!/usr/bin/env bash
#
# Lightweight dev checks: format, lint, test, coverage.
#
set -euo pipefail

cd "$(dirname "$0")"

# The project's own Python: package, entry shim, packaging, tests.
# build/ is gitignored (ruff skips it automatically).
SOURCES=(gitk gitkcli.py setup.py test)

usage() {
    cat <<'EOF'
Usage: ./check.sh <command> [extra args]

Commands:
  format     apply standard formatting (ruff format)
  lint       ruff check + format --check (add --fix to autofix)
  test       golden-screen test suite
  coverage   run the suite under coverage, print a gitk/ report
  all        format, then lint, then test

Extra args are forwarded to the underlying tool, e.g.:
  ./check.sh lint --fix
  ./check.sh test --filter refs
  ./check.sh coverage --filter refs
EOF
}

have() { command -v "$1" >/dev/null 2>&1; }
need_ruff() { have ruff || { echo "ruff not found (pip install ruff)" >&2; exit 1; }; }

run_format() {
    need_ruff
    echo ">> ruff format"
    ruff format "${SOURCES[@]}" "$@"
}

run_lint() {
    need_ruff
    echo ">> ruff check"
    ruff check "${SOURCES[@]}" "$@"
    echo ">> ruff format --check"
    ruff format --check "${SOURCES[@]}"
}

run_test() {
    echo ">> test suite"
    python3 test/run.py "$@"
}

run_coverage() {
    echo ">> test suite (coverage)"
    python3 test/run.py --coverage "$@"
}

cmd="${1:-}"
[ $# -gt 0 ] && shift || true

case "$cmd" in
    format)        run_format "$@" ;;
    lint)          run_lint "$@" ;;
    test)          run_test "$@" ;;
    coverage)      run_coverage "$@" ;;
    all)           run_format; run_lint; run_test ;;
    ""|-h|--help|help) usage ;;
    *) echo "unknown command: $cmd" >&2; echo >&2; usage >&2; exit 2 ;;
esac
