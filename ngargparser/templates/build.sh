#!/bin/bash
#
# -----------------------------------------------------------------------------
# This file may only be modified by the admin responsible for the IEDB build system.
# General contributors must not edit this file.
#
# Build script for the IEDB project
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
TOTAL_STEPS=8
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
# This script lives in scripts/core/ (framework-owned, sync-managed).
# build.conf and hooks.sh live one level up in scripts/ (user-owned). SRC_DIR
# keeps its established meaning: the user-owned scripts/ dir, which is what the
# build hook sees via the export below. The exclusion file is .distignore at
# PROJECT_ROOT (legacy: scripts/do-not-distribute.txt) — see setup_exclusions.
BUILD_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$BUILD_SH_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$SRC_DIR/.." && pwd)"
APP_NAME=$(basename "$PROJECT_ROOT")

# git is a hard dependency: the .distignore exclusion file is evaluated with
# `git check-ignore` (and git deps in requirements.txt are vendored via clone).
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
EXCLUDE_FROM_BUILD_SYMLINK=""
TARBALL_PREFIX=""
if [ -f "$SRC_DIR/build.conf" ]; then
    # shellcheck source=build.conf
    source "$SRC_DIR/build.conf"
fi

# Apply defaults when not set by build.conf
[ -z "$TOOL_NAME" ] && TOOL_NAME="ng_${APP_NAME}"
[ -z "$EXCLUDE_FROM_BUILD_SYMLINK" ] && EXCLUDE_FROM_BUILD_SYMLINK="libs run_*.py"
# pull the tool version from the environment, otherwise set it to 'local'
TOOL_VERSION="${TOOL_VERSION:-local}"
# Replace '/' with '-': CI often passes the git ref name as TOOL_VERSION, and a
# branch like 'feature/foo' would otherwise break the version-stamping sed
# (s/TOOL_VERSION/.../ — the slash ends the command) and nest TOOL_DIR/BUILD_DIR.
TOOL_VERSION="${TOOL_VERSION//\//-}"
TOOL_DIR=$TOOL_NAME-$TOOL_VERSION
BUILD_DIR=$PROJECT_ROOT/build/$TOOL_DIR

[ -z "$TARBALL_PREFIX" ] && TARBALL_PREFIX="IEDB_"

# Returns 0 if $1 matches any glob pattern in $EXCLUDE_FROM_BUILD_SYMLINK (space-separated).
# Used by handle_src_dir and handle_item *) to decide copy-vs-symlink for each file/dir.
should_copy_not_symlink() {
    local item="$1"
    for pattern in $EXCLUDE_FROM_BUILD_SYMLINK; do
        # shellcheck disable=SC2053  # intentional glob match (RHS is a pattern, not a string)
        if [[ "$item" == $pattern ]]; then
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# distignore engine.
# The exclusion file (.distignore at the project root; the legacy name
# scripts/do-not-distribute.txt is still accepted) has EXACT .gitignore
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
# list, so no rule (not even '!') can bring them back.
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

    # Resolve the exclusion file: prefer the new '.distignore' (root, then
    # scripts/) over the legacy 'do-not-distribute.txt'. First match wins.
    local distignore=""
    local cand
    for cand in "$PROJECT_ROOT/.distignore" "$SRC_DIR/.distignore" \
                "$SRC_DIR/do-not-distribute.txt" "$PROJECT_ROOT/do-not-distribute.txt"; do
        if [ -f "$cand" ]; then
            distignore="$cand"
            break
        fi
    done

    {
        printf '.*\n'
        if [ -n "$distignore" ]; then
            sed 's/\r$//' "$distignore"
        fi
        printf '!README\n!deploy/\n!deploy/install.sh\n'
    } > "$IGNORE_REPO/.gitignore"

    # Candidate paths: everything in the project, PROJECT_ROOT-relative,
    # directories suffixed with '/' (required for dir-only patterns to match
    # paths that do not exist inside the throwaway repo). Nested .git dirs are
    # listed but not descended into, so the '.*' baseline excludes them whole.
    (
        cd "$PROJECT_ROOT" && {
            find . -mindepth 1 \( -path ./build -o -path ./.git \) -prune -o \
                -type d -name .git -prune -print -o -type d -print \
                | sed -e 's|^\./||' -e 's|$|/|'
            find . -mindepth 1 \( -path ./build -o -path ./.git \) -prune -o \
                -type d -name .git -prune -o ! -type d -print \
                | sed 's|^\./||'
        }
    ) > "$candidates"

    # Batch evaluation. Exit 0 = some paths ignored, 1 = none ignored (not an
    # error), >1 = fatal.
    local status=0
    git -C "$IGNORE_REPO" -c core.quotePath=off -c core.excludesFile=/dev/null \
        check-ignore --stdin < "$candidates" > "$EXCLUDED_LIST" || status=$?
    if [ "$status" -gt 1 ]; then
        echo "ERROR: git check-ignore failed (exit $status) while evaluating distignore rules" >&2
        exit 1
    fi
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

# Stage one entry into the build tree, enforcing exclusions at every depth.
#   $1 src (absolute)   $2 dstparent (absolute)
#   $3 rel (PROJECT_ROOT-relative, no trailing '/')
#   $4 mode: symlink|copy
#   $5 fastpath: yes|no — 'yes' allows a whole-dir symlink/copy when nothing
#      inside the subtree is excluded; 'no' forces per-file staging (used for
#      src/, where hooks patch files under build/src/ and a whole-dir symlink
#      would leak those writes back into the source tree).
stage_entry() {
    local src="$1" dstparent="$2" rel="$3" mode="$4" fastpath="${5:-yes}"
    local name
    name=$(basename "$src")

    if [ -d "$src" ] && [ ! -L "$src" ]; then
        is_excluded "$rel/" && return 0
        if [ "$fastpath" = yes ] && ! subtree_has_exclusions "$rel"; then
            mkdir -p "$dstparent"
            if [ "$mode" = copy ]; then
                log_verbose "Copying directory: $rel"
                cp -r "$src" "$dstparent/$name"
            else
                log_verbose "Symlinking directory: $rel"
                ln -sf "$src" "$dstparent/$name"
            fi
            return 0
        fi
        local child
        for child in "$src"/*; do
            { [ -e "$child" ] || [ -L "$child" ]; } || continue
            stage_entry "$child" "$dstparent/$name" "$rel/$(basename "$child")" "$mode" "$fastpath"
        done
        # Dirs emptied by exclusion are dropped; genuinely empty dirs ship.
        if [ ! -e "$dstparent/$name" ] && [ -z "$(ls -A "$src")" ]; then
            mkdir -p "$dstparent/$name"
        fi
    else
        is_excluded "$rel" && return 0
        mkdir -p "$dstparent"
        if [ "$mode" = copy ]; then
            log_verbose "Copying file: $rel"
            cp "$src" "$dstparent/$name"
        else
            log_verbose "Symlinking file: $rel"
            ln -sf "$src" "$dstparent/$name"
        fi
    fi
}

# Ensure we clean up temp state always, and the build directory on failure
TMPWORK=""
trap 'status=$?; \
  [ -n "$TMPWORK" ] && rm -rf "$TMPWORK"; \
  if [ $status -ne 0 ]; then \
  echo "Build failed; removing $BUILD_DIR"; \
  [ -n "$BUILD_DIR" ] && rm -rf "$BUILD_DIR"; \
  # Remove top-level build dir if empty
  if [ -d "$PROJECT_ROOT/build" ] && [ -z "$(ls -A "$PROJECT_ROOT/build")" ]; then \
    rmdir "$PROJECT_ROOT/build"; \
  fi; \
fi; exit $status' EXIT

# Clean and recreate build directory
show_progress "Setting up build directory"
rm -rf $BUILD_DIR
mkdir -p $BUILD_DIR

# Create libs directory (this will be a real directory, not a symlink)
mkdir -p $BUILD_DIR/libs

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

# Process requirements.txt if it exists
show_progress "Processing requirements.txt"
if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
    log_verbose "Processing requirements.txt..."

    # Check if there are any git repositories in requirements.txt
    has_git_repos=false
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip empty lines and comments
        if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
            continue
        fi
        # Check if line contains a Git repository
        if [[ "$line" =~ ^git\+ || "$line" =~ github\.com || "$line" =~ gitlab\.com || "$line" =~ gitlab\. ]]; then
            has_git_repos=true
            break
        fi
    done < "$PROJECT_ROOT/requirements.txt"

    if [ "$has_git_repos" = true ]; then
        log_verbose "Git repositories detected, creating filtered requirements.txt..."
        
        # Create filtered requirements.txt for build directory (Python packages only)
        > "$BUILD_DIR/requirements.txt"

        # Process each line in requirements.txt
        while IFS= read -r line || [ -n "$line" ]; do
            # Skip empty lines and comments
            if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
                continue
            fi
            # Check if line contains a Git repository
            if [[ "$line" =~ ^git\+ || "$line" =~ ^git[[:space:]]+clone || "$line" =~ github\.com || "$line" =~ gitlab\.com || "$line" =~ gitlab\. ]]; then
                log_verbose "Installing Git repository: $line"
                
                # Parse repository name and clone
                repo_name=""
                # Case 1: pip-style VCS URL (git+https://...@branch)
                if [[ "$line" =~ ^git\+ ]]; then
                    # Parse git+ URL format
                    base_url=$(echo "$line" | sed 's/^git+//' | sed 's/@[^@]*#.*$//' | sed 's/#.*$//')
                    branch=""
                    if [[ "$line" =~ @ ]]; then
                        branch=$(echo "$line" | sed -n 's/.*@\([^#]*\).*/\1/p')
                    fi
                    repo_name=$(echo "$base_url" | sed 's/.*\///' | sed 's/\.git.*//')
                    
                    cd "$BUILD_DIR/libs"
                    if [[ -n "$branch" ]]; then
                        git clone -b "$branch" --single-branch --depth 1 "$base_url" "$repo_name" 2>/dev/null && rm -rf "$repo_name/.git"
                    else
                        git clone --single-branch --depth 1 "$base_url" "$repo_name" 2>/dev/null && rm -rf "$repo_name/.git"
                    fi
                    
                    # Ensure __init__.py files exist in the cloned repository
                    ensure_init_files "$BUILD_DIR/libs/$repo_name"
                    
                    cd "$BUILD_DIR"
                # Case 2: shell-style 'git clone ... URL' line
                elif [[ "$line" =~ ^git[[:space:]]+clone ]]; then
                    # Extract URL (last http/https or git@ token)
                    url=$(echo "$line" | grep -Eo '(https?://[^ ]+|git@[^ ]+)' | tail -n1)
                    # Extract branch by tokenizing and taking the arg after -b
                    branch=""
                    read -r -a parts <<< "$line"
                    for i in "${!parts[@]}"; do
                        if [[ "${parts[$i]}" == "-b" && $((i+1)) -lt ${#parts[@]} ]]; then
                            branch="${parts[$((i+1))]}"
                            # Strip single quotes if present
                            branch="${branch%\'}"
                            branch="${branch#\'}"
                        fi
                    done
                    repo_name=$(echo "$url" | sed 's/.*\///' | sed 's/\.git.*//')
                    cd "$BUILD_DIR/libs"
                    if [[ -n "$branch" ]]; then
                        if git clone -b "$branch" --single-branch --depth 1 "$url" "$repo_name"; then
                            rm -rf "$repo_name/.git"
                        else
                            echo "ERROR: git clone failed for $url (branch: $branch)" >&2
                        fi
                    else
                        if git clone --single-branch --depth 1 "$url" "$repo_name"; then
                            rm -rf "$repo_name/.git"
                        else
                            echo "ERROR: git clone failed for $url" >&2
                        fi
                    fi
                    ensure_init_files "$repo_name"
                    cd "$BUILD_DIR"
                else
                    # Handle regular GitHub/GitLab URLs
                    if [[ "$line" =~ github\.com ]]; then
                        repo_name=$(echo "$line" | sed -n 's/.*github\.com\/[^\/]*\/\([^\/@]*\).*/\1/p')
                    elif [[ "$line" =~ gitlab\.com ]]; then
                        repo_name=$(echo "$line" | sed -n 's/.*gitlab\.com\/[^\/]*\/\([^\/@]*\).*/\1/p')
                    elif [[ "$line" =~ gitlab\. ]]; then
                        repo_name=$(echo "$line" | sed -n 's/.*gitlab\.[^\/]*\/[^\/]*\/\([^\/@]*\).*/\1/p')
                    else
                        repo_name=$(echo "$line" | sed 's/.*\///' | sed 's/\.git.*//' | sed 's/@.*//' | sed 's/#.*//')
                    fi
                    
                    if [[ -z "$repo_name" ]]; then
                        repo_name="repo_$(date +%s)"
                    fi
                    
                    cd "$BUILD_DIR/libs"
                    git clone "$line" "$repo_name" 2>/dev/null && rm -rf "$repo_name/.git"
                    
                    # Ensure __init__.py files exist in the cloned repository
                    ensure_init_files "$BUILD_DIR/libs/$repo_name"
                    
                    cd "$BUILD_DIR"
                fi
            else
                # This is a Python package, add to filtered requirements.txt
                echo "$line" >> "$BUILD_DIR/requirements.txt"
            fi
        done < "$PROJECT_ROOT/requirements.txt"

        log_verbose "✓ Processed requirements.txt with Git repository filtering"
    else
        log_verbose "No Git repositories detected, symlinking requirements.txt..."
        # No git repos found, just symlink the original file (will be handled in the main loop)
    fi
fi

# Evaluate the .distignore exclusion file (exact .gitignore semantics) up front
show_progress "Evaluating distignore rules"
setup_exclusions

# Copy only the libs directory and create symlinks for everything else
show_progress "Copying source files"

# Function to handle src directory
handle_src_dir() {
    local src_dir="$1"
    local build_src_dir="$2"
    mkdir -p "$build_src_dir"

    for src_file in "$src_dir"/*; do
        { [ -e "$src_file" ] || [ -L "$src_file" ]; } || continue
        local src_file_name
        src_file_name=$(basename "$src_file")
        local mode=symlink
        should_copy_not_symlink "$src_file_name" && mode=copy
        stage_entry "$src_file" "$build_src_dir" "src/$src_file_name" "$mode" no
    done
}

# Function to handle a single item
handle_item() {
    local item="$1"
    local build_dir="$2"

    { [ -e "$item" ] || [ -L "$item" ]; } || return 0

    local item_name
    item_name=$(basename "$item")

    # Never distribute the build output dir or the git repo. These are also
    # pruned from the candidate list, so a '!' rule cannot re-include them.
    [[ "$item_name" == "build" || "$item_name" == ".git" ]] && return 0

    # .distignore check (exact .gitignore semantics)
    if [ -d "$item" ] && [ ! -L "$item" ]; then
        is_excluded "$item_name/" && return 0
    else
        is_excluded "$item_name" && return 0
    fi

    case "$item_name" in
        "src")
            handle_src_dir "$item" "$build_dir/src"
            ;;
        "libs")
            # Merge project-level libs/* into build/libs (flattened) to avoid build/libs/libs
            log_verbose "Merging project libs/* into $build_dir/libs"
            mkdir -p "$build_dir/libs"
            for libentry in "$item"/*; do
                { [ -e "$libentry" ] || [ -L "$libentry" ]; } || continue
                name=$(basename "$libentry")
                stage_entry "$libentry" "$build_dir/libs" "libs/$name" copy
                if [ -d "$build_dir/libs/$name" ]; then
                    # remove VCS metadata if present (the '.*' baseline already
                    # filters these on the per-file path; this covers fast-path copies)
                    rm -rf "$build_dir/libs/$name/.git" "$build_dir/libs/$name/.github" "$build_dir/libs/$name/.gitlab"
                    ensure_init_files "$build_dir/libs/$name"
                fi
            done
            ;;
        "scripts")
            for scripts_file in "$item"/*; do
                { [ -e "$scripts_file" ] || [ -L "$scripts_file" ]; } || continue
                stage_entry "$scripts_file" "$build_dir/scripts" "scripts/$(basename "$scripts_file")" symlink
            done
            ;;
        "requirements.txt")
            # Only symlink if not already processed as a filtered file (with git deps stripped out).
            if [ ! -f "$build_dir/requirements.txt" ]; then
                ln -sf "$item" "$build_dir/$item_name"
            fi
            ;;
        *)
            # Default: symlink. To copy a top-level item instead, list it (or a glob)
            # in EXCLUDE_FROM_BUILD_SYMLINK in scripts/build.conf.
            local mode=symlink
            should_copy_not_symlink "$item_name" && mode=copy
            stage_entry "$item" "$build_dir" "$item_name" "$mode" yes
            ;;
    esac
}

show_progress "Processing project files"
# Process all items in PROJECT_ROOT
for item in "$PROJECT_ROOT"/*; do
    handle_item "$item" "$BUILD_DIR"
done

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
cd $BUILD_DIR/libs

show_progress "Running build hooks"
# Execute the project's build hook if present. Legacy projects may still use
# the older filenames; we accept those too so builds keep working until migration.
HOOK_SCRIPT=""
if [ -f "$SRC_DIR/hooks.sh" ]; then
    HOOK_SCRIPT="$SRC_DIR/hooks.sh"
elif [ -f "$SRC_DIR/build_hooks.sh" ]; then
    HOOK_SCRIPT="$SRC_DIR/build_hooks.sh"
    log_verbose "⚠  Using legacy 'build_hooks.sh'; rename to 'hooks.sh' (or run 'cli sync' to do it for you)."
elif [ -f "$SRC_DIR/dependencies.sh" ]; then
    HOOK_SCRIPT="$SRC_DIR/dependencies.sh"
    log_verbose "⚠  Using legacy 'dependencies.sh'; rename to 'hooks.sh' (or run 'cli sync' to do it for you)."
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

cd $BUILD_DIR

# Create version file
echo ${TOOL_VERSION} > VERSION
date >> VERSION

# Remove macOS resource fork files
find . -type f -name '._*' -delete

# Ensure all directories in libs/ have __init__.py files
log_verbose "Ensuring __init__.py files exist in all libs directories..."
ensure_init_files "$BUILD_DIR/libs"
log_verbose "✓ All __init__.py files ensured"

# Create tarball in build directory
show_progress "Creating tarball"
cd $PROJECT_ROOT/build
if [ -n "$TARBALL_PREFIX" ]; then
    TAR_NAME="${TARBALL_PREFIX}$(echo $TOOL_NAME | tr '[:lower:]' '[:upper:]')-${TOOL_VERSION}.tar.gz"
else
    TAR_NAME="${TOOL_NAME}-${TOOL_VERSION}.tar.gz"
fi
tar -chzf "$TAR_NAME" $TOOL_DIR

if [ "$PROGRESS_MODE" = true ]; then
    echo ""
    echo "Build completed: build/$TAR_NAME"
else
    echo "Build completed!"
fi