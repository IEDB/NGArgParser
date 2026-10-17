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
# (child processes inherit the environment), so these always come from this
# tool's .env.
IDENTITY_KEYS = ("APP_ROOT", "APP_NAME")


def load_app_env():
    """Load this project's .env into os.environ.

    Keys other than APP_ROOT and APP_NAME follow python-dotenv's rule: a value
    already in the environment wins. APP_ROOT and APP_NAME always come from this
    project's .env, so a tool started by another tool (phbr starting tc1)
    imports its own libs/, not the caller's.

    Without a .env (a build copy, or a project not configured yet), an inherited
    APP_ROOT is kept only when it points at this project, as the build's startup
    check sets it. One that points elsewhere belongs to a calling tool: it is
    dropped together with APP_NAME, so the tool stops with "not configured".
    """
    dotenv.load_dotenv(ENV_FILE)
    own = dotenv.dotenv_values(ENV_FILE) if ENV_FILE.is_file() else {}
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
