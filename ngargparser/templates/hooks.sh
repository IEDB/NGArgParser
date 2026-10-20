#!/bin/bash

# Build hook for ngargparser apps (optional).
#
# Most tools do not need this file: `cli generate` does not create it, and the
# build skips this step when it is missing. Create it only to change files in
# the packaged copy. Leaving files out of the tarball belongs in .distignore;
# outside code (downloads, tools you download by hand) belongs in
# scripts/get_libs.sh. To start one, copy this template to scripts/hooks.sh.
#
# This script is invoked by scripts/core/build.sh after the tool folder has been
# copied into the build dir but before the tarball is created. It works on that
# copy only: nothing it changes reaches the tool folder. It is a user-owned
# extension point in the build pipeline; `cli sync` never touches it.
#
# Downloads belong in scripts/get_libs.sh, not here: get_libs.sh fetches into the
# tool folder's libs/ once, so a run in the tool folder sees the same dependencies,
# and the build does not download them again every time.
#
# Working directory: build/<TOOL>/libs/ — anything you create lands in libs/.
# Available exports: SRC_DIR, PROJECT_ROOT, APP_NAME, TOOL_NAME, TOOL_VERSION,
#                    TOOL_DIR, BUILD_DIR.
#
# Common uses:
#   - Patch copied files under $BUILD_DIR (sed -i)
#   - Run code generators
#   - Run validators, generate version stamps
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

# Example 1: Patch a copied file (the tool folder's own file is not touched)
# sed -i 's|GIT_SOURCE|libs/my-package|' "$BUILD_DIR/pyproject.toml"

# Example 2: Remove files that only the build should drop
# rm -f "$BUILD_DIR/src/Dockerfile.dev"

# Example 3: Run a setup script
# echo "Running setup..."
# bash /path/to/setup-script.sh

# =====================================================================
# END OF BUILD HOOKS
# =====================================================================

echo "✓ Build hooks completed"
