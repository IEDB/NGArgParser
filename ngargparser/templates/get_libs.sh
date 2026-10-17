#!/bin/bash

# Dependency fetch for ngargparser apps.
#
# Put all outside code your tool needs at run time here: git clones, archives,
# data files, and third-party tools you download by hand (licensed software).
# This script is USER-OWNED; `cli sync` never touches it.
#
# When it runs: at the start of `make build`, before the project is copied into
# build/. It is skipped while this file is unchanged and everything it fetched
# last time is still in libs/. To force a fresh fetch, delete the folders this
# script fetched into libs/ (listed in libs/.gitignore) and build again.
# .ngargparser/libs-stamp records which libs/ folders the build owns; if it is
# lost, the build reads the same list from libs/.gitignore. If both are gone,
# delete the fetched folders in libs/ and build again.
#
# Working directory: an empty temp dir. When the script succeeds, each
# top-level entry it leaves there replaces libs/<entry> in the project, gets
# __init__.py files, and is listed in libs/.gitignore. Both the project-folder
# run and the build then see the same libs/. If the script fails, libs/ is left
# as it was and the build stops.
#
# Rules:
#   - Fail loudly. `set -euo pipefail` below stops on the first error; do not
#     swallow failures, because a fetch that "succeeds" with something missing
#     is remembered as done.
#   - Delete what you do not want shipped (downloaded archives, .git dirs, test
#     data). Fetched entries ship as you leave them: .distignore does not filter
#     them. The build only strips .git, .github, .gitlab and __pycache__.
#   - Symlinks must be relative and stay inside the entry; the build fails on
#     any other link.
#   - Never write a token into this file. In CI, read it from a CI/CD variable
#     such as TOOLS_DEPLOY_TOKEN.
#
# Available exports: PROJECT_ROOT, APP_NAME, TOOL_NAME, TOOL_VERSION, SRC_DIR,
#                    LIBS_DIR (read-only for this script; the build moves the
#                    results into it).

set -euo pipefail

# =====================================================================
# ADD YOUR DEPENDENCY DOWNLOADS BELOW
# ---------------------------------------------------------------------
# Replace the examples below with your actual commands.
# =====================================================================

# Example 1: Clone pinned GitLab repositories. Uses the CI token when it is
# set, your SSH key otherwise, and fails in CI when the token is missing.
# GITLAB_HOST="gitlab.lji.org"
# vendor() {
#     local tag="$1" path="$2"
#     local name
#     name=$(basename "$path")
#     if [ -n "${TOOLS_DEPLOY_TOKEN:-}" ]; then
#         git clone -b "$tag" --single-branch --depth 1 \
#             "https://oauth2:${TOOLS_DEPLOY_TOKEN}@${GITLAB_HOST}/${path}.git"
#     elif [ -n "${CI:-}" ]; then
#         echo "TOOLS_DEPLOY_TOKEN is not set; cannot clone ${path} in CI" >&2
#         exit 1
#     else
#         GIT_SSH_COMMAND="ssh -o BatchMode=yes" git clone -b "$tag" --single-branch --depth 1 \
#             "ssh://git@${GITLAB_HOST}/${path}.git"
#     fi
#     rm -rf "$name/.git"
# }
# vendor 'v1.1.3' "iedb/tools/tools-redesign/global-dependencies/nxg-tools"

# Example 2: Download and unpack an archive
# curl -fsSL -o tool.tar.gz https://example.com/tool-1.0.tar.gz
# tar xzf tool.tar.gz
# rm tool.tar.gz

# Example 3: A tool you download by hand (e.g. behind a license form). Each
# developer points an environment variable at their unpacked copy. CI has no
# copy, so CI builds go without it. A local build stops instead of skipping:
# a skipped fetch is remembered as done, and setting the variable later would
# be ignored until this file changes.
# if [ -n "${MYTOOL_DIR:-}" ]; then
#     if [ ! -f "$MYTOOL_DIR/mytool.py" ]; then
#         echo "ERROR: MYTOOL_DIR=$MYTOOL_DIR has no mytool.py." >&2
#         exit 1
#     fi
#     cp -R "$MYTOOL_DIR" mytool
# elif [ -n "${CI:-}" ]; then
#     echo "MYTOOL_DIR is not set in CI; building without mytool."
# else
#     echo "ERROR: download mytool from <url> and set MYTOOL_DIR to its folder." >&2
#     exit 1
# fi

# =====================================================================
# END OF DEPENDENCY DOWNLOADS
# =====================================================================
