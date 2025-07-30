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

# Process requirements.txt if it exists
if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
    echo "Processing requirements.txt..."

    # Remove any symlink to requirements.txt in build dir before writing
    if [ -L "$BUILD_DIR/requirements.txt" ]; then
        rm -f "$BUILD_DIR/requirements.txt"
    fi
    # Create filtered requirements.txt for build directory (Python packages only)
    > "$BUILD_DIR/requirements.txt"

    # Process each line in requirements.txt
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip empty lines and comments
        if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
            continue
        fi
        # Check if line contains a Git repository (starts with git+ or contains github.com/gitlab.com)
        if [[ "$line" =~ ^git\+ || "$line" =~ github\.com || "$line" =~ gitlab\.com || "$line" =~ gitlab\. ]]; then
            echo "Installing Git repository: $line"
            
            # Parse the git+ URL to extract components
            repo_url=""
            branch=""
            repo_name=""
            
            if [[ "$line" =~ ^git\+ ]]; then
                # Parse git+ URL format: git+https://.../repo.git@branch#egg=package==version
                # Extract the base URL by removing git+ prefix and everything after @ or #
                # Use a more robust approach to handle complex URLs
                base_url=$(echo "$line" | sed 's/^git+//' | sed 's/@[^@]*#.*$//' | sed 's/#.*$//')
                
                # Extract branch/tag if present (between @ and #)
                if [[ "$line" =~ @ ]]; then
                    branch=$(echo "$line" | sed -n 's/.*@\([^#]*\).*/\1/p')
                fi
                
                # Extract repository name from the end of the URL
                repo_name=$(echo "$base_url" | sed 's/.*\///' | sed 's/\.git.*//')
                
                # Clean up the repo name (remove any remaining fragments)
                repo_name=$(echo "$repo_name" | sed 's/#.*//' | sed 's/@.*//')
                
                echo "Parsed URL: $base_url"
                echo "Branch/Tag: $branch"
                echo "Repository name: $repo_name"
                
                # Clone the repository with proper git clone command
                cd "$BUILD_DIR/libs"
                if [[ -n "$branch" ]]; then
                    # Clone with specific branch/tag
                    if git clone -b "$branch" --single-branch --depth 1 "$base_url" "$repo_name" 2>/dev/null; then
                        echo "✓ Successfully cloned $repo_name (branch: $branch)"
                        # Remove .git directory to save space
                        rm -rf "$repo_name/.git"
                    else
                        echo "✗ Failed to clone: $base_url (branch: $branch)"
                        echo "  This might be due to authentication or network issues"
                    fi
                else
                    # Clone without specific branch
                    if git clone --single-branch --depth 1 "$base_url" "$repo_name" 2>/dev/null; then
                        echo "✓ Successfully cloned $repo_name"
                        # Remove .git directory to save space
                        rm -rf "$repo_name/.git"
                    else
                        echo "✗ Failed to clone: $base_url"
                        echo "  This might be due to authentication or network issues"
                    fi
                fi
                cd "$BUILD_DIR"
            else
                # Handle regular GitHub/GitLab URLs (non-git+ format)
                repo_name=""
                if [[ "$line" =~ github\.com ]]; then
                    # Extract from github.com/owner/repo format
                    repo_name=$(echo "$line" | sed -n 's/.*github\.com\/[^\/]*\/\([^\/@]*\).*/\1/p')
                elif [[ "$line" =~ gitlab\.com ]]; then
                    # Extract from gitlab.com/owner/repo format
                    repo_name=$(echo "$line" | sed -n 's/.*gitlab\.com\/[^\/]*\/\([^\/@]*\).*/\1/p')
                elif [[ "$line" =~ gitlab\. ]]; then
                    # Extract from gitlab.organization.com/owner/repo format
                    repo_name=$(echo "$line" | sed -n 's/.*gitlab\.[^\/]*\/[^\/]*\/\([^\/@]*\).*/\1/p')
                else
                    # For other git URLs, try to extract repo name from the end
                    repo_name=$(echo "$line" | sed 's/.*\///' | sed 's/\.git.*//' | sed 's/@.*//' | sed 's/#.*//')
                fi
                
                # If we couldn't extract a name, use a default
                if [[ -z "$repo_name" ]]; then
                    repo_name="repo_$(date +%s)"
                fi
                
                # Clean up the repo name (remove any remaining fragments)
                repo_name=$(echo "$repo_name" | sed 's/#.*//' | sed 's/@.*//')
                
                echo "Extracted repository name: $repo_name"
                
                # Clone the repository into libs directory
                cd "$BUILD_DIR/libs"
                if git clone "$line" "$repo_name" 2>/dev/null; then
                    echo "✓ Successfully cloned $repo_name"
                    # Remove .git directory to save space
                    rm -rf "$repo_name/.git"
                else
                    echo "✗ Failed to clone: $line"
                    echo "  This might be due to authentication or network issues"
                fi
                cd "$BUILD_DIR"
            fi
        else
            # This is a Python package, add to filtered requirements.txt
            echo "$line" >> "$BUILD_DIR/requirements.txt"
        fi
    done < "$PROJECT_ROOT/requirements.txt"

    echo "✓ Processed requirements.txt"
    echo "  - Git repositories installed in libs/"
    echo "  - Python packages saved to build/requirements.txt"
    echo "DEBUG: requirements.txt file type after processing: $(stat -f '%HT%TT' "$BUILD_DIR/requirements.txt" 2>/dev/null || stat -c '%F' "$BUILD_DIR/requirements.txt")"
    ls -l "$BUILD_DIR/requirements.txt"
    cat "$BUILD_DIR/requirements.txt"
fi

# Copy only the libs directory and create symlinks for everything else
# In the symlinking loop, skip symlinking requirements.txt if a real file exists in build dir
if [ -f "$SRC_DIR/do-not-distribute.txt" ]; then
    for item in $PROJECT_ROOT/*; do
        if [ -e "$item" ]; then
            item_name=$(basename "$item")
            if [ "$item_name" != "build" ] && [ "$item_name" != "README" ] && [ "$item_name" != "README.md" ]; then
                if ! grep -q "^$item_name$" "$SRC_DIR/do-not-distribute.txt" 2>/dev/null; then
                    if [ "$item_name" = "src" ]; then
                        mkdir -p "$BUILD_DIR/src"
                        for src_file in "$item"/*; do
                            if [ -e "$src_file" ]; then
                                src_file_name=$(basename "$src_file")
                                if [ "$src_file_name" = "core" ]; then
                                    cp -r "$src_file" "$BUILD_DIR/src/$src_file_name"
                                else
                                    ln -sf "$src_file" "$BUILD_DIR/src/$src_file_name"
                                fi
                            fi
                        done
                    elif [ "$item_name" = "requirements.txt" ] && [ -f "$BUILD_DIR/requirements.txt" ] && [ ! -L "$BUILD_DIR/requirements.txt" ]; then
                        echo "Skipping requirements.txt symlink (filtered file already exists)"
                    else
                        ln -sf "$item" "$BUILD_DIR/$item_name"
                    fi
                fi
            fi
        fi
    done
else
    for item in $PROJECT_ROOT/*; do
        if [ -e "$item" ]; then
            item_name=$(basename "$item")
            if [ "$item_name" != "build" ] && [ "$item_name" != "README" ] && [ "$item_name" != "README.md" ]; then
                if [ "$item_name" = "src" ]; then
                    mkdir -p "$BUILD_DIR/src"
                    for src_file in "$item"/*; do
                        if [ -e "$src_file" ]; then
                            src_file_name=$(basename "$src_file")
                            if [ "$src_file_name" = "core" ]; then
                                cp -r "$src_file" "$BUILD_DIR/src/$src_file_name"
                            else
                                ln -sf "$src_file" "$BUILD_DIR/src/$src_file_name"
                            fi
                        fi
                    done
                elif [ "$item_name" = "requirements.txt" ] && [ -f "$BUILD_DIR/requirements.txt" ] && [ ! -L "$BUILD_DIR/requirements.txt" ]; then
                    echo "Skipping requirements.txt symlink (filtered file already exists)"
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

# Failsafe: Ensure requirements.txt is a regular file with filtered content
if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
    echo "Failsafe: Ensuring requirements.txt is a regular file with filtered content"
    if [ -L "$BUILD_DIR/requirements.txt" ]; then
        echo "Removing symlink to requirements.txt"
        rm -f "$BUILD_DIR/requirements.txt"
    fi
    if [ ! -f "$BUILD_DIR/requirements.txt" ]; then
        echo "Recreating filtered requirements.txt"
        > "$BUILD_DIR/requirements.txt"
        while IFS= read -r line || [ -n "$line" ]; do
            if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
                continue
            fi
            if [[ ! "$line" =~ ^git\+ && ! "$line" =~ github\.com && ! "$line" =~ gitlab\.com && ! "$line" =~ gitlab\. ]]; then
                echo "$line" >> "$BUILD_DIR/requirements.txt"
            fi
        done < "$PROJECT_ROOT/requirements.txt"
    fi
    echo "Final requirements.txt status: $(ls -l "$BUILD_DIR/requirements.txt")"
    echo "Final requirements.txt content:"
    cat "$BUILD_DIR/requirements.txt"
else
    echo "DEBUG: Failsafe not running, requirements.txt not found"
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