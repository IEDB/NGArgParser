"""`cli sync`: the test drive's progress and timeout, src/core/__init__.py, new file modes,
CRLF framework files, a build copy's subfolder, and a framework file it cannot read.

* The test drive printed nothing while it ran 4 help screens with a 60 s timeout each,
  before and after the update. It now says when each run starts, stops a run at its
  first timeout and names that screen, and takes NGARGPARSER_SYNC_HELP_TIMEOUT seconds.
* With src/core/__init__.py missing, sync said "up to date" and make build refused. Sync
  now creates it, empty, as a change the undo takes back.
* A framework file sync created got the template's mode (644, 755) whatever the umask.
* A framework file with CRLF line endings (git's core.autocrlf) counted as a local edit.
* Run in a build copy's subfolder, sync said to cd to the build copy, which it refuses.
* A framework file it could not read gave "Error during synchronization: [Errno 13] ...".
"""

import hashlib
import os
import shutil
import stat
import sys
import time

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
ENTRY = "src/run_demo.py"
needs_permissions = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply"
)


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    monkeypatch.setattr(cli, "_local_version_label", lambda: None, raising=False)


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


def tree_state(root):
    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative:
            continue
        if path.is_dir():
            dirs.add(relative)
        else:
            files[relative] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    return files, dirs


def edit(project, path, note="# a local edit that sync will overwrite\n"):
    target = project / path
    target.write_text(target.read_text() + note)


def slow_screens(project, *, words, seconds, log, when_core_lacks=None):
    """Make the entry script wait `seconds` when its arguments hold one of `words` (and, with
    `when_core_lacks`, only once the core file no longer holds that text: after the update),
    and log each run's arguments to `log`."""
    entry = project / ENTRY
    condition = f"any(w in sys.argv for w in {list(words)!r})"
    if when_core_lacks:
        condition += f" and {when_core_lacks!r} not in open({CORE_FILE!r}).read()"
    entry.write_text(
        "import sys, time\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"if {condition}:\n    time.sleep({seconds})\n" + entry.read_text()
    )


# --- 3. The test drive says what it runs, and stops at the first timeout -------------------------


def test_each_test_drive_says_it_starts(runnable_project, sync_args, strip_ansi, capsys):
    edit(runnable_project, CORE_FILE)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    # The count is not said: the screens are --help and those of the subcommands it lists.
    before = "\nChecking that the tool starts (its help screens) before the update...\n"
    after = "\nChecking that the tool starts (its help screens) after the update...\n"
    assert before in out and after in out
    assert out.index(before) < out.index("Updating framework files...") < out.index(after)
    assert out.index(after) < out.index("✓ Verified")


def test_a_help_screen_that_times_out_ends_that_run_of_screens(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch, tmp_path
):
    log = tmp_path / "screens.log"
    slow_screens(runnable_project, words=["predict"], seconds=5, log=log)
    edit(runnable_project, CORE_FILE)
    monkeypatch.setenv("NGARGPARSER_SYNC_HELP_TIMEOUT", "1")
    started = time.monotonic()

    rc = cli.sync_command(sync_args())

    out, err = (strip_ansi(text) for text in capsys.readouterr())
    line = (
        f"  └ ⚠ {ENTRY} predict --help timed out after 1s; the other 2 help screen(s) were not run. To allow more "
        "time, set NGARGPARSER_SYNC_HELP_TIMEOUT to the seconds each help screen may take.\n"
    )
    assert out.count(line) == 1
    # --help, then predict --help, which timed out: preprocess and postprocess never ran. A screen that
    # times out before the update cannot show what the update does, so the sync stops there.
    assert log.read_text().splitlines() == ["--help", "predict --help"]
    assert rc == 1 and "Nothing was changed." in err
    assert time.monotonic() - started < 30  # one 1 s timeout, not the 60 s default


def test_a_timeout_after_the_update_rolls_the_sync_back(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch, tmp_path
):
    log = tmp_path / "screens.log"
    note = "# a local edit that sync will overwrite\n"
    # Every screen waits once the update replaced the edited core file.
    slow_screens(runnable_project, words=["--help"], seconds=5, log=log, when_core_lacks=note.strip())
    edit(runnable_project, CORE_FILE, note)
    before = tree_state(runnable_project)
    monkeypatch.setenv("NGARGPARSER_SYNC_HELP_TIMEOUT", "1")

    rc = cli.sync_command(sync_args())

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert f"  └ ⚠ {ENTRY} --help timed out after 1s; the other 3 help screen(s) were not run." in out
    assert "✗ Sync rolled back: these commands worked before the sync and fail after it." in out
    assert f"  {ENTRY} --help\n      timed out after 1s\n" in out
    assert f"  {ENTRY} predict --help\n      did not run\n" in out
    assert "  Every file was restored to its state before the sync." in out
    assert log.read_text().splitlines() == [
        "--help",
        "predict --help",
        "preprocess --help",
        "postprocess --help",
        "--help",
    ]
    assert tree_state(runnable_project) == before


def test_a_timeout_that_is_not_a_number_is_said_and_the_default_is_used(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch
):
    edit(runnable_project, CORE_FILE)
    monkeypatch.setenv("NGARGPARSER_SYNC_HELP_TIMEOUT", "soon")

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert (
        "⚠ NGARGPARSER_SYNC_HELP_TIMEOUT=soon is not a number of seconds above 0 and at most 86400, so each help "
        "screen may take 60s.\n"
    ) in out
    assert cli._help_screen_timeout()[0] == cli.SMOKE_TIMEOUT
    monkeypatch.setenv("NGARGPARSER_SYNC_HELP_TIMEOUT", "2.5")
    assert cli._help_screen_timeout() == (2.5, None)


# --- 4. src/core/__init__.py ---------------------------------------------------------------------


def test_a_missing_core_init_is_created_and_the_undo_removes_it(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch
):
    init = runnable_project / "src/core/__init__.py"
    init.unlink()
    before = tree_state(runnable_project)

    assert cli.sync_command(sync_args(dry_run=True)) == 0
    preview = strip_ansi("".join(capsys.readouterr()))
    assert "  └ Would create src/core/__init__.py\n" in preview
    assert "up to date" not in preview.split("Synchronization Summary:")[1]
    assert tree_state(runnable_project) == before

    # An undo after it was created takes it away again.
    real_check, calls = cli._smoke_check, []

    def fails_after_the_update():
        calls.append(1)
        result = real_check()
        if len(calls) == 2:
            result["results"] = {command: (False, "boom") for command in result["results"]}
        return result

    monkeypatch.setattr(cli, "_smoke_check", fails_after_the_update)
    assert cli.sync_command(sync_args()) == 1
    assert "Sync rolled back" in strip_ansi("".join(capsys.readouterr()))
    assert tree_state(runnable_project) == before

    monkeypatch.setattr(cli, "_smoke_check", real_check)
    assert cli.sync_command(sync_args()) == 0
    out = strip_ansi("".join(capsys.readouterr()))
    assert "  └ Created src/core/__init__.py\n" in out
    assert "Framework files changed: 1" in out and "✓ Verified" in out
    assert init.read_bytes() == b""


# --- 5. New files follow the umask; CRLF is not a local edit ---------------------------------------


@needs_permissions
def test_files_sync_creates_follow_the_umask(scaffolded_project, sync_args):
    for path in ("src/core/app_env.py", "scripts/core/build.sh", "configure", "Makefile", "deploy/install.sh"):
        (scaffolded_project / path).unlink()
    previous = os.umask(0o077)
    try:
        assert cli.sync_command(sync_args(verify=False)) == 0
    finally:
        os.umask(previous)

    def mode(path):
        return stat.S_IMODE((scaffolded_project / path).stat().st_mode)

    assert mode("src/core/app_env.py") == 0o600
    assert mode("Makefile") == 0o600
    assert mode("deploy/install.sh") == 0o600  # its template has no execute bit
    assert mode("scripts/core/build.sh") == 0o700  # executable wherever it is readable
    assert mode("configure") == 0o700

    # Control: under umask 022 the same files get 644 and 755.
    (scaffolded_project / "src/core/app_env.py").unlink()
    (scaffolded_project / "configure").unlink()
    previous = os.umask(0o022)
    try:
        assert cli.sync_command(sync_args(verify=False)) == 0
    finally:
        os.umask(previous)
    assert (mode("src/core/app_env.py"), mode("configure")) == (0o644, 0o755)


def test_a_framework_file_with_crlf_endings_is_not_a_local_edit(scaffolded_project, sync_args, strip_ansi, capsys):
    target = scaffolded_project / "src/core/result_writer.py"
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))  # as a checkout with core.autocrlf has it

    assert cli.sync_command(sync_args(dry_run=True, verify=False)) == 0
    preview = strip_ansi(capsys.readouterr().out)
    assert "Would update src/core/result_writer.py" in preview  # its bytes differ, so sync writes it
    assert "local edits" not in preview

    assert cli.sync_command(sync_args(verify=False)) == 0
    assert "local edits" not in strip_ansi(capsys.readouterr().out)

    # Control: a real edit in a CRLF file still counts.
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n") + b"# mine\r\n")
    assert cli.sync_command(sync_args(dry_run=True, verify=False)) == 0
    assert "Would replace local edits in src/core/result_writer.py." in strip_ansi(capsys.readouterr().out)


# --- 6a. A build copy's subfolder; 6c. a file sync cannot read --------------------------------------


def test_a_build_copy_subfolder_is_named_as_a_build_copy(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch
):
    copy = scaffolded_project / "build" / "ng_demo-local"
    shutil.copytree(scaffolded_project, copy, ignore=shutil.ignore_patterns("build"))
    (copy / ".ngargparser-build").write_text("")
    monkeypatch.chdir(copy / "src")

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert (
        f"✗ This folder is inside {copy}, a build or deployed copy of a tool (it has .ngargparser-build), not its "
        f"source. Nothing was changed. Run cli sync in the tool's source folder: cd {scaffolded_project}, then "
        "build again.\n"
    ) in out
    assert "run cli sync there" not in out

    # Control: a subfolder of the source still names the tool to cd to.
    monkeypatch.chdir(scaffolded_project / "src")
    assert cli.sync_command(sync_args()) == 1
    assert f"This folder is inside the ngargparser tool {scaffolded_project}: run cli sync there" in strip_ansi(
        capsys.readouterr().err
    )


@needs_permissions
def test_a_framework_file_sync_cannot_read_is_named_with_the_fix(scaffolded_project, sync_args, strip_ansi, capsys):
    edit(scaffolded_project, "src/core/app_env.py")
    target = scaffolded_project / "src/core/app_env.py"
    target.chmod(0o000)
    try:
        before = tree_state_without(scaffolded_project, target)
        for run in (sync_args(dry_run=True), sync_args(verify=False)):
            assert cli.sync_command(run) == 1
            out = strip_ansi(capsys.readouterr().err)
            assert (
                "✗ Cannot read src/core/app_env.py (Permission denied): sync reads every file it may change, to "
                "compare it with the framework's copy and to keep a copy that undoes the sync. Nothing was "
                "changed. Make it readable, then run cli sync again:\n    chmod u+r src/core/app_env.py\n"
            ) in out
            assert "Error during synchronization" not in out
            assert tree_state_without(scaffolded_project, target) == before
    finally:
        target.chmod(0o644)
    assert cli.sync_command(sync_args(verify=False)) == 0  # readable again, it syncs


def tree_state_without(root, unreadable):
    files, dirs = tree_state_skipping(root, unreadable)
    return files, dirs, stat.S_IMODE(unreadable.stat().st_mode)


def tree_state_skipping(root, skipped):
    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative or path == skipped:
            continue
        if path.is_dir():
            dirs.add(relative)
        else:
            files[relative] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    return files, dirs
