#!/bin/bash

# Build hook for ngargparser apps.
#
# This script is invoked by scripts/core/build.sh after the project has been
# copied into the build dir but before the tarball is created. It works on that
# copy only: nothing it changes reaches the project. It is a user-owned
# extension point in the build pipeline; `cli sync` never touches it.
#
# Downloads belong in scripts/deps.sh, not here: deps.sh fetches into the
# project's libs/ once, so the project-folder run sees the same dependencies,
# and the build does not download them again every time.
#
# Working directory: build/<TOOL>/libs/ — anything you create lands in libs/.
# Available exports: SRC_DIR, PROJECT_ROOT, APP_NAME, TOOL_NAME, TOOL_VERSION,
#                    TOOL_DIR, BUILD_DIR.
#
# Common uses:
#   - Patch copied files under $BUILD_DIR (sed -i, rm)
#   - Copy local binaries/data into the build dir
#   - Run code generators
#   - Strip files, run validators, generate version stamps
#
# Symlinks you create must be relative and stay inside the build; the build
# fails on any other link. Host-specific paths belong in deploy/install.sh.

set -e  # Exit on any error

echo "Running build hooks..."

# =====================================================================
# ADD YOUR BUILD-HOOK COMMANDS BELOW
# ---------------------------------------------------------------------
# Replace the examples below with your actual commands.
# =====================================================================

# Example 1: Patch a copied file (the project's own file is not touched)
# sed -i 's|GIT_SOURCE|libs/my-package|' "$BUILD_DIR/pyproject.toml"

# Example 2: Copy local dependency files
# echo "Copying local dependencies..."
# cp -r /path/to/local/dependency .

# Example 3: Remove files that only the build should drop
# rm -f "$BUILD_DIR/src/Dockerfile.dev"

# Example 4: Run a setup script
# echo "Running setup..."
# bash /path/to/setup-script.sh

# =====================================================================
# END OF BUILD HOOKS
# =====================================================================

echo "✓ Build hooks completed"
