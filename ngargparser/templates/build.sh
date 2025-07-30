#!/bin/bash

# Build script for the application

# Ensure this script always runs with bash, regardless of how it's invoked
if [ -z "$BASH_VERSION" ]; then
    # Re-execute this script with bash if not already running with bash
    exec bash "$0" "$@"
fi

set -ex

TOOL_NAME=ng_bcell
# pull the tool version from the environment, otherwise set it to 'local'
TOOL_VERSION="${TOOL_VERSION:-local}"
TOOL_DIR=$TOOL_NAME-$TOOL_VERSION
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SRC_DIR/.." && pwd)"
BUILD_DIR=$PROJECT_ROOT/build/$TOOL_DIR

# Clean and recreate build directory
rm -rf $BUILD_DIR
mkdir -p $BUILD_DIR

# Create libs directory (this will be a real directory, not a symlink)
mkdir -p $BUILD_DIR/libs



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
# ADD YOUR DEPENDENCIES BELOW
# ---------------------------------------------------------------------
# This is where you should add commands to download/install dependencies
# that should be included in the libs/ directory.
#
# Examples:
#   - git clone https://github.com/IEDB/nxg-tools.git
#   - wget https://example.com/allele-validator.tar.gz && tar xf allele-validator.tar.gz
# =====================================================================


cd $BUILD_DIR

# create a version file and add the version and date
echo ${TOOL_VERSION} > VERSION
date >> VERSION

# remove all ._ files
find . -type f -name '._*' -delete

# Create tarball in build directory
cd $PROJECT_ROOT/build
TAR_NAME="IEDB_$(echo $TOOL_NAME | tr '[:lower:]' '[:upper:]')-${TOOL_VERSION}.tar.gz"
tar -chzf $TAR_NAME $TOOL_DIR

echo "Build completed!"