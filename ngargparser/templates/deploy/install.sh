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
# here: those come from the target's install_shell_init. Hardcoding locks
# cluster paths into every release tarball and breaks dev-laptop installs.
#
# Env vars available (most install scripts ignore these):
#   IEDB_STANDALONE_NAME       e.g. {TOOL_NAME}
#   IEDB_VERSION               e.g. 0.1.0
#   IEDB_VERSION_DIR           extracted tarball dir (also CWD)
#   IEDB_STANDALONE_DIR        target's install_dir root (peer tools live here)
#   IEDB_PREVIOUS_VERSION_DIR  previously-deployed version dir, or empty
#
# Idempotent: safe to re-run.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

# uv settings from the environment that would change what the uv commands
# below do, the ones make build unsets for its own (keep the two lists the
# same): UV_LOCKED and UV_FROZEN pick a lock mode (UV_LOCKED with --frozen is
# an error), UV_PROJECT and UV_WORKING_DIR point uv at another folder, and
# UV_PROJECT_ENVIRONMENT makes the virtualenv somewhere other than .venv, which
# the activate line below needs. UV_RESOLUTION, UV_PRERELEASE, UV_FORK_STRATEGY,
# UV_EXCLUDE_NEWER and UV_NO_SOURCES change how uv resolves. Index, cache,
# Python and offline settings stay: the target's install_shell_init may set them.
unset UV_LOCKED UV_FROZEN UV_PROJECT UV_WORKING_DIR UV_PROJECT_ENVIRONMENT
unset UV_RESOLUTION UV_PRERELEASE UV_FORK_STRATEGY UV_EXCLUDE_NEWER UV_NO_SOURCES

# The uv version CI tests with (UV_VERSION in .gitlab-ci.yml), not the latest
# release: bump it here, and in the installer URL below, whenever you bump that pin.
UV_PIN=0.11.14

# True when $1, what `uv --version` printed, is a version older than UV_PIN.
# Text that names no version (or none at all) counts as not older: that uv is kept.
uv_older_than_pin() {
    local have want i
    [[ ${1:-} =~ ^uv\ ([0-9]+(\.[0-9]+)*) ]] || return 1
    IFS=. read -r -a have <<<"${BASH_REMATCH[1]}"
    IFS=. read -r -a want <<<"$UV_PIN"
    for i in 0 1 2; do
        if [ "$((10#${have[i]:-0}))" -ne "$((10#${want[i]:-0}))" ]; then
            [ "$((10#${have[i]:-0}))" -lt "$((10#${want[i]:-0}))" ]
            return
        fi
    done
    return 1
}

# The pinned uv, from the official installer. UV_INSTALL_DIR puts it in
# ~/.local/bin, the folder put first on PATH here, even where XDG_BIN_HOME or
# XDG_DATA_HOME would send it elsewhere. UV_NO_MODIFY_PATH=1 leaves the deploy
# account's shell start-up files (.profile, .bashrc, .zshrc, fish) alone.
install_pinned_uv() {
    curl -LsSf https://astral.sh/uv/0.11.14/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
    export PATH="$HOME/.local/bin:$PATH"
}

# A uv an earlier deploy installed (below) is in ~/.local/bin, which the deploy
# shell may not have on PATH: use it rather than download uv again.
if ! command -v uv >/dev/null 2>&1 && [ -f "$HOME/.local/bin/uv" ] && [ -x "$HOME/.local/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# A uv older than UV_PIN (a pin bump since it was installed) is replaced for
# this script by the one in ~/.local/bin when that one is not older, else by
# the pinned one; a newer one is kept.
uv_said=''
if ! command -v uv >/dev/null 2>&1; then
    echo "[install.sh] uv not found; installing via the official installer"
    install_pinned_uv
else
    uv_said=$(uv --version 2>/dev/null) || uv_said=''
    if uv_older_than_pin "$uv_said"; then
        local_said=''
        if [ -f "$HOME/.local/bin/uv" ] && [ -x "$HOME/.local/bin/uv" ]; then
            local_said=$("$HOME/.local/bin/uv" --version 2>/dev/null) || local_said=''
        fi
        if [ -n "$local_said" ] && ! uv_older_than_pin "$local_said"; then
            echo "[install.sh] $uv_said is older than uv $UV_PIN; using $HOME/.local/bin/uv"
            export PATH="$HOME/.local/bin:$PATH"
            uv_said=$local_said
        else
            echo "[install.sh] $uv_said is older than uv $UV_PIN; installing uv $UV_PIN via the official installer"
            install_pinned_uv
            uv_said=''
        fi
    fi
fi
if [ -z "$uv_said" ]; then
    uv_said=$(uv --version) || uv_said=''
fi

echo "[install.sh] uv:   $uv_said"
echo "[install.sh] venv: $PROJECT_ROOT/.venv"

# --frozen installs exactly what uv.lock pins and never rewrites it on the host.
# --no-dev leaves out the dev group (pytest and other tools for developers),
# which a plain `uv sync` installs.
if [ -f uv.lock ]; then
    uv sync --frozen --no-dev
else
    uv sync --no-dev
fi

# ./configure runs src/core/configure.py with .venv/bin/python on its own; the
# venv is activated so any command added after it here uses the tool's Python too.
# shellcheck disable=SC1091
source .venv/bin/activate

# Run with sh, so a configure that lost its exec bit (an archive or a copy that
# dropped it) still runs instead of leaving the tool unconfigured. A failure
# stops this script (set -e), and with it the deploy.
if [ -f ./configure ]; then
    echo "[install.sh] running ./configure"
    sh ./configure
fi

echo "[install.sh] done"
