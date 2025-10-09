#!/bin/bash

# Build script for the application

# Ensure this script always runs with bash, regardless of how it's invoked
if [ -z "$BASH_VERSION" ]; then
    # Re-execute this script with bash if not already running with bash
    exec bash "$0" "$@"
fi

set -ex
set -o pipefail

# Get the app name from the project root directory name
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SRC_DIR/.." && pwd)"
APP_NAME=$(basename "$PROJECT_ROOT")
TOOL_NAME=ng_${APP_NAME}
# pull the tool version from the environment, otherwise set it to 'local'
TOOL_VERSION="${TOOL_VERSION:-local}"
TOOL_DIR=$TOOL_NAME-$TOOL_VERSION
BUILD_DIR=$PROJECT_ROOT/build/$TOOL_DIR

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
rm -rf $BUILD_DIR
mkdir -p $BUILD_DIR

# Create libs directory (this will be a real directory, not a symlink)
mkdir -p $BUILD_DIR/libs

# Function to ensure __init__.py files exist in directories
ensure_init_files() {
    local dir_name="$1"
    local full_path="$BUILD_DIR/libs/$dir_name"
    
    if [ -d "$full_path" ]; then
        echo "Ensuring __init__.py files exist in $dir_name..."
        
        # Find all subdirectories and create __init__.py files if they don't exist
        find "$full_path" -type d | while read -r subdir; do
            if [ ! -f "$subdir/__init__.py" ]; then
                echo "  Creating __init__.py in: $subdir"
                echo "# Auto-generated __init__.py file" > "$subdir/__init__.py"
            fi
        done
        
        echo "✓ __init__.py files ensured in $dir_name"
    fi
}

# Process requirements.txt if it exists
if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
    echo "Processing requirements.txt..."

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
        echo "Git repositories detected, creating filtered requirements.txt..."
        
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
                echo "Installing Git repository: $line"
                
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
                    ensure_init_files "$repo_name"
                    
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
                    ensure_init_files "$repo_name"
                    
                    cd "$BUILD_DIR"
                fi
            else
                # This is a Python package, add to filtered requirements.txt
                echo "$line" >> "$BUILD_DIR/requirements.txt"
            fi
        done < "$PROJECT_ROOT/requirements.txt"

        echo "✓ Processed requirements.txt with Git repository filtering"
    else
        echo "No Git repositories detected, symlinking requirements.txt..."
        # No git repos found, just symlink the original file (will be handled in the main loop)
    fi
fi

# Copy only the libs directory and create symlinks for everything else
# Function to handle src directory
handle_src_dir() {
    local src_dir="$1"
    local build_src_dir="$2"
    mkdir -p "$build_src_dir"
    for src_file in "$src_dir"/*; do
        if [ -e "$src_file" ]; then
            src_file_name=$(basename "$src_file")
            if [ "$src_file_name" = "core" ]; then
                cp -r "$src_file" "$build_src_dir/$src_file_name"
            elif [[ "$src_file_name" == run_*.py ]]; then
                # Ensure run_*.py are copied, not symlinked
                cp "$src_file" "$build_src_dir/$src_file_name"
            else
                ln -sf "$src_file" "$build_src_dir/$src_file_name"
            fi
        fi
    done
}

# Function to handle scripts directory
handle_scripts_dir() {
    local scripts_dir="$1"
    local build_scripts_dir="$2"
    mkdir -p "$build_scripts_dir"
    for scripts_file in "$scripts_dir"/*; do
        if [ -e "$scripts_file" ]; then
            scripts_file_name=$(basename "$scripts_file")
            ln -sf "$scripts_file" "$build_scripts_dir/$scripts_file_name"
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
            echo "Merging project libs/* into $build_dir/libs"
            mkdir -p "$build_dir/libs"
            for libentry in "$item"/*; do
                [ -e "$libentry" ] || continue
                name=$(basename "$libentry")
                if [ -d "$libentry" ]; then
                    cp -r "$libentry" "$build_dir/libs/$name"
                    # remove VCS metadata if present
                    rm -rf "$build_dir/libs/$name/.git" "$build_dir/libs/$name/.github" "$build_dir/libs/$name/.gitlab"
                    ensure_init_files "$name"
                else
                    cp "$libentry" "$build_dir/libs/$name"
                fi
            done
            ;;
        "scripts")
            handle_scripts_dir "$item" "$build_dir/scripts"
            ;;
        "configure")
            cp "$item" "$build_dir/$item_name"
            ;;
        "license-LJI.txt")
            cp "$item" "$build_dir/$item_name"
            ;;
        "README"|"README.md")
            cp "$item" "$build_dir/$item_name"
            ;;
        "requirements.txt")
            # Only symlink if not already processed as filtered file
            if [ ! -f "$build_dir/requirements.txt" ]; then
                ln -sf "$item" "$build_dir/$item_name"
            fi
            ;;
        *)
            ln -sf "$item" "$build_dir/$item_name"
            ;;
    esac
}

# Process all items in PROJECT_ROOT
for item in "$PROJECT_ROOT"/*; do
    if [ -f "$SRC_DIR/do-not-distribute.txt" ]; then
        handle_item "$item" "$BUILD_DIR" "$SRC_DIR/do-not-distribute.txt"
    else
        handle_item "$item" "$BUILD_DIR" ""
    fi
done







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

# =====================================================================
# CALLING CUSTOM DEPENDENCIES.SH
# =====================================================================
# Check if a custom dependencies script exists and execute it
if [ -f "$SRC_DIR/dependencies.sh" ]; then
    echo "Executing custom dependencies script: $SRC_DIR/dependencies.sh"
    if [ -x "$SRC_DIR/dependencies.sh" ]; then
        # Script is executable, run it directly with all relevant variables as environment variables
        SRC_DIR="$SRC_DIR" PROJECT_ROOT="$PROJECT_ROOT" APP_NAME="$APP_NAME" TOOL_NAME="$TOOL_NAME" TOOL_VERSION="$TOOL_VERSION" TOOL_DIR="$TOOL_DIR" BUILD_DIR="$BUILD_DIR" "$SRC_DIR/dependencies.sh"
    else
        # Script is not executable, run it with bash and all relevant variables as environment variables
        SRC_DIR="$SRC_DIR" PROJECT_ROOT="$PROJECT_ROOT" APP_NAME="$APP_NAME" TOOL_NAME="$TOOL_NAME" TOOL_VERSION="$TOOL_VERSION" TOOL_DIR="$TOOL_DIR" BUILD_DIR="$BUILD_DIR" bash "$SRC_DIR/dependencies.sh"
    fi
    echo "✓ Custom dependencies script completed"
else
    echo "No custom dependencies script found. Add dependency commands above or create scripts/dependencies.sh"
fi

# Check for any new dependency directories and ensure they have __init__.py files
# Only process directories if they exist
# Only process directories if libs/ is not empty and contains directories
if [ "$(find . -mindepth 1 -maxdepth 1 -type d)" ]; then
    for dir in */; do
        if [ -d "$dir" ]; then
            # Get directory name without trailing slash
            dir_name=${dir%/}
            ensure_init_files "$dir_name"
        fi
    done
else
    echo "No directories found in libs/ to process"
fi


cd $BUILD_DIR

# create a version file and add the version and date
echo ${TOOL_VERSION} > VERSION
date >> VERSION

# remove all ._ files
find . -type f -name '._*' -delete

# Ensure all directories in libs/ have __init__.py files
echo "Ensuring __init__.py files exist in all libs directories..."
find "$BUILD_DIR/libs" -type d | while read -r subdir; do
    if [ ! -f "$subdir/__init__.py" ]; then
        echo "  Creating __init__.py in: $subdir"
        echo "# Auto-generated __init__.py file" > "$subdir/__init__.py"
    fi
done
echo "✓ All __init__.py files ensured"

# Create tarball in build directory
cd $PROJECT_ROOT/build
TAR_NAME="IEDB_$(echo $TOOL_NAME | tr '[:lower:]' '[:upper:]')-${TOOL_VERSION}.tar.gz"
tar -chzf $TAR_NAME $TOOL_DIR

echo "Build completed!"