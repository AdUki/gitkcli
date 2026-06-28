#!/usr/bin/env bash
# Build a richly-populated git repository for exercising gitkcli.
# Wipes and rebuilds test/repo/ and test/remotes/*.git from scratch.

set -euo pipefail

# Pin timezone so commit dates serialize identically regardless of the host.
export TZ=UTC

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ "$(basename "$SCRIPT_DIR")" != "test" ]]; then
    echo "Error: this script must live in a directory named 'test/' — refusing to run." >&2
    exit 1
fi

REPO_DIR="$SCRIPT_DIR/repo"
REMOTES_DIR="$SCRIPT_DIR/remotes"

echo "Wiping previous fixture..."
rm -rf "$REPO_DIR" "$REMOTES_DIR"
mkdir -p "$REPO_DIR" "$REMOTES_DIR"

echo "Initializing bare remotes..."
for remote in origin upstream fork; do
    git init --bare -q -b master "$REMOTES_DIR/$remote.git"
done

echo "Initializing working repo..."
git init -q -b master "$REPO_DIR"
cd "$REPO_DIR"
git config user.name "Default Dev"
git config user.email "dev@example.com"
git config commit.gpgSign false
git config tag.gpgSign false
git remote add origin   "$REMOTES_DIR/origin.git"
git remote add upstream "$REMOTES_DIR/upstream.git"
git remote add fork     "$REMOTES_DIR/fork.git"

# ---------- deterministic pseudo-randomness ----------
# Pure function of an integer key — same value every run, survives subshells.
det_rand() {
    local k=$1
    local h=$(( (k * 2654435761 + 1013904223) & 0x7FFFFFFF ))
    h=$(( ((h ^ (h >> 13)) * 1597334677) & 0x7FFFFFFF ))
    h=$(( (h ^ (h >> 16)) & 0x7FFFFFFF ))
    echo "$h"
}

AUTHORS=(
    "Alice Anderson|alice@example.com"
    "Bob Brown|bob@example.com"
    "Carol Chen|carol@example.com"
    "David Davis|david@example.com"
    "Eve Evans|eve@example.com"
)

# Fixed base date (UTC) — keeps commit hashes byte-for-byte identical across runs.
CURRENT_DATE=$(date -d "2024-06-01 09:00:00 UTC" +%s)
COMMIT_COUNTER=0

advance_date() {
    local h; h=$(det_rand $(( COMMIT_COUNTER * 31 + 17 )))
    local step=$(( (h % 86400) + 14400 ))   # 4h..28h
    CURRENT_DATE=$(( CURRENT_DATE + step ))
}

format_date() { date -d "@$CURRENT_DATE" --iso-8601=seconds; }

current_author() {
    echo "${AUTHORS[$(( COMMIT_COUNTER % ${#AUTHORS[@]} ))]}"
}

make_commit() {
    local message="$1"
    advance_date
    local author name email date_str
    author="$(current_author)"
    name="${author%|*}"; email="${author#*|}"
    date_str="$(format_date)"
    GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" \
    GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" \
    GIT_AUTHOR_DATE="$date_str" GIT_COMMITTER_DATE="$date_str" \
    git commit -q -m "$message"
    COMMIT_COUNTER=$(( COMMIT_COUNTER + 1 ))
}

make_multiline_commit() {
    local subject="$1"
    advance_date
    local author name email date_str
    author="$(current_author)"
    name="${author%|*}"; email="${author#*|}"
    date_str="$(format_date)"
    GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" \
    GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" \
    GIT_AUTHOR_DATE="$date_str" GIT_COMMITTER_DATE="$date_str" \
    git commit -q -m "$subject" -m "This change includes the following:

- Reviewed the relevant module
- Made several small adjustments
- Tested locally against the fixture

Co-authored-by: $name <$email>"
    COMMIT_COUNTER=$(( COMMIT_COUNTER + 1 ))
}

make_merge_commit() {
    local branch="$1"
    advance_date
    local author name email date_str
    author="$(current_author)"
    name="${author%|*}"; email="${author#*|}"
    date_str="$(format_date)"
    GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" \
    GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" \
    GIT_AUTHOR_DATE="$date_str" GIT_COMMITTER_DATE="$date_str" \
    git merge -q --no-ff "$branch" -m "Merge branch '$branch'"
    COMMIT_COUNTER=$(( COMMIT_COUNTER + 1 ))
}

# Stashes get pinned dates so refs/stash is byte-for-byte reproducible. Unlike
# make_commit this deliberately does NOT advance CURRENT_DATE or COMMIT_COUNTER:
# stash commits are off to the side (refs/stash), so leaving the main timeline
# untouched keeps every branch/tag SHA identical to a run without stashes.
STASH_COUNTER=0
stash_push() {
    local d; d="$(date -d "@$(( CURRENT_DATE + (STASH_COUNTER + 1) * 3600 ))" --iso-8601=seconds)"
    GIT_AUTHOR_DATE="$d" GIT_COMMITTER_DATE="$d" git stash push "$@"
    STASH_COUNTER=$(( STASH_COUNTER + 1 ))
}

SUBJECTS=(
    "Refactor request handler"
    "Fix off-by-one in pagination"
    "Add timeout to HTTP client"
    "Bump dependency versions"
    "Improve error messages"
    "Tighten input validation"
    "Drop unused helper"
    "Inline a one-shot function"
    "Rename variable for clarity"
    "Reorder imports"
    "Add docstring to public API"
    "Remove dead code"
    "Cache repeated lookup"
    "Fix typo in log message"
    "Handle empty input case"
    "Extract magic number into constant"
    "Use context manager for file handle"
    "Switch to logging from prints"
    "Add type hints"
    "Guard against null inputs"
    "Polish CLI output"
    "Document the config schema"
    "Trim trailing whitespace"
    "Clarify error path"
    "Speed up hot path"
)
random_subject() {
    local h; h=$(det_rand $(( COMMIT_COUNTER * 13 + 5 )))
    echo "${SUBJECTS[$(( h % ${#SUBJECTS[@]} ))]}"
}

random_line() {
    local n=$COMMIT_COUNTER
    case "${1:-code}" in
        code) echo "    return value_${n} + $(( n * 7 ))  # iteration ${n}" ;;
        md)   echo "- note ${n}: observation about feature ${n}" ;;
        text) echo "line ${n} extra-${n} (commit ${n})" ;;
    esac
}

touch_file() {
    local path="$1"
    local kind="${2:-code}"
    mkdir -p "$(dirname "$path")"
    if [[ ! -f "$path" ]]; then
        case "$kind" in
            code) printf '"""%s"""\n\n' "$path" > "$path" ;;
            md)   printf '# %s\n\n' "$path" > "$path" ;;
            text) : > "$path" ;;
        esac
    fi
    if [[ "$kind" == "json" ]]; then
        cat > "$path" <<EOF
{
  "version": "0.$((COMMIT_COUNTER / 50)).$((COMMIT_COUNTER % 50))",
  "build_number": $COMMIT_COUNTER,
  "feature_count": $((COMMIT_COUNTER + 5)),
  "last_author": "$(current_author | cut -d'|' -f1)"
}
EOF
    else
        random_line "$kind" >> "$path"
    fi
    git add "$path"
}

# ---------- initial commit ----------
cat > README.md <<'EOF'
# Example Project

A fixture repository used to exercise the gitkcli git browser. Not a real
project — files here exist only to make the log, diff, and refs views
interesting to navigate.

## Layout

- `src/`     - application code
- `docs/`    - documentation
- `config/`  - configuration files
- `tests/`   - tests
EOF
mkdir -p src docs config tests
cat > src/main.py <<'EOF'
"""Entry point for the example application."""


def main():
    print("hello, world")


if __name__ == "__main__":
    main()
EOF
cat > src/utils.py <<'EOF'
"""Utility helpers."""


def add(a, b):
    return a + b


def sub(a, b):
    return a - b
EOF
cat > docs/changelog.md <<'EOF'
# Changelog

## Unreleased
EOF
cat > config/settings.json <<'EOF'
{
  "version": "0.0.1"
}
EOF
cat > tests/test_main.py <<'EOF'
from src.main import main


def test_main(capsys):
    main()
    captured = capsys.readouterr()
    assert "hello" in captured.out
EOF
cat > .gitignore <<'EOF'
__pycache__/
*.pyc
.venv/
*.egg-info/
build/
dist/
EOF

git add .
make_commit "Initial commit"

# ---------- phase 1: straight-line master commits ----------
echo "Generating main timeline (phase 1)..."

PY_FILES=("src/main.py" "src/utils.py")
MD_FILES=("README.md" "docs/changelog.md")
TEST_FILE="tests/test_main.py"

for i in $(seq 1 200); do
    structural=true
    if   (( i == 20 )); then touch_file "src/api/handlers.py" code
    elif (( i == 35 )); then touch_file "src/api/routes.py" code
    elif (( i == 55 )); then touch_file "LICENSE" text
    elif (( i == 80 )); then
        mkdir -p src/common
        git mv src/utils.py src/common/utils.py
        PY_FILES=("src/main.py" "src/common/utils.py")
    elif (( i == 100 )); then
        git rm -q "$TEST_FILE"
        TEST_FILE="tests/test_smoke.py"
        touch_file "$TEST_FILE" code
    elif (( i == 140 )); then
        touch_file "docs/architecture.md" md
        touch_file "docs/contributing.md" md
    elif (( i == 170 )); then
        git rm -q src/api/routes.py
    else
        structural=false
    fi

    if [[ "$structural" == "false" ]]; then
        case $(( i % 7 )) in
            0) touch_file "${PY_FILES[$(( i % ${#PY_FILES[@]} ))]}" code ;;
            1) touch_file "${MD_FILES[$(( i % ${#MD_FILES[@]} ))]}" md ;;
            2) touch_file "${PY_FILES[1]}" code; touch_file "$TEST_FILE" code ;;
            3) touch_file "config/settings.json" json ;;
            4) touch_file "src/main.py" code ;;
            5) touch_file "docs/changelog.md" md; touch_file "README.md" md ;;
            6) touch_file "${PY_FILES[1]}" code ;;
        esac
    fi

    if (( i % 23 == 0 )); then
        make_multiline_commit "$(random_subject)"
    else
        make_commit "$(random_subject)"
    fi
done

# Capture upstream's master tip here — upstream will lag from this point.
UPSTREAM_MASTER_TIP="$(git rev-parse HEAD)"

# ---------- phase 2: feature branches with --no-ff merges ----------
echo "Creating merged feature branches..."

FEATURE_BRANCHES=(
    "feature/login-flow"
    "feature/dark-mode"
    "feature/api-pagination"
    "feature/caching-layer"
    "feature/csv-export"
    "feature/admin-dashboard"
    "feature/webhook-support"
    "feature/profile-page"
)

for fb_idx in "${!FEATURE_BRANCHES[@]}"; do
    branch="${FEATURE_BRANCHES[$fb_idx]}"
    # One master commit between merges, for visual separation in the log graph.
    touch_file "src/main.py" code
    make_commit "$(random_subject)"

    git checkout -q -b "$branch"
    h=$(det_rand $(( fb_idx * 101 + 7 )))
    n=$(( (h % 5) + 4 ))   # 4..8 commits per branch
    for j in $(seq 1 $n); do
        touch_file "src/feature_${branch##*/}.py" code
        if   (( j == 1 )); then make_commit "Scaffold ${branch##*/}"
        elif (( j == n )); then make_multiline_commit "Polish ${branch##*/} for review"
        else                    make_commit "WIP ${branch##*/}: $(random_subject)"
        fi
    done

    git checkout -q master
    make_merge_commit "$branch"
done

# ---------- phase 3: unmerged branches left lying around ----------
echo "Creating WIP / unmerged branches..."

UNMERGED_BRANCHES=(
    "wip/migrate-to-postgres"
    "wip/refactor-auth"
    "wip/experiment-redis"
    "bugfix/leaking-fd"
    "release/0.9"
)

for ub_idx in "${!UNMERGED_BRANCHES[@]}"; do
    branch="${UNMERGED_BRANCHES[$ub_idx]}"
    git checkout -q -b "$branch" master
    h=$(det_rand $(( ub_idx * 211 + 19 )))
    n=$(( (h % 4) + 2 ))   # 2..5 commits
    for j in $(seq 1 $n); do
        touch_file "src/wip_${branch##*/}.py" code
        make_commit "[$branch] $(random_subject)"
    done
done
git checkout -q master

# ---------- phase 4: more master commits so master is ahead of upstream ----------
echo "Final master commits..."

for i in $(seq 1 40); do
    touch_file "${PY_FILES[$(( i % 2 ))]}" code
    if (( i % 11 == 0 )); then
        make_multiline_commit "$(random_subject)"
    else
        make_commit "$(random_subject)"
    fi
done

# A branch pointing 25 commits behind master.
git branch behind/old-master master~25

# A branch ahead of master with a few extra commits.
git checkout -q -b ahead/experimental master
for i in 1 2 3 4; do
    touch_file "src/experimental.py" code
    make_commit "Experimental: $(random_subject)"
done
git checkout -q master

# ---------- tags ----------
echo "Creating tags..."

mapfile -t ALL_COMMITS < <(git rev-list master)
TOTAL=${#ALL_COMMITS[@]}

annotated_tag() {
    local tag="$1" target="$2" msg="$3"
    advance_date
    local date_str; date_str="$(format_date)"
    GIT_COMMITTER_DATE="$date_str" \
    git tag -a "$tag" -m "$msg" "$target"
}

annotated_tag "v0.1.0"     "${ALL_COMMITS[$(( TOTAL - 5 ))]}"        "Initial release

The first publicly available version. Establishes the project layout
and basic CLI."
annotated_tag "v0.2.0"     "${ALL_COMMITS[$(( TOTAL * 4 / 5 ))]}"    "v0.2.0

- Added utility helpers
- Improved configuration story
- Various bugfixes"
annotated_tag "v0.3.0"     "${ALL_COMMITS[$(( TOTAL * 3 / 5 ))]}"    "v0.3.0

Substantial expansion of the API surface.

- New handler module
- Pagination helper
- Initial caching"
annotated_tag "v0.4.0"     "${ALL_COMMITS[$(( TOTAL * 2 / 5 ))]}"    "v0.4.0 — dark mode + login flow"
annotated_tag "v0.5.0"     "${ALL_COMMITS[$(( TOTAL / 3 ))]}"        "v0.5.0 — admin dashboard"
annotated_tag "v0.6.0"     "${ALL_COMMITS[$(( TOTAL / 4 ))]}"        "v0.6.0 — webhooks and profile page"
annotated_tag "v0.7.0"     "${ALL_COMMITS[$(( TOTAL / 6 ))]}"        "v0.7.0 — performance pass"
annotated_tag "v0.8.0"     "${ALL_COMMITS[$(( TOTAL / 8 ))]}"        "v0.8.0 — stability fixes"
annotated_tag "v0.9.0-rc1" "${ALL_COMMITS[$(( TOTAL / 12 ))]}"       "Release candidate for 0.9"
annotated_tag "v1.0.0"     "${ALL_COMMITS[0]}"                       "v1.0.0

The first stable release.

Highlights:
- Stable public API
- Documentation
- Full test coverage"

git tag wip-marker     "${ALL_COMMITS[$(( TOTAL - 10 ))]}"
git tag pre-refactor   "${ALL_COMMITS[$(( TOTAL * 3 / 4 ))]}"
git tag post-refactor  "${ALL_COMMITS[$(( TOTAL * 2 / 3 ))]}"
git tag good-baseline  "${ALL_COMMITS[$(( TOTAL / 2 ))]}"
git tag demo-friday    "${ALL_COMMITS[$(( TOTAL / 3 + 5 ))]}"
git tag broken-build   "${ALL_COMMITS[$(( TOTAL / 4 + 10 ))]}"
git tag green-ci       "${ALL_COMMITS[$(( TOTAL / 5 ))]}"
git tag handover-2024  "${ALL_COMMITS[$(( TOTAL / 7 ))]}"
git tag perf-bench     "${ALL_COMMITS[$(( TOTAL / 10 ))]}"
git tag latest-stable  "${ALL_COMMITS[1]}"

# ---------- push to remotes ----------
echo "Populating origin..."
git push -q origin master
git push -q origin "ahead/experimental"
git push -q origin "behind/old-master"
for b in "${FEATURE_BRANCHES[@]}"; do git push -q origin "$b"; done
for b in "${UNMERGED_BRANCHES[@]}"; do git push -q origin "$b"; done
git push -q origin --tags

# origin-only branches: create locally, push, then delete locally so the
# remote-tracking ref `origin/<name>` is the only place they appear.
ORIGIN_ONLY=("abandoned/old-api" "release/0.8.x-maintenance" "wip/someone-else-experiment" "review/pr-1234")
for oo_idx in "${!ORIGIN_ONLY[@]}"; do
    orig_only="${ORIGIN_ONLY[$oo_idx]}"
    h=$(det_rand $(( oo_idx * 331 + 23 )))
    base_offset=$(( (h % 25) + 5 ))
    git checkout -q -b "$orig_only" "master~$base_offset"
    touch_file "src/origin_only_${orig_only##*/}.py" code
    make_commit "Origin-only work: $(random_subject)"
    git push -q origin "$orig_only"
    git checkout -q master
    git branch -q -D "$orig_only"
done

echo "Populating upstream..."
# upstream/master lags — push it at the earlier tip.
git push -q upstream "$UPSTREAM_MASTER_TIP:refs/heads/master"
for b in "${FEATURE_BRANCHES[@]:0:5}"; do git push -q upstream "$b"; done
# upstream-only branch
git checkout -q -b "legacy-import" "$UPSTREAM_MASTER_TIP"
touch_file "src/legacy.py" code
make_commit "Legacy import path (upstream maintainer)"
git push -q upstream "legacy-import"
git checkout -q master
git branch -q -D "legacy-import"

echo "Populating fork..."
for b in "${UNMERGED_BRANCHES[@]:0:3}"; do git push -q fork "$b"; done
git checkout -q -b "contributor-patch" master~10
touch_file "src/patch.py" code
make_commit "Contributor patch from fork"
git push -q fork "contributor-patch"
git checkout -q master
git branch -q -D "contributor-patch"

# ---------- stashes ----------
echo "Creating stashes..."

echo "    # work in progress" >> src/main.py
stash_push -q -m "WIP: tweaking entrypoint"

echo "scratch notes about the refactor" > scratch.txt
echo "    # another tweak" >> src/main.py
stash_push -q -u -m "WIP: scratch notes and entrypoint"

echo "  multi-file change" >> src/main.py
echo "  multi-file change" >> README.md
echo "  multi-file change" >> docs/changelog.md
stash_push -q -m "WIP: multi-file refactor draft"

echo "  unnamed wip" >> src/main.py
stash_push -q

echo "  before extra commit" >> src/main.py
stash_push -q -m "WIP: kept while doing something else"
touch_file "src/main.py" code
make_commit "Quick fix while stash sits around"

# ---------- summary ----------
echo
echo "===== Test repo created ====="
echo "  Location:          $REPO_DIR"
echo "  Commits (all):     $(git rev-list --count --all)"
echo "  Commits (master):  $(git rev-list --count master)"
echo "  Local branches:    $(git branch | wc -l)"
echo "  Remote branches:   $(git branch -r | wc -l)"
echo "  Tags:              $(git tag | wc -l)"
echo "  Stashes:           $(git stash list | wc -l)"
echo
echo "Remotes:"
git remote -v
