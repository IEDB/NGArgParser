"""`cli sync`: whose unfinished-sync marker it is, and a lock file another user made.

The marker of an unfinished sync named only a pid. A pid the system gave to another
process since (pid 1 always runs; after a reboot any pid can come back) made the
next sync think that sync still ran, so it never reported it. The marker now records
the host, and on Linux the process's start time and the boot it ran in: the same host
with another start time or boot is a sync that ended. A marker from another host,
whose processes cannot be seen from here, is reported with that host's name.

.ngargparser/build.lock made by another user (mode 444, in a group-writable folder)
stopped every sync with "[Errno 13] Permission denied". Sync now opens it read-only,
as flock needs no more, makes a new one readable and writable for everyone, and says
who can fix a lock file it cannot open at all.
"""

import hashlib
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
LOCK = ".ngargparser/build.lock"
FLOCK = shutil.which("flock")  # before the scaffold fixture hides every program from cli's shutil.which
needs_proc = pytest.mark.skipif(
    not os.path.isfile("/proc/sys/kernel/random/boot_id") or not os.path.isfile("/proc/1/stat"),
    reason="needs Linux /proc",
)
needs_flock = pytest.mark.skipif(os.name != "posix" or FLOCK is None, reason="needs the flock command")
needs_permissions = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply"
)


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # The tests run from a git checkout; stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


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


def boot_id():
    with open("/proc/sys/kernel/random/boot_id") as f:
        return f.read().strip()


def start_time(pid):
    with open(f"/proc/{pid}/stat") as f:
        stat_line = f.read()
    return stat_line[stat_line.rindex(")") + 1 :].split()[19]


def leave_marker(project, pid, **record):
    """Leave what a sync killed midway leaves: its pre-sync folder, with a copy of a
    framework file, and its marker, which records `pid` and `record`."""
    name = f"20261001T000000.000000Z-{pid}"
    folder = project / ".ngargparser" / "sync-in-progress" / name
    (folder / "src" / "core").mkdir(parents=True)
    shutil.copy2(project / CORE_FILE, folder / CORE_FILE)
    files = f".ngargparser/sync-in-progress/{name}"
    marker = {"pid": pid, "started": "2026-10-01 00:00:00 UTC", "files": files, **record}
    (folder.parent / f"{name}.json").write_text(json.dumps(marker) + "\n")
    return files


# --- 3. Whose marker it is -------------------------------------------------------------------


@needs_proc
def test_a_marker_whose_pid_is_now_another_process_is_reported(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    here = {"host": socket.gethostname(), "boot_id": boot_id()}
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        # Control: a sync that still runs (its pid, with the start time it has, and holding the
        # build lock, as a real sync does) is not reported; a dry run says it runs.
        files = leave_marker(project, sleeper.pid, start_time=start_time(sleeper.pid), **here)
        lock = project / ".ngargparser" / "build.lock"
        lock.mkdir()
        (lock / "pid").write_text(f"{sleeper.pid}\n")
        assert cli.sync_command(sync_args(dry_run=True)) == 1
        out = strip_ansi(capsys.readouterr().err)
        assert "did not finish" not in out
        assert f"✗ A cli sync is running in this folder (pid {sleeper.pid}, started " in out
        shutil.rmtree(project / ".ngargparser" / "sync-in-progress")
        shutil.rmtree(lock)

        # pid 1 always runs, but not since the time this marker records: that sync ended.
        files = leave_marker(project, 1, start_time=str(int(start_time(1)) + 1), **here)
        before = tree_state(project)
        for run in (sync_args(dry_run=True), sync_args()):
            assert cli.sync_command(run) == 1
            out = strip_ansi(capsys.readouterr().err)
            # The copy left behind matches the tool's file, so nothing needs putting back.
            assert (
                "✗ A previous cli sync (pid 1, started 2026-10-01 00:00:00 UTC) did not finish, and may have left "
                f"framework files half updated. The files from before it are in {files}/. None of the files it "
                "wrote differs from its copy there, so none needs copying back, then delete that folder and run cli "
                f"sync again. From the tool folder:\n    rm -r {files}\n"
            ) in out
        assert tree_state(project) == before
        shutil.rmtree(project / ".ngargparser" / "sync-in-progress")

        # The same pid and start time, in an earlier boot: the computer restarted since.
        files = leave_marker(
            project, sleeper.pid, start_time=start_time(sleeper.pid), host=here["host"], boot_id="an-earlier-boot"
        )
        assert cli.sync_command(sync_args()) == 1
        assert f"A previous cli sync (pid {sleeper.pid}, started " in strip_ansi(capsys.readouterr().err)
    finally:
        sleeper.kill()
        sleeper.wait()


def test_a_marker_from_another_host_is_reported_with_its_name(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    # pid 1 runs here too, which proves nothing about the host the marker came from.
    files = leave_marker(project, 1, host="node7.example")
    core = project / CORE_FILE
    core.write_text(core.read_text() + "# half updated\n")  # differs from the copy left behind
    before = tree_state(project)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert (
        "✗ A cli sync on the host node7.example (pid 1, started 2026-10-01 00:00:00 UTC) is still running there, "
        "or did not finish and may have left framework files half updated. The files from before it are in "
        f"{files}/. If it is not running on node7.example, copy back the files it changed (or run git checkout -- "
        f"{CORE_FILE} if they are committed), then delete that folder and run cli sync again. From the tool folder:\n"
        f"    cp -a {files}/{CORE_FILE} {CORE_FILE}\n    rm -r {files}\n"
    ) in out
    assert tree_state(project) == before


@needs_proc
def test_the_marker_records_the_host_and_the_start_of_the_sync_process(scaffolded_project, sync_args, monkeypatch):
    project = scaffolded_project
    target = project / CORE_FILE
    target.write_text(target.read_text() + "# a local edit\n")
    real_backup_root, seen = cli._new_backup_root, []

    def look_at_the_marker():  # called once the marker is written
        seen.extend(json.loads(m.read_text()) for m in (project / ".ngargparser" / "sync-in-progress").glob("*.json"))
        return real_backup_root()

    monkeypatch.setattr(cli, "_new_backup_root", look_at_the_marker)

    assert cli.sync_command(sync_args(verify=False)) == 0

    [marker] = seen
    assert marker["pid"] == os.getpid() and marker["host"] == socket.gethostname()
    assert (marker["boot_id"], marker["start_time"]) == (boot_id(), start_time(os.getpid()))
    assert not (project / ".ngargparser" / "sync-in-progress").exists()


# --- 4. A lock file another user made ------------------------------------------------------------


@pytest.fixture
def with_flock(monkeypatch):
    """Sync with the flock command on PATH, as on Linux; the scaffold fixture hides every program."""
    monkeypatch.setattr(cli.shutil, "which", lambda cmd: FLOCK if cmd == "flock" else None)


@needs_flock
@needs_permissions
def test_a_lock_file_that_is_only_readable_still_locks(scaffolded_project, with_flock, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()  # a pid that no longer runs
    lock = project / LOCK
    lock.write_text(f"{gone.pid}\n")  # what another user's build left, with that build's pid
    lock.chmod(0o444)
    target = project / CORE_FILE
    target.write_text(target.read_text() + "# a local edit\n")

    # A build holds it: a sync still sees that, through a lock file it may only read.
    # In a session of its own: the sleep flock runs holds the lock too, and goes with the group.
    holder = subprocess.Popen([FLOCK, str(lock), "sleep", "60"], start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while subprocess.run([FLOCK, "-n", str(lock), "true"]).returncode == 0:
            assert time.monotonic() < deadline, "flock never took the lock"
            time.sleep(0.05)
        before = tree_state(project)
        assert cli.sync_command(sync_args(verify=False)) == 1
        out = strip_ansi("".join(capsys.readouterr()))
        # The pid in the file is not running, so no pid is named.
        assert (
            "✗ A build or another sync is running in this folder. Nothing was changed. "
            "Wait for it, then run cli sync again."
        ) in out
        assert tree_state(project) == before
    finally:
        os.killpg(holder.pid, signal.SIGKILL)
        holder.wait()

    assert cli.sync_command(sync_args(verify=False)) == 0
    out = strip_ansi("".join(capsys.readouterr()))
    assert "Permission denied" not in out and f"Updated {CORE_FILE}" in out
    assert stat.S_IMODE(lock.stat().st_mode) == 0o444  # left as its owner made it


@needs_flock
@needs_permissions
def test_a_lock_file_that_cannot_be_opened_says_who_can_fix_it(
    scaffolded_project, with_flock, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    target = project / CORE_FILE
    target.write_text(target.read_text() + "# a local edit\n")
    lock = project / LOCK
    lock.write_text("")
    lock.chmod(0o644)
    before = tree_state(project)
    lock.chmod(0o000)
    try:
        rc = cli.sync_command(sync_args(verify=False))
    finally:
        lock.chmod(0o644)

    assert rc == 1
    assert strip_ansi(capsys.readouterr().err) == (
        "✗ Cannot open .ngargparser/build.lock (Permission denied): ask its owner to run "
        "chmod a+rw .ngargparser/build.lock. Nothing was changed.\n"
    )
    assert tree_state(project) == before

    # No lock file yet, in a folder that is not writable: the folder's owner (here, this user) can fix it.
    lock.unlink()
    state = project / ".ngargparser"
    state.chmod(0o555)
    try:
        rc = cli.sync_command(sync_args(verify=False))
    finally:
        state.chmod(0o755)
    assert rc == 1
    assert strip_ansi(capsys.readouterr().err) == (
        "✗ Cannot create .ngargparser/build.lock (Permission denied): .ngargparser is read-only for you; "
        "run chmod u+rwx .ngargparser. Nothing was changed.\n"
    )


@needs_flock
def test_a_new_lock_file_is_readable_and_writable_for_everyone(scaffolded_project, with_flock, sync_args):
    project = scaffolded_project
    umask = os.umask(0o022)
    try:
        assert cli.sync_command(sync_args(verify=False)) == 0
    finally:
        os.umask(umask)

    lock = project / LOCK
    assert stat.S_IMODE(lock.stat().st_mode) == 0o666  # the next user's build can write its pid there
    assert lock.read_text() == f"{os.getpid()}\n"
