#!/bin/bash

# Custom Dependencies Script for ngargparser apps
# 
# This script is executed by build.sh during the build process.
# You can customize this script to download, install, or configure
# any dependencies your application needs.
#
# The script runs from the libs/ directory in the build folder.
# All dependencies should be placed in the current directory (libs/).
#
# Examples:
#   - Clone Git repositories
#   - Download and extract archives
#   - Install Python packages
#   - Copy local dependency files
#   - Run setup/installation scripts

set -e  # Exit on any error

echo "Installing custom dependencies..."

# =====================================================================
# ADD YOUR DEPENDENCY COMMANDS BELOW
# ---------------------------------------------------------------------
# Replace the examples below with your actual dependency commands
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

# Example 4: Install Python package from source
# echo "Installing custom Python package..."
# pip install --target . git+https://github.com/user/package.git

# Example 5: Run a setup script
# echo "Running dependency setup..."
# bash /path/to/setup-script.sh

# =====================================================================
# END OF DEPENDENCIES
# =====================================================================

echo "✓ Custom dependencies installation completed"
