"""`cli sync`: each run's own backup folder, SIGTERM and SIGHUP, and links out of the tool.

"Nothing changed" is checked against a hash of every file in the project, as in
test_sync_safety.py.
"""

import hashlib
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
APP_ENV = "src/core/app_env.py"


def tree_state(root):
    """Every file (content hash + mode) and every directory under `root`."""
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


def make_stale(project):
    core = project / CORE_FILE
    pristine = core.read_text()
    core.write_text(pristine + "\n# a local edit that sync will overwrite\n")
    return pristine


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


# --- 1. Each run has its own backup folder --------------------------------------------


class _FrozenDatetime(datetime):
    """datetime whose now() is always the same moment: two syncs "in the same second"."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 18, 9, 0, 0, 123456, tzinfo=timezone.utc)


def test_a_rolled_back_sync_keeps_the_backups_of_an_earlier_sync_in_the_same_second(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch
):
    monkeypatch.setattr(cli, "datetime", _FrozenDatetime)
    app_env = runnable_project / APP_ENV
    edited = app_env.read_text() + "# my edit\n"
    app_env.write_text(edited)

    assert cli.sync_command(sync_args()) == 0
    out = strip_ansi("".join(capsys.readouterr()))
    backup = runnable_project / re.search(r"Your version is in (\S+?)\.?$", out, flags=re.M).group(1)
    assert backup.read_text() == edited

    # The next sync, in the same second, fails and rolls back: deploy/ is now a file.
    shutil.move(str(runnable_project / "deploy"), str(runnable_project / "deploy-moved"))
    (runnable_project / "deploy").write_text("")
    before = tree_state(runnable_project)

    assert cli.sync_command(sync_args()) == 1
    out = strip_ansi("".join(capsys.readouterr()))

    assert "Error during synchronization" in out and "Every file was restored" in out
    assert backup.read_text() == edited  # the earlier sync's backup is still there
    assert tree_state(runnable_project) == before  # and the failed run left no folder of its own


# --- 2. SIGTERM and SIGHUP undo the sync like Ctrl-C --------------------------------------


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_a_stop_signal_during_the_sync_is_rolled_back(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch, signum
):
    make_stale(runnable_project)
    before = tree_state(runnable_project)
    real_check, calls, stores = cli._smoke_check, [], []

    def signalled_second_time():
        calls.append(1)
        if len(calls) == 2:  # the "after" run, once the new framework files are in place
            os.kill(os.getpid(), signum)
        return real_check()

    real_new = cli._new_sync_in_progress

    def recording_new():
        folder, marker = real_new()
        stores.extend([os.path.abspath(folder), os.path.abspath(marker)])
        return folder, marker

    monkeypatch.setattr(cli, "_smoke_check", signalled_second_time)
    monkeypatch.setattr(cli, "_new_sync_in_progress", recording_new)
    received = []

    def outer_handler(number, _frame):  # what the caller had installed before the sync
        received.append(number)

    previous = signal.signal(signum, outer_handler)
    try:
        rc = cli.sync_command(sync_args())
        handler_after = signal.getsignal(signum)
    finally:
        signal.signal(signum, previous)

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 128 + signum
    assert f"Sync interrupted ({signal.Signals(signum).name})" in out
    assert "Every file was restored" in out and "Cancelled; nothing changed." in out
    assert tree_state(runnable_project) == before
    assert stores and not any(os.path.exists(store) for store in stores)  # the snapshot and its marker are gone
    assert handler_after is outer_handler and received == []  # the caller's handler is back


# --- 3. A framework file that leads outside the tool ---------------------------------------


def test_a_framework_file_linked_outside_the_tool_is_refused(
    scaffolded_project, sync_args, strip_ansi, capsys, tmp_path_factory
):
    shared = tmp_path_factory.mktemp("shared") / "app_env.py"
    linked = scaffolded_project / APP_ENV
    shared.write_text(linked.read_text() + "# shared copy, older than the framework\n")
    shared_before = shared.read_bytes()
    linked.unlink()
    linked.symlink_to(shared)
    before = tree_state(scaffolded_project)

    for args in (sync_args(dry_run=True), sync_args(), sync_args(verify=False)):
        assert cli.sync_command(args) == 1
        out = strip_ansi("".join(capsys.readouterr()))
        assert f"{APP_ENV} -> {shared}" in out
        assert "Nothing was changed" in out and "Replace each link with a copy" in out
        assert f"cp {APP_ENV} {APP_ENV}.copy && mv {APP_ENV}.copy {APP_ENV}" in out
        assert shared.read_bytes() == shared_before
        assert tree_state(scaffolded_project) == before


# A link that points to nothing has nothing to copy, so "cp" of it cannot work:
# the fix is to remove it, and sync then creates the file in its place.


def test_a_dangling_link_out_of_the_tool_says_to_remove_it(
    runnable_project, sync_args, strip_ansi, capsys, tmp_path_factory
):
    shared = tmp_path_factory.mktemp("shared")
    gone = shared / "app_env.py"  # never created
    linked = runnable_project / APP_ENV
    linked.unlink()
    linked.symlink_to(gone)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert f"{APP_ENV} -> {gone}" in out
    assert (
        "Nothing was changed. Each link points to nothing: remove it, then run cli sync again, which creates "
        "the file in its place:\n"
        f"    rm {APP_ENV}\n"
    ) in out
    assert "cp " not in out
    assert linked.is_symlink() and not gone.exists()

    # Control: next to a link that leads to a file, each gets its own command.
    core = runnable_project / CORE_FILE
    (shared / "NGArgumentParser.py").write_text(core.read_text() + "# shared\n")
    core.unlink()
    core.symlink_to(shared / "NGArgumentParser.py")

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert (
        "Replace each link with a copy of what it points to, and remove each one that points to nothing (sync "
        "creates the file in its place), then run cli sync again:\n"
        f"    cp {CORE_FILE} {CORE_FILE}.copy && mv {CORE_FILE}.copy {CORE_FILE}\n"
        f"    rm {APP_ENV}\n"
    ) in out

    # The commands work: sync then goes ahead and creates the file.
    for line in out.splitlines():
        if line.startswith(("    cp ", "    rm ")):
            subprocess.run(["sh", "-c", line.strip()], cwd=runnable_project, check=True)
    assert cli.sync_command(sync_args()) == 0
    assert not linked.is_symlink() and linked.read_bytes() == (cli.NGPARSER_DIR / "app_env.py").read_bytes()
    assert not core.is_symlink() and not gone.exists()


def test_a_framework_folder_linked_outside_the_tool_is_refused(
    scaffolded_project, sync_args, strip_ansi, capsys, tmp_path_factory
):
    outside = tmp_path_factory.mktemp("shared") / "core"
    shutil.move(str(scaffolded_project / "src" / "core"), str(outside))
    (scaffolded_project / "src" / "core").symlink_to(outside)
    (outside / "app_env.py").write_text((outside / "app_env.py").read_text() + "# shared\n")
    outside_before = tree_state(outside)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert f"{APP_ENV} -> {outside / 'app_env.py'} (through src/core, a link)" in out
    assert "cp -RL src/core src/core.copy && rm src/core && mv src/core.copy src/core" in out
    assert tree_state(outside) == outside_before
