# app_env.py
# Loads this tool's own .env - DO NOT MODIFY
# Framework-owned: `cli sync` overwrites this file.

import ast
import difflib
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

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

# The .env key where ./configure lists, comma-separated, the keys it read from a
# dependency's own .env: <DEP>_VENV from its APP_VENV, <DEP>_ENTRY from its
# APP_ENTRY. The suffix names the key read from "<DEP>_PATH/.env".
KEYS_FROM_DEPS = "APP_KEYS_FROM_DEPS"
DEP_KEY_SOURCES = (("_VENV", "APP_VENV"), ("_ENTRY", "APP_ENTRY"))

# Framework keys that describe one tool: what it publishes to the tools that
# call it (APP_VENV, APP_ENTRY), and which keys it follows (APP_KEYS_FROM_DEPS).
# An inherited value describes the tool that started this one, so it is dropped
# when this tool's .env leaves the key out.
FRAMEWORK_KEYS = ("APP_VENV", "APP_ENTRY", KEYS_FROM_DEPS)

# The keys ./configure writes for a dependency <DEP>: its paths.py settings and
# what it publishes. A <NAME>_PATH whose <NAME> ends in one of SETTING_ENDINGS is
# a setting of a dependency, not a dependency (the rule ./configure uses).
DEP_SUFFIXES = ("_PATH", "_VENV", "_ENTRY", "_MODULE", "_LIB_PATH")
SETTING_ENDINGS = ("_LIB", "_VENV", "_MODULE")

# The key that names the Python a dependency <DEP> runs with. It is in .env only
# when paths.py sets <dep>_python, which then wins everywhere; else load_app_env
# derives it from <DEP>_VENV and <DEP>_ENTRY, as setup_<dep>_env.sh does when it
# is sourced (see _dependency_python and _runs_with_python).
PYTHON_SUFFIX = "_PYTHON"

# Several core modules call load_app_env, and the first call puts this tool's
# APP_ROOT into os.environ, so only that first call can see an inherited one.
_root_checked = False
# This folder, when .env names another one as APP_ROOT but the environment's
# APP_ROOT names this one (see _check_configured_here). Kept for later calls.
_root_here = None
# True once load_app_env has run in this process: dependency_command runs it
# first otherwise, for a helper or a test that imports only core.app_env.
_env_loaded = False
# The values this tool's .env set when load_app_env last found it there with
# values. dependency_command reads the .env again at each call, and uses these
# while it is missing or empty: a redeploy of this tool removes it for a moment.
_loaded_values: Dict[str, Optional[str]] = {}
# True once the check that this tool runs with its own Python has run (see
# _check_interpreter): it warns once per process.
_interpreter_checked = False
# True once _activate_own_venv has run: it sets VIRTUAL_ENV and PATH once per process.
_venv_activated = False


class DependencyError(RuntimeError):
    """dependency_command cannot run a dependency; the message says what to do.

    A RuntimeError, so `except RuntimeError` still catches it.
    """


def _stop(message):
    """Stop the tool with `message`, as sys.exit(message) does.

    pytest imports a tool's modules while it collects the tests. A SystemExit
    there ends in a long INTERNALERROR traceback, and pytest.exit() in an error
    for each test file. An exception that is both stops the run with only the
    message, and exit code 1. Anywhere else it is a plain SystemExit.
    """
    if "pytest" in sys.modules:
        import pytest

        not_configured = type("NotConfigured", (pytest.exit.Exception, SystemExit), {})
        error = not_configured(message, returncode=1)
        error.code = message  # printed, with exit code 1, if nothing catches it
        raise error
    sys.exit(message)


def _own_values():
    """The values this tool's .env sets, or {} without one.

    Stops the tool with one line when it is not UTF-8 text, or cannot be read
    (chmod 000, or another user's file): ./configure writes it again.
    """
    if not ENV_FILE.is_file():
        return {}
    try:
        return dotenv.dotenv_values(ENV_FILE)
    except UnicodeDecodeError:
        _stop(f"{ENV_FILE} is not UTF-8 text: run ./configure again to rewrite it")
    except OSError as e:
        _stop(
            f"{ENV_FILE} cannot be read ({e.strerror or e}): run ./configure in {PROJECT_ROOT} "
            f"to write it again, or ask its owner to make it readable"
        )


def _same_folder(a, b):
    """True when paths `a` and `b` name one folder: a `current` link to it counts."""
    if os.path.realpath(a) == os.path.realpath(b):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _venv_made_for(venv):
    """The folder virtualenv `venv` was made in, when that is another folder; else None.

    The same check as ./configure's: bin/activate sets VIRTUAL_ENV to the
    folder the virtualenv was made in, and `python -m venv` also names it in
    the `command` line of pyvenv.cfg. A moved or copied one keeps the old one.
    """
    recorded = []
    try:
        with open(os.path.join(venv, "bin", "activate"), encoding="utf-8", errors="replace") as f:
            for line in f:
                match = re.match(r"^[ \t]*(?:export[ \t]+)?VIRTUAL_ENV=(.*?)[ \t]*$", line.rstrip("\r\n"))
                if match:
                    value = match.group(1)
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                        value = value[1:-1]
                    if value.startswith("/") and "$" not in value and "`" not in value:
                        recorded.append(value)
                        break
    except OSError:
        pass
    try:
        with open(os.path.join(venv, "pyvenv.cfg"), encoding="utf-8", errors="replace") as f:
            for line in f:
                key, _sep, value = line.partition("=")
                if key.strip() == "command" and " -m venv " in value:
                    # Options such as --prompt="x" come first, the absolute folder last.
                    found = re.search(r"(?:^|\s)(/.*?)\s*$", value.split(" -m venv ", 1)[1])
                    if found:
                        recorded.append(found.group(1))
    except OSError:
        pass
    for folder in recorded:
        if os.path.realpath(folder) != os.path.realpath(venv):
            return folder
    return None


def _deployed_copy(folder):
    """True when tool folder `folder` is a build or deployed copy, as ./configure tells it.

    It has .ngargparser-build, which `make build` writes into every build copy,
    and deploy/install.sh, which makes its .venv: it has no uv project to sync.
    """
    return os.path.isfile(os.path.join(folder, ".ngargparser-build")) and os.path.isfile(
        os.path.join(folder, "deploy", "install.sh")
    )


def _check_configured_here(own):
    """Stop when this tool's .env was written for another folder.

    ./configure writes the absolute APP_ROOT, so a copied tool would import the
    original folder's libs/ without a word, and a moved one would fail on its
    first import. Only a tool's own src/core/app_env.py checks, and an APP_ROOT
    in the environment that points here (the build's startup check, `cli sync`)
    lets it run, with this folder as APP_ROOT (_root_here), so its own libs/ is
    imported, not the folder's in .env. Checked once per process (see
    _root_checked).
    """
    global _root_checked, _root_here
    if _root_checked:
        return
    _root_checked = True
    configured = own.get("APP_ROOT")
    here = Path(os.path.abspath(__file__)).parent
    if not configured or here.name != "core" or here.parent.name != "src":
        return
    if _same_folder(configured, PROJECT_ROOT):
        return
    inherited = os.environ.get("APP_ROOT")
    if inherited and _same_folder(inherited, PROJECT_ROOT):
        _root_here = str(PROJECT_ROOT)
        return
    # A virtualenv that came along still points at the old folder, and
    # ./configure does not publish it: it has to be made again first. In a
    # build or deployed copy, deploy/install.sh makes it, then runs ./configure.
    venv = PROJECT_ROOT / ".venv"
    made_for = _venv_made_for(venv) if venv.is_dir() else None
    if made_for and _deployed_copy(PROJECT_ROOT):
        _stop(
            f"This tool was configured in {configured} but is running from {PROJECT_ROOT}.\n"
            f"{venv} was made for {made_for}; recreate it first: rm -r .venv && bash deploy/install.sh, "
            f"which runs ./configure too\n"
            f"Run that in {PROJECT_ROOT}, then re-run this command."
        )
    recreate = f"{venv} was made for {made_for}; recreate it first: rm -r .venv && uv sync\n" if made_for else ""
    _stop(
        f"This tool was configured in {configured} but is running from {PROJECT_ROOT}.\n"
        f"{recreate}"
        f"Run './configure' in {PROJECT_ROOT}, then re-run this command."
    )


def _requires_python():
    """[project] requires-python in this tool's pyproject.toml, or None.

    Read as text, line by line, without a TOML library: a quoted value on one
    line, under a [project] header. Anything else counts as not set.
    """
    try:
        with open(PROJECT_ROOT / "pyproject.toml", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    table = None
    for line in lines:
        header = re.match(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$", line)
        if header:
            table = header.group(1).strip()
        elif line.lstrip().startswith("[["):
            table = None
        elif table == "project":
            found = re.match(r"""^\s*requires-python\s*=\s*(?:"([^"\\]*)"|'([^']*)')\s*(?:#.*)?$""", line)
            if found:
                return (found.group(1) if found.group(1) is not None else found.group(2)).strip() or None
    return None


def _satisfies(version, spec):
    """True when Python `version` (major, minor, micro) satisfies requires-python `spec`, False when not.

    None when a clause is not one this reads: >=, >, <=, <, ==, != and ~=
    with a version of numbers, and == or != with a trailing .*. == and !=
    compare only the parts given (==3.11 matches 3.11.4), the others pad it
    with zeros (<3.12 leaves out 3.12.0).
    """
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        found = re.fullmatch(r"(~=|==|!=|<=|>=|<|>)\s*(\d+(?:\.\d+)*)(\.\*)?", clause)
        if not found or (found.group(3) and found.group(1) not in ("==", "!=")):
            return None
        op, wanted = found.group(1), tuple(int(part) for part in found.group(2).split("."))
        if op in ("==", "!="):
            if (tuple(version[: len(wanted)]) == wanted) != (op == "=="):
                return False
            continue
        if op == "~=":
            if len(wanted) < 2:
                return None
            if tuple(version[: len(wanted) - 1]) != wanted[:-1]:
                return False
            op = ">="
        padded = (wanted + (0, 0, 0))[:3]
        have = tuple(version[:3])
        ok = {">=": have >= padded, ">": have > padded, "<=": have <= padded, "<": have < padded}[op]
        if not ok:
            return False
    return True


def _venv_version(venv):
    """The Python version a virtualenv `venv` was made with, from its pyvenv.cfg, as (major, minor, micro); else None."""
    try:
        with open(os.path.join(venv, "pyvenv.cfg"), encoding="utf-8", errors="replace") as f:
            for line in f:
                key, _sep, value = line.partition("=")
                if key.strip() in ("version", "version_info"):
                    found = re.match(r"\s*(\d+)\.(\d+)(?:\.(\d+))?", value)
                    if found:
                        return tuple(int(part or 0) for part in found.groups())
    except OSError:
        pass
    return None


def _check_python_version(own, venvs, running_own):
    """Stop when this Python does not satisfy requires-python and a fix can be named.

    That is when it runs in its own virtualenv (`running_own`), made with a
    Python requires-python leaves out, or with another Python while its own
    virtualenv (one of `venvs`) has one that satisfies it. A tool with no
    virtualenv of its own has none to name, and is not stopped.
    """
    spec = _requires_python()
    have = tuple(sys.version_info[:3])
    if not spec or _satisfies(have, spec) is not False:
        return
    name = own.get("APP_NAME") or PROJECT_ROOT.name
    running = ".".join(str(part) for part in have)
    if running_own:
        if _same_folder(sys.prefix, PROJECT_ROOT / ".venv"):
            remake = "bash deploy/install.sh" if _deployed_copy(PROJECT_ROOT) else "uv sync"
            fix = f"recreate it: rm -r .venv && {remake}, in {PROJECT_ROOT}"
        else:
            fix = "recreate it with a Python that does"
        _stop(f"{name} needs Python {spec}, but its virtualenv {sys.prefix} has Python {running}: {fix}")
    for venv in venvs:
        python = os.path.join(venv, "bin", "python")
        version = _venv_version(venv)
        if os.path.exists(python) and version and _satisfies(version, spec):
            _stop(f"{name} needs Python {spec}, not {running} ({sys.executable}): run it with {python}")


def _on_sys_path(venv):
    """True when the site-packages of virtualenv `venv` is on sys.path.

    `uv run --with <package>` runs a Python whose environment lies over the
    project's virtualenv: its own sys.prefix, the project's packages too.
    """
    sites = {os.path.realpath(site) for site in Path(venv).glob("lib/python*/site-packages")}
    return bool(sites) and any(entry and os.path.realpath(entry) in sites for entry in sys.path)


def _own_venvs(own):
    """This tool's own virtualenvs: the one its .env publishes (APP_VENV), then its .venv."""
    venvs = []
    published = None if _root_here else own.get("APP_VENV")  # with _root_here, .env describes another folder
    if published:
        venvs.append(published if os.path.isabs(published) else str(PROJECT_ROOT / published))
    venvs.append(str(PROJECT_ROOT / ".venv"))
    return venvs


def _activate_own_venv(own):
    """Set VIRTUAL_ENV and PATH as activating this tool's own virtualenv does, when it runs with its Python.

    A caller that runs it as ${X_PYTHON:+"$X_PYTHON"} "${X_ENTRY:?}" from its
    own virtualenv leaves VIRTUAL_ENV naming that one, and its bin/ first on
    PATH: `python` or a console script this tool starts by name would come
    from the caller's virtualenv. So when sys.prefix is this tool's own
    virtualenv (_own_venvs) while VIRTUAL_ENV names another folder, or its
    bin/ is not first on PATH, VIRTUAL_ENV becomes it and its bin/ goes first
    on PATH, for the programs this tool starts. Nothing is printed. Only a
    tool's own src/core/app_env.py does this, once per process.
    """
    global _venv_activated
    if _venv_activated:
        return
    _venv_activated = True
    here = Path(os.path.abspath(__file__)).parent
    if here.name != "core" or here.parent.name != "src":
        return
    venv = next((venv for venv in _own_venvs(own) if _same_folder(sys.prefix, venv)), None)
    if venv is None:
        return
    bin_dir = os.path.join(venv, "bin")
    path = os.environ.get("PATH")
    first = path.split(os.pathsep, 1)[0] if path else ""
    bin_first = bool(first) and _same_folder(first, bin_dir)
    active = os.environ.get("VIRTUAL_ENV")
    if bin_first and not (active and not _same_folder(active, venv)):
        return
    os.environ["VIRTUAL_ENV"] = venv
    if not bin_first:
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{path}" if path else bin_dir


def _check_interpreter(own):
    """Warn once, on stderr, when this tool runs with a Python that is not its own.

    A caller that runs [sys.executable, entry], or `<other tool>/.venv/bin/python
    src/run_x.py`, gives this tool the other Python's packages: python-dotenv
    is in both, so it starts without a word, and may fail later, or run with
    other versions. Its own Python is the one in the virtualenv its .env
    publishes (APP_VENV), or in its .venv; the build's startup check runs it
    in .ngargparser/startup-venv, which counts as its own too, and so does one
    whose sys.path has its virtualenv's packages (`uv run --with`). `uv run` and
    dependency_command run its own. Nothing is said when it has no virtualenv
    to name, and only a tool's own src/core/app_env.py checks. A warning, not
    a stop: the other Python may have every package it needs.

    A Python that requires-python in pyproject.toml leaves out stops the tool
    first, when a fix can be named (_check_python_version).
    """
    global _interpreter_checked
    if _interpreter_checked:
        return
    _interpreter_checked = True
    here = Path(os.path.abspath(__file__)).parent
    if here.name != "core" or here.parent.name != "src":
        return
    venvs = _own_venvs(own)
    running_own = any(_same_folder(sys.prefix, venv) for venv in venvs)
    # uv makes the build's startup-venv with a Python requires-python allows.
    if os.path.realpath(sys.prefix).endswith(os.sep + os.path.join(".ngargparser", "startup-venv")):
        return
    _check_python_version(own, venvs, running_own)
    if running_own or any(_on_sys_path(venv) for venv in venvs):
        return
    pythons = [os.path.join(venv, "bin", "python") for venv in venvs]
    python = next((python for python in pythons if os.path.exists(python)), None)
    if python is None:
        return
    name = own.get("APP_NAME") or PROJECT_ROOT.name
    print(
        f"Warning: {name} is running with {sys.executable}, not with its own virtualenv, "
        f"so it gets that Python's packages: run it with {python}",
        file=sys.stderr,
    )


def _written_for(values, dep_path):
    """The APP_ROOT a dependency's .env `values` name, when that is not the folder `dep_path`; else None.

    ./configure writes the absolute folder it ran in. A dependency copied or
    moved after that keeps the old .env, whose APP_VENV and APP_ENTRY name the
    old folder's files: they are not used until ./configure runs there again.
    """
    root = values.get("APP_ROOT")
    if root and os.path.isabs(root) and os.path.realpath(root) != os.path.realpath(dep_path):
        return root
    return None


def _read_from_dependency(own, key, cache):
    """The value `key` has now in its dependency's own .env.

    `<DEP>_PATH` comes from this tool's .env, and the value from the file
    "<DEP>_PATH/.env", never from os.environ. Returns None when there is no
    such file to read (the dependency is not deployed, or <DEP>_PATH is not
    set): the copy in this tool's .env is the fallback. Returns "" when the file
    is there but sets no value: the dependency as deployed now publishes none,
    and the copy describes an earlier deploy, maybe another version. A .env
    written for another folder (_written_for) counts as no file, and so does
    one that cannot be read (a permission this user lacks) or is not UTF-8
    text, as in ./configure and setup_<dep>_env.sh.

    A relative APP_ENTRY is joined to the folder that .env describes, as
    ./configure does: its APP_ROOT, else the real folder behind <DEP>_PATH.
    Through a `current` link, that is the version folder its APP_VENV names
    too, so the two never come from two versions.
    """
    for suffix, source in DEP_KEY_SOURCES:
        if key.endswith(suffix) and len(key) > len(suffix):
            dep_path = own.get(key[: -len(suffix)] + "_PATH")
            break
    else:
        return None
    if not dep_path:
        return None
    if dep_path not in cache:
        dep_env = os.path.join(dep_path, ".env")
        if not os.path.isfile(dep_env):
            cache[dep_path] = None
        else:
            values: Optional[Dict[str, Optional[str]]]
            try:
                values = dotenv.dotenv_values(dep_env)
            except (OSError, UnicodeDecodeError):  # cannot be read, or not UTF-8 text: as missing
                values = None
            cache[dep_path] = None if values is None or _written_for(values, dep_path) else values
    values = cache[dep_path]
    if values is None:
        return None
    value = values.get(source)
    if not value:
        return ""
    if source == "APP_ENTRY" and not os.path.isabs(value):
        root = values.get("APP_ROOT")
        if not root or not os.path.isabs(root):
            root = os.path.realpath(dep_path)
        value = os.path.join(root, value)
    return value


def _followed_keys(own):
    """The keys APP_KEYS_FROM_DEPS lists: the ones read again from a dependency's own .env."""
    return {key.strip() for key in (own.get(KEYS_FROM_DEPS) or "").split(",") if key.strip()}


def _names_another_folder(value, dep_path, key):
    """True when `value`, a <DEP>_VENV or <DEP>_ENTRY (`key`) kept from earlier, names a folder outside the dependency.

    The folder is the virtualenv itself, or the one the entry script is in. It
    counts only when it is there, and its real path is not inside the real
    folder behind <DEP>_PATH (`dep_path`), or that folder is not there.
    ./configure saves the values through a `current` link as the version
    folder it pointed to then: once a redeploy moves the link, they name the
    version deployed before, which may still be there. A relative value is
    joined to <DEP>_PATH, so it never names another folder. The same rule as
    setup_<dep>_env.sh.
    """
    if not value or not os.path.isabs(value):
        return False
    folder = value if key.endswith("_VENV") else (os.path.dirname(value) or os.sep)
    if not os.path.isdir(folder):
        return False
    if not dep_path or not os.path.isdir(dep_path):
        return True
    root, real = os.path.realpath(dep_path), os.path.realpath(folder)
    return real != root and not real.startswith(root.rstrip(os.sep) + os.sep)


def _fallback_elsewhere(own, prefix, cache, values):
    """True when a fallback for dependency <prefix> (`prefix`) names another folder.

    A key APP_KEYS_FROM_DEPS lists is read from "<DEP>_PATH/.env"; while that
    .env cannot be used (missing during a redeploy, unreadable, not UTF-8 text,
    or written for another folder), the value in `values` stands in for it:
    this tool's .env (load_app_env), or what load_app_env loaded
    (dependency_command). One that names a folder outside the dependency
    (_names_another_folder) would run another version without a word, so none
    of the dependency's fallbacks is used then.
    """
    followed = _followed_keys(own)
    dep_path = own.get(prefix + "_PATH")
    for suffix, _source in DEP_KEY_SOURCES:
        key = prefix + suffix
        if (
            key in followed
            and _read_from_dependency(own, key, cache) is None
            and _names_another_folder(values.get(key), dep_path, key)
        ):
            return True
    return False


def _declared_dependencies(own):
    """The dependencies this tool's .env describes, as the <DEP> of their keys.

    ./configure writes <DEP>_PATH for each one, and lists <DEP>_VENV or
    <DEP>_ENTRY in APP_KEYS_FROM_DEPS for each one it follows. (A dependency
    whose path is not set stops ./configure before it writes .env.)
    """
    deps = set()
    for key in own:
        if key.endswith("_PATH") and key not in SYSTEM_KEYS:
            name = key[: -len("_PATH")]
            if name and not name.endswith(SETTING_ENDINGS):
                deps.add(name)
    for key in (own.get(KEYS_FROM_DEPS) or "").split(","):
        key = key.strip()
        for suffix, _source in DEP_KEY_SOURCES:
            if key.endswith(suffix) and len(key) > len(suffix):
                deps.add(key[: -len(suffix)])
    return deps


def _dependency_python(venv):
    """The Python a dependency runs with: its virtualenv's python, else python3.

    setup_<dep>_env.sh exports <DEP>_PYTHON by the same rule, so a dependency
    run from Python (dependency_command) and one run from a shell after
    sourcing that script ("$<DEP>_PYTHON" "$<DEP>_ENTRY") get the same one.
    A <dep>_python set in paths.py wins over this rule in both.
    """
    return os.path.join(venv, "bin", "python") if venv else "python3"


def _first_line(entry):
    """The first line of file `entry`, as bytes; b"" when it is missing or unreadable."""
    try:
        with open(entry, "rb") as f:
            return f.readline(4096)
    except OSError:
        return b""


def _runs_with_python(entry):
    """True when start-up script `entry` is a Python script, so its dependency's Python runs it.

    A first line that starts with #! decides: it names python, or it does not.
    Without one, a name that ends in .py is a Python script. setup_<dep>_env.sh
    uses the same rule, and leaves <DEP>_PYTHON empty for any other entry.
    """
    line = _first_line(entry)  # missing or unreadable: the name decides
    if line.startswith(b"#!"):
        return b"python" in line
    return entry.endswith(".py")


def load_app_env():
    """Load this project's .env into os.environ.

    Every key this project's .env defines comes from that file, even when the
    environment already has a value. A tool started by another tool (phbr
    starting tc1) inherits the caller's environment, so it would otherwise see
    the caller's APP_ROOT (and import the caller's libs/), APP_VENV, APP_ENTRY
    and dependency paths. To change a key the file defines, change the file
    (./configure writes it); exporting the variable does not override it.

    Keys the file does not define keep the value from the environment, with
    these exceptions. APP_NAME: when the .env sets APP_ROOT but not APP_NAME, an
    inherited APP_NAME names the caller, so it is dropped. FRAMEWORK_KEYS
    (APP_VENV, APP_ENTRY, APP_KEYS_FROM_DEPS) describe the caller too, so they
    are dropped whenever the file leaves them out. So are the keys of a
    dependency this tool declares (<DEP>_PATH, _VENV, _ENTRY, _MODULE,
    _LIB_PATH; see _declared_dependencies): the caller's dependency of the same
    name may be another install. The keys of a dependency it does not declare
    are kept, since they may be the user's own. SYSTEM_KEYS (PATH and the like)
    are the last exception: the environment's value always wins.

    The keys listed in APP_KEYS_FROM_DEPS (a dependency's virtualenv and
    start-up script, as ./configure last saw them) are read again from that
    dependency's own .env, so a redeploy of it is seen without re-running
    ./configure here. The value in this project's .env is the fallback when
    the dependency has no .env (not deployed yet), unless one of its
    fallbacks names a folder outside the dependency (an older version behind
    a `current` link; see _fallback_elsewhere): then none of them is loaded,
    and dependency_command says to try again. With neither, or when its
    .env sets none, an inherited value is dropped: it belongs to the calling
    tool, whose dependency of the same name may sit in another folder.

    Each declared dependency also gets <DEP>_PYTHON, worked out from the
    <DEP>_VENV loaded here: "<DEP>_VENV/bin/python", else python3, as its setup
    script exports it. It is empty when <DEP>_ENTRY is not a Python script
    (_runs_with_python): that entry runs by itself. It is empty too when no
    <DEP>_ENTRY is known (the dependency is not deployed or configured yet),
    so "$<DEP>_PYTHON" never runs an empty script name. An inherited one is
    replaced; one this tool's .env sets (<dep>_python in paths.py) is kept,
    like any other key it defines.

    An APP_ROOT in the .env that names another folder (the tool was copied or
    moved after ./configure) stops the tool with a message to re-run it. A
    Python that is not this tool's own gets one warning (_check_interpreter).
    Run with its own virtualenv's Python, the tool gets that virtualenv's
    VIRTUAL_ENV and bin/ first on PATH, as activating it does
    (_activate_own_venv), so the programs it starts by name are its own.

    Without a .env (a build copy, or a project not configured yet), an inherited
    APP_ROOT is kept only when it points at this project, as the build's startup
    check sets it. One that points elsewhere belongs to a calling tool: it is
    dropped together with APP_NAME, so the tool stops with "not configured".
    """
    global _env_loaded, _loaded_values
    own = _own_values()
    _check_configured_here(own)
    _check_interpreter(own)
    _env_loaded = True
    if own:
        _loaded_values = dict(own)
    for key, value in own.items():
        if value is not None and (key not in SYSTEM_KEYS or key not in os.environ):
            os.environ[key] = value
    _activate_own_venv(own)
    cache: Dict[str, Optional[Dict[str, Optional[str]]]] = {}
    followed = set()
    for key in (own.get(KEYS_FROM_DEPS) or "").split(","):
        key = key.strip()
        if not key or key in IDENTITY_KEYS or key in SYSTEM_KEYS:
            continue
        followed.add(key)
        value = _read_from_dependency(own, key, cache)
        if value:
            os.environ[key] = value
        elif value == "" or own.get(key) is None:
            # The dependency's .env publishes none, or there is none and this
            # tool's .env has no copy either: a value in the environment is the
            # calling tool's, for its own dependency of the same name.
            os.environ.pop(key, None)
    for dep in _declared_dependencies(own):
        if _fallback_elsewhere(own, dep, cache, own):
            for suffix, _source in DEP_KEY_SOURCES:
                if dep + suffix in followed:
                    os.environ.pop(dep + suffix, None)
    for dep in _declared_dependencies(own):
        for suffix in DEP_SUFFIXES:
            key = dep + suffix
            if key not in followed and own.get(key) is None and key not in SYSTEM_KEYS + IDENTITY_KEYS:
                os.environ.pop(key, None)
        if not own.get(dep + PYTHON_SUFFIX):
            entry = os.environ.get(dep + "_ENTRY")
            if not entry or not _runs_with_python(entry):
                os.environ[dep + PYTHON_SUFFIX] = ""
            else:
                os.environ[dep + PYTHON_SUFFIX] = _dependency_python(os.environ.get(dep + "_VENV"))
    for key in FRAMEWORK_KEYS:
        if own.get(key) is None:
            os.environ.pop(key, None)
    if own.get("APP_ROOT"):
        for key in IDENTITY_KEYS:
            own_value = own.get(key)
            if own_value:
                os.environ[key] = own_value
            else:
                os.environ.pop(key, None)
        if _root_here:
            os.environ["APP_ROOT"] = _root_here
        return
    inherited = os.environ.get("APP_ROOT")
    if inherited and os.path.realpath(inherited) != os.path.realpath(PROJECT_ROOT):
        for key in IDENTITY_KEYS:
            os.environ.pop(key, None)


def _paths_py_declaration(prefix):
    """How this tool's paths.py assigns <prefix>_path, which ./configure writes to .env as <PREFIX>_PATH.

    Returns None when it does not, the name it assigns (`netmhc_path`) when
    every assignment of it sets None or a blank text (./configure stops on such
    a path), and "" for any other value. Read as text and parsed, never run.
    Any case counts, as ./configure writes the key in capitals. A name that
    ends like a setting (<dep>_lib_path) is not a dependency.
    """
    if prefix.endswith(SETTING_ENDINGS):
        return None
    try:
        with open(PROJECT_ROOT / "paths.py", encoding="utf-8-sig") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):  # missing, unreadable, or not Python
        return None
    target = f"{prefix}_PATH"
    names = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id.upper() == target
    ]
    if not names:
        return None
    values = [
        stmt.value
        for stmt in ast.walk(tree)
        if (isinstance(stmt, ast.Assign) and any(each is node for each in stmt.targets for node in names))
        or (isinstance(stmt, ast.AnnAssign) and any(stmt.target is node for node in names))
    ]
    unset = len(values) == len(names) and all(
        isinstance(value, ast.Constant)
        and (value.value is None or (isinstance(value.value, str) and not value.value.strip()))
        for value in values
    )
    return names[0].id if unset else ""


def _not_a_dependency(name, prefix, own):
    """Why `name` (as <prefix>) cannot be run: this tool's .env has no <prefix>_PATH."""
    assigned = _paths_py_declaration(prefix)
    if assigned:  # its path is None in paths.py: a plain ./configure stops on it
        flag = f"--{assigned[: -len('_path')].replace('_', '-')}-path=<path>"
        return f"run ./configure {flag}: paths.py declares {name}, but {assigned} is None there"
    if assigned is not None:
        return f"run ./configure: paths.py declares {name}, but .env has no values for it yet"
    declared = sorted(dep for dep in _declared_dependencies(own) if own.get(dep + "_PATH") is not None)

    def shown(dep):  # in the style of `name`: aa_counter for aa_countr, else aa-counter
        dep = dep.lower()
        return dep if "_" in name and "-" not in name else dep.replace("_", "-")

    close = difflib.get_close_matches(prefix, declared, n=1)
    hint = f" (did you mean {shown(close[0])}?)" if close else ""
    listed = ", ".join(shown(dep) for dep in declared) or "none"
    return (
        f"{name} is not a dependency of this tool (declared: {listed}): "
        f"check the name{hint}, or add it with cli deps add {name}"
    )


def _no_entry(name, prefix, dep_path, follows_entry=False):
    """Why dependency `name` (as <prefix>), at `dep_path`, has no <prefix>_ENTRY.

    A folder that is not there now, or a tool folder (it has ./configure)
    without its .env, is most often a redeploy: the deploy orchestrator
    removes the version folder, extracts the new one, and its install.sh runs
    ./configure there last. <prefix>_ENTRY follows the dependency's .env, so
    the fix is to try again, not ./configure here: the words _not_there uses
    when the entry ./configure saw is gone. setup_<dep>_env.sh says the same.
    A git checkout without its .env is one ./configure has not run in yet.
    """
    var = f"{prefix.lower()}_entry"
    dep_env = os.path.join(dep_path, ".env")
    if not os.path.isdir(dep_path):  # the folder itself is what is missing
        return _not_there(name, dep_path, dep_path)
    if _unconfigured_clone(dep_path):
        return _not_configured(name, dep_path)
    if _being_installed(dep_path, None, follows_entry):
        return _installing(name, dep_path)
    if not os.path.isfile(dep_env):
        return (
            f"{name} publishes no entry script: {dep_env} does not exist. "
            f"Deploy and configure {name} there, or set {var} in paths.py, then run ./configure"
        )
    try:
        written_for = _written_for(dotenv.dotenv_values(dep_env), dep_path)
    except UnicodeDecodeError:
        return (
            f"{name} publishes no entry script: {dep_env} is not UTF-8 text, so its values are not used. "
            f"Save it as UTF-8, or run ./configure in {dep_path} to write it again; "
            f"or set {var} in paths.py, then run ./configure"
        )
    except OSError as e:
        return (
            f"{name} publishes no entry script: {dep_env} cannot be read ({e.strerror or e}), "
            f"so its values are not used. Ask its owner to make it readable; "
            f"or set {var} in paths.py, then run ./configure"
        )
    if written_for:
        return (
            f"{name} publishes no entry script: {dep_env} was written for {written_for}. "
            f"Run ./configure in {dep_path}, or set {var} in paths.py, then run ./configure here"
        )
    return (
        f"{name} publishes no entry script (APP_ENTRY in {dep_env}, or [tool.ngargparser] entry): "
        f"set {var} in paths.py, then run ./configure"
    )


def _can_run(program, path):
    """True when `program` is there to start: a file that exists, or a name found on PATH text `path`."""
    if os.sep in program:
        return os.path.exists(program)
    return shutil.which(program, path=path) is not None


def _not_there(name, dep_path, missing, python=False):
    """Why dependency `name`, at `dep_path`, cannot start now: `missing`, its entry script or Python (`python`), is not there.

    Most often a redeploy, so it says to try again; the second sentence names
    the fix for a dependency that moved, or a mistyped <dep>_python.
    """
    moved = f"If {name} moved, run ./configure --{name.replace('_', '-').lower()}-path=<new folder>"
    if not os.path.isdir(dep_path):
        return f"{name} is not deployed at {dep_path} right now (a redeploy in progress?); try again. {moved}"
    if os.sep not in missing:
        return (
            f"{name} runs with {missing}, which is not on PATH: put it there, "
            f"or set {name.replace('-', '_').lower()}_python in paths.py to its full path, then run ./configure"
        )
    if python:
        moved += f" or fix {name.replace('-', '_').lower()}_python in paths.py"
    return f"{name} cannot run right now: {missing} does not exist (a redeploy in progress?); try again. {moved}"


def _unconfigured_clone(dep_path):
    """True when the real folder behind `dep_path` is a git checkout of a tool (it has ./configure) with no .env.

    A fresh clone: ./configure has not run there yet, and trying again never
    helps, so it is not "being installed". A deploy never makes a .git.
    setup_<dep>_env.sh uses the same rule.
    """
    real = os.path.realpath(dep_path)
    return (
        os.path.isdir(real)
        and os.path.exists(os.path.join(real, ".git"))
        and any(os.path.isfile(os.path.join(real, marker)) for marker in ("configure", ".ngargparser-build"))
        and not os.path.isfile(os.path.join(real, ".env"))
    )


def _not_configured(name, dep_path):
    """The message for dependency `name` while its clone at `dep_path` has no .env (_unconfigured_clone)."""
    return f"{name} at {os.path.realpath(dep_path)} is not configured yet: run ./configure there"


def _being_installed(dep_path, venv, follows_entry=False):
    """True when the tool at `dep_path` is being installed now (a redeploy), so no saved value stands in.

    Its real folder is there, is no git checkout (_unconfigured_clone), and is
    a tool folder (it has ./configure or .ngargparser-build), but it has no
    .env yet, or the virtualenv `venv` it publishes lies in it and has no
    bin/python yet. The deploy orchestrator removes the version folder,
    extracts the new one, and its install.sh makes .venv, then runs
    ./configure, which writes .env last. setup_<dep>_env.sh uses the same rule.

    A same-version redeploy removes the old files in any order, so ./configure
    and .ngargparser-build may go before the rest. With no .env, a folder that
    still has deploy/install.sh, .ngargparser/, .venv/ or src/ counts too,
    when `follows_entry` (its <DEP>_ENTRY follows its .env, so it publishes
    one): any other folder with src/ or .venv/ may be no tool, and never has a
    .env.
    """
    real = os.path.realpath(dep_path)
    if not os.path.isdir(real):
        return False
    tool = any(os.path.isfile(os.path.join(real, marker)) for marker in ("configure", ".ngargparser-build"))
    if not os.path.isfile(os.path.join(real, ".env")):
        if os.path.exists(os.path.join(real, ".git")):
            return False
        return tool or (
            follows_entry
            and (
                os.path.isfile(os.path.join(real, "deploy", "install.sh"))
                or any(os.path.isdir(os.path.join(real, folder)) for folder in (".ngargparser", ".venv", "src"))
            )
        )
    if not tool:
        return False
    if venv and os.path.isabs(venv):
        inside = os.path.realpath(venv)
        if inside.startswith(real.rstrip(os.sep) + os.sep) and not os.path.exists(os.path.join(venv, "bin", "python")):
            return True
    return False


def _installing(name, dep_path):
    """The message for dependency `name` while the tool at `dep_path` is being installed (_being_installed)."""
    return f"{name} is being installed at {os.path.realpath(dep_path)} right now (a redeploy in progress?); try again"


def _module_loaded(module):
    """True when LOADEDMODULES, which `module load` keeps, lists `module` or a version of it."""
    loaded = (os.environ.get("LOADEDMODULES") or "").split(":")
    return any(item == module or item.startswith(module + "/") for item in loaded)


def _in_a_venv(entry):
    """True when PATH entry `entry` is the bin/ folder of a virtualenv: its parent folder holds a pyvenv.cfg."""
    real = os.path.realpath(entry)
    return os.path.isfile(os.path.join(os.path.dirname(real), "pyvenv.cfg"))


def _without_caller_venv(path):
    """PATH text `path` without the bin/ folder of any virtualenv.

    That is the virtualenv this tool runs in, VIRTUAL_ENV (set by `uv run` or
    bin/activate) and the one this Python belongs to, and every other one on
    PATH (_in_a_venv): a tool that runs under `uv run` and starts this one
    leaves its own there too, behind this tool's. Other entries, empty ones
    included, keep their order.
    """
    venvs = [os.environ.get("VIRTUAL_ENV"), sys.prefix if sys.prefix != sys.base_prefix else None]
    bins = {os.path.realpath(os.path.join(venv, "bin")) for venv in venvs if venv}
    return os.pathsep.join(
        entry
        for entry in path.split(os.pathsep)
        if not (entry and (os.path.realpath(entry) in bins or _in_a_venv(entry)))
    )


def _published_now(own, key, cache):
    """The value of `key` (<DEP>_VENV or <DEP>_ENTRY) for dependency_command, read again now.

    load_app_env read it when this tool started, and the dependency may have
    been redeployed since: the deploy orchestrator removes a version folder
    and extracts the new one in its place, whose entry script may have
    another name. So a key listed in APP_KEYS_FROM_DEPS is read again from
    "<DEP>_PATH/.env", by load_app_env's rules (_read_from_dependency): none
    when that .env publishes none, and the value loaded at start-up when
    there is no such file, one written for another folder, or one that cannot
    be read or is not UTF-8 text (dependency_command does not use that one
    when it names another folder: _fallback_elsewhere). Any other key
    (set in paths.py, or a virtualenv ./configure found) is the loaded one.
    """
    followed = {item.strip() for item in (own.get(KEYS_FROM_DEPS) or "").split(",")}
    if key in followed:
        value = _read_from_dependency(own, key, cache)
        if value is not None:
            return value or None
    return os.environ.get(key)


def dependency_command(name, *args):
    """The command that runs dependency `name` with `args`, and the environment for it.

    Returns (argv, env), ready for subprocess:

        argv, env = dependency_command("pepx", "predict", "-j", str(job))
        subprocess.run(argv, env=env, check=True)

    <DEP> is `name` in capitals, with '-' as '_' (mhci-predictor: MHCI_PREDICTOR).
    The values are the ones load_app_env() put in os.environ; it runs first
    when it has not run in this process (core.set_pythonpath, which the entry
    script imports, runs it). <DEP>_VENV and <DEP>_ENTRY, when they follow the
    dependency's own .env, are read from it again at each call
    (_published_now), so a dependency redeployed while this tool runs is
    called as it is now. While that .env cannot be used, the values loaded
    at start-up stand in, unless one names a folder outside the dependency
    (an older version folder a `current` link no longer points to), or the
    dependency is a tool folder being installed (_being_installed): then it
    raises the redeploy error, "try again". A git checkout of a tool with no
    .env raises "not configured yet: run ./configure there" (_unconfigured_clone).

    argv is the dependency's Python, then its entry script, <DEP>_ENTRY, then
    `args`. The Python follows the rule setup_<dep>_env.sh uses for
    <DEP>_PYTHON: <dep>_python when paths.py sets it, else
    "<DEP>_VENV/bin/python", else python3. An entry that is not a Python
    script (_runs_with_python: a shell script, a program) runs by itself:
    argv is then <DEP>_ENTRY, then `args`. A <dep>_python set in paths.py
    still runs it. With neither a virtualenv nor <dep>_python, an executable
    entry with a #! line runs by itself too.

    env is a copy of os.environ, set up as activating the dependency's
    virtualenv does: VIRTUAL_ENV is <DEP>_VENV, its bin/ comes first on PATH,
    and PYTHONHOME is unset. So a dependency that runs `python` or a console
    script by name gets its own. A dependency with no virtualenv runs with the
    first python3 on PATH, and a warning on stderr; VIRTUAL_ENV and the bin/ of
    every virtualenv on PATH (this tool's, and one a tool that started this
    one left there) are left out, so it does not run inside another tool's
    virtualenv by accident. <DEP>_LIB_PATH, when set, goes in front of
    LD_LIBRARY_PATH, as setup_<dep>_env.sh does.

    An environment module (<dep>_module in paths.py) is not loaded: `module
    load` needs a shell. A warning on stderr says so, unless LOADEDMODULES lists
    it. Load it before the tool starts, or source setup_<dep>_env.sh in a shell
    and run the dependency from that shell.

    This tool's identity (APP_ROOT, APP_NAME) and what it publishes (APP_VENV,
    APP_ENTRY, APP_KEYS_FROM_DEPS) are left out of env: they describe this
    tool, not the dependency.

    This tool's own .env is read again at each call too. While it is missing
    or empty (a redeploy of this tool removes it for a moment), the values
    load_app_env loaded at start-up are used.

    Raises DependencyError, a RuntimeError, when this tool has no .env now
    nor had one at start-up, when its .env has no <DEP>_PATH (`name` is not a
    dependency, or ./configure has not run since paths.py declared it), when
    <DEP>_ENTRY is unset, when it is neither a Python script nor executable,
    or when it or the Python that runs it is not there (a redeploy in
    progress: try again).

    Each argument is one item of argv: text (str or bytes) or a path. An int
    or a float becomes its text (str), as in dependency_command("pepx",
    "-n", 5). A list or tuple of them raises TypeError, before anything is
    read: pass them unpacked, as in dependency_command("pepx", *args). So
    does None, a bool, or any other value, naming it.
    """
    items = []
    for number, arg in enumerate(args, start=1):
        if isinstance(arg, (list, tuple)):
            raise TypeError(
                f"dependency_command({name!r}, ...) got a {type(arg).__name__} as one argument; "
                f"pass the arguments unpacked: dependency_command({name!r}, *args)"
            )
        if isinstance(arg, (int, float)) and not isinstance(arg, bool):
            arg = str(arg)
        elif not isinstance(arg, (str, bytes, os.PathLike)):
            raise TypeError(
                f"dependency_command({name!r}, ...) got {arg!r} ({type(arg).__name__}) as argument {number}; "
                f"each argument must be text (str or bytes), a path, or a number"
            )
        items.append(arg)
    args = tuple(items)
    if not _env_loaded:
        load_app_env()
    prefix = name.replace("-", "_").upper()
    # A redeploy of this tool removes its .env for a moment (the orchestrator
    # removes the version folder and extracts the new one): the values loaded
    # at start-up stand in while it is missing or empty.
    own = _own_values() or _loaded_values
    if not own:
        raise DependencyError(
            f"cannot run {name}: this tool's .env is missing (a redeploy in progress?); "
            f"try again, or run ./configure in {PROJECT_ROOT}"
        )
    dep_path = own.get(f"{prefix}_PATH")
    if dep_path is None or prefix not in _declared_dependencies(own):
        raise DependencyError(_not_a_dependency(name, prefix, own))
    cache: Dict[str, Optional[Dict[str, Optional[str]]]] = {}
    venv = _published_now(own, f"{prefix}_VENV", cache)
    # A clone ./configure has not run in yet: trying again never helps.
    if _unconfigured_clone(dep_path):
        raise DependencyError(_not_configured(name, dep_path))
    # A tool folder being installed (a redeploy) has no .env or .venv yet:
    # what it had before is gone, so no saved value stands in for them.
    follows_entry = f"{prefix}_ENTRY" in _followed_keys(own)
    if _being_installed(dep_path, venv, follows_entry):
        raise DependencyError(_installing(name, dep_path))
    entry = _published_now(own, f"{prefix}_ENTRY", cache)
    # While the dependency's .env cannot be used (a redeploy removes it for a
    # moment), a value loaded at start-up that names another folder, the
    # version a `current` link pointed to then, is not run in its place.
    if not entry or _fallback_elsewhere(own, prefix, cache, os.environ):
        raise DependencyError(_no_entry(name, prefix, dep_path, follows_entry))
    # A relative entry (a <dep>_entry set in paths.py) is relative to the dependency's
    # folder, as the setup script reads it.
    if not os.path.isabs(entry):
        entry = os.path.join(dep_path, entry)
    module = os.environ.get(f"{prefix}_MODULE")
    if module and not _module_loaded(module):
        print(
            f"{name} needs the environment module {module}, which dependency_command cannot load: "
            f"load it before starting this tool, or call the dependency through setup_{prefix.lower()}_env.sh",
            file=sys.stderr,
        )
    # <dep>_python in paths.py wins, as in the setup script. Read from this
    # tool's .env, where ./configure writes it: os.environ may hold a derived one.
    explicit = own.get(f"{prefix}{PYTHON_SUFFIX}")
    # With no virtualenv and no <dep>_python, an executable entry with a #! line
    # runs by itself, with the Python that line names.
    by_itself = (
        not explicit
        and not venv
        and os.path.isfile(entry)
        and os.access(entry, os.X_OK)
        and _first_line(entry).startswith(b"#!")
    )
    with_python = not by_itself and (bool(explicit) or _runs_with_python(entry))
    if not with_python and os.path.isfile(entry) and not os.access(entry, os.X_OK):
        raise DependencyError(
            f"{name}'s entry {entry} is not a Python script and not executable: chmod +x it, "
            f"or point {prefix.lower()}_entry in paths.py at its start-up script"
        )
    env = {key: value for key, value in os.environ.items() if key not in IDENTITY_KEYS + FRAMEWORK_KEYS}
    python = explicit or None
    if venv:
        python = python or _dependency_python(venv)
        bin_dir = os.path.join(venv, "bin")
        env["VIRTUAL_ENV"] = venv
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}" if env.get("PATH") else bin_dir
        env.pop("PYTHONHOME", None)
    else:
        env.pop("VIRTUAL_ENV", None)
        if "PATH" in env:
            env["PATH"] = _without_caller_venv(env["PATH"])
        if with_python and not python:
            python = shutil.which(_dependency_python(None), path=env.get("PATH")) or _dependency_python(None)
            print(f"{name} publishes no virtualenv; running it with {python}", file=sys.stderr)
    lib_path = os.environ.get(f"{prefix}_LIB_PATH")
    if lib_path:
        current = env.get("LD_LIBRARY_PATH")
        env["LD_LIBRARY_PATH"] = f"{lib_path}:{current}" if current else lib_path
    command = [python, entry] if with_python else [entry]
    # The deploy orchestrator removes a version folder before it extracts the
    # new tarball, so a dependency redeployed while this tool runs is missing for
    # a moment: say so in one line, not with the FileNotFoundError subprocess
    # raises. The race stays: the folder can still go between this check and
    # the call, or while its Python starts (which then cannot import its own
    # modules).
    for program in command:
        if not _can_run(program, env.get("PATH")):
            raise DependencyError(_not_there(name, dep_path, program, python=with_python and program == python))
    return [*command, *args], env
