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

# Copy project files from project root, excluding build directory and do-not-distribute files
if [ -f "$PROJECT_ROOT/do-not-distribute.txt" ]; then
    rsync --cvs-exclude --exclude build --exclude-from="$PROJECT_ROOT/do-not-distribute.txt" -a --delete $PROJECT_ROOT/ $BUILD_DIR/
else
    rsync --cvs-exclude --exclude build -a --delete $PROJECT_ROOT/ $BUILD_DIR/
fi

# Create libs directory (this will be a real directory, not a symlink)
mkdir -p $BUILD_DIR/libs

# Remove all files and directories that were copied, except libs/
# This will be replaced with symbolic links
find $BUILD_DIR -mindepth 1 -maxdepth 1 ! -name 'libs' -exec rm -rf {} +

# Create symbolic links for all files from project root directory
# This ensures everything can be synced back to source except VERSION, libs/, and README files
for item in $PROJECT_ROOT/*; do
    if [ -e "$item" ]; then
        item_name=$(basename "$item")
        # Skip if it's the build directory, README files, or do-not-distribute files
        if [ "$item_name" != "build" ] && [ "$item_name" != "README" ] && [ "$item_name" != "README.md" ]; then
            if [ -f "$PROJECT_ROOT/do-not-distribute.txt" ]; then
                if ! grep -q "^$item_name$" "$PROJECT_ROOT/do-not-distribute.txt" 2>/dev/null; then
                    ln -sf "$item" "$BUILD_DIR/$item_name"
                fi
            else
                ln -sf "$item" "$BUILD_DIR/$item_name"
            fi
        fi
    fi
done

# Copy README files as real files (not symlinks)
if [ -f "$PROJECT_ROOT/README" ]; then
    cp "$PROJECT_ROOT/README" "$BUILD_DIR/README"
fi
if [ -f "$PROJECT_ROOT/README.md" ]; then
    cp "$PROJECT_ROOT/README.md" "$BUILD_DIR/README.md"
fi

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