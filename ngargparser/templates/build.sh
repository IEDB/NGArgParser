#!/bin/bash
#
# -----------------------------------------------------------------------------
# This file may only be modified by the admin responsible for the IEDB build system.
# General contributors must not edit this file.
#
# Build script for the IEDB project
#
# Developers edit and run the tool in the project folder. This script:
#   1. fetches dependencies into the project's libs/ with scripts/get_libs.sh
#      (skipped while scripts/get_libs.sh is unchanged),
#   2. copies the project into build/<TOOL>-<VERSION>/ (symlinks are kept as
#      links, never followed),
#   3. runs scripts/hooks.sh against that copy,
#   4. checks that every symlink stays inside the build and that the tool's
#      help screens start,
#   5. packs the tarball and makes the copied files read-only, because the
#      build dir is output: the next build replaces it.
# -----------------------------------------------------------------------------

# Parse command line arguments
PROGRESS_MODE=false
for arg in "$@"; do
    case $arg in
        --progress|-p)
            PROGRESS_MODE=true
            shift
            ;;
    esac
done

# Progress bar configuration
TOTAL_STEPS=10
CURRENT_STEP=0
PROGRESS_BAR_WIDTH=40

# Function to show progress bar
show_progress() {
    if [ "$PROGRESS_MODE" = true ]; then
        local message="$1"
        CURRENT_STEP=$((CURRENT_STEP + 1))
        local percent=$((CURRENT_STEP * 100 / TOTAL_STEPS))
        local filled=$((CURRENT_STEP * PROGRESS_BAR_WIDTH / TOTAL_STEPS))
        local empty=$((PROGRESS_BAR_WIDTH - filled))

        # Build progress bar string
        local bar=""
        for ((i=0; i<filled; i++)); do bar+="█"; done
        for ((i=0; i<empty; i++)); do bar+="░"; done

        # Print progress bar (overwrite previous line)
        printf "\r\033[K[%s] %3d%% - %s" "$bar" "$percent" "$message"

        # Print newline on completion
        if [ "$CURRENT_STEP" -eq "$TOTAL_STEPS" ]; then
            echo ""
        fi
    fi
}

# Function for verbose logging (only prints in non-progress mode)
log_verbose() {
    if [ "$PROGRESS_MODE" != true ]; then
        echo "$@"
    fi
}

# Messages the developer must see in both modes. In progress mode the bar line
# is cleared first so the message does not run into it.
notice() {
    if [ "$PROGRESS_MODE" = true ]; then
        printf '\r\033[K'
    fi
    echo "$@"
}

error() {
    if [ "$PROGRESS_MODE" = true ]; then
        printf '\r\033[K' >&2
    fi
    echo "ERROR: $*" >&2
}

# Set error handling based on mode
if [ "$PROGRESS_MODE" = true ]; then
    set -e
    set -o pipefail
else
    set -ex
    set -o pipefail
fi

# Globs must see dotfiles: hidden files are excluded from the build by the
# built-in '.*' rule, but a '!' negation in .distignore can only re-include a
# dotfile if the copy loops actually iterate over it.
shopt -s dotglob

# Resolve paths.
# This script lives in scripts/core/ (framework-owned, sync-managed). build.conf
# sits next to it in scripts/core/ (user-owned; sync never overwrites it).
# hooks.sh and get_libs.sh live one level up in scripts/ (user-owned).
# SRC_DIR keeps its established meaning: the user-owned scripts/ dir, which is
# what the hooks see via the exports below. The exclusion file is .distignore
# at PROJECT_ROOT (see setup_exclusions).
BUILD_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$BUILD_SH_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$SRC_DIR/.." && pwd)"
APP_NAME=$(basename "$PROJECT_ROOT")

# git is a hard dependency: the .distignore exclusion file is evaluated with
# `git check-ignore`, and scripts/get_libs.sh is fingerprinted with `git hash-object`.
if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: 'git' is required to build (distignore matching uses 'git check-ignore'). Install git and retry." >&2
    exit 1
fi
# The caller's environment must not redirect our git calls into another repo.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

# Initialize all build.conf-overridable variables to empty, source build.conf if present,
# then apply defaults. This lets per-project build.conf override anything below without
# touching build.sh (which is framework-owned and gets overwritten by `cli sync`).
TOOL_NAME=""
TARBALL_PREFIX=""
# No longer read. Initialized only so a leftover setting in build.conf can be reported.
EXCLUDE_FROM_BUILD_SYMLINK=""
if [ -f "$BUILD_SH_DIR/build.conf" ]; then
    # shellcheck source=build.conf
    source "$BUILD_SH_DIR/build.conf"
fi

# Apply defaults when not set by build.conf
[ -z "$TOOL_NAME" ] && TOOL_NAME="ng_${APP_NAME}"
# pull the tool version from the environment, otherwise set it to 'local'
TOOL_VERSION="${TOOL_VERSION:-local}"
# Replace '/' with '-': CI often passes the git ref name as TOOL_VERSION, and a
# branch like 'feature/foo' would otherwise break the version-stamping sed
# (s/TOOL_VERSION/.../ — the slash ends the command) and nest TOOL_DIR/BUILD_DIR.
TOOL_VERSION="${TOOL_VERSION//\//-}"
TOOL_DIR=$TOOL_NAME-$TOOL_VERSION
BUILD_DIR=$PROJECT_ROOT/build/$TOOL_DIR

[ -z "$TARBALL_PREFIX" ] && TARBALL_PREFIX="IEDB_"
if [ -n "$TARBALL_PREFIX" ]; then
    TAR_NAME="${TARBALL_PREFIX}$(echo "$TOOL_NAME" | tr '[:lower:]' '[:upper:]')-${TOOL_VERSION}.tar.gz"
else
    TAR_NAME="${TOOL_NAME}-${TOOL_VERSION}.tar.gz"
fi

# Dependency fetch state. .ngargparser/ is the project's runtime-state dir: the
# scaffold's .gitignore keeps it out of git and the '.*' rule keeps it out of
# the tarball.
STATE_DIR="$PROJECT_ROOT/.ngargparser"
GET_LIBS_SCRIPT="$SRC_DIR/get_libs.sh"
LIBS_STAMP="$STATE_DIR/libs-stamp"
GET_LIBS_LOG="$STATE_DIR/get_libs.log"
LIBS_DIR="$PROJECT_ROOT/libs"
STAMP_FORMAT="ngargparser-libs-stamp 1"
NL=$'\n'
LIBS_GITIGNORE_MARKER="# Managed by scripts/core/build.sh: entries fetched by scripts/get_libs.sh. Do not edit."

# Function to ensure __init__.py files exist in a directory tree
ensure_init_files() {
    local target_dir="$1"

    if [ -d "$target_dir" ]; then
        find "$target_dir" -type d | while read -r subdir; do
            if [ ! -f "$subdir/__init__.py" ]; then
                log_verbose "  Creating __init__.py in: $subdir"
                echo "# Auto-generated __init__.py file" > "$subdir/__init__.py"
            fi
        done
    fi
}

# rm -rf that also removes read-only directories (some archives and tool caches
# unpack that way, and the build makes its own output read-only).
rm_tree() {
    { [ -e "$1" ] || [ -L "$1" ]; } || return 0
    if [ -d "$1" ] && [ ! -L "$1" ]; then
        chmod -R u+w "$1"
    fi
    rm -rf "$1"
}

# ---------------------------------------------------------------------------
# distignore engine.
# The exclusion file (.distignore at the project root) has EXACT .gitignore
# semantics, evaluated by `git check-ignore` against a throwaway repo whose
# .gitignore is composed as:
#   1. '.*'                      built-in baseline: hidden files do not ship
#                                (lowest precedence — '!' rules can re-include)
#   2. the user's exclusion file, verbatim
#   3. '!README' '!deploy/' '!deploy/install.sh'
#                                tarball contract: the deploy orchestrator
#                                requires these at the tarball top level
#                                (highest precedence — not overridable)
# The project-root build/ and .git/ dirs are hard-pruned from the candidate
# list, so no rule (not even '!') can bring them back. Entries fetched by
# scripts/get_libs.sh are not filtered: they ship as get_libs.sh left them.
# ---------------------------------------------------------------------------
setup_exclusions() {
    TMPWORK="$(mktemp -d "${TMPDIR:-/tmp}/ngbuild-exclude.XXXXXX")"
    IGNORE_REPO="$TMPWORK/repo"
    EXCLUDED_LIST="$TMPWORK/excluded"
    local candidates="$TMPWORK/candidates"

    git -c init.defaultBranch=main init -q "$IGNORE_REPO"
    # A user's init.templateDir may seed info/exclude; builds must not depend
    # on developer-machine git config.
    : > "$IGNORE_REPO/.git/info/exclude" 2>/dev/null || true

    local distignore=""
    if [ -f "$PROJECT_ROOT/.distignore" ]; then
        distignore="$PROJECT_ROOT/.distignore"
    fi

    {
        printf '.*\n'
        if [ -n "$distignore" ]; then
            sed 's/\r$//' "$distignore"
            # Terminate the last line even when the file lacks a trailing newline,
            # so its final pattern cannot fuse with the forced rules below (a blank
            # line here is inert — .gitignore ignores blank lines).
            printf '\n'
        fi
        printf '!README\n!deploy/\n!deploy/install.sh\n'
    } > "$IGNORE_REPO/.gitignore"

    # Candidate paths: everything in the project, PROJECT_ROOT-relative. Nested
    # .git dirs are listed but not descended into, so the '.*' baseline
    # excludes them whole.
    local dirs="$TMPWORK/dirs"
    (
        cd "$PROJECT_ROOT" && {
            find . -mindepth 1 \( -path ./build -o -path ./.git \) -prune -o \
                -type d -name .git -prune -print -o -type d -print \
                | sed 's|^\./||' > "$dirs"
            cat "$dirs"
            find . -mindepth 1 \( -path ./build -o -path ./.git \) -prune -o \
                -type d -name .git -prune -o ! -type d -print \
                | sed 's|^\./||'
        }
    ) > "$candidates"

    # The project's dirs also exist (empty) inside the throwaway repo, so git
    # judges a dir as a dir, as in a real repo: dir-only patterns ('name/')
    # match it, and 'name/*' matches only what is inside it, not the dir
    # itself. Nested .git dirs are not created; '.*' excludes them by name.
    grep -v '\(^\|/\)\.git$' "$dirs" > "$TMPWORK/mkdirs" || true
    if [ -s "$TMPWORK/mkdirs" ]; then
        tr '\n' '\0' < "$TMPWORK/mkdirs" | (cd "$IGNORE_REPO" && xargs -0 mkdir -p)
    fi

    # Batch evaluation. Exit 0 = some paths ignored, 1 = none ignored (not an
    # error), >1 = fatal. Excluded dirs get their trailing '/' back, the form
    # is_excluded expects.
    local status=0
    git -C "$IGNORE_REPO" -c core.quotePath=off -c core.excludesFile=/dev/null \
        check-ignore --stdin < "$candidates" > "$TMPWORK/ignored" || status=$?
    if [ "$status" -gt 1 ]; then
        echo "ERROR: git check-ignore failed (exit $status) while evaluating distignore rules" >&2
        exit 1
    fi
    awk 'NR == FNR { dir[$0] = 1; next } { print (($0 in dir) ? $0 "/" : $0) }' \
        "$dirs" "$TMPWORK/ignored" > "$EXCLUDED_LIST"
}

# $1 = PROJECT_ROOT-relative path; directories MUST carry a trailing '/'.
is_excluded() {
    grep -qxF -- "$1" "$EXCLUDED_LIST"
}

# $1 = PROJECT_ROOT-relative dir path, no trailing '/'. True when any excluded
# path lives under it (descendants of an ignored dir are individually listed,
# so this is a complete test).
subtree_has_exclusions() {
    P="$1/" awk 'index($0, ENVIRON["P"]) == 1 { found = 1; exit } END { exit !found }' "$EXCLUDED_LIST"
}

# Copy one entry into the build tree, enforcing exclusions at every depth.
#   $1 src (absolute)   $2 dstparent (absolute)
#   $3 rel (PROJECT_ROOT-relative, no trailing '/')
# A directory with nothing excluded inside is copied in one go; otherwise it is
# walked entry by entry. `cp -RPp` copies a symlink as a link (never what it
# points at) and keeps modes and timestamps, on GNU and BSD alike.
stage_entry() {
    local src="$1" dstparent="$2" rel="$3"
    local name child
    name=$(basename "$src")

    if [ -d "$src" ] && [ ! -L "$src" ]; then
        is_excluded "$rel/" && return 0
        if ! subtree_has_exclusions "$rel"; then
            mkdir -p "$dstparent"
            log_verbose "Copying directory: $rel"
            cp -RPp "$src" "$dstparent/$name"
            return 0
        fi
        for child in "$src"/*; do
            { [ -e "$child" ] || [ -L "$child" ]; } || continue
            stage_entry "$child" "$dstparent/$name" "$rel/$(basename "$child")"
        done
        # Dirs emptied by exclusion are dropped; genuinely empty dirs ship.
        if [ ! -e "$dstparent/$name" ] && [ -z "$(ls -A "$src")" ]; then
            mkdir -p "$dstparent/$name"
        fi
    else
        is_excluded "$rel" && return 0
        mkdir -p "$dstparent"
        log_verbose "Copying file: $rel"
        cp -RPp "$src" "$dstparent/$name"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Dependency fetch.
# scripts/get_libs.sh runs in an empty temp dir; each top-level entry it leaves
# there replaces libs/<entry> in the project. The stamp file records the hash
# of scripts/get_libs.sh and the names it produced:
#   line 1  format marker
#   line 2  hash <git hash-object of scripts/get_libs.sh>   ('pending' mid-move)
#   line 3+ one fetched name per line
# The fetch is skipped while the hash matches and every name is still there.
# ---------------------------------------------------------------------------

# True when $1 is a line of the newline-separated list $2.
in_list() {
    grep -qxF -- "$1" <<< "$2"
}

stamp_names() {
    if [ -f "$LIBS_STAMP" ]; then
        sed -n '3,$p' "$LIBS_STAMP"
    fi
    return 0
}

# Names of the libs/ entries this build fetched, so it may replace them. The
# stamp is the record; when it is lost (a fresh clone with libs/ copied in, or
# .ngargparser/ deleted), the managed libs/.gitignore lists the same names. Its
# marker line shows the build wrote it, so hand-made entries stay protected.
owned_names() {
    if [ -f "$LIBS_STAMP" ]; then
        stamp_names
        return 0
    fi
    local gitignore="$LIBS_DIR/.gitignore"
    if [ -f "$gitignore" ] && [ "$(sed -n 1p "$gitignore")" = "$LIBS_GITIGNORE_MARKER" ]; then
        # Skip the marker and the self-ignore line, then undo gitignore_pattern:
        # drop the anchoring '/' and the glob escapes.
        sed -e '1d' -e '\|^/\.gitignore$|d' -e 's|^/||' -e 's/\\\(.\)/\1/g' "$gitignore"
    fi
    return 0
}

write_stamp() {
    {
        echo "$STAMP_FORMAT"
        echo "hash $1"
        if [ -n "$2" ]; then
            printf '%s\n' "$2"
        fi
    } > "$LIBS_STAMP.tmp"
    mv -f "$LIBS_STAMP.tmp" "$LIBS_STAMP"
}

# Explicit returns only: this runs as an `if` condition, where set -e is off.
libs_up_to_date() {
    [ -f "$LIBS_STAMP" ] || return 1
    [ "$(sed -n 1p "$LIBS_STAMP")" = "$STAMP_FORMAT" ] || return 1
    [ "$(sed -n 2p "$LIBS_STAMP")" = "hash $1" ] || return 1
    local name
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        { [ -e "$LIBS_DIR/$name" ] || [ -L "$LIBS_DIR/$name" ]; } || return 1
    done <<< "$(stamp_names)"
    return 0
}

# One anchored .gitignore pattern for a top-level libs/ entry, glob characters escaped.
gitignore_pattern() {
    printf '/%s\n' "$(printf '%s' "$1" | sed 's/[][\\*?]/\\&/g')"
}

# Keep fetched entries out of git without touching the project's own .gitignore:
# libs/ can also hold committed code, so only the fetched names are listed. The
# file ignores itself, like .pytest_cache/.gitignore and uv's .venv/.gitignore.
write_libs_gitignore() {
    local names="$1" gitignore="$LIBS_DIR/.gitignore" name
    if [ -f "$gitignore" ] && [ "$(sed -n 1p "$gitignore")" != "$LIBS_GITIGNORE_MARKER" ]; then
        # The project owns this file. Leave it, and only point out fetched
        # names git would still pick up.
        if git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            while IFS= read -r name; do
                [ -n "$name" ] || continue
                if ! git -C "$PROJECT_ROOT" check-ignore -q "libs/$name"; then
                    notice "Warning: libs/$name was fetched by scripts/get_libs.sh but git does not ignore it (libs/.gitignore is yours, so it was left alone)."
                fi
            done <<< "$names"
        fi
        return 0
    fi
    if [ -z "$names" ]; then
        rm -f "$gitignore"
        return 0
    fi
    {
        echo "$LIBS_GITIGNORE_MARKER"
        echo "/.gitignore"
        while IFS= read -r name; do
            if [ -n "$name" ]; then
                gitignore_pattern "$name"
            fi
        done <<< "$names"
    } > "$gitignore"
}

# Call at top level only (never as `fetch_libs || ...`), so set -e stays active.
fetch_libs() {
    if [ ! -f "$GET_LIBS_SCRIPT" ]; then
        show_progress "No scripts/get_libs.sh; nothing to fetch"
        log_verbose "No scripts/get_libs.sh; nothing to fetch."
        return 0
    fi

    local hash
    hash=$(git hash-object --no-filters "$GET_LIBS_SCRIPT")
    if libs_up_to_date "$hash"; then
        show_progress "Libs up to date"
        log_verbose "Libs up to date (scripts/get_libs.sh unchanged); skipping the fetch."
        return 0
    fi

    show_progress "Getting libs (log: .ngargparser/get_libs.log)"
    log_verbose "Getting libs with scripts/get_libs.sh..."
    mkdir -p "$STATE_DIR"
    local leftover
    for leftover in "$STATE_DIR"/libs-fetch.*; do  # from a build that was killed
        rm_tree "$leftover"
    done
    LIBS_TMP=$(mktemp -d "$STATE_DIR/libs-fetch.XXXXXX")
    export PROJECT_ROOT APP_NAME TOOL_NAME TOOL_VERSION SRC_DIR LIBS_DIR

    # stdin from /dev/null and no git prompts: a credential prompt fails the
    # fetch instead of hanging the build.
    local rc=0
    if [ "$PROGRESS_MODE" = true ]; then
        (cd "$LIBS_TMP" && GIT_TERMINAL_PROMPT=0 bash "$GET_LIBS_SCRIPT") </dev/null >"$GET_LIBS_LOG" 2>&1 || rc=$?
    else
        (cd "$LIBS_TMP" && GIT_TERMINAL_PROMPT=0 bash "$GET_LIBS_SCRIPT") </dev/null || rc=$?
    fi
    if [ "$rc" -ne 0 ]; then
        error "scripts/get_libs.sh failed (exit $rc); libs/ was not changed."
        if [ "$PROGRESS_MODE" = true ]; then
            echo "--- last lines of .ngargparser/get_libs.log ---" >&2
            tail -n 20 "$GET_LIBS_LOG" >&2
        fi
        exit 1
    fi

    local produced="" entry name
    for entry in "$LIBS_TMP"/*; do
        { [ -e "$entry" ] || [ -L "$entry" ]; } || continue
        name=${entry##*/}
        case "$name" in
            .gitignore|*$'\n'*)
                error "scripts/get_libs.sh may not create '$name' at the top level of its working directory."
                exit 1
                ;;
        esac
        produced="${produced:+$produced$NL}$name"
    done

    # Never replace something in libs/ that get_libs.sh does not own.
    local old
    old=$(owned_names)
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if { [ -e "$LIBS_DIR/$name" ] || [ -L "$LIBS_DIR/$name" ]; } && ! in_list "$name" "$old"; then
            if [ -n "$old" ] || [ -f "$LIBS_STAMP" ]; then
                error "libs/$name already exists and was not fetched by scripts/get_libs.sh. Remove or rename it, then build again."
            else
                # No record at all: the entry may be hand-made or an orphaned fetch.
                error "libs/$name already exists and was not fetched by scripts/get_libs.sh. If you made it yourself, rename it or stop get_libs.sh from creating it. If get_libs.sh fetched it in an earlier build (the record in .ngargparser/ is gone), delete libs/$name and build again."
            fi
            exit 1
        fi
    done <<< "$produced"

    # Project-folder runs need the same package layout the build gives libs/.
    find "$LIBS_TMP" -type d -exec chmod u+w {} +
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if [ -d "$LIBS_TMP/$name" ] && [ ! -L "$LIBS_TMP/$name" ]; then
            ensure_init_files "$LIBS_TMP/$name"
        fi
    done <<< "$produced"

    if [ -n "$produced" ]; then
        mkdir -p "$LIBS_DIR"
    fi
    # Record every name that may end up half-moved, so an interrupted run is
    # redone instead of trusted.
    write_stamp pending "$(printf '%s\n%s\n' "$old" "$produced" | sed '/^$/d' | sort -u)"
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        in_list "$name" "$produced" || rm_tree "$LIBS_DIR/$name"
    done <<< "$old"
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        rm_tree "$LIBS_DIR/$name"
        mv "$LIBS_TMP/$name" "$LIBS_DIR/$name"
    done <<< "$produced"
    write_libs_gitignore "$produced"
    write_stamp "$hash" "$produced"

    rm_tree "$LIBS_TMP"
    LIBS_TMP=""
    return 0
}

# Remove VCS metadata and bytecode caches from a staged libs/ entry. Real
# directories only: a symlink here may point outside the build.
strip_vcs_and_caches() {
    local dir="$1"
    rm -rf "$dir/.github" "$dir/.gitlab"
    find "$dir" \( -name .git -o -name __pycache__ \) -prune -exec rm -rf {} +
}

# Function to handle a single top-level item
handle_item() {
    local item="$1"
    local build_dir="$2"

    { [ -e "$item" ] || [ -L "$item" ]; } || return 0

    local item_name
    item_name=$(basename "$item")

    # Never distribute the build output dir or the git repo. These are also
    # pruned from the candidate list, so a '!' rule cannot re-include them.
    [[ "$item_name" == "build" || "$item_name" == ".git" ]] && return 0

    if [ "$item_name" = "libs" ] && [ -d "$item" ]; then
        # Merge project libs/* into build/libs (it already exists, so copying
        # the whole dir would nest build/libs/libs). Entries fetched by
        # scripts/get_libs.sh ship as get_libs.sh left them; everything else in libs/
        # follows .distignore like any project file.
        local fetched libentry name
        fetched=$(stamp_names)
        for libentry in "$item"/*; do
            { [ -e "$libentry" ] || [ -L "$libentry" ]; } || continue
            name=$(basename "$libentry")
            if in_list "$name" "$fetched"; then
                log_verbose "Copying fetched dependency: libs/$name"
                cp -RPp "$libentry" "$build_dir/libs/$name"
            else
                stage_entry "$libentry" "$build_dir/libs" "libs/$name"
            fi
        done
        return 0
    fi

    # .distignore check (exact .gitignore semantics)
    if [ -d "$item" ] && [ ! -L "$item" ]; then
        is_excluded "$item_name/" && return 0
    else
        is_excluded "$item_name" && return 0
    fi

    stage_entry "$item" "$build_dir" "$item_name"
    return 0
}

# Every symlink in the build must be relative and resolve to something inside
# the build: the tarball stores links as links, so anything else is broken once
# extracted on another host. Call at top level only.
check_links() {
    local root bad="" link target resolved rel
    root=$(cd "$BUILD_DIR" && pwd -P)
    while IFS= read -r -d '' link; do
        target=$(readlink "$link")
        rel=${link#"$BUILD_DIR"/}
        case "$target" in
            /*)
                bad+="  $rel -> $target (absolute)"$'\n'
                continue
                ;;
        esac
        if [ ! -e "$link" ]; then
            bad+="  $rel -> $target (dangling or a loop)"$'\n'
            continue
        fi
        if [ -d "$link" ]; then
            resolved=$(cd -P "$link" && pwd -P)
        else
            resolved=$(cd -P "$(dirname "$link")" && cd -P "$(dirname "$target")" && pwd -P)/$(basename "$target")
        fi
        case "$resolved/" in
            "$root"/*) ;;
            *) bad+="  $rel -> $target (points outside the build)"$'\n' ;;
        esac
    done < <(find "$BUILD_DIR" -type l -print0)

    if [ -n "$bad" ]; then
        error "Symlinks in the build must be relative and stay inside it. Fix these in the project, scripts/get_libs.sh or scripts/hooks.sh, or keep project paths out with .distignore:"
        printf '%s' "$bad" | head -n 20 >&2
        exit 1
    fi
    return 0
}

# The startup check: the entry script's help screens, run from the build dir
# with the project's .venv, the same commands `cli sync` uses to test-drive a
# project. APP_ROOT points at the build so its libs/ is the one imported (the
# env var wins over the project .env that python-dotenv would otherwise find).
# A small Python driver gives each command a timeout without needing GNU
# `timeout`, which macOS does not have.
STARTUP_DRIVER=$(cat <<'PY'
import os, subprocess, sys
py, script, root = sys.argv[1:4]
env = dict(os.environ, APP_ROOT=root, PYTHONDONTWRITEBYTECODE="1")
failed = 0
for args in (["--help"], ["predict", "--help"], ["preprocess", "--help"], ["postprocess", "--help"]):
    label = " ".join([script] + args)
    try:
        proc = subprocess.run([py, script] + args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=60, env=env)
    except subprocess.TimeoutExpired:
        print(label + ": timed out after 60s")
        failed += 1
        continue
    except OSError as exc:
        print(label + ": " + str(exc))
        failed += 1
        continue
    if proc.returncode != 0:
        lines = [line for line in proc.stderr.splitlines() if line.strip()]
        print(label + ": " + (lines[-1].strip() if lines else "exit code %d" % proc.returncode))
        failed += 1
sys.exit(1 if failed else 0)
PY
)

# Call at top level only.
check_startup() {
    local python="$PROJECT_ROOT/.venv/bin/python"
    local scripts=() script output rc=0
    for script in "$BUILD_DIR"/src/run_*.py; do
        if [ -f "$script" ]; then
            scripts+=("$script")
        fi
    done
    if [ ${#scripts[@]} -ne 1 ]; then
        notice "Startup check skipped: expected exactly one src/run_*.py in the build, found ${#scripts[@]}."
        return 0
    fi
    if [ ! -x "$python" ]; then
        notice "Startup check skipped: the project has no .venv/bin/python (run 'uv sync' to enable it)."
        return 0
    fi
    output=$(cd "$BUILD_DIR" && "$python" -I -c "$STARTUP_DRIVER" "$python" "src/$(basename "${scripts[0]}")" "$BUILD_DIR") || rc=$?
    if [ "$rc" -ne 0 ]; then
        error "The tool does not start from the build dir:"
        printf '%s\n' "$output" >&2
        exit 1
    fi
    log_verbose "Startup check passed."
    return 0
}

# ---------------------------------------------------------------------------
# Preflight: fail before anything is removed.
# Removed layouts stop the build instead of being ignored, so an old exclusion
# file or hook name never changes what ships without anyone noticing.
# ---------------------------------------------------------------------------
if [ ! -f "$PROJECT_ROOT/.distignore" ]; then
    for legacy in scripts/do-not-distribute.txt do-not-distribute.txt scripts/.distignore; do
        if [ -f "$PROJECT_ROOT/$legacy" ]; then
            error "$legacy is no longer read. Move it to .distignore at the project root (same syntax): git mv $legacy .distignore"
            exit 1
        fi
    done
fi
if [ ! -f "$SRC_DIR/hooks.sh" ]; then
    for legacy in build_hooks.sh dependencies.sh; do
        if [ -f "$SRC_DIR/$legacy" ]; then
            error "scripts/$legacy is no longer run. Rename it to scripts/hooks.sh (cli sync does this for you)."
            exit 1
        fi
    done
fi
if [ -f "$SRC_DIR/build.conf" ]; then
    error "scripts/build.conf is no longer read. Move it to scripts/core/build.conf (cli sync does this for you)."
    exit 1
fi
if [ -f "$SRC_DIR/deps.sh" ]; then
    error "scripts/deps.sh is no longer run. Rename it to scripts/get_libs.sh."
    exit 1
fi
if [ -L "$LIBS_DIR" ]; then
    error "libs/ is a symlink (-> $(readlink "$LIBS_DIR")). Replace it with a real directory: the build copies libs/, and fetched dependencies are written into it."
    exit 1
fi
if [ -e "$LIBS_DIR" ] && [ ! -d "$LIBS_DIR" ]; then
    error "libs exists but is not a directory."
    exit 1
fi
if [ -n "$EXCLUDE_FROM_BUILD_SYMLINK" ]; then
    notice "Note: EXCLUDE_FROM_BUILD_SYMLINK in scripts/core/build.conf no longer has any effect (the build copies everything); you can delete it."
fi

# Clean up temp state always, and the build directory on failure
TMPWORK=""
LIBS_TMP=""
TAR_TMP=""
on_exit() {
    local status=$?
    set +e
    [ -n "$TMPWORK" ] && rm -rf "$TMPWORK"
    [ -n "$LIBS_TMP" ] && rm_tree "$LIBS_TMP"
    [ -n "$TAR_TMP" ] && rm -f "$TAR_TMP"
    if [ $status -ne 0 ]; then
        echo "Build failed; removing $BUILD_DIR" >&2
        [ -n "$BUILD_DIR" ] && rm_tree "$BUILD_DIR"
        # Remove top-level build dir if empty
        if [ -d "$PROJECT_ROOT/build" ] && [ -z "$(ls -A "$PROJECT_ROOT/build")" ]; then
            rmdir "$PROJECT_ROOT/build"
        fi
    fi
    exit $status
}
trap on_exit EXIT

# Clean and recreate build directory. The previous tarball goes too, so a
# failed build never leaves an old one that looks fresh.
show_progress "Setting up build directory"
rm_tree "$BUILD_DIR"
rm -f "$PROJECT_ROOT/build/$TAR_NAME"
mkdir -p "$BUILD_DIR"

# Create libs directory (this will be a real directory, not a symlink)
mkdir -p "$BUILD_DIR/libs"

# Fetch dependencies into the project's libs/ (skipped when up to date)
fetch_libs

# Evaluate the .distignore exclusion file (exact .gitignore semantics) up front
show_progress "Evaluating distignore rules"
setup_exclusions

show_progress "Copying project files"
# Process all items in PROJECT_ROOT
for item in "$PROJECT_ROOT"/*; do
    handle_item "$item" "$BUILD_DIR"
done
# cp -p keeps read-only directories from dependencies; the build must be able
# to add __init__.py files now and delete the tree on the next build.
find "$BUILD_DIR" -type d -exec chmod u+w {} +
for libentry in "$BUILD_DIR"/libs/*; do
    if [ -d "$libentry" ] && [ ! -L "$libentry" ]; then
        strip_vcs_and_caches "$libentry"
        ensure_init_files "$libentry"
    fi
done
# Before the hooks run, so no hook can write through a link to outside the build.
check_links

show_progress "Updating version info"
# Use sed to replace the string with the environment variable
if [ -f "$BUILD_DIR/README" ]; then
    if [[ "$(uname)" == "Darwin" ]]; then
        # For MacOS
        sed -i "" "s/TOOL_VERSION/${TOOL_VERSION}/g" "$BUILD_DIR/README"
    else
        # For Linux
        sed -i "s/TOOL_VERSION/${TOOL_VERSION}/g" "$BUILD_DIR/README"
    fi
fi

# All dependencies should be in the libs directory
cd "$BUILD_DIR/libs"

show_progress "Running build hooks"
# Execute the project's build hook if present.
HOOK_SCRIPT=""
if [ -f "$SRC_DIR/hooks.sh" ]; then
    HOOK_SCRIPT="$SRC_DIR/hooks.sh"
fi

if [ -n "$HOOK_SCRIPT" ]; then
    log_verbose "Executing build hook: $HOOK_SCRIPT"

    # Set environment variables visible to the hook
    export SRC_DIR PROJECT_ROOT APP_NAME TOOL_NAME TOOL_VERSION TOOL_DIR BUILD_DIR

    # Run script (suppress output in progress mode)
    if [ "$PROGRESS_MODE" = true ]; then
        bash "$HOOK_SCRIPT" > /dev/null 2>&1
    else
        bash "$HOOK_SCRIPT"
    fi

    log_verbose "✓ Build hook completed"
fi

cd "$BUILD_DIR"

# Create version file
echo "${TOOL_VERSION}" > VERSION
date >> VERSION

# Remove macOS resource fork files
find . -type f -name '._*' -delete

# Ensure all directories in libs/ have __init__.py files
log_verbose "Ensuring __init__.py files exist in all libs directories..."
ensure_init_files "$BUILD_DIR/libs"
log_verbose "✓ All __init__.py files ensured"

show_progress "Checking symlinks"
check_links

# Before the tarball, so a tool that cannot start never leaves one behind.
show_progress "Checking the tool starts"
check_startup

# Create tarball in build directory. Symlinks are stored as links (no -h), and
# COPYFILE_DISABLE stops macOS tar from adding ._* members.
show_progress "Creating tarball"
cd "$PROJECT_ROOT/build"
TAR_TMP="$PROJECT_ROOT/build/.$TAR_NAME.partial"
COPYFILE_DISABLE=1 tar -czf "$TAR_TMP" "$TOOL_DIR"
mv -f "$TAR_TMP" "$TAR_NAME"
TAR_TMP=""

# The build dir is output: fixes made here would be lost on the next build.
# Read-only files make an editor refuse the save instead. Directories stay
# writable, so `make clean`, the next build, and new files from ./configure work.
show_progress "Making build files read-only"
find "$BUILD_DIR" -type f -exec chmod a-w {} +

if [ "$PROGRESS_MODE" = true ]; then
    echo ""
    echo "Build completed: build/$TAR_NAME"
else
    echo "Build completed!"
fi
