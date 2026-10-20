"""`cli sync` and its build lock and saved copies: a kill while a restore that did not finish
moves the copies aside, a .ngargparser/ that cannot hold the lock, and a check of the lock
that a starting build could take for a holder.

The copies of a restore that did not finish moved to .ngargparser/failed-restore-<time>/
before the marker named that folder, so a kill between the two left no marker that found
them. A tool folder that is read-only, and a lock left behind in a read-only .ngargparser/
(without flock), stopped the sync with a raw [Errno 13]. Asking whether the build lock was
free took the lock for a moment, and a make build that started then stopped with "running".
"""

import json
import os
import shutil
import signal
import subprocess
import sys

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))
FLOCK = shutil.which("flock")  # before the scaffold fixture hides every program from cli's shutil.which
needs_permissions = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply"
)


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def make_stale(project):
    core = project / CORE_FILE
    core.write_text(core.read_text() + "\n# a local edit that sync will overwrite\n")


def dead_pid():
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    return gone.pid


# --- 1. A kill while the copies of a restore that did not finish move aside --------------------

KILL_WHILE_KEEPING = """
import os, signal, sys
from argparse import Namespace
from ngargparser import cli

real_rename = os.rename


def rename(src, dst, *args, **kwargs):
    real_rename(src, dst, *args, **kwargs)
    if os.path.basename(str(dst)).startswith("failed-restore-"):
        os.kill(os.getpid(), signal.SIGKILL)  # the copies have moved, and nothing after that runs


def restore_fails(self, backup_root=None):
    raise OSError(30, "Read-only file system")


def gitignore(path=".gitignore", *, dry_run=False):
    # Needs an update, and the write fails: the sync is undone, after it wrote the core file.
    if dry_run:
        return "updated"
    raise OSError(28, "No space left on device", path)


os.rename = rename
cli._SyncSnapshot.restore = restore_fails
cli.ensure_gitignore = gitignore
cli._local_version_label = lambda: None
sys.exit(cli.sync_command(Namespace(upgrade=False, dev=False, ref="latest", dry_run=False, backup=True, verify=False)))
"""


@pytest.mark.skipif(os.name != "posix", reason="needs kill -9")
def test_a_kill_while_kept_copies_move_aside_still_leaves_them_found(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    make_stale(project)
    env = {**os.environ, "PYTHONPATH": PACKAGE_ROOT, "NGARGPARSER_NO_UPDATE_CHECK": "1"}

    killed = subprocess.run([sys.executable, "-c", KILL_WHILE_KEEPING], cwd=project, env=env, capture_output=True)

    assert killed.returncode == -signal.SIGKILL, killed.stderr
    [kept] = (project / ".ngargparser").glob("failed-restore-*")
    assert (kept / CORE_FILE).is_file()  # the copy from before the sync, moved aside

    assert cli.sync_command(sync_args(verify=False)) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert "did not finish, and may have left framework files half updated." in out
    assert f"The files from before it are in .ngargparser/{kept.name}/." in out
    assert out.endswith(f"    rm -r .ngargparser/{kept.name}\n")
    # Each marker that is left names it, or names nothing that is there.
    for marker in (project / ".ngargparser" / "sync-in-progress").glob("*.json"):
        files = json.loads(marker.read_text())["files"]
        assert files == f".ngargparser/{kept.name}" or not (project / files).exists()


# --- 2. A .ngargparser/ that cannot hold the build lock --------------------------------------------


@needs_permissions
def test_a_read_only_tool_folder_without_a_state_folder_is_said_in_one_line(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    shutil.rmtree(project / ".ngargparser")
    make_stale(project)
    project.chmod(0o555)
    try:
        rc = cli.sync_command(sync_args(verify=False))
    finally:
        project.chmod(0o755)

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert out.endswith(
        "✗ Cannot create .ngargparser (Permission denied), where cli sync takes the build lock: this tool folder "
        "is read-only for you. Ask its owner to make it writable (chmod g+w . for a group that shares it), then "
        "run cli sync again. Nothing was changed.\n"
    )
    assert not (project / ".ngargparser").exists()


@needs_permissions
@pytest.mark.parametrize("left_behind", ["file", "folder"])
def test_a_lock_left_behind_in_a_read_only_state_folder_is_said_in_one_line(
    scaffolded_project, sync_args, strip_ansi, capsys, left_behind
):
    project = scaffolded_project
    make_stale(project)
    state = project / ".ngargparser"
    lock = state / "build.lock"
    pid = dead_pid()
    if left_behind == "file":
        lock.write_text(f"{pid}\n")  # what a build with flock leaves, read here without flock
    else:
        lock.mkdir()
        (lock / "pid").write_text(f"{pid}\n")
    state.chmod(0o555)
    try:
        rc = cli.sync_command(sync_args(verify=False))
    finally:
        state.chmod(0o755)

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert out.endswith(
        f"✗ A build or a cli sync that is no longer running (pid {pid}) left .ngargparser/build.lock, and this "
        "sync could not remove it (Permission denied). Remove .ngargparser/build.lock (or ask its owner to), then "
        "run cli sync again. Nothing was changed.\n"
    )
    assert "Errno" not in out


# --- 3. Asking whether the build lock is free takes no lock ---------------------------------------


@pytest.mark.skipif(FLOCK is None or not os.access("/proc/locks", os.R_OK), reason="needs flock and /proc/locks")
def test_asking_whether_the_build_lock_is_free_takes_no_lock(scaffolded_project, monkeypatch):
    import fcntl

    monkeypatch.setattr(cli.shutil, "which", lambda cmd: FLOCK if cmd == "flock" else None)
    lock = scaffolded_project / ".ngargparser" / "build.lock"
    lock.write_text("")
    taken = []
    real_flock = fcntl.flock

    def flock(fd, operation):
        taken.append(operation)  # a lock taken, even for a moment, is one a starting build finds held
        return real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", flock)
    # A build holding the lock, as build.sh's flock -n 9 holds it.
    holding = (
        "import fcntl, sys, time\n"
        "f = open(sys.argv[1])\n"
        "fcntl.flock(f, fcntl.LOCK_EX)\n"
        "print('held', flush=True)\n"
        "time.sleep(60)\n"
    )
    holder = subprocess.Popen([sys.executable, "-c", holding, str(lock)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline() == "held\n"
        assert cli._build_lock_free() is False
    finally:
        holder.kill()
        holder.wait()
    assert cli._build_lock_free() is True  # and gave it back
    assert taken == []
