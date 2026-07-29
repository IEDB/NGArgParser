#!/usr/bin/env python3
"""
Project path setup module
Import this module to automatically configure Python paths
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# A fresh build tree or clone has no .env (build.sh's glob skips dotfiles; .env is
# gitignored), so APP_ROOT is unset until './configure' runs. Say that outright instead
# of letting Path(None) raise a TypeError several frames deep in pathlib.
_app_root = os.getenv('APP_ROOT')
if not _app_root:
    sys.exit(
        "This project has not been configured yet — '.env' is missing or incomplete.\n"
        f"Run './configure' from the project root ({Path(__file__).resolve().parents[2]}), "
        "then re-run this command."
    )

APP_ROOT = Path(_app_root)

# Auto-setup nxg-tools path when this module is imported
_nxg_tools_path = str(APP_ROOT / 'libs')
# print(f"DEBUG: _nxg_tools_path = {_nxg_tools_path}")

if _nxg_tools_path not in sys.path:
    sys.path.insert(0, _nxg_tools_path)

# Recursively add all nested package directories to Python path
def add_nested_packages(libs_dir):
    """Recursively add all subdirectories in libs/ to Python path"""
    for root, dirs, files in os.walk(libs_dir):
        if '__init__.py' in files:  # Only add directories that are Python packages
            if root not in sys.path:
                sys.path.insert(0, root)
                # print(f"DEBUG: Added nested package path: {root}")

add_nested_packages(_nxg_tools_path)
