# app_env.py
# Loads this tool's own .env - DO NOT MODIFY
# Framework-owned: `cli sync` overwrites this file.

import os
from pathlib import Path

import dotenv

# <project>/src/core/app_env.py -> <project>. abspath, not resolve: a symlinked
# file still belongs to the project it sits in.
PROJECT_ROOT = Path(os.path.abspath(__file__)).parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

# Which tool this is. A tool that starts this one passes its own values down
# (child processes inherit the environment), so unlike other keys these are not
# simply inherited when this tool's .env leaves them out (see load_app_env).
IDENTITY_KEYS = ("APP_ROOT", "APP_NAME")

# Process settings a .env value never replaces. A stale or accidental key (an
# old ./configure once wrote PATH=<class 'pathlib.Path'>) would break every
# command the tool runs; one missing from the environment is still filled in.
SYSTEM_KEYS = ("PATH", "PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "HOME")


def load_app_env():
    """Load this project's .env into os.environ.

    Every key this project's .env defines comes from that file, even when the
    environment already has a value. A tool started by another tool (phbr
    starting tc1) inherits the caller's environment, so it would otherwise see
    the caller's APP_ROOT (and import the caller's libs/), APP_VENV, APP_ENTRY
    and dependency paths. To change a key the file defines, change the file
    (./configure writes it); exporting the variable does not override it.

    Keys the file does not define keep the value from the environment, except
    APP_NAME: when the .env sets APP_ROOT but not APP_NAME, an inherited
    APP_NAME names the caller, so it is dropped. SYSTEM_KEYS (PATH and the like)
    are the other exception: the environment's value always wins.

    Without a .env (a build copy, or a project not configured yet), an inherited
    APP_ROOT is kept only when it points at this project, as the build's startup
    check sets it. One that points elsewhere belongs to a calling tool: it is
    dropped together with APP_NAME, so the tool stops with "not configured".
    """
    own = dotenv.dotenv_values(ENV_FILE) if ENV_FILE.is_file() else {}
    for key, value in own.items():
        if value is not None and (key not in SYSTEM_KEYS or key not in os.environ):
            os.environ[key] = value
    if own.get("APP_ROOT"):
        for key in IDENTITY_KEYS:
            if own.get(key):
                os.environ[key] = own[key]
            else:
                os.environ.pop(key, None)
        return
    inherited = os.environ.get("APP_ROOT")
    if inherited and os.path.realpath(inherited) != os.path.realpath(PROJECT_ROOT):
        for key in IDENTITY_KEYS:
            os.environ.pop(key, None)
