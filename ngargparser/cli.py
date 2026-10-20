import argparse
import filecmp
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TypedDict

from ngargparser.dep_names import (
    DEP_FIELDS,
    DEP_NAME_FIELD_SUFFIXES,
    invalid_dep_name,
    normalize_name,
)

# Get the absolute path of the followings
CURR_FILE_PATH = Path(__file__).resolve()
NGPARSER_DIR = CURR_FILE_PATH.parent
TEMPLATE_DIR = NGPARSER_DIR / "templates"
EXAMPLE_DIR = TEMPLATE_DIR / "example-app"


def get_version():
    try:
        import importlib.metadata

        return importlib.metadata.version("ngargparser")
    except Exception:
        return "unknown"


__version__ = get_version()

# The colour codes cli writes (\033[91m ... \033[0m), and what makes them go (see print below).
_COLOUR_CODE_RE = re.compile(r"\033\[[0-9;]*m")


def _colour_wanted(stream):
    """Whether output to `stream` keeps its colour codes: the stream is a terminal and NO_COLOR
    is unset or empty (https://no-color.org), the rule build.sh and ./configure follow too."""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):  # no isatty, or a stream that was closed
        return False


def print(*args, sep=" ", end="\n", file=None, flush=False):
    """builtins.print, which every print in this module calls through here: without the colour
    codes when the stream it writes to (`file`, else sys.stdout) is not a terminal, or NO_COLOR
    is set (see _colour_wanted). `cli deps list | cat` and a log file get plain text."""
    import builtins
    import sys

    stream = sys.stdout if file is None else file
    if stream is not None and not _colour_wanted(stream):
        args = tuple(_COLOUR_CODE_RE.sub("", arg if isinstance(arg, str) else str(arg)) for arg in args)
    try:
        builtins.print(*args, sep=sep, end=end, file=file, flush=flush)
    except (OSError, ValueError) as e:
        if stream is not None and stream is sys.stdout:
            _note_output_lost(e)  # so the sync that stops on it knows its output is what failed
        raise


# A project name becomes a directory, a Python module (run_<name>.py), and a
# class prefix, so it must be a single safe path segment — no separators,
# parent refs, absolute paths, or leading dash (which argparse-style tools and
# shells treat as a flag). validate_project_name also requires a letter or digit
# at the end, as a Python package name does.
PROJECT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def validate_project_name(name):
    """Return an error message if `name` is unsafe to scaffold, else None.

    Guards against path traversal (``../foo``), absolute paths, nested paths
    (``a/b``), and names that aren't a valid leading directory/module segment.
    """
    if not name or not name.strip():
        return "Tool name must not be empty."
    if os.path.isabs(name) or name.startswith("~"):
        return f"Tool name must be a plain name, not a path: '{name}'."
    seps = {os.sep} | ({os.altsep} if os.altsep else set())
    if any(s in name for s in seps):
        return f"Tool name must not contain path separators: '{name}'."
    if not PROJECT_NAME_RE.match(name):
        return (
            f"Invalid tool name '{name}'. Use a letter followed by letters, "
            "digits, underscores, or hyphens (e.g. 'aa-counter')."
        )
    # The name is also the Python package name in pyproject.toml, which must end in a
    # letter or digit: uv lock fails on 'a-'.
    if not name[-1].isalnum():
        return (
            f"Invalid tool name '{name}'. End it with a letter or digit: the name is also the "
            f"Python package name, which cannot end in '{name[-1]}' (e.g. 'aa-counter')."
        )
    # The name is the tool's Python package name, so it cannot be one the tool needs:
    # uv refuses a package that depends on itself.
    taken = _reserved_tool_names().get(_package_key(name))
    if taken:
        return (
            f"Invalid tool name '{name}'. {taken}, and the tool's name is also its Python package name, "
            "so the two would clash. Pick another name (e.g. 'aa-counter')."
        )
    # Belt-and-suspenders: the resolved target must live directly in the CWD.
    if (Path.cwd() / name).resolve().parent != Path.cwd().resolve():
        return f"Tool name would resolve outside the current directory: '{name}'."
    return None


def _package_key(name):
    """`name` as Python package names compare (PEP 503): python_dotenv and Python-Dotenv are python-dotenv."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _reserved_tool_names():
    """{package key: why a tool cannot take that name}: each package a new tool's
    pyproject.toml lists in its dependencies or dependency groups, the module
    python-dotenv installs (dotenv), and ngargparser itself."""
    reserved = {
        "pytest": "A new tool has pytest in its dev dependency group",
        "python-dotenv": "Every tool depends on python-dotenv",
        "dotenv": "Every tool depends on python-dotenv, which installs the module dotenv",
        "ngargparser": "ngargparser is the framework's own package",
    }
    try:
        with open(TEMPLATE_DIR / "pyproject.toml.tmpl", encoding="utf-8") as f:
            data = _parse_pyproject(f.read())
    except OSError:
        data = None
    if isinstance(data, dict):
        project, groups = data.get("project"), data.get("dependency-groups")
        project = project if isinstance(project, dict) else {}
        groups = groups if isinstance(groups, dict) else {}
        listed = [("dependencies", req) for req in project.get("dependencies") or []]
        listed += [(f"{group} dependency group", req) for group, reqs in groups.items() for req in reqs or []]
        for where, req in listed:
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req) if isinstance(req, str) else None
            if match and _package_key(match.group(1)) not in reserved:
                reserved[_package_key(match.group(1))] = f"A new tool lists {match.group(1)} in its {where}"
    return reserved


def _replace_file(path, data, mode=None, *, follow_link=True, writable_only=False):
    """Write `data` (text, written as UTF-8 the way open(path, "w") writes it, or bytes)
    to `path` through a temporary file in the same folder that is renamed over it.

    `path` is never left cut short: when the write fails (a full disk, a file size
    limit, a read-only folder), `path` stays as it was and the temporary file goes.
    An OSError names `path`, not the temporary file. The new file gets `mode` when
    given, else the mode `path` has (a new file: the one open() would give it). A
    `path` that is a symlink stays one, and the file it points at is replaced, unless
    `follow_link` is False. With `writable_only`, an existing file that is not
    writable is refused (PermissionError), as open(path, "w") refuses it."""
    import stat
    import tempfile

    target = os.path.realpath(path) if follow_link and os.path.islink(path) else path
    try:
        if writable_only and os.path.exists(target) and not os.access(target, os.W_OK):
            raise PermissionError(13, "Permission denied", path)
        if mode is None:
            try:
                mode = stat.S_IMODE(os.stat(target).st_mode)
            except FileNotFoundError:
                umask = os.umask(0)
                os.umask(umask)
                mode = 0o666 & ~umask
        fd, temporary = tempfile.mkstemp(
            prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=os.path.dirname(target) or "."
        )
    except OSError as e:
        raise (OSError(e.errno, e.strerror, path) if e.errno is not None else e) from e
    try:
        if isinstance(data, bytes):
            with os.fdopen(fd, "wb") as out:
                out.write(data)
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException as e:
        if os.path.lexists(temporary):
            os.remove(temporary)
        if isinstance(e, OSError) and e.errno is not None:
            raise OSError(e.errno, e.strerror, path) from e
        raise


def _backup_file(path):
    """Copy `path` to `path`.bak before a destructive rewrite; return the backup
    path (or None if the source didn't exist).

    The copy goes through a temporary file renamed over the old backup (see
    _replace_file), so a copy that fails leaves the old backup whole. It gets the
    mode of `path`, as shutil.copy2 gave it, so a read-only file leaves a read-only
    backup; the next backup replaces it rather than writing into it."""
    import stat

    if not os.path.exists(path):
        return None
    backup_path = f"{path}.bak"
    with open(path, "rb") as f:
        data = f.read()
    _replace_file(backup_path, data, stat.S_IMODE(os.stat(path).st_mode), follow_link=False)
    return backup_path


GITIGNORE_MANAGED_MARKER = "# --- ngargparser (managed) ---"
# Each group is a comment and the rules it explains. A group's comment is
# written only together with at least one of its rules.
GITIGNORE_MANAGED_RULES = (
    (
        "# Runtime artifacts written by `cli sync` / `cli deps` — keep out of git.",
        (".ngargparser/*", "*.bak"),
    ),
    (
        "# Written by ./configure with this machine's paths; rebuilt on each host.",
        (".env", "setup_*_env.sh"),
    ),
    (
        "# The framework file hashes stay in git, so cli sync spots local edits in a fresh clone.",
        ("!.ngargparser/framework-hashes.json",),  # _FRAMEWORK_HASHES
    ),
)
# Managed rules an earlier release wrote, and the rule that replaces each inside the
# managed section. git cannot re-include a file under an ignored folder, so `.ngargparser/`
# became `.ngargparser/*` when the hash record started to travel with the tool.
GITIGNORE_REPLACED_RULES = {".ngargparser/": ".ngargparser/*"}
GITIGNORE_MANAGED_BLOCK = "".join(
    f"{line}\n"
    for line in [GITIGNORE_MANAGED_MARKER]
    + [line for comment, rules in GITIGNORE_MANAGED_RULES for line in (comment, *rules)]
)
GITIGNORE_BASE = """# Python
__pycache__/
*.py[cod]
*.egg-info/
.eggs/
build/
dist/

# Virtual environments
.venv/
venv/

# OS / editor cruft
.DS_Store
"""


def _gitignore_has_rule(lines, rule):
    """True when `lines` already ignore `rule` (as `rule`, `/rule` or `**/rule`),
    or re-include it with `!`, which means the project chose to track it. For a
    re-include rule (`!path`), a line that ignores `path` counts as well: the
    project chose to keep it out of git."""
    path = rule[1:] if rule.startswith("!") else rule
    forms = {path, f"/{path}", f"**/{path}"}
    return any(line in forms or (line.startswith("!") and line[1:] in forms) for line in lines)


def _missing_managed_lines(content):
    """The managed rules `content` doesn't cover yet, each group under its comment."""
    lines = [line.strip() for line in content.splitlines()]
    missing = []
    for comment, rules in GITIGNORE_MANAGED_RULES:
        absent = [rule for rule in rules if not _gitignore_has_rule(lines, rule)]
        if absent:
            missing += [comment, *absent]
    return missing


def _managed_section(parts):
    """(start, end) of the managed section in .gitignore lines `parts`: it runs from
    the marker line to the first blank line, or to the end of the file."""
    start = next((i for i, line in enumerate(parts) if line.strip() == GITIGNORE_MANAGED_MARKER), len(parts))
    end = start + 1
    while end < len(parts) and parts[end].strip():
        end += 1
    return start, min(end, len(parts))


def _managed_rules_misordered(rules):
    """True when, in the managed section's rule lines `rules`, a managed `!` rule comes
    before a managed ignore rule: git reads the lines in order, so the later ignore
    rule would ignore again what the `!` rule keeps (the hash record)."""
    managed = {rule for _comment, group in GITIGNORE_MANAGED_RULES for rule in group}
    seen_reinclude = False
    for rule in rules:
        if rule not in managed:
            continue
        if rule.startswith("!"):
            seen_reinclude = True
        elif seen_reinclude:
            return True
    return False


def _canonical_managed_section(section, missing, nl):
    """The managed section's lines (after its marker) rewritten in the order of
    GITIGNORE_MANAGED_RULES: each group's comment, then its rules that were in the
    section or are `missing`. Other lines the section held (your own) follow, in
    their own order. Each line ends with `nl`."""
    rules = {rule for _comment, group in GITIGNORE_MANAGED_RULES for rule in group}
    comments = {comment for comment, _group in GITIGNORE_MANAGED_RULES}
    stripped = [line.strip() for line in section]
    wanted = set(missing) | {line for line in stripped if line in rules}
    lines = []
    for comment, group in GITIGNORE_MANAGED_RULES:
        present = [rule for rule in group if rule in wanted]
        if present:
            lines += [comment, *present]
    others = [
        line.rstrip("\r\n") for line, rule in zip(section, stripped) if rule not in rules and rule not in comments
    ]
    return [f"{line}{nl}" for line in lines + others]


def ensure_gitignore(path=".gitignore", *, dry_run=False):
    """Ensure `path` exists and covers every ngargparser-managed ignore rule.

    Idempotent: creates the file (base ignores + managed block) when missing,
    appends the managed block when the file exists without it, and adds rules a
    newer release introduced to an existing managed section. A rule the file
    already covers, or re-includes with `!`, is never added. Inside the managed
    section, a rule an earlier release wrote is replaced by its current form
    (GITIGNORE_REPLACED_RULES); a user's own entries are never rewritten. A
    section that changes is written in the order of GITIGNORE_MANAGED_RULES
    (see _canonical_managed_section), so an ignore rule never lands after the `!`
    rule that keeps the hash record in git; a section whose `!` rule already sits
    before a managed ignore rule is put back in that order too. The file keeps
    its line endings ("\\r\\n" stays "\\r\\n", and a line added gets the file's
    ending) and a UTF-8 byte order mark at its start.
    Returns 'created' | 'updated' | 'unchanged'. With `dry_run`, reports the
    action without writing. The file is written through a temporary file renamed
    over it (see _replace_file), so a write that fails (a full disk) raises the
    OSError and leaves the file as it was; a file that is not writable stays as it
    is too (PermissionError).
    """
    if not os.path.exists(path):
        if not dry_run:
            _replace_file(path, GITIGNORE_BASE.rstrip() + "\n\n" + GITIGNORE_MANAGED_BLOCK, writable_only=True)
        return "created"

    with open(path, encoding="utf-8", newline="") as f:
        content = f.read()
    bom = _BOM if content.startswith(_BOM) else ""
    content = content[len(bom) :]
    nl = "\r\n" if "\r\n" in content else "\n"  # a line added follows the file's own line endings
    parts = content.splitlines(keepends=True)
    replaced = misordered = False
    if GITIGNORE_MANAGED_MARKER in content:
        start, end = _managed_section(parts)
        for i in range(start + 1, end):
            rule = parts[i].strip()
            if rule in GITIGNORE_REPLACED_RULES:
                parts[i] = parts[i].replace(rule, GITIGNORE_REPLACED_RULES[rule], 1)
                replaced = True
        misordered = _managed_rules_misordered([part.strip() for part in parts[start + 1 : end]])
    missing = _missing_managed_lines("".join(parts))
    if not missing and not replaced and not misordered:
        return "unchanged"

    if GITIGNORE_MANAGED_MARKER not in content:
        if not dry_run:
            separator = "" if content.endswith("\n") else nl
            block = "".join(f"{line}{nl}" for line in [GITIGNORE_MANAGED_MARKER, *missing])
            # The whole file, as bytes: its own line endings stay as they are.
            _replace_file(path, f"{bom}{content}{separator}{nl}{block}".encode("utf-8"), writable_only=True)
        return "updated"

    start, end = _managed_section(parts)
    parts[start + 1 : end] = _canonical_managed_section(parts[start + 1 : end], missing, nl)
    if parts[start] and not parts[start].endswith("\n"):
        parts[start] += nl  # the marker was the file's last line
    if not dry_run:
        _replace_file(path, (bom + "".join(parts)).encode("utf-8"), writable_only=True)
    return "updated"


def _mode_fix(src, dst, executable):
    """(current mode, the mode `dst` should have) when framework file `dst`, whose
    content matches `src`, has the wrong execute bits; else None.

    Only the files whose mode matters are checked: an `executable` one (build.sh,
    configure, configure.py) needs an execute bit wherever it has a read bit, and
    another file in src/core/ has execute bits only when its template `src` does.
    Read and write bits stay as they are, so a group-writable clone stays so. Not
    checked on Windows, where files have no execute bits to set."""
    import stat

    if os.name == "nt" or not (executable or dst.replace(os.sep, "/").startswith("src/core/")):
        return None
    mode = stat.S_IMODE(os.stat(dst).st_mode)
    wanted = _with_framework_execute_bits(src, dst, executable, mode)
    return None if wanted == mode else (mode, wanted)


def _with_framework_execute_bits(src, dst, executable, mode):
    """`mode` with the execute bits framework file `dst` should have, by the rules of
    _mode_fix; the other bits as they are. `mode` itself for a file whose mode does
    not matter, and on Windows."""
    if os.name == "nt" or not (executable or dst.replace(os.sep, "/").startswith("src/core/")):
        return mode
    if executable or os.stat(src).st_mode & 0o111:
        return (mode & ~0o111) | ((mode & 0o444) >> 2)
    return mode & ~0o111


def _current_umask():
    """The process's umask, which os.umask can only read by setting it: it is put back at once."""
    umask = os.umask(0)
    os.umask(umask)
    return umask


def _new_file_mode(src, dst, executable):
    """The mode a framework file `dst` that sync creates gets: what open() would give a
    new file (0o666 without the umask's bits), with the execute bits _mode_fix wants, so
    an executable one is executable wherever it is readable. A umask of 077 gives 600 and
    700, not the template's 644 and 755."""
    return _with_framework_execute_bits(src, dst, executable, 0o666 & ~_current_umask())


def _write_atomically(src, dst, mode=None):
    """Copy `src` over `dst` the way shutil.copy does (content and `src`'s mode;
    `mode` instead when given), through a temporary file in the same folder that
    is renamed over `dst`. A program that has `dst` open, such as bash running
    scripts/core/build.sh in `make build`, goes on reading the old file. A `dst`
    that is a symlink stays one: the file it points at is replaced."""
    import tempfile

    target = os.path.realpath(dst) if os.path.islink(dst) else dst
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(target)}.", suffix=".ngargparser-sync", dir=os.path.dirname(target) or "."
    )
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as source:
            shutil.copyfileobj(source, out)
        shutil.copymode(src, temporary)
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        if os.path.lexists(temporary):
            os.remove(temporary)
        raise


def _sync_file(src, dst, *, executable=False, dry_run=False, backup_root=None, changes=None, before_write=None):
    """Copy `src` to `dst` only when their contents differ (or `dst` is missing).
    `before_write`, when given, is called once it is known that `dst` is written (its
    content or its mode), just before it is; never in a dry run.

    Records ``('created'|'updated'|'mode'|'unchanged', dst)`` in `changes`. When an
    existing file would be overwritten and `backup_root` is set, its current
    content is first copied under `backup_root` (preserving `dst`'s relative
    path), unless `dry_run` is set, in which case nothing is written. The new
    content is written atomically (see _write_atomically); a replaced file keeps
    its mode, with the execute bits _mode_fix sets, and a new one follows the
    umask (see _new_file_mode). A file whose content
    matches but whose execute bits are wrong (see _mode_fix) gets its mode fixed:
    'mode', or 'mode-denied' (recorded as unchanged) when the file is someone
    else's and its mode cannot be changed. Returns the action string. A write to
    `dst` that fails raises _SyncWriteError, with `dst` as it was.
    """
    exists = os.path.exists(dst)
    if exists and filecmp.cmp(src, dst, shallow=False):
        fix = _mode_fix(src, dst, executable)
        action = "mode" if fix else "unchanged"
        if fix and not dry_run:
            if before_write is not None:
                before_write()
            try:
                os.chmod(dst, fix[1])
            except PermissionError:
                action = "mode-denied"
            except OSError as e:  # a read-only file system
                raise _SyncWriteError(dst, e) from e
        if changes is not None:
            changes.append(("unchanged" if action == "mode-denied" else action, dst))
        return action

    action = "updated" if exists else "created"
    if not dry_run:
        if before_write is not None:
            before_write()
        if exists and backup_root is not None:
            backup_path = os.path.join(backup_root, dst)
            os.makedirs(os.path.dirname(backup_path) or ".", exist_ok=True)
            shutil.copy2(dst, backup_path)
        # A write that fails (a read-only folder, a full disk) changes nothing: the new
        # content goes to a temporary file first. It is reported as _SyncWriteError.
        try:
            parent = os.path.dirname(dst)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if exists:
                # A replaced file keeps its mode (a group-writable clone stays so), with the
                # execute bits fixed the way a mode-only fix does.
                import stat

                mode = _with_framework_execute_bits(src, dst, executable, stat.S_IMODE(os.stat(dst).st_mode))
            else:
                # A new file follows the umask, as one open() creates does (see _new_file_mode).
                mode = _new_file_mode(src, dst, executable)
            _write_atomically(src, dst, mode)
        except OSError as e:
            raise _SyncWriteError(dst, e) from e
    if changes is not None:
        changes.append((action, dst))
    return action


def format_project_name(name, capitalize=False):
    """`name` with each '-' as '_' (aa-counter: aa_counter, the name in run_<name>.py).
    With `capitalize`, the class name prefix instead: each part between '-' or '_'
    gets an upper-case first letter and keeps the rest as written, so my-tool,
    my_tool and MyTool all give MyTool."""
    name = name.replace("-", "_")

    if capitalize:
        name = "".join(part[:1].upper() + part[1:] for part in name.split("_"))

    return name


def write_pyproject_toml(project_dir, tool_name):
    """Render templates/pyproject.toml.tmpl into project_dir with {TOOL_NAME} substituted,
    {TOOL_EXEC_NAME} with the name in the entry script's file name (run_<it>.py), and
    {NGARGPARSER_VERSION} with the scaffold_version stamp (see _sync_version), then
    attempt to generate uv.lock via `uv lock` if uv is available."""
    import subprocess

    template_path = TEMPLATE_DIR / "pyproject.toml.tmpl"
    target_path = os.path.join(project_dir, "pyproject.toml")

    shutil.copy(template_path, target_path)
    replace_text_in_place(target_path, "{TOOL_NAME}", tool_name)
    # The same name create_project_structure gives src/run_<name>.py (aa-counter: run_aa_counter.py).
    replace_text_in_place(target_path, "{TOOL_EXEC_NAME}", format_project_name(tool_name))
    # The version cli sync stamps (a checkout's has its commit: 0.4.1+g123abcd), so the
    # first sync of a new tool changes nothing.
    replace_text_in_place(target_path, "{NGARGPARSER_VERSION}", _sync_version())

    if shutil.which("uv"):
        try:
            subprocess.run(["uv", "lock"], cwd=project_dir, check=True)
            print(f"\033[92m✓\033[0m Generated 'uv.lock' for '{project_dir}'.")
        except subprocess.CalledProcessError as e:
            print(f"\033[93m⚠\033[0m  'uv lock' failed (exit {e.returncode}); run it manually in '{project_dir}'.")
    else:
        print(
            "\033[93m⚠\033[0m  'uv' not found on PATH; skipping 'uv lock'. Run 'uv lock' inside the tool once uv is installed."
        )


# A TOML table header line ([name] or [[name]], an optional trailing comment).
_TOML_HEADER_RE = re.compile(r"^[ \t]*\[\[?[ \t]*([^\]]+?)[ \t]*\]\]?[ \t]*(?:#.*)?$")


def _scaffold_stamp(content, key="scaffold_version"):
    """Find [tool.ngargparser] and its `key` (scaffold_version, app_name) in pyproject.toml text.

    Returns (header_end, value_span): header_end is the offset just past the
    [tool.ngargparser] header line, or None when there is no such table, and
    value_span is the (start, end) of the `key` string value inside that
    table, or None when it has none. Line based: only a header line ends the
    table, so a "[" in a comment or a value (`# see [release notes]`) does not.
    Reads a single-line "basic" or 'literal' string; any other form (a
    multi-line string, an escape, a dotted key) is left to _ngargparser_value.
    """
    key_line = re.compile(rf"""^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(?:"([^"\\\r\n]*)"|'([^'\r\n]*)')[ \t]*(?:#.*)?$""")
    header_end = None
    offset = 0
    for line in content.splitlines(keepends=True):
        header = _TOML_HEADER_RE.match(line.rstrip("\r\n"))
        if header:
            if header_end is not None:
                break  # the next table starts
            if header.group(1) == "tool.ngargparser":
                header_end = offset + len(line)
        elif header_end is not None:
            stamp = key_line.match(line.rstrip("\r\n"))
            if stamp:
                group = 1 if stamp.group(1) is not None else 2
                return header_end, (offset + stamp.start(group), offset + stamp.end(group))
        offset += len(line)
    return header_end, None


def _toml_reader():
    """tomllib (Python 3.11+), else tomli (the same parser) when installed, else None."""
    import sys

    # A version check, not a try: mypy then reads one import for each Python version.
    if sys.version_info >= (3, 11):
        import tomllib

        return tomllib
    try:
        import tomli  # the same parser, when installed
    except ImportError:
        return None
    return tomli


def _parse_pyproject(content):
    """`content` read by a TOML parser: a dict, or None when no parser is installed
    or `content` is not valid TOML."""
    toml_reader = _toml_reader()
    if toml_reader is None:
        return None
    try:
        return toml_reader.loads(content)
    except toml_reader.TOMLDecodeError:
        return None


def _ngargparser_section(data):
    """The [tool.ngargparser] table of parsed pyproject `data`, in any form TOML allows, or None."""
    tool = data.get("tool") if isinstance(data, dict) else None
    return tool.get("ngargparser") if isinstance(tool, dict) else None


_NOT_A_STRING = object()  # a [tool.ngargparser] value the line reader sees is not a quoted string


def _ngargparser_setting(content, key):
    """(found, value): whether pyproject text `content`'s [tool.ngargparser] table sets
    `key`, and its value.

    Read with tomllib or tomli when one is installed and the text parses, so every
    form TOML allows counts (a dotted key, an inline table, a multi-line string), and
    the value has the type TOML gives it (a str; a float for 0.5; a bool). Else the
    line reader reads a single-line "..." or '...' value, and gives _NOT_A_STRING for
    a `key = ...` line in the table that holds anything else."""
    data = _parse_pyproject(content)
    if data is not None:
        table = _ngargparser_section(data)
        if isinstance(table, dict) and key in table:
            return True, table[key]
        return False, None
    span = _scaffold_stamp(content, key)[1]
    if span:
        return True, content[span[0] : span[1]]
    if _ngargparser_line(content, key) is not None:
        return True, _NOT_A_STRING
    return False, None


def _ngargparser_line(content, key):
    """What follows `key =` on the `key = ...` line of pyproject text `content`'s
    [tool.ngargparser] table, without the spaces around it, or None when the table
    has no such line. Line based, like _scaffold_stamp."""
    key_line = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(.*?)[ \t]*$")
    in_table = False
    for line in content.splitlines():
        header = _TOML_HEADER_RE.match(line)
        if header:
            if in_table:
                break  # the next table starts
            in_table = header.group(1) == "tool.ngargparser"
        elif in_table:
            found = key_line.match(line)
            if found:
                return found.group(1)
    return None


def _without_comment(value):
    """A TOML value as written on its line, without a trailing # comment. For a bare
    value (a number, true); a quoted string that holds a # is cut there too."""
    return re.sub(r"[ \t]*#.*$", "", value)


def _ngargparser_value(content, key):
    """The string `key` in pyproject text `content`'s [tool.ngargparser] table, or None
    (also when it is set to something that is not a string). See _ngargparser_setting."""
    found, value = _ngargparser_setting(content, key)
    return value if found and isinstance(value, str) else None


def _scaffold_stamp_text(content):
    """(stamp, problem) for pyproject text `content`: its scaffold_version as text to
    compare as a version, and what is wrong with it when it cannot be compared.

    A string is the stamp as it is. A number (scaffold_version = 0.5, which TOML reads
    as a float) is the version it spells, as written in the file when a line holds it.
    Any other value (true, a list), and with no TOML parser installed anything that is
    not a quoted string, gives no stamp and a `problem` that says what the value is."""
    found, value = _ngargparser_setting(content, "scaffold_version")
    if not found:
        return None, None
    if isinstance(value, str):
        return value, None
    written = _ngargparser_line(content, "scaffold_version")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        spelled = _without_comment(written or "").replace("_", "")
        return (spelled if _version_key(spelled) else str(value)), None
    if not written:
        import json

        written = json.dumps(value, default=str)
    return None, f"scaffold_version = {_without_comment(written)}"


def _toml_string(value, quote='"'):
    """`value` as a single-line TOML string: 'literal' when `quote` is ' and the
    value fits in one, else "basic" (escaped where needed)."""
    import json

    if quote == "'" and not any(c in value for c in "'\r\n"):
        return f"'{value}'"
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string


# [tool.ngargparser] written as something other than its own table header: an inline
# table or dotted keys (`ngargparser = {...}` under [tool], `tool.ngargparser.x = ...`),
# or a sub-table header ([tool.ngargparser.x]). Read only when no TOML parser is there.
_NGARGPARSER_ELSEWHERE_RE = re.compile(
    r"^[ \t]*(?:tool[ \t]*\.[ \t]*)?ngargparser[ \t]*[.=]|^[ \t]*\[[ \t]*tool[ \t]*\.[ \t]*ngargparser[ \t]*\.",
    re.MULTILINE,
)


def _plan_ngargparser_key(content, key, value, path="pyproject.toml"):
    """Work out how to write the string `key` = `value` into the [tool.ngargparser]
    table of pyproject text `content`, without writing anything.

    Returns (previous, new_content). `previous` is the key's current value, or None
    when it is not set. `new_content` is the edited text (`content` itself when it
    already holds `value`), or None when the table or the key is written in a form
    this line editor does not change (an inline table, a dotted key, a multi-line
    string): the key has to be set by hand. A replaced value keeps its quote style.
    Raises ValueError when `content` is not valid TOML to begin with.
    """
    found, current = _ngargparser_setting(content, key)
    if found and current == value:
        # Already set, as a TOML parser reads it: a line inside a multi-line string that
        # looks like the key is not the key, and is never edited.
        return current, content
    header_end, span = _scaffold_stamp(content, key)
    if span:
        previous = content[span[0] : span[1]]
        if previous == value:
            return previous, content
        start, end = span[0] - 1, span[1] + 1  # the quotes around the value
        new_content = content[:start] + _toml_string(value, quote=content[start]) + content[end:]
    else:
        table = _ngargparser_section(_parse_pyproject(content))
        if isinstance(table, dict) and key in table:
            # Set, in a form the line reader does not edit.
            previous = table[key] if isinstance(table[key], str) else None
            return previous, (content if previous == value else None)
        previous = None
        nl = "\r\n" if "\r\n" in content else "\n"  # a line added follows the file's own line endings
        stamp_line = f"{key} = {_toml_string(value)}{nl}"
        if header_end is not None:
            # The table exists without the key: add it there, never as a second table.
            head = content[:header_end] if content[:header_end].endswith("\n") else content[:header_end] + nl
            new_content = head + stamp_line + content[header_end:]
        else:
            if _toml_reader() is None and _NGARGPARSER_ELSEWHERE_RE.search(content):
                # No TOML parser to read it, but the table is there in another form (an
                # inline table, dotted keys): a second [tool.ngargparser] would make the
                # file invalid TOML, so the key has to be set by hand.
                return None, None
            ending = "" if content.endswith("\n") else nl
            new_content = content + ending + f"{nl}[tool.ngargparser]{nl}" + stamp_line
    try:
        _check_scaffold_stamp(new_content, value, path, key)
    except ValueError:
        if _parse_pyproject(content) is not None:
            return previous, None  # valid before the edit: the table is written in a form we don't extend
        raise
    return previous, new_content


def _set_by_hand_message(path, key, value, content=None):
    """Why sync does not write `key` = `value` into pyproject text `content` at `path`, and what to do."""
    found, current = _ngargparser_setting(content, key) if content is not None else (False, None)
    if found and not isinstance(current, str):
        # A value of another type (scaffold_version = 0.5, a number), not a form of the table.
        written = _ngargparser_line(content, key)
        shown = f" ({_without_comment(written)})" if written else ""
        return (
            f"{path} sets {key} under [tool.ngargparser] to a value that is not a quoted string{shown}, "
            f"which cli sync does not edit. Set {key} = {_toml_string(value)} in it by hand."
        )
    return (
        f"{path} writes [tool.ngargparser] in a form cli sync does not edit (an inline table, a dotted key "
        f"or a multi-line string). Set {key} = {_toml_string(value)} in it by hand."
    )


def _check_scaffold_stamp(content, version, path, key="scaffold_version"):
    """Raise ValueError unless `content` is valid pyproject text whose one
    [tool.ngargparser] table holds `key` = `version`."""
    toml_reader = _toml_reader()
    if toml_reader is not None:
        try:
            data = toml_reader.loads(content)
        except toml_reader.TOMLDecodeError as e:
            raise ValueError(
                f"{path} would not be valid TOML with the version stamp ({e}); it was left unchanged"
            ) from e
        stamped = data.get("tool", {}).get("ngargparser", {}).get(key)
    else:
        headers = 0
        for line in content.splitlines():
            header = _TOML_HEADER_RE.match(line)
            if header and header.group(1) == "tool.ngargparser":
                headers += 1
        span = _scaffold_stamp(content, key)[1]
        stamped = content[span[0] : span[1]] if headers == 1 and span else None
    if stamped != version:
        raise ValueError(
            f"could not stamp {key} = {version!r} in {path} safely; it was left unchanged. "
            "Check its [tool.ngargparser] table by hand."
        )


def write_scaffold_version(pyproject_path, version):
    """Write or update [tool.ngargparser] scaffold_version in pyproject.toml.
    Returns the previous version string, or None if no stamp existed before."""
    return write_ngargparser_key(pyproject_path, "scaffold_version", version)


_BOM = "\ufeff"  # a UTF-8 byte order mark, as text


def _read_pyproject(path="pyproject.toml", newline=None):
    """Return (text, bom): pyproject.toml's text without a leading UTF-8 byte order
    mark, and that mark ("" when it has none). uv reads a file that starts with one;
    a TOML parser and the line editor here do not, so the mark is set aside on
    reading and written back on writing. `newline` is open()'s: "" keeps each line's
    own ending ("\\r\\n" stays "\\r\\n"), for text that is written back."""
    with open(path, encoding="utf-8", newline=newline) as f:
        content = f.read()
    if content.startswith(_BOM):
        return content[len(_BOM) :], _BOM
    return content, ""


def write_ngargparser_key(pyproject_path, key, version):
    """Write or update the string `key` in pyproject.toml's [tool.ngargparser] table.
    Returns the previous value, or None if the key was not there before. Raises
    ValueError, leaving the file unchanged, when the result would not be valid or
    the table is written in a form this line editor does not change. A byte order
    mark at the start of the file stays there, and so do its line endings: a file
    written with "\\r\\n" keeps them, and a line added gets them too. The file is
    written through a temporary file renamed over it (see _replace_file): a write
    that fails (a full disk) raises the OSError and leaves the file as it was."""
    if not os.path.exists(pyproject_path):
        return None

    content, bom = _read_pyproject(pyproject_path, newline="")

    previous, new_content = _plan_ngargparser_key(content, key, version, pyproject_path)
    if new_content is None:
        raise ValueError(f"{_set_by_hand_message(pyproject_path, key, version)} It was left unchanged.")
    if new_content != content:
        _replace_file(pyproject_path, (bom + new_content).encode("utf-8"), writable_only=True)
    return previous


def _copy_ci_template(project_name):
    """Give a new tool a starter .gitlab-ci.yml from templates/gitlab-ci.yml, when this
    ngargparser ships one. User-owned from then on: cli sync never creates or changes it."""
    template = f"{TEMPLATE_DIR}/gitlab-ci.yml"
    if os.path.isfile(template):
        shutil.copy(template, f"{project_name}/.gitlab-ci.yml")
        replace_text_in_place(f"{project_name}/.gitlab-ci.yml", "{TOOL_NAME}", project_name)


# tests/test_parser.py in a new tool: the parser test README.md's "Test your parser" shows,
# so `uv sync && uv run pytest` passes from the start. The tool's own file from then on.
_STARTER_TEST = '''"""Tests for the {PARSER} options. Run them with: uv run pytest

pytest puts src/ on the import path ([tool.pytest.ini_options] in pyproject.toml),
so a test imports the parser module directly. Importing it reads .env: in a fresh
clone, which has none, tests/conftest.py runs ./configure first.
"""

import pytest
from {PARSER} import {PARSER}


def test_predict_reads_json(tmp_path):
    peptides = tmp_path / "input.json"
    peptides.write_text({INPUT})
    args = {PARSER}().parse_args(["predict", "-j", str(peptides)])
    assert args.input_json == peptides
    assert args.output_prefix is None


def test_predict_rejects_a_missing_file():
    with pytest.raises(SystemExit) as e:
        {PARSER}().parse_args(["predict", "-j", "missing.json"])
    assert e.value.code == 2
'''

# tests/conftest.py in a new tool: a fresh clone has no .env (.gitignore keeps it out of git,
# since ./configure writes this machine's paths into it), and the tool's modules stop at
# import without one, so `uv run pytest` there runs ./configure first. The tool's own file
# from then on; `make build` leaves tests/ out of the tarball.
_STARTER_CONFTEST = '''"""Test set-up for this tool. pytest reads this file before it imports any test.

The tool's modules read .env when they are imported, and a fresh clone has none:
./configure writes it with this machine's paths, so it is not in git. When .env is
missing, this runs ./configure once, with the Python that runs the tests. When
./configure fails (a dependency path that is not set, say), the tests stop with its
message, which says what to do.
"""

import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent


def pytest_configure(config):
    if (TOOL / ".env").is_file():
        return
    configured = subprocess.run(
        [sys.executable, str(TOOL / "src" / "core" / "configure.py")],
        cwd=TOOL,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if configured.returncode != 0:
        pytest.exit(
            "This tool has no .env, and ./configure, which writes it, failed:\\n" + configured.stdout.rstrip(),
            returncode=1,
        )
'''

# .gitattributes in a new tool: the files bash runs keep LF line endings in every checkout
# (a Windows one with core.autocrlf too), since bash stops at a CRLF line, and so do the
# framework files cli sync replaces (src/core/*.py, Makefile), so a checkout holds the bytes
# sync compares with its own copy. User-owned: cli sync never creates or changes it.
_STARTER_GITATTRIBUTES = """# The files bash runs keep LF line endings in every checkout: bash cannot run a CRLF line.
*.sh text eol=lf
configure text eol=lf
build.conf text eol=lf
# The framework files cli sync replaces keep LF too, so a checkout holds the framework's own copy.
src/core/*.py text eol=lf
Makefile text eol=lf
"""


def _write_starter_files(project_name, parser_name):
    """Give a new tool tests/test_parser.py, which tests `parser_name`, tests/conftest.py,
    which configures a fresh clone before the tests, and a .gitattributes, each only when
    missing. All are the tool's own files: cli sync never changes them."""
    import json

    test_file = os.path.join(project_name, "tests", "test_parser.py")
    if not os.path.lexists(test_file):
        os.makedirs(os.path.dirname(test_file), exist_ok=True)
        # The input the README's Quickstart writes: {"peptide": [...]}.
        sample = {"peptide": ["ADMGHLKY"], "amino_acid": "L"} if parser_name == "AACounterArgumentParser" else None
        sample_text = json.dumps(sample) if sample else _BLANK_SAMPLE_INPUT
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(_STARTER_TEST.replace("{PARSER}", parser_name).replace("{INPUT}", repr(sample_text)))
    conftest = os.path.join(project_name, "tests", "conftest.py")
    if not os.path.lexists(conftest):
        os.makedirs(os.path.dirname(conftest), exist_ok=True)
        with open(conftest, "w", encoding="utf-8") as f:
            f.write(_STARTER_CONFTEST)
    attributes = os.path.join(project_name, ".gitattributes")
    if not os.path.lexists(attributes):
        with open(attributes, "w", encoding="utf-8") as f:
            f.write(_STARTER_GITATTRIBUTES)


def create_example_structure():
    project_name = "aa-counter"

    # Fail fast if the target already exists, before writing anything.
    try:
        os.makedirs(project_name)
    except FileExistsError:
        _print_error(f"\033[91m✗\033[0m '{project_name}' already exists; aborting.")
        return 1

    try:
        # Create directory structure
        os.makedirs(os.path.join(project_name, "src"))
        os.makedirs(os.path.join(project_name, "src", "core"))
        os.makedirs(os.path.join(project_name, "scripts"))
        os.makedirs(os.path.join(project_name, "scripts", "core"))

        # Create necessary files
        parser_file = "AACounterArgumentParser.py"
        # README.md is the developer walkthrough; README, which ships in the tarball, is the
        # sample app's own end-user README (the blank tool's template, filled in).
        enduser = f"{EXAMPLE_DIR}/README.enduser"
        update_and_place_readme(
            f"{EXAMPLE_DIR}/README",
            project_name,
            is_example=True,
            plain_file_path=enduser if os.path.isfile(enduser) else f"{TEMPLATE_DIR}/README",
        )
        shutil.copy(f"{EXAMPLE_DIR}/example.json", f"{project_name}/src/example.json")
        shutil.copy(f"{EXAMPLE_DIR}/example.tsv", f"{project_name}/src/example.tsv")
        shutil.copy(f"{EXAMPLE_DIR}/run_aa_counter.py", f"{project_name}/src/run_aa_counter.py")
        shutil.copy(f"{EXAMPLE_DIR}/{parser_file}", f"{project_name}/src/{parser_file}")
        shutil.copy(f"{EXAMPLE_DIR}/preprocess.py", f"{project_name}/src/preprocess.py")
        shutil.copy(f"{EXAMPLE_DIR}/postprocess.py", f"{project_name}/src/postprocess.py")
        shutil.copy(f"{TEMPLATE_DIR}/configure.py", f"{project_name}/src/core/configure.py")
        # Make configure.py executable
        os.chmod(f"{project_name}/src/core/configure.py", 0o755)

        # Copy core files to protected core/ directory
        shutil.copy(f"{NGPARSER_DIR}/NGArgumentParser.py", f"{project_name}/src/core/NGArgumentParser.py")
        shutil.copy(f"{NGPARSER_DIR}/core_validators.py", f"{project_name}/src/core/core_validators.py")
        shutil.copy(f"{TEMPLATE_DIR}/set_pythonpath.py", f"{project_name}/src/core/set_pythonpath.py")
        shutil.copy(f"{NGPARSER_DIR}/result_writer.py", f"{project_name}/src/core/result_writer.py")
        shutil.copy(f"{NGPARSER_DIR}/app_env.py", f"{project_name}/src/core/app_env.py")
        shutil.copy(f"{NGPARSER_DIR}/dep_names.py", f"{project_name}/src/core/dep_names.py")

        # Create __init__.py for core package
        with open(f"{project_name}/src/core/__init__.py", "w") as f:
            f.write("")

        # Copy user-modifiable files to src/: the sample app's validators.py adds
        # its own check for -a next to the blank tool's re-exports.
        shutil.copy(f"{EXAMPLE_DIR}/validators.py", f"{project_name}/src/validators.py")

        # Framework-owned: scripts/core/build.sh (cli sync overwrites this)
        shutil.copy(f"{TEMPLATE_DIR}/build.sh", f"{project_name}/scripts/core/build.sh")
        os.chmod(f"{project_name}/scripts/core/build.sh", 0o755)
        # Framework-owned: root Makefile (cli sync overwrites this)
        shutil.copy(f"{TEMPLATE_DIR}/Makefile", f"{project_name}/Makefile")
        # User-owned: scripts/core/build.conf, .distignore (root)
        shutil.copy(f"{TEMPLATE_DIR}/build.conf", f"{project_name}/scripts/core/build.conf")
        shutil.copy(f"{TEMPLATE_DIR}/distignore", f"{project_name}/.distignore")

        # User-owned: deploy/install.sh — invoked by nxg-tools-deployments at deploy time.
        os.makedirs(f"{project_name}/deploy", exist_ok=True)
        shutil.copy(f"{TEMPLATE_DIR}/deploy/install.sh", f"{project_name}/deploy/install.sh")
        replace_text_in_place(f"{project_name}/deploy/install.sh", "{TOOL_NAME}", project_name)
        _copy_ci_template(project_name)

        # Copy the configure launcher (forwards args to src/core/configure.py)
        shutil.copy(f"{TEMPLATE_DIR}/configure", f"{project_name}/configure")
        os.chmod(f"{project_name}/configure", 0o755)

        _write_starter_files(project_name, "AACounterArgumentParser")

        # Render pyproject.toml from template (and lock with uv if available)
        write_pyproject_toml(project_name, project_name)
        # The sample app is finished, so its description is too (a blank tool's says TODO).
        replace_text_in_place(
            f"{project_name}/pyproject.toml",
            f"TODO: short description for {project_name}",
            "Counts an amino acid in peptides: the ngargparser sample app",
        )

        print(f"Created the tool '{project_name}'.")
        return 0
    except Exception as e:
        _print_error(f"\033[91m✗ Error: {e}\033[0m")
        shutil.rmtree(project_name, ignore_errors=True)
        _print_error(f"  └ Cleaned up the partial tool '{project_name}'.")
        return 1


def create_project_structure(project_name):
    # Fail fast if the target already exists, before writing anything.
    try:
        os.makedirs(project_name)
    except FileExistsError:
        _print_error(f"\033[91m✗\033[0m '{project_name}' already exists; aborting.")
        return 1

    try:
        # Create directory structure
        os.makedirs(os.path.join(project_name, "src"))
        os.makedirs(os.path.join(project_name, "src", "core"))
        os.makedirs(os.path.join(project_name, "scripts"))
        os.makedirs(os.path.join(project_name, "scripts", "core"))

        # Create necessary files
        exec_file = f"run_{format_project_name(project_name)}.py"
        parser_file = f"{format_project_name(project_name, capitalize=True)}ArgumentParser.py"
        parser_name = f"{format_project_name(project_name, capitalize=True)}ArgumentParser"
        update_and_place_readme(f"{TEMPLATE_DIR}/README.md", project_name, plain_file_path=f"{TEMPLATE_DIR}/README")
        shutil.copy(f"{TEMPLATE_DIR}/run_app.py", f"{project_name}/src/{exec_file}")
        shutil.copy(f"{NGPARSER_DIR}/NGChildArgumentParser.py", f"{project_name}/src/{parser_file}")
        shutil.copy(f"{TEMPLATE_DIR}/preprocess.py", f"{project_name}/src/preprocess.py")
        shutil.copy(f"{TEMPLATE_DIR}/postprocess.py", f"{project_name}/src/postprocess.py")
        shutil.copy(f"{TEMPLATE_DIR}/configure.py", f"{project_name}/src/core/configure.py")
        # Make configure.py executable
        os.chmod(f"{project_name}/src/core/configure.py", 0o755)

        # Copy core files to protected core/ directory
        shutil.copy(f"{NGPARSER_DIR}/NGArgumentParser.py", f"{project_name}/src/core/NGArgumentParser.py")
        shutil.copy(f"{NGPARSER_DIR}/core_validators.py", f"{project_name}/src/core/core_validators.py")
        shutil.copy(f"{TEMPLATE_DIR}/set_pythonpath.py", f"{project_name}/src/core/set_pythonpath.py")
        shutil.copy(f"{NGPARSER_DIR}/result_writer.py", f"{project_name}/src/core/result_writer.py")
        shutil.copy(f"{NGPARSER_DIR}/app_env.py", f"{project_name}/src/core/app_env.py")
        shutil.copy(f"{NGPARSER_DIR}/dep_names.py", f"{project_name}/src/core/dep_names.py")

        # Create __init__.py for core package
        with open(f"{project_name}/src/core/__init__.py", "w") as f:
            f.write("")

        # Copy user-modifiable files to src/
        shutil.copy(f"{NGPARSER_DIR}/validators.py", f"{project_name}/src/validators.py")

        # Framework-owned: scripts/core/build.sh + root Makefile (cli sync overwrites these)
        shutil.copy(f"{TEMPLATE_DIR}/build.sh", f"{project_name}/scripts/core/build.sh")
        os.chmod(f"{project_name}/scripts/core/build.sh", 0o755)
        shutil.copy(f"{TEMPLATE_DIR}/Makefile", f"{project_name}/Makefile")
        # User-owned: scripts/core/build.conf, get_libs.sh, .distignore (root) (sync leaves alone).
        # scripts/hooks.sh is optional and not created; templates/hooks.sh is the starting point.
        shutil.copy(f"{TEMPLATE_DIR}/build.conf", f"{project_name}/scripts/core/build.conf")
        shutil.copy(f"{TEMPLATE_DIR}/distignore", f"{project_name}/.distignore")
        shutil.copy(f"{TEMPLATE_DIR}/get_libs.sh", f"{project_name}/scripts/get_libs.sh")
        os.chmod(f"{project_name}/scripts/get_libs.sh", 0o755)

        # User-owned: deploy/install.sh — invoked by nxg-tools-deployments at deploy time.
        os.makedirs(f"{project_name}/deploy", exist_ok=True)
        shutil.copy(f"{TEMPLATE_DIR}/deploy/install.sh", f"{project_name}/deploy/install.sh")
        replace_text_in_place(f"{project_name}/deploy/install.sh", "{TOOL_NAME}", project_name)
        _copy_ci_template(project_name)

        # Try to copy license file, but don't fail if it's not available
        license_source = f"{NGPARSER_DIR}/license-LJI.txt"
        if os.path.exists(license_source):
            shutil.copy(license_source, f"{project_name}/license-LJI.txt")
        else:
            print(f"Warning: License file not found at {license_source}")
            print("Skipping license file copy.")

        # Add default content to all the files
        replace_text_in_place(f"{project_name}/src/{exec_file}", "CHILDPARSER", parser_name)
        replace_text_in_place(f"{project_name}/src/{parser_file}", "ChildArgumentParser", parser_name)
        replace_text_in_place(f"{project_name}/src/core/configure.py", "PROJECT_NAME", project_name)

        # Copy the configure launcher (forwards args to src/core/configure.py)
        shutil.copy(f"{TEMPLATE_DIR}/configure", f"{project_name}/configure")
        os.chmod(f"{project_name}/configure", 0o755)

        _write_starter_files(project_name, parser_name)
        # An input to run predict on from the start (a user-owned file, as the sample app's is).
        with open(f"{project_name}/src/example.json", "w", encoding="utf-8") as f:
            f.write(_BLANK_SAMPLE_INPUT + "\n")

        # Render pyproject.toml from template (and lock with uv if available)
        write_pyproject_toml(project_name, project_name)

        print(f"Created the tool '{project_name}'.")
        return 0
    except Exception as e:
        _print_error(f"\033[91m✗ Error: {e}\033[0m")
        shutil.rmtree(project_name, ignore_errors=True)
        _print_error(f"  └ Cleaned up the partial tool '{project_name}'.")
        return 1


# A blank tool's example input, src/example.json: what its tests/test_parser.py gives predict -j,
# so the command Next steps prints reads a file that is there.
_BLANK_SAMPLE_INPUT = '{"peptide": ["ADMGHLKY"]}'


# The input file the end-user README (templates/README) writes in its predict example, and
# the one aa-counter reads, which the sample app's README shows instead.
_README_SAMPLE_INPUT = '{"<parameter>": "<value>"}'
_EXAMPLE_INPUT = '{"peptide": ["ADMGHLKY"], "amino_acid": "L"}'


def update_and_place_readme(file_path, app_name, is_example=False, plain_file_path=None):
    # Copy over and generate README files (both README.md and README).
    # README.md comes from `file_path` (the developer note, or the example's
    # walkthrough) and gets the version badge. README comes from `plain_file_path`
    # when given (the end-user README, which ships in the tarball), else from
    # `file_path` too.
    readme_md_path = f"{app_name}/README.md"
    readme_plain_path = f"{app_name}/README"
    # Ensure we have a starting file to read from
    shutil.copy(file_path, readme_md_path)

    def replace_variables(content):
        content = content.replace("{TOOL_NAME}", app_name)
        content = content.replace("{TOOL_EXEC_NAME}", format_project_name(app_name))
        if is_example:
            return content.replace("{TOOL_NAME_CAP}", "AACounter")
        return content.replace("{TOOL_NAME_CAP}", format_project_name(app_name, capitalize=True))

    with open(readme_md_path, "r") as file:
        content = file.read()

    # Replace variables in README
    base_updated_content = replace_variables(content)

    # Insert ngargparser version badge just below the title if possible: the badge
    # cli sync writes, so the first sync of a new tool has nothing to change in it.
    try:
        badge_md = _readme_badge(__version__)
        lines = base_updated_content.splitlines(True)  # keep line endings
        if len(lines) >= 2:
            # Common template starts with title and an underline of dashes
            lines.insert(2, f"\n{badge_md}\n\n")
            content_with_badge = "".join(lines)
        else:
            content_with_badge = f"{badge_md}\n\n" + base_updated_content
    except Exception:
        # If anything goes wrong, proceed without badge insertion
        content_with_badge = base_updated_content

    plain_content = base_updated_content
    if plain_file_path:
        with open(plain_file_path, "r") as file:
            title, newline, rest = file.read().partition("\n")
        # The end-user README's title shows the name as typed (tcell_mhcii, netMHCpan-el),
        # not the class-name form (TcellMhcii); the rest of the file is filled in as before.
        plain_content = replace_variables(title.replace("{TOOL_NAME_CAP}", app_name) + newline + rest)
        if is_example:
            # The sample app's predict runs as its README shows it: with aa-counter's own input.
            plain_content = plain_content.replace(_README_SAMPLE_INPUT, _EXAMPLE_INPUT)

    # Write the updated content to both README.md and README
    with open(readme_md_path, "w") as f_md:
        f_md.write(content_with_badge)  # README.md includes badge
    with open(readme_plain_path, "w") as f_plain:
        f_plain.write(plain_content)  # README (no extension) has no badge


def replace_text_in_place(file_path, old_text, new_text):
    """
    Replaces all occurrences of old_text with new_text in the specified file.

    The file is rewritten through a temporary file renamed over it (see _replace_file),
    so it is never left half substituted: a read or a write that fails (a full disk, a
    read-only folder) raises the OSError and leaves the file as it was. cli generate
    then stops with the error and removes the partial tool.

    :param file_path: Path to the file where the replacement should occur.
    :param old_text: Text to be replaced.
    :param new_text: Text to replace with.
    """
    with open(file_path, encoding="utf-8") as file:
        content = file.read()
    _replace_file(file_path, content.replace(old_text, new_text))


def _readme_badge(version: str, color: str = "green") -> str:
    """The ngargparser version badge (Markdown) that cli generate and cli sync write into README.md."""
    return (
        f"[![ngargparser](https://img.shields.io/badge/ngargparser-{version}-{color}.svg)]"
        "(https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser)"
    )


def _readme_with_badge(content: str, version: str, color: str = "green") -> str:
    """README text `content` with the ngargparser badge set to `version`: the first
    badge replaced, or a new one inserted below the title (else at the top). A
    line added gets the text's own line ending, and a leading byte order mark stays first."""
    bom = _BOM if content.startswith(_BOM) else ""
    content = content[len(bom) :]
    new_badge = _readme_badge(version, color)
    pattern = r"\[!\[ngargparser\]\(https://img\.shields\.io/badge/ngargparser-[^-/\s)]+-[^)\s]+\.svg\)\]\([^)]+\)"
    if re.search(pattern, content):
        return bom + re.sub(pattern, lambda _match: new_badge, content, count=1)
    nl = "\r\n" if "\r\n" in content else "\n"
    # If no existing badge, insert below title when possible, else prepend
    lines = content.splitlines(True)
    if len(lines) >= 2:
        lines.insert(2, f"{nl}{new_badge}{nl}{nl}")
        return bom + "".join(lines)
    return bom + f"{new_badge}{nl}{nl}" + content


def upsert_readme_badge(readme_path: str, version: str, color: str = "green", dry_run: bool = False) -> bool:
    """
    Update or insert the ngargparser version badge in the given README file.
    Returns True if the file was modified (with `dry_run`, would be; nothing is
    written), False otherwise. The file keeps its line endings and a byte order
    mark. A file that cannot be read (or not as UTF-8) is skipped with a warning.
    The new text is written through a temporary file renamed over it (see
    _replace_file), so a write that fails (a full disk, a file that is not
    writable) raises the OSError and leaves the file as it was.
    """
    try:
        if not os.path.exists(readme_path):
            return False
        with open(readme_path, "r", encoding="utf-8", newline="") as f:
            content = f.read()
        updated = _readme_with_badge(content, version, color)
    except Exception as e:
        print(f"\033[93m⚠\033[0m Skipped updating README badge for {readme_path}: {e}")
        return False
    if updated == content:
        return False
    if not dry_run:
        _replace_file(readme_path, updated.encode("utf-8"), writable_only=True)
    return True


def normalize_content_ending(content):
    """
    Normalize the content to ensure it ends with exactly one newline.

    Args:
        content (str): The content to normalize

    Returns:
        str: Content with exactly one trailing newline
    """
    return content.strip() + "\n"


def format_display_name(name):
    """
    Format the dependency name for display in comments.
    Handles Roman numerals (i, ii) specially.

    Args:
        name (str): The original dependency name

    Returns:
        str: Formatted display name
    """
    # Split by common separators and capitalize each word
    words = re.split(r"[_\s-]+", name)
    formatted_words = []

    for word in words:
        if word:  # Skip empty strings
            # Check if word is a Roman numeral (i, ii, iii, iv, v, etc.)
            if word.lower() in ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]:
                formatted_words.append(word.upper())
            else:
                formatted_words.append(word.capitalize())

    return " ".join(formatted_words)


def _stub_comment_patterns():
    """The comment lines generate_dependency_section writes, as patterns that match
    them for any dependency title (or none), so `cli deps remove` can tell them apart
    from a comment the developer wrote. Includes the "Path to the <title> (required)"
    line an earlier cli deps wrote."""
    marker = "Zzstubtitlezz"  # format_display_name keeps it as it is
    comments = [line for line in generate_dependency_section(marker).splitlines() if line.startswith("#")]
    comments.append(f"# Path to the {marker} (required)")
    return [re.compile(re.escape(line).replace(re.escape(f"{marker} "), "(?:.+ )?")) for line in comments]


def generate_dependency_section(name):
    """
    Generate the configuration section for a dependency.

    Args:
        name (str): The dependency name

    Returns:
        str: The configuration section
    """
    display_name = format_display_name(name)
    var_name = normalize_name(name)

    section = f"""\n
''' [ {display_name} ] '''
# Path to the {display_name} tool (required)
{var_name}_path=None

# Path to the {display_name} virtual environment (optional)
{var_name}_venv=None

# Name of the environment module to be activated (optional).
# Most users can keep this as None
{var_name}_module=None

# If using a different Python environment or binary for this tool compared to others,
# you may need to update LD_LIBRARY_PATH to ensure the correct libraries are used.
# If you're using the same environment across all tools, this can usually be left empty.
# (optional)
{var_name}_lib_path=None"""
    return section


def get_dependencies():
    """
    Get the list of dependencies from user input.

    Returns:
        list: List of dependency names
    """
    dependencies = []

    print("\nEnter the names of all dependencies that the current tool depends on.")
    print("Press Enter after each dependency name.")
    print("Press Enter on an empty line when you're done.\n")

    while True:
        dep = input("Dependency name (or press Enter to finish): ").strip()
        if not dep:
            break
        dependencies.extend(_split_dep_names([dep]))  # "pepx, netmhcpan" is two names

    return dependencies


def _split_dep_names(names):
    """`names` as `cli deps add` and the interactive menu take them: each one split at
    commas, with the spaces around each part dropped, so `cli deps add pepx,netmhcpan`
    and the menu answer `pepx, netmhcpan` are two dependencies, as `cli deps add pepx
    netmhcpan` is. A quoted name with a space in it ("T Cell") stays one name. A name
    with nothing but commas and spaces stays as it is, so it is refused ("has no letters
    or digits")."""
    return [part for name in names for part in ([p.strip() for p in name.split(",") if p.strip()] or [name])]


def _once_each(names):
    """`names` with each dependency once, in the order first given, saying which were
    entered more than once. Names that become the same variable name (bar and Bar)
    are one dependency, as for `cli deps add bar Bar`."""
    spellings: dict[str, list[str]] = {}
    count: dict[str, int] = {}
    for name in names:
        var = normalize_name(name)
        spellings.setdefault(var, [])
        if name not in spellings[var]:
            spellings[var].append(name)
        count[var] = count.get(var, 0) + 1
    for var, times in count.items():
        if times > 1:
            how_often = "twice" if times == 2 else f"{times} times"
            forms = f" (as {', '.join(spellings[var])})" if len(spellings[var]) > 1 else ""
            print(f"\033[93m⚠\033[0m  {spellings[var][0]} was entered {how_often}{forms}; added once.")
    return [spelled[0] for spelled in spellings.values()]


def create_paths_file(project_name_or_path):
    """
    Create the paths.py file based on user input for dependencies.
    Integrated configuration functionality from configure.py.
    """
    print("=== Setting up paths.py configuration ===")

    # Check if this is a project name (for startapp) or a direct file path (for setup-paths)
    if project_name_or_path.endswith(".py"):
        # Direct file path
        paths_file_path = project_name_or_path
    else:
        # Project name - create in project/src/ structure
        if project_name_or_path == "example":
            project_name_or_path = "aa-counter"

        paths_file_path = f"{project_name_or_path}/paths.py"

    try:
        # Check if paths.py already exists
        overwrite = Path(paths_file_path).exists()
        if overwrite:
            print("\n\033[93m⚠\033[0m  'paths.py' already exists!")
            while True:
                response = input("Do you want to overwrite it? (y/n): ").lower().strip()
                if response in ["y", "yes"]:
                    break
                elif response in ["n", "no"]:
                    print("Skipping paths.py creation. Existing file was not modified.")
                    return 0
                else:
                    print("Please enter 'y' for yes or 'n' for no.")

        # Get dependencies from user, refusing names ./configure couldn't see as a tool
        dependencies = []
        refused = False
        for dep in get_dependencies():
            reason = invalid_dep_name(dep)
            if reason:
                _print_error(f"\033[91m✗\033[0m Not added: {reason}")
                refused = True
            else:
                dependencies.append(dep)
        dependencies = _once_each(dependencies)

        if not dependencies:
            print("No dependencies specified. Creating empty paths.py file.")
            # Create empty file
            with _PathsLock(paths_file_path):
                if not overwrite and Path(paths_file_path).exists():
                    return _created_meanwhile(paths_file_path)
                _write_paths(paths_file_path, "")
            print(f"\n\033[92m✓\033[0m Created empty '\033[92m{paths_file_path}\033[0m'.")
            return 1 if refused else 0
        else:
            print(f"Found {len(dependencies)} dependencies:")
            for i, dep in enumerate(dependencies, 1):
                print(f"  {i}. {dep}")

        # Create the content
        content = ""

        # Add dependency sections if any
        if dependencies:
            for dep in dependencies:
                content += generate_dependency_section(dep)

        # Normalize content ending to ensure exactly one newline
        content = normalize_content_ending(content)

        # Write to paths.py, under the paths.py lock: one another cli deps or ./configure made while
        # the questions were answered is not overwritten.
        with _PathsLock(paths_file_path):
            if not overwrite and Path(paths_file_path).exists():
                return _created_meanwhile(paths_file_path)
            _write_paths(paths_file_path, content)

        print(
            f"\n\033[92m✓\033[0m Created '\033[92m{paths_file_path}\033[0m' with \033[92m{len(dependencies)}\033[0m dependencies."
        )
        print(f"You can now edit '{paths_file_path}' to set the actual paths for your dependencies.")
        return 1 if refused else 0

    except (EOFError, KeyboardInterrupt):
        raise  # Ctrl-D or Ctrl-C at a prompt: the menu says "Cancelled." (see _deps_menu)
    except (_PathsLockBusy, _LockUnusable) as busy:
        _print_error(f"\n\033[91m✗\033[0m {busy}")
        return 1
    except Exception as e:
        _print_error(f"\n\033[91m✗\033[0m Error creating paths.py: \033[91m{e}\033[0m")
        return 1


def _created_meanwhile(file_path):
    """Say that `file_path` appeared while the menu asked its questions, so it was not
    overwritten; return 1."""
    _print_error(
        f"\n\033[91m✗\033[0m '{file_path}' was created while you answered (by another cli deps or ./configure), "
        "so it was not overwritten; nothing was changed. Run cli deps add <name> ... to add to it."
    )
    return 1


# The rule of which names a dependency can have (DEP_NAME_FIELD_SUFFIXES, DEP_FIELDS, the
# variables a name must not spell, invalid_dep_name) is ngargparser/dep_names.py, which
# ./configure reads too, as src/core/dep_names.py.
_DEP_TITLE_RE = re.compile(r"\s*\[\s*([^\]]+?)\s*\]\s*")


class PathsFileError(Exception):
    """paths.py can't be read or doesn't parse; nothing may be written to it."""


class _PathsNotUtf8(PathsFileError):
    """paths.py is not UTF-8 text (a Latin-1 file, say). Its message is a whole line, with the
    fix and "nothing was changed", which the commands print as it is (see _paths_error_line)."""


def _paths_error_line(e):
    """The line cli deps add and remove print for PathsFileError `e`, after the ✗: its
    message as one sentence, then what to do."""
    if isinstance(e, _PathsNotUtf8):
        return str(e)
    end = "" if str(e).endswith((".", "?", "!")) else "."
    return f"{e}{end} Fix it, then run cli deps again; nothing was changed."


class _AddResult(TypedDict):
    """What _add_deps did, by name."""

    added: list[str]
    skipped: list[str]
    rejected: list[str]
    error: Optional[str]


class _RemoveResult(TypedDict):
    """What _remove_deps did, by name."""

    removed: list[str]
    not_found: list[str]
    refused: list[str]
    error: Optional[str]


def _paths_dependencies(content):
    """Return the dependencies declared in paths.py `content`, in file order.

    {name: {"display", "path", "start", "end", "own_lines", "shared_lines"}}: `display` is the
    title right above the block, or the name when there is none; `path` is the
    source text of `<name>_path`; `start`/`end` are the block's 1-based first and
    last lines (from the title, or the first setting, through the last setting);
    `own_lines` are the 1-based lines of its title and of its settings (the
    DEP_FIELDS and `<name>_entry`, wherever they are), the statements a stub block
    holds or ./configure reads for this dependency. A line that holds another statement
    too (`pepx_venv=None; netmhcpan_entry="x"`) is not one of them: it is in
    `shared_lines`, and cli deps remove keeps it.
    """
    import ast

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        raise PathsFileError(f"paths.py doesn't parse (line {e.lineno}): {e.msg}") from e

    assigns = {}  # variable -> (index in the module body, node)
    on_line: dict[int, set[int]] = {}  # line -> the statements on it (several when ';' joins them)
    for i, node in enumerate(tree.body):
        var = _top_level_variable(node)
        if var:
            assigns[var] = (i, node)
        for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            on_line.setdefault(line, set()).add(i)

    deps = {}
    for var, (_, path_node) in assigns.items():
        if not var.endswith("_path"):
            continue
        name = var[: -len("_path")]
        if not name or name.endswith(DEP_NAME_FIELD_SUFFIXES):
            continue
        fields = [assigns[name + suffix] for suffix in DEP_FIELDS if name + suffix in assigns]
        first = min(i for i, _ in fields)
        start = min(node.lineno for _, node in fields)
        end = max(node.end_lineno or node.lineno for _, node in fields)
        # <name>_entry and <name>_python are settings of the dependency too (./configure
        # reads both), so they go with its block.
        settings = fields + [assigns[name + extra] for extra in ("_entry", "_python") if name + extra in assigns]
        mine = {i for i, _ in settings}
        lines = {line for _, node in settings for line in range(node.lineno, (node.end_lineno or node.lineno) + 1)}
        display = name
        if first > 0:
            above = tree.body[first - 1]
            if (
                isinstance(above, ast.Expr)
                and isinstance(above.value, ast.Constant)
                and isinstance(above.value.value, str)
            ):
                match = _DEP_TITLE_RE.fullmatch(above.value.value)
                if match:
                    display = match.group(1)
                    start = above.lineno
                    mine.add(first - 1)
                    lines.update(range(above.lineno, (above.end_lineno or above.lineno) + 1))
        own_lines = {line for line in lines if on_line[line] <= mine}
        deps[name] = {
            "display": display,
            "path": ast.get_source_segment(content, path_node.value) or "None",
            "start": start,
            "end": end,
            "own_lines": own_lines,
            "shared_lines": lines - own_lines,
        }
    return deps


def _top_level_variable(node):
    """The variable statement `node` of paths.py sets, when it is one cli deps reads: a plain
    `name = value`, or an annotated `name: str = value` (./configure runs paths.py, so it
    sees both). None for anything else (a chained or unpacked assignment, a bare annotation)."""
    import ast

    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
        return node.target.id
    return None


def _variables_set_elsewhere(content):
    """{variable: line} for each variable paths.py `content` sets in a form cli deps does not
    read (see _top_level_variable): inside an if, a for or a with, a chained `a = b = ...`,
    an unpacked `a, b = ...`, a `+=`. ./configure runs the file, so it still sees them."""
    import ast

    tree = ast.parse(content)
    found: dict[str, int] = {}
    # The module's own statements, and the blocks inside them; not a function's or a class's
    # body, whose names are not the module's variables.
    pending: list[ast.AST] = [node for node in tree.body if not _top_level_variable(node)]
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.id not in found or node.lineno < found[node.id]:
                found[node.id] = node.lineno
        pending.extend(ast.iter_child_nodes(node))
    return found


def _set_elsewhere_reason(var, elsewhere, file_path):
    """Why the stub block of dependency `var` can't be added to `file_path`: one of its
    settings is set already, in a form cli deps does not read (`elsewhere`, from
    _variables_set_elsewhere), and the stub's line would replace that value. None when
    none is."""
    for suffix in DEP_FIELDS:
        field = f"{var}{suffix}"
        if field in elsewhere:
            return (
                f"'{file_path}' already sets {field} on line {elsewhere[field]}, in a form cli deps does not read "
                f"(inside an if, or a chained or unpacked assignment), and the {field}=None that cli deps adds "
                f"below it would replace that value. Write it as a plain {field} = ... line at the top level of "
                f"'{file_path}' (then cli deps lists it), or edit the file by hand."
            )
    return None


def _read_paths(file_path):
    """Return (content, dependencies) of an existing paths.py; raise PathsFileError.

    A UTF-8 byte order mark at the start is left out of `content`, as Python skips it
    when ./configure runs the file; _write_paths writes it back."""
    try:
        with open(file_path, encoding="utf-8-sig") as f:
            content = f.read()
    except OSError as e:
        raise PathsFileError(f"can't read '{file_path}': {e}") from e
    except UnicodeDecodeError as e:
        raise _PathsNotUtf8(
            f"{os.path.basename(file_path)} is not UTF-8 text (byte 0x{e.object[e.start]:02x} at offset {e.start}): "
            "save it as UTF-8, then run cli deps again; nothing was changed."
        ) from e
    return content, _paths_dependencies(content)


def _write_paths(file_path, content):
    """Write `content` to paths.py `file_path` through a temporary file renamed over it
    (see _replace_file), so a write that fails leaves the file as it was, never cut
    short. The file keeps its mode, a byte order mark at its start, and its line
    endings: `content`, read with Python's universal newlines, gets each line's
    own ending back ("\\r\\n" stays "\\r\\n", "\\n" stays "\\n", in a file that mixes
    them too), and a line cli deps added gets the ending most of the file's lines
    have (see _with_line_endings). A paths.py that is not writable stays as it is
    (PermissionError)."""
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
    except OSError:
        _replace_file(file_path, content, writable_only=True)  # a new file
        return
    bom = _BOM if raw.startswith(_BOM.encode("utf-8")) else ""
    old = raw.decode("utf-8", errors="replace")[len(bom) :]
    text = bom + _with_line_endings(old, content.replace("\r\n", "\n"))
    _replace_file(file_path, text.encode("utf-8"), writable_only=True)


def _with_line_endings(old, content):
    """`content` (lines that end in "\\n") with the line endings of text `old`, the file
    it was read from: a line that is in `old` too keeps the ending it had there, and
    another one gets the ending most of `old`'s lines have (the first one's, when two
    are as common; "\\n" when `old` has none)."""
    import difflib

    parts = re.split(r"(\r\n|\r|\n)", old)
    old_lines, old_endings = parts[0::2], parts[1::2]
    counts: dict[str, int] = {}
    for ending in old_endings:
        counts[ending] = counts.get(ending, 0) + 1
    usual = max(counts, key=lambda ending: counts[ending]) if counts else "\n"  # the first of the most common
    new_lines = content.split("\n")
    endings = ["\n"] * (len(new_lines) - 1) + [""]  # the last part has no ending
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    matched = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            matched[block.b + k] = block.a + k
    for i in range(len(new_lines) - 1):
        j = matched.get(i)
        endings[i] = old_endings[j] if j is not None and j < len(old_endings) else usual
    return "".join(line + ending for line, ending in zip(new_lines, endings))


def parse_existing_paths_file(file_path):
    """
    Parse an existing paths.py file to extract current dependencies.

    Uses ./configure's rule (see _paths_dependencies), so both see the same tools.

    Args:
        file_path (str): Path to the existing paths file

    Returns:
        tuple: (file_content, {name: display_name}), or (None, {}) when the file
        can't be read or doesn't parse (the reason is printed).
    """
    try:
        content, deps = _read_paths(file_path)
    except PathsFileError as e:
        _print_error(f"\n\033[91m✗\033[0m {e}")
        return None, {}
    return content, {name: dep["display"] for name, dep in deps.items()}


def update_paths_file(file_path, existing_content, existing_deps):
    """
    Update an existing paths file with new dependencies.

    Args:
        file_path (str): Path to the paths file
        existing_content (str): Current file content
        existing_deps (dict): Dictionary of existing dependencies

    The file is read again under the paths.py lock (see _PathsLock) once the new names
    are entered, and the new blocks are added to what it holds then.
    """
    print(f"=== Updating existing paths configuration at {file_path} ===")

    if existing_deps:
        print(f"\nFound {len(existing_deps)} existing dependencies:")
        for i, (var_name, display_name) in enumerate(existing_deps.items(), 1):
            print(f"  {i}. {display_name}")

    # Get new dependencies from user
    print("\nAdd new dependencies:")
    new_dependencies = get_dependencies()

    if not new_dependencies:
        print("No new dependencies to add.")
        return 0

    # Read again, under the paths.py lock, what another cli deps or ./configure may have
    # written while the questions were answered, and write before the lock is given back.
    try:
        with _PathsLock(file_path):
            try:
                existing_content, deps = _read_paths(file_path)
            except PathsFileError as e:
                _print_error(f"\n\033[91m✗\033[0m {e}")
                return 1
            existing_deps = {name: dep["display"] for name, dep in deps.items()}
            return _append_menu_dependencies(file_path, existing_content, existing_deps, new_dependencies)
    except (_PathsLockBusy, _LockUnusable) as busy:
        _print_error(f"\n\033[91m✗\033[0m {busy}")
        return 1


def _append_menu_dependencies(file_path, existing_content, existing_deps, new_dependencies):
    """The part of update_paths_file that writes the menu's new dependencies to `file_path`,
    whose content and dependencies are `existing_content` and `existing_deps`."""
    # Check for duplicates and prepare new sections
    to_add = []
    skipped = []
    refused = False

    elsewhere = _variables_set_elsewhere(existing_content)
    for dep in new_dependencies:
        var_name = normalize_name(dep)
        reason = invalid_dep_name(dep)
        if not reason and var_name not in existing_deps:
            reason = _set_elsewhere_reason(var_name, elsewhere, file_path)
        if reason:
            _print_error(f"\033[91m✗\033[0m Not added: {reason}")
            refused = True
        elif var_name in existing_deps:
            skipped.append(dep)
        else:
            to_add.append(dep)
    new_sections = [generate_dependency_section(dep) for dep in _once_each(to_add)]

    if skipped:
        print(f"\nSkipped {len(skipped)} dependencies (already exist):")
        for dep in skipped:
            print(f"  - {dep}")

    if not new_sections:
        print("No new dependencies to add after checking for duplicates.")
        return 1 if refused else 0

    # Append new sections to existing content
    # Remove any trailing whitespace from existing content first
    existing_content = existing_content.rstrip()

    # If this is the first dependency being added, ensure no empty lines at the top
    if not existing_deps:
        # For the first dependency, remove all leading newlines
        first_section = new_sections[0].lstrip("\n")  # Remove leading newlines
        remaining_sections = new_sections[1:] if len(new_sections) > 1 else []

        # If existing content is empty or just whitespace, don't add any newline
        if not existing_content.strip():
            updated_content = first_section + "".join(remaining_sections)
        else:
            # If there's existing content, add one newline to separate
            updated_content = existing_content + "\n" + first_section + "".join(remaining_sections)
    else:
        # For subsequent dependencies, use the normal format
        updated_content = existing_content + "".join(new_sections)

    # Normalize content ending to ensure exactly one newline
    updated_content = normalize_content_ending(updated_content)

    # Write updated content
    try:
        _write_paths(file_path, updated_content)

        print(
            f"\n\033[92m✓\033[0m Updated '\033[92m{file_path}\033[0m' with \033[92m{len(new_sections)}\033[0m new dependencies."
        )
        print(f"You can now edit '{file_path}' to set the actual paths for your dependencies.")
        return 1 if refused else 0

    except Exception as e:
        _print_error(f"\n\033[91m✗\033[0m Error updating file: \033[91m{e}\033[0m")
        return 1


def remove_dependencies_from_file(file_path, existing_content, existing_deps):
    """
    Remove dependencies from an existing paths file.

    Args:
        file_path (str): Path to the paths file
        existing_content (str): Current file content
        existing_deps (dict): Dictionary of existing dependencies
    """
    print(f"=== Removing dependencies from {file_path} ===")

    if not existing_deps:
        print("No dependencies found to remove.")
        return 0

    print("\nCurrent dependencies:")
    dep_list = list(existing_deps.items())
    for i, (var_name, display_name) in enumerate(dep_list, 1):
        print(f"  {i}. {display_name}")

    # Get dependencies to remove
    print("\nEnter the numbers of dependencies to remove (e.g., 1,3,5 or 1-3):")
    print("Press Enter to cancel removal.")

    while True:
        selection = input("Dependencies to remove: ").strip()
        if not selection:
            print("Removal cancelled.")
            return 0

        try:
            # Parse selection (support both comma-separated and ranges)
            indices_to_remove: set[int] = set()
            reversed_range = None

            for part in selection.split(","):
                part = part.strip()
                if "-" in part:
                    # Handle range (e.g., 1-3)
                    start, end = map(int, part.split("-"))
                    if start > end:
                        reversed_range = part  # 3-2 selects nothing: asked again, not "Remove 0"
                    indices_to_remove.update(range(start, end + 1))
                else:
                    # Handle single number
                    indices_to_remove.add(int(part))

            if reversed_range:
                print(f"Invalid range {reversed_range}: write the smaller number first (e.g., 1-3).")
                continue

            # Validate indices
            valid_indices = set(range(1, len(dep_list) + 1))
            invalid_indices = indices_to_remove - valid_indices

            if invalid_indices:
                print(f"Invalid indices: {sorted(invalid_indices)}. Please use numbers 1-{len(dep_list)}.")
                continue

            break

        except ValueError:
            print("Invalid format. Use numbers separated by commas (e.g., 1,3,5) or ranges (e.g., 1-3).")

    # Get dependencies to remove
    deps_to_remove = []
    for idx in sorted(indices_to_remove):
        var_name, display_name = dep_list[idx - 1]
        deps_to_remove.append((var_name, display_name))

    # Confirm removal
    print("\nDependencies to remove:")
    for var_name, display_name in deps_to_remove:
        print(f"  - {display_name}")

    while True:
        confirm = input(f"\nRemove {len(deps_to_remove)} dependencies? (y/n): ").lower().strip()
        if confirm in ["y", "yes"]:
            break
        elif confirm in ["n", "no"]:
            print("Removal cancelled.")
            return 0
        else:
            print("Please enter 'y' for yes or 'n' for no.")

    # Same removal as `cli deps remove`: exact block lines, a backup first, and
    # a re-read afterwards so only what is really gone is reported.
    result = _remove_deps(file_path, [var_name for var_name, _ in deps_to_remove])
    return 1 if result["not_found"] or result["refused"] or result["error"] else 0


# The lock every command that changes paths.py holds while it reads, changes and writes it:
# cli deps add and remove (and the menu), and ./configure --<dep>-path (templates/configure.py).
_PATHS_LOCK = os.path.join(".ngargparser", "paths.lock")
_PATHS_LOCK_WAIT = 30  # seconds to wait for another process to give the lock back


class _PathsLockBusy(Exception):
    """Another process held .ngargparser/paths.lock for all of _PATHS_LOCK_WAIT seconds. The
    message names it when its pid is known, and says that nothing was changed."""

    def __init__(self, holder, lock):
        who = f"Another process (pid {holder})" if holder else "Another process"
        super().__init__(
            f"{who} is changing paths.py: it still holds {lock} after {_PATHS_LOCK_WAIT:g}s. Nothing was changed. "
            "Wait for it to finish (a cli deps or ./configure), then run cli deps again."
        )


class _PathsLock:
    """`with _PathsLock(file_path):` holds the paths.py lock, .ngargparser/paths.lock in the
    folder of `file_path`, for the block: an exclusive flock, so a parallel cli deps add or
    ./configure --<dep>-path waits, and each one reads the file the other wrote. It waits up
    to _PATHS_LOCK_WAIT seconds, then raises _PathsLockBusy. A new lock file is made
    readable and writable for everyone, whatever the umask, as ./configure makes it. A lock
    file that cannot be opened (or made) raises _LockUnusable, naming who can fix it, and
    nothing is changed: going on without it would let two runs lose each other's changes.
    Only where the tool folder cannot be written at all (a read-only deployed copy, where
    writing paths.py fails anyway), and where the lock cannot work (a file system without
    flock, Windows), does the block run without it. The holder writes its pid into the
    file, for the message of the one that waits."""

    def __init__(self, file_path):
        folder = os.path.dirname(file_path)
        self._lock = os.path.join(folder, _PATHS_LOCK) if folder else _PATHS_LOCK
        self._tool = folder or "."
        self._fd = None

    def __enter__(self):
        import time

        try:
            import fcntl
        except ImportError:  # Windows
            return self
        created = False
        try:
            os.makedirs(os.path.dirname(self._lock), exist_ok=True)
            try:
                fd = os.open(self._lock, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o666)
                created = True
            except FileExistsError:
                try:
                    fd = os.open(self._lock, os.O_RDWR)
                except PermissionError:
                    fd = os.open(self._lock, os.O_RDONLY)  # flock needs no write access
        except OSError as e:
            if not os.access(self._tool, os.W_OK):
                return self  # a read-only tool folder (a deployed copy): go on without the lock
            raise _LockUnusable(e, created=not os.path.lexists(self._lock), lock=self._lock) from None
        if created:
            try:
                os.chmod(self._lock, 0o666)  # a+rw, whatever the umask took away: the next user writes here
            except OSError:
                pass
        started = time.monotonic()
        deadline = started + _PATHS_LOCK_WAIT
        said = False  # the wait is said once it has taken a second
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        holder = _lock_holder(self._lock)
                        raise _PathsLockBusy(holder if _pid_alive(holder) else "", self._lock) from None
                    if not said and time.monotonic() - started >= 1:
                        said = True
                        holder = _lock_holder(self._lock)
                        who = (
                            f"pid {holder} (cli deps or ./configure)"
                            if _pid_alive(holder)
                            else "another cli deps or ./configure"
                        )
                        print(f"Waiting for {who} to finish with paths.py...", flush=True)
                    time.sleep(0.05)
                except OSError:  # a file system that cannot lock
                    os.close(fd)
                    return self
        except KeyboardInterrupt:
            # Ctrl-C while it waits: nothing was read or written yet. One line, not a traceback.
            try:
                os.close(fd)
            except OSError:
                pass
            _print_error("\033[91m✗\033[0m Stopped while waiting for paths.py. Nothing was changed.")
            raise SystemExit(130) from None
        self._fd = fd
        try:
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        except (OSError, AttributeError):
            pass  # a lock file only its owner may write keeps the pid it holds
        return self

    def __exit__(self, *_exc):
        if self._fd is not None:
            os.close(self._fd)  # gives the lock back; the file stays, as the build lock's does
            self._fd = None
        return False


def _add_deps(file_path, names):
    """Append a stub block per name to `file_path` (created if missing).

    Returns {"added", "skipped", "rejected", "error"}. A name that ./configure
    couldn't see as a tool is rejected; one already declared is skipped; a
    paths.py that can't be read or doesn't parse is left untouched (`error`).
    The file is read, changed and written under the paths.py lock (see _PathsLock).
    """
    result: _AddResult = {"added": [], "skipped": [], "rejected": [], "error": None}
    names = _split_dep_names(names)  # pepx,netmhcpan is two names
    for name in names:
        reason = invalid_dep_name(name)
        if reason:
            result["rejected"].append(name)
            _print_error(f"\033[91m✗\033[0m Not added: {reason}")
    wanted = [n for n in names if n not in result["rejected"]]
    if not wanted:
        return result
    try:
        with _PathsLock(file_path):
            return _add_deps_locked(file_path, wanted, result)
    except (_PathsLockBusy, _LockUnusable) as busy:
        result["error"] = str(busy)
        _print_error(f"\033[91m✗\033[0m {busy}")
        return result


def _add_deps_locked(file_path, wanted, result):
    """The part of _add_deps that reads and writes `file_path`, under the paths.py lock."""
    target = Path(file_path)
    existing_content, existing_names = "", set()
    elsewhere: dict[str, int] = {}
    if target.exists():
        try:
            existing_content, deps = _read_paths(file_path)
        except PathsFileError as e:
            result["error"] = str(e)
            _print_error(f"\033[91m✗\033[0m {_paths_error_line(e)}")
            return result
        existing_names = set(deps)
        elsewhere = _variables_set_elsewhere(existing_content)

    had_existing_deps = bool(existing_names)
    new_sections = []
    added_now: dict[str, str] = {}  # var name -> the name first given for it in this command
    for name in wanted:
        var_name = normalize_name(name)
        set_elsewhere = None if var_name in existing_names else _set_elsewhere_reason(var_name, elsewhere, file_path)
        if var_name in added_now:
            print(f"\033[93m⚠\033[0m  {name} was given twice (as {added_now[var_name]}, {name}); added once.")
        elif var_name in existing_names:
            result["skipped"].append(name)
        elif set_elsewhere:
            result["rejected"].append(name)
            _print_error(f"\033[91m✗\033[0m Not added: {set_elsewhere}")
        else:
            added_now[var_name] = name
            new_sections.append(generate_dependency_section(name))
            existing_names.add(var_name)
            result["added"].append(name)

    if result["skipped"]:
        print(f"\033[93m⚠\033[0m  Skipped (already declared): {', '.join(result['skipped'])}")

    if not new_sections:
        return result

    existing_content = existing_content.rstrip()
    if not had_existing_deps:
        first_section = new_sections[0].lstrip("\n")
        remaining = "".join(new_sections[1:])
        if not existing_content.strip():
            updated_content = first_section + remaining
        else:
            updated_content = existing_content + "\n" + first_section + remaining
    else:
        updated_content = existing_content + "".join(new_sections)

    updated_content = normalize_content_ending(updated_content)
    # ./configure runs paths.py: text that does not parse is never written.
    try:
        _paths_dependencies(updated_content)
    except PathsFileError as e:
        result["error"] = str(e)
        result["rejected"].extend(result["added"])
        result["added"] = []
        _print_error(f"\033[91m✗\033[0m Adding would break '{file_path}' ({e}); nothing was changed.")
        return result

    try:
        _write_paths(file_path, updated_content)
    except OSError as e:
        # A read-only folder or file, such as a build or deployed copy.
        result["error"] = str(e)
        result["added"] = []
        _print_error(f"\033[91m✗\033[0m Could not write '{e.filename or file_path}': {e.strerror or e}.")
        return result

    print(
        f"\033[92m✓\033[0m Added {len(new_sections)} dependency stub(s) to '{file_path}': {', '.join(result['added'])}"
    )
    # The flag ./configure makes for each one (templates/configure.py, build_arg_parser), all in one
    # command: ./configure stops while any dependency's path is still None, so one at a time fails.
    flags = [normalize_name(name).replace("_", "-") for name in result["added"]]
    lead = "Set its path" if len(flags) == 1 else "Set their paths"
    print(f"   {lead}: ./configure {' '.join(f'--{flag}-path=/path/to/{flag}' for flag in flags)}")
    print(f"   Or edit '{file_path}' by hand.")
    return result


def add_deps_to_paths(file_path, names):
    """Non-interactive equivalent of `setup_paths_file`'s "add" path.
    Appends a stub block for each name in `names` to `file_path`, skipping
    any already declared and rejecting any ./configure couldn't see as a
    tool. Creates the file if missing. Returns the count of blocks added."""
    if not names:
        return 0
    return len(_add_deps(file_path, names)["added"])


def _remove_deps(file_path, names):
    """Delete the block of each named dependency (by name or by title).

    Returns {"removed", "not_found", "refused", "error"}. A block whose lines
    overlap another dependency's is refused rather than cut apart. The file is
    backed up first, and re-read afterwards: only what is really gone counts. It is
    read, changed and written under the paths.py lock (see _PathsLock).
    """
    result: _RemoveResult = {"removed": [], "not_found": [], "refused": [], "error": None}
    names = _split_dep_names(names)  # node,gem is two names, as for cli deps add
    try:
        with _PathsLock(file_path):
            return _remove_deps_locked(file_path, names, result)
    except (_PathsLockBusy, _LockUnusable) as busy:
        result["error"] = str(busy)
        _print_error(f"\033[91m✗\033[0m {busy}")
        return result


def _remove_deps_locked(file_path, names, result):
    """The part of _remove_deps that reads and writes `file_path`, under the paths.py lock."""
    if not Path(file_path).exists():
        result["error"] = f"'{file_path}' not found"
        _print_error(f"\033[91m✗\033[0m '{file_path}' not found. Nothing to remove.")
        return result
    try:
        content, deps = _read_paths(file_path)
    except PathsFileError as e:
        result["error"] = str(e)
        _print_error(f"\033[91m✗\033[0m {_paths_error_line(e)}")
        return result

    targets = []
    for n in names:
        # The variable name (<name>_path) decides first. A title is only a label and
        # can sit above another dependency's settings, so it is the fallback, and
        # only when exactly one block carries it.
        if n in deps or normalize_name(n) in deps:
            match = n if n in deps else normalize_name(n)
        else:
            titled = [
                name
                for name, dep in deps.items()
                if n == dep["display"] or normalize_name(n) == normalize_name(dep["display"])
            ]
            if len(titled) > 1:
                result["refused"].append(n)
                _print_error(
                    f"\033[91m✗\033[0m Not removed: '{n}' is the title of several dependencies "
                    f"({', '.join(titled)}). Name the one to remove by its variable name."
                )
                continue
            match = titled[0] if titled else None
            if match is not None and _written_as_a_dependency_name(n):
                # `n` names a dependency, and none declares its variables: the block whose title
                # reads the same holds another dependency's settings (`n` removed already, say,
                # and the title is a hand-made label), which this command was not asked to remove.
                result["refused"].append(n)
                _print_error(
                    f"\033[91m✗\033[0m Not removed: {n} is not declared in {file_path}. The title "
                    f"{deps[match]['display']} is on the block of {match}; to remove that block, run: "
                    f"cli deps remove {match}"
                )
                continue
        if match is None:
            result["not_found"].append(n)
        elif match not in targets:
            targets.append(match)

    for name in list(targets):
        others = _mixed_with(name, deps, targets)
        if others:
            targets.remove(name)
            result["refused"].append(name)
            _print_error(
                f"\033[91m✗\033[0m Not removed: the lines of '{name}' are mixed with those of "
                f"{', '.join(others)}. Edit '{file_path}' by hand."
            )

    if result["not_found"]:
        print(f"\033[93m⚠\033[0m  Not in '{file_path}' (skipped): {', '.join(result['not_found'])}")
    if not targets:
        return result

    # A name with two blocks (a duplicate, as an older interactive menu wrote) still has
    # one after its last block goes: remove each block in turn, the last one first,
    # until the name is gone or its next block is mixed with another dependency's lines.
    stub_comments = _stub_comment_patterns()
    updated, remaining, todo = content, deps, list(targets)
    kept: list[str] = []
    while todo:
        updated_now, kept_now = _drop_blocks(updated, remaining, todo, stub_comments)
        try:
            remaining = _paths_dependencies(updated_now)
        except PathsFileError as e:
            result["error"] = str(e)
            _print_error(f"\033[91m✗\033[0m Removing would break '{file_path}' ({e}); nothing was changed.")
            return result
        if updated_now == updated:
            break
        updated, kept = updated_now, kept + kept_now
        todo = [name for name in todo if name in remaining and not _mixed_with(name, remaining, todo)]

    # ./configure runs paths.py, so a line left behind that reads a variable the removal takes
    # out (`x = os.path.join(base_path, "bin")`, `pepx_path += "/bin"`) would stop it with a
    # NameError: refuse, and name each such line as paths.py has it now.
    uses = _uses_of_removed_variables(content, updated)
    if uses:
        result["refused"] += [name for name in targets if name not in result["refused"]]
        lines = _lines_before_removal(content, updated)
        where = ", ".join(f"line {lines.get(line, line)} uses {var}" for line, var in uses)
        fix = "that line" if len(uses) == 1 else "those lines"
        _print_error(
            f"\033[91m✗\033[0m Not removed: {', '.join(targets)}. In '{file_path}', {where}, which the removal would "
            f"delete, so ./configure would fail. Delete or fix {fix} first. Nothing was changed."
        )
        return result

    try:
        # The new content is complete (and parses) before anything is written: the backup
        # first, then paths.py, each through a temporary file, so neither is ever cut short.
        backup_path = _backup_file(file_path)
        _write_paths(file_path, updated)
    except OSError as e:
        # A read-only folder or file, such as a build or deployed copy.
        result["error"] = str(e)
        _print_error(f"\033[91m✗\033[0m Could not write '{e.filename or file_path}': {e.strerror or e}.")
        return result

    result["removed"] = [name for name in targets if name not in remaining]
    # Count blocks, not names: each block has its own <name>_path line.
    before, after = _path_assignments(content), _path_assignments(updated)
    blocks = {n: before.get(f"{n}_path", 0) - after.get(f"{n}_path", 0) for n in targets}

    # Name each block like `cli deps list` does: its title, and its variable name when they differ.
    def label(n):
        notes = ([] if deps[n]["display"] == n else [n]) + ([f"{blocks[n]} blocks"] if blocks[n] > 1 else [])
        return deps[n]["display"] + (f" ({', '.join(notes)})" if notes else "")

    removed = [n for n in targets if blocks[n] > 0]
    print(
        f"\033[92m✓\033[0m Removed {sum(blocks.values())} dependency block(s) from '{file_path}': "
        f"{', '.join(label(n) for n in removed)}"
    )
    if backup_path:
        print(f"  └ Backup saved to '{backup_path}'.")
    if kept:
        print(f"\033[93m⚠\033[0m  Kept {len(kept)} line(s) that cli deps did not write:")
        for line in kept:
            print(f"     {line}")
    for name in targets:
        if name in remaining:
            result["refused"].append(name)
            others = _mixed_with(name, remaining, targets)
            why = f" whose lines are mixed with those of {', '.join(others)}" if others else ""
            _print_error(
                f"\033[91m✗\033[0m '{name}' is still declared in '{file_path}': it has another block{why}. "
                f"Edit '{file_path}' by hand."
            )
    return result


def _uses_of_removed_variables(before, after):
    """[(line, variable)] for each place in paths.py text `after` that reads a variable
    `before` set at the top level and `after` no longer sets anywhere: a load (`x =
    pepx_path`), a `pepx_path += ...`, a `del pepx_path`. Lines are `after`'s, in order."""
    import ast

    def top_level(content):
        return {var for var in map(_top_level_variable, ast.parse(content).body) if var}

    nodes = list(ast.walk(ast.parse(after)))
    # A `+=` target is a Store too, but it reads the variable first.
    augmented = {id(node.target) for node in nodes if isinstance(node, ast.AugAssign)}
    still_set = {
        node.id
        for node in nodes
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and id(node) not in augmented
    }
    gone = top_level(before) - top_level(after)
    found = {
        (node.lineno, node.id)
        for node in nodes
        if isinstance(node, ast.Name)
        and node.id in gone - still_set
        and (not isinstance(node.ctx, ast.Store) or id(node) in augmented)
    }
    return sorted(found)


def _lines_before_removal(before, after):
    """{line in paths.py text `after`: the same line in `before`}, for `after` made from `before`
    by taking lines out (see _drop_blocks)."""
    import difflib

    matcher = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False)
    return {b + k + 1: a + k + 1 for a, b, size in matcher.get_matching_blocks() for k in range(size)}


def _written_as_a_dependency_name(name):
    """True when `name` is written the way a dependency name is (pepx, Pepx, mhci-predictor:
    what `cli deps add` turns into the variable prefix by lower-casing it and changing '-'
    to '_'), not as a title with spaces or other characters ("T Cell Class I")."""
    return normalize_name(name) == name.lower().replace("-", "_")


def _mixed_with(name, deps, targets):
    """The dependencies in `deps`, other than `targets`, whose lines overlap those of `name`."""
    dep = deps[name]
    return [
        other
        for other, o in deps.items()
        if other not in targets and o["start"] <= dep["end"] and dep["start"] <= o["end"]
    ]


def _drop_blocks(content, deps, names, stub_comments):
    """Return (content, kept): paths.py `content` without what cli deps writes for each
    of `names` (its title, its settings and the comments of a stub block, as `deps`,
    from _paths_dependencies(content), places them), and the lines inside a block that
    stay because the developer wrote them (a setting of theirs, a note)."""
    lines = content.splitlines(keepends=True)
    drop: set[int] = set()
    kept: set[int] = set()  # line numbers; one another dependency of `names` takes with it is not kept
    for name in names:
        dep = deps[name]
        span = range(dep["start"], dep["end"] + 1)
        # A setting of this dependency that is commented out goes with it too.
        disabled = re.compile(rf"#\s*{re.escape(name)}(?:{'|'.join(DEP_FIELDS)}|_entry|_python)\s*=.*")
        patterns = [*stub_comments, disabled]
        theirs = [
            number
            for number in span
            if number not in dep["own_lines"]
            and lines[number - 1].strip()
            and not any(p.fullmatch(lines[number - 1].strip()) for p in patterns)
        ]
        if theirs:
            drop.update(number for number in span if number not in theirs and lines[number - 1].strip())
            kept.update(theirs)
        else:
            drop.update(span)  # the whole block, blank lines too
        drop.update(dep["own_lines"])  # a <name>_entry set below the block goes with it
        # A setting on a line with another statement (`pepx_venv=None; netmhcpan_entry="x"`): the
        # line stays whole, as a chained `pepx_venv = netmhcpan_entry = None` does, and is named.
        kept.update(dep.get("shared_lines", ()))
    updated = "".join(line for number, line in enumerate(lines, 1) if number not in drop)
    left = [lines[number - 1].rstrip("\r\n") for number in sorted(kept - drop)]
    return normalize_content_ending(re.sub(r"\n{3,}", "\n\n", updated).lstrip("\n")), left


def _path_assignments(content):
    """How many top-level `<name>_path = ...` lines paths.py `content` has, by variable."""
    import ast

    counts: dict[str, int] = {}
    for node in ast.parse(content).body:
        var = _top_level_variable(node)
        if var and var.endswith("_path"):
            counts[var] = counts.get(var, 0) + 1
    return counts


def remove_deps_from_paths(file_path, names):
    """Non-interactive equivalent of `setup_paths_file`'s "remove" path.
    Deletes the block of each dependency whose name or title matches an entry
    in `names`. Returns the count of blocks removed."""
    if not names:
        return 0
    return len(_remove_deps(file_path, names)["removed"])


def _list_deps(file_path):
    """Print the dependencies declared in `file_path`. Returns (count, error)."""
    if not Path(file_path).exists():
        print(f"\033[93m⚠\033[0m  '{file_path}' not found — no dependencies declared.")
        return 0, None
    try:
        _content, deps = _read_paths(file_path)
    except PathsFileError as e:
        _print_error(f"\033[91m✗\033[0m {e}")
        return 0, str(e)
    if not deps:
        print(f"No dependencies declared in '{file_path}'.")
        return 0, None

    print(f"Dependencies declared in '{file_path}':")
    for name, dep in deps.items():
        label = dep["display"] if dep["display"] == name else f"{dep['display']} ({name})"
        path_value = dep["path"]
        status = "\033[92m●\033[0m" if path_value not in ("None", "''", '""') else "\033[93m○\033[0m"
        print(f"  {status} {label:<30} {name}_path = {path_value}")
    print("\n\033[92m●\033[0m = path filled in   \033[93m○\033[0m = stub (still None)")
    # A name written by hand that cli deps add would refuse (ld_library_path, app_path): listed,
    # and said, since ./configure reads the same rule (dep_names.py).
    for name in deps:
        reason = invalid_dep_name(name)
        if reason:
            print(f"\033[93m⚠\033[0m  '{file_path}' declares {name}, a name cli deps refuses: {reason}")
    return len(deps), None


def list_deps_in_paths(file_path):
    """Print the dependency blocks declared in `file_path`. Returns the count."""
    return _list_deps(file_path)[0]


def setup_paths_file(file_path):
    """
    Setup or update a paths file at the specified location.
    If file exists, provides options to add or remove dependencies.
    If not, create a new one.

    Args:
        file_path (str): Path where to create or update the paths file
    """
    target_path = Path(file_path)

    if target_path.exists():
        # File exists: offer options. Never overwrite a paths.py that can't be read or
        # doesn't parse; the not-UTF-8 line already says what to do.
        try:
            existing_content, deps = _read_paths(file_path)
        except PathsFileError as e:
            _print_error(f"\n\033[91m✗\033[0m {e}")
            if not isinstance(e, _PathsNotUtf8):
                _print_error(f"Fix '{file_path}' first; nothing was changed.")
            return 1
        existing_deps = {name: dep["display"] for name, dep in deps.items()}

        print(f"=== Paths file exists at {file_path} ===")

        if existing_deps:
            print(f"\nFound {len(existing_deps)} existing dependencies:")
            for i, (var_name, display_name) in enumerate(existing_deps.items(), 1):
                print(f"  {i}. {display_name}")
        else:
            print("\nNo existing dependencies found.")

        # Ask user what they want to do
        print("\nWhat would you like to do?")
        print("1. Add new dependencies")
        print("2. Remove existing dependencies")
        print("3. Cancel")

        while True:
            choice = input("Choose an option (1-3): ").strip()
            if choice == "1":
                return update_paths_file(file_path, existing_content, existing_deps)
            elif choice == "2":
                return remove_dependencies_from_file(file_path, existing_content, existing_deps)
            elif choice == "3":
                print("Operation cancelled.")
                return 0
            else:
                print("Please enter 1, 2, or 3.")
    else:
        # File doesn't exist - create new one
        return create_paths_file(file_path)


def _enclosing_tool(above=False):
    """The ngargparser tool folder that holds the current folder (the current folder
    itself or one of its parents; with `above`, one of its parents only), as (path, first
    marker), or None. The search stops at the home folder or the filesystem root,
    whichever comes first."""
    folder = Path.cwd().resolve()
    if above:
        try:
            home = Path.home().resolve()
        except (KeyError, RuntimeError):  # no home folder known
            home = None
        if folder == home or folder.parent == folder:
            return None
        folder = folder.parent
    try:
        home = Path.home().resolve()
    except (KeyError, RuntimeError):  # no home folder known
        home = None
    while True:
        markers = _tool_markers(str(folder))
        if markers:
            return str(folder), markers[0]
        if folder == home or folder.parent == folder:
            return None
        folder = folder.parent


def startapp_command(args):
    # A tool generated inside another one ships in that tool's tarball (make build
    # packs the whole folder), so refuse before anything is created.
    enclosing = _enclosing_tool()
    if enclosing:
        import shlex

        tool, marker = enclosing
        where = "This folder is" if tool == str(Path.cwd().resolve()) else "This folder is inside"
        # Both forms end with the command to run: in the folder that holds the tool.
        _print_error(
            f"\033[91m✗\033[0m {where} the ngargparser tool {tool} (it has {marker}). A tool generated here "
            "would ship in that tool's tarball, so nothing was created. Run cli generate in a folder outside "
            f"it: cd {shlex.quote(os.path.dirname(tool))} && cli generate {shlex.quote(args.project_name)}"
        )
        return 1
    # The example tool, in any case (Example, EXAMPLE); a name close to it (examples) makes a
    # blank tool, with a note at the end in case the example was meant.
    is_example = args.project_name.lower() == "example"
    near_example = False
    if is_example:
        rc = create_example_structure()
    else:
        import difflib

        near_example = bool(difflib.get_close_matches(args.project_name.lower(), ["example"], n=1, cutoff=0.8))
        error = validate_project_name(args.project_name)
        if error:
            _print_error(f"\033[91m✗\033[0m {error}")
            return 1
        rc = create_project_structure(args.project_name)

    # Scaffolding failed (and already cleaned up after itself) — don't write
    # paths.py/.env into a missing or partial project.
    if rc:
        return rc

    project_dir_name = "aa-counter" if is_example else args.project_name

    # Seed a .gitignore so ngargparser runtime artifacts (and common cruft)
    # stay out of the user's git history from the first commit.
    ensure_gitignore(os.path.join(project_dir_name, ".gitignore"))
    # Record the framework files as generated, so `cli sync` can spot later local edits.
    _write_framework_hashes(_sync_framework_files(), root=project_dir_name)

    # Write initial .env with APP_NAME and APP_ROOT in the new project root, plus
    # APP_ENTRY: the start-up script a tool that depends on this one runs.
    try:
        import glob

        project_root_abs = os.path.abspath(project_dir_name)
        env_file_path = os.path.join(project_dir_name, ".env")
        entry_scripts = sorted(glob.glob(os.path.join(project_dir_name, "src", "run_*.py")))
        with open(env_file_path, "w", encoding="utf-8") as f:
            f.write(f"APP_ROOT={project_root_abs}\n")
            f.write(f"APP_NAME={project_dir_name}\n")
            if len(entry_scripts) == 1:
                f.write(f"APP_ENTRY=src/{os.path.basename(entry_scripts[0])}\n")
        print(f"\033[92m✓\033[0m Created initial '.env' at '\033[92m{env_file_path}\033[0m'")
    except Exception as e:
        _print_error(f"\033[91m✗\033[0m Error writing initial .env: \033[91m{e}\033[0m")

    # Which ngargparser did this, so a stale `cli` earlier on PATH shows up right away.
    print(f"ℹ Generated with ngargparser {__version__}.")

    # Closing hint: the path the README's Quickstart takes from here.
    entry = f"src/run_{format_project_name(project_dir_name)}.py"
    print()
    print(f"Tool '\033[1m{project_dir_name}\033[0m' is ready. Next steps:")
    print(f"  cd {project_dir_name}")
    print("  uv sync                  # install the locked packages into .venv/")
    if not is_example:
        print(f"  # write the predict logic: replace the predict branch in {entry}")
    print(f"  uv run python {entry} predict -j src/example.json")
    print("  make build               # build the tarball")
    if near_example:
        print(f"Note: '{args.project_name}' made a blank tool; for the sample app run: cli generate example")


def deps_command(args):
    action = getattr(args, "deps_action", None)
    # A build or deployed copy has every sign of a tool, but the next build replaces it
    # (or it is what the host runs), so a paths.py written there is not the tool's.
    # Listing writes nothing, so it still works there.
    if action != "list" and os.path.exists(_BUILD_MARKER):
        _print_error(_build_copy_message("deps"))
        return 1
    # paths.py belongs in a tool's top folder, where ./configure reads it; anywhere
    # else (another project, the tool's src/) it would be a file nothing reads.
    if not _is_ngargparser_tool():
        _print_error(_not_a_tool_message("deps"))
        return 1
    # Exit 1 when something asked for didn't happen, so scripts and CI can tell.
    if action == "add":
        if args.names:
            result = _add_deps("paths.py", args.names)
            return 1 if result["rejected"] or result["error"] else 0
        # No names given → fall through to interactive add menu via setup_paths_file
        return _deps_menu("paths.py")
    if action == "remove":
        if args.names:
            result = _remove_deps("paths.py", args.names)
            return 1 if result["not_found"] or result["refused"] or result["error"] else 0
        return _deps_menu("paths.py")
    if action == "list":
        _count, error = _list_deps("paths.py")
        return 1 if error else 0
    # Bare `cli deps` → today's interactive flow
    return _deps_menu("paths.py")


def _deps_menu(file_path):
    """Run the interactive `cli deps` menu; return 1 when it changed nothing because of an error.

    The menu asks questions, so without a terminal (a script, CI) it stops at once
    instead of reading empty answers and reporting success.
    """
    import sys

    if not sys.stdin.isatty():
        _print_error(
            "\033[91m✗\033[0m cli deps without names asks questions, so it needs a terminal. "
            "Use 'cli deps add <name> ...', 'cli deps remove <name> ...' or 'cli deps list'."
        )
        return 1
    try:
        return setup_paths_file(file_path)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 1


class _RemoteUnavailable(Exception):
    """Raised (only in strict mode) when `git ls-remote` can't reach the remote.

    Lets callers tell "couldn't check" apart from "checked, found no tags" —
    both of which otherwise collapse into a None return.
    """


_UNSET = object()  # sentinel: "argument not supplied" (distinct from None)


# A release tag: vX.Y.Z, the v optional, a +build suffix allowed. Pre-release tags
# (v0.4.2-test, v1.0.0-rc1) do not match.
_RELEASE_TAG_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:\+.*)?")


def _latest_release_tag(remote_url, timeout=10, *, strict=False, prompt=True):
    """Return the highest release tag from a git remote, or None.

    Uses `git ls-remote --tags --refs` (no clone), keeps tags matching
    `vX.Y.Z` (an optional `+build` suffix is allowed), and returns the
    highest one by numeric (major, minor, patch). Pre-release tags such as
    `v0.4.2-test` or `v1.0.0-rc1` are skipped, so a test tag never becomes the
    default upgrade or the version the update notice points to; install one
    on purpose with `--ref`. `timeout` bounds the network call (kept short
    for the update notifier).

    Returns None when the remote is reachable but has no release tags. On a
    transport failure (network/timeout/git error) it returns None by default,
    or raises `_RemoteUnavailable` when `strict=True` — so a network blip is
    not misreported as "no tags exist".

    `prompt=False` makes the lookup non-interactive: git may not ask for
    credentials on the terminal or through an askpass helper, so a remote that
    wants credentials fails fast instead of stalling until `timeout`. Stored
    credentials, credential helpers and SSH keys still apply.
    """
    import subprocess

    env, stdin = None, None
    if not prompt:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}
        stdin = subprocess.DEVNULL
    try:
        out = subprocess.check_output(
            ["git", "ls-remote", "--tags", "--refs", remote_url],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            env=env,
            stdin=stdin,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        if strict:
            raise _RemoteUnavailable(str(e)) from e
        return None
    semver_tags = []
    for line in out.splitlines():
        ref = line.split("\t", 1)[-1] if "\t" in line else ""
        name = ref.rsplit("/", 1)[-1]
        m = _RELEASE_TAG_RE.fullmatch(name)
        if m:
            semver_tags.append((tuple(int(g) for g in m.groups()), name))
    return max(semver_tags)[1] if semver_tags else None


def _is_uv_tool_install():
    """True when this ngargparser runs from a uv-managed tool env.

    uv installs each tool under `<data-dir>/uv/tools/<name>/`, and those
    venvs ship without pip — so `python -m pip` can't self-upgrade them.
    Detect the layout from sys.prefix so sync can use `uv tool install`.
    """
    import sys

    return os.path.join("uv", "tools") in os.path.normpath(getattr(sys, "prefix", ""))


BASE_REPO_URL = "git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git"


def _local_source(ref):
    """If `ref` points at a local directory ('.' or a path), return its absolute
    path; else None. Lets contributors install their working tree via `--ref .`.

    Only a ref written as a path ('.', '..', or starting with './', '../', '/' or
    '~') that resolves to an existing directory counts, so a git ref such as
    'latest', 'master' or 'feature/x' is never taken for a folder of that name in
    the current directory.
    """
    import os

    if not ref:
        return None
    if ref not in (".", "..") and not ref.startswith(("./", "../", "/", "~")):
        return None
    p = os.path.abspath(os.path.expanduser(ref))
    return p if os.path.isdir(p) else None


def _is_ngargparser_checkout(path):
    """Return (ok, message). True iff `path` looks like the ngargparser package source."""
    import os

    pyproject = os.path.join(path, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return False, f"'{path}' has no pyproject.toml — not an ngargparser checkout."
    try:
        with open(pyproject, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return False, f"can't read '{pyproject}': {e}"
    if 'name = "ngargparser"' not in content:
        return False, f"'{path}' doesn't look like the ngargparser repo (pyproject name is not 'ngargparser')."
    return True, ""


def _local_checkout():
    """Return the checkout path when this ngargparser runs from a source checkout
    (an editable install of the repo), else None. An installed release lives in
    site-packages, whose parent is not an ngargparser checkout."""
    import os

    parent = os.path.dirname(str(NGPARSER_DIR))
    return parent if _is_ngargparser_checkout(parent)[0] else None


def _checkout_version(path):
    """Best-effort read of the [project] version from a checkout's pyproject.toml, or None.

    Used to tailor the post-upgrade sync reminder after a local-checkout install, where the
    installed semver isn't otherwise known in-process (no re-exec; `__version__` is still stale)."""
    import os
    import re

    try:
        with open(os.path.join(path, "pyproject.toml"), encoding="utf-8") as f:
            m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', f.read())
    except OSError:
        return None
    return m.group(1) if m else None


def _git_in(folder, *args):
    """Run git with `args` in `folder`; its stdout, or None when git is missing, fails or
    takes more than 5 seconds."""
    import subprocess

    try:
        done = subprocess.run(
            ["git", "-C", folder, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _installed_from_local_folder():
    """True when ngargparser was installed from a folder on this machine rather than from
    GitLab or a package index (`cli upgrade --ref <path>`, `pip install <path>`): its
    files are that folder's at install time, which the version number does not tell apart."""
    import json

    try:
        import importlib.metadata

        text = importlib.metadata.distribution("ngargparser").read_text("direct_url.json")
        record = json.loads(text) if text else {}
    except Exception:
        return False
    url = record.get("url") if isinstance(record, dict) else None
    dir_info = record.get("dir_info") if isinstance(record, dict) else None
    # An editable install runs from its checkout, which _local_checkout finds.
    editable = isinstance(dir_info, dict) and dir_info.get("editable") is True
    return isinstance(url, str) and url.startswith("file:") and "vcs_info" not in record and not editable


def _local_version_label():
    """The local version label (PEP 440, the part after '+') of this ngargparser's files, or
    None when they are a release's.

    From a git checkout (an editable install, or the source folder on PYTHONPATH):
    g<short sha of HEAD>, with .dirty when files under ngargparser/ have uncommitted
    changes. 'local' for a checkout git cannot read and for an install from a local folder."""
    checkout = _local_checkout()
    if not checkout:
        return "local" if _installed_from_local_folder() else None
    head = _git_in(checkout, "rev-parse", "--short", "HEAD")
    if not head or not head.strip():
        return "local"
    changes = _git_in(checkout, "status", "--porcelain", "--untracked-files=no", "--", os.path.basename(NGPARSER_DIR))
    return f"g{head.strip()}" + (".dirty" if changes and changes.strip() else "")


def _sync_version():
    """The version `cli sync` stamps as scaffold_version: the installed version, plus the
    local label of a checkout or local install (0.4.1+g123abcd), whose files can differ
    from the 0.4.1 release's. See _checkout_stamp_stop."""
    label = _local_version_label()
    return f"{__version__}+{label}" if label and "+" not in __version__ else __version__


def _local_label(version):
    """The local label of version string `version` ('g123abcd.dirty' in 0.4.1+g123abcd.dirty), or ''."""
    return (version or "").strip().partition("+")[2]


def _checkout_stamp_stop(stamp, version):
    """Why syncing from `version` (see _sync_version) would replace the framework files of a tool
    stamped `stamp` with ones that may be older though the two are the same version number, or
    None. A stamp with a local label (0.4.1+g123abcd) came from a development checkout: the 0.4.1
    release, or another checkout, can have older files under the same number. A release stamp, an
    equal label, and a checkout whose history holds the stamp's commit are not stopped at."""
    synced, installed = _local_label(stamp), _local_label(version)
    if not synced or synced == installed:
        return None
    if not installed:
        return (
            f"This tool was last synced from a development checkout of ngargparser ({stamp}); the installed "
            f"ngargparser is the {__version__} release, whose framework files may be older. Nothing was changed. "
            "Sync from that checkout again, or pass --allow-downgrade to take the release's files."
        )
    commit = re.fullmatch(r"g([0-9a-f]+)(\.dirty)?", synced)
    head = re.fullmatch(r"g([0-9a-f]+)(\.dirty)?", installed)
    checkout = _local_checkout()
    if commit and head and checkout:
        same_commit = commit.group(1).startswith(head.group(1)) or head.group(1).startswith(commit.group(1))
        if same_commit:
            # The labels differ only in .dirty: this checkout's edits on that commit are newer,
            # but a clean checkout of the commit lacks the edits the tool was synced with.
            if not commit.group(2):
                return None
        elif _git_in(checkout, "merge-base", "--is-ancestor", commit.group(1), "HEAD") is not None:
            return None  # the checkout has the stamp's commit in its history: its files are newer
    return (
        f"This tool was last synced from a development checkout of ngargparser ({stamp}); the installed "
        f"ngargparser is another checkout or local copy ({version}), whose framework files may be older. "
        "Nothing was changed. Sync from that checkout again, or pass --allow-downgrade to take these files."
    )


def _resolve_upgrade_url(ref="latest", dev=False, latest_tag=_UNSET):
    """Resolve (source, label) to upgrade ngargparser to.

    Honors NGARGPARSER_UPGRADE_URL (full override). `dev` forces 'master'.
    `ref='latest'` resolves the highest semver tag on the remote, falling back
    to 'master' when no tags exist. A `ref` that points at a local directory
    ('.' or a path) returns that absolute path with a 'local:' label, so the
    install comes from the working tree instead of the git remote.

    When `ref='latest'`, pass `latest_tag` to reuse an already-resolved tag
    (or None for "no tags") instead of querying the remote again; omit it to
    query here as before.
    """
    import os

    override = os.environ.get("NGARGPARSER_UPGRADE_URL")
    if override:
        return override, ref
    if dev:
        ref = "master"
    local = _local_source(ref)
    if local:
        return local, f"local:{local}"
    if ref == "latest":
        if latest_tag is _UNSET:
            latest_tag = _latest_release_tag(BASE_REPO_URL[len("git+") :])
        ref = latest_tag or "master"
    return f"{BASE_REPO_URL}@{ref}", ref


def _run_self_upgrade(url):
    """Install `url` into the current ngargparser env — whatever the install type.

    Picks an installer that works for the current environment so a developer only
    ever types `cli upgrade`:
      - uv-tool install     -> `uv tool install`        (uv-tool envs have no pip)
      - env that has pip     -> `python -m pip install`
      - pip-less venv + uv   -> `uv pip install --python <this interpreter>`
        (e.g. a uv-managed project .venv, which ships without pip)
    Returns 0 on success, else a non-zero exit code (after printing a diagnostic).
    """
    import importlib.util
    import shutil
    import subprocess
    import sys

    uv = shutil.which("uv")
    has_pip = importlib.util.find_spec("pip") is not None

    if _is_uv_tool_install() and uv:
        cmd, tool = ["uv", "tool", "install", "--force", "--reinstall", url], "uv tool"
    elif has_pip:
        cmd, tool = [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--quiet", url], "pip"
    elif uv:
        cmd, tool = ["uv", "pip", "install", "--python", sys.executable, "--reinstall", url], "uv pip"
    else:
        _print_error("\033[91m✗\033[0m Can't upgrade: this environment has no pip and uv isn't on PATH.")
        _print_error("  Install uv (https://astral.sh/uv) or run manually:")
        _print_error(f"    uv tool install --force --reinstall '{url}'")
        return 1

    try:
        subprocess.check_call(cmd)
        return 0
    except subprocess.CalledProcessError as e:
        _print_error(f"\033[91m✗\033[0m Upgrade failed ({tool} exit {e.returncode}).")
        _print_error(f"  Try manually:  uv tool install --force --reinstall '{url}'")
        return e.returncode or 1


def _project_scaffold_version():
    """Return the [tool.ngargparser] scaffold_version stamp in CWD's pyproject.toml, or None
    (i.e. None when not run from inside a stamped scaffolded project). A number
    (scaffold_version = 0.5) is the version it spells (see _scaffold_stamp_text)."""
    return _project_scaffold_stamp()[0]


def _project_scaffold_stamp():
    """(stamp, problem) of CWD's pyproject.toml, as _scaffold_stamp_text gives them; (None, None)
    without a readable pyproject.toml."""
    if not os.path.isfile("pyproject.toml"):
        return None, None
    try:
        content = _read_pyproject()[0]
    except (OSError, UnicodeDecodeError):
        return None, None
    return _scaffold_stamp_text(content)


def _derived_app_name():
    """The APP_NAME ./configure gives the tool in CWD: the one in its own .env file, else
    the name it derives from the folder ('ng_<name>-local' gives <name>, any other
    folder its own name)."""
    if os.path.isfile(".env"):
        from dotenv import dotenv_values

        env_app_name = dotenv_values(".env").get("APP_NAME")
        if env_app_name:
            return env_app_name
    base_name = os.path.basename(os.getcwd())
    match = re.match(r"^ng[_-]([A-Za-z0-9_-]+?)-local$", base_name)
    return match.group(1) if match else base_name


def _project_ngargparser_key(key):
    """Return `key` from the [tool.ngargparser] table in CWD's pyproject.toml, or None."""
    if not os.path.isfile("pyproject.toml"):
        return None
    try:
        content = _read_pyproject()[0]
    except (OSError, UnicodeDecodeError):
        return None
    return _ngargparser_value(content, key)


def _remind_sync(installed, did_install):
    """After a successful `cli upgrade`, remind the developer to run `cli sync` so a project's
    vendored framework files actually pick up the newly installed tool. Informational only — the
    project keeps working on its old vendored files until it's synced. Context-aware:

      - inside a stamped scaffolded project that's *behind* the installed tool → a specific nudge
        naming both versions;
      - inside a stamped project that's already current (or has an unparseable stamp) → stay silent;
      - anywhere else → a generic reminder, but only when we actually (re)installed (`did_install`),
        so the 'already up to date' path doesn't nag when nothing changed.
    """
    stamp = _project_scaffold_version()
    if stamp:
        proj, tool = _version_key(stamp), _version_key(installed)
        if proj and tool and proj < tool:
            lead = f"You upgraded the CLI to {installed}" if did_install else f"The CLI is on {installed}"
            print(f"\nℹ {lead}, but this tool is still on {stamp}.")
            print(f"  Run `cli sync` here to update this tool to {installed}.")
        return
    if did_install:
        ver = installed or "the new version"
        print("\nℹ `cli upgrade` updates the CLI, not your tools.")
        print(f"  Run `cli sync` inside a tool to update it to {ver}.")


def upgrade_command(args):
    """Check GitLab for the latest tag and upgrade ngargparser in place.

    Works from any directory and never touches project files. `--check` only
    reports installed-vs-latest. Unlike `sync` it does not re-exec — the next
    `cli` invocation runs the upgraded code.
    """
    import os

    ref, dev = getattr(args, "ref", "latest"), getattr(args, "dev", False)
    check = getattr(args, "check", False)
    current = __version__

    # Plain-latest case: resolve the tag ourselves in strict mode so a network
    # failure is reported honestly instead of being misread as "no tags exist"
    # (and silently resolving to master). The resolved tag is handed to
    # _resolve_upgrade_url so it doesn't query the remote a second time.
    latest_tag = _UNSET
    plain_latest = (
        ref == "latest" and not dev and _local_source(ref) is None and not os.environ.get("NGARGPARSER_UPGRADE_URL")
    )
    if plain_latest:
        try:
            latest_tag = _latest_release_tag(BASE_REPO_URL[len("git+") :], strict=True)
        except _RemoteUnavailable:
            print("⚠ Couldn't reach the remote to check for the latest release (network/VPN, or git unavailable).")
            print(f"ℹ Installed: {current}    Latest: unknown")
            if not check:
                print(
                    "  To upgrade anyway, name the version: cli upgrade --ref <tag>, or cli upgrade --dev for master."
                )
            return 2

    url, resolved = _resolve_upgrade_url(ref, dev, latest_tag=latest_tag)
    target = resolved.lstrip("v") if resolved else resolved

    # Local checkout mode (`--ref .` or `--ref <path>`): install the working tree.
    # Skips the tag-resolution / up-to-date logic — you always want what's on disk.
    if resolved.startswith("local:"):
        path = url
        ok, msg = _is_ngargparser_checkout(path)
        if not ok:
            _print_error(f"\033[91m✗\033[0m {msg}")
            return 1
        if check:
            print(f"ℹ Would install ngargparser from local checkout: {path}")
            return 0
        print(f"ℹ Installing ngargparser from local checkout: {path} …")
        rc = _run_self_upgrade(path)
        if rc:
            return rc
        print(f"\033[92m✓\033[0m Installed ngargparser from {path}. Run 'cli --version' to confirm.")
        _remind_sync(_checkout_version(path), did_install=True)
        return 0

    pinned = dev or ref != "latest" or os.environ.get("NGARGPARSER_UPGRADE_URL")
    if not pinned:
        if resolved == "master":
            print("⚠ No semver tags on remote; 'latest' resolves to master.")
        else:
            up_to_date = current == target
            # A pre-release or a checkout build can be newer than the latest release tag;
            # installing the tag would be a downgrade.
            ahead = not up_to_date and _installed_is_newer(resolved)
            status = "  (up to date)" if up_to_date else "  (installed is newer)" if ahead else "  (update available)"
            print(f"ℹ Installed: {current}    Latest: {resolved}" + status)
            if check:
                return 0
            if up_to_date:
                print("\033[92m✓\033[0m Already on the latest tag; nothing to do.")
                _remind_sync(target, did_install=False)  # flag a stale project even when the tool is current
                return 0
            if ahead:
                print(
                    f"ℹ Installed ngargparser {current} is newer than the latest release {resolved}; not changing it."
                )
                _remind_sync(current, did_install=False)
                return 0
    if check:  # --check with an explicit --ref / --dev / override
        print(f"ℹ Installed: {current}    Target: {resolved}")
        return 0

    print(f"ℹ Upgrading ngargparser ({url}) …")
    rc = _run_self_upgrade(url)
    if rc:
        return rc
    print(f"\033[92m✓\033[0m Upgraded ngargparser → {resolved}. Run 'cli --version' to confirm.")
    _remind_sync(target, did_install=True)
    return 0


# --- Passive "update available" notifier -------------------------------------
# Standard update-notifier pattern (like npm / gh / uv): when a developer runs
# `cli`, occasionally remind them a newer release exists. Throttled to one
# network check per day (cached on disk), printed to stderr, and suppressed in
# non-interactive / CI / opted-out contexts. Never blocks or breaks a command.

UPDATE_CHECK_TTL = 24 * 60 * 60  # seconds between remote checks


def _env_flag(name):
    """True when environment variable `name` is switched on. Unset, empty, and
    the usual "off" spellings (0, false, no, off; any case) count as off, so
    `NAME=0` undoes `NAME=1`."""
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


def _parse_semver(v):
    """Parse 'vX.Y.Z' / 'X.Y.Z' (optional pre-release/build) into (X, Y, Z), else None."""
    import re

    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", (v or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


# A PEP 440 version, in any of the spellings PEP 440 normalizes: an optional epoch,
# a release of any length, then optional pre (a, b, rc), post and dev parts, and a
# +local label that does not count for ordering.
_PEP440_RE = re.compile(
    r"""
    v?(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)
    (?:[-_.]?(?P<pre>alpha|beta|preview|pre|rc|a|b|c)[-_.]?(?P<pre_n>\d*))?
    (?:-(?P<post_implicit>\d+)|[-_.]?(?:post|rev|r)[-_.]?(?P<post_n>\d*))?
    (?:[-_.]?dev[-_.]?(?P<dev_n>\d*))?
    (?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?
    """,
    re.VERBOSE | re.IGNORECASE,
)
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}


def _version_key(v):
    """A sort key for version string `v`, or None when it is not a version.

    PEP 440 ordering, close enough for comparing ngargparser versions without the
    `packaging` library (not a dependency): 0.5 == 0.5.0, 0.4.2.dev1 < 0.4.2a1 <
    0.4.2rc1 < 0.4.2 < 0.4.2.post1 < 0.4.3. A string only the older X.Y.Z parser
    reads (`0.4.2-test`) keeps the order it had there: that of X.Y.Z."""
    text = (v or "").strip()
    m = _PEP440_RE.fullmatch(text)
    if m is None:
        semver = _parse_semver(text)
        m = _PEP440_RE.fullmatch(".".join(str(n) for n in semver)) if semver else None
        if m is None:
            return None
    release = [int(n) for n in m.group("release").split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()  # 0.5 and 0.5.0 are the same version
    # A part that is absent reads as None; one written without a number ("1.0.post") as "".
    post = m.group("post_implicit") if m.group("post_implicit") is not None else m.group("post_n")
    pre, dev = m.group("pre"), m.group("dev_n")
    if pre is not None:
        pre_key = (_PRE_RANK[pre.lower()], int(m.group("pre_n") or 0))
    elif dev is not None and post is None:
        pre_key = (-1, 0)  # X.devN comes before every pre-release of X
    else:
        pre_key = (3, 0)  # no pre-release part: after every pre-release
    post_key = (0, 0) if post is None else (1, int(post or 0))
    dev_key = (1, 0) if dev is None else (0, int(dev or 0))
    return (int(m.group("epoch") or 0), tuple(release), pre_key, post_key, dev_key)


def _installed_is_newer(target):
    """True when the installed ngargparser is a newer version than `target` (a release
    tag such as v0.4.1) in PEP 440 order: 0.4.2rc1 is newer than v0.4.1. False when
    either one is not a version (unknown, master)."""
    installed, latest = _version_key(__version__), _version_key(target)
    return installed is not None and latest is not None and installed > latest


def _installed_is_latest(target):
    """True when the installed ngargparser is the same version as `target` (a release
    tag such as v0.4.1) in PEP 440 order: 0.4.1 is v0.4.1. False when either one is
    not a version (unknown, master)."""
    installed, latest = _version_key(__version__), _version_key(target)
    return installed is not None and latest is not None and installed == latest


def _is_plain_latest(ref, dev):
    """True when the upgrade target is the latest release tag: no --ref, no --dev, no
    local checkout and no NGARGPARSER_UPGRADE_URL. Any of those names a version on purpose."""
    return ref == "latest" and not dev and _local_source(ref) is None and not os.environ.get("NGARGPARSER_UPGRADE_URL")


def _update_cache_file():
    import os

    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "ngargparser", "update-check.json")


def _read_update_cache():
    import json

    try:
        with open(_update_cache_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_update_cache(checked_at, latest):
    import json
    import os

    path = _update_cache_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"checked_at": checked_at, "latest": latest}, f)
    except OSError:
        pass


def _maybe_notify_update(command, cached_only=False):
    """Print a one-line 'update available' notice on stderr, if appropriate.

    Silent (and side-effect-free) unless: stderr is a TTY, not in CI, not opted
    out via NGARGPARSER_NO_UPDATE_CHECK, and not already running `cli upgrade`.
    Re-checks the remote at most once per UPDATE_CHECK_TTL; otherwise reads the
    cached result. With `cached_only` (a help screen), it reads the cache only,
    however old, and never contacts the remote or writes the cache. Any failure is
    swallowed so a command is never affected.
    """
    import os
    import sys
    import time

    try:
        if (
            command in ("upgrade", "u")
            or _env_flag("NGARGPARSER_NO_UPDATE_CHECK")
            or os.environ.get("CI")
            or not sys.stderr.isatty()
        ):
            return

        cache = _read_update_cache()
        cached = cache.get("latest")
        latest = cached if cached and _RELEASE_TAG_RE.fullmatch(cached) else None
        # A pre-release in the cache came from an older cli (they share the file); look it up again.
        fresh = cached == latest and (time.time() - cache.get("checked_at", 0)) < UPDATE_CHECK_TTL
        if not fresh and not cached_only:
            # One bounded network check per TTL. Stamp the time even on failure
            # so we don't re-query (or hang) on every invocation when offline.
            latest = _latest_release_tag(BASE_REPO_URL[len("git+") :], timeout=4) or latest
            _write_update_cache(time.time(), latest)

        cur, new = _version_key(__version__), _version_key(latest)
        if not cur or not new or new <= cur:
            return

        _print_update_notice(latest)
    except Exception:
        pass  # an update check must never break or slow the real command


def _release_notes_url(tag):
    """Web URL of CHANGELOG.md as it stands at `tag`: the release notes for that tag."""
    from urllib.parse import quote

    return f"{BASE_REPO_URL[len('git+') : -len('.git')]}/-/blob/{quote(tag, safe='')}/CHANGELOG.md"


def _print_update_notice(latest, color=True, notes_url=None):
    """Print the 'update available' notice on stderr. `color=False` leaves out
    the ANSI codes, for output that isn't a terminal. `notes_url` adds a
    'release notes' line between the headline and the how-to-update line."""
    import sys

    yellow, reset = ("\033[33m", "\033[0m") if color else ("", "")
    notes = f"   release notes: {notes_url}\n" if notes_url else ""
    print(
        f"\n{yellow}⬆  A new ngargparser is available: {__version__} → {latest}{reset}\n"
        f"{notes}"
        f"   run `cli upgrade` to update   ·   silence: NGARGPARSER_NO_UPDATE_CHECK=1",
        file=sys.stderr,
    )


RELEASE_CHECK_TIMEOUT = 5  # seconds `cli sync --dry-run` waits for the remote before giving up
VERSION_CHECK_TIMEOUT = 4  # seconds `cli --version` waits; a GitLab lookup takes 1.5 to 3.1 s


def _check_latest_release(timeout=VERSION_CHECK_TIMEOUT):
    """Look up the latest release tag right now and report on stderr how the
    installed version compares. Called by `cli --version` on every run.

    Unlike the passive notifier above, this never reads or writes the update
    cache and is not limited to interactive terminals: asking for the version
    is an explicit request, so the answer is always live. Only CI and the
    NGARGPARSER_NO_UPDATE_CHECK opt-out skip it. A remote that can't be reached
    in `timeout` seconds is reported as such, so silence always means "up to
    date". Any failure is swallowed so the command itself never breaks.
    """
    import subprocess
    import sys

    try:
        if _env_flag("NGARGPARSER_NO_UPDATE_CHECK") or os.environ.get("CI"):
            return

        try:
            latest = _latest_release_tag(BASE_REPO_URL[len("git+") :], timeout=timeout, strict=True, prompt=False)
        except _RemoteUnavailable as e:
            why = (
                f"GitLab did not answer within {timeout} s"
                if isinstance(e.__cause__, subprocess.TimeoutExpired)
                else "GitLab unreachable: network/VPN, credentials, or git missing"
            )
            print(f"⚠ Couldn't check for a newer ngargparser ({why}).", file=sys.stderr)
            return

        new = _version_key(latest)
        if not new:
            return  # remote reachable but has no release tags to compare against

        # PEP 440 order, so a pre-release ahead of the latest tag (0.4.2rc1 over
        # v0.4.1) is reported as ahead, not as having an update.
        cur = _version_key(__version__)
        if not cur or new > cur:
            _print_update_notice(latest, color=sys.stderr.isatty(), notes_url=_release_notes_url(latest))
        elif cur > new:
            print(
                f"ℹ Installed ngargparser {__version__} is ahead of the latest release tag {latest}.",
                file=sys.stderr,
            )
    except Exception:
        pass  # a release check must never break `cli --version`


_INVOKED_COMMAND = None  # canonical subcommand once argv is parsed; read by the atexit hook
_HELP_SHOWN = False  # set by -h / --help (see _HelpAction): the notice then reads only its cache


def _notify_at_exit():
    """atexit hook so the notice also appears on early exits (a `--help`, an
    argparse error), where argparse exits before the end of main(). After a help
    screen it uses only the cache: asking for help never waits for GitLab.
    Top-level `--version` cancels it and runs a live check instead (see
    `_ReleaseCheckAction`)."""
    import sys

    command = _INVOKED_COMMAND
    if command is None and len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        command = sys.argv[1]  # parse_args() exited early; best-effort from raw argv
    _maybe_notify_update(command, cached_only=_HELP_SHOWN)


class _ReleaseCheckAction(argparse.Action):
    """Base for the top-level flags that print something and then check the
    latest release tag (see `_check_latest_release`): `--version`. Subclasses say
    what to print in `show`.

    The passive atexit notifier is unregistered first, so these flags never add
    a second, cache-based notice.
    """

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def show(self, parser):
        raise NotImplementedError

    def __call__(self, parser, namespace, values, option_string=None):
        import atexit
        import sys

        atexit.unregister(_notify_at_exit)
        self.show(parser)
        if sys.stdout is not None:  # None: started with stdout closed (cli --version >&-), so nothing to show
            sys.stdout.flush()  # show the output before the remote lookup starts
        _check_latest_release()
        parser.exit()


class _VersionAction(_ReleaseCheckAction):
    """`-v` / `--version`. Stands in for argparse's stock "version" action and
    prints the same `<prog> <version>` line on stdout."""

    def show(self, parser):
        print(f"{parser.prog} {__version__}")


class _HelpAction(argparse.Action):
    """`-h` / `--help` on `cli` and on every subcommand (see _CliParser). Stands in
    for argparse's stock "help" action and prints the same help text. It contacts
    no remote: the update notice at exit then reads only its cache (_HELP_SHOWN)."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        global _HELP_SHOWN
        _HELP_SHOWN = True
        parser.print_help()
        parser.exit()


class _CliParser(argparse.ArgumentParser):
    """The parser of `cli` and, through add_subparsers (which makes each subcommand's
    parser of its own class), of every subcommand: its -h / --help is _HelpAction."""

    def __init__(self, add_help=True, **kwargs):
        super().__init__(add_help=False, **kwargs)
        if add_help:
            self.add_argument("-h", "--help", action=_HelpAction, help="show this help message and exit")


# --- Sync safety -------------------------------------------------------------
# `cli sync` overwrites framework-owned files inside someone's project. Three
# nets keep that from leaving a broken project behind:
#   1. known clashes: project code this framework version is known to break is
#      reported before anything is written;
#   2. a test drive: the project's own help screens are run before and after;
#   3. a snapshot: if a screen that worked before fails after (or sync itself
#      errors out), every touched file is put back exactly as it was.


def _sync_framework_files():
    """Framework-owned files as (source, destination, executable). `cli sync`
    overwrites each destination whose content differs from its source."""
    return [
        (f"{NGPARSER_DIR}/NGArgumentParser.py", "src/core/NGArgumentParser.py", False),
        (f"{NGPARSER_DIR}/core_validators.py", "src/core/core_validators.py", False),
        (f"{NGPARSER_DIR}/result_writer.py", "src/core/result_writer.py", False),
        (f"{NGPARSER_DIR}/app_env.py", "src/core/app_env.py", False),
        (f"{NGPARSER_DIR}/dep_names.py", "src/core/dep_names.py", False),
        (f"{TEMPLATE_DIR}/set_pythonpath.py", "src/core/set_pythonpath.py", False),
        (f"{TEMPLATE_DIR}/configure.py", "src/core/configure.py", True),
        (f"{TEMPLATE_DIR}/configure", "configure", True),
        (f"{TEMPLATE_DIR}/build.sh", "scripts/core/build.sh", True),
        (f"{TEMPLATE_DIR}/Makefile", "Makefile", False),
    ]


# Legacy file names `cli sync` moves to their current location.
_SYNC_LEGACY_BUILD = "scripts/build.sh"
_SYNC_LEGACY_HOOKS = ("scripts/dependencies.sh", "scripts/build_hooks.sh")
_SYNC_LEGACY_BUILD_CONF = "scripts/build.conf"
# Older exclusion files, in the order build.sh checks them. The first one found becomes .distignore.
_SYNC_LEGACY_DISTIGNORE = ("scripts/do-not-distribute.txt", "do-not-distribute.txt", "scripts/.distignore")

# The content hash of each framework file as `cli generate` or `cli sync` last
# wrote it, so sync can tell a local edit from an older framework version.
_FRAMEWORK_HASHES = ".ngargparser/framework-hashes.json"

# Makes src/core/ the package `from core.<module> import ...` reads. `cli generate` writes it
# empty, and `cli sync` creates it, empty, when it is missing; it never changes one that is there.
_CORE_PACKAGE_INIT = "src/core/__init__.py"


# `make build` writes this marker at the top of every build copy, so it ships in the
# tarball and sits in every deployed copy too: neither is a tool's source folder.
_BUILD_MARKER = ".ngargparser-build"


def _tool_markers(root="."):
    """The signs in folder `root` (the current folder by default) that ngargparser
    made it, as labels: a [tool.ngargparser] table in pyproject.toml and
    src/core/NGArgumentParser.py. Every tool has at least one; an unrelated
    project with src/ and scripts/ has none. scripts/core/build.sh is not a
    sign: a folder that has only that and src/ is not a tool."""
    found = []
    try:
        content = _read_pyproject(os.path.join(root, "pyproject.toml"))[0]
    except (OSError, UnicodeDecodeError):
        content = None
    if content is not None and (
        _scaffold_stamp(content)[0] is not None or _ngargparser_section(_parse_pyproject(content)) is not None
    ):
        found.append("a [tool.ngargparser] table in pyproject.toml")
    if os.path.isfile(os.path.join(root, "src/core/NGArgumentParser.py")):
        found.append("src/core/NGArgumentParser.py")
    return found


def _is_ngargparser_tool(root="."):
    """True when folder `root` (the current folder by default) shows a sign that
    ngargparser made it (see _tool_markers)."""
    return bool(_tool_markers(root))


def _not_a_tool_message(command):
    """Why cli `command` does not run in the current folder, which is not a tool: it is
    inside one (a subfolder such as src/), or in none at all."""
    import shlex

    enclosing = _enclosing_tool()
    if enclosing and os.path.exists(os.path.join(enclosing[0], _BUILD_MARKER)):
        # A subfolder of a build copy (build/ng_<name>-local/src) or of a deployed one, where
        # cli refuses to run too: say what the folder is, and where the source is when it holds it.
        copy = enclosing[0]
        source = None
        try:
            home = str(Path.home().resolve())
        except (KeyError, RuntimeError):  # no home folder known
            home = None
        above = copy
        # Up to the home folder or the filesystem root, as _enclosing_tool searches.
        while above != home and above != os.path.dirname(above):
            above = os.path.dirname(above)
            if _is_ngargparser_tool(above) and not os.path.exists(os.path.join(above, _BUILD_MARKER)):
                source = above
                break
        where = f": cd {shlex.quote(source)}" if source else ""
        return (
            f"\033[91m✗\033[0m This folder is inside {copy}, a build or deployed copy of a tool (it has "
            f"{_BUILD_MARKER}), not its source. Nothing was changed. Run cli {command} in the tool's source "
            f"folder{where}, then build again."
        )
    if enclosing:
        tool = enclosing[0]
        return (
            f"\033[91m✗\033[0m This folder is inside the ngargparser tool {tool}: "
            f"run cli {command} there: cd {shlex.quote(tool)}"
        )
    return (
        f"\033[91m✗\033[0m This folder is not an ngargparser tool: cli {command} found no "
        "[tool.ngargparser] table in pyproject.toml and no src/core/NGArgumentParser.py. "
        f"Run cli {command} in a tool's top folder, or cli generate <name> to start one."
    )


def _build_copy_message(command):
    return (
        f"\033[91m✗\033[0m This folder is a build or deployed copy of a tool (it has {_BUILD_MARKER}), "
        f"not its source. Nothing was changed. Run cli {command} in the tool's source folder, then build again."
    )


def _sync_legacy_moves():
    """The legacy files `cli sync` moves, as (legacy, current) pairs in the order
    it moves them. A legacy file stays put when its current name already exists."""
    moves = []
    if os.path.exists(_SYNC_LEGACY_BUILD) and not os.path.exists("scripts/core/build.sh"):
        moves.append((_SYNC_LEGACY_BUILD, "scripts/core/build.sh"))
    hook = next((p for p in _SYNC_LEGACY_HOOKS if os.path.exists(p)), None)
    if hook and not os.path.exists("scripts/hooks.sh"):
        moves.append((hook, "scripts/hooks.sh"))
    if os.path.exists(_SYNC_LEGACY_BUILD_CONF) and not os.path.exists("scripts/core/build.conf"):
        moves.append((_SYNC_LEGACY_BUILD_CONF, "scripts/core/build.conf"))
    exclusion = next((p for p in _SYNC_LEGACY_DISTIGNORE if os.path.exists(p)), None)
    if exclusion and not os.path.exists(".distignore"):
        moves.append((exclusion, ".distignore"))
    return moves


def _file_sha256(path):
    """The hash of a framework file as the hash record keeps it: of its content with each
    CR before an LF left out, so a checkout with CRLF line endings (git's core.autocrlf)
    hashes as the LF file it is in git, and is not taken for a local edit."""
    import hashlib

    with open(path, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()


def _content_sha256(path):
    """The sha256 of the bytes of the file `path` leads to (through a link), or None when
    there is no file there (nothing, a folder, a link that leads nowhere)."""
    import hashlib

    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_path(path):
    """Ask the system to put file or folder `path` on disk now (fsync). Where it cannot (a
    folder on Windows, a file system without it), this does nothing."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _read_framework_hashes(root="."):
    """The recorded framework file hashes as {path: sha256}, or {} when there is no usable record."""
    import json

    try:
        with open(os.path.join(root, _FRAMEWORK_HASHES), encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _framework_hashes_problem(root="."):
    """Why the hash record cannot be used ('does not parse', 'cannot be read'), or None:
    also None when there is no record, which is a tool from before the record existed."""
    import json

    path = os.path.join(root, _FRAMEWORK_HASHES)
    if not os.path.lexists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except ValueError:  # invalid JSON (merge conflict markers, say) or not UTF-8
        return "does not parse"
    except OSError:
        return "cannot be read"
    if not isinstance(record, dict) or not all(isinstance(v, str) for v in record.values()):
        return "does not parse"
    return None


def _framework_hashes_record(framework_files, root="."):
    """{path: hash} of each framework file under `root`, as the hash record keeps it (see _file_sha256)."""
    return {
        dst: _file_sha256(os.path.join(root, dst))
        for _src, dst, _exe in framework_files
        if os.path.isfile(os.path.join(root, dst))
    }


def _write_framework_hashes(framework_files, root="."):
    """Record the hash of each framework file under `root`, unless the record already says so.
    The record is written through a temporary file renamed over it (see _replace_file), so a
    write that fails (a full disk) raises the OSError and leaves the old record whole."""
    import json

    record = _framework_hashes_record(framework_files, root)
    if record == _read_framework_hashes(root):
        return
    path = os.path.join(root, _FRAMEWORK_HASHES)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _replace_file(path, json.dumps(record, indent=2, sort_keys=True) + "\n", writable_only=True)


def _sync_touched_paths(framework_files):
    """Every project file `cli sync` may create, overwrite, move or edit."""
    return [dst for _src, dst, _exe in framework_files] + [
        _CORE_PACKAGE_INIT,
        _SYNC_LEGACY_BUILD,
        *_SYNC_LEGACY_HOOKS,
        "scripts/hooks.sh",
        _SYNC_LEGACY_BUILD_CONF,
        "scripts/core/build.conf",
        *_SYNC_LEGACY_DISTIGNORE,
        ".distignore",
        _FRAMEWORK_HASHES,
        "deploy/install.sh",
        ".gitignore",
        "pyproject.toml",
        "README.md",
    ]


def _framework_changes_pending(framework_files):
    """True when sync would change a framework file, move a legacy one or create
    src/core/__init__.py, i.e. when there is something a test drive could be affected by."""
    if _sync_legacy_moves() or not os.path.exists(_CORE_PACKAGE_INIT):
        return True
    return any(
        not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False) for src, dst, _exe in framework_files
    )


# Each sync keeps the files it may change, as they were before it, in a folder of its own here,
# with a marker beside it, until the sync ends: see _SyncSnapshot and _unfinished_syncs.
_SYNC_IN_PROGRESS = os.path.join(".ngargparser", "sync-in-progress")
# Beside that folder, <folder>.partial/ holds the copy being made (renamed into the folder once
# whole), and <folder>.restore the file the undo is putting back (renamed over the tool's file).
_PARTIAL_SUFFIX = ".partial"
_RESTORE_SUFFIX = ".restore"


class _SyncWriteError(Exception):
    """A file in the tool that sync writes could not be written. The message names the
    file, and the read-only folder when that is the cause:
    "Could not write src/core/result_writer.py: the folder src/core is read-only (Permission denied)."
    """

    def __init__(self, path, error):
        import errno

        self.path = path
        target = os.path.realpath(path) if os.path.islink(path) else path
        folder = os.path.dirname(target)
        while folder and not os.path.isdir(folder):
            folder = os.path.dirname(folder)  # the nearest folder that exists, the one sync writes in
        if os.path.isabs(folder):
            inside = os.path.relpath(folder)
            folder = "" if inside == "." else folder if inside.startswith("..") else inside
        where = f"the folder {folder}" if folder else "the tool folder"
        reason = error.strerror or str(error)
        read_only = error.errno == errno.EROFS or (
            isinstance(error, PermissionError) and not os.access(folder or ".", os.W_OK | os.X_OK)
        )
        super().__init__(
            f"Could not write {path}: " + (f"{where} is read-only ({reason})." if read_only else f"{reason}.")
        )


def _state_path(error, default):
    """The file under .ngargparser/ that OSError `error` names, for _SyncWriteError, else
    `default`: a copy sync could not make there fails on the file it writes, or on the
    tool file it reads (which sync checked it can read before it started)."""
    name = getattr(error, "filename", None)
    if isinstance(name, str) and name.replace(os.sep, "/").startswith(".ngargparser/"):
        return name
    return default


def _host_name():
    """This computer's name, as a sync marker records it, or "" when it cannot be read."""
    import socket

    try:
        return socket.gethostname()
    except OSError:
        return ""


def _boot_id():
    """The id Linux gives this boot of the computer (/proc/sys/kernel/random/boot_id), or None
    where there is none to read (another system)."""
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as f:
            return f.read().strip() or None
    except (OSError, ValueError):
        return None


def _process_start_time(pid):
    """When process `pid` (text) started, in clock ticks since boot, as Linux records it
    (field 22 of /proc/<pid>/stat), or None where that cannot be read (another system, a
    process that is gone, a /proc that hides other users' processes). A pid the system
    gives to a new process gets that process's start time, so the pair tells processes apart."""
    if not pid.isdigit():
        return None
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            stat = f.read()
        # The command name (field 2) is in parentheses and may hold spaces or ")": the fields
        # after the last ")" are 3, 4, ..., so field 22 is the 20th of them.
        return stat[stat.rindex(")") + 1 :].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def _is_cli_sync(pid):
    """Whether process `pid` (text) runs cli sync, as its command line shows it on Linux
    (/proc/<pid>/cmdline names cli, and sync or its alias s): True or False, or None where
    that cannot be read (another system, a process that is gone or hidden)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            words = f.read().decode("utf-8", errors="replace").split("\0")
    except OSError:
        return None
    # The words after the program: python runs cli as /path/to/bin/cli sync, or -m ngargparser.cli s.
    arguments = [word for word in words[1:] if word]
    text = " ".join(arguments)
    return "cli" in text and ("sync" in text or "s" in arguments)


def _sync_still_running(record, pid, parsed=True):
    """Whether the sync that wrote marker `record` (its pid is `pid`, text) still runs: True,
    False, or None when it ran on another host, whose processes cannot be seen from here.

    On this host, a pid that is not running is a sync that ended, and so is one whose
    process started at another time, or before the last reboot, than the marker records:
    the system gave that pid to another process since (pid 1, say, which always runs). A
    marker without those records (an older one, or one from a system without /proc) is
    judged by its pid alone, as build.sh judges its lock. A marker that did not parse
    (`parsed` False, cut short or garbled) records neither: its pid, from its name, counts
    as a running sync only when that process runs cli sync (see _is_cli_sync), as a sync
    that has just made its marker does; else the sync that wrote it ended."""
    host = record.get("host")
    if isinstance(host, str) and host and host != _host_name():
        return None
    if pid == str(os.getpid()) or not _pid_alive(pid):
        return False  # this process has written no marker yet when markers are read
    if not parsed:
        return _is_cli_sync(pid) is not False
    boot, start = record.get("boot_id"), record.get("start_time")
    if isinstance(boot, str) and boot and _boot_id() not in (None, boot):
        return False  # the computer restarted since: no process from before is left
    if isinstance(start, str) and start:
        now = _process_start_time(pid)
        if now is not None and now != start:
            return False
    return True


def _new_sync_in_progress():
    """Make this sync's own pre-sync folder, .ngargparser/sync-in-progress/<UTC time>Z-<pid>/,
    and its marker beside it, <that name>.json, which names the process (its pid, and on
    Linux when it started and the boot it runs in), the host, when the sync started and
    the folder; return (folder, marker).

    The marker is written first, so a sync that is killed at any point after this leaves
    one for the next sync to find (see _unfinished_syncs). The markers of earlier syncs
    that ended (see _sync_still_running) and whose folder was deleted (by hand, after one
    did not finish) are removed here, and so is each folder without a marker: a sync that
    finished removes its marker first (see _SyncSnapshot.discard), so that is one that
    was stopped while it deleted its folder. This runs under the build lock, so no other
    sync is using them."""
    import itertools
    import json

    os.makedirs(_SYNC_IN_PROGRESS, exist_ok=True)
    for pid, _started, folder, marker, record, parsed in _sync_in_progress_records():
        if not _sync_still_running(record, pid, parsed) and not os.path.lexists(folder):
            try:
                os.remove(marker)
            except OSError:
                pass
            # What a kill -9 left beside it: a copy cut short, a file the undo was putting back.
            shutil.rmtree(f"{folder}{_PARTIAL_SUFFIX}", ignore_errors=True)
            try:
                os.remove(f"{folder}{_RESTORE_SUFFIX}")
            except OSError:
                pass
    for name in os.listdir(_SYNC_IN_PROGRESS):
        path = os.path.join(_SYNC_IN_PROGRESS, name)
        if name.startswith(".") and name.endswith(".tmp"):
            # A marker update cut short (see _replace_file): the marker itself is whole.
            leftover = True
        else:
            run = name
            for suffix in (_PARTIAL_SUFFIX, _RESTORE_SUFFIX):
                run = run[: -len(suffix)] if run.endswith(suffix) else run
            leftover = not name.endswith(".json") and not os.path.lexists(
                os.path.join(_SYNC_IN_PROGRESS, f"{run}.json")
            )
        if not leftover:
            continue
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass
    started = datetime.now(timezone.utc)
    name = f"{started:%Y%m%dT%H%M%S.%f}Z-{os.getpid()}"
    for n in itertools.count(1):
        folder = os.path.join(_SYNC_IN_PROGRESS, name if n == 1 else f"{name}-{n}")
        marker = f"{folder}.json"
        if os.path.lexists(folder) or os.path.lexists(marker):
            continue
        # `written` and `created` grow as the sync writes (see _SyncSnapshot.wrote): none yet.
        record = {
            "pid": os.getpid(),
            "host": _host_name(),
            "started": f"{started:%Y-%m-%d %H:%M:%S} UTC",
            "files": folder,
            "written": [],
            "created": [],
        }
        boot, start = _boot_id(), _process_start_time(str(os.getpid()))
        if boot and start:
            record.update(boot_id=boot, start_time=start)
        try:
            with open(marker, "x", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            os.mkdir(folder)
        except BaseException:
            if os.path.lexists(marker):
                os.remove(marker)  # a marker cut short (a full disk), or one without its folder
            raise
        return folder, marker
    raise AssertionError("unreachable")  # itertools.count never ends


def _sync_in_progress_records():
    """[(pid, started, folder, marker, record, parsed)] for each marker under
    .ngargparser/sync-in-progress/; `record` is what it holds, and `parsed` False when it
    does not parse (then `record` is {}). A marker that does not parse names the folder
    beside it, and the pid in its name."""
    import glob
    import json

    found = []
    for marker in sorted(glob.glob(os.path.join(_SYNC_IN_PROGRESS, "*.json"))):
        try:
            with open(marker, encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, ValueError):
            record = None
        parsed = isinstance(record, dict)
        record = record if isinstance(record, dict) else {}
        named = re.search(r"Z-(\d+)(?:-\d+)?\.json$", marker)
        pid = str(record.get("pid") or (named.group(1) if named else ""))
        started = record.get("started") if isinstance(record.get("started"), str) else "at an unknown time"
        folder = record.get("files") if isinstance(record.get("files"), str) else marker[: -len(".json")]
        found.append((pid, started, folder, marker, record, parsed))
    return found


def _build_lock_free():
    """Whether no build or sync holds the build lock now (see _take_build_lock): True, False,
    or None when that cannot be told. It takes nothing and writes nothing, so a dry run can
    ask too, and a build that starts at that moment never finds the lock taken by the
    question (build.sh's flock -n would fail at once): an flock is looked up in the system's
    list of locks (see _flock_held), and a missing lock file is not made. Where that list
    cannot be read (a system without /proc), a lock file whose pid runs is held (each
    holder writes its pid there), and one whose pid does not run cannot be told: a holder
    that may only read the file leaves an older pid in it."""
    if not os.path.lexists(_BUILD_LOCK):
        return True
    if os.path.isdir(_BUILD_LOCK):
        # The folder lock: held while the pid in it runs, and by one that has just made it (no pid yet).
        holder = _lock_holder(os.path.join(_BUILD_LOCK, "pid"))
        return bool(holder) and not _pid_alive(holder)
    try:
        import fcntl
    except ImportError:  # Windows
        fcntl = None  # type: ignore[assignment]
    if fcntl is None or not shutil.which("flock"):
        # Without flock the file is no lock: a build or sync makes the folder lock instead,
        # and takes the file's place only when the pid in it is gone (see _take_build_lock).
        return not _pid_alive(_lock_holder(_BUILD_LOCK))
    held = _flock_held(_BUILD_LOCK)
    if held is not None:
        return not held
    return False if _pid_alive(_lock_holder(_BUILD_LOCK)) else None


def _flock_held(path):
    """Whether a process holds a lock on file `path` now, as Linux lists the locks it holds in
    /proc/locks (an flock, or the POSIX lock a network file system makes of one): True or
    False, or None where that cannot be read. Each line there names the file's device (major
    and minor, in hex) and inode, as in 1: FLOCK  ADVISORY  WRITE 4242 08:30:29327 0 EOF."""
    try:
        info = os.stat(path)
        with open("/proc/locks", encoding="ascii", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    wanted = (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino)
    for line in lines:
        for word in line.split():
            found = re.fullmatch(r"([0-9a-f]+):([0-9a-f]+):([0-9]+)", word)
            if found and (int(found.group(1), 16), int(found.group(2), 16), int(found.group(3))) == wanted:
                return True
    return False


def _unfinished_syncs(lock_held=False):
    """[(pid, started, folder, host, record)] for each earlier cli sync that did not finish: its
    marker is still there, its process is gone (killed with kill -9, a power cut, a restore
    that did not finish; see _sync_still_running), and the folder that holds the files from
    before it is still there. `host` is None for a sync on this host, else the name of the
    host it ran on, where it may still be running: that cannot be seen from here. `record`
    is what the marker holds ({} when it does not parse).

    A sync that runs holds the build lock. So with `lock_held` (this run holds it), or when
    no build or sync holds it now (see _build_lock_free), no sync runs on this host, and
    every marker from this host whose folder is there is one that did not finish, whatever
    its pid shows (a pid the system gave to another process since): the rule make build
    applies (scripts/core/build.sh, unfinished_sync). Not one whose process is the sync that
    wrote it (see _same_sync_process): that sync is still running, so its files are never
    named for deleting.

    The lock is looked at after the markers are listed: a sync writes its marker once it holds
    the lock, so one that took the lock just after a first look found it free is seen holding it
    then. A marker that is gone by then belongs to a sync that finished meanwhile."""
    records = _sync_in_progress_records()
    idle = lock_held or _build_lock_free() is True
    found = []
    for pid, started, folder, marker, record, parsed in records:
        running = _sync_still_running(record, pid, parsed)
        if running is True and idle and not _same_sync_process(record, pid):
            running = False
        if running is not True and os.path.isdir(folder) and os.path.lexists(marker):
            found.append((pid, started, folder, record.get("host") if running is None else None, record))
    return found


def _same_sync_process(record, pid):
    """True when process `pid` (text), which runs, is the one that wrote marker `record`: the
    marker records when it started and in which boot, and both match it now (Linux). A pid
    the system gave to another process since has another start time."""
    boot, start = record.get("boot_id"), record.get("start_time")
    if not (isinstance(boot, str) and boot and isinstance(start, str) and start):
        return False
    return _boot_id() == boot and _process_start_time(pid) == start


def _running_syncs():
    """[(pid, started)] for each cli sync that runs in this tool folder now, on this host: its
    marker names a process that still runs it (see _sync_still_running), and a build or sync
    holds the build lock (see _build_lock_free)."""
    if _build_lock_free() is True:
        return []
    return [
        (pid, started)
        for pid, started, _folder, _marker, record, parsed in _sync_in_progress_records()
        if _sync_still_running(record, pid, parsed) is True
    ]


def _report_unfinished_sync(unfinished):
    """Say that the first of `unfinished` (from _unfinished_syncs) did not finish, where the files
    from before it are, and the commands that finish it by hand (see _restore_by_hand)."""
    pid, started, folder, host, record = unfinished[0]
    restore, commands = _restore_by_hand(folder, record)
    if host:
        # A shared folder seen from another computer: its process cannot be seen from here.
        _print_error(
            f"\033[91m✗\033[0m A cli sync on the host {host} (pid {pid or 'unknown'}, started {started}) "
            "is still running there, or did not finish and may have left framework files half updated. "
            f"The files from before it are in {folder}/. If it is not running on {host}, {restore}"
        )
    else:
        _print_error(
            f"\033[91m✗\033[0m A previous cli sync (pid {pid or 'unknown'}, started {started}) did not "
            "finish, and may have left framework files half updated. The files from before it are in "
            f"{folder}/. {restore[0].upper()}{restore[1:]}"
        )
    for command in commands:
        _print_error(f"    {command}")


def _leftover_temporary_files(framework_files):
    """The temporary files a cli sync that was stopped (kill -9, a power cut) left beside the
    files it writes, sorted: <name>.ngargparser-restore (see _SyncSnapshot._put_back), and
    .<name>.<8 characters>.ngargparser-sync and .<name>.<8 characters>.tmp (see
    _write_atomically and _replace_file), for each file sync writes (see _sync_touched_paths),
    and for the file a link among them leads to. A sync that ends removes its own."""
    names: dict[str, set[str]] = {}  # folder -> the names of the files sync writes in it
    for path in _sync_touched_paths(framework_files):
        places = [path]
        if os.path.islink(path):
            target = os.path.relpath(os.path.realpath(path))
            if not target.startswith(".."):
                places.append(target)
        for place in places:
            folder, name = os.path.split(place)
            names.setdefault(folder, set()).add(name)
    found = []
    for folder, written in names.items():
        try:
            entries = os.listdir(folder or ".")
        except OSError:
            continue
        for entry in entries:
            temporary = re.fullmatch(r"\.(.+)\.[a-z0-9_]{8}\.(?:tmp|ngargparser-sync)", entry)
            if (temporary and temporary.group(1) in written) or (
                entry.endswith(".ngargparser-restore") and entry[: -len(".ngargparser-restore")] in written
            ):
                found.append(os.path.join(folder, entry))
    return sorted(found)


def _report_leftover_temporary_files(framework_files):
    """Name the temporary files a stopped cli sync left (see _leftover_temporary_files), if any."""
    leftover = _leftover_temporary_files(framework_files)
    if leftover:
        one = len(leftover) == 1
        files = "this temporary file" if one else "these temporary files"
        print(
            f"\033[93m⚠\033[0m A cli sync that was stopped left {files}: {', '.join(leftover)}. Sync does not use "
            f"{'it' if one else 'them'}: once you have checked {'it, it is' if one else 'them, they are'} safe to delete."
        )


def _restore_by_hand(folder, record):
    """(what to do, commands) to finish by hand a sync that did not finish, whose files from
    before it are in `folder` and whose marker holds `record`. `what` is a sentence that
    starts in lower case and ends with a colon; the commands run from the tool folder.

    The marker names each file the sync was about to write, before it wrote it (see
    _SyncSnapshot.wrote): `written` for one that was there before the sync, whose copy is in
    `folder`, and `created` for one that was not; and in `left`, once the write was done,
    what the sync left there (its sha256). Each written file that differs from its copy now
    (content or mode) gets one cp that puts the copy back, each created file that is there
    now is deleted, then `folder` is deleted. A file the sync did not write is never named,
    so an edit made to it after the sync stopped stays. Nor is a file that changed again
    after the sync wrote it (it no longer holds what `left` records): the commands leave it
    as it is, and `what` says to compare it with its copy first. A marker that names no
    written files (one that does not parse) gets the copy back of each file in `folder`
    that differs from the one in the tool.

    A copy that was cut short (the sync was killed while it made it) sits in <folder>.partial/,
    never in `folder`: the sync copies every file before it writes any, so that file was not
    written yet, and the commands leave the copy out."""
    import shlex
    import stat

    def paths(key):
        value = record.get(key)
        return [path for path in value if isinstance(path, str)] if isinstance(value, list) else None

    left = record.get("left") if isinstance(record.get("left"), dict) else {}

    def changed_since(path):
        # The sync wrote `path` and it holds something else now: an edit made after the sync stopped.
        return path in left and left[path] != _content_sha256(path)

    partial = f"{folder}{_PARTIAL_SUFFIX}"
    cut = sorted(
        os.path.relpath(os.path.join(root, name), partial) for root, _dirs, files in os.walk(partial) for name in files
    )
    written = paths("written")
    if written is None:
        written = [
            os.path.relpath(os.path.join(root, name), folder)
            for root, _dirs, files in os.walk(folder)
            for name in files
        ]
    differ: list[str] = []  # written files to copy back
    edited: list[str] = []  # written files that changed again since: left as they are
    created: list[str] = []  # created files to delete
    edited_new: list[str] = []  # created files that changed again since: left as they are
    for path in sorted(set(written)):
        saved = os.path.join(folder, path)
        if not os.path.isfile(saved):
            continue  # nothing to put back: no file was there before the sync (a dangling link)
        try:
            same = (
                os.path.isfile(path)
                and filecmp.cmp(saved, path, shallow=False)
                and stat.S_IMODE(os.stat(saved).st_mode) == stat.S_IMODE(os.stat(path).st_mode)
            )
        except OSError:
            same = False
        if not same:
            (edited if changed_since(path) else differ).append(path)
    for path in sorted(set(paths("created") or [])):
        if os.path.lexists(path):
            (edited_new if changed_since(path) else created).append(path)
    if differ:
        what = (
            f"copy back the files it changed (or run git checkout -- {' '.join(shlex.quote(path) for path in differ)} "
            "if they are committed)"
        )
    else:
        what = "none of the files it wrote differs from its copy there, so none needs copying back"
    if created:
        what += f", then delete these files the sync created: {', '.join(created)}. Then delete that folder"
    else:
        what += ", then delete that folder"
    what += " and run cli sync again. From the tool folder:"
    # A folder the commands write in that is read-only for this user (why an undo could not
    # put the files back, say): named first, or the commands fail as the undo did.
    folders = dict.fromkeys(os.path.dirname(path) or "." for path in differ + created)
    blocked = [folder for folder in folders if os.path.isdir(folder) and not os.access(folder, os.W_OK | os.X_OK)]
    if blocked:
        one = len(blocked) == 1
        what = (
            f"make {' and '.join(blocked)} writable ({'it is' if one else 'they are'} read-only for you: chmod u+w "
            f"{' '.join(shlex.quote(folder) for folder in blocked)}, or ask {'its' if one else 'their'} owner), "
            f"then {what}"
        )
    if edited or edited_new:
        kept = sorted(edited + edited_new)
        one = len(kept) == 1
        # Only a file that was there before the sync has a copy to compare with.
        which = ", ".join(edited) if edited_new else "it" if one else "them"
        compare = (
            f": compare {which} with {'its copy' if len(edited) == 1 else 'their copies'} in that folder before "
            "you delete it"
            if edited
            else ""
        )
        what = (
            f"the sync wrote {', '.join(kept)}, and {'it' if one else 'they'} changed since, so the commands leave "
            f"{'it as it is' if one else 'them as they are'}{compare}. {what[0].upper()}{what[1:]}"
        )
    if cut:
        what = (
            f"it stopped while it copied {', '.join(cut)}, before it wrote any file, so that copy is incomplete "
            f"and is left out. {what[0].upper()}{what[1:]}"
        )
    commands = [f"cp -a {shlex.quote(os.path.join(folder, path))} {shlex.quote(path)}" for path in differ]
    if created:
        commands.append(f"rm {' '.join(shlex.quote(path) for path in created)}")
    commands.append(f"rm -r {shlex.quote(folder)}" + (f" {shlex.quote(partial)}" if os.path.lexists(partial) else ""))
    return what, commands


class _RestoreIncomplete(Exception):
    """The undo put back every file it could, and not these: `failed` is [(path, OSError)]."""

    def __init__(self, failed):
        super().__init__(f"{len(failed)} file(s) could not be put back")
        self.failed = failed


def _restore_failures(failed):
    """One sentence per reason for [(path, OSError)] the undo could not put back, naming the
    folder that is read-only when that is the cause (see _SyncWriteError): "Could not put back
    src/core/a.py, src/core/b.py: the folder src/core is read-only (Permission denied)." """
    reasons: dict[str, list[str]] = {}
    for path, error in failed:
        why = str(_SyncWriteError(path, error)).split(": ", 1)[1]
        reasons.setdefault(why, []).append(path)
    return " ".join(f"Could not put back {', '.join(paths)}: {why}" for why, paths in reasons.items())


class _SyncSnapshot:
    """Pre-image of the files `cli sync` may touch, so a sync can be undone exactly.

    Copies each existing file into this run's own folder under
    .ngargparser/sync-in-progress/ (see _new_sync_in_progress), under the same relative
    path, and remembers which paths (and which directories) did not exist. The folder,
    and the marker beside it, stay until the sync ends, so one that is killed is found
    by the next (see _unfinished_syncs). Sync writes *through* a symlink that stays
    inside the tool (one that leads outside is refused first, see _links_outside_tool),
    so for a symlinked path the content of the file it points at is what gets saved,
    along with the link itself.

    Each copy is made in <folder>.partial/ and renamed into the folder once it is whole
    and on disk (fsync), so the folder never holds a copy cut short (see _restore_by_hand).

    `wrote(path)` notes a path the run is about to write (create, replace, move or
    change the mode of), and `written(path)` one it has written, with what it left
    there. `restore()` puts back each written path whose state differs from its
    pre-image, and only those: a path the run never wrote, or a write that failed
    before it changed anything, needs nothing put back. A written file that changed
    again after the sync wrote it (an edit made while the help screens ran) is moved
    aside first, never overwritten (see _move_aside). It deletes files and empty
    directories the sync created, and removes that sync's backup directory. `discard()`
    drops the pre-image and its marker.
    """

    _DIRS = (
        "src/core",
        "scripts",
        "scripts/core",
        "deploy",
        ".ngargparser",
        ".ngargparser/sync-backups",
        _SYNC_IN_PROGRESS,
    )

    def __init__(self, paths):
        import stat

        self._absent_dirs = [d for d in self._DIRS if not os.path.isdir(d)]
        store, marker = _new_sync_in_progress()
        self._store: Optional[str] = store
        self._marker = marker
        self._partial = f"{store}{_PARTIAL_SUFFIX}"
        self._restore_temporary = f"{store}{_RESTORE_SUFFIX}"
        self._written: set[str] = set()
        self._created: list[str] = []  # as the marker records them (see wrote)
        self._recorded: list[str] = []  # the marker's `written`, likewise
        # project path -> (it exists, sha256 of the file's content or None): what the sync left there
        self._left: dict[str, tuple[bool, Optional[str]]] = {}
        self._aside: Optional[str] = None  # where restore() moves files edited during the sync
        self.moved_aside: list[tuple[str, str]] = []  # (path, where restore() moved it)
        # project path -> (existed, link target or None, stored copy of its content or None, mode or None)
        self._entries = {}
        try:
            for path in dict.fromkeys(paths):
                link = os.readlink(path) if os.path.islink(path) else None
                stored, mode = None, None
                if os.path.isfile(path):  # follows a symlink to the file sync would write to
                    stored = os.path.join(store, path)
                    os.makedirs(os.path.dirname(stored), exist_ok=True)
                    # Made whole and on disk before it takes its name: a kill -9 or a power cut
                    # while it is copied leaves no copy cut short in the folder.
                    partial = os.path.join(self._partial, path)
                    os.makedirs(os.path.dirname(partial), exist_ok=True)
                    shutil.copy2(path, partial)
                    _fsync_path(partial)
                    os.replace(partial, stored)
                    mode = stat.S_IMODE(os.stat(path).st_mode)
                self._entries[path] = (os.path.lexists(path), link, stored, mode)
            for folder in [os.path.dirname(store), *(folder for folder, _dirs, _files in os.walk(store))]:
                _fsync_path(folder)  # the new names, before any file they keep is replaced
            shutil.rmtree(self._partial, ignore_errors=True)
        except BaseException:
            self.discard()  # don't leave a half-built pre-image behind
            raise

    def _update_marker(self, **fields):
        """Set `fields` in this run's marker, written whole through a temporary file (see
        _replace_file). An OSError (a full disk, a read-only .ngargparser/) is raised."""
        import json

        with open(self._marker, encoding="utf-8") as f:
            record = json.load(f)
        record.update(fields)
        _replace_file(self._marker, json.dumps(record) + "\n")

    def wrote(self, *paths):
        """Note that this run is about to write each of `paths` (call it only for a write that
        is made: a file that changes). Each one is recorded in the marker before it is written:
        as `written` when it was there before the sync, else as `created`. A sync killed after
        that (kill -9) leaves files the next sync can name: each written one to copy back from
        the pre-sync folder, each created one to delete (see _restore_by_hand); a file it did
        not write is never named, so an edit made to it after the kill stays. A marker that
        cannot record it raises _SyncWriteError before the file is written: a write the next
        sync would not know of is not made."""
        self._written.update(paths)
        noted = sorted(path for path in self._written if path in self._entries)
        created = [path for path in noted if not self._entries[path][0]]
        written = [path for path in noted if self._entries[path][0]]
        if (created, written) != (self._created, self._recorded) and self._store is not None:
            try:
                self._update_marker(written=written, created=created)
            except (OSError, ValueError, TypeError) as e:
                if isinstance(e, OSError):
                    raise _SyncWriteError(self._marker, e) from e
                raise _SyncWriteError(self._marker, OSError(0, f"it does not parse ({e})")) from e
            self._created, self._recorded = created, written

    def written(self, *paths):
        """Note that this run has written each of `paths`, and what it left there: only a
        path noted here is put back by restore(). Called once the write is done, so a write
        that failed before it changed the file, or one that found nothing to change, leaves
        the path as the user has it. The marker records it too, as `left` (the sha256 of
        the file there, or None): a file that holds something else when the next sync finds
        the marker was changed after the sync wrote it, and is not put back (see
        _restore_by_hand)."""
        for path in paths:
            self._left[path] = (os.path.lexists(path), _content_sha256(path))
        left = {path: digest for path, (_there, digest) in sorted(self._left.items()) if path in self._entries}
        if self._store is not None:
            try:
                self._update_marker(left=left)
            except (OSError, ValueError, TypeError):
                pass  # the marker keeps `written`: the next sync then copies the file back if it differs

    @staticmethod
    def _same(stored, path, mode):
        import stat

        return filecmp.cmp(stored, path, shallow=False) and stat.S_IMODE(os.stat(path).st_mode) == mode

    def _unchanged(self, path):
        """True when `path` is as it was before the sync: content, mode, and link."""
        existed, link, stored, mode = self._entries[path]
        if not existed:
            return not os.path.lexists(path)
        if link is not None:
            if not os.path.islink(path) or os.readlink(path) != link:
                return False
            target = os.path.realpath(path)
            if stored is None:
                return not os.path.isfile(target)
            return os.path.isfile(target) and self._same(stored, target, mode)
        if stored is not None:
            return not os.path.islink(path) and os.path.isfile(path) and self._same(stored, path, mode)
        return True  # a directory or special file sat at this path; the snapshot leaves it alone

    def changed(self):
        """The paths this run wrote that are not as they were before it."""
        return [path for path in self._entries if path in self._left and not self._unchanged(path)]

    @staticmethod
    def _put_back(stored, destination, temporary=None):
        """Copy `stored` over `destination` without a moment where it is missing, through
        `temporary` (this run's <folder>.restore under .ngargparser/, which a kill -9 leaves
        out of sight, and the next sync deletes). Where `destination` is on another file
        system, or no `temporary` is given, the copy is made beside it.

        On one file system the copy is a second name for `stored` (a hard link), which
        takes no space, so an undo on a full disk still puts the file back; it is copied
        where a link cannot be made."""
        import errno

        def place(temporary):
            try:
                os.link(stored, temporary)
            except OSError:
                shutil.copy2(stored, temporary)
            os.replace(temporary, destination)

        os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
        beside = destination + ".ngargparser-restore"
        temporary = temporary or beside
        try:
            try:
                place(temporary)
            except OSError as e:
                if e.errno != errno.EXDEV or temporary == beside:
                    raise
                if os.path.lexists(temporary):
                    os.remove(temporary)
                temporary = beside
                place(temporary)
        finally:
            if os.path.lexists(temporary):
                os.remove(temporary)

    def _aside_folder(self):
        """This undo's .ngargparser/edited-during-sync-<UTC time>/, made the first time it is
        needed; an existing folder of that name gets a -2, -3, ... suffix instead."""
        if self._aside is None:
            name = os.path.join(".ngargparser", f"edited-during-sync-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
            folder, n = name, 1
            while os.path.lexists(folder):
                n += 1
                folder = f"{name}-{n}"
            os.makedirs(folder)
            self._aside = folder
        return self._aside

    def _move_aside(self, path):
        """Move the file at written `path` (the file it leads to, for a link) to this undo's
        edited-during-sync folder, under the same path, and return where it went; None when
        there is no file there (it was deleted). Renamed, or copied where the folder is on
        another file system: the undo then puts the file from before the sync over it."""
        source = os.path.realpath(path) if os.path.islink(path) else path
        if not os.path.isfile(source):
            return None
        destination = os.path.join(self._aside_folder(), path)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        try:
            os.rename(source, destination)
        except OSError:
            shutil.copy2(source, destination)
        return destination

    def restore(self, backup_root=None):
        """Put back each path this run wrote (see changed), and return [(path, where it went)]
        for each one that changed again after the sync wrote it: that version is moved aside
        first (see _move_aside), never overwritten. The list is kept as `moved_aside` too,
        so an undo that stops partway can still name them. A path that cannot be put back (a
        read-only folder) does not stop the rest: each one is, and then _RestoreIncomplete
        names those that were not, and this run's backups stay."""
        moved = self.moved_aside
        failed: list[tuple[str, OSError]] = []
        for path in self.changed():
            try:
                self._restore_one(path, moved)
            except OSError as e:
                failed.append((path, e))
        if failed:
            raise _RestoreIncomplete(failed)
        self.clean_up(backup_root)
        return moved

    def _restore_one(self, path, moved):
        """Put back the written `path` (see restore), noting in `moved` where a version of it
        that changed after the sync wrote it went."""
        existed, link, stored, _mode = self._entries[path]
        if self._left[path] != (os.path.lexists(path), _content_sha256(path)):
            destination = self._move_aside(path)
            if destination:
                moved.append((path, destination))
        if not existed:
            if os.path.lexists(path):
                os.remove(path)  # the sync created it
        elif link is not None:
            if not os.path.islink(path) or os.readlink(path) != link:
                if os.path.lexists(path):
                    os.remove(path)
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                os.symlink(link, path)
            target = os.path.realpath(path)
            if stored is not None:
                # the content sync overwrote through the link
                self._put_back(stored, target, self._restore_temporary)
            elif os.path.isfile(target):
                os.remove(target)  # the link dangled before; the sync created its target
        elif stored is not None:
            if os.path.islink(path):
                os.remove(path)
            self._put_back(stored, path, self._restore_temporary)

    def record(self):
        """What this run's marker holds ({} when it cannot be read): see _restore_by_hand."""
        import json

        try:
            with open(self._marker, encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, ValueError):
            return {}
        return record if isinstance(record, dict) else {}

    def clean_up(self, backup_root=None):
        """Remove this run's backup folder and each directory the sync made that is empty now."""
        if backup_root and os.path.isdir(backup_root):
            shutil.rmtree(backup_root)  # this run's own folder (see _new_backup_root)
        self.remove_new_empty_dirs()

    def remove_new_empty_dirs(self):
        """Remove each directory that did not exist before the sync and is empty now."""
        for directory in sorted(self._absent_dirs, key=len, reverse=True):  # deepest first
            try:
                os.rmdir(directory)  # only removes it when the sync left it empty
            except OSError:
                pass

    def discard(self):
        """Delete the pre-image and its marker: the sync stands, or was undone in full.

        The marker goes first, in one step: from then on the run is finished, and a kill
        while the copies are deleted leaves a folder without a marker, which the next sync
        deletes without a word (see _new_sync_in_progress). Deleted the other way round, a
        kill partway left a marker whose folder held only some of the copies, and the
        commands the next sync gave put back only those."""
        if self._store:
            store, self._store = self._store, None
            try:
                os.remove(self._marker)
            except OSError:
                pass
            shutil.rmtree(store, ignore_errors=True)
            shutil.rmtree(self._partial, ignore_errors=True)
            try:
                os.rmdir(_SYNC_IN_PROGRESS)  # only when no other marker is left in it
            except OSError:
                pass

    def keep(self):
        """Keep the pre-image (used when a restore failed) and return where it is.
        Files sit under their project-relative paths, so they can be copied back by hand.

        It is renamed to .ngargparser/failed-restore-<UTC time>/, and a marker that names
        that folder stays (.ngargparser/sync-in-progress/failed-restore-<UTC time>.json), so
        the next cli sync, and make build, stop until the folder is deleted (see
        _unfinished_syncs). That marker is written before the folder moves, and the old one
        is removed after: at every moment one of them names the folder where the files are,
        so a kill between the two steps still leaves the files found. When the folder
        cannot be moved, it stays where it is, and so does the marker that names it."""
        import json

        store, self._store = self._store, None
        if store is None:
            return store
        name = os.path.join(".ngargparser", f"failed-restore-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
        destination, n = name, 1
        while os.path.lexists(destination) or os.path.lexists(_kept_marker(destination)):
            n += 1
            destination = f"{name}-{n}"
        kept = _kept_marker(destination)
        try:
            with open(self._marker, encoding="utf-8") as f:
                record = json.load(f)
            record["files"] = destination
            _replace_file(kept, json.dumps(record) + "\n")  # names the folder before it is there
        except (OSError, ValueError, TypeError):
            return store  # the marker names the folder where it is
        try:
            os.rename(store, destination)
        except OSError:
            try:
                os.remove(kept)
            except OSError:
                pass  # a marker whose folder is not there: the next sync removes it
            return store
        try:
            os.remove(self._marker)  # it names the folder where the files were
        except OSError:
            pass  # likewise removed by the next sync
        self._marker = kept
        return destination


def _kept_marker(folder):
    """The marker that names `folder`, a .ngargparser/failed-restore-<UTC time>/ a restore that
    did not finish keeps (see _SyncSnapshot.keep): <its name>.json in .ngargparser/sync-in-progress/."""
    return os.path.join(_SYNC_IN_PROGRESS, f"{os.path.basename(folder)}.json")


def _new_backup_root():
    """Create this sync's own backup folder and return its path.

    .ngargparser/sync-backups/<UTC time to the microsecond>Z-<process id>, created
    here, so no two runs share one: a rollback deletes its own folder, never the
    backups of an earlier sync that started in the same second. An existing folder
    of that name is never reused; the name gets a -2, -3, ... suffix instead.
    """
    import itertools

    parent = os.path.join(".ngargparser", "sync-backups")
    os.makedirs(parent, exist_ok=True)
    name = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S.%f}Z-{os.getpid()}"
    for n in itertools.count(1):
        candidate = os.path.join(parent, name if n == 1 else f"{name}-{n}")
        try:
            os.mkdir(candidate)
            return candidate
        except FileExistsError:
            continue
    raise AssertionError("unreachable")  # itertools.count never ends


# The lock `make build` holds (scripts/core/build.sh, take_build_lock). A real sync holds it too,
# so a sync and a build, or two syncs, never run at once in one tool folder.
_BUILD_LOCK = os.path.join(".ngargparser", "build.lock")


class _LockBusy(Exception):
    """A build or another sync holds the build lock; `holder` is its pid, or "" when not known.
    `owner_unknown`: a lock folder that holds no pid, which a build or sync that has just made
    it holds, or one killed before it wrote its pid left (see _lock_busy_message)."""

    def __init__(self, holder, owner_unknown=False):
        super().__init__(holder)
        self.holder = holder
        self.owner_unknown = owner_unknown


class _LockUnusable(Exception):
    """.ngargparser/build.lock (or `lock`, such as the paths.py lock) cannot be opened (or
    created) to take the lock. The message says why, that nothing was changed, and who can
    fix it."""

    def __init__(self, error, created, lock=None):
        lock = lock or _BUILD_LOCK
        reason = error.strerror or str(error)
        folder = os.path.dirname(lock)
        if created and os.path.lexists(folder) and not os.path.isdir(folder):
            # A file where the state folder goes: no chmod makes a folder of it.
            message = (
                f"Cannot create {lock}: {folder} is a file, not a folder. Move it away (mv {folder} {folder}.old), "
                "then run the command again. Nothing was changed."
            )
        elif created:
            # No lock file yet, and the folder it goes in is not writable for this user: writable
            # for its owner, or for a group that shares the tool, never for everyone.
            try:
                mine = os.name == "nt" or os.stat(folder).st_uid == os.getuid()
            except (OSError, AttributeError):
                mine = True
            if mine:
                fix = f"{folder} is read-only for you; run chmod u+rwx {folder}"
            else:
                fix = f"ask the owner of {folder} to run chmod g+rwx {folder}, which lets a group you share write there"
            message = f"Cannot create {lock} ({reason}): {fix}. Nothing was changed."
        else:
            message = f"Cannot open {lock} ({reason}): ask its owner to run chmod a+rw {lock}. Nothing was changed."
        super().__init__(message)


class _LockLeftOver(_LockUnusable):
    """A build or sync that is no longer running left .ngargparser/build.lock (a lock file,
    where flock is missing, or a lock folder), and this sync cannot remove it to take its
    place: build.sh's lock_left_over, in this sync's words."""

    def __init__(self, holder, error):
        pid = f" (pid {holder})" if holder.isdigit() else ""
        Exception.__init__(
            self,
            f"A build or a cli sync that is no longer running{pid} left {_BUILD_LOCK}, and this sync could not "
            f"remove it ({error.strerror or error}). Remove {_BUILD_LOCK} (or ask its owner to), then run cli sync "
            "again. Nothing was changed.",
        )


class _StateFolderUnwritable(_LockUnusable):
    """.ngargparser/, where the build lock goes, does not exist and cannot be made: the tool
    folder is read-only for this user."""

    def __init__(self, error):
        state = os.path.dirname(_BUILD_LOCK)
        Exception.__init__(
            self,
            f"Cannot create {state} ({error.strerror or error}), where cli sync takes the build lock: this tool "
            "folder is read-only for you. Ask its owner to make it writable (chmod g+w . for a group that shares "
            "it), then run cli sync again. Nothing was changed.",
        )


def _pid_alive(pid):
    """True when process `pid` (text) is running, someone else's included, as build.sh's
    pid_alive sees it. Where that cannot be checked (Windows), a pid counts as running."""
    if not pid.isdigit():
        return False
    if os.name == "nt":
        return True  # os.kill would stop the process there, not test it
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True  # another user's process
    except (OSError, OverflowError):
        return False
    return True


def _lock_holder(path):
    """The first line of `path`, the pid a build or sync wrote there, or ""."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readline().strip()
    except OSError:
        return ""


def _folder_lock_holder(path):
    """Who holds the lock at `path` when the folder lock is in use: the pid in the lock folder
    ("" when it holds none), or for a lock file, the file a build with flock leaves behind, the
    pid in it ("none" when it is empty, which is never in use)."""
    if os.path.isdir(path):
        return _lock_holder(os.path.join(path, "pid"))
    return _lock_holder(path) or "none"


# Seconds a lock that holds no pid yet is given to show one: a build or sync writes its pid
# right after it makes the lock, so one found in between is named, not "its pid is not known".
_LOCK_PID_WAIT = 1.0


def _settled_lock_holder(path):
    """_folder_lock_holder(path), once the lock at `path` holds a pid: one without a pid ("" for
    a folder, "none" for a file) is read again every 0.05 s for up to _LOCK_PID_WAIT seconds, as
    the run that has just made it writes its pid next. Returns what it holds then (still "" or
    "none" when no pid came), or None when the lock is gone meanwhile."""
    import time

    deadline = time.monotonic() + _LOCK_PID_WAIT
    while True:
        if not os.path.lexists(path):
            return None
        holder = _folder_lock_holder(path)
        if holder not in ("", "none") or time.monotonic() >= deadline:
            return holder
        time.sleep(0.05)


# The folder a run holds while it takes a stale build lock over (see _take_over_stale_lock),
# and the seconds after which one is taken for a run killed while it held it: a minute, which
# a shell can check too (find -mmin +1).
_LOCK_TAKEOVER = f"{_BUILD_LOCK}.takeover"
_LOCK_TAKEOVER_STALE = 60


def _take_over_stale_lock(holder):
    """Take away .ngargparser/build.lock, which `holder` left (a pid no longer running, or
    "none"), so this run can make its own; the mkdir that follows decides which run goes on
    (the other finds that lock busy, see _settled_lock_holder).

    Two runs that both found the lock stale take turns: each first makes the folder
    _LOCK_TAKEOVER, and only the one that made it looks again and takes the lock away, and
    only while it is still the one `holder` left. A run that finds the folder there waits
    up to 2 s for it to go, then tries its mkdir; one more than _LOCK_TAKEOVER_STALE seconds
    old was left by a run killed while it held it, and is removed (and this run makes it
    again to take the lock over). So no run takes away a lock another run has just
    made (a check and then a delete let both runs delete, and both went on). The lock goes
    with one rename, so it is never seen half deleted. Raises _LockLeftOver when it cannot
    be taken away (a read-only .ngargparser/), as build.sh's lock_left_over."""
    import tempfile
    import time

    left_by = "" if holder == "none" else holder
    try:
        os.mkdir(_LOCK_TAKEOVER)
    except FileExistsError:
        # Another run is taking it over: wait for it, then the mkdir decides. A folder older
        # than _LOCK_TAKEOVER_STALE was left by a run killed while it held it: removed, and
        # this run makes it again and takes the stale lock over, as build.sh does.
        deadline = time.monotonic() + 2 * _LOCK_PID_WAIT
        while time.monotonic() < deadline:
            try:
                left_over = time.time() - os.stat(_LOCK_TAKEOVER).st_mtime > _LOCK_TAKEOVER_STALE
            except OSError:  # gone: that run is done
                return
            if left_over:
                try:
                    os.rmdir(_LOCK_TAKEOVER)
                except FileNotFoundError:
                    pass  # another run removed it first
                except OSError as e:
                    raise _LockLeftOver(left_by, e) from None
                return _take_over_stale_lock(holder)
            time.sleep(0.05)
        return
    except OSError as e:
        raise _LockLeftOver(left_by, e) from None
    try:
        if not os.path.lexists(_BUILD_LOCK) or _folder_lock_holder(_BUILD_LOCK) != holder:
            return  # taken over already, and maybe made again: the mkdir decides
        try:
            aside = tempfile.mkdtemp(prefix="build.lock.stale-", dir=os.path.dirname(_BUILD_LOCK))
            try:
                os.rename(_BUILD_LOCK, os.path.join(aside, "build.lock"))
            finally:
                shutil.rmtree(aside, ignore_errors=True)
        except FileNotFoundError:
            pass  # removed by hand meanwhile: the mkdir decides
        except OSError as e:
            raise _LockLeftOver(left_by, e) from None
    finally:
        try:
            os.rmdir(_LOCK_TAKEOVER)
        except OSError:
            pass


def _take_build_lock():
    """Take the build lock the way scripts/core/build.sh takes it, and return the
    function that gives it back. Raises _LockBusy when a build or another sync has it.

    Where the build uses flock (the flock command is on PATH and no lock folder is
    there), this holds an flock on .ngargparser/build.lock and writes its pid into
    it; the lock ends with the process. A file system that cannot lock, or a system
    without flock, gets the folder lock instead: .ngargparser/build.lock made as a
    folder holding the pid, taken over when that process is gone. Either way a
    build and a sync see each other's lock, and the second one stops.

    The lock file is opened for reading and writing, as build.sh opens it (an NFS
    lock needs that), else read-only: flock needs no write access, so one that
    another user's build made (mode 444 in a shared, group-writable folder) still
    locks. A new one is made readable and writable for everyone, so the next user's
    build or sync can write its pid there. A lock file that cannot be opened at all
    raises _LockUnusable."""
    import errno

    made_state = not os.path.lexists(".ngargparser")
    try:
        os.makedirs(".ngargparser", exist_ok=True)
    except OSError as e:
        if isinstance(e, PermissionError) or e.errno == errno.EROFS:
            raise _StateFolderUnwritable(e) from None
        if isinstance(e, FileExistsError):  # .ngargparser is a file: said, with what to do
            raise _LockUnusable(e, created=True) from None
        raise
    try:
        import fcntl
    except ImportError:  # Windows
        fcntl = None  # type: ignore[assignment]
    if fcntl is not None and shutil.which("flock") and not os.path.isdir(_BUILD_LOCK):
        created = False
        try:
            try:
                fd = os.open(_BUILD_LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o666)
                created = True
            except FileExistsError:
                try:
                    fd = os.open(_BUILD_LOCK, os.O_RDWR)
                except PermissionError:
                    fd = os.open(_BUILD_LOCK, os.O_RDONLY)  # a lock file only its owner may write
        except PermissionError as e:
            raise _LockUnusable(e, created=not os.path.lexists(_BUILD_LOCK)) from None
        if created:
            try:
                os.chmod(_BUILD_LOCK, 0o666)  # a+rw, whatever the umask took away
            except OSError:
                pass
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            holder = _lock_holder(_BUILD_LOCK)
            # A holder that could not write its pid (a lock file it may only read) leaves
            # the pid of an earlier one, which is not running: no pid is named then.
            raise _LockBusy(holder if _pid_alive(holder) else "") from None
        except OSError:
            # The file system cannot lock (some network file systems): the folder lock
            # takes its place, as in build.sh.
            os.close(fd)
            try:
                os.remove(_BUILD_LOCK)
            except OSError:
                pass
        else:
            try:
                # The pid, for the message of a build or sync that finds the lock held. A lock
                # file this user may only read keeps the pid it holds.
                with open(_BUILD_LOCK, "w", encoding="utf-8") as f:
                    f.write(f"{os.getpid()}\n")
            except OSError:
                pass
            return lambda: os.close(fd)  # the file stays, as a build leaves it
    for attempt in (1, 2):
        try:
            os.mkdir(_BUILD_LOCK)
        except FileExistsError:
            holder = _settled_lock_holder(_BUILD_LOCK)
            if holder is None:
                continue  # the lock went while this run waited for its pid: make it again
            # A pid that is no longer running: that build or sync was killed. An empty
            # pid after the wait is one killed before it wrote its pid, or one that is slow to.
            if attempt == 1 and holder and not _pid_alive(holder):
                _take_over_stale_lock(holder)
                continue
            if not holder:
                raise _LockBusy("", owner_unknown=True) from None
            raise _LockBusy("" if holder == "none" else holder) from None
        except PermissionError as e:
            # .ngargparser/ is not writable for this user: say who can fix it, in one line.
            raise _LockUnusable(e, created=True) from None
        with open(os.path.join(_BUILD_LOCK, "pid"), "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()}\n")

        def release():
            if _lock_holder(os.path.join(_BUILD_LOCK, "pid")) == str(os.getpid()):
                shutil.rmtree(_BUILD_LOCK, ignore_errors=True)
            if made_state:
                try:
                    os.rmdir(".ngargparser")  # only when this run left it empty
                except OSError:
                    pass

        return release
    # The lock went and came back on both tries: another build or sync runs (build.sh's lock_busy "").
    raise _LockBusy("")


def _lock_busy_message(holder, owner_unknown=False):
    if owner_unknown:
        # build.sh's lock_left_over for a lock folder without a pid, in this sync's words.
        return (
            "\033[91m✗\033[0m Another build or a cli sync may be running in this folder (its pid is not known). "
            f"Nothing was changed. Wait for it to finish, or remove {_BUILD_LOCK} if none is running, then run "
            "cli sync again."
        )
    pid = f" (pid {holder})" if holder.isdigit() else ""
    return (
        f"\033[91m✗\033[0m A build or another sync is running in this folder{pid}. Nothing was changed. "
        "Wait for it, then run cli sync again."
    )


class _SyncStopSignal(KeyboardInterrupt):
    """SIGTERM, SIGHUP or SIGQUIT during `cli sync` (a `kill`, a closed terminal, a dropped
    ssh session, Ctrl-\\), raised like Ctrl-C so the sync is undone the same way."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


# The signals that stop a sync: Ctrl-C, a `kill`, a closed terminal or dropped ssh session, Ctrl-\.
_STOP_SIGNAL_NAMES = ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")


def _stop_signals():
    """The stop signals this system has (Windows has no SIGHUP or SIGQUIT)."""
    import signal

    return [getattr(signal, name) for name in _STOP_SIGNAL_NAMES if hasattr(signal, name)]


# While sync holds its stop signals to the end of the run (see _hold_stop_signals): "mask", the
# signal mask from before, and "arrived", each stop signal its handler saw meanwhile.
_STOP_HOLD: dict[str, Any] = {}


def _hold_stop_signals():
    """Hold the stop signals back from here to the end of the sync: once its outcome is decided
    (it stands, or its undo ran) and while it cleans up, a stop signal must not undo a finished
    sync or end the run with a traceback. One that arrives meanwhile is delivered once the run
    is over (see _end_stop_signal_hold). Holding twice is holding once."""
    import signal

    _STOP_HOLD.setdefault("arrived", [])  # first: from here on the handler of _catch_stop_signals only notes one
    if "mask" in _STOP_HOLD:
        return
    _STOP_HOLD["mask"] = None
    sigmask = getattr(signal, "pthread_sigmask", None)
    if sigmask is not None:
        try:
            _STOP_HOLD["mask"] = sigmask(signal.SIG_BLOCK, set(_stop_signals()))
        except (OSError, ValueError):  # not the main thread
            pass


def _end_stop_signal_hold():
    """End the hold of _hold_stop_signals and return the stop signal that arrived during it,
    or None. That signal is taken out of the way, so it neither runs a handler nor stops the
    process: the caller says it was stopped, and exits as the signal asks."""
    import signal

    if not _STOP_HOLD:
        return None
    arrived = list(_STOP_HOLD.get("arrived", []))
    mask = _STOP_HOLD.get("mask")
    if mask is not None:
        try:
            pending = signal.sigpending()
            for signum in _stop_signals():
                if signum in pending:
                    signal.sigwait({signum})  # held, so this takes it at once
                    arrived.append(signum)
        except (OSError, ValueError, AttributeError):  # no sigpending or sigwait here
            pass
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        except (OSError, ValueError):
            pass
    _STOP_HOLD.clear()
    return arrived[0] if arrived else None


def _catch_stop_signals():
    """Make Ctrl-C (SIGINT) raise KeyboardInterrupt, and SIGTERM, SIGHUP and SIGQUIT raise
    _SyncStopSignal; return the handlers they had.

    Only the first of them raises. Every one that arrives after it, the same
    signal or another, is ignored for the rest of the run, so the undo runs to the
    end: a closed terminal can send SIGHUP and SIGINT at the same moment, and the
    second must not stop the undo the first one started. Once the run holds its
    stop signals (see _hold_stop_signals), one that arrives is only noted. Outside
    the main thread Python cannot set handlers, and the signals keep theirs.
    """
    import signal

    raised: list[int] = []

    def stop(signum, _frame):
        if "arrived" in _STOP_HOLD:
            _STOP_HOLD["arrived"].append(signum)
            return
        if raised:
            return
        raised.append(signum)
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise _SyncStopSignal(signum)

    previous = {}
    for name in _STOP_SIGNAL_NAMES:
        signum = getattr(signal, name, None)  # Windows has no SIGHUP or SIGQUIT
        if signum is None:
            continue
        if name in ("SIGINT", "SIGHUP", "SIGQUIT") and signal.getsignal(signum) == signal.SIG_IGN:
            # A background job ignores Ctrl-C and Ctrl-\, and `nohup` ignores a hangup:
            # a signal the caller chose to ignore still does not stop the sync.
            continue
        try:
            previous[signum] = signal.signal(signum, stop)
        except (ValueError, OSError):  # not the main thread
            pass
    return previous


def _restore_signal_handlers(previous):
    """Put back the handlers _catch_stop_signals replaced."""
    import signal

    for signum, handler in previous.items():
        try:
            signal.signal(signum, signal.SIG_DFL if handler is None else handler)
        except (ValueError, OSError, TypeError):
            pass


_OUTPUT_LOST: list[str] = []  # why stdout could not be written this run, once a write to it found out


def _print_safely(*args, **kwargs):
    """print(), except that output that cannot be written stops neither the undo that
    follows nor the exit: a terminal that is gone (a dropped ssh session), or a pipe
    whose reader quit (`cli sync | head`, BrokenPipeError), a stdout closed before the
    run (`cli sync >&-`, where sys.stdout is None). This line and later output then go
    to os.devnull (or nowhere), and _OUTPUT_LOST says why."""
    import sys

    if sys.stdout is None:
        # Started with stdout closed (cli sync >&-): Python has no stdout to write to at all.
        if not _OUTPUT_LOST:
            _OUTPUT_LOST.append("stdout is closed")
        return
    try:
        print(*args, **kwargs)
        sys.stdout.flush()
    except (OSError, ValueError) as e:  # ValueError: a stdout that was closed
        _note_output_lost(e)
        try:
            # The descriptor too, so what the old stream still holds goes there when Python
            # exits, instead of failing again ("Exception ignored ... BrokenPipeError").
            devnull = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull, sys.stdout.fileno())
            finally:
                os.close(devnull)
        except (OSError, ValueError, AttributeError):  # a stdout without a descriptor
            pass
        sys.stdout = open(os.devnull, "w")  # left open for the rest of the run


def _print_error(*args, **kwargs):
    """print() to stderr, for a ✗ line and the lines that belong to it, so `cli sync >/dev/null`
    (or a script that keeps only stdout) still shows what went wrong; progress and success stay
    on stdout. stdout is flushed first, so where both go to one place (2>&1 | tee log) the lines
    keep their order. With stderr closed (2>&-), nothing is printed."""
    import sys

    try:
        if sys.stdout is not None:
            sys.stdout.flush()
    except (OSError, ValueError):  # a stdout that is gone: said where it is written next
        pass
    if sys.stderr is None:
        return
    print(*args, file=sys.stderr, **kwargs)


def _print_error_safely(*args, **kwargs):
    """_print_error, except that output that cannot be written stops neither the undo that
    follows nor the exit (see _print_safely): stderr that is gone (a dropped ssh session, a
    pipe whose reader quit, 2>&-) then goes to os.devnull for the rest of the run."""
    import sys

    _print_safely(end="")  # flushes stdout, and notes a stdout that is gone (see _output_lost_by)
    if sys.stderr is None:
        return
    try:
        print(*args, file=sys.stderr, **kwargs)
        sys.stderr.flush()
    except (OSError, ValueError):
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull, sys.stderr.fileno())
            finally:
                os.close(devnull)
        except (OSError, ValueError, AttributeError):  # a stderr without a descriptor
            pass
        sys.stderr = open(os.devnull, "w")  # left open for the rest of the run


def _note_output_lost(error):
    """Note in _OUTPUT_LOST why stdout could not be written: `error`, from a write to it."""
    _OUTPUT_LOST.append(getattr(error, "strerror", None) or str(error) or type(error).__name__)


def _flush_output():
    """Write what stdout holds now: an OSError (a pipe whose reader quit) is raised, and stops
    the sync like any other error (see _output_lost_by)."""
    import sys

    if sys.stdout is not None:
        try:
            sys.stdout.flush()
        except (OSError, ValueError) as e:
            _note_output_lost(e)
            raise


def _output_lost_by(error):
    """True when `error`, which stopped the sync, is its own output failing (see _print_safely):
    a pipe whose reader quit (`cli sync | head`, BrokenPipeError), a terminal that is gone, a
    stdout that was closed. The error lines of the sync go to stderr, which may still be there
    (`cli sync 2>err | head`), so they say it, and how the sync ended."""
    import errno

    _print_safely(end="")  # notes a stdout that is gone, when no write to it noted it yet
    if not _OUTPUT_LOST:
        return False
    if isinstance(error, OSError):
        return error.errno in (errno.EPIPE, errno.EIO)
    return isinstance(error, ValueError) and "closed file" in str(error)


def _links_outside_tool(framework_files, other_paths=()):
    """The paths sync would write through a symlink to a place outside the tool
    folder, as [(path, where it resolves, the link)].

    A framework file sync would write is one that is missing, differs from its
    framework copy, or has a mode sync fixes (see _mode_fix). `other_paths` are the
    other paths this run writes or creates (see _sync_writes_beyond_framework), and
    are checked as they are. `the link` is the path itself when it is the link, or
    the linked folder it sits in. A link that resolves inside the tool is not listed.
    """
    tool = os.path.realpath(os.getcwd())
    written = [
        dst
        for src, dst, exe in framework_files
        if not (os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False) and not _mode_fix(src, dst, exe))
    ]
    found = []
    for path in dict.fromkeys([*written, *other_paths]):
        target = os.path.realpath(path)
        if os.path.commonpath([tool, target]) == tool:
            continue
        # Name the link that leads out: the file itself, else the nearest linked folder above it.
        link = path
        while link and not os.path.islink(link):
            link = os.path.dirname(link)
        found.append((path, target, link or path))
    return found


def _pyproject_edits_pending(version=None):
    """True when sync would edit pyproject.toml: its scaffold_version stamp differs from
    `version` (the installed version by default; see _sync_version), or it has no
    app_name, in a form the line editor changes."""
    version = version or __version__
    if not os.path.exists("pyproject.toml"):
        return False
    try:
        text = _read_pyproject()[0]
        previous, stamped = _plan_ngargparser_key(text, "scaffold_version", version)
        if previous != version and stamped is not None:
            return True
        if _ngargparser_value(stamped or text, "app_name") is None:
            return _plan_ngargparser_key(stamped or text, "app_name", _derived_app_name())[1] is not None
    except (OSError, ValueError):
        return True  # the real run reads it and stops with the error; count it as written
    return False


def _sync_writes_beyond_framework(do_backup, version=None):
    """The paths besides the framework files that a sync run writes or creates, for the
    link check: .ngargparser/ (the lock, the hash record, the pre-sync copy, the
    backups), a legacy file's new name, deploy/install.sh when it is missing, and
    .gitignore, pyproject.toml and README.md when they would change (README.md never when it
    is a link, whose badge sync skips): what a real run writes, so a dry run reports the
    same. `version` is the stamp (see _sync_version)."""
    paths = [_BUILD_LOCK, _FRAMEWORK_HASHES, _SYNC_IN_PROGRESS]
    if do_backup:
        paths.append(os.path.join(".ngargparser", "sync-backups"))
    paths += [current for _legacy, current in _sync_legacy_moves()]
    if not os.path.exists(_CORE_PACKAGE_INIT):
        paths.append(_CORE_PACKAGE_INIT)
    if not os.path.exists("deploy/install.sh"):
        paths.append("deploy/install.sh")
    try:
        if ensure_gitignore(".gitignore", dry_run=True) != "unchanged":
            paths.append(".gitignore")
    except (OSError, ValueError):
        paths.append(".gitignore")  # the real run reads it and stops with the error
    if _pyproject_edits_pending(version):
        paths.append("pyproject.toml")
    if os.path.islink("README.md"):
        return paths  # sync skips the badge of a README.md that is a link: not written
    try:
        with open("README.md", encoding="utf-8", newline="") as f:
            readme = f.read()
        if _readme_with_badge(readme, __version__) != readme:
            paths.append("README.md")
    except (OSError, ValueError):
        pass  # missing, or skipped with a warning: not written
    return paths


def _unwritable_place(framework_files, version=None):
    """Why a sync run cannot make its first write that would fail on permissions, as a
    _SyncWriteError, or None: checked before it writes anything, so a read-only folder
    stops it with nothing written, rather than after it wrote other files and had to undo
    them. The places are the ones it writes in the tool, in the order it writes them: the
    folder of each legacy file it moves and of its new name, of each framework file it
    creates or replaces (through a temporary file there, see _write_atomically), of
    src/core/__init__.py and deploy/install.sh when missing (the nearest folder that exists,
    for one it makes), and the folder of .gitignore, pyproject.toml and README.md when they
    would change, and the file too (it is edited only when writable, see _replace_file).
    .ngargparser/ is the lock's and the pre-sync copy's to report. A write that fails all
    the same (a full disk) is undone as before."""
    import errno

    moves = _sync_legacy_moves()
    pending = [(path, False) for legacy, current in moves for path in (legacy, current)]
    moved = {current for _legacy, current in moves}
    pending += [
        (dst, False)
        for src, dst, _exe in framework_files
        if dst in moved or not (os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False))
    ]
    pending += [(path, False) for path in (_CORE_PACKAGE_INIT, "deploy/install.sh") if not os.path.exists(path)]
    edited = _sync_writes_beyond_framework(False, version)
    pending += [(path, True) for path in (".gitignore", "pyproject.toml", "README.md") if path in edited]
    for path, in_place in pending:
        target = os.path.realpath(path) if os.path.islink(path) else path
        folder = os.path.dirname(target)
        while folder and not os.path.isdir(folder):
            folder = os.path.dirname(folder)  # the folder sync makes it in
        folder = folder or "."
        if not os.access(folder, os.W_OK | os.X_OK):
            try:
                read_only = bool(os.statvfs(folder).f_flag & os.ST_RDONLY)
            except (OSError, AttributeError):  # no statvfs (Windows)
                read_only = False
            code = errno.EROFS if read_only else errno.EACCES
            return _SyncWriteError(path, OSError(code, os.strerror(code), path))  # EACCES makes a PermissionError
        if in_place and os.path.exists(target) and not os.access(target, os.W_OK):
            return _SyncWriteError(path, PermissionError(errno.EACCES, os.strerror(errno.EACCES), path))
    return None


def _unreadable_files(paths):
    """[(path, reason)] for each of `paths` that is a file this user cannot open to read."""
    found = []
    for path in dict.fromkeys(paths):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb"):
                pass
        except OSError as e:
            found.append((path, e.strerror or str(e)))
    return found


def _report_unreadable_files(unreadable):
    """Say which files sync cannot read (from _unreadable_files), that nothing changed, and the fix:
    chmod u+r for a file of this user's, else the chmod its owner can run."""
    import shlex

    paths = [path for path, _reason in unreadable]
    reasons = list(dict.fromkeys(reason for _path, reason in unreadable))
    one = len(paths) == 1
    _print_error(
        f"\033[91m✗\033[0m Cannot read {', '.join(paths)} ({'; '.join(reasons)}): sync reads every file it may "
        "change, to compare it with the framework's copy and to keep a copy that undoes the sync. Nothing was "
        f"changed. Make {'it' if one else 'them'} readable, then run cli sync again:"
    )
    mine: list[str] = []
    theirs: list[str] = []
    for path in paths:
        try:
            owned = os.name == "nt" or os.stat(path).st_uid == os.getuid()
        except (OSError, AttributeError):
            owned = True
        (mine if owned else theirs).append(path)
    if mine:
        _print_error(f"    chmod u+r {' '.join(shlex.quote(path) for path in mine)}")
    for path in theirs:
        _print_error(f"  {path} is not yours: ask its owner to run chmod a+r {shlex.quote(path)}")


def _report_links_outside_tool(links, framework_files=()):
    """Say which paths sync would write lead outside the tool, that nothing changed, and what to do."""
    framework = {dst for _src, dst, _exe in framework_files}
    if all(path in framework for path, _target, _link in links):
        what = "these framework files are links (or sit in a linked folder) that lead outside this tool, so sync would write the new framework code there"
    else:
        what = "these files sync would write are links (or sit in a linked folder) that lead outside this tool, so sync would write there"
    _print_error(f"\033[91m✗\033[0m Sync stopped: {what}:\n")
    for path, target, link in links:
        via = "" if link == path else f" (through {link}, a link)"
        _print_error(f"  {path} -> {target}{via}")
    to_fix = list(dict.fromkeys(link for _path, _target, link in links))
    # A link that points to nothing has nothing to copy: removed, sync creates the file in its place.
    dangling = {link for link in to_fix if os.path.islink(link) and not os.path.exists(link)}
    if not dangling:
        _print_error(
            "\n  Nothing was changed. Replace each link with a copy of what it points to, then run cli sync again:"
        )
    elif len(dangling) == len(to_fix):
        _print_error(
            "\n  Nothing was changed. Each link points to nothing: remove it, then run cli sync again, which "
            "creates the file in its place:"
        )
    else:
        _print_error(
            "\n  Nothing was changed. Replace each link with a copy of what it points to, and remove each one that "
            "points to nothing (sync creates the file in its place), then run cli sync again:"
        )
    for link in to_fix:
        if link in dangling:
            _print_error(f"    rm {link}")
        elif os.path.isdir(link):
            _print_error(f"    cp -RL {link} {link}.copy && rm {link} && mv {link}.copy {link}")
        else:
            _print_error(f"    cp {link} {link}.copy && mv {link}.copy {link}")


def _pyproject_toml_problem():
    """Why pyproject.toml in the current folder cannot be read as TOML, or None.

    None too when there is no pyproject.toml or no TOML parser (tomllib, tomli) to
    check it with. A leading byte order mark is not a problem (see _read_pyproject)."""
    if not os.path.isfile("pyproject.toml"):
        return None
    toml_reader = _toml_reader()
    if toml_reader is None:
        return None
    try:
        content = _read_pyproject()[0]
    except UnicodeDecodeError as e:
        return f"it is not UTF-8 text ({e.reason} at byte {e.start})"
    except OSError as e:
        return f"it cannot be read ({e.strerror or e})"
    try:
        toml_reader.loads(content)
    except toml_reader.TOMLDecodeError as e:
        return str(e)
    return None


def _gitignore_problem():
    """Why .gitignore in the current folder cannot be read as UTF-8 text, as ensure_gitignore
    reads it, or None (none there, or one that reads)."""
    try:
        with open(".gitignore", encoding="utf-8", newline="") as f:
            f.read()
    except UnicodeDecodeError as e:
        return f"is not UTF-8 text (byte 0x{e.object[e.start]:02x} at offset {e.start})"
    except OSError:
        return None  # missing, or said where sync writes it
    return None


def _clashes_the_stamp_has(clashes, stamp):
    """The (entry, findings) of `clashes` whose `since` release is not newer than
    scaffold_version `stamp`; none when the stamp is missing or unparseable."""
    synced_with = _version_key(stamp)
    if synced_with is None:
        return []
    return [
        (entry, findings)
        for entry, findings in clashes
        if _version_key(entry["since"]) is not None and _version_key(entry["since"]) <= synced_with
    ]


def _drop_clashes_the_tool_runs_past(clashes, stamp):
    """Return (clashes, baseline): `clashes` without the findings the tool's own help
    screens show to be false, and the test drive run to decide that, or None.

    The detectors read the code and cannot follow the order it runs in: a
    remove_argument() in a helper method defined further down the file than the
    redeclaration still runs first. Whether the tool starts is the ground truth.
    So an entry is dropped when both hold:
      * the project's scaffold_version stamp is not older than the entry's `since`
        release, i.e. the framework files the tool has today already carry the
        change the entry is about, and
      * every help screen passes before the sync, on those framework files.
    Then the tool already runs with the change, and the finding is a false
    positive. Without that proof (no stamp, an older or unparseable one, no
    test drive possible, a screen that fails) every finding stands.
    """
    candidates = _clashes_the_stamp_has(clashes, stamp)
    if not candidates:
        return clashes, None
    _announce_test_drive("before")
    baseline = _smoke_check()
    results = baseline["results"]
    if not (baseline["runnable"] and results and all(passed for passed, _ in results.values())):
        return clashes, baseline
    for entry, findings in candidates:
        for f in findings:
            print(
                f"ℹ Not a clash here: {f['path']}:{f['first']} {f['what']}, and every help screen passes on "
                f"this tool's framework files (ngargparser {stamp}, which has the {entry['since']} change)."
            )
    return [clash for clash in clashes if clash not in candidates], baseline


def _clash_predict_output_options(skipped=None):
    """Find `add_argument` calls on a project's predict parser that use an
    option string the base class owns there: `--output-prefix`, `-o`,
    `--output-format` or `-f`.

    The base class has added `--output-prefix/-o` and `--output-format/-f` to
    `predict` itself since 0.3.0, so a project that declares either again, or
    reuses one of the short letters for another option, stops with "-o belongs to
    the framework (--output-prefix)" as soon as its parser is built. A declaration
    that follows `remove_argument(<option>, "predict")` or
    `self.parser_predict.remove_argument(<option>)` in the same file is not a
    clash: removing an option frees all its names (removing --output-format frees
    -f too), so the project can declare its own.

    Only calls on the predict subparser count (see _predict_parser_finder), so an
    `add_argument("-o")` on another object whose name holds "predict"
    (`predictor_cfg`) is not a clash. The project's parser files are read as
    syntax trees, so the option names in a comment or a help string don't count.
    A file that does not parse is
    appended to `skipped` (when given) instead of being judged. Returns a list
    of {'path', 'first', 'last', 'what'}.
    """
    import ast
    import glob

    owned = ("--output-prefix", "-o", "--output-format", "-f")
    # Removing either name of a framework option frees both.
    pairs = {
        "--output-prefix": {"--output-prefix", "-o"},
        "-o": {"--output-prefix", "-o"},
        "--output-format": {"--output-format", "-f"},
        "-f": {"--output-format", "-f"},
    }
    findings = []
    for path in sorted(glob.glob(os.path.join("src", "*ArgumentParser.py"))):
        try:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except (OSError, SyntaxError, ValueError):
            if skipped is not None:
                skipped.append(path)
            continue  # unreadable here; the test drive is the net for this file
        on_predict = _predict_parser_finder(tree)
        # Each method call, with the attribute it calls (`<object>.<method>`).
        calls = [
            (node, node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        freed = []  # (line, names) for each removal of <option> from predict
        for node, func in calls:
            if func.attr != "remove_argument":
                continue
            values = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            values += [
                k.value.value
                for k in node.keywords
                if k.arg == "subparser_name" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)
            ]
            # remove_argument(<option>, "predict"), or the subparser's own
            # self.parser_predict.remove_argument(<option>).
            on_the_subparser = len(values) == 1 and on_predict(func.value)
            if (len(values) >= 2 and values[1] == "predict") or on_the_subparser:
                freed.append((node.lineno, pairs.get(values[0], {values[0]})))
        for node, func in calls:
            if func.attr != "add_argument" or not on_predict(func.value):
                continue
            names = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            declared = [
                name
                for name in owned
                if name in names and not any(line < node.lineno and name in gone for line, gone in freed)
            ]
            if declared:
                findings.append(
                    {
                        "path": path,
                        "first": node.lineno,
                        "last": node.end_lineno,
                        "what": f"declares {declared[0]} on predict",
                        # The name it takes, and the framework option that name belongs to.
                        "flag": declared[0],
                        "option": "--output-prefix" if declared[0] in pairs["-o"] else "--output-format",
                    }
                )
    return sorted(findings, key=lambda f: (f["path"], f["first"]))


_PREDICT_ATTRIBUTES = ("parser_predict", "predict_input_group")  # NGArgumentParser's names for them
_GROUP_METHODS = ("add_argument_group", "add_mutually_exclusive_group")


def _predict_parser_finder(tree):
    """A function that tells whether an expression in parsed parser file `tree`, the object
    of a method call, is the predict subparser or one of its argument groups.

    That is: an attribute that ends in parser_predict or predict_input_group
    (self.parser_predict), or in an attribute the file assigns the result of
    add_predict_subparser(...) to (self.predict_parser = self.add_predict_subparser(...));
    the name parser_predict; or a local name whose latest assignment before the call, in
    the same function, is one of those, add_predict_subparser(...), or
    add_argument_group(...) / add_mutually_exclusive_group(...) on one of those."""
    import ast

    def call_name(node):
        func = node.func if isinstance(node, ast.Call) else None
        return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None

    attributes = set(_PREDICT_ATTRIBUTES)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and call_name(node.value) == "add_predict_subparser":
            attributes.update(t.attr for t in node.targets if isinstance(t, ast.Attribute))

    # The function, lambda, class body or module each node runs in, and the names each one assigns.
    scope_of: dict[ast.AST, ast.AST] = {}
    assigned: dict[ast.AST, dict[str, list[tuple[tuple[int, int], ast.AST]]]] = {}
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    pending: list[tuple[ast.AST, ast.AST]] = [(tree, tree)]
    while pending:
        node, scope = pending.pop()
        for child in ast.iter_child_nodes(node):
            scope_of[child] = scope
            pending.append((child, child if isinstance(child, scopes) else scope))
        targets: list[ast.expr] = []
        value: Optional[ast.expr] = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            targets, value = [node.target], node.value
        for target in targets:
            if isinstance(target, ast.Name) and value is not None:
                # It takes effect once its value is worked out: at the end of the statement.
                position = (
                    getattr(node, "end_lineno", None) or target.lineno,
                    getattr(node, "end_col_offset", None) or 0,
                )
                assigned.setdefault(scope, {}).setdefault(target.id, []).append((position, value))

    def on_predict(expr, scope=None):
        scope = scope if scope is not None else scope_of.get(expr, tree)
        if isinstance(expr, ast.Attribute):
            return expr.attr in attributes
        if not isinstance(expr, ast.Name):
            return False
        if expr.id == "parser_predict":
            return True
        here = (expr.lineno, expr.col_offset)
        earlier = [(at, value) for at, value in assigned.get(scope, {}).get(expr.id, []) if at < here]
        return bool(earlier) and makes_predict(max(earlier, key=lambda item: item[0])[1], scope)

    def makes_predict(value, scope):
        if on_predict(value, scope):
            return True
        name = call_name(value)
        if name == "add_predict_subparser":
            return True
        return name in _GROUP_METHODS and isinstance(value.func, ast.Attribute) and on_predict(value.func.value, scope)

    return on_predict


# Project code that a framework release is known to break. Add an entry whenever
# a release changes something project-owned code may rely on: `since` is the
# release that made the change, `detect` returns the findings in the project in
# the current directory, and `explain` tells the developer what to do about them.
# An `explain` line may name what the findings found: {flag}, the name the first one
# takes, {option}, the framework option it belongs to, and {remove}, the
# remove_argument call that frees each option found (see _print_known_clash_findings).
class _KnownClash(TypedDict):
    since: str
    detect: Callable[[Optional[list[str]]], list[dict[str, Any]]]
    explain: tuple[str, ...]


KNOWN_CLASHES: list[_KnownClash] = [
    {
        "since": "0.3.0",
        "detect": _clash_predict_output_options,
        "explain": (
            "Since 0.3.0 the framework adds --output-prefix/-o and --output-format/-f to predict.",
            "Declaring either again, or reusing -o or -f for another option there, makes every",
            'command stop with "{flag} belongs to the framework ({option})".',
            "Delete the duplicate declaration, or give your own option another letter. To change",
            "the help text or default of the framework's option, use",
            "self.parser_predict.update_arguments(...); to replace it, call",
            "{remove} before your add_argument(...).",
            "Then run `cli sync` again.",
        ),
    },
]


def _find_known_clashes(skipped=None):
    """Return [(entry, findings)] for each KNOWN_CLASHES entry found in the project.
    Files a detector could not read are appended to `skipped` when it is given."""
    found = []
    for entry in KNOWN_CLASHES:
        findings = entry["detect"](skipped)
        if findings:
            found.append((entry, findings))
    return found


def _clash_place(finding):
    """Where a known-clash finding is: <file>:<line>, or <file>:<first>-<last>."""
    return f"{finding['path']}:{finding['first']}" + (
        "" if finding["last"] == finding["first"] else f"-{finding['last']}"
    )


def _print_known_clash_findings(clashes, say=print):
    """Say (with `say`) where each known clash is and what to do about it: the advice names
    the option the findings clash with ({flag}, {option} and {remove} in `explain`), so a
    -o declaration is told to remove --output-prefix, not --output-format."""
    for entry, findings in clashes:
        places = [_clash_place(f) for f in findings]
        width = max(len(place) for place in places)
        say()
        for place, finding in zip(places, findings):
            say(f"  {place:<{width}}   {finding['what']}")
        say()
        options = list(dict.fromkeys(f["option"] for f in findings if f.get("option")))
        fields = {
            "flag": next((f["flag"] for f in findings if f.get("flag")), ""),
            "option": options[0] if options else "",
            # The one remove form the framework recommends: the subparser's own (the clash is on predict).
            "remove": " and ".join(f'self.parser_predict.remove_argument("{option}")' for option in options),
        }
        for line in entry["explain"]:
            say(f"  {line.format_map(fields)}")


def _report_known_clashes(clashes, dry_run=False, may_pass=False, why=None):
    """Say what a known clash is and how to fix it. A real run stops after this, and
    `why` says why the help screens did not rule it out; a dry run says what a real run
    does, then goes on with its preview. With `may_pass` (a dry run of a tool whose help
    screens a real run can run), it says that a real run lets those decide."""
    if dry_run:
        lead = "Sync may stop" if may_pass else "Sync would stop"
    else:
        lead = "Sync stopped"
    mark = "\033[93m⚠\033[0m" if dry_run and may_pass else "\033[91m✗\033[0m"
    # A clash that stops the run is an error, on stderr; one a real run may get past is a warning.
    say = print if dry_run and may_pass else _print_error
    say(f"\n{mark} {lead}: this tool has code the current framework breaks.")
    _print_known_clash_findings(clashes, say=say)
    if dry_run and may_pass:
        say(
            "\n  The check reads the code, so a finding can be false, and a dry run runs no tool code to tell. A real "
            "run first runs the tool's help screens: if one fails, it stops here and changes nothing; if every one "
            "passes, it goes ahead, and rolls back if one fails after the update. To sync without these checks: "
            "cli sync --no-verify"
        )
        say("  The rest of this preview shows what sync would change once it goes ahead.")
    elif dry_run:
        say("\n  A real run stops here and changes nothing. To sync without these checks: cli sync --no-verify")
        say("  The rest of this preview shows what sync would change once it goes ahead.")
    else:
        if why:
            say(f"\n  {why}")
        say("\n  Nothing was changed. To sync without these checks: cli sync --no-verify")


def _why_help_screens_kept_clashes(baseline):
    """Why the help screens run before the sync (`baseline`, from _smoke_check) did not rule a
    known clash out: they could not run, or one of them fails. None when there is no run."""
    if baseline is None:
        return None
    if not baseline["runnable"]:
        return f"The tool's help screens could not run to rule it out: {baseline['reason']}."
    failed = [(command, error) for command, (passed, error) in baseline["results"].items() if not passed]
    if failed:
        command, error = failed[0]
        return f"The tool's help screens did not rule it out: {command} fails before the sync ({error})."
    return None


def _every_help_screen_passes(baseline):
    """True when the test drive `baseline` (from _smoke_check) ran, and every help screen passed."""
    results = baseline["results"] if baseline else {}
    return bool(baseline and baseline["runnable"] and results and all(passed for passed, _ in results.values()))


def _report_possible_clashes(clashes):
    """Say that each known clash in `clashes` may be one, and that the help screens decide:
    they all pass before the sync, so it goes ahead and runs them again after the update."""
    for entry, findings in clashes:
        for f in findings:
            print(f"ℹ Possible known clash (ngargparser {entry['since']} and later): {_clash_place(f)} {f['what']}.")
    print(
        "  Every help screen passes before the sync, so sync goes ahead, runs them again after the update, and "
        "rolls back if one fails then."
    )


def _ignore_stop_signals():
    """Ignore Ctrl-C (SIGINT), SIGTERM, SIGHUP and SIGQUIT; return the handlers they had, for
    _restore_signal_handlers. Outside the main thread the signals keep theirs."""
    import signal

    previous = {}
    for signum in _stop_signals():
        try:
            previous[signum] = signal.signal(signum, signal.SIG_IGN)
        except (ValueError, OSError):  # not the main thread
            pass
    return previous


class _StopSignalsHeld:
    """`with _StopSignalsHeld():` holds Ctrl-C (SIGINT), SIGTERM, SIGHUP and SIGQUIT back while
    the block runs; one that arrives then takes effect when it ends. Sync writes a file and notes
    it written in one such block, so a stop never lands between the two and leaves a written
    file the undo does not know of. Where signals cannot be held (Windows, another thread
    than the main one), the block runs as it is."""

    def __enter__(self):
        import signal

        self._previous = None
        sigmask = getattr(signal, "pthread_sigmask", None)
        if sigmask is not None:
            try:
                self._previous = sigmask(signal.SIG_BLOCK, set(_stop_signals()))
            except (OSError, ValueError):
                pass
        return self

    def __exit__(self, *_exc):
        import signal

        if self._previous is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, self._previous)  # a held signal acts here
        return False


def _undo_sync(snapshot, backup_root, say=print):
    """Put the project back as it was before the sync and say so (with `say`); return
    True when every file was restored.

    A second Ctrl-C, a SIGTERM, a SIGHUP or a SIGQUIT while the files are put back is
    ignored, so the undo runs to the end; the handlers come back afterwards, with the stop
    signals held to the end of the run (see _hold_stop_signals). A file that cannot be put
    back (a read-only folder) does not stop the others. When one is left, the pre-image
    stays on disk, and instead of raising it says which files and why (the read-only
    folder), where the pre-image and this run's backups are, the commands that finish the
    undo by hand (see _restore_by_hand), and not to sync over the half-restored tool with
    --no-verify."""
    ignored = _ignore_stop_signals()

    def say_moved_aside():
        # A file edited while the sync ran, after the sync wrote it: kept, not overwritten.
        for path, where in getattr(snapshot, "moved_aside", []):
            say(
                f"  \033[93m⚠\033[0m {path} changed after the sync wrote it (an edit made while the sync ran); "
                f"that version was moved to {where}."
            )

    try:
        try:
            snapshot.restore(backup_root)
        except BaseException as e:  # anything that stops the undo: keep what is needed to finish it by hand
            say_moved_aside()
            backups = (
                f"backups of overwritten files are in {backup_root}"
                if backup_root and os.path.isdir(backup_root)
                else "this run made no backups"
            )
            if isinstance(e, _RestoreIncomplete):
                # Each file it could put back is; the rest are named, with the reason.
                lead = f"Restore did not finish. {_restore_failures(e.failed)}"
            else:
                lead = f"Restore did not finish ({str(e) or type(e).__name__})."
            kept = snapshot.keep()
            say(f"  \033[91m✗\033[0m {lead} The files before the sync are in {kept}; {backups}.")
            if kept:
                # The commands the next sync would give (see _report_unfinished_sync), now.
                what, commands = _restore_by_hand(kept, snapshot.record())
                say(f"  To finish it by hand, {what}")
                for command in commands:
                    say(f"    {command}")
            say("  Do not run cli sync --no-verify on a half-restored tool.")
            return False
        say_moved_aside()
        say("  Every file was restored to its state before the sync.")
        return True
    finally:
        # The undo ran: a stop signal from here on waits until the run is over (see sync_command).
        _hold_stop_signals()
        _restore_signal_handlers(ignored)


SMOKE_TIMEOUT = 60  # seconds each help screen of the project's own tool may take, unless set below
_SMOKE_TIMEOUT_VARIABLE = "NGARGPARSER_SYNC_HELP_TIMEOUT"  # the seconds, when the tool needs longer (or less)
_SMOKE_TIMEOUT_MAX = 86400  # one day: the most it may be set to (Python waits at most about 24 days)
# The framework's subcommands, whose --help the test drive runs after the top-level --help, before
# any other subcommand the tool adds (see _help_screen_subcommands).
_FRAMEWORK_SUBCOMMANDS = ("predict", "preprocess", "postprocess")


def _help_screen_subcommands(help_text):
    """The subcommands whose --help the test drive runs after the top-level one, from the
    top-level --help output `help_text` ("" when that screen failed): the framework's, then
    each other name in the order the usage lists it (a subcommand the tool adds with
    self.subparser.add_parser, or an alias). The names are the first {a,b,...} group, split
    at ',', in the usage (from "usage:" to the first blank line, without colour codes) that
    holds a framework subcommand, as in usage: run_x.py [-h] {predict,preprocess,postprocess,
    list-alleles} ...; without one, the framework's alone. The rule make build's startup
    check applies (scripts/core/build.sh, subcommands())."""
    text = _COLOUR_CODE_RE.sub("", help_text)
    start = text.find("usage:")
    usage = re.split(r"\n[ \t]*\n", text[start:], maxsplit=1)[0] if start >= 0 else ""
    for group in re.findall(r"\{([^{}]*)\}", usage):
        names = [name.strip() for name in group.split(",")]
        if set(names) & set(_FRAMEWORK_SUBCOMMANDS):
            return [*_FRAMEWORK_SUBCOMMANDS, *(name for name in names if name and name not in _FRAMEWORK_SUBCOMMANDS)]
    return list(_FRAMEWORK_SUBCOMMANDS)


def _help_screen_timeout():
    """(seconds, problem): how long each help screen of the test drive may take, which is
    NGARGPARSER_SYNC_HELP_TIMEOUT when it is a number of seconds above 0 and at most
    _SMOKE_TIMEOUT_MAX, else SMOKE_TIMEOUT; `problem` says what is wrong with a value that
    is set and not used, else it is None."""
    import math

    value = os.environ.get(_SMOKE_TIMEOUT_VARIABLE, "").strip()
    if not value:
        return SMOKE_TIMEOUT, None
    try:
        seconds = float(value)
    except ValueError:
        seconds = math.nan
    if math.isfinite(seconds) and 0 < seconds <= _SMOKE_TIMEOUT_MAX:
        return seconds, None
    return SMOKE_TIMEOUT, (
        f"{_SMOKE_TIMEOUT_VARIABLE}={value} is not a number of seconds above 0 and at most {_SMOKE_TIMEOUT_MAX}, "
        f"so each help screen may take {SMOKE_TIMEOUT}s"
    )


def _announce_test_drive(when):
    """Say that the help screens run now, `when` ("before" or "after") the update, when the
    test drive can run them: each one may take up to the timeout, so the wait is not silent."""
    plan = _test_drive_plan()
    if plan["script"] and plan["python"]:
        print(f"\nChecking that the tool starts (its help screens) {when} the update...", flush=True)


def _project_python():
    """The interpreter of the project's own virtualenv (`.venv`), or None."""
    for relative in (os.path.join(".venv", "bin", "python"), os.path.join(".venv", "Scripts", "python.exe")):
        if os.path.isfile(relative):
            return os.path.abspath(relative)
    return None


# Why a sync that would change framework files stops when the tool has no .venv: the test
# drive has no interpreter to run the tool with (see _test_drive_plan).
_NO_VENV_STOP = (
    "Sync needs the tool's .venv to check that the tool still starts after the update. "
    "Run 'uv sync', then cli sync again (or cli sync --no-verify to update without the check)."
)


def _entry_problem(entry):
    """Why `entry`, the [tool.ngargparser] entry, breaks one of the rules make build
    applies to it (scripts/core/build.sh, check_entry), or None: it is a path inside
    the tool, to a file that exists and is not a folder."""
    where = f'entry = "{entry}" under [tool.ngargparser] in pyproject.toml'
    if entry.startswith("/") or ".." in entry.split("/"):
        return f"{where} must be a path inside the tool folder, such as src/run_<name>.py"
    if os.path.isdir(entry):
        return f"{where} names a folder, not a file"
    if not os.path.isfile(entry):
        return f"{where} names a file that does not exist"
    return None


def _entry_runs_with_python(entry):
    """True when entry script `entry` is a Python script, which the test drive runs with the
    tool's .venv python; any other entry (a shell script, a program) runs by itself. A first
    #! line decides: it names python, or it does not; without one, a name that ends in .py
    is a Python script. The rule of ./configure (entry_runs_with_python) and app_env.py."""
    try:
        with open(entry, "rb") as f:
            line = f.readline(4096)
    except OSError:  # missing or unreadable: the name decides
        line = b""
    if line.startswith(b"#!"):
        return b"python" in line
    return entry.endswith(".py")


def _test_drive_plan():
    """What the test drive would run, worked out without running anything:
    ``{'python', 'script', 'reason', 'stop'}``.

    The entry script is the one pyproject.toml names as [tool.ngargparser] entry (a
    string in any form TOML allows, as ./configure reads it), else the only
    `src/run_*.py`; the interpreter is the tool's `.venv` one. When the test drive
    cannot run, `script` or `python` is None and `reason` says why, and `stop` says what
    a real sync that would change framework files stops with, and how to go on: the
    cause is the tool's to fix before a checked sync (an entry the build would refuse,
    no entry and not exactly one `src/run_*.py`, or no .venv). With several
    `src/run_*.py` and no entry, sync cannot tell which one is the entry script, and
    running the others with `--help` could execute whatever they do, so it runs none.
    """
    import glob

    plan: dict[str, Any] = {"python": None, "script": None, "reason": "", "stop": None}
    content = None
    if os.path.isfile("pyproject.toml"):
        try:
            content = _read_pyproject()[0]
        except (OSError, UnicodeDecodeError):
            content = None
    entry, problem = "", None
    if content is not None:
        # Read as TOML when a parser is installed, so an inline table or a dotted key counts,
        # as it does for ./configure; else line by line, as make build reads it.
        found, raw = _ngargparser_setting(content, "entry")
        if found and isinstance(raw, str):
            if raw:  # an empty entry counts as none, as in the build
                entry = raw[2:] if raw.startswith("./") else raw
                problem = _entry_problem(entry)
        elif found:
            problem = 'entry under [tool.ngargparser] in pyproject.toml must be a quoted path, such as entry = "src/run_<name>.py"'
    if problem:
        plan["reason"] = problem
        plan["stop"] = (
            f"Sync cannot check that the tool still starts after the update: {problem}. "
            "Fix it, then run cli sync again (or cli sync --no-verify to update without the check)."
        )
        return plan
    scripts = [entry] if entry else sorted(glob.glob(os.path.join("src", "run_*.py")))
    if len(scripts) != 1:
        # Which script to run is the tool's to say: without it, nothing shows the update broke the tool.
        if scripts:
            plan["reason"] = (
                f"several src/run_*.py scripts were found ({', '.join(scripts)}), and pyproject.toml names "
                "none of them as the entry script"
            )
        else:
            plan["reason"] = "no src/run_*.py entry script was found, and pyproject.toml names no entry script"
        plan["stop"] = (
            f"Sync cannot check that the tool still starts after the update: {plan['reason']}. "
            'Set entry = "src/run_<name>.py" under [tool.ngargparser] in pyproject.toml, then run cli sync '
            "again (or cli sync --no-verify to update without the check)."
        )
        return plan
    if not _entry_runs_with_python(scripts[0]) and os.name != "nt" and not os.access(scripts[0], os.X_OK):
        # An entry that is not a Python script runs by itself (see _smoke_check), so it must be executable.
        plan["reason"] = (
            f'entry = "{scripts[0]}" under [tool.ngargparser] in pyproject.toml is not a Python script and not '
            f"executable (chmod +x {scripts[0]})"
        )
        plan["stop"] = (
            f"Sync cannot check that the tool still starts after the update: {plan['reason']}. "
            "Fix it, then run cli sync again (or cli sync --no-verify to update without the check)."
        )
        return plan
    plan["script"] = scripts[0]
    plan["python"] = _project_python()
    if not plan["python"]:
        plan["reason"] = "the tool has no .venv interpreter (run `uv sync`)"
        plan["stop"] = _NO_VENV_STOP
    elif os.name != "nt" and not os.access(plan["python"], os.X_OK):
        # A .venv whose python cannot be run starts nothing: a broken .venv, which uv sync makes again.
        python = os.path.relpath(plan["python"])
        plan["python"] = None
        plan["reason"] = f"the tool's .venv interpreter {python} is not executable (run `uv sync`)"
        plan["stop"] = (
            f"Sync needs the tool's .venv to check that the tool still starts after the update, and {python} is "
            "not executable. Run 'uv sync', then cli sync again (or cli sync --no-verify to update without the check)."
        )
    return plan


def _smoke_check():
    """Test drive: run the help screens of the tool's entry script: --help, then <name> --help
    for each subcommand its output lists, the framework's first (see _help_screen_subcommands),
    so a subcommand the tool adds is checked too. A screen that worked before the update and
    is not run after it (its subcommand is no longer listed) counts as one that fails.

    Uses the tool's own interpreter, with APP_ROOT pointed at the tool so an
    unconfigured checkout can still start. The entry script and interpreter are
    the ones _test_drive_plan finds; an entry that is not a Python script (see
    _entry_runs_with_python) runs by itself. The environment is the one make build
    gives its startup check (scripts/core/build.sh, STARTUP_DRIVER): no PYTHONPATH,
    PYTHONHOME or PYTHONSTARTUP from the shell, and no user site-packages, so what the
    shell has set does not decide whether the tool starts; the tool's .venv/bin first on
    PATH, and VIRTUAL_ENV set to the tool's .venv (unset when it has none). Returns
    ``{'runnable': bool, 'reason': str, 'stop': str or None, 'results': {command: (passed, error)},
    'timed_out': command or None}``; `runnable` is False, with the plan's `reason` and `stop`,
    when the plan has no script or no interpreter, and `timed_out` names the screen that took
    longer than the timeout, if one did.

    Each screen may take the seconds _help_screen_timeout gives. The first one that
    takes longer fails this run of them: the screens after it are not run (and are not
    in `results`), and a line says which one timed out, so a tool that hangs costs one
    timeout, not one each. Each screen runs in a process group of its own, and the whole
    group is killed when it times out (or the sync is stopped while it runs), so a
    program it started in the background does not outlive it.
    """
    import subprocess

    plan = _test_drive_plan()
    if not (plan["script"] and plan["python"]):
        return {"runnable": False, "reason": plan["reason"], "stop": plan["stop"], "results": {}, "timed_out": None}

    python, script = plan["python"], plan["script"]
    start = [python, script] if _entry_runs_with_python(script) else [os.path.abspath(script)]
    env = {**os.environ, "APP_ROOT": os.getcwd(), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(name, None)
    # The tool's .venv, as make build gives it to the help screens: its bin first on PATH, so
    # an entry that is not a Python script (a shell script that runs python3) finds the
    # tool's Python, and VIRTUAL_ENV naming it, not the one the shell has active.
    venv = os.path.abspath(".venv")
    if os.path.isdir(venv):
        bin_folder = os.path.join(venv, "Scripts" if os.name == "nt" else "bin")
        env["VIRTUAL_ENV"] = venv
        env["PATH"] = bin_folder + os.pathsep + env.get("PATH", "")
    else:
        env.pop("VIRTUAL_ENV", None)
    timeout = _help_screen_timeout()[0]
    results: dict[str, tuple[bool, str]] = {}
    timed_out = None
    screens = [["--help"]]  # and, once it has run, those of the subcommands it lists
    while len(results) < len(screens):
        arguments = screens[len(results)]
        command = " ".join([script, *arguments])
        try:
            returncode, stderr, stdout = _run_help_screen([*start, *arguments], env, timeout)
            lines = [line for line in stderr.splitlines() if line.strip()]
            passed, error = returncode == 0, (lines[-1].strip() if lines else f"exit code {returncode}")
        except subprocess.TimeoutExpired:
            if len(screens) == 1:  # a top-level --help that timed out lists none
                screens += [[name, "--help"] for name in _help_screen_subcommands("")]
            results[command] = (False, f"timed out after {timeout:g}s")
            timed_out = command
            rest = len(screens) - len(results)
            skipped = f"; the other {rest} help screen(s) were not run" if rest else ""
            print(
                f"  └ \033[93m⚠\033[0m {command} timed out after {timeout:g}s{skipped}. To allow more time, set "
                f"{_SMOKE_TIMEOUT_VARIABLE} to the seconds each help screen may take.",
                flush=True,
            )
            break
        except OSError as e:
            passed, error, stdout = False, str(e), ""
            if e.filename == python:
                error += " (run `uv sync` to make the .venv again)"
        if len(screens) == 1:
            # The subcommands the top-level --help lists, the tool's own among them.
            screens += [[name, "--help"] for name in _help_screen_subcommands(stdout if passed else "")]
        results[command] = (passed, "" if passed else error)
    return {"runnable": True, "reason": "", "stop": None, "results": results, "timed_out": timed_out}


def _run_help_screen(argv, env, timeout):
    """Run one help screen of the test drive from the tool folder: (exit code, its stderr,
    its stdout).

    It starts in a session of its own (POSIX), so it and whatever it starts form one process
    group: on a timeout (subprocess.TimeoutExpired, raised again) or anything else that stops
    the wait (Ctrl-C), the whole group is killed, and so is what is left of it once the screen
    ends. Its stderr and stdout go to temporary files, read once it ends: a program it started
    in the background inherits them, and through a pipe would hold the screen open until the
    timeout. A program that cannot be started raises OSError."""
    import subprocess
    import tempfile

    with tempfile.TemporaryFile() as stderr, tempfile.TemporaryFile() as stdout:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=env,
            start_new_session=os.name == "posix",
        )
        try:
            process.wait(timeout=timeout)
        except BaseException:
            _kill_process_group(process)
            raise
        _kill_process_group(process)  # what it started in the background, if anything
        stderr.seek(0)
        stdout.seek(0)
        return (
            process.returncode,
            stderr.read().decode("utf-8", errors="replace"),
            stdout.read().decode("utf-8", errors="replace"),
        )


def _kill_process_group(process):
    """Kill `process` and every process in its group (one it started in the background
    too), then reap it. Its stderr pipe is closed without waiting for what is left in it:
    a program that left the group could hold it open."""
    import signal

    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass  # already gone, with all of its group
    process.wait()
    if process.stderr is not None:
        process.stderr.close()


def _checkout_skips_upgrade(ref, dev):
    """The local checkout a plain `cli sync` syncs from instead of replacing it
    with the latest release, else None. Naming a version (--ref, --dev or
    NGARGPARSER_UPGRADE_URL) still upgrades, as does any released install."""
    import os

    plain_latest = (
        ref == "latest" and not dev and _local_source(ref) is None and not os.environ.get("NGARGPARSER_UPGRADE_URL")
    )
    return _local_checkout() if plain_latest else None


def _report_dry_run_upgrade(ref, dev):
    """--dry-run skips the self-upgrade a real sync starts with, so its preview
    shows the installed version's files. Say what a real run would install
    first, and warn when that replaces a local checkout. Reads the remote's
    tags (no install), resolving the target the way `upgrade_command` does."""
    import os

    checkout = _checkout_skips_upgrade(ref, dev)
    if checkout:
        print(
            f"ℹ A real run syncs from your local checkout at {checkout} (no upgrade), the same files as this preview."
        )
        return

    latest_tag = _UNSET
    plain_latest = (
        ref == "latest" and not dev and _local_source(ref) is None and not os.environ.get("NGARGPARSER_UPGRADE_URL")
    )
    if plain_latest:
        try:
            latest_tag = _latest_release_tag(
                BASE_REPO_URL[len("git+") :], timeout=RELEASE_CHECK_TIMEOUT, strict=True, prompt=False
            )
        except _RemoteUnavailable:
            print("⚠ Couldn't check which ngargparser a real run would install (GitLab unreachable).")
            return

    url, resolved = _resolve_upgrade_url(ref, dev, latest_tag=latest_tag)
    if plain_latest and _installed_is_newer(resolved):
        print(
            f"ℹ A real run keeps the installed ngargparser {__version__}, which is newer than the latest release "
            f"{resolved}, and syncs from it: the same files as this preview."
        )
        return
    if plain_latest and _installed_is_latest(resolved):
        print(
            f"ℹ A real run keeps the installed ngargparser {__version__}, which is the latest release "
            f"({resolved}), and syncs from it: the same files as this preview."
        )
        return
    from_local = resolved.startswith("local:")
    target = f"from the local checkout {url}" if from_local else resolved
    print(
        f"ℹ A real run first installs ngargparser {target} (installed: {__version__}), then syncs from it. "
        "This preview uses the installed version's files."
    )
    checkout = _local_checkout()
    if checkout and not (from_local and os.path.realpath(url) == os.path.realpath(checkout)):
        print(
            f"⚠ The installed ngargparser is your local checkout at {checkout}; a real run replaces it with "
            f"{target}. To sync from the checkout, use --no-upgrade."
        )


def sync_command(args):
    """Synchronize framework files in existing projects to the latest version.

    A stop signal (Ctrl-C, SIGTERM, SIGHUP, SIGQUIT) that arrives once the outcome is decided
    (the sync stands, or its undo ran) is held while the run cleans up, and delivered here,
    after it: one line on stderr, and the exit code 128 + the signal, without a traceback."""
    import signal

    _STOP_HOLD.clear()
    try:
        rc = _sync_command(args)
    except KeyboardInterrupt as e:
        # A stop in the moment between the run's last step and its hold: no traceback either.
        signum = getattr(e, "signum", None) or signal.SIGINT
        _print_error_safely(f"\n\033[91m✗\033[0m Sync interrupted ({signal.Signals(signum).name}).")
        rc = 128 + signum
    finally:
        held = _end_stop_signal_hold()
    if held is not None and rc < 128:  # a run the first stop signal ended already exits 128 + it
        _print_error_safely(
            f"\n\033[91m✗\033[0m Sync interrupted ({signal.Signals(held).name}) after it ended: "
            "the result above stands."
        )
        return 128 + held
    return rc


def _sync_command(args):
    """sync_command, but for the stop signal it delivers once the run is over."""
    snapshot = None
    backup_root = None
    previous_handlers = None
    interrupted_handlers = None
    release_lock = None
    # True once the sync stands or its undo finished: only then do the pre-sync copy and its
    # marker go. Anything else that ends the run (an error inside an error path) leaves them,
    # so the next sync stops and says where the files from before this one are.
    settled = False
    _OUTPUT_LOST.clear()
    try:
        import os
        import shutil

        dry_run = getattr(args, "dry_run", False)
        do_backup = getattr(args, "backup", True) and not _env_flag("NGARGPARSER_SYNC_NO_BACKUP")
        verify = getattr(args, "verify", True) and not _env_flag("NGARGPARSER_SYNC_NO_VERIFY")
        allow_downgrade = getattr(args, "allow_downgrade", False) or _env_flag("NGARGPARSER_SYNC_ALLOW_DOWNGRADE")

        # A build or deployed copy has every sign of a tool, but the next build replaces
        # it (or it is what the host runs), so syncing it is never what was meant.
        if os.path.exists(_BUILD_MARKER):
            _print_error(_build_copy_message("sync"))
            return 1
        # Check that this is an ngargparser tool: src/ and scripts/ alone match plenty of
        # other projects, whose Makefile and README.md sync would otherwise overwrite. In a
        # subfolder of a tool (its src/), say where the tool is instead.
        markers = _tool_markers()
        if not markers:
            _print_error(_not_a_tool_message("sync"))
            return 1
        # A folder inside a tool that looks like one (a copy sync saved under
        # .ngargparser/sync-backups/<run>/) is not a tool of its own: syncing it would overwrite that copy.
        outer = _enclosing_tool(above=True)
        if outer:
            import shlex

            _print_error(
                f"\033[91m✗\033[0m This folder is inside the ngargparser tool {outer[0]} (it has {outer[1]}), so it "
                f"is not a tool of its own. Nothing was changed. Run cli sync in the tool's folder: "
                f"cd {shlex.quote(outer[0])}"
            )
            return 1
        # A tool's code lives in src/; scripts/core/ is created below when it is missing.
        if not os.path.isdir("src"):
            _print_error(
                f"\033[91m✗\033[0m This folder has {' and '.join(markers)}, but no src/ folder, so it is not a "
                "whole ngargparser tool. Nothing was changed. Run cli sync from the tool's top folder, the one "
                "that holds src/."
            )
            return 1
        # A sync that was killed (kill -9, a power cut) or whose undo did not finish may have
        # left framework files half updated: the copy from before it is still in the tool.
        # Syncing over that, or reading its half-written pyproject.toml, would hide it.
        unfinished = _unfinished_syncs()
        if unfinished:
            _report_leftover_temporary_files(_sync_framework_files())
            _report_unfinished_sync(unfinished)
            return 1
        # A dry run takes no lock, so it can run while a real sync does: its preview would
        # compare files that sync is still changing, and could say there is nothing to do.
        running = _running_syncs() if dry_run else []
        if running:
            pid, started = running[0]
            _print_error(
                f"\033[91m✗\033[0m A cli sync is running in this folder (pid {pid}, started {started}), so a "
                "preview now would compare files it is still changing. Wait for it to finish, then run "
                "cli sync --dry-run again."
            )
            return 1
        # Sync edits pyproject.toml and reads its [tool.ngargparser] table; a file that is
        # not TOML to begin with is the developer's to fix, so say that, not "the stamp".
        problem = _pyproject_toml_problem()
        if problem:
            _print_error(
                f"\033[91m✗\033[0m pyproject.toml is not valid TOML: {problem}; fix it, then run cli sync again. "
                "Nothing was changed."
            )
            return 1
        # Sync adds its ignore rules to .gitignore, which it reads as UTF-8: a file in another
        # encoding is the developer's to save again, said before anything is written.
        problem = _gitignore_problem()
        if problem:
            _print_error(
                f"\033[91m✗\033[0m .gitignore {problem}: save it as UTF-8, then run cli sync again. Nothing was changed."
            )
            return 1

        # Self-upgrade the installed ngargparser, then re-exec so the new code
        # (including __version__ used by the scaffold_version stamp below) takes
        # effect for the rest of this sync. The env-var sentinel breaks recursion.
        # --dry-run must be side-effect-free, and the re-exec drops flags, so it
        # implies --no-upgrade.
        self_upgrade = (
            getattr(args, "upgrade", True) and not dry_run and not os.environ.get("NGARGPARSER_NO_SELF_UPGRADE")
        )
        # A plain sync from a local checkout syncs from it: the latest release
        # would replace the working copy and copy older framework files in.
        checkout = (
            _checkout_skips_upgrade(getattr(args, "ref", "latest"), getattr(args, "dev", False))
            if self_upgrade
            else None
        )
        if checkout:
            self_upgrade = False
            print(
                f"ℹ Using your local checkout at {checkout} (no upgrade). "
                "To install the latest release first: cli upgrade, then cli sync."
            )
        if self_upgrade:
            if getattr(args, "dev", False):
                print("ℹ Dev mode: pulling from master.")
            plain_latest = _is_plain_latest(getattr(args, "ref", "latest"), getattr(args, "dev", False))
            # A lookup that failed (no network or VPN, credentials, git missing) is not a remote
            # without release tags: resolving it to master would install unreleased code. Sync
            # from the installed version instead. A remote with no release tags still gets master.
            latest_tag = _UNSET
            if plain_latest:
                try:
                    latest_tag = _latest_release_tag(BASE_REPO_URL[len("git+") :], strict=True)
                except _RemoteUnavailable:
                    print(
                        "⚠ Couldn't check for a newer ngargparser (GitLab unreachable: network/VPN, credentials, "
                        f"or git missing); syncing from the installed version {__version__}."
                    )
                    self_upgrade = False
        if self_upgrade:
            url, resolved = _resolve_upgrade_url(
                getattr(args, "ref", "latest"), getattr(args, "dev", False), latest_tag=latest_tag
            )
            if plain_latest and latest_tag is None:
                print("⚠ No semver tags on remote; 'latest' resolves to master.")
            # The latest release can be older than the installed ngargparser (a pre-release or a
            # checkout build): installing it would be a downgrade, so keep what is installed and
            # sync from it. A version named with --ref or --dev is installed as asked.
            if plain_latest and _installed_is_newer(resolved):
                print(
                    f"ℹ Installed ngargparser {__version__} is newer than the latest release {resolved}; not changing it."
                )
                self_upgrade = False
            elif plain_latest and _installed_is_latest(resolved):
                # Reinstalling the same release every run changes nothing and costs a download.
                print(f"ℹ Installed ngargparser {__version__} is the latest release ({resolved}); not reinstalling it.")
                self_upgrade = False
        if self_upgrade:
            import sys

            if resolved.startswith("local:"):
                ok, msg = _is_ngargparser_checkout(url)
                if not ok:
                    _print_error(f"\033[91m✗\033[0m {msg}")
                    return 1
                print(f"ℹ Installing ngargparser from local checkout: {url} …")
            else:
                if resolved and resolved != "master":
                    print(f"ℹ Latest release tag: {resolved}")
                print(f"ℹ Upgrading ngargparser ({url}) …")
            rc = _run_self_upgrade(url)
            if rc:
                _print_error("  Then re-run:       cli s --no-upgrade")
                return rc

            # On re-exec, pass only the bare `s` subcommand. The sentinel env
            # var skips the upgrade block, and any --ref/--no-upgrade flags
            # would break compatibility with older ngargparser versions whose
            # sync subparser doesn't recognize them.
            env = {**os.environ, "NGARGPARSER_NO_SELF_UPGRADE": "1"}
            if not verify:  # the bare re-exec drops --no-verify, so carry it in the environment
                env["NGARGPARSER_SYNC_NO_VERIFY"] = "1"
            if not do_backup:  # ... and --no-backup the same way
                env["NGARGPARSER_SYNC_NO_BACKUP"] = "1"
            if allow_downgrade:  # ... and --allow-downgrade the same way
                env["NGARGPARSER_SYNC_ALLOW_DOWNGRADE"] = "1"
            os.execvpe(sys.argv[0], [sys.argv[0], "s"], env)

        # From here on, SIGTERM and SIGHUP (a kill, a dropped ssh session) undo the sync
        # like Ctrl-C does, and only the first of the three counts: the undo it starts
        # runs to the end. The handlers they had come back in `finally`.
        previous_handlers = _catch_stop_signals()

        if dry_run:
            print("\033[93mDRY RUN\033[0m: showing what would change; no files will be written.")
            if getattr(args, "upgrade", True) and not os.environ.get("NGARGPARSER_NO_SELF_UPGRADE"):
                _report_dry_run_upgrade(getattr(args, "ref", "latest"), getattr(args, "dev", False))

        # What this run stamps: the installed version, with the local label of a checkout
        # (0.4.1+g123abcd), whose files can differ from the 0.4.1 release's.
        version = _sync_version()

        # A project last synced with a newer ngargparser than this one would get older
        # framework files and an older stamp. Refuse unless asked; an unparseable stamp
        # (or version) is not a downgrade. A stamp that is a number (0.5) compares as the
        # version it spells; one of another type is said, and treated like an unparseable one.
        stamp, stamp_problem = _project_scaffold_stamp()
        if stamp_problem:
            print(
                f"\033[93m⚠\033[0m pyproject.toml sets {stamp_problem} under [tool.ngargparser], which is not a "
                "version string, so sync cannot tell which ngargparser last synced this tool; it goes on without "
                "that check."
            )
        synced_with, installed = _version_key(stamp), _version_key(__version__)
        checkout_stop = _checkout_stamp_stop(stamp, version) if synced_with and synced_with == installed else None
        if checkout_stop:
            # The same release number, but the stamp came from a development checkout.
            if not allow_downgrade:
                _print_error(f"\033[91m✗\033[0m {checkout_stop}")
                return 1
            print(
                f"\033[93m⚠\033[0m Downgrading: this tool was last synced from a development checkout of ngargparser "
                f"({stamp}); syncing it to the installed {version} (--allow-downgrade)."
            )
        if synced_with and installed and synced_with > installed:
            if not allow_downgrade:
                _print_error(
                    f"\033[91m✗\033[0m This tool was last synced with ngargparser {stamp}, which is newer than "
                    f"the installed ngargparser {__version__}. Syncing would replace its framework files with older "
                    "ones, so nothing was changed."
                )
                _print_error(
                    "  Upgrade ngargparser first (cli upgrade), then run cli sync again; "
                    f"or sync to {__version__} anyway: cli sync --allow-downgrade"
                )
                return 1
            print(
                f"\033[93m⚠\033[0m Downgrading: this tool was last synced with ngargparser {stamp}; "
                f"syncing it to the installed {__version__} (--allow-downgrade)."
            )
        # Sync reads each file it may change: to compare it with the framework's copy, and to
        # keep the copy that undoes the sync. One it cannot read stops it here, before it
        # writes anything, naming the file and the fix. The hash record has its own warning.
        framework_files = _sync_framework_files()
        unreadable_files = _unreadable_files(
            path for path in _sync_touched_paths(framework_files) if path != _FRAMEWORK_HASHES
        )
        if unreadable_files:
            _report_unreadable_files(unreadable_files)
            return 1
        # A framework file that is a link out of the tool (to a shared copy, say) would get
        # the new framework code written into that other file, and so would any other file
        # this run writes (.gitignore, README.md, .ngargparser/). Stop before writing anything.
        links = _links_outside_tool(framework_files, _sync_writes_beyond_framework(do_backup, version))
        if links:
            _report_links_outside_tool(links, framework_files)
            return 1
        # A folder this run writes into (or a file it edits in place) that is read-only stops it
        # here, before the first write, in one line: found after another file was written, it
        # took an undo and two lines. A dry run says the same, since a real run stops here.
        unwritable = _unwritable_place(framework_files, version)
        if unwritable:
            _print_error(f"\033[91m✗\033[0m {unwritable} Nothing was changed.")
            return 1
        # A build reads scripts/core/build.sh while it runs, and two syncs would undo each
        # other's files: hold the lock make build takes for the rest of the run.
        if not dry_run:
            try:
                release_lock = _take_build_lock()
            except _LockBusy as busy:
                _print_error(_lock_busy_message(busy.holder, busy.owner_unknown))
                return 1
            except _LockUnusable as unusable:
                _print_error(f"\033[91m✗\033[0m {unusable}")
                return 1
            # With the lock held, no other sync runs here: a marker from this host whose
            # folder is still there is one that did not finish, whatever its pid shows, as
            # make build judges it. Checked above too, before the lock, by the pid alone
            # when a build held the lock then.
            unfinished = _unfinished_syncs(lock_held=True)
            if unfinished:
                _report_leftover_temporary_files(framework_files)
                _report_unfinished_sync(unfinished)
                return 1
            # A sync that still runs without the lock (one removed by hand): its files stay as they are.
            running = [(pid, started) for pid, started in _running_syncs() if pid != str(os.getpid())]
            if running:
                pid, started = running[0]
                _print_error(
                    f"\033[91m✗\033[0m A cli sync is running in this folder (pid {pid}, started {started}). "
                    "Nothing was changed. Wait for it to finish, then run cli sync again."
                )
                return 1
        _report_leftover_temporary_files(framework_files)
        print("Synchronizing framework files to latest version...")

        # Get the project name from current directory
        project_name = os.path.basename(os.getcwd())
        print(f"Tool: {project_name}")
        # A timeout for the help screens that is set but cannot be used is said once, up front: in a
        # dry run too, which runs no help screen, so the real run that follows is not the first to say it.
        timeout_problem = _help_screen_timeout()[1]
        if verify and timeout_problem:
            print(f"\033[93m⚠\033[0m {timeout_problem}.")

        # Safety net 1: project code this framework version is known to break
        # stops the sync before anything is written. A dry run reports it, goes on
        # with the preview, and exits 1 at the end like the real run would, unless the
        # help screens decide it in a real run (which a dry run cannot run): then it
        # says so and exits as the rest of the preview has it. A real
        # run first drops a finding the tool's own help screens prove false (see
        # _drop_clashes_the_tool_runs_past). The check reads the code, so a finding can
        # be false (a declaration under `if False:`): when every help screen passes
        # before the sync, a real run goes ahead and lets the test drive after the
        # update decide; a screen that fails then rolls the sync back, and the clash is
        # named as the likely cause. A dry run runs no project code, so it keeps the
        # finding and says what a real run does.
        clash_found = False
        clash_may_pass = False
        clash_baseline = None
        deferred_clashes: list[tuple[_KnownClash, list[dict[str, Any]]]] = []
        if verify:
            unreadable: list[str] = []
            clashes = _find_known_clashes(unreadable)
            if clashes and not dry_run:
                clashes, clash_baseline = _drop_clashes_the_tool_runs_past(clashes, stamp)
            if clashes and not dry_run:
                if clash_baseline is None:
                    _announce_test_drive("before")
                    clash_baseline = _smoke_check()
                if _every_help_screen_passes(clash_baseline):
                    if _framework_changes_pending(framework_files):
                        deferred_clashes, clashes = clashes, []
                        _report_possible_clashes(deferred_clashes)
                    else:
                        # The tool already has this version's framework files, and runs.
                        for _entry, findings in clashes:
                            for f in findings:
                                print(
                                    f"ℹ Not a clash here: {_clash_place(f)} {f['what']}, and every help screen "
                                    "passes on this tool's framework files, which are this version's already."
                                )
                        clashes = []
            if clashes:
                plan = _test_drive_plan() if dry_run else None
                clash_may_pass = bool(plan and plan["script"] and plan["python"])
                _report_known_clashes(
                    clashes,
                    dry_run=dry_run,
                    may_pass=clash_may_pass,
                    why=None if dry_run else _why_help_screens_kept_clashes(clash_baseline),
                )
                if not dry_run:
                    return 1
                clash_found = True
            for path in dict.fromkeys(unreadable):
                print(
                    f"\033[93m⚠\033[0m Could not check {path} for known clashes (it does not parse with this Python)."
                )
        else:
            print("\033[93m⚠\033[0m Safety checks are off for this run (--no-verify or NGARGPARSER_SYNC_NO_VERIFY).")

        # Safety nets 2 and 3: note which of the project's help screens work now,
        # and keep a pre-image of every file about to be touched. The screens are
        # run again once the files are in place; see "Test drive" below.
        # A test drive that cannot run because of something the tool can fix (no .venv,
        # an entry the build refuses) stops a sync that would change framework files,
        # before anything is written: without it nothing shows the update broke the tool.
        baseline = None
        test_drive_blocked = False
        # A record that does not parse (merge conflict markers, say) cannot tell a local
        # edit from an older framework version: said below, rather than read as empty.
        hashes_problem = _framework_hashes_problem()
        if not dry_run:
            if verify and _framework_changes_pending(framework_files):
                # The help screens already ran for the clash check above when it needed them.
                baseline = clash_baseline
                if baseline is None:
                    _announce_test_drive("before")
                    baseline = _smoke_check()
                if not baseline["runnable"] and baseline.get("stop"):
                    _print_error(f"\033[91m✗\033[0m {baseline['stop']} Nothing was changed.")
                    return 1
                if baseline.get("timed_out"):
                    # A screen that did not finish before the update cannot show whether the update
                    # breaks it, nor can the screens after it, which did not run.
                    _print_error(
                        f"\033[91m✗\033[0m {baseline['timed_out']} timed out before the update, so sync cannot "
                        f"check that the tool still starts after it. Set {_SMOKE_TIMEOUT_VARIABLE} to the seconds "
                        "each help screen may take, then run cli sync again (or cli sync --no-verify to update "
                        "without the check). Nothing was changed."
                    )
                    return 1
            touched = _sync_touched_paths(framework_files)
            if hashes_problem == "cannot be read":
                # Nor can it be copied. This run does not read it, and writes it only once
                # the sync stands, after any rollback.
                touched = [path for path in touched if path != _FRAMEWORK_HASHES]
            try:
                snapshot = _SyncSnapshot(touched)
            except OSError as e:
                # .ngargparser/ is read-only, or the disk is full: nothing is written yet.
                raise _SyncWriteError(_state_path(e, _SYNC_IN_PROGRESS), e) from e
        elif verify and _framework_changes_pending(framework_files):
            # A dry run runs no tool code, but what stops the real run is known without it.
            blocker = _test_drive_plan()["stop"]
            if blocker:
                _print_error(f"\n\033[91m✗\033[0m A real run would stop here and change nothing. {blocker}")
                _print_error("  The rest of this preview shows what sync would change once it goes ahead.")
                test_drive_blocked = True

        # Backups of any overwritten file go under a timestamped dir of this run's own
        # so an accidental clobber of a hand-edited framework file stays recoverable.
        if do_backup and not dry_run:
            try:
                backup_root = _new_backup_root()
            except OSError as e:
                raise _SyncWriteError(_state_path(e, os.path.join(".ngargparser", "sync-backups")), e) from e

        changes: list[tuple[str, str]] = []

        def about_to_write(*paths):
            # Before the write: a file this run creates is named in the marker (see _SyncSnapshot.wrote).
            if snapshot is not None:
                snapshot.wrote(*paths)

        def wrote(*paths):
            # Once the write is done, inside _StopSignalsHeld with it: the undo puts back only
            # what this run wrote, and moves aside what changed again since (see _SyncSnapshot.written).
            if snapshot is not None:
                snapshot.written(*paths)

        def report(action, label):
            verbs = {
                "created": "Would create" if dry_run else "Created",
                "updated": "Would update" if dry_run else "Updated",
            }
            if action in verbs:
                print(f"  └ {verbs[action]} {label}")
            else:
                print(f"  └ {label} is already up to date")

        # Layout migrations run before the sync loop so build.sh/hooks.sh/build.conf/.distignore
        # sit at their current paths first (see _sync_legacy_moves). Content is preserved
        # exactly. Dry-run lists the moves without making them.
        moves = _sync_legacy_moves()
        for legacy, current in moves:
            note = " (framework-owned scripts now live under scripts/core/)" if legacy == _SYNC_LEGACY_BUILD else ""
            if dry_run:
                print(f"  └ \033[93mWould move\033[0m {legacy} → {current}{note}")
                continue
            about_to_write(legacy, current)
            if os.path.dirname(current):
                os.makedirs(os.path.dirname(current), exist_ok=True)
            with _StopSignalsHeld():
                shutil.move(legacy, current)
                wrote(legacy, current)
            print(f"  └ \033[93mMigrated\033[0m {legacy} → {current}{note}")
        # A legacy file left behind because its new name already exists is never
        # run (a hook), stops the build (build.conf) or is ignored (an old exclusion
        # file); say so rather than leave it.
        moved = {legacy for legacy, _current in moves}
        for legacy, current, effect in (
            *((hook, "scripts/hooks.sh", "The build never runs it") for hook in _SYNC_LEGACY_HOOKS),
            (_SYNC_LEGACY_BUILD_CONF, "scripts/core/build.conf", "The build stops while it exists"),
            *((path, ".distignore", "The build ignores it") for path in _SYNC_LEGACY_DISTIGNORE),
        ):
            if os.path.exists(legacy) and legacy not in moved:
                # build.sh's error for scripts/build.conf says cli sync moves it, which holds only
                # while scripts/core/build.conf is missing; here it takes a hand merge.
                how = " by hand" if legacy == _SYNC_LEGACY_BUILD_CONF else ""
                print(
                    f"  └ \033[93m⚠\033[0m {legacy} was not moved: {current} already exists. "
                    f"{effect}; merge it into {current}{how} and delete it."
                )

        # Framework-owned files (see _sync_framework_files). _sync_file copies
        # only when content differs, backs up any overwrite, and records the
        # action so the summary can count what changed. A file whose hash differs
        # from the one recorded when it was last written has local edits: say they
        # are replaced, and where the old copy is. A real run says so in the
        # summary, once the sync stands: a rollback restores the file and deletes
        # the backup, so naming it earlier would point at a file that is gone.
        # Flushed, as the test drive's announcement is: a reader that quits after the first lines
        # (`cli sync --no-verify | head -1`) is found before the sync stands, as with the test drive.
        print("\nUpdating framework files...", flush=True)
        if hashes_problem:
            if not do_backup:
                kept = "backups are off for this run"
            else:
                kept = "a real run still makes backups" if dry_run else "backups are still made"
            print(
                f"  └ \033[93m⚠\033[0m {_FRAMEWORK_HASHES} {hashes_problem}; local edits to framework files "
                f"cannot be detected this run ({kept})."
            )
        recorded = {} if hashes_problem else _read_framework_hashes()
        local_edits = []  # what to say about each replaced local edit, once the sync stands
        # A dry run did not make the moves above, so it compares the file a real run would move there.
        moved_to = {current: legacy for legacy, current in moves} if dry_run else {}
        for src, dst, executable in framework_files:
            path = moved_to.get(dst, dst)
            edited = dst in recorded and os.path.isfile(path) and _file_sha256(path) != recorded[dst]
            mode_fix = _mode_fix(src, path, executable) if os.path.isfile(path) else None
            with _StopSignalsHeld():
                # Noted in the marker only when it is written: one already up to date is not (see wrote).
                action = _sync_file(
                    src,
                    path,
                    executable=executable,
                    dry_run=dry_run,
                    backup_root=backup_root,
                    changes=changes,
                    before_write=lambda: about_to_write(path),
                )
                if action in ("created", "updated", "mode") and not dry_run:
                    wrote(path)
            if action == "mode" and mode_fix:
                verb = "Would fix" if dry_run else "Fixed"
                print(f"  └ {verb} the file mode of {dst} ({mode_fix[0]:o} → {mode_fix[1]:o})")
            elif action == "mode-denied" and mode_fix:
                print(
                    f"  └ \033[93m⚠\033[0m {dst} has mode {mode_fix[0]:o}, and it is not yours to change. "
                    f"Its owner can fix it: chmod {mode_fix[1]:o} {dst}"
                )
            else:
                report(action, dst)
            if edited and action == "updated":
                if dry_run:
                    print(f"  └ \033[93m⚠\033[0m Would replace local edits in {dst}.")
                elif backup_root:
                    local_edits.append(
                        f"  └ \033[93m⚠\033[0m {dst} had local edits; sync replaced them. "
                        f"Your version is in {os.path.join(backup_root, dst)}."
                    )
                else:
                    local_edits.append(
                        f"  └ \033[93m⚠\033[0m {dst} had local edits; sync replaced them. "
                        "Backups are off for this run, so your version was not kept."
                    )

        files_changed = sum(1 for action, _ in changes if action in ("created", "updated", "mode"))

        # src/core/__init__.py makes src/core the package the framework files import from; make
        # build stops without it. Created empty when missing, never changed when it is there.
        if not os.path.exists(_CORE_PACKAGE_INIT):
            if not dry_run:
                about_to_write(_CORE_PACKAGE_INIT)
                try:
                    os.makedirs(os.path.dirname(_CORE_PACKAGE_INIT), exist_ok=True)
                    with _StopSignalsHeld():
                        _replace_file(_CORE_PACKAGE_INIT, "")  # a new file: the mode open() gives it
                        wrote(_CORE_PACKAGE_INIT)
                except OSError as e:
                    raise _SyncWriteError(_CORE_PACKAGE_INIT, e) from e
            report("created", _CORE_PACKAGE_INIT)
            files_changed += 1

        # deploy/install.sh — user-owned; created only when missing, never overwritten.
        if not os.path.exists("deploy/install.sh"):
            if not dry_run:
                about_to_write("deploy/install.sh")
                os.makedirs("deploy", exist_ok=True)
                # Written whole, through a temporary file (see _replace_file): a write that fails
                # leaves no half-written script, and the undo below puts the tool back.
                template = f"{TEMPLATE_DIR}/deploy/install.sh"
                try:
                    import stat

                    with open(template, encoding="utf-8") as f:
                        script = f.read().replace("{TOOL_NAME}", project_name)
                    # The mode a new file gets under the umask, executable wherever it is
                    # readable when the template is executable (umask 077: 700, not 755).
                    mode = 0o666 & ~_current_umask()
                    if os.stat(template).st_mode & 0o111:
                        mode |= (mode & 0o444) >> 2
                    with _StopSignalsHeld():
                        _replace_file("deploy/install.sh", script, stat.S_IMODE(mode))
                        wrote("deploy/install.sh")
                except OSError as e:
                    raise _SyncWriteError("deploy/install.sh", e) from e
            report("created", "deploy/install.sh (user-owned; created only when missing)")
            files_changed += 1

        # Ensure the project's .gitignore keeps ngargparser runtime artifacts
        # (.ngargparser/, *.bak) out of git. Append-only; older projects created
        # before scaffolds shipped a .gitignore get one here. A write that fails (a full
        # disk) leaves the file whole and undoes the sync, naming the file. Worked out first,
        # so the marker names the file only when it is written (see _SyncSnapshot.wrote).
        try:
            gitignore_action = ensure_gitignore(".gitignore", dry_run=True)
            if gitignore_action != "unchanged" and not dry_run:
                about_to_write(".gitignore")
                with _StopSignalsHeld():
                    gitignore_action = ensure_gitignore(".gitignore")
                    if gitignore_action != "unchanged":
                        wrote(".gitignore")
        except OSError as e:
            if dry_run:
                raise  # it only read the file
            raise _SyncWriteError(".gitignore", e) from e
        if gitignore_action != "unchanged":
            report(gitignore_action, ".gitignore (ngargparser ignore rules)")
            files_changed += 1

        # Advisory: legacy projects without pyproject.toml
        if not os.path.exists("pyproject.toml") and os.path.exists("requirements.txt"):
            print(
                "\n\033[93m⚠\033[0m  This tool still uses 'requirements.txt'. "
                "ngargparser scaffolds now generate 'pyproject.toml' + 'uv.lock'.\n"
                f"   To migrate: copy '{TEMPLATE_DIR}/pyproject.toml.tmpl' to './pyproject.toml', "
                "edit name/dependencies, then run 'uv lock'."
            )

        # Stamp the project with the framework version it was last synced against.
        # Future `cli` commands can read [tool.ngargparser] scaffold_version to
        # dispatch version-specific migrations.
        # Each edit is worked out first, in memory, so a dry run reports what a real
        # run does. A table written in a form the line editor does not change (an
        # inline table, a dotted key, a multi-line string) gets a warning naming the
        # value to set by hand, and the sync goes on.
        stamp_updated = False
        if os.path.exists("pyproject.toml"):
            pyproject_text = _read_pyproject()[0]
            stamp_heading = False
            current_stamp, stamped = _plan_ngargparser_key(pyproject_text, "scaffold_version", version)
            if current_stamp != version:
                stamp_heading = True
                print("\nStamping framework version...")
                if stamped is None:
                    by_hand = _set_by_hand_message("pyproject.toml", "scaffold_version", version, pyproject_text)
                    print(f"  └ \033[93m⚠\033[0m {by_hand}")
                else:
                    stamp_updated = True
                    if current_stamp is None:
                        verb = "Would add" if dry_run else "Added"
                        print(f"  └ {verb} scaffold_version = \033[92m{version}\033[0m to [tool.ngargparser]")
                    else:
                        print(f"  └ scaffold_version: \033[93m{current_stamp}\033[0m → \033[92m{version}\033[0m")
                    if not dry_run:
                        about_to_write("pyproject.toml")
                        try:
                            with _StopSignalsHeld():
                                write_scaffold_version("pyproject.toml", version)
                                wrote("pyproject.toml")
                        except OSError as e:
                            raise _SyncWriteError("pyproject.toml", e) from e
                    pyproject_text = stamped

            # A deployed copy has no .env, so ./configure reads the tool's name from
            # [tool.ngargparser] app_name instead of the install folder (1.0.0).
            # Tools generated before app_name existed get it here, once.
            if _ngargparser_value(pyproject_text, "app_name") is None:
                app_name = _derived_app_name()
                _previous, named = _plan_ngargparser_key(pyproject_text, "app_name", app_name)
                if not stamp_heading:
                    print("\nStamping framework version...")
                if named is None:
                    by_hand = _set_by_hand_message("pyproject.toml", "app_name", app_name, pyproject_text)
                    print(f"  └ \033[93m⚠\033[0m {by_hand}")
                else:
                    stamp_updated = True
                    verb = "Would add" if dry_run else "Added"
                    print(f'  └ {verb} app_name = "\033[92m{app_name}\033[0m" to [tool.ngargparser]')
                    if not dry_run:
                        about_to_write("pyproject.toml")
                        try:
                            with _StopSignalsHeld():
                                write_ngargparser_key("pyproject.toml", "app_name", app_name)
                                wrote("pyproject.toml")
                        except OSError as e:
                            raise _SyncWriteError("pyproject.toml", e) from e

        # README badge → current ngargparser version (green), only in README.md.
        print("\nUpdating README version badges...")
        # A dry run works the change out the same way, and warns about the same README.md.
        # A README.md that cannot be read is skipped with a warning; one that cannot be
        # written (a full disk) undoes the sync, naming the file. A missing one is not created.
        # A README.md that is a link (to the README end users get, say) is left alone: the badge
        # would land in the file it points at.
        readme_updated = False
        if os.path.islink("README.md"):
            print(
                f"  └ \033[93m⚠\033[0m Skipped the README.md badge: README.md is a link to {os.readlink('README.md')}, "
                "and sync does not write the badge through a link."
            )
        else:
            try:
                # Worked out first, so the marker names README.md only when it is written.
                readme_updated = upsert_readme_badge("README.md", __version__, color="green", dry_run=True)
                if readme_updated and not dry_run:
                    about_to_write("README.md")
                    with _StopSignalsHeld():
                        readme_updated = upsert_readme_badge("README.md", __version__, color="green")
                        if readme_updated:
                            wrote("README.md")
            except OSError as e:
                raise _SyncWriteError("README.md", e) from e
            if readme_updated:
                print(f"  └ {'Would update' if dry_run else 'Updated'} README.md badge")
            else:
                print("  └ README.md badge is already current (or no README found)")

        anything_changed = files_changed > 0 or stamp_updated or readme_updated or bool(moves)

        # Test drive: every help screen that worked before the sync must still
        # work now. If one doesn't, undo the whole sync and say why. Output from here on
        # is printed safely: a reader that quit (`cli sync | head`) must not stop the undo.
        verification = None
        if baseline is not None:
            worked_before = [command for command, result in baseline["results"].items() if result[0]]
            if not baseline["runnable"]:
                verification = ("unverified", baseline["reason"])
            elif not worked_before:
                first_error = next(iter(baseline["results"].values()))[1]
                verification = ("unverified", f"it did not start before the sync either ({first_error})")
            else:
                _announce_test_drive("after")
                after = _smoke_check()["results"]
                broken = [command for command in worked_before if not after.get(command, (False, ""))[0]]
                if broken:
                    _print_error_safely(
                        "\n\033[91m✗\033[0m Sync rolled back: these commands worked before the sync and fail after it.\n"
                    )
                    for command in broken:
                        _print_error_safely(f"  {command}")
                        _print_error_safely(f"      {after.get(command, (False, 'did not run'))[1]}")
                    if deferred_clashes:
                        # The known clash the help screens could not rule out before the sync.
                        _print_error_safely("\n  The likely cause is the known clash found before the sync:")
                        _print_known_clash_findings(deferred_clashes, say=_print_error_safely)
                    _print_error_safely()
                    settled = _undo_sync(snapshot, backup_root, say=_print_error_safely)
                    if settled:
                        _print_error_safely("  Fix the cause, or sync without these checks: cli sync --no-verify")
                    return 1
                verification = ("verified", f"{len(worked_before)} of {len(baseline['results'])}")
                for _entry, findings in deferred_clashes:
                    for f in findings:
                        _print_safely(
                            f"ℹ Not a clash here: {f['path']}:{f['first']} {f['what']}, and every help screen "
                            "passes after the sync."
                        )

        # The sync stands (a rollback returned above): record what each framework file now holds.
        # A record that cannot be written (a folder, a file that is not yours) does not undo it.
        if not dry_run:
            # Output that cannot be written undoes the sync until it stands, with the test drive or
            # without: the buffered lines are written now, so a reader that quit is found here.
            _flush_output()
            try:
                # Named in the marker only when the record changes (see _SyncSnapshot.wrote).
                if _framework_hashes_record(framework_files) != _read_framework_hashes():
                    about_to_write(_FRAMEWORK_HASHES)
                    # Like every other write: a stop right after it undoes it too.
                    with _StopSignalsHeld():
                        _write_framework_hashes(framework_files)
                        wrote(_FRAMEWORK_HASHES)
            except (OSError, _SyncWriteError) as failed:
                # The marker could not name it (_SyncWriteError, see wrote), so it was not written.
                cause = failed.__cause__ if isinstance(failed, _SyncWriteError) else failed
                detail = getattr(cause, "strerror", None) or cause
                _print_safely(
                    f"\n\033[93m⚠\033[0m Could not update {_FRAMEWORK_HASHES} ({detail}). The framework "
                    "files are synced, but local edits to them cannot be detected reliably until the record can be "
                    "written: make it a writable file, or delete it."
                )
            # The sync stands: a stop signal from here on neither undoes it nor ends the run with a
            # traceback while it cleans up; it is delivered once the run is over (see sync_command).
            _hold_stop_signals()
            # A backup folder this run made but did not need (nothing was overwritten) goes again.
            if backup_root and os.path.isdir(backup_root) and not os.listdir(backup_root):
                os.rmdir(backup_root)
            if snapshot is not None:  # a real run always has one
                snapshot.remove_new_empty_dirs()
            settled = True

        # Summary. Printed safely: the sync stands, and output that cannot be written (a
        # reader that quit) must not undo it.
        say = _print_safely
        say("\nSynchronization Summary:")
        say(f"  └ Framework files {'to change' if dry_run else 'changed'}: \033[92m{files_changed}\033[0m")
        for line in local_edits:
            say(line)
        if stamp_updated:
            say(f"  └ [tool.ngargparser] stamps: \033[92m{'would update' if dry_run else 'updated'}\033[0m")
        if readme_updated:
            say(f"  └ README badge: \033[92m{'would update' if dry_run else 'updated'}\033[0m")

        if dry_run and test_drive_blocked and not clash_found:
            _print_error_safely(
                "\n\033[91m✗\033[0m DRY RUN: a real run stops before the update, at the check reported above, and "
                "changes nothing. Fix that (or sync with --no-verify) to make the changes listed here."
            )
            return 1
        if dry_run and clash_found:
            if not clash_may_pass:
                _print_error_safely(
                    "\n\033[91m✗\033[0m DRY RUN: a real run stops at the known clash reported above and changes "
                    "nothing. Fix it (or sync with --no-verify) to make the changes listed here."
                )
                return 1
            # A real run lets the help screens decide, and may well go ahead (a finding under
            # `if False:`): the dry run, which cannot run them, does not fail for it.
            say(
                "\n\033[93m⚠\033[0m DRY RUN: a dry run runs no tool code, so it cannot tell whether the known clash "
                "reported above is real. A real run first runs the help screens: if every one passes, it goes "
                "ahead; if one fails, it stops at the clash and changes nothing."
            )
        if dry_run:
            if anything_changed:
                say("\n\033[93mDRY RUN\033[0m: the above changes would be made. Re-run without --dry-run to apply.")
            else:
                say("\n\033[92m✓\033[0m DRY RUN: tool is already up to date; nothing would change.")
        elif anything_changed:
            if backup_root and os.path.isdir(backup_root):
                say(f"  └ Backups of overwritten files: \033[92m{backup_root}\033[0m")
            say("\n\033[92m✓\033[0m Framework synchronization completed successfully!")
            say(f"Your {project_name} tool now has the latest framework files.")
        else:
            say("\n\033[93m⚠\033[0m No files needed updating - your tool is already up to date!")

        if verification and verification[0] == "verified":
            say(
                f"\033[92m✓\033[0m Verified: every help screen that worked before the sync still works ({verification[1]})."
            )
        elif verification:
            _print_error_safely(f"\033[93m⚠\033[0m Could not verify that the tool still runs: {verification[1]}.")

        return 0

    except KeyboardInterrupt as e:
        # Ctrl-C, SIGTERM, SIGHUP or SIGQUIT: undo like any other failure, then exit 128 + the
        # signal (130, 143, 129, 131) without a traceback. The output is printed safely: after
        # a dropped ssh session the terminal is gone, and the undo must still run. Another
        # Ctrl-C (pressed again and again, say) is ignored from here to the end of the
        # sync, so the undo and the clean-up after it finish; the handlers come back in `finally`.
        interrupted_handlers = _ignore_stop_signals()
        signum = getattr(e, "signum", None)
        if signum is None:
            _print_error_safely("\n\033[91m✗\033[0m Sync interrupted.")
        else:
            import signal

            _print_error_safely(f"\n\033[91m✗\033[0m Sync interrupted ({signal.Signals(signum).name}).")
        settled = snapshot is None or _undo_sync(snapshot, backup_root, say=_print_error_safely)
        if settled:
            _print_error_safely("Cancelled; nothing changed.")
        return 128 + (signum or 2)

    except Exception as e:
        # Printed safely, like the undo: the error can be the output itself, a pipe whose
        # reader quit (`cli sync | head`, BrokenPipeError), and the undo must still run.
        if isinstance(e, _SyncWriteError) and (snapshot is None or not snapshot.changed()):
            # The write failed before this run changed any file (the pre-sync copy could not
            # be made, say): there is nothing to put back, only this run's own backup folder
            # and the folders it made to remove.
            _print_error_safely(f"\033[91m✗\033[0m {e} Nothing was changed.")
            try:
                if snapshot is not None:
                    snapshot.clean_up(backup_root)
            except OSError:
                pass
            settled = True
            return 1
        if _output_lost_by(e):
            # Its own output failed (`cli sync | head`): said on stderr, with how the sync ended.
            nothing = "" if snapshot is not None else " Nothing was changed."
            _print_error_safely(
                f"\033[91m✗\033[0m cli sync stopped: its output could not be written ({_OUTPUT_LOST[0]}).{nothing}"
            )
        elif isinstance(e, _SyncWriteError):
            _print_error_safely(f"\033[91m✗\033[0m {e}")
        else:
            _print_error_safely(f"\033[91m✗\033[0m Error during synchronization: {e}")
        settled = snapshot is None or _undo_sync(snapshot, backup_root, say=_print_error_safely)
        return 1

    finally:
        # A stop signal while the run cleans up waits until it is over (see sync_command). The note
        # comes first, before any call into Python code, where a signal that came just now acts.
        _STOP_HOLD.setdefault("arrived", [])
        _hold_stop_signals()
        # The pre-sync copy and its marker go only once the sync stands or its undo finished
        # (see `settled`); an undo that did not finish keeps them itself (see _undo_sync).
        if snapshot is not None and settled:
            snapshot.discard()
        if release_lock is not None:
            release_lock()
        if interrupted_handlers:
            _restore_signal_handlers(interrupted_handlers)
        if previous_handlers:
            _restore_signal_handlers(previous_handlers)


def main():
    # _CliParser registers -h/--help itself, here and on every subcommand, so a help
    # screen never waits for the remote (see _HelpAction).
    parser = _CliParser(description="NG Argument Parser Framework")

    # Add version argument
    parser.add_argument("-v", "--version", action=_VersionAction, help="show program's version number and exit")

    subparsers = parser.add_subparsers(dest="command")

    # Create 'generate' sub-command
    startapp_parser = subparsers.add_parser("generate", aliases=["g"], allow_abbrev=True, help="Create a new tool")
    startapp_parser.add_argument(
        "project_name",
        metavar="tool_name",
        type=str,
        help="Name of the tool to create, or 'example' to generate the sample app aa-counter",
    )

    # Create 'deps' sub-command (manages external tool deps in paths.py)
    deps_parser = subparsers.add_parser(
        "deps", aliases=["d"], allow_abbrev=True, help="Manage external tool dependencies declared in paths.py"
    )
    deps_subparsers = deps_parser.add_subparsers(dest="deps_action")
    deps_add = deps_subparsers.add_parser("add", help="Add one or more dependency stubs to paths.py")
    deps_add.add_argument("names", nargs="*", help="Dependency names to add (interactive if omitted)")
    deps_remove = deps_subparsers.add_parser(
        "remove", aliases=["rm"], help="Remove one or more dependency blocks from paths.py"
    )
    deps_remove.add_argument("names", nargs="*", help="Dependency names to remove (interactive if omitted)")
    deps_subparsers.add_parser("list", aliases=["ls"], help="List dependencies declared in paths.py")

    # Create 'sync' sub-command
    sync_parser = subparsers.add_parser(
        "sync",
        aliases=["s"],
        allow_abbrev=True,
        help="Synchronize the framework files of an existing tool to the latest version.",
    )
    sync_parser.add_argument(
        "--no-upgrade",
        dest="upgrade",
        action="store_false",
        default=True,
        help="Skip the self-upgrade step; only sync templates from the currently-installed ngargparser. "
        "A local checkout skips it already.",
    )
    sync_parser.add_argument(
        "-n",
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show what would change without writing any files. Implies --no-upgrade.",
    )
    sync_parser.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        default=True,
        help="Don't back up framework files before overwriting them (backups are on by default).",
    )
    sync_parser.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        default=True,
        help="Skip the safety checks: the known-clash check and the before/after test drive of the tool's help screens.",
    )
    sync_parser.add_argument(
        "--allow-downgrade",
        dest="allow_downgrade",
        action="store_true",
        help="Sync even when the tool was last synced with a newer ngargparser than the installed one, "
        "replacing its framework files with older ones and stamping the older version.",
    )
    sync_parser.add_argument(
        "--ref",
        default="latest",
        help="Git ref to upgrade ngargparser to. 'latest' (default) resolves to the highest semver tag on the remote, falling back to 'master' if no tags exist. Pass a branch/tag/sha (e.g., 'master', 'v0.2.2') to override, or a local directory path (e.g., a checkout of the ngargparser repo) to install from your working tree.",
    )
    sync_parser.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: pull the bleeding-edge tip of 'master' instead of the latest semver tag. Shortcut for --ref master; overrides --ref if both are given.",
    )

    # Create 'upgrade' sub-command (upgrades ngargparser itself; works anywhere)
    upgrade_parser = subparsers.add_parser(
        "upgrade",
        aliases=["u"],
        allow_abbrev=True,
        help="Upgrade ngargparser itself to the latest release tag on GitLab.",
    )
    upgrade_parser.add_argument(
        "--check",
        action="store_true",
        help="Report the installed version vs the latest remote tag without installing anything.",
    )
    upgrade_parser.add_argument(
        "--ref",
        default="latest",
        help="What to upgrade to. 'latest' (default) resolves to the highest semver tag on the remote, falling back to 'master' if no tags exist. Pass a branch/tag/sha (e.g., 'master', 'v0.2.2'), or a local directory ('.' or a path to an ngargparser checkout) to install from your working tree.",
    )
    upgrade_parser.add_argument(
        "--dev",
        action="store_true",
        help="Upgrade to the bleeding-edge tip of 'master' instead of the latest semver tag. Shortcut for --ref master.",
    )

    # Register the update notifier via atexit so it fires no matter how we exit,
    # including argparse's early exits (a `--help`, a usage error), not just after a
    # full command. Registered before parse_args() so those early exits are covered.
    # A help screen makes it read only the cache; `--version` unregisters it and checks live.
    import atexit

    atexit.register(_notify_at_exit)

    args = parser.parse_args()
    global _INVOKED_COMMAND
    _INVOKED_COMMAND = args.command

    # Normalize 'rm' → 'remove' and 'ls' → 'list' for the dispatcher
    if getattr(args, "deps_action", None) == "rm":
        args.deps_action = "remove"
    elif getattr(args, "deps_action", None) == "ls":
        args.deps_action = "list"

    if args.command == "generate" or args.command == "g":
        rc = startapp_command(args) or 0
    elif args.command == "deps" or args.command == "d":
        rc = deps_command(args) or 0
    elif args.command == "sync" or args.command == "s":
        rc = sync_command(args) or 0
    elif args.command == "upgrade" or args.command == "u":
        rc = upgrade_command(args) or 0
    else:
        parser.print_help()  # Print help message if no command is specified
        rc = 0

    return rc  # the update notice fires via the atexit hook registered above


if __name__ == "__main__":
    import sys

    sys.exit(main() or 0)
