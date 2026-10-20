#! /usr/bin/env python3

import argparse
import ast
import atexit
import builtins
import difflib
import os
import shutil
import sys
import re
import glob
import importlib.util
import io
import shlex
import signal
import tempfile
import time
import tokenize
import traceback
from typing import Optional
try:
    import fcntl
except ImportError:  # not a POSIX system: paths.py is changed without the lock
    fcntl = None  # type: ignore[assignment]

# Colours only on a terminal, and never with NO_COLOR set to a value
# (no-color.org): a log file or a pipe gets the plain text. A closed stdout
# (`./configure >&-`) leaves sys.stdout None, and print() writes nothing there.
if sys.stdout is not None and sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
    RED, YELLOW, BOLD, DIM, RESET = "\033[91m", "\033[93m", "\033[1m", "\033[2m", "\033[0m"
else:
    RED = YELLOW = BOLD = DIM = RESET = ""

def to_devnull(stream):
    """Send whatever is still to be written to `stream`, whose reader has gone, to /dev/null."""
    try:
        fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(fd, stream.fileno())
        finally:
            os.close(fd)
    except (AttributeError, OSError, ValueError):  # no stream, or no file behind it
        pass

def print(*args, **kwargs):
    """builtins.print, quiet once the reader of its output has gone (`./configure | head -1`).

    The run goes on and writes every file, and exits with its own exit code:
    what it still prints goes to /dev/null (to_devnull). A BrokenPipeError
    stopped it half way, or Python's last flush of stdout made it exit 120.
    """
    try:
        builtins.print(*args, **kwargs)
    except BrokenPipeError:
        to_devnull(kwargs.get("file") or sys.stdout)

def flush_output():
    """Flush stdout and stderr before Python does on the way out, quietly when their reader has gone."""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.flush()
        except BrokenPipeError:
            to_devnull(stream)
            try:
                stream.flush()  # what was left in the buffer, now to /dev/null
            except OSError:
                pass
        except (OSError, ValueError):
            pass

def stop_on_signal(signum, _frame):
    """Exit as the shell reports signal `signum`, 128 + its number, through SystemExit.

    SIGTERM and SIGHUP otherwise end the run at once, and the files it staged
    next to their place (stage_writes) stayed behind as hidden ..env.*.tmp
    and .paths.py.*.tmp files: SystemExit runs the cleanup on the way out.
    """
    sys.exit(128 + signum)

def catch_stop_signals():
    """Stop through stop_on_signal on SIGTERM and SIGHUP, unless they are ignored (nohup ./configure)."""
    for name in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is None:  # not on this system
            continue
        try:
            if signal.getsignal(signum) == signal.SIG_DFL:
                signal.signal(signum, stop_on_signal)
        except (OSError, ValueError):  # not the main thread
            pass

def is_deployed_copy(folder):
    """True when tool folder `folder` is a build or deployed copy, which has no uv project to sync.

    It has .ngargparser-build, which `make build` writes into every build copy
    (set_pythonpath.py checks it too), and deploy/install.sh, which makes its
    .venv and then runs ./configure: the step to give there instead of uv sync.
    """
    return os.path.isfile(os.path.join(folder, ".ngargparser-build")) and os.path.isfile(
        os.path.join(folder, "deploy", "install.sh")
    )

try:
    from dotenv import dotenv_values
except ImportError:
    if is_deployed_copy(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))):
        sys.exit(
            "./configure needs python-dotenv. Run `bash deploy/install.sh`; "
            "it installs it into .venv, then runs ./configure."
        )
    sys.exit("./configure needs python-dotenv. Run `uv sync` first; it installs it into .venv.")

def load_dep_names():
    """dep_names.py, the rule of which names a dependency can have: `cli deps` reads the same file.

    The tool's copy sits next to this file, as src/core/dep_names.py, and is
    read by its path: `import dep_names` finds it only while this file runs
    as a script, not when a test loads it. ngargparser's own copy of this
    file, templates/configure.py, reads ngargparser/dep_names.py, which
    `cli generate` and `cli sync` copy into each tool. Without one, the run
    stops.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "dep_names.py")
    if not os.path.isfile(path) and os.path.basename(here) == "templates":
        path = os.path.join(os.path.dirname(here), "dep_names.py")
    spec = importlib.util.spec_from_file_location("dep_names", path) if os.path.isfile(path) else None
    if spec is None or spec.loader is None:
        sys.exit(f"./configure needs {path}, which is missing: run `cli sync` to restore it, then ./configure again.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

dep_names = load_dep_names()

CONFIG_PATH = "paths.py"
DOT_ENV_PATH = ".env"
# The .env key listing the keys this tool reads from a dependency's own .env
# (<TOOL>_VENV from its APP_VENV, <TOOL>_ENTRY from its APP_ENTRY), comma-separated.
# The tool's src/core/app_env.py reads those keys again from that .env at load time.
KEYS_FROM_DEPS = 'APP_KEYS_FROM_DEPS'
# Keys that describe the app itself rather than one of its dependencies.
APP_KEYS = {'APP_ROOT', 'APP_NAME', 'APP_VENV', 'APP_ENTRY', KEYS_FROM_DEPS}
# The settings of a dependency <tool> that paths.py holds as <tool>_<field>,
# each one set by a --<tool>-<field> flag (underscores as dashes). <tool>_python
# names the Python that runs it, whatever virtualenv is found.
TOOL_FIELDS = ("path", "venv", "module", "lib_path", "entry", "python")
# Conventional virtualenv directory names, in the order they're tried.
VENV_CANDIDATE_NAMES = (".venv", "venv", "env", ".virtualenv")
# The line after the shebang of every setup_<tool>_env.sh this script writes. A
# setup script without it was written by hand, so it is never deleted.
GENERATED_MARKER = "# Generated by ./configure from paths.py. Changes here are lost on the next ./configure."
# The first line of the .env this script writes, followed by the keys it wrote,
# comma-separated. The next run reads it to tell a key added by hand, which it
# names when it drops it, from one paths.py no longer has.
ENV_KEYS_MARKER = "# Written by ./configure from paths.py; keys added here by hand are removed on the next run. Keys: "
# The .env this script writes is not loaded into os.environ: a paths.py that
# reads os.environ (os.environ.get("X_PATH", "/opt/x")) would get back what the
# last run wrote, and never see an edit to its own default. Values a calling
# shell exports still reach it.
# The lock `cli deps` holds too while it changes paths.py (an flock on this
# file), and how long a run waits for it.
PATHS_LOCK = os.path.join(".ngargparser", "paths.lock")
PATHS_LOCK_WAIT = 30
# The open lock file while this run holds the lock: it ends with the process.
paths_lock_fd = None

def lock_paths():
    """Hold PATHS_LOCK until this run ends, so no other run changes paths.py while this one uses it.

    A run with --<tool>-<field> flags reads paths.py, changes it and moves the
    new file in: two at once (two ./configure runs, or one and a `cli deps add`)
    both read the old file, and the change moved in first was lost. A run
    without flags takes it too: it writes .env and the setup scripts from the
    paths.py it read, so one that read it just before a flag run changed it
    wrote the old values after that run. Taken before paths.py is first read,
    so this run reads the file as the one before it left it. Waits up to
    PATHS_LOCK_WAIT seconds, then exits 1 with nothing changed.

    A new lock file is made readable and writable for everyone (0666, whatever
    the umask), as `cli deps` makes it, so the next user can take it too. One
    that is there but cannot be opened stops the run with one line, nothing
    changed: going on without it lost changes. The run goes on without the lock
    only where the tool folder cannot be written (a read-only deployed copy,
    where writing paths.py or .env fails with its own message, and a run with
    nothing to change still works), on a file system without flock, and
    without fcntl. The same rule as `cli deps` (_PathsLock in ngargparser's
    cli.py).
    """
    global paths_lock_fd
    if fcntl is None:
        return
    created = False
    try:
        os.makedirs(os.path.dirname(PATHS_LOCK), exist_ok=True)
        try:
            fd = os.open(PATHS_LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o666)
            created = True
        except FileExistsError:
            try:
                fd = os.open(PATHS_LOCK, os.O_RDWR)
            except PermissionError:  # another user's lock file: flock needs only read access
                fd = os.open(PATHS_LOCK, os.O_RDONLY)
    except OSError as e:
        cannot_lock(e, os.path.lexists(PATHS_LOCK))
        return
    if created:
        try:
            os.chmod(PATHS_LOCK, 0o666)  # a+rw, whatever the umask took away
        except OSError:
            pass
    start = time.monotonic()
    deadline = start + PATHS_LOCK_WAIT
    said = False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                paths_lock_fd = fd
                write_lock_pid(fd)
                return
            except BlockingIOError:
                pass
            except OSError:  # a file system without flock: as without the lock file
                os.close(fd)
                return
            now = time.monotonic()
            if now >= deadline:
                os.close(fd)
                print(
                    f"{RED}✗{RESET} Another ./configure or cli deps is changing paths.py: {PATHS_LOCK} "
                    f"was still locked after {PATHS_LOCK_WAIT} s. Nothing was changed; try again when it is done."
                )
                sys.exit(1)
            if not said and now - start >= 1:
                said = True
                holder = lock_holder()
                if holder:
                    print(f"Waiting for pid {holder} (cli deps or ./configure) to finish with paths.py...")
                else:
                    print("Waiting for another cli deps or ./configure to finish with paths.py...")
                flush_output()
            time.sleep(0.05)
    except KeyboardInterrupt:
        print(f"{RED}✗{RESET} Stopped while waiting for paths.py. Nothing was changed.")
        sys.exit(130)

def write_lock_pid(fd):
    """Write this run's pid into the lock file it holds (`fd`), for the message of a run that waits.

    `cli deps` does the same (_PathsLock in ngargparser's cli.py). A lock file
    only its owner may write keeps the pid it holds.
    """
    try:
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
    except (OSError, AttributeError):
        pass

def lock_holder():
    """The pid in PATHS_LOCK, as text, when that process is running; else ""."""
    try:
        with open(PATHS_LOCK, encoding="utf-8", errors="replace") as f:
            pid = f.readline().strip()
    except OSError:
        return ""
    if not pid.isdigit() or int(pid) in (0, os.getpid()):
        return ""
    try:
        os.kill(int(pid), 0)
    except PermissionError:  # another user's process
        return pid
    except (OSError, OverflowError):
        return ""
    return pid

def cannot_lock(error, exists):
    """Stop the run, nothing changed, because PATHS_LOCK cannot be opened or made (`error`).

    `exists` says whether the lock file is there. Says who can fix it, in the
    words `cli deps` uses (_LockUnusable in ngargparser's cli.py): a folder
    this user cannot write is made writable for its owner, or for a group
    they share, never for everyone. Returns instead, so the run goes on
    without the lock, when this user cannot write the tool folder: a
    read-only deployed copy.
    """
    if not os.access(".", os.W_OK):
        return
    state = os.path.dirname(PATHS_LOCK)
    why = error.strerror or str(error)
    if exists:
        message = f"Cannot open {PATHS_LOCK} ({why}): ask its owner to run chmod a+rw {PATHS_LOCK}."
    elif os.path.lexists(state) and not os.path.isdir(state):
        message = (
            f"Cannot create {PATHS_LOCK}: {state} is a file, not a folder. "
            f"Move it away (mv {state} {state}.old), then run the command again."
        )
    else:
        try:
            mine = os.stat(state).st_uid == os.getuid()
        except (OSError, AttributeError):
            mine = True
        if mine:
            fix = f"{state} is read-only for you; run chmod u+rwx {state}"
        else:
            fix = f"ask the owner of {state} to run chmod g+rwx {state}, which lets a group you share write there"
        message = f"Cannot create {PATHS_LOCK} ({why}): {fix}."
    print(f"{RED}✗{RESET} {message} Nothing was changed.")
    sys.exit(1)

def report_syntax_error(path, error):
    """Say where paths.py stops parsing, with the offending line, and exit 1."""
    line = (error.text or "").rstrip("\r\n")
    if not line and error.lineno:
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                lines = f.read().splitlines()
            line = lines[error.lineno - 1] if error.lineno <= len(lines) else ""
        except OSError:
            pass
    where = f"{path} line {error.lineno}" if error.lineno else path
    print(f"{RED}✗{RESET} {where}: {error.msg}")
    if line.strip():
        print(f"    {line.strip()}")
    sys.exit(1)

def report_exec_error(path, error):
    """Say which line of paths.py raised `error` while it ran, with that line, and exit 1."""
    frames = [
        frame for frame in traceback.extract_tb(error.__traceback__)
        if os.path.abspath(frame.filename) == os.path.abspath(path)
    ]
    frame = frames[-1] if frames else None
    where = f"{path} line {frame.lineno}" if frame and frame.lineno else path
    print(f"{RED}✗{RESET} {where}: {type(error).__name__}: {error}")
    line = (frame.line or "").strip() if frame else ""
    if line:
        print(f"    {line}")
    sys.exit(1)

def load_config(path):
    if not os.path.exists(path):
        # Missing paths.py is a valid steady state for tools with no external
        # IEDB-tool dependencies (e.g. standalone scorers like pepsysco).
        print(f"{DIM}ℹ  No '{path}' found: treating as a tool with no external dependencies.{RESET}")
        return {}
    
    # Run 'path' as Python and keep the names it sets in 'config'. Compiled
    # from its text on every run, never through the import system: that
    # writes __pycache__/paths.*.pyc and reads it back while the file's size
    # and modification time match, so a same-size edit within a second (two
    # flags in a row) was not seen. The bytes go to compile() as they are,
    # so a UTF-8 byte order mark, a coding line and CRLF line endings work
    # as they do in an import.
    config = {"__name__": "config", "__file__": path, "__builtins__": builtins}
    try:
        with open(path, "rb") as f:
            source = f.read()
        exec(compile(source, path, "exec", dont_inherit=True), config)
    except SyntaxError as e:
        if e.filename and os.path.abspath(e.filename) != os.path.abspath(path):
            report_exec_error(path, e)  # in a module paths.py imports
        report_syntax_error(path, e)
    except Exception as e:  # an import that fails, a name paths.py never defined, ...
        report_exec_error(path, e)

    # Plain values only: everything returned here is written to .env, so an
    # import such as `from pathlib import Path` must not become PATH=<class ...>.
    # Path objects (APP_ROOT = Path(__file__).parent) are kept as text.
    return {
        k: os.fspath(v) if isinstance(v, os.PathLike) else v
        for k, v in config.items()
        if not k.startswith("__") and (v is None or isinstance(v, (str, int, float, os.PathLike)))
    }

def detect_dependency_tools(config):
    """
    Detect all dependency tools from the config by finding variables ending with '_path'.
    Only detects main tool paths, ignoring optional paths like _lib_path.
    Returns a dictionary mapping tool names to their variable prefixes.

    A name `cli deps add` refuses (dep_names.invalid_dep_name), written into
    paths.py by hand, stops the run with the same reason, nothing changed:
    ld_library_path would export LD_LIBRARY_PATH, and app_path would replace
    this tool's own APP_VENV and APP_ENTRY.
    """
    tools = {}
    
    for key in config.keys():
        if key.endswith('_path'):
            # Extract the tool prefix (everything before '_path')
            tool_prefix = key[:-5]  # Remove '_path' suffix
            
            # Skip optional path variables (lib_path, venv, module are not main tools)
            if tool_prefix.endswith(dep_names.DEP_NAME_FIELD_SUFFIXES):
                continue
            
            # Check if this tool has the required configuration structure
            # (at minimum, it should have a _path variable)
            if f"{tool_prefix}_path" in config:
                tools[tool_prefix] = tool_prefix

    refused = [(name, dep_names.invalid_dep_name(name)) for name in tools]
    refused = [(name, reason) for name, reason in refused if reason]
    if refused:
        for name, reason in refused:
            print(f"{RED}✗{RESET} {CONFIG_PATH} declares {name}, a name cli deps refuses: {reason}")
        print("  Nothing was changed.")
        sys.exit(1)
    return tools

def report_miscased_names(config, detected_tools):
    """Stop the run, nothing changed, when paths.py spells a name ./configure reads in another case.

    ./configure reads APP_ROOT, APP_NAME, APP_VENV, APP_ENTRY and
    APP_KEYS_FROM_DEPS in capitals, and the settings of a dependency as
    <tool>_<field>, all in lower case. app_entry or pepx_VENV was not read,
    and went into .env next to the key of the right name (PEPX_VENV twice).
    One line names each one with its right spelling.
    """
    right = {key.lower(): key for key in APP_KEYS}
    right.update(
        (f"{tool_prefix}_{field}".lower(), f"{tool_prefix}_{field}")
        for tool_prefix in detected_tools
        for field in TOOL_FIELDS
    )
    wrong = [(name, right[name.lower()]) for name in config if name.lower() in right and name != right[name.lower()]]
    if not wrong:
        return
    them = "them" if len(wrong) > 1 else "it"
    print(
        f"{RED}✗{RESET} {CONFIG_PATH} sets {and_list([name for name, _right in wrong])}, which ./configure "
        f"does not read: spell {them} {and_list([spelling for _name, spelling in wrong])}. Nothing was changed."
    )
    sys.exit(1)

def and_list(items):
    """`items` as "a", "a and b", or "a, b and c"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"

def is_usable_venv(path):
    """True when `path` really is a virtualenv.

    Both markers are required: pyvenv.cfg identifies a virtualenv (PEP 405),
    and bin/activate is in every one that venv, virtualenv and uv make. An
    empty or half-deleted directory fails on one or the other.
    """
    return os.path.isfile(os.path.join(path, "pyvenv.cfg")) and os.path.isfile(
        os.path.join(path, "bin", "activate")
    )

def find_bundled_venvs(root):
    """Return the conventional virtualenvs bundled inside `root`.

    Convention, not discovery: only well-known directory names are tried, so
    a scan can never bind a tool to some unrelated directory that happens to
    contain an 'activate' file. Every match is returned, because two matches
    is a question for the user rather than something to guess at.
    """
    return [
        os.path.join(root, name)
        for name in VENV_CANDIDATE_NAMES
        if is_usable_venv(os.path.join(root, name))
    ]

# The .env files this run could not read, as absolute paths: each is named once.
unreadable_envs = set()

def warn_unreadable_env(env_path, error):
    """Name .env file `env_path`, which could not be read (`error`), in one warning line, once per run."""
    where = os.path.abspath(env_path)
    if where in unreadable_envs:
        return
    unreadable_envs.add(where)
    not_utf8 = isinstance(error, UnicodeDecodeError)
    why = "it is not UTF-8 text" if not_utf8 else (error.strerror or str(error))
    if where == os.path.abspath(DOT_ENV_PATH):
        then = "./configure writes it again from paths.py."
    elif not_utf8:  # nor by the setup script or app_env.py: there it counts as missing too
        then = (
            f"Its values are not used: save it as UTF-8, or run ./configure in "
            f"{os.path.dirname(env_path) or '.'} to write it again."
        )
    else:  # a permission it lacks, say: as missing, in the setup script and app_env.py too
        then = "Its values are not used: ask its owner to make it readable."
    print(f"{YELLOW}⚠{RESET}  Could not read {env_path}: {why}. {then}")

def read_env_values(env_path):
    """The values python-dotenv reads from .env file `env_path`, or None when it cannot be read.

    A .env that is not UTF-8 text (python-dotenv's encoding), or that cannot be
    opened, is named in a warning (warn_unreadable_env) and publishes nothing,
    like a missing one.
    """
    try:
        return dotenv_values(env_path)
    except (OSError, UnicodeDecodeError) as e:
        warn_unreadable_env(env_path, e)
        return None

def declared_venv(tool_path):
    """Read a dependency's own APP_VENV out of its .env, if it publishes one.

    Parsed as key/value text, never executed. The tool's paths.py is a Python
    module, so reading that instead would run another project's code inside
    this configure run. A tool with no .env, or one that predates APP_VENV,
    simply declares nothing.
    """
    env_path = os.path.join(tool_path, DOT_ENV_PATH)
    if not os.path.isfile(env_path):
        return None
    values = read_env_values(env_path)
    return (values.get("APP_VENV") or None) if values is not None else None

def declared_entry(tool_path):
    """Read a dependency's own APP_ENTRY (its start-up script) out of its .env.

    Same rules as declared_venv: parsed as key/value text, never executed.
    Returns the script as an absolute path, or None when the tool publishes
    nothing (no .env, or a tool that predates APP_ENTRY).

    A relative APP_ENTRY is joined to the folder that .env describes: its
    APP_ROOT, else the real folder behind `tool_path`. Through a `current` link,
    that is the version folder its APP_VENV names too, so a redeploy that moves
    the link never pairs one version's virtualenv with another's script.
    """
    env_path = os.path.join(tool_path, DOT_ENV_PATH)
    if not os.path.isfile(env_path):
        return None
    values = read_env_values(env_path)
    if values is None:
        return None
    entry = values.get("APP_ENTRY")
    if not entry:
        return None
    if os.path.isabs(entry):
        return entry
    root = values.get("APP_ROOT")
    if not root or not os.path.isabs(root):
        root = os.path.realpath(tool_path)
    return os.path.join(root, entry)

def env_written_for(tool_path):
    """The folder a dependency's .env names as APP_ROOT, when that is another folder; else None.

    ./configure writes the absolute folder it ran in as APP_ROOT. A dependency
    copied or moved after that keeps the old .env, whose APP_VENV and APP_ENTRY
    name the old folder's files, so a caller would run the original without a
    word. Such a .env publishes nothing until ./configure runs in the
    dependency's folder again. Through a `current` link, APP_ROOT names the
    real folder behind it, which counts as the same one.
    """
    env_path = os.path.join(tool_path, DOT_ENV_PATH)
    if not os.path.isfile(env_path):
        return None
    values = read_env_values(env_path)
    if values is None:
        return None
    root = values.get("APP_ROOT")
    if root and os.path.isabs(root) and os.path.realpath(root) != os.path.realpath(tool_path):
        return root
    return None

def report_stale_envs(config, detected_tools):
    """Warn about each dependency whose .env was written for another folder; return those dependencies.

    Their published values (APP_VENV, APP_ENTRY) are not used, here, in the
    setup script or in app_env.py: resolve_tool_venvs and resolve_tool_entries
    look in the dependency's folder itself instead.
    """
    stale = set()
    for tool_prefix in detected_tools:
        tool_path = config.get(f"{tool_prefix}_path")
        if path_is_unset(tool_path) or not os.path.isdir(tool_path):
            continue
        root = env_written_for(tool_path)
        if root:
            stale.add(tool_prefix)
            print(
                f"{YELLOW}⚠{RESET}  {tool_prefix}'s .env was written for {root}: "
                f"run ./configure in {tool_path} first; its values are not used until then."
            )
    return stale

def entry_in_folder(tool_path):
    """The start-up script a dependency's own folder names, as an absolute path, or None.

    For a dependency whose .env cannot be used (see env_written_for): `entry`
    under [tool.ngargparser] in its pyproject.toml when that file exists, else
    its single src/run_*.py. pyproject.toml is read as TOML, never run.
    """
    root = os.path.realpath(tool_path)
    declared = pyproject_value("entry", os.path.join(root, "pyproject.toml"))
    if declared and os.path.isfile(os.path.join(root, declared)):
        return os.path.join(root, declared)
    scripts = sorted(glob.glob(os.path.join(glob.escape(root), "src", "run_*.py")))
    return scripts[0] if len(scripts) == 1 else None

def resolve_tool_entries(config, detected_tools, stale=()):
    """Record each dependency's published start-up script as <tool>_entry.

    In memory only, like a resolved virtualenv: paths.py is never written, and
    the value lands in .env and setup_<tool>_env.sh as <TOOL>_ENTRY. A caller
    then runs `"$<TOOL>_PYTHON" "$<TOOL>_ENTRY" predict ...` instead of
    hard-coding the dependency's script name, which can change between its
    versions. A <tool>_entry set in paths.py wins, like an explicit <tool>_venv.
    For a tool in `stale`, whose .env was written for another folder, the
    script its own folder names is used instead (entry_in_folder).
    """
    for tool_prefix in detected_tools:
        if config.get(f"{tool_prefix}_entry"):
            continue  # an explicit setting always wins
        tool_path = config.get(f"{tool_prefix}_path")
        if not tool_path or not os.path.isdir(tool_path):
            continue
        if tool_prefix in stale:
            entry = entry_in_folder(tool_path)
            if entry:
                config[f"{tool_prefix}_entry"] = entry
                print(f"* {tool_prefix}: using start-up script found at {entry}")
            continue
        entry = declared_entry(tool_path)
        if entry:
            config[f"{tool_prefix}_entry"] = entry
            print(f"* {tool_prefix}: start-up script declared in its .env: {entry}")

def own_entry(app_root):
    """The start-up script this project publishes as APP_ENTRY, relative to app_root.

    Three sources, in order. First `entry` in the [tool.ngargparser] table of
    pyproject.toml, which ships in the tarball, so a deployed copy with several
    src/run_*.py scripts still publishes one, and an edit to it takes effect on
    the next run; an entry naming a missing file warns and is skipped. Next, a
    value already in this project's .env file, when it names a file (older
    tools set it there by hand); it is read from the file, not os.environ,
    because a tool that started this one may have passed its own APP_ENTRY
    down. Last, the single src/run_*.py, the same rule as the build's startup
    check. With none of them, nothing is published.
    """
    scripts = sorted(glob.glob(os.path.join(app_root, "src", "run_*.py")))
    single = os.path.relpath(scripts[0], app_root) if len(scripts) == 1 else None
    declared = pyproject_value("entry", os.path.join(app_root, "pyproject.toml"))
    if declared and os.path.isfile(os.path.join(app_root, declared)):
        return declared
    kept = None
    env_path = os.path.join(app_root, DOT_ENV_PATH)
    if os.path.isfile(env_path):
        kept = (read_env_values(env_path) or {}).get("APP_ENTRY")
        if not (kept and os.path.isfile(os.path.join(app_root, kept))):
            kept = None
    if declared:
        fallback = kept or single  # what is published below
        print(
            f"{YELLOW}⚠{RESET}  [tool.ngargparser] entry in pyproject.toml names '{declared}', which does not exist."
            + (f" Publishing {fallback} instead." if fallback else " Publishing nothing.")
        )
    if kept:
        return kept
    if single:
        return single
    if declared:
        return None  # the warning above said so; entry is already set
    print(
        f"{YELLOW}⚠{RESET}  No APP_ENTRY published: found {len(scripts)} src/run_*.py scripts. "
        f"Set '{BOLD}entry = \"src/<script>\"{RESET}' under [tool.ngargparser] in pyproject.toml "
        f"so tools that depend on this one can find it."
    )
    return None

def venv_made_for(venv):
    """The folder virtualenv `venv` was made in, when that is another folder; else None.

    A virtualenv records its own folder: bin/activate sets VIRTUAL_ENV to it,
    and `python -m venv` also names it in the `command` line of pyvenv.cfg. A
    moved or copied one keeps the old folder, so activating it puts the old
    folder's bin/ first on PATH: `python` is then some other Python, without
    this tool's packages. A value worked out when activate is sourced (a
    relocatable virtualenv) names no folder and is not checked.
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

def resolve_tool_venvs(config, detected_tools, published=None, stale=()):
    """Fill in a tool's virtualenv when the user hasn't set one explicitly.

    Two sources, in order: what the tool publishes about itself in its own
    .env, then a conventional virtualenv directory bundled inside the tool.

    Resolved in memory only -- paths.py keeps its None, so the same file
    stays portable across hosts (dev laptop, dev server, SDSC), each
    resolving locally at configure time. A tool whose venv lives elsewhere
    (pyenv, conda) and that publishes nothing simply isn't matched here;
    that's what the explicit <tool>_venv setting is for.

    Returns the tools left unresolved because their bundled virtualenv was
    ambiguous, so the caller can skip the generic "no virtualenv" warning for
    them: "has more than one" already said it, more precisely. Tools whose
    virtualenv came from their own .env are added to the `published` set, if
    one is given: their setup script reads it again when it is sourced. The
    .env of a tool in `stale` was written for another folder, so only the
    second source counts for it.
    """
    ambiguous = set()
    for tool_prefix in detected_tools:
        if config.get(f"{tool_prefix}_venv"):
            continue  # an explicit setting always wins
        tool_path = config.get(f"{tool_prefix}_path")
        if not tool_path:
            continue

        declared = None if tool_prefix in stale else declared_venv(tool_path)
        if declared:
            # Adopted even when it looks wrong, for the same reason a missing
            # path doesn't block: it may be correct on the deploy target.
            config[f"{tool_prefix}_venv"] = declared
            if published is not None:
                published.add(tool_prefix)
            print(f"* {tool_prefix}: using virtualenv declared in its .env: {declared}")
            if os.path.isdir(declared) and not is_usable_venv(declared):
                print(
                    f"{YELLOW}⚠{RESET}  {BOLD}{tool_prefix}{RESET} declares "
                    f"'{declared}', which has no pyvenv.cfg. Using it anyway."
                )
            continue

        candidates = find_bundled_venvs(tool_path)
        if len(candidates) == 1:
            config[f"{tool_prefix}_venv"] = candidates[0]
            print(f"* {tool_prefix}: using virtualenv found at {candidates[0]}")
            # Its python still runs, but sourcing its activate script points
            # PATH at the folder it was made in.
            made_for = venv_made_for(candidates[0])
            if made_for:
                remake = "bash deploy/install.sh" if is_deployed_copy(tool_path) else "uv sync"
                print(
                    f"{YELLOW}⚠{RESET}  {candidates[0]} was made for {made_for}, so activating it does not work. "
                    f"Recreate it in {tool_path} (rm -r {os.path.basename(candidates[0])} && {remake}), or run "
                    f'"${tool_prefix.upper()}_VENV/bin/python" directly.'
                )
        elif candidates:
            ambiguous.add(tool_prefix)
            flag_base = tool_prefix.replace("_", "-")
            names = [os.path.basename(c) for c in candidates]
            remove, why = which_venvs_to_remove(names)
            print(
                f"{YELLOW}⚠{RESET}  {BOLD}{tool_prefix}{RESET} has more than one "
                f"virtualenv in '{tool_path}' ({', '.join(names)}). Not guessing: pick one with "
                f"'{BOLD}./configure --{flag_base}-venv=<path>{RESET}', or remove {remove} there{why}. "
                f"Until then, {tool_prefix} runs with the system Python."
            )

    return ambiguous

def which_venvs_to_remove(names):
    """Which of the bundled virtualenvs `names` (folder names) to remove, so that one is left, and why.

    Returns (what, why). With .venv among them, the others are named: uv sync,
    the ./configure launcher and deploy/install.sh use .venv.
    """
    if ".venv" in names:
        return ", ".join(name for name in names if name != ".venv"), " (uv sync uses .venv)"
    return "all but one of them", ""

class QuietArgumentParser(argparse.ArgumentParser):
    """argparse.ArgumentParser, quiet as print() here is when its output is closed or its reader has gone.

    Python 3.11+ argparse already ignores both; before that, `./configure --help | :`
    ended in a BrokenPipeError traceback.
    """

    def _print_message(self, message, file=None):
        try:
            super()._print_message(message, file)
        except BrokenPipeError:
            to_devnull(file or sys.stderr)
        except (AttributeError, OSError):  # no stream to write to
            pass

def build_arg_parser(config, detected_tools):
    """
    Build an argparse parser with one --<tool>-path/-venv/-module/-lib-path/-entry/-python
    flag per field of every tool already declared in paths.py. Flags let you
    set a dependency's fields without hand-editing paths.py; a flag only
    exists for a tool `cli deps add` already declared.
    """
    parser = QuietArgumentParser(
        prog="./configure",
        description="Regenerate .env and per-tool setup scripts from paths.py. "
                     "Optionally set a dependency's fields without hand-editing paths.py.",
    )
    if not detected_tools:
        parser.epilog = "No dependencies declared in paths.py yet: run `cli deps add <tool>` first."
        return parser

    for tool_prefix in detected_tools:
        flag_base = tool_prefix.replace("_", "-")
        for suffix in TOOL_FIELDS:
            var_name = f"{tool_prefix}_{suffix}"
            current = config.get(var_name)
            parser.add_argument(
                f"--{flag_base}-{suffix.replace('_', '-')}",
                default=None,
                help=f"Set {var_name} (currently: {current!r}).",
            )
    return parser

def unknown_flag_hint(flag, detected_tools):
    """What `flag`, an option ./configure does not have (without its =value), may have meant, or None.

    The dash form of a paths.py spelling (--alpha_path: --alpha-path), else, for
    a --<name>-<field> flag, the closest declared dependency (--alpah-path:
    --alpha-path) or the command that declares <name> (--pepx-path before
    `cli deps add pepx`), else the closest flag (--alpha-pth: --alpha-path).
    """
    flags = [
        f"--{tool_prefix.replace('_', '-')}-{suffix.replace('_', '-')}"
        for tool_prefix in detected_tools
        for suffix in TOOL_FIELDS
    ]
    dashed = "--" + flag.lstrip("-").replace("_", "-")
    if dashed in flags:
        return f"did you mean {dashed}?"
    body = dashed[2:]
    for field in sorted((suffix.replace("_", "-") for suffix in TOOL_FIELDS), key=len, reverse=True):
        if body.endswith(f"-{field}") and len(body) > len(field) + 1:
            name = body[: -len(field) - 1]
            close = difflib.get_close_matches(name.replace("-", "_"), list(detected_tools), n=1)
            if close:
                return f"did you mean --{close[0].replace('_', '-')}-{field}?"
            return f"{name} is not declared in paths.py: cli deps add {name}"
    close = difflib.get_close_matches(dashed, flags + ["--help"], n=1)
    return f"did you mean {close[0]}?" if close else None

def report_unknown_args(parser, detected_tools, unknown):
    """Stop with an error that names `unknown`, the arguments ./configure does not take; exit 2.

    When each flag among them gets a hint (unknown_flag_hint), the error is one
    line that ends with the hints, as a tool's own parser gives them, without
    the usage line, which lists every flag of every dependency. Otherwise it is
    argparse's usage line and error, as before.
    """
    message = f"unrecognized arguments: {' '.join(unknown)}"
    hints = []
    value_next = False  # the value of an unknown flag given as `--flag value`
    for arg in unknown:
        if arg.startswith("-") and len(arg) > 1:
            flag, equals, _value = arg.partition("=")
            hint = unknown_flag_hint(flag, detected_tools)
            if hint is None:
                break
            hints.append(hint)
            value_next = not equals
        elif value_next:
            value_next = False
        else:
            break
    else:
        if hints:
            print(f"{parser.prog}: error: {message}; {'; '.join(dict.fromkeys(hints))}", file=sys.stderr)
            sys.exit(2)
    parser.error(message)

def find_assignment(tree, var_name):
    """Find the lines of paths.py that a --<tool>-<field> flag may rewrite.

    Only a lone top-level `name = ...` or `name: T = ...` qualifies, and only
    when nothing else in the file binds the name: replacing those lines must
    change the value Python ends up with, and nothing else. Returns
    (first_line, last_line, None), or (None, None, reason) when the variable
    can't be rewritten safely.
    """
    bindings = [
        node for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == var_name)
        or (isinstance(node, ast.alias) and (node.asname or node.name.split(".")[0]) == var_name)
        or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler))
            and node.name == var_name)
    ]
    if not bindings:
        return None, None, "paths.py does not assign it"
    if len(bindings) > 1:
        return None, None, "paths.py assigns it more than once"

    binding = bindings[0]
    stmt = next(s for s in tree.body if any(node is binding for node in ast.walk(s)))
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and stmt.targets[0] is binding:
        pass
    elif isinstance(stmt, ast.AnnAssign) and stmt.target is binding and stmt.value is not None:
        pass
    elif isinstance(stmt, ast.Assign):  # a_path = b_path = ..., or a_path, b_path = ...
        return None, None, "paths.py assigns it together with other names"
    elif hasattr(stmt, "body"):
        return None, None, "paths.py assigns it inside a block, not at the top level"
    else:
        return None, None, f"paths.py does not assign it with '{var_name} = ...'"

    # a_path = None; b_path = None: replacing the line would drop b_path.
    if any(s is not stmt and s.lineno <= stmt.end_lineno and s.end_lineno >= stmt.lineno for s in tree.body):
        return None, None, "paths.py has another statement on the same line"
    return stmt.lineno, stmt.end_lineno, None

def trailing_comment(tree, lines, last):
    """The comment that ends line `last` of paths.py, with the spaces before it, or "" without one.

    `lines` are the lines of the text `tree` was parsed from. Only one
    statement ends on that line (find_assignment checks), and the comment
    comes after it; ast gives where it ends in UTF-8 bytes.
    """
    stmt = next((s for s in tree.body if s.end_lineno == last), None)
    if stmt is None or stmt.end_col_offset is None:
        return ""
    rest = lines[last - 1].encode("utf-8")[stmt.end_col_offset:].decode("utf-8", errors="replace")
    return rest if rest.lstrip().startswith("#") else ""

def apply_paths_overrides(config, args, config_path):
    """
    Apply any --<tool>-<field> flags to `config`, and return the PathsUpdate
    that persists them back into `config_path` (paths.py), or None when no
    flag changes anything. main() stages it after .env and the setup scripts,
    and moves it in last, so a run that cannot write one of them leaves
    paths.py as it was.

    Each flag replaces the lines of its variable's assignment, or adds the
    variable right after its tool's <tool>_path line when paths.py leaves it
    out, and the old file is kept as paths.py.bak. When any variable is
    assigned in a way that can't be rewritten safely (chained, inside an if),
    or paths.py cannot be opened for writing, nothing is written and
    ./configure exits 1.
    """
    overrides = {
        var_name: value
        for var_name, value in vars(args).items()
        if value is not None and config.get(var_name) != value
    }
    if not overrides:
        return None

    # utf-8-sig drops a byte order mark, which Python reads past when it runs
    # paths.py but ast.parse does not. It is put back when paths.py is written.
    # newline="" keeps each line ending as it is, \r\n in a file saved on Windows.
    # A paths.py in another encoding, which its coding line names (PEP 263),
    # runs as it is, but is not rewritten: the run stops with nothing changed.
    try:
        with open(config_path, "r", encoding="utf-8-sig", newline="") as f:
            content = f.read()
    except UnicodeDecodeError:
        encoding = coding_of(config_path)
        print(
            f"{RED}✗{RESET} Can't update {config_path}: it is {encoding}, and ./configure rewrites UTF-8 only. "
            f"Save it as UTF-8 (with no coding line, or coding: utf-8), or edit it by hand. Nothing was changed."
        )
        sys.exit(1)
    with open(config_path, "rb") as raw:
        bom = "\ufeff" if raw.read(3) == b"\xef\xbb\xbf" else ""
    tree = ast.parse(content, filename=config_path)

    spans = {}
    added = {}  # a field paths.py leaves out: inserted after its tool's <tool>_path line
    problems = []
    for var_name in overrides:
        first, last, reason = find_assignment(tree, var_name)
        if reason == "paths.py does not assign it":
            tool_path = re.sub(r"_(lib_path|path|venv|module|entry|python)$", "", var_name) + "_path"
            _first, anchor_last, anchor_reason = find_assignment(tree, tool_path)
            if not anchor_reason and tool_path != var_name:
                added[var_name] = anchor_last
                continue
        if reason:
            problems.append((var_name, reason))
        else:
            spans[var_name] = (first, last)
    if problems:
        for var_name, reason in problems:
            print(
                f"{RED}✗{RESET} Can't set {BOLD}{var_name}{RESET}: {reason}. "
                f"Edit {config_path} by hand, then re-run ./configure."
            )
        sys.exit(1)

    # Bottom to top, so an edit never moves the lines of one still to come. An
    # inserted line goes right after its anchor, so it sorts just past it.
    edits = [(first - 1, last, var_name) for var_name, (first, last) in spans.items()]
    edits += [(anchor_last, anchor_last, var_name) for var_name, anchor_last in added.items()]
    # Each line keeps its own ending, split where Python ends a line, so a
    # rewrite changes only the lines it replaces: a CRLF paths.py stays CRLF.
    parts = re.split(r"(\r\n|\r|\n)", content)
    lines, endings = parts[0::2], parts[1::2] + [""]
    eol = next((ending for ending in endings if ending), "\n")
    # A comment after the value (pepx_path = None  # where pepx lives) stays on the new line.
    comments = {var_name: trailing_comment(tree, lines, last) for var_name, (_first, last) in spans.items()}
    for start, end, var_name in sorted(edits, reverse=True):
        line = f"{var_name}={overrides[var_name]!r}{comments.get(var_name, '')}"
        if start < end:  # replaces lines start+1 to end, with the ending of the last
            lines[start:end] = [line]
            endings[start:end] = [endings[end - 1]]
        elif endings[start - 1]:  # inserted after line `start`, with its ending
            lines[start:start] = [line]
            endings[start:start] = [endings[start - 1]]
        else:  # after the last line, which had no ending
            endings[start - 1] = eol
            lines[start:start] = [line]
            endings[start:start] = [""]
    new_content = "".join(line + ending for line, ending in zip(lines, endings))
    try:
        ast.parse(new_content, filename=config_path)
    except SyntaxError as e:
        print(
            f"{RED}✗{RESET} Can't update {config_path}: the result would not parse "
            f"({e.msg}, line {e.lineno}). {config_path} is unchanged."
        )
        sys.exit(1)

    try:
        # A read-only paths.py stops the run here, before anything is written,
        # with .env, the setup scripts and the backup of an earlier run
        # untouched. Opened only to check: paths.py is replaced, never written
        # into (see PathsUpdate).
        with open(config_path, "r+", encoding="utf-8", newline=""):
            pass
    except OSError as e:
        print(
            f"{RED}✗{RESET} Could not write '{e.filename or config_path}': {e.strerror or e}. "
            f"{config_path} is unchanged."
        )
        sys.exit(1)
    for var_name, value in overrides.items():
        config[var_name] = value

    return PathsUpdate(config_path, (bom + new_content).encode("utf-8"), overrides)

def coding_of(config_path):
    """What paths.py, which is not UTF-8, is: "<encoding> text" from its coding line, else "not UTF-8 text"."""
    try:
        with open(config_path, "rb") as raw:
            return f"{tokenize.detect_encoding(raw.readline)[0]} text"
    except (OSError, SyntaxError):
        return "not UTF-8 text"

def not_utf8(config_path):
    """None when paths.py is UTF-8 text (or cannot be read), else what it is (coding_of).

    ./configure and cli deps rewrite a UTF-8 paths.py only: a paths.py in
    another encoding, which its coding line names, runs, but either one
    stops before it changes the file.
    """
    try:
        with open(config_path, "r", encoding="utf-8-sig") as f:
            f.read()
    except UnicodeDecodeError:
        return coding_of(config_path)
    except OSError:
        return None
    return None

class PathsUpdate:
    """The new text of paths.py that the --<tool>-<field> flags make, and its backup, paths.py.bak.

    main() stages both (stage) after .env and the setup scripts, as new files
    next to their place, and moves them in last (commit). So a run that cannot
    write one of them (a full disk) leaves paths.py whole, and .env and the
    setup scripts as they were: paths.py is never emptied and written again.
    The new file keeps the mode of the one it replaces; the text keeps the
    byte order mark and line endings paths.py had.
    """

    def __init__(self, config_path, content, overrides):
        self.config_path = config_path
        self.backup_path = f"{config_path}.bak"
        self.content = content
        self.overrides = overrides

    def stage(self, staged):
        """Add paths.py and its backup to `staged` (see stage_writes); exits 1 when one cannot be written."""
        for path, stage in (
            (self.backup_path, lambda: stage_copy(self.config_path, self.backup_path)),
            (self.config_path, lambda: stage_file(self.config_path, self.content)),
        ):
            try:
                staged[path] = stage()
            except OSError as e:
                discard_staged(staged)
                print(f"{RED}✗{RESET} Could not write '{path}': {e.strerror or e}. Nothing else was changed.")
                sys.exit(1)

    def commit(self, staged):
        """Move the backup, then paths.py, into place, and say what was set."""
        commit_file(staged, self.backup_path)
        commit_file(staged, self.config_path)
        for var_name, value in self.overrides.items():
            print(f"* Set {var_name} = {value!r} in paths.py")
        print(f"  └ Backup saved to '{self.backup_path}'.")

def toml_reader():
    """tomllib (Python 3.11+), else tomli (the same parser) when installed, else None."""
    try:
        if sys.version_info >= (3, 11):
            import tomllib as reader
        else:
            import tomli as reader  # the same parser, when installed
    except ImportError:  # tomli not installed, or a Python built without tomllib
        return None
    return reader

def pyproject_value(name, path="pyproject.toml"):
    """Return the text `name` in the [tool.ngargparser] table of pyproject.toml, or None.

    Read with tomllib (or tomli), so any form TOML allows works. Without one, or
    when the file is not valid TOML, it is read line by line: only a table
    header line ends the table, and only a one-line "basic" or 'literal'
    string is read. An empty value counts as none.
    """
    if not os.path.isfile(path):
        return None
    reader = toml_reader()
    if reader is not None:
        try:
            with open(path, "rb") as f:
                data = reader.load(f)
        except (OSError, ValueError):  # TOMLDecodeError is a ValueError
            data = None
        if data is not None:
            tool = data.get("tool")
            table = tool.get("ngargparser") if isinstance(tool, dict) else None
            value = table.get(name) if isinstance(table, dict) else None
            return value if isinstance(value, str) and value else None
    header = re.compile(r'^[ \t]*\[\[?[ \t]*([^\]]+?)[ \t]*\]\]?[ \t]*(?:#.*)?$')
    key = re.compile(
        rf"""^[ \t]*{re.escape(name)}[ \t]*=[ \t]*(?:"([^"\\\r\n]*)"|'([^'\r\n]*)')[ \t]*(?:#.*)?$"""
    )
    in_table = False
    # utf-8-sig drops a byte order mark, which would hide a header on line 1.
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            table = header.match(line)
            if table:
                if in_table:
                    break
                in_table = table.group(1) == "tool.ngargparser"
            elif in_table:
                value = key.match(line)
                if value:
                    return (value.group(1) if value.group(1) is not None else value.group(2)) or None
    return None

def pyproject_app_name(path="pyproject.toml"):
    """Return app_name from the [tool.ngargparser] table of pyproject.toml, or None."""
    return pyproject_value("app_name", path)


def render_env_content(config, app_root):
    """Build the .env content for `config`. Returns a string; callers
    decide whether it differs from what's on disk before writing.

    The first line is ENV_KEYS_MARKER with the keys written, so the next run
    can tell a key added by hand from one paths.py no longer has."""
    if not config or (set(config.keys()) <= APP_KEYS):
        lines = [f"APP_ROOT={app_root}\n", f"APP_NAME={config['APP_NAME']}\n"]
        if config.get('APP_VENV'):
            lines.append(f"APP_VENV={config['APP_VENV']}\n")
        if config.get('APP_ENTRY'):
            lines.append(f"APP_ENTRY={config['APP_ENTRY']}\n")
    else:
        lines = []
        for key, value in config.items():
            if value is None:
                continue

            if isinstance(value, str):
                value = value.strip("'").strip('"')

            lines.append(f"{key.upper()}={value}\n")
    keys = ",".join(line.split("=", 1)[0] for line in lines)
    return f"{ENV_KEYS_MARKER}{keys}\n" + "".join(lines)

def env_keys(content):
    """The keys .env text `content` sets, in order, as python-dotenv reads them."""
    return list(dotenv_values(stream=io.StringIO(content)).keys())

def keys_written_last_time(content):
    """The keys the ENV_KEYS_MARKER line of .env text `content` lists, or None without one."""
    for line in content.splitlines():
        if line.startswith(ENV_KEYS_MARKER):
            return {key.strip() for key in line[len(ENV_KEYS_MARKER):].split(",") if key.strip()}
    return None

def warn_removed_keys(old_content, new_content):
    """Name the keys a rewrite of .env drops that ./configure did not write itself.

    .env is rebuilt from paths.py on every run, so a key added to it by hand
    is lost. Keys the last run wrote, which paths.py no longer has, and the
    app keys are dropped without a word. Without the marker line (a .env from
    `cli generate` or an older ./configure), every dropped key is named.
    """
    written = keys_written_last_time(old_content) or set()
    kept = set(env_keys(new_content))
    removed = [
        key for key in env_keys(old_content)
        if key not in kept and key not in APP_KEYS and key not in written
    ]
    if removed:
        them = "them" if len(removed) > 1 else "it"
        print(
            f"{YELLOW}⚠{RESET}  Removed {', '.join(removed)} from .env: ./configure rewrites .env from paths.py. "
            f"Put {them} in paths.py to keep {them}."
        )

def follow_dependency_env(config, detected_tools, published_venvs, ambiguous_venvs, explicit_entries):
    """Which of each dependency's settings follow what it publishes in its own .env.

    Returns {tool_prefix: (venv_follows, entry_follows)}. Called after
    resolve_tool_venvs. The virtualenv follows the dependency's APP_VENV when
    it came from there, or when none was settled on: nothing found at all (no
    <tool>_venv in paths.py, no .env, no virtualenv inside <tool>_path; the
    dependency may not be deployed yet), or two bundled ones that
    ./configure would not choose between (`ambiguous_venvs`). The start-up
    script follows its APP_ENTRY unless paths.py sets <tool>_entry. The setup
    script reads these values when it is sourced, and app_env.py when the tool
    starts, so a dependency deployed or redeployed after this run is picked up
    without running ./configure again. A value that neither the dependency's
    .env nor this tool's sets is unset there, an inherited one too: it belongs
    to the calling tool. (A tool whose <tool>_path is not set stops this run
    before anything is written.)
    """
    follows = {}
    for tool_prefix in detected_tools:
        venv_follows = tool_prefix in published_venvs or not config.get(f"{tool_prefix}_venv")
        follows[tool_prefix] = (venv_follows, tool_prefix not in explicit_entries)
    return follows

def path_is_unset(tool_path):
    """True when a dependency's <tool>_path is None or blank in paths.py."""
    return tool_path is None or (isinstance(tool_path, str) and tool_path.strip() == "")

def entry_runs_with_python(entry):
    """True when start-up script `entry` is a Python script: a first #! line names
    python, or, without one, the name ends in .py. app_env.py and the setup
    scripts use the same rule."""
    try:
        with open(entry, "rb") as f:
            line = f.readline(4096)
    except OSError:  # missing or unreadable: the name decides
        line = b""
    if line.startswith(b"#!"):
        return b"python" in line
    return entry.endswith(".py")


def report_tool_settings(config, tool_prefix, warn_no_venv=True):
    """Say what may go wrong with a dependency's settings, before its setup script is written.

    Returns False, without a word, when <tool>_path is not set, so there is no
    setup script to write: main() names every such path in one message
    (report_unset_paths). Everything else only warns.

    Args:
        config: Configuration dictionary
        tool_prefix: The prefix used for this tool's variables (e.g., 'phbr', 'pepx', 'mhci')
        warn_no_venv: Whether to warn when the tool has no virtualenv. Off for a
            tool whose bundled virtualenvs were ambiguous, which already warned.
    """
    venv = config.get(f"{tool_prefix}_venv")
    lib_path = config.get(f"{tool_prefix}_lib_path")
    tool_path = config.get(f"{tool_prefix}_path")

    # Check if required path is None or empty
    if path_is_unset(tool_path):
        return False

    # Advisory only: a missing directory doesn't block .env/script generation,
    # it just warns the tool may not work until the path is corrected.
    for field, value in (("path", tool_path), ("venv", venv), ("lib_path", lib_path)):
        if value and not os.path.isdir(value):
            print(
                f"{YELLOW}⚠{RESET}  {BOLD}{tool_prefix}_{field}{RESET} = '{value}' does not exist. "
                f"'{tool_prefix}' may fail until this is corrected."
            )

    # Nothing set, nothing published, nothing bundled: the tool inherits
    # whatever interpreter happens to be active, which is worth saying out
    # loud rather than leaving to be discovered at runtime.
    # An entry that is not a Python script (a shell script, a program) runs by
    # itself, so a missing virtualenv is not worth a warning there. Nor is it
    # with <tool>_python set in paths.py: that Python runs the tool.
    # Nor is it when the tool may still publish one: with no virtualenv known,
    # its APP_VENV is read from its .env when the setup script is sourced and
    # when this tool starts (follow_dependency_env). It is not deployed yet, or
    # has a ./configure that has not written its .env yet.
    entry = config.get(f"{tool_prefix}_entry")
    if entry and tool_path and not os.path.isabs(entry):
        entry = os.path.join(tool_path, entry)
    python = config.get(f"{tool_prefix}_python")
    may_publish = not os.path.isdir(tool_path) or (
        not os.path.isfile(os.path.join(tool_path, DOT_ENV_PATH))
        and os.path.isfile(os.path.join(tool_path, "configure"))
    )
    if (
        not venv
        and warn_no_venv
        and not python
        and not may_publish
        and not (entry and not entry_runs_with_python(entry))
    ):
        flag_base = tool_prefix.replace("_", "-")
        print(
            f"{YELLOW}⚠{RESET}  {BOLD}{tool_prefix}{RESET} has no virtualenv. It will run "
            f"under whatever Python is active. Set one with "
            f"'{BOLD}./configure --{flag_base}-venv=<path>{RESET}' if it needs its own."
        )
    return True

def report_unset_paths(unset, args):
    """Name every dependency in `unset`, whose <tool>_path is not set, in one message with one command that sets them all.

    A run that stops here writes none of its flags (`args`, as parsed), so the
    command repeats them, and leaves out a <tool>_path placeholder for a path
    they set. A hint for one path at a time failed on the next one.
    """
    unset_flags = {f"{tool_prefix}_path" for tool_prefix in unset}
    flags = []
    for var_name, value in vars(args).items():  # tool by tool, the order paths.py declares them in
        if var_name in unset_flags:
            flags.append(f"--{var_name.replace('_', '-')}=<path>")
        elif value is not None:
            flags.append(f"--{var_name.replace('_', '-')}={shlex.quote(value)}")
    command = " ".join(["./configure", *flags])
    names = [f"{BOLD}{tool_prefix}_path{RESET}" for tool_prefix in unset]
    # Both commands rewrite paths.py, and refuse one that is not UTF-8.
    encoding = not_utf8(CONFIG_PATH)
    first, then = "", "Set"
    if encoding:
        first = (
            f"paths.py is {encoding}, and ./configure and cli deps change UTF-8 text only: "
            f"save it as UTF-8 first (with no coding line, or coding: utf-8). "
        )
        then = "Then set"
    if len(names) == 1:
        print(
            f"{RED}✗{RESET} {names[0]} is None in paths.py. {first}{then} it with '{BOLD}{command}{RESET}', "
            f"or run '{BOLD}cli deps remove {unset[0]}{RESET}' to drop this dependency."
        )
    else:
        print(
            f"{RED}✗{RESET} {', '.join(names[:-1])} and {names[-1]} are None in paths.py. "
            f"{first}{then} them with '{BOLD}{command}{RESET}', "
            f"or run '{BOLD}cli deps remove <name>{RESET}' to drop the ones this tool does not need."
        )

def shell_double_quoted(text):
    """`text` escaped to stand as written inside "..." in a shell script."""
    for char in '\\"$`':
        text = text.replace(char, "\\" + char)
    return text

def real_folder(folder):
    """A shell command substitution that prints the real folder behind `folder` (a shell word), or nothing.

    `command` runs the shell's own cd and pwd, never a cd or pwd function or
    alias of the caller's shell (dotfiles, autoenv), whose output would be
    captured too. Every folder change in a setup script goes through it.
    """
    return f"$(CDPATH='' command cd -P -- {folder} >/dev/null 2>&1 && command pwd -P)"

def no_env_lines(indent, unconfigured, installing, follows_entry):
    """Shell lines for tool folder $_ng_real when it is there without a .env.

    A tool folder (./configure, or .ngargparser-build in a build copy) that
    is a git checkout (it has .git) is not configured yet: the lines
    `unconfigured` run. One that is no git checkout is being installed:
    deploy/install.sh writes .env last, by running ./configure. The lines
    `installing` run. A redeploy removes the old files in any order, so with
    `follows_entry` (the tool publishes its entry in its .env) a folder that
    is no git checkout and still has deploy/install.sh, .ngargparser/, .venv/
    or src/ is being installed too; any other such folder may be no tool, and
    never has a .env. dependency_command in app_env.py uses the same rule
    (_unconfigured_clone, _being_installed).
    """
    pad = " " * indent
    lines = [
        f'{pad}if [ -n "$_ng_real" ] && [ ! -f "$_ng_real/.env" ]; then',
        f'{pad}    if [ -f "$_ng_real/configure" ] || [ -f "$_ng_real/.ngargparser-build" ]; then',
        f'{pad}        if [ -e "$_ng_real/.git" ]; then',
        *(f"{pad}            {line}" for line in unconfigured),
        f"{pad}        else",
        *(f"{pad}            {line}" for line in installing),
        f"{pad}        fi",
    ]
    if follows_entry:
        lines.append(
            f'{pad}    elif [ ! -e "$_ng_real/.git" ] && {{ [ -f "$_ng_real/deploy/install.sh" ] || '
            '[ -d "$_ng_real/.ngargparser" ] || [ -d "$_ng_real/.venv" ] || [ -d "$_ng_real/src" ]; }; then'
        )
        lines.extend(f"{pad}        {line}" for line in installing)
    return lines + [f"{pad}    fi", f"{pad}fi"]

def unconfigured_text(tool_prefix, folder):
    """The line, escaped for "...", that says `tool_prefix` at `folder` (a shell expression) is not configured yet."""
    return (
        shell_double_quoted(f"{tool_prefix} at ")
        + folder
        + shell_double_quoted(" is not configured yet: run ./configure there")
    )

def half_made_venv_lines(name, indent, then):
    """Shell lines that run the lines `then` when tool folder $_ng_real is being installed: its .env is there,
    but the virtualenv in shell variable `name`, which lies in that folder, has no bin/python yet.

    A tool folder has ./configure (or .ngargparser-build in a build copy).
    dependency_command in app_env.py uses the same rule (_being_installed).
    The virtualenv's folder is found through links, else its parent's.
    """
    pad = " " * indent
    return [
        f'{pad}if [ -n "$_ng_real" ] && [ -f "$_ng_real/.env" ] && [ ! -e "${{{name}:-}}/bin/python" ] && '
        '{ [ -f "$_ng_real/configure" ] || [ -f "$_ng_real/.ngargparser-build" ]; }; then',
        f"{pad}    case ${{{name}:-}} in",
        f"{pad}        /*)",
        f"{pad}            _ng_folder=" + real_folder(f'"${name}"') + " ||",
        f"{pad}                _ng_folder=" + real_folder(f'"${{{name}%/*}}/"') + " || _ng_folder=''",
        f"{pad}            case $_ng_folder/ in",
        f"{pad}                /) ;;",
        f'{pad}                "$_ng_real"/*)',
        *(f"{pad}                    {line}" for line in then),
        f"{pad}                    ;;",
        f"{pad}            esac",
        f"{pad}            ;;",
        f"{pad}    esac",
        f"{pad}fi",
    ]

def installing_text(tool_prefix, folder):
    """The line, escaped for "...", that says `tool_prefix` is being installed at `folder` (a shell expression)."""
    return (
        shell_double_quoted(f"{tool_prefix} is being installed at ")
        + folder
        + shell_double_quoted(" right now (a redeploy in progress?); try again")
    )

def moved_text(tool_prefix, python=False):
    """The sentence that follows a "try again" which may never pass: the dependency moved.

    With `python`, the Python that runs it is what is missing, which a mistyped
    <tool>_python in paths.py names too. app_env.py adds the same sentence.
    """
    text = f". If {tool_prefix} moved, run ./configure --{tool_prefix.replace('_', '-')}-path=<new folder>"
    return text + (f" or fix {tool_prefix}_python in paths.py" if python else "")

def venv_free_path_lines(indent):
    """Shell lines that set _ng_kept to ":" + $PATH without the bin/ of any virtualenv.

    Left out: the bin/ of VIRTUAL_ENV, and any folder whose parent holds a
    pyvenv.cfg, as dependency_command in app_env.py leaves them out. PATH
    must be set. They use _ng_skip, _ng_rest, _ng_dir and _ng_here too.
    """
    pad = " " * indent
    return [
        f"{pad}_ng_skip=''",
        f'{pad}if [ -n "${{VIRTUAL_ENV:-}}" ]; then',
        f"{pad}    _ng_skip=" + real_folder('"$VIRTUAL_ENV/bin"') + " || _ng_skip=''",
        f"{pad}fi",
        f"{pad}_ng_kept=''",
        f'{pad}_ng_rest="$PATH:"',
        f'{pad}while [ -n "$_ng_rest" ]; do',
        f"{pad}    _ng_dir=${{_ng_rest%%:*}}",
        f"{pad}    _ng_rest=${{_ng_rest#*:}}",
        f'{pad}    if [ -n "$_ng_dir" ]; then',
        f"{pad}        _ng_here=" + real_folder('"$_ng_dir"') + " || _ng_here=''",
        f'{pad}        if [ -n "$_ng_here" ] && '
        '{ [ "$_ng_here" = "$_ng_skip" ] || [ -f "${_ng_here%/*}/pyvenv.cfg" ]; }; then',
        f"{pad}            continue",
        f"{pad}        fi",
        f"{pad}    fi",
        f'{pad}    _ng_kept="$_ng_kept:$_ng_dir"',
        f"{pad}done",
    ]

def read_env_value_lines(key, var, indent):
    """Shell lines that set `var` to the value of `key` in the .env file "$_ng_env".

    Read as python-dotenv reads these simple values: a leading `export `,
    spaces around `=` and one pair of quotes are not part of the value, nor is
    a ` # comment` after an unquoted one, and the last line for a key wins.
    """
    pad = " " * indent
    return [
        f"{pad}_ng_value=$(sed -n -e 's/^[[:space:]]*//' -e 's/^export[[:space:]][[:space:]]*//'"
        f" -e 's/[[:space:]]*$//' -e 's/^{key}[[:space:]]*=[[:space:]]*//p' \"$_ng_env\" | tail -n 1)",
        f"{pad}case $_ng_value in",
        f"{pad}    \\\"*) _ng_value=${{_ng_value#\\\"}}; _ng_value=${{_ng_value%%\\\"*}} ;;",
        f"{pad}    \\'*) _ng_value=${{_ng_value#\\'}}; _ng_value=${{_ng_value%%\\'*}} ;;",
        f"{pad}    *)",
        f"{pad}        _ng_value=${{_ng_value%%[[:space:]]#*}}",
        f'{pad}        _ng_value=${{_ng_value%"${{_ng_value##*[![:space:]]}}"}}',
        f"{pad}        ;;",
        f"{pad}esac",
        f"{pad}{var}=$_ng_value",
    ]

def render_shell_script(config, tool_prefix, venv_published=False, entry_published=None):
    """
    The text of the setup script for a dependency tool, or None when its <tool>_path is not set.

    The script is POSIX sh (`.`, not `source`), so it works when sourced from
    sh or dash, as `subprocess.run(..., shell=True)` does, and from bash.

    Args:
        config: Configuration dictionary
        tool_prefix: The prefix used for this tool's variables (e.g., 'phbr', 'pepx', 'mhci')
        venv_published: The virtualenv is read from the tool's own .env (APP_VENV)
            when the script is sourced; the value in config, if any, is the fallback.
        entry_published: The same for the start-up script (APP_ENTRY). None, the
            default, means "whenever config has one".
    """
    # Get values from config using prefix
    module = config.get(f"{tool_prefix}_module")
    venv = config.get(f"{tool_prefix}_venv")
    lib_path = config.get(f"{tool_prefix}_lib_path")
    tool_path = config.get(f"{tool_prefix}_path")
    # <tool>_python set in paths.py: the Python that runs the tool, whatever else is found.
    python = config.get(f"{tool_prefix}_python")
    env_var = f"{tool_prefix.upper()}_PATH"
    if path_is_unset(tool_path):
        return None

    lines = ["#!/bin/bash", f"{GENERATED_MARKER}\n"]
    # The script's own variables, unset at its end so they do not stay in the caller's shell.
    temps: set[str] = set()

    lines.append(f"# ---- Setup for {tool_prefix.upper()} ----")
    venv_var = f"{tool_prefix.upper()}_VENV"
    python_var = f"{tool_prefix.upper()}_PYTHON"
    entry_var = f"{tool_prefix.upper()}_ENTRY"
    # run_<tool> is the form to use: the raw one runs what was found when this
    # script was sourced, in the caller's virtualenv (a tool built on
    # ngargparser moves into its own at start-up, others do not).
    lines.append(f"# Source this script, then run {tool_prefix} with run_{tool_prefix} <arguments>: each call")
    lines.append(f"# finds {tool_prefix} again (so a redeploy since is picked up) and runs it inside")
    raw_form = f'${{{python_var}:+"${python_var}"}} "${{{entry_var}:?}}"'
    lines.append(f"# {tool_prefix}'s own virtualenv. {raw_form} <arguments>")
    lines.append("# runs what was found when this script was sourced, in the caller's VIRTUAL_ENV and PATH.")

    # Optional: load the environment module, next to the modules already loaded.
    # No `module purge`: it would unload the caller's own modules, and the one a
    # setup script sourced before this one loaded. A shell without `module` (sh or
    # dash, which do not see a function bash exported, or a host without modules)
    # gets a warning on stderr, and the script goes on.
    if module:
        missing = (
            f"setup_{tool_prefix}_env.sh: {tool_prefix} needs the environment module {module}, "
            f"but this shell has no 'module' command; load it first (module load {module}), "
            f"or source this script from a login shell."
        )
        lines.append("if command -v module >/dev/null 2>&1; then")
        lines.append(f"    module load {module}")
        lines.append("else")
        lines.append(f'    echo "{shell_double_quoted(missing)}" >&2')
        lines.append("fi")

    # The values this script exports are found by _ng_<tool>_resolve, a
    # function it defines: it runs once when this script is sourced, for the
    # variables the raw form uses, and again at each call of run_<tool>, in its
    # own subshell, so a shell that sourced this script before a redeploy runs
    # the new version. Its one argument (setup_<tool>_env.sh or run_<tool>)
    # starts the lines it says. It says nothing about a tool being installed or
    # with no entry: the script and run_<tool> each say that their own way.
    resolve = f"_ng_{tool_prefix}_resolve"
    outer, lines = lines, []
    # The real folder behind <tool>_path is found once, and every value is read
    # from the .env in it: through a `current` link that a redeploy moves while
    # this script is sourced, each read through the link could see another
    # version, and pair one version's virtualenv with another's script. With
    # no folder there, _ng_real is empty, as is _ng_env: as without a .env.
    lines.append(f"_ng_real={real_folder(shlex.quote(tool_path))} || _ng_real=''")
    temps.add("_ng_real")

    # What the tool publishes in its own .env (APP_VENV, APP_ENTRY) is read from
    # that file, not copied in now: a redeploy puts a new version behind the
    # same <tool>_path (a `current` link), and nothing re-runs this tool's
    # ./configure. The values found now are the fallback while
    # that .env is missing, empty when the tool published nothing yet (it may be
    # deployed later). A key the .env leaves out is none: the value found now
    # may belong to the version deployed before. A .env whose APP_ROOT names
    # another folder (the tool was copied or moved, and not configured again)
    # counts as missing: its values name that other folder's files.
    entry = config.get(f"{tool_prefix}_entry")
    if entry_published is None:
        entry_published = bool(entry)
    published_keys = [("APP_VENV", "_ng_venv", venv or "")] if venv_published else []
    if entry_published:
        published_keys.append(("APP_ENTRY", "_ng_entry", entry or ""))
    if published_keys:
        lines.append(f"# {' and '.join(key for key, _var, _value in published_keys)} come from {tool_prefix}'s own .env, read")
        lines.append("# when this script is sourced and at each run_" + tool_prefix + ", so a redeploy of it is picked")
        lines.append("# up without re-running ./configure here. The values below are what ./configure saw,")
        lines.append("# used only while that .env is missing, cannot be read, is not UTF-8 text,")
        lines.append("# or was written for another folder (its APP_ROOT): one it leaves out is none.")
        if not all(value for _key, _var, value in published_keys):
            lines.append("# An empty one means it found none: it is unset, an inherited value too, unless that .env has one.")
        lines.append("_ng_env=${_ng_real:+$_ng_real/.env}")
        # A relative APP_ENTRY is joined to the folder that same .env describes (its
        # APP_ROOT, else that real folder), not to <tool>_path, for the same reason.
        for _key, var, value in published_keys + [
            ("APP_ROOT", "_ng_root", ""),
            ("", "_ng_stale", ""),
            ("", "_ng_installing", ""),
        ]:
            lines.append(f"{var}={shlex.quote(value)}")
        temps.update(
            (
                "_ng_env",
                "_ng_root",
                "_ng_value",
                "_ng_folder",
                "_ng_stale",
                "_ng_installing",
                *(var for _key, var, _value in published_keys),
            )
        )
        # A .env this shell cannot read, or that is not UTF-8 text (python-dotenv's
        # encoding), counts as missing, as in ./configure and app_env.py: one
        # line says why, and sed never gets to fail on it. Without iconv, one
        # that is not UTF-8 is read as it is.
        dep_env = os.path.join(tool_path, DOT_ENV_PATH)
        unreadable = (
            f"{dep_env} cannot be read (permission denied), so its values are not used: ask its owner to make it readable"
        )
        not_utf8 = (
            f"{dep_env} is not UTF-8 text, "
            f"so its values are not used: save it as UTF-8, or run ./configure in {tool_path} to write it again"
        )
        lines.append('if [ -f "$_ng_env" ]; then')
        lines.append('    if [ ! -r "$_ng_env" ]; then')
        lines.append(f'        echo "$1: {shell_double_quoted(unreadable)}" >&2')
        lines.append("        _ng_env=''")
        lines.append('    elif command -v iconv >/dev/null 2>&1 && ! iconv -f UTF-8 -t UTF-8 "$_ng_env" >/dev/null 2>&1; then')
        lines.append(f'        echo "$1: {shell_double_quoted(not_utf8)}" >&2')
        lines.append("        _ng_env=''")
        lines.append("    fi")
        lines.append("fi")
        lines.append('if [ -f "$_ng_env" ]; then')
        lines.extend(read_env_value_lines("APP_ROOT", "_ng_root", 4))
        lines.append("    case $_ng_root in")
        lines.append("        /*)")
        root_real = real_folder('"$_ng_root"')
        lines.append(f'            if [ "{root_real}" != "$_ng_real" ]; then')
        lines.append("                _ng_env=''")
        lines.append("                _ng_root=''")
        lines.append("            fi")
        lines.append("            ;;")
        lines.append("    esac")
        lines.append("fi")
        lines.append('if [ -n "$_ng_env" ] && [ -f "$_ng_env" ]; then')
        for key, var, _value in published_keys:
            lines.extend(read_env_value_lines(key, var, 4))
        if venv_published:
            # Its .env is there, but the virtualenv it publishes in its own
            # folder has no bin/python yet: being installed too (_ng_installing).
            # No entry is exported, so run_<tool> says so again; <TOOL>_VENV
            # is, as that .env has it, for run_<tool> to check.
            lines.extend(
                half_made_venv_lines(
                    "_ng_venv",
                    4,
                    [
                        "_ng_installing=installing",
                        "_ng_stale=1",
                        *(["_ng_entry=''"] if entry_published else []),
                    ],
                )
            )
        # Without that .env, a value ./configure saw stands in only when it lies in
        # the real folder found above: through a `current` link it names the
        # version folder the link pointed to then, which a redeploy leaves behind.
        # One in another folder that is there would run that older version without
        # a word, so then none of them is used (_ng_stale), as in app_env.py
        # (_fallback_elsewhere): the lines below say to try again.
        # A real folder without its .env that is a git checkout is not
        # configured yet; one with a tool's files (no_env_lines) is being
        # installed: a same-version redeploy extracts it where it was, and its
        # install.sh writes .env last, so a value ./configure saw there may name
        # a half-made .venv. Either way none of them is used, and one line says
        # which (_ng_installing).
        lines.append("else")
        lines.extend(
            no_env_lines(
                4,
                ["_ng_installing=unconfigured", "_ng_stale=1"],
                ["_ng_installing=installing", "_ng_stale=1"],
                entry_published,
            )
        )
        for _key, var, _value in published_keys:
            folder = f'"${var}"' if var == "_ng_venv" else f'"${{{var}%/*}}/"'
            lines.append(f"    case ${var} in")
            lines.append("        /*)")
            lines.append(f"            _ng_folder={real_folder(folder)} || _ng_folder=''")
            lines.append('            if [ -n "$_ng_folder" ]; then')
            lines.append("                case $_ng_folder/ in")
            lines.append('                    "$_ng_real"/*) [ -n "$_ng_real" ] || _ng_stale=1 ;;')
            lines.append("                    *) _ng_stale=1 ;;")
            lines.append("                esac")
            lines.append("            fi")
            lines.append("            ;;")
            lines.append("    esac")
        lines.append('    if [ -n "$_ng_stale" ]; then')
        lines.append("        " + " ".join(f"{var}=''" for _key, var, _value in published_keys))
        lines.append("    fi")
        lines.append("fi")

    # Optional: export the virtualenv as <TOOL>_VENV. It is not activated in
    # the caller's shell: VIRTUAL_ENV, PATH and PYTHONHOME stay as they were,
    # so a `python` the caller runs after sourcing this script is still its
    # own. run_<tool> activates it for the tool it runs, in its own subshell,
    # as dependency_command in app_env.py does. <TOOL>_PYTHON is always
    # exported, below: <tool>_python from paths.py when set, else the
    # virtualenv's python, else python3 (found on PATH without any
    # virtualenv's bin/, as run_<tool> finds it), the same rule
    # dependency_command follows. So a caller runs "$<TOOL>_PYTHON"
    # "$<TOOL>_ENTRY" with the tool's own Python. With none found, <TOOL>_VENV
    # is unset, as is <TOOL>_ENTRY below: a value in the environment came from
    # the tool that started this one, whose own dependency of the same name
    # may be another install. A value read when the script is sourced can be
    # empty even when ./configure found one.
    if not python:
        temps.add("_ng_python")
    if venv_published:
        lines.append('if [ -n "$_ng_venv" ]; then')
        lines.append(f'    export {venv_var}="$_ng_venv"')
        if not python:
            lines.append('    _ng_python="$_ng_venv/bin/python"')
        lines.append("else")
        lines.append(f"    unset {venv_var}")
        if not python:
            lines.append("    _ng_python=python3")
        lines.append("fi")
    elif venv:
        lines.append(f"export {venv_var}={shlex.quote(venv)}")
        if not python:
            lines.append(f"_ng_python={shlex.quote(os.path.join(venv, 'bin', 'python'))}")
    elif not python:
        lines.append("_ng_python=python3")

    # Optional: the start-up script the tool publishes in its .env (APP_ENTRY),
    # relative to the tool unless it is an absolute path. Left unset when there
    # is none, rather than set to the bare tool folder: the lines after
    # _ng_<tool>_resolve say why. One read from the .env is joined to the
    # APP_ROOT read with it, else to the real folder behind <tool>_path found
    # above (_ng_real), the folder that .env sits in.
    if entry_published:
        lines.append('if [ -n "$_ng_entry" ]; then')
        lines.append('    case "$_ng_entry" in')
        lines.append("        /*) ;;")
        lines.append("        *)")
        lines.append('            case "$_ng_root" in')
        lines.append("                /*) ;;")
        lines.append(f"                *) _ng_root=${{_ng_real:-${env_var}}} ;;")
        lines.append("            esac")
        lines.append('            _ng_entry="$_ng_root/$_ng_entry"')
        lines.append("            ;;")
        lines.append("    esac")
        lines.append(f'    export {entry_var}="$_ng_entry"')
        lines.append("else")
        lines.append(f"    unset {entry_var}")
        lines.append("fi")
    elif entry:  # set in paths.py: fixed, never read from the tool's .env
        temps.add("_ng_entry")
        lines.append(f"_ng_entry={shlex.quote(entry)}")
        lines.append(f'case "$_ng_entry" in /*) ;; *) _ng_entry="${env_var}/$_ng_entry" ;; esac')
        if venv_published:
            # A virtualenv ./configure saw in another version folder is not paired
            # with this script (_ng_stale above): run_<tool> says to try again.
            lines.append('if [ -n "$_ng_stale" ]; then')
            lines.append(f"    unset {entry_var}")
            lines.append("else")
            lines.append(f'    export {entry_var}="$_ng_entry"')
            lines.append("fi")
        else:
            lines.append(f'export {entry_var}="$_ng_entry"')

    # <TOOL>_PYTHON runs the entry script when it is a Python script: a #! line
    # that names python, else a name that ends in .py (dependency_command uses
    # the same rule). Any other entry (a shell script, a program) runs by
    # itself, so <TOOL>_PYTHON is empty, and one call runs either kind:
    # ${<TOOL>_PYTHON:+"$<TOOL>_PYTHON"} "$<TOOL>_ENTRY". A <tool>_python set
    # in paths.py always runs it. With no entry known (the tool is not deployed
    # or configured yet), <TOOL>_PYTHON is empty too, so that one call fails
    # (command "" not found) instead of starting python3 with "" as its
    # script, which runs the __main__.py of the current folder.
    # Without a virtualenv (and no <tool>_python), an executable entry whose #!
    # line names python runs by itself too: that line may name the one Python
    # the tool needs, which python3 is not (dependency_command uses the same
    # rule). The python3 that stands in otherwise is the first one on PATH
    # without any virtualenv's bin/ (VIRTUAL_ENV's, or a folder whose parent
    # holds a pyvenv.cfg), as run_<tool> finds it, written as its full path: a
    # bare python3 in the caller's shell would be the caller's own virtualenv's.
    if python:
        lines.append(f"export {python_var}={shlex.quote(str(python))}")
    else:
        if entry_published or entry:
            temps.add("_ng_line")
            lines.append(f'if [ -n "${{{entry_var}:-}}" ]; then')
            lines.append("    _ng_line=''")
            lines.append(f'    if [ -f "${entry_var}" ] && [ -r "${entry_var}" ]; then')
            lines.append(f'        IFS= read -r _ng_line < "${entry_var}" || :')
            lines.append("    fi")
            lines.append("    case $_ng_line in")
            lines.append("        '#!'*python*)")
            lines.append(f'            if [ "$_ng_python" = python3 ] && [ -x "${entry_var}" ]; then')
            lines.append("                _ng_python=''")
            lines.append("            fi")
            lines.append("            ;;")
            lines.append("        '#!'*) _ng_python='' ;;")
            lines.append(f"        *) case ${entry_var} in *.py) ;; *) _ng_python='' ;; esac ;;")
            lines.append("    esac")
            lines.append("else")
            lines.append("    _ng_python=''")
            lines.append("fi")
        temps.update(("_ng_skip", "_ng_kept", "_ng_rest", "_ng_dir", "_ng_here"))
        lines.append('if [ "$_ng_python" = python3 ] && [ -n "${PATH+set}" ]; then')
        lines.extend(venv_free_path_lines(4))
        # The first python3 in those folders, as `command -v` finds it, without
        # changing PATH here: this shell is the caller's. A relative folder is
        # written out in full; an empty one (the current folder) is skipped.
        lines.append('    _ng_rest="${_ng_kept#:}:"')
        lines.append('    while [ -n "$_ng_rest" ]; do')
        lines.append("        _ng_dir=${_ng_rest%%:*}")
        lines.append("        _ng_rest=${_ng_rest#*:}")
        lines.append('        if [ -n "$_ng_dir" ] && [ -f "$_ng_dir/python3" ] && [ -x "$_ng_dir/python3" ]; then')
        lines.append("            case $_ng_dir in")
        lines.append("                /*) _ng_here=$_ng_dir ;;")
        lines.append(
            "                *) _ng_here=$(CDPATH='' command cd -- \"$_ng_dir\" >/dev/null 2>&1 && command pwd) "
            "|| _ng_here='' ;;"
        )
        lines.append("            esac")
        lines.append('            if [ -n "$_ng_here" ]; then')
        lines.append('                _ng_python="$_ng_here/python3"')
        lines.append("                break")
        lines.append("            fi")
        lines.append("        fi")
        lines.append("    done")
        lines.append("fi")
        lines.append(f'export {python_var}="$_ng_python"')

    body, lines = lines, outer
    lines.append(f"{resolve}() {{")
    lines.extend(f"    {line}" if line else line for line in body)
    lines.append("}")

    # Optional: Set LD_LIBRARY_PATH
    # The ':' only when LD_LIBRARY_PATH is set and not empty: a trailing one makes
    # the loader search the current folder, and a bare $LD_LIBRARY_PATH stops a
    # caller that uses `set -u` when it is unset.
    if lib_path:
        lines.append(f"export LD_LIBRARY_PATH={lib_path}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}")

    # Required: Export tool path (before the values: a relative entry may be joined to it)
    lines.append(f"export {env_var}={tool_path}")
    lines.append(f"{resolve} setup_{tool_prefix}_env.sh")

    # A tool folder without its .env (no_env_lines), or whose virtualenv is
    # half-made, gets one line: not configured yet, or being installed (try
    # again). With no entry, a folder that is not there is most often a
    # redeploy in progress: the line says to try again, as dependency_command
    # in app_env.py does (_no_entry). Else the tool publishes none yet.
    setup_prefix = shell_double_quoted(f"setup_{tool_prefix}_env.sh: ")
    if published_keys:
        lines.append("case $_ng_installing in")
        lines.append(f'    installing) echo "{setup_prefix}{installing_text(tool_prefix, "$_ng_real")}" >&2 ;;')
        lines.append(f'    unconfigured) echo "{setup_prefix}{unconfigured_text(tool_prefix, "$_ng_real")}" >&2 ;;')
        lines.append("esac")
    if entry_published:
        dep_env = os.path.join(tool_path, DOT_ENV_PATH)
        no_entry = (
            f"setup_{tool_prefix}_env.sh: {tool_prefix} publishes no entry script yet "
            f"(no APP_ENTRY in {dep_env}, or no .env there yet): deploy and configure {tool_prefix}, "
            f"or set {tool_prefix}_entry in paths.py"
        )
        not_deployed_now = (
            f"setup_{tool_prefix}_env.sh: {tool_prefix} is not deployed at {tool_path} right now "
            f"(a redeploy in progress?); try again{moved_text(tool_prefix)}"
        )
        lines.append('if [ -z "$_ng_entry" ]; then')
        lines.append('    if [ -z "$_ng_real" ]; then')
        lines.append(f'        echo "{shell_double_quoted(not_deployed_now)}" >&2')
        lines.append('    elif [ -z "$_ng_installing" ]; then')
        lines.append(f'        echo "{shell_double_quoted(no_entry)}" >&2')
        lines.append("    fi")
        lines.append("fi")

    # run_<tool> runs the tool with the arguments it is given (run_pepx predict
    # -j in.json), as ${<TOOL>_PYTHON:+"$<TOOL>_PYTHON"} "$<TOOL>_ENTRY" does,
    # with the values _ng_<tool>_resolve finds again at the call, and returns
    # the tool's exit status. With no entry known it says so on stderr and
    # returns 127, as for a command that is not found, so a caller under
    # `set -e` stops there. Its Python, when it has one, and its entry are
    # checked first: one that is not there (127, a redeploy in progress) or an
    # entry that runs by itself and is not executable (126) is named in the
    # line dependency_command in app_env.py raises, not the shell's "not found"
    # or "Permission denied". A "try again" that never passes, because the
    # tool moved (or <tool>_python names no Python), is followed by how to fix
    # that (moved_text). A tool folder without its .env is not configured yet
    # (a git checkout) or being installed again where it was (no_env_lines):
    # none of the values that follow its .env runs then.
    # Without a virtualenv (<TOOL>_VENV empty) it runs outside every one, as
    # dependency_command does: VIRTUAL_ENV is unset, and PATH loses the bin/
    # of VIRTUAL_ENV and of any folder whose parent holds a pyvenv.cfg, so
    # python3 is not the caller's. With one, it runs inside it, as
    # dependency_command does: VIRTUAL_ENV is <TOOL>_VENV, its bin/ comes first
    # on PATH, and PYTHONHOME is unset. The function's body is a subshell, so
    # this changes the environment of the tool it runs, not the caller's shell.
    not_configured = (
        f"run_{tool_prefix}: {tool_prefix} has no entry script ({entry_var} is empty): deploy and configure "
        f"{tool_prefix}, or set {tool_prefix}_entry in paths.py and run ./configure, "
        f"then source setup_{tool_prefix}_env.sh again"
    )
    entry_ref, path_ref = f"${{{entry_var}}}", f"${{{env_var}}}"
    not_deployed = (
        shell_double_quoted(f"{tool_prefix} is not deployed at ")
        + path_ref
        + shell_double_quoted(f" right now (a redeploy in progress?); try again{moved_text(tool_prefix)}")
    )
    missing_file, missing_python = (
        shell_double_quoted(f"{tool_prefix} cannot run right now: ")
        + "$_ng_missing"
        + shell_double_quoted(f" does not exist (a redeploy in progress?); try again{moved_text(tool_prefix, python)}")
        for python in (False, True)
    )
    not_on_path = (
        shell_double_quoted(f"{tool_prefix} runs with ")
        + "$_ng_missing"
        + shell_double_quoted(
            f", which is not on PATH: put it there, or set {tool_prefix}_python in paths.py "
            f"to its full path, then run ./configure"
        )
    )
    not_executable = (
        shell_double_quoted(f"{tool_prefix}'s entry ")
        + entry_ref
        + shell_double_quoted(
            f" is not a Python script and not executable: chmod +x it, "
            f"or point {tool_prefix}_entry in paths.py at its start-up script"
        )
    )
    no_env_said = no_env_lines(
        4,
        [f'echo "{unconfigured_text(tool_prefix, "$_ng_real")}" >&2', "exit 127"],
        [f'echo "{installing_text(tool_prefix, "$_ng_real")}" >&2', "exit 127"],
        entry_published,
    )
    half_made_said = half_made_venv_lines(
        venv_var, 4, [f'echo "{installing_text(tool_prefix, "$_ng_real")}" >&2', "exit 127"]
    )
    lines.append(f"run_{tool_prefix}() (")
    lines.append(f"    {resolve} run_{tool_prefix}")
    lines.append(f'    if [ -z "${{{entry_var}:-}}" ]; then')
    lines.append(f'        if [ ! -d "${env_var}" ]; then')
    lines.append(f'            echo "{not_deployed}" >&2')
    lines.append("            exit 127")
    lines.append("        fi")
    lines.extend(f"    {line}" for line in no_env_said + half_made_said)
    lines.append(f'        echo "{shell_double_quoted(not_configured)}" >&2')
    lines.append("        exit 127")
    lines.append("    fi")
    # While a redeploy installs the tool again where it was, the values
    # found above may name its half-made .venv: none of them runs, as in
    # dependency_command (_being_installed).
    lines.extend(no_env_said + half_made_said)
    lines.append(f'    if [ -z "${{{venv_var}:-}}" ]; then')
    lines.append('        if [ -n "${PATH+set}" ]; then')
    lines.extend(venv_free_path_lines(12))
    lines.append("            PATH=${_ng_kept#:}")
    lines.append("        fi")
    lines.append("        unset VIRTUAL_ENV")
    lines.append("    else")
    lines.append(f'        export VIRTUAL_ENV="${venv_var}"')
    lines.append(f'        export PATH="${venv_var}/bin${{PATH:+:$PATH}}"')
    lines.append("        unset PYTHONHOME")
    lines.append("    fi")
    lines.append("    _ng_missing=''")
    lines.append("    _ng_python=''")
    lines.append(f'    if [ -n "${{{python_var}:-}}" ]; then')
    lines.append(f"        case ${python_var} in")
    lines.append(f'            */*) [ -e "${python_var}" ] || _ng_missing=${python_var} ;;')
    lines.append(f'            *) command -v "${python_var}" >/dev/null 2>&1 || _ng_missing=${python_var} ;;')
    lines.append("        esac")
    lines.append('        [ -z "$_ng_missing" ] || _ng_python=1')
    lines.append("    fi")
    lines.append(f'    if [ -z "$_ng_missing" ] && [ ! -e "${entry_var}" ]; then')
    lines.append(f"        _ng_missing=${entry_var}")
    lines.append("    fi")
    lines.append('    if [ -n "$_ng_missing" ]; then')
    lines.append(f'        if [ ! -d "${env_var}" ]; then')
    lines.append(f'            echo "{not_deployed}" >&2')
    lines.append("        else")
    lines.append("            case $_ng_missing in")
    lines.append("                */*)")
    lines.append('                    if [ -n "$_ng_python" ]; then')
    lines.append(f'                        echo "{missing_python}" >&2')
    lines.append("                    else")
    lines.append(f'                        echo "{missing_file}" >&2')
    lines.append("                    fi")
    lines.append("                    ;;")
    lines.append(f'                *) echo "{not_on_path}" >&2 ;;')
    lines.append("            esac")
    lines.append("        fi")
    lines.append("        exit 127")
    lines.append("    fi")
    lines.append(f'    if [ -z "${{{python_var}:-}}" ] && [ -f "${entry_var}" ] && [ ! -x "${entry_var}" ]; then')
    lines.append(f'        echo "{not_executable}" >&2')
    lines.append("        exit 126")
    lines.append("    fi")
    lines.append(f'    exec ${{{python_var}:+"${python_var}"}} "${entry_var}" "$@"')
    lines.append(")")

    order = (
        "_ng_real",
        "_ng_env",
        "_ng_venv",
        "_ng_entry",
        "_ng_root",
        "_ng_stale",
        "_ng_installing",
        "_ng_value",
        "_ng_folder",
        "_ng_line",
        "_ng_python",
        "_ng_skip",
        "_ng_kept",
        "_ng_rest",
        "_ng_dir",
        "_ng_here",
    )
    if temps:
        lines.append(f"unset {' '.join(name for name in order if name in temps)}")

    return "\n".join(lines) + "\n"

def stage_file(path, content, mode=None):
    """Write `content` to a new file next to `path`, for commit_file to move into place.

    Returns (temp, target). The target is `path`, or the file a symlink at
    `path` points to, which is where open(path, "w") wrote. Text is written as
    open(path, "w") writes it, bytes as they are. The new file gets `mode`,
    else the mode of the file it replaces, else the default for a new file
    (0o666 less the umask). Raises OSError when it cannot be written; the
    partly written file is removed.
    """
    target = os.path.realpath(path)
    fd, temp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=f".{os.path.basename(target)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb" if isinstance(content, bytes) else "w") as f:
            f.write(content)
        if mode is None:
            try:
                mode = os.stat(target).st_mode & 0o7777
            except OSError:
                mode = new_file_mode()
        os.chmod(temp, mode)
    except BaseException:
        discard_staged({path: (temp, target)})
        raise
    return temp, target

def new_file_mode():
    """The mode open(path, "w") gives a new file: 0o666 less the umask."""
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask

def stage_copy(source, path):
    """Copy file `source` to a new file next to `path`, for commit_file to move into place.

    Returns (temp, target), as stage_file does. shutil.copy2 keeps the mode of
    `source`, a read-only one too, and its times. Raises OSError when it cannot
    be written; the partly written file is removed.
    """
    target = os.path.realpath(path)
    fd, temp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=f".{os.path.basename(target)}.", suffix=".tmp")
    os.close(fd)
    try:
        shutil.copy2(source, temp)
    except BaseException:
        discard_staged({path: (temp, target)})
        raise
    return temp, target

def discard_staged(staged):
    """Remove the files stage_file wrote for `staged` ({path: (temp, target)})."""
    for temp, _target in staged.values():
        try:
            os.remove(temp)
        except OSError:
            pass

def stage_writes(writes):
    """Write every file in `writes` ({path: (content, mode)}) next to its place, or none.

    Returns {path: (temp, target)}. When one cannot be written (a read-only
    folder, a full disk), the ones already written are removed, and ./configure
    exits 1 with nothing changed: paths.py is written only after these.
    """
    staged: dict[str, tuple[str, str]] = {}
    # Whatever stops the run before commit_file moved them in leaves no temp files.
    atexit.register(discard_staged, staged)
    for path, (content, mode) in writes.items():
        try:
            staged[path] = stage_file(path, content, mode)
        except OSError as e:
            discard_staged(staged)
            print(f"{RED}✗{RESET} Could not write '{path}': {e.strerror or e}. Nothing else was changed.")
            sys.exit(1)
    return staged

def commit_file(staged, path):
    """Move the file staged for `path` into place, if one was staged; exits 1 when that fails."""
    if path not in staged:
        return
    # Taken out of `staged` only once moved: a signal that stops the run in
    # between still finds the file there to remove (catch_stop_signals).
    temp, target = staged[path]
    try:
        os.replace(temp, target)
    except OSError as e:
        discard_staged(staged)
        print(f"{RED}✗{RESET} Could not write '{path}': {e.strerror or e}.")
        sys.exit(1)
    del staged[path]

def read_text(path):
    """The text of file `path`, or None when it is missing or cannot be read."""
    try:
        with open(path, "r") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None

def is_generated_script(path):
    """True when ./configure wrote `path`: GENERATED_MARKER is in its first lines."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = [f.readline() for _ in range(3)]
    except OSError:
        return False
    return any(line.rstrip("\r\n") == GENERATED_MARKER for line in head)

def cleanup_old_shell_scripts(current_tools):
    """
    Remove shell scripts for tools that are no longer in paths.py
    
    Only scripts ./configure generated (GENERATED_MARKER) are removed. A
    setup_*_env.sh written by hand is gitignored like the generated ones, so
    deleting it would lose it for good: it is kept, and named in one line.

    Args:
        current_tools: Set of current tool prefixes from paths.py
    """
    # Find all existing setup_*_env.sh files
    existing_scripts = glob.glob("setup_*_env.sh")
    removed = 0

    for script_path in existing_scripts:
        # Extract tool prefix from filename (setup_TOOL_env.sh -> TOOL)
        filename = os.path.basename(script_path)
        if filename.startswith("setup_") and filename.endswith("_env.sh"):
            tool_prefix = filename[6:-7]  # Remove "setup_" and "_env.sh"

            # If this tool is no longer in paths.py, remove the script
            if tool_prefix not in current_tools:
                if not is_generated_script(script_path):
                    print(
                        f"{YELLOW}⚠{RESET}  Kept '{filename}': '{tool_prefix}' is not in paths.py, "
                        f"but ./configure did not write this file. Delete it if nothing uses it."
                    )
                    continue
                os.remove(script_path)
                removed += 1
                print(f"* Removed shell script for '{tool_prefix}' (no longer in paths.py)")

    return removed

# Module names in libs/ that are never imported by name, so two of them clash harmlessly.
UNIMPORTED_MODULES = {"setup", "conftest"}
# How many clashing module names in libs/ are named, one line each.
SHOWN_LIB_CLASHES = 5

def lib_import_folders(libs_dir):
    """The folders src/core/set_pythonpath.py puts on the import path for `libs_dir`, the first one first.

    libs/ itself, and each folder in it with an __init__.py, walked top-down in
    sorted order: the ones reached last come first, libs/ itself last. The
    same rule as add_nested_packages there.
    """
    dirs = [libs_dir]
    for root, subdirs, files in os.walk(libs_dir):
        subdirs.sort()
        if "__init__.py" in files and root not in dirs:
            dirs.insert(0, root)
    return dirs

def top_level_modules(folder):
    """{name: path} of what `import name` finds in `folder`: a name.py file, or a name/ package (it has __init__.py)."""
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return {}
    found = {}
    for entry in entries:
        path = os.path.join(folder, entry)
        if os.path.isfile(os.path.join(path, "__init__.py")):
            found[entry] = path  # a package wins over name.py in the same folder, as in Python
        elif entry.endswith(".py") and os.path.isfile(path):
            found.setdefault(entry[:-3], path)
    return {name: path for name, path in found.items() if name.isidentifier()}

def warn_shadowed_lib_modules(app_root):
    """Warn when two folders in libs/ on the import path provide a module of the same name.

    `import util` then imports the one in the folder that comes first
    (lib_import_folders), and code written for the other gets the wrong
    module without a word. One line per name, naming both files, once per
    run; set_pythonpath.py says nothing when the tool starts.
    """
    libs_dir = os.path.join(app_root, "libs")
    if not os.path.isdir(libs_dir):
        return
    imported: dict[str, str] = {}  # name -> the path `import name` finds
    clashes: list[tuple[str, str, str]] = []
    for folder in lib_import_folders(libs_dir):
        for name, path in top_level_modules(folder).items():
            if name in UNIMPORTED_MODULES or (name.startswith("__") and name.endswith("__")):
                continue
            first = imported.setdefault(name, path)
            if first != path and os.path.realpath(first) != os.path.realpath(path):
                if name not in (clash[0] for clash in clashes):
                    clashes.append((name, first, path))
    for name, first, other in clashes[:SHOWN_LIB_CLASHES]:
        print(
            f"{YELLOW}⚠{RESET}  Two folders in libs/ provide the module {BOLD}{name}{RESET}: "
            f"import {name} gets {os.path.relpath(first, app_root)}, never {os.path.relpath(other, app_root)}. "
            f"Rename one if code needs the other."
        )
    if len(clashes) > SHOWN_LIB_CLASHES:
        print(f"   ...and {len(clashes) - SHOWN_LIB_CLASHES} more module names that two folders in libs/ provide.")

def main():
    catch_stop_signals()
    # Held from the first read of paths.py to the last write, in every run: flags
    # change paths.py, and every run writes .env from it.
    lock_paths()
    config = load_config(CONFIG_PATH)
    config_present = os.path.exists(CONFIG_PATH)

    # Dynamically detect all dependency tools from paths.py, build --<tool>-*
    # flags from them, and apply any the user supplied before doing anything
    # else, so the rest of this run sees the updated values.
    detected_tools = detect_dependency_tools(config)
    parser = build_arg_parser(config, detected_tools)
    args, unknown = parser.parse_known_args()
    if unknown:
        report_unknown_args(parser, detected_tools, unknown)
    report_miscased_names(config, detected_tools)
    paths_update = apply_paths_overrides(config, args, CONFIG_PATH)

    # Fall back to a tool's own bundled .venv for any tool without an
    # explicit virtualenv, so paths.py doesn't need a host-specific path.
    # A dependency's .env written for another folder (a copy that was not
    # configured again) names that folder's virtualenv and script: not used.
    stale_envs = report_stale_envs(config, detected_tools)
    published_venvs: set[str] = set()
    ambiguous_venvs = resolve_tool_venvs(config, detected_tools, published=published_venvs, stale=stale_envs)
    explicit_entries = {tool for tool in detected_tools if config.get(f"{tool}_entry")}
    resolve_tool_entries(config, detected_tools, stale=stale_envs)
    follows = follow_dependency_env(config, detected_tools, published_venvs, ambiguous_venvs, explicit_entries)

    # Always ensure APP_ROOT is present in config
    app_root = os.path.abspath(".")
    if 'APP_ROOT' not in config:
        config['APP_ROOT'] = app_root

    # Ensure APP_NAME is set, from the first of these that gives one. APP_NAME in
    # paths.py, like APP_VENV and APP_ENTRY. Then [tool.ngargparser] app_name in
    # pyproject.toml, which ships in the tarball, so an edit to it reaches .env on
    # the next run. Then APP_NAME in this project's .env, for an older tool whose
    # pyproject.toml has no app_name; read from the file, not os.environ, because
    # a tool that started this one may have passed its own APP_NAME down. Last,
    # the build dir name pattern 'ng_<name>-local', or the directory name.
    if not config.get('APP_NAME'):
        env_app_name = None
        if os.path.isfile(DOT_ENV_PATH):
            env_app_name = (read_env_values(DOT_ENV_PATH) or {}).get('APP_NAME')
        base_name = os.path.basename(app_root)
        match = re.match(r'^ng[_-]([A-Za-z0-9_-]+?)-local$', base_name)
        config['APP_NAME'] = pyproject_app_name() or env_app_name or (match.group(1) if match else base_name)

    # Publish this project's own virtualenv so a tool that depends on it can
    # read it from .env instead of hunting for it. Re-derived every run, so a
    # deleted virtualenv stops being advertised. Two candidates publishes
    # nothing: the ambiguity belongs to whoever set the project up.
    # A virtualenv moved or copied here with the tool still points at its old
    # folder, so a tool that activates it would get another Python: it is not
    # published until it is made again.
    if 'APP_VENV' not in config:
        own_venvs = find_bundled_venvs(app_root)
        if len(own_venvs) == 1:
            made_for = venv_made_for(own_venvs[0])
            if made_for:
                name = os.path.basename(own_venvs[0])
                if is_deployed_copy(app_root):  # install.sh runs ./configure itself
                    remake = f"{BOLD}rm -r {name} && bash deploy/install.sh{RESET}, which runs ./configure again"
                else:
                    remake = f"{BOLD}rm -r {name} && uv sync{RESET}, then run ./configure again"
                print(
                    f"{YELLOW}⚠{RESET}  {own_venvs[0]} was made for {made_for}; recreate it: "
                    f"{remake}. Until then it is not published as APP_VENV."
                )
            else:
                config['APP_VENV'] = own_venvs[0]
        elif own_venvs:
            names = [os.path.basename(venv) for venv in own_venvs]
            remove, why = which_venvs_to_remove(names)
            print(
                f"{YELLOW}⚠{RESET}  Found {len(own_venvs)} virtualenvs in this tool ({', '.join(names)}); "
                f"publishing none as APP_VENV. Remove {remove}{why}, or set APP_VENV = \"<path>\" in paths.py. "
                f"Until then, tools that call this one run it with the system Python."
            )

    # Publish this project's start-up script the same way, so a tool that
    # depends on it runs $<TOOL>_ENTRY instead of hard-coding a file name.
    if 'APP_ENTRY' not in config:
        entry = own_entry(app_root)
        if entry:
            config['APP_ENTRY'] = entry
    elif config['APP_ENTRY'] and not os.path.isfile(os.path.join(app_root, str(config['APP_ENTRY']))):
        print(
            f"{YELLOW}⚠{RESET}  APP_ENTRY in paths.py names '{config['APP_ENTRY']}', which does not exist. "
            f"Publishing it anyway."
        )

    # Name the .env keys that follow a dependency's own .env, so the tool reads
    # them from there again when it starts: the copies below go stale as soon
    # as that dependency is redeployed. Values set in paths.py are never listed.
    from_deps = [
        f"{tool.upper()}_{suffix}"
        for tool, follow in follows.items()
        for suffix, follows_key in (("VENV", follow[0]), ("ENTRY", follow[1]))
        if follows_key
    ]
    if from_deps:
        config[KEYS_FROM_DEPS] = ",".join(from_deps)

    # A dependency whose <tool>_path is not set stops the run with exit 1. That
    # is known before anything is written, so the run stops here and changes
    # nothing. Every such path is named in one message, with one command that
    # sets them all (report_unset_paths), after the warnings about the others.
    unset = [tool_prefix for tool_prefix in detected_tools if path_is_unset(config.get(f"{tool_prefix}_path"))]
    if unset:
        print(f"* Detected {len(detected_tools)} dependency tools: {', '.join(detected_tools.keys())}")
        for tool_prefix in detected_tools:
            report_tool_settings(config, tool_prefix, warn_no_venv=tool_prefix not in ambiguous_venvs)
        report_unset_paths(unset, args)
        if paths_update:
            print("  Nothing was changed, paths.py included: run ./configure again with every flag you gave.")
        else:
            print("  Nothing was changed.")
        sys.exit(1)

    warn_shadowed_lib_modules(app_root)

    # Regenerate .env file based on current paths.py content (this ensures
    # removed dependencies are cleaned up), but skip the write entirely when
    # nothing would actually change.
    env_content = render_env_content(config, app_root)
    existing_content = None
    env_existed = os.path.exists(DOT_ENV_PATH)
    # The new .env keeps the mode of the old one, unless that is why it could
    # not be read (chmod 000): then it gets the mode of a new file.
    env_mode = None
    if env_existed:
        try:
            with open(DOT_ENV_PATH, "r") as f:
                existing_content = f.read()
        except (OSError, UnicodeDecodeError) as e:  # not UTF-8 text, or not a file: written again
            warn_unreadable_env(DOT_ENV_PATH, e)
            if isinstance(e, OSError):
                env_mode = new_file_mode()

    # Every file this run changes is written next to its place first, and moved
    # in only once all of them are written: a run that cannot write one of them
    # changes nothing. paths.py and its backup come last, after .env and the
    # setup scripts, in both steps.
    scripts = {}
    for tool_prefix in detected_tools:
        script = render_shell_script(
            config,
            tool_prefix,
            venv_published=follows.get(tool_prefix, (False, False))[0],
            entry_published=follows.get(tool_prefix, (False, False))[1],
        )
        if script is not None:
            scripts[f"setup_{tool_prefix}_env.sh"] = script
    writes: dict[str, tuple[str, Optional[int]]] = {}
    if existing_content != env_content:
        writes[DOT_ENV_PATH] = (env_content, env_mode)
    for path, script in scripts.items():
        if read_text(path) != script:
            writes[path] = (script, 0o755)
    staged = stage_writes(writes)
    # A setup script for a current dependency that ./configure did not write
    # (no GENERATED_MARKER) was written by hand: it is kept as <script>.bak,
    # moved in just before the new one, and named in one line.
    for path in scripts:
        if path in staged and os.path.isfile(path) and not is_generated_script(path):
            try:
                staged[f"{path}.bak"] = stage_copy(path, f"{path}.bak")
            except OSError as e:
                discard_staged(staged)
                print(f"{RED}✗{RESET} Could not write '{path}.bak': {e.strerror or e}. Nothing else was changed.")
                sys.exit(1)
    if paths_update:
        paths_update.stage(staged)

    if existing_content == env_content:
        action = "unchanged"
    else:
        commit_file(staged, DOT_ENV_PATH)
        action = "updated" if env_existed else "created"

    if not config or (set(config.keys()) <= APP_KEYS):
        print(f"* Minimal .env file {action} (no external dependencies declared).")
    else:
        print(f"* .env file {action}")
    if action == "updated" and existing_content is not None:
        warn_removed_keys(existing_content, env_content)

    # Clean up shell scripts for removed dependencies
    removed = cleanup_old_shell_scripts(set(detected_tools.keys()))

    if not detected_tools:
        # Only surface this line when there's something to report: either paths.py
        # exists (so the user might expect deps) or we cleaned up stale scripts.
        # When paths.py is absent and nothing was cleaned, load_config's info line
        # already covers the state.
        # No flag exists without a dependency (build_arg_parser), so there is
        # no paths.py to write here.
        if config_present or removed:
            print("* No dependency tools declared in paths.py")
        return

    print(f"* Detected {len(detected_tools)} dependency tools: {', '.join(detected_tools.keys())}")

    # Create shell scripts for each detected tool. Every <tool>_path is set: a
    # run with one unset stopped above, before anything was written.
    for tool_prefix in detected_tools.keys():
        report_tool_settings(config, tool_prefix, warn_no_venv=tool_prefix not in ambiguous_venvs)
        output_path = f"setup_{tool_prefix}_env.sh"
        if output_path in staged:
            if f"{output_path}.bak" in staged:
                commit_file(staged, f"{output_path}.bak")
                print(
                    f"{YELLOW}⚠{RESET}  ./configure did not write '{output_path}': "
                    f"moved it to '{output_path}.bak', and wrote a new one from paths.py."
                )
            commit_file(staged, output_path)
        elif os.stat(output_path).st_mode & 0o7777 != 0o755:  # unchanged, but made executable as before
            try:
                os.chmod(output_path, 0o755)
            except OSError as e:
                print(f"{RED}✗{RESET} Could not write '{output_path}': {e.strerror or e}.")
                sys.exit(1)
        print(f"* Shell script for '{tool_prefix}' created at '{output_path}'")

    if paths_update:
        paths_update.commit(staged)

if __name__ == "__main__":
    try:
        main()
    finally:
        flush_output()
