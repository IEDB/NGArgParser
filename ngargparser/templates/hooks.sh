#!/bin/bash

# Build hook for ngargparser apps.
#
# This script is invoked by scripts/core/build.sh after the source tree has been
# copied/symlinked into the build dir but before the tarball is created. It is
# the single user-owned extension point in the build pipeline; `cli sync` never
# touches it.
#
# Working directory: build/<TOOL>/libs/ — anything you create lands in libs/.
# Available exports: SRC_DIR, PROJECT_ROOT, APP_NAME, TOOL_NAME, TOOL_VERSION,
#                    TOOL_DIR, BUILD_DIR.
#
# Common uses:
#   - Vendor external tools (git clone, wget + tar, curl)
#   - Copy local binaries/data into the build dir
#   - Run code generators or patch source files
#   - Install Python packages with `uv pip install --target .`
#   - Strip files, run validators, generate version stamps

set -e  # Exit on any error

echo "Running build hooks..."

# =====================================================================
# ADD YOUR BUILD-HOOK COMMANDS BELOW
# ---------------------------------------------------------------------
# Replace the examples below with your actual commands.
# =====================================================================

# Example 1: Clone a Git repository
# echo "Cloning nxg-tools..."
# git clone https://github.com/IEDB/nxg-tools.git
# rm -rf nxg-tools/.git  # Remove git history to reduce size

# Example 2: Download and extract an archive
# echo "Downloading allele-validator..."
# wget https://example.com/allele-validator.tar.gz
# tar xf allele-validator.tar.gz
# rm allele-validator.tar.gz  # Clean up downloaded file

# Example 3: Copy local dependency files
# echo "Copying local dependencies..."
# cp -r /path/to/local/dependency .

# Example 4: Install Python package from source (uv preferred)
# echo "Installing custom Python package..."
# uv pip install --target . git+https://github.com/user/package.git

# Example 5: Run a setup script
# echo "Running setup..."
# bash /path/to/setup-script.sh

# =====================================================================
# END OF BUILD HOOKS
# =====================================================================

echo "✓ Build hooks completed"
