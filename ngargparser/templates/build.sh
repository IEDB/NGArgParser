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
if [ -f "$SRC_DIR/do-not-distribute.txt" ]; then
    # Copy only files that are not in do-not-distribute.txt
    for item in $PROJECT_ROOT/*; do
        if [ -e "$item" ]; then
            item_name=$(basename "$item")
            if [ "$item_name" != "build" ] && [ "$item_name" != "README" ] && [ "$item_name" != "README.md" ]; then
                if ! grep -q "^$item_name$" "$SRC_DIR/do-not-distribute.txt" 2>/dev/null; then
                    if [ "$item_name" = "src" ]; then
                        # Handle src directory specially - create individual symlinks for each file
                        mkdir -p "$BUILD_DIR/src"
                        for src_file in "$item"/*; do
                            if [ -e "$src_file" ]; then
                                src_file_name=$(basename "$src_file")
                                if [ "$src_file_name" = "core" ]; then
                                    # Copy core directory as real files (not symlinked)
                                    cp -r "$src_file" "$BUILD_DIR/src/$src_file_name"
                                else
                                    ln -sf "$src_file" "$BUILD_DIR/src/$src_file_name"
                                fi
                            fi
                        done
                    else
                        ln -sf "$item" "$BUILD_DIR/$item_name"
                    fi
                fi
            fi
        fi
    done
else
    # Since do-not-distribute.txt is not present, create symlinks for all files and directories
    # from the project root into the build directory, except for 'build', 'README' and 'README.md'.
    # The 'src' directory gets special handling - its contents are symlinked individually.
    for item in $PROJECT_ROOT/*; do
        if [ -e "$item" ]; then
            item_name=$(basename "$item")
            if [ "$item_name" != "build" ] && [ "$item_name" != "README" ] && [ "$item_name" != "README.md" ]; then
                if [ "$item_name" = "src" ]; then
                    # Handle src directory specially - create individual symlinks for each file
                    mkdir -p "$BUILD_DIR/src"
                    for src_file in "$item"/*; do
                        if [ -e "$src_file" ]; then
                            src_file_name=$(basename "$src_file")
                            if [ "$src_file_name" = "core" ]; then
                                # Copy core directory as real files (not symlinked)
                                cp -r "$src_file" "$BUILD_DIR/src/$src_file_name"
                            else
                                ln -sf "$src_file" "$BUILD_DIR/src/$src_file_name"
                            fi
                        fi
                    done
                else
                    ln -sf "$item" "$BUILD_DIR/$item_name"
                fi
            fi
        fi
    done
fi



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