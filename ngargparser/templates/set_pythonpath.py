#!/usr/bin/env python3
"""
Project path setup module
Import this module to automatically configure Python paths
"""

import sys
import os
from pathlib import Path

try:
    from core.app_env import _stop, load_app_env
except ImportError as e:
    # A Python without the tool's packages (/usr/bin/python3 src/run_<name>.py):
    # say how to run the tool instead of a traceback that ends in "No module
    # named 'dotenv'". Any other import error is a real one, so it is raised.
    # A build or deployed copy (it has .ngargparser-build) has no uv project to
    # work on, and may be read-only: run its .venv's python, which
    # deploy/install.sh makes. From another folder, the tool folder is named.
    if getattr(e, "name", None) != "dotenv":
        raise
    _script = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0].endswith(".py") else "<script>"
    _tool = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        _here = os.path.realpath(os.getcwd())
    except OSError:  # the current folder was removed
        _here = None
    _where = "" if _here == os.path.realpath(_tool) else f"in {_tool}, "
    if os.path.isfile(os.path.join(_tool, ".ngargparser-build")):
        sys.exit(
            f"This tool needs its virtualenv: {_where}run it with '.venv/bin/python src/{_script} ...'. "
            "Run 'bash deploy/install.sh' if .venv is missing."
        )
    sys.exit(
        f"This tool needs its virtualenv: {_where}run it with 'uv run python src/{_script} ...' "
        "(or activate .venv first). Run 'uv sync' if .venv is missing."
    )

# This tool's .env; every key it defines comes from that file, even
# when another tool started this one and passed its values down.
load_app_env()

# A fresh build tree or clone has no .env (the build does not ship it, and .env is
# gitignored), so APP_ROOT is unset until './configure' runs. Say that outright instead
# of letting Path(None) raise a TypeError several frames deep in pathlib. Under pytest,
# which imports this while it collects the tests, the run stops with just this message.
_app_root = os.getenv('APP_ROOT')
# A deployed copy without .env is most often one a redeploy is installing: its
# install.sh runs ./configure, which writes .env, last. A same-version redeploy
# removes the old files in any order, so any one of a tool folder's files left
# counts. A git checkout (it has .git) is a clone ./configure has not run in yet.
_tool_dir = Path(__file__).resolve().parents[2]
_deployed = not (_tool_dir / '.git').exists() and (
    any((_tool_dir / name).is_file() for name in ('configure', '.ngargparser-build', 'deploy/install.sh'))
    or any((_tool_dir / name).is_dir() for name in ('.ngargparser', '.venv', 'src'))
)
if not _app_root and _deployed and not (_tool_dir / '.env').is_file():
    _stop(
        "This tool is being installed right now (a redeploy in progress?): '.env' is missing; try again.\n"
        f"If no install is running, run './configure' in the tool folder ({_tool_dir}), "
        "then re-run this command."
    )
if not _app_root:
    _stop(
        "This tool has not been configured yet: '.env' is missing or incomplete.\n"
        f"Run './configure' in the tool folder ({Path(__file__).resolve().parents[2]}), "
        "then re-run this command."
    )

APP_ROOT = Path(_app_root)

# Auto-setup nxg-tools path when this module is imported
_nxg_tools_path = str(APP_ROOT / 'libs')
# print(f"DEBUG: _nxg_tools_path = {_nxg_tools_path}")

def _add_last(paths):
    """Put `paths` at the end of sys.path, in this order.

    A module of the standard library or the tool's virtualenv always wins over
    a folder of the same name in libs/: the build adds an __init__.py to every
    fetched folder, so a fetched somerepo/tests/fixtures/csv/ or docs/_build/html/
    would otherwise be imported as `csv` or `html`. At the end they also stay
    behind the tool's own src/ folder, so a preprocess.py or validators.py that
    a fetched folder holds (another tool's src/, a library's examples/) never
    replaces the tool's own. A path already on sys.path moves to the end too.
    """
    sys.path[:] = [entry for entry in sys.path if entry not in paths] + list(paths)

# Recursively add all nested package directories to Python path
def add_nested_packages(libs_dir):
    """Add libs/ and all package subdirectories in it to the end of the Python path.

    Each folder with an __init__.py is added, so code in libs/ can import its
    own modules by their plain name; a name the standard library or an
    installed package already has is not taken from libs/. The folders are
    walked top-down in sorted order, so every host gets the same order, and
    the ones reached last come first, libs/ itself last: of libs/aaa/util.py
    and libs/zzz/util.py, `import util` imports libs/zzz/util.py.
    """
    dirs = [libs_dir]
    for root, subdirs, files in os.walk(libs_dir):
        subdirs.sort()  # os.walk lists them in the file system's order, which differs between hosts
        if '__init__.py' in files and root not in dirs:  # Only add directories that are Python packages
            dirs.insert(0, root)
            # print(f"DEBUG: Added nested package path: {root}")
    _add_last(dirs)

add_nested_packages(_nxg_tools_path)
