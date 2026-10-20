"""`cli sync`: output that cannot be written, and a full disk.

`cli sync | head` closes the pipe while sync runs. The next print raised
BrokenPipeError, the error message raised it again, the undo never ran, and the
record of the unfinished sync was deleted on the way out: the tool kept a half
updated set of framework files, and the next sync said nothing needed updating.
Now the error path prints safely, the undo runs, and the pre-sync copy goes only
once the sync stands or its undo finished.

A full disk (here, a file size limit) cut README.md short and sync went on with
"Skipped updating README badge". Now README.md, .gitignore and pyproject.toml are
written like the framework files, through a temporary file, and a write that fails
undoes the sync and names the file.

"Nothing changed" is checked against a hash of every file in the project, as in
test_sync_safety.py.
"""

import hashlib
import io
import os
import re
import stat
import subprocess
import sys

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
RESULT_WRITER = "src/core/result_writer.py"
LOCK = ".ngargparser/build.lock"
PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # The tests run from a git checkout; stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def tree_state(root, leave_out=(LOCK,)):
    """Every file (content hash + mode) and every directory under `root`, but `leave_out`:
    a sync with flock leaves the lock file it held, with its pid, as make build does."""
    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative or relative in leave_out:
            continue
        if path.is_dir():
            dirs.add(relative)
        else:
            files[relative] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    return files, dirs


def edit(project, path, note="# a local edit that sync will overwrite\n"):
    target = project / path
    target.write_text(target.read_text() + note)


def child_env():
    return {
        **os.environ,
        "PYTHONPATH": PACKAGE_ROOT,
        "NGARGPARSER_NO_SELF_UPGRADE": "1",
        "NGARGPARSER_NO_UPDATE_CHECK": "1",
        # No bytecode: under a file size limit, Python would write ngargparser's .pyc files cut short.
        "PYTHONDONTWRITEBYTECODE": "1",
    }


# --- 1. A pipe whose reader quit ----------------------------------------------------------

# A child `cli sync` that stops after writing src/core/NGArgumentParser.py until `flag`
# is gone, so the test can close the pipe exactly then. Unbuffered, as
# `PYTHONUNBUFFERED=1 cli sync | head -21` runs it.
HELD_SYNC = """
import os, sys, time
import ngargparser.cli as cli
real = cli._sync_file
def held(src, dst, **kwargs):
    action = real(src, dst, **kwargs)
    if dst == {core!r}:
        sys.stdout.write("held after " + dst + "\\n")
        while os.path.exists({flag!r}):
            time.sleep(0.05)
    return action
cli._sync_file = held
sys.argv = ["cli", "sync", "--no-upgrade", "--no-verify"]
sys.exit(cli.main())
"""


@pytest.mark.skipif(os.name != "posix", reason="needs a POSIX pipe")
def test_a_pipe_closed_midway_undoes_the_sync_and_the_next_sync_redoes_it(
    scaffolded_project, sync_args, strip_ansi, capsys, tmp_path
):
    project = scaffolded_project
    for path in (CORE_FILE, "src/core/core_validators.py", RESULT_WRITER):
        edit(project, path)
    before = tree_state(project)
    flag = tmp_path / "hold"
    flag.write_text("")
    env = {**child_env(), "PYTHONUNBUFFERED": "1"}
    code = HELD_SYNC.format(core=CORE_FILE, flag=str(flag))
    sync = subprocess.Popen(
        [sys.executable, "-c", code], cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        seen = []
        for line in sync.stdout:
            seen.append(line)
            if line.startswith("held after "):
                break
        assert seen and seen[-1].startswith("held after "), "".join(seen)
        sync.stdout.close()  # the reader quits, as head does after its lines
    finally:
        flag.unlink()
        error = sync.stderr.read()
        rc = sync.wait(timeout=120)

    assert rc == 1
    assert "Traceback" not in error and "BrokenPipeError" not in error
    assert strip_ansi(error) == (
        "✗ cli sync stopped: its output could not be written (Broken pipe).\n"
        "  Every file was restored to its state before the sync.\n"
    )
    # NGArgumentParser.py was new and the other two old: put back, and no record or backup left.
    assert tree_state(project) == before
    assert not (project / ".ngargparser" / "sync-in-progress").exists()

    # The next sync does the whole update, instead of "No files needed updating".
    assert cli.sync_command(sync_args(verify=False)) == 0
    out = strip_ansi(capsys.readouterr().out)
    assert f"Updated {CORE_FILE}" in out and f"Updated {RESULT_WRITER}" in out
    assert "No files needed updating" not in out


class _OutputThatBreaks(io.StringIO):
    """A stdout whose reader quits once `after` has been written: every write from then
    on raises BrokenPipeError, as a pipe's does."""

    def __init__(self, after):
        super().__init__()
        self.after = after

    def write(self, text):
        if self.after in self.getvalue():
            raise BrokenPipeError(32, "Broken pipe")
        return super().write(text)

    def fileno(self):
        raise io.UnsupportedOperation("fileno")


def test_output_that_breaks_once_the_sync_stands_does_not_undo_it(scaffolded_project, sync_args, monkeypatch):
    project = scaffolded_project
    edit(project, CORE_FILE)
    pristine = (cli.NGPARSER_DIR / "NGArgumentParser.py").read_bytes()
    output = _OutputThatBreaks(after="Synchronization Summary:")
    monkeypatch.setattr(sys, "stdout", output)

    rc = cli.sync_command(sync_args(verify=False))

    assert rc == 0
    assert "Synchronization Summary:" in output.getvalue()
    assert (project / CORE_FILE).read_bytes() == pristine  # the sync stands
    assert not (project / ".ngargparser" / "sync-in-progress").exists()
    assert cli._read_framework_hashes(str(project))[CORE_FILE] == hashlib.sha256(pristine).hexdigest()


# --- 2. A full disk -------------------------------------------------------------------------

# Bigger than any framework file (scripts/core/build.sh is the biggest), so the size limit
# below lets every other file through and stops only the one this test grows. Worked out
# from their sizes, so it stays bigger as they grow: each line is at least 16 bytes.
PADDING_LINES = max(os.path.getsize(src) for src, _dst, _exe in cli._sync_framework_files()) // 16 + 1000


def pad(path, comment):
    """Make `path` bigger than every framework file, with comment lines."""
    path.write_text(path.read_text() + "".join(f"{comment} padding line {n}\n" for n in range(PADDING_LINES)))


def grow_readme(project):
    readme = project / "README.md"
    readme.write_text(re.sub(r"ngargparser-[^-/\s)]+-green", "ngargparser-0.1-green", readme.read_text(), count=1))
    pad(readme, "")


def grow_gitignore(project):
    gitignore = project / ".gitignore"
    gitignore.write_text(gitignore.read_text().replace("*.bak\n", ""))  # a managed rule sync adds again
    pad(gitignore, "#")


def grow_pyproject(project):
    cli.write_scaffold_version(str(project / "pyproject.toml"), "0.1")  # the stamp sync writes is longer
    pad(project / "pyproject.toml", "#")


def sync_with_a_file_size_limit(folder, limit):
    """Run `cli sync --no-upgrade --no-verify` in `folder` in a child process that may not
    write a file past `limit` bytes, as a disk with only that much room left."""
    code = (
        "import resource, sys\n"
        "from ngargparser.cli import main\n"
        f"resource.setrlimit(resource.RLIMIT_FSIZE, ({limit}, {limit}))\n"
        "sys.argv = ['cli', 'sync', '--no-upgrade', '--no-verify']\n"
        "sys.exit(main())\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code], cwd=folder, env=child_env(), capture_output=True, text=True, timeout=120
    )


@pytest.mark.skipif(sys.platform != "linux", reason="needs RLIMIT_FSIZE as Linux applies it")
@pytest.mark.parametrize(
    "path, grow", [("README.md", grow_readme), (".gitignore", grow_gitignore), ("pyproject.toml", grow_pyproject)]
)
def test_a_full_disk_while_writing_a_file_undoes_the_sync_and_names_the_file(
    scaffolded_project, strip_ansi, path, grow
):
    project = scaffolded_project
    grow(project)
    edit(project, RESULT_WRITER)  # written first, then put back
    (project / LOCK).parent.mkdir(exist_ok=True)
    before = tree_state(project)
    limit = (project / path).stat().st_size + 1  # the old file fits; the longer new one does not
    assert limit > max(os.path.getsize(src) for src, _dst, _exe in cli._sync_framework_files())

    done = sync_with_a_file_size_limit(project, limit)

    out = strip_ansi(done.stderr)
    assert done.returncode == 1, done.stdout + out
    assert f"✗ Could not write {path}: File too large.\n" in out
    assert "  Every file was restored to its state before the sync." in out
    assert "Skipped updating README badge" not in done.stdout + out and "Error during synchronization" not in out
    assert tree_state(project) == before  # the file is whole: it was cut to `limit` bytes
    assert [p.name for p in project.iterdir() if p.name.endswith(".tmp")] == []  # no temporary file left
