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

# Get the app name from the project root directory name
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SRC_DIR/.." && pwd)"
APP_NAME=$(basename "$PROJECT_ROOT")
TOOL_NAME=ng_${APP_NAME}
# pull the tool version from the environment, otherwise set it to 'local'
TOOL_VERSION="${TOOL_VERSION:-local}"
TOOL_DIR=$TOOL_NAME-$TOOL_VERSION
BUILD_DIR=$PROJECT_ROOT/build/$TOOL_DIR

# Load build configuration (optional). Defaults make the script generic for any project.
BUILD_ENTRY_SCRIPT=""
BUILD_SYMLINK_SRC_DIRS=""
BUILD_COPY_TOPLEVEL_FILES=""
TARBALL_PREFIX=""
if [ -f "$SRC_DIR/build.conf" ]; then
    # shellcheck source=build.conf
    source "$SRC_DIR/build.conf"
fi

# Apply defaults when not set by build.conf
if [ -z "$BUILD_ENTRY_SCRIPT" ] && [ -d "$PROJECT_ROOT/src" ]; then
    run_scripts=( "$PROJECT_ROOT/src"/run_*.py )
    if [ -e "${run_scripts[0]}" ] && [ ${#run_scripts[@]} -eq 1 ]; then
        BUILD_ENTRY_SCRIPT=$(basename "${run_scripts[0]}")
    fi
fi
[ -z "$BUILD_COPY_TOPLEVEL_FILES" ] && BUILD_COPY_TOPLEVEL_FILES="configure license-LJI.txt README"
[ -z "$TARBALL_PREFIX" ] && TARBALL_PREFIX="IEDB_"

# Ensure we clean up the build directory on failure
trap 'status=$?; if [ $status -ne 0 ]; then \
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

# Copy only the libs directory and create symlinks for everything else
show_progress "Copying source files"

# Function to symlink directory contents recursively
symlink_directory_contents() {
    local src_dir="$1"
    local build_dir="$2"
    mkdir -p "$build_dir"
    
    for item in "$src_dir"/*; do
        [ -e "$item" ] || continue
        local item_name=$(basename "$item")
        local abs_item=$(cd "$(dirname "$item")" && pwd)/$(basename "$item")
        
        if [ -d "$item" ]; then
            symlink_directory_contents "$item" "$build_dir/$item_name"
        else
            log_verbose "Symlinking: $item_name"
            ln -sf "$abs_item" "$build_dir/$item_name"
        fi
    done
}

# Function to check if a value is in a space-separated list
is_in_list() {
    local item="$1"
    local list="$2"
    for x in $list; do
        [ "$x" = "$item" ] && return 0
    done
    return 1
}

# Function to handle src directory
handle_src_dir() {
    local src_dir="$1"
    local build_src_dir="$2"
    mkdir -p "$build_src_dir"
    
    for src_file in "$src_dir"/*; do
        [ -e "$src_file" ] || continue
        
        local src_file_name=$(basename "$src_file")
        local abs_src_file=$(cd "$(dirname "$src_file")" && pwd)/$(basename "$src_file")
        
        if [ -d "$src_file" ]; then
            # Symlink directory contents if in BUILD_SYMLINK_SRC_DIRS; otherwise copy
            if is_in_list "$src_file_name" "$BUILD_SYMLINK_SRC_DIRS"; then
                log_verbose "Symlinking directory contents: $src_file_name"
                symlink_directory_contents "$src_file" "$build_src_dir/$src_file_name"
            else
                log_verbose "Copying directory: $src_file_name"
                cp -r "$src_file" "$build_src_dir/$src_file_name"
            fi
        elif is_in_list "$src_file_name" "$BUILD_ENTRY_SCRIPT"; then
            log_verbose "Copying entry script: $src_file_name"
            cp "$src_file" "$build_src_dir/$src_file_name"
        else
            log_verbose "Symlinking file: $src_file_name"
            ln -sf "$abs_src_file" "$build_src_dir/$src_file_name"
        fi
    done
}

# Function to handle a single item
handle_item() {
    local item="$1"
    local build_dir="$2"
    local do_not_distribute="$3"

    [ ! -e "$item" ] && return
    
    item_name=$(basename "$item")
    
    # Skip build directory
    [[ "$item_name" == "build" ]] && return
    
    # Check do-not-distribute.txt if it exists
    if [ -n "$do_not_distribute" ] && grep -q "^$item_name$" "$do_not_distribute" 2>/dev/null; then
        return
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
                [ -e "$libentry" ] || continue
                name=$(basename "$libentry")
                if [ -d "$libentry" ]; then
                    cp -r "$libentry" "$build_dir/libs/$name"
                    # remove VCS metadata if present
                    rm -rf "$build_dir/libs/$name/.git" "$build_dir/libs/$name/.github" "$build_dir/libs/$name/.gitlab"
                    ensure_init_files "$build_dir/libs/$name"
                else
                    cp "$libentry" "$build_dir/libs/$name"
                fi
            done
            ;;
        "scripts")
            mkdir -p "$build_dir/scripts"
            for scripts_file in "$item"/*; do
                [ -e "$scripts_file" ] && ln -sf "$scripts_file" "$build_dir/scripts/$(basename "$scripts_file")"
            done
            ;;
        "requirements.txt")
            # Only symlink if not already processed as filtered file
            if [ ! -f "$build_dir/requirements.txt" ]; then
                ln -sf "$item" "$build_dir/$item_name"
            fi
            ;;
        *)
            if is_in_list "$item_name" "$BUILD_COPY_TOPLEVEL_FILES"; then
                cp "$item" "$build_dir/$item_name"
            else
                ln -sf "$item" "$build_dir/$item_name"
            fi
            ;;
    esac
}

show_progress "Processing project files"
# Process all items in PROJECT_ROOT
for item in "$PROJECT_ROOT"/*; do
    if [ -f "$SRC_DIR/do-not-distribute.txt" ]; then
        handle_item "$item" "$BUILD_DIR" "$SRC_DIR/do-not-distribute.txt"
    else
        handle_item "$item" "$BUILD_DIR" ""
    fi
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

show_progress "Installing dependencies"
# Execute custom dependencies script if it exists
if [ -f "$SRC_DIR/dependencies.sh" ]; then
    log_verbose "Executing custom dependencies script: $SRC_DIR/dependencies.sh"
    
    # Set environment variables for dependencies script
    export SRC_DIR PROJECT_ROOT APP_NAME TOOL_NAME TOOL_VERSION TOOL_DIR BUILD_DIR
    
    # Run script (suppress output in progress mode)
    if [ "$PROGRESS_MODE" = true ]; then
        bash "$SRC_DIR/dependencies.sh" > /dev/null 2>&1
    else
        bash "$SRC_DIR/dependencies.sh"
    fi
    
    log_verbose "✓ Custom dependencies script completed"
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