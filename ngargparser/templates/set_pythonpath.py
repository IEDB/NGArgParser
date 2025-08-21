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
APP_ROOT = Path(os.getenv('APP_ROOT'))

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
