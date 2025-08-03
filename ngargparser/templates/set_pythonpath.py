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
if _nxg_tools_path not in sys.path:
    sys.path.insert(0, _nxg_tools_path)
