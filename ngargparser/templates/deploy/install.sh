#!/bin/bash
# Install script for {TOOL_NAME}.
#
# Run by nxg-tools-deployments after extracting the tarball on the target host.
# CWD when invoked: $IEDB_VERSION_DIR (the extracted tarball directory).
# The orchestrator sources the target's install_shell_init snippet in the same
# shell first, so UV_PYTHON_INSTALL_DIR / UV_CACHE_DIR / UV_PYTHON_PREFERENCE
# / `module load` etc. are already set when this script runs.
#
# DO NOT hardcode UV_PYTHON_INSTALL_DIR, UV_CACHE_DIR, or UV_PYTHON_PREFERENCE
# here — those come from the target's install_shell_init. Hardcoding locks
# cluster paths into every release tarball and breaks dev-laptop installs.
#
# Env vars available (most install scripts ignore these):
#   IEDB_STANDALONE_NAME       e.g. {TOOL_NAME}
#   IEDB_VERSION               e.g. 0.1.0
#   IEDB_VERSION_DIR           extracted tarball dir (also CWD)
#   IEDB_STANDALONE_DIR        target's install_dir root (peer tools live here)
#   IEDB_PREVIOUS_VERSION_DIR  previously-deployed version dir, or empty
#
# Idempotent — safe to re-run.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

if ! command -v uv >/dev/null 2>&1; then
    echo "[install.sh] uv not found; installing via the official installer"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "[install.sh] uv:   $(uv --version)"
echo "[install.sh] venv: $PROJECT_ROOT/.venv"

uv sync

# Activate the venv so ./configure (shebangless wrapper around src/core/configure.py)
# finds the right interpreter via PATH.
# shellcheck disable=SC1091
source .venv/bin/activate

if [ -x ./configure ]; then
    echo "[install.sh] running ./configure"
    ./configure
fi

echo "[install.sh] done"
