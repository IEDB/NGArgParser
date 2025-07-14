#!/bin/bash

# Ensure this script always runs with bash, regardless of how it's invoked
if [ -z "$BASH_VERSION" ]; then
    # Re-execute this script with bash if not already running with bash
    exec bash "$0" "$@"
fi

#TODO: update the set flags appropriately
set -ex

TOOL_NAME=ng_appname
# pull the tool version from the environment, otherwise set it to 'local'
TOOL_VERSION="${TOOL_VERSION:-local}"
TOOL_DIR=$TOOL_NAME-$TOOL_VERSION
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR=$SRC_DIR/build/$TOOL_DIR

mkdir -p $BUILD_DIR

rsync --cvs-exclude --exclude build --exclude-from='do-not-distribute.txt' -a --delete $SRC_DIR/ $BUILD_DIR/

mkdir -p $BUILD_DIR/libs

# Use sed to replace the string with the environment variable
if [[ "$(uname)" == "Darwin" ]]; then
    # For MacOS
    sed -i "" "s/TOOL_VERSION/${TOOL_VERSION}/g" "$BUILD_DIR/README"
else
    # For Linux
    sed -i "s/TOOL_VERSION/${TOOL_VERSION}/g" "$BUILD_DIR/README"
fi

# All dependencies should be in the libs directory
cd $BUILD_DIR/libs

# --- ADD YOUR DEPENDENCIES HERE (e.g. nxg-tools, allele-validator, etc.)




cd $BUILD_DIR

# create a version file and add the version and date
echo ${TOOL_VERSION} > VERSION
date >> VERSION

# remove all ._ files
find . -type f -name '._*' -delete

cd $BUILD_DIR/..

tar -czf IEDB_${TOOL_NAME^^}-${TOOL_VERSION}.tar.gz $TOOL_DIR
