"""`cli sync` after a sync that was stopped: the commands it gives, a marker whose pid runs
something else, the temporary files a kill leaves, a dry run during a real sync, and a
.ngargparser/ that is read-only.

A sync deleted its saved copies before its marker, so a kill while it cleaned up left a
marker whose folder held only some copies, and the commands the next sync gave put back
only those. The commands also copied back every file the sync might change, so an edit
made after the kill was lost. A marker whose pid now runs another process was taken for
a sync that still ran, while make build called it unfinished.
"""

import hashlib
import json
import os
import shlex
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
OTHER_FILE = "src/core/app_env.py"
PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))
FLOCK = shutil.which("flock")  # before the scaffold fixture hides every program from cli's shutil.which
needs_kill = pytest.mark.skipif(os.name != "posix", reason="needs kill -9")
needs_permissions = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply"
)


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # Generate and sync stamp the same plain version, so pyproject.toml needs no new stamp.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def tree_state(root, skip=()):
    """Every file (content hash + mode) and every directory under `root`, but those under `skip`."""
    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative or relative.split("/")[0] in skip:
            continue
        if path.is_dir():
            dirs.add(relative)
        else:
            files[relative] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    return files, dirs


def make_stale(project, path, note="# a local edit that sync will overwrite\n"):
    target = project / path
    target.write_text(target.read_text() + note)
    return target.read_bytes()


def subprocess_env(**extra):
    return {
        **os.environ,
        "PYTHONPATH": PACKAGE_ROOT,
        "NGARGPARSER_NO_SELF_UPGRADE": "1",
        "NGARGPARSER_NO_UPDATE_CHECK": "1",
        **extra,
    }


def commands_after(out):
    """The commands the unfinished-sync message ends with (the indented lines after it)."""
    commands = out.split("From the tool folder:\n", 1)[1].splitlines()
    assert commands and all(command.startswith("    ") for command in commands), out
    return [command.strip() for command in commands]


def run_commands(project, commands):
    for command in commands:
        subprocess.run(command, shell=True, cwd=project, check=True)


# --- 1. A kill while a sync that finished deletes its saved copies ------------------------------

KILL_WHILE_DISCARDING = """
import os, shutil, signal, sys
from argparse import Namespace
from ngargparser import cli

real = shutil.rmtree


def rmtree(path, *args, **kwargs):
    # The saved copies of a sync that stands: delete one of them, then stop as kill -9 does.
    name = os.path.basename(str(path))
    if "sync-in-progress" in str(path) and not name.endswith((".partial", ".restore")):
        for root, _dirs, files in os.walk(path):
            if files:
                os.remove(os.path.join(root, files[0]))
                break
        os.kill(os.getpid(), signal.SIGKILL)
    return real(path, *args, **kwargs)


shutil.rmtree = rmtree
cli._local_version_label = lambda: None  # stamp the plain version, as the scaffold did
sys.exit(cli.sync_command(Namespace(upgrade=False, dev=False, ref="latest", dry_run=False, backup=True, verify=False)))
"""


@needs_kill
def test_a_kill_while_a_finished_sync_cleans_up_leaves_a_sync_that_finished(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    make_stale(project, CORE_FILE)
    make_stale(project, OTHER_FILE)

    killed = subprocess.run(
        [sys.executable, "-c", KILL_WHILE_DISCARDING], cwd=project, env=subprocess_env(), capture_output=True
    )

    assert killed.returncode == -signal.SIGKILL, killed.stderr
    folders = [p for p in (project / ".ngargparser" / "sync-in-progress").iterdir() if p.is_dir()]
    assert len(folders) == 1  # killed with some of the copies still there
    after_kill = tree_state(project, skip=(".ngargparser",))

    # The sync stood: the next one finds nothing unfinished, changes nothing, and removes the folder.
    assert cli.sync_command(sync_args(verify=False)) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "did not finish" not in out
    assert "No files needed updating" in out
    assert tree_state(project, skip=(".ngargparser",)) == after_kill
    assert not (project / ".ngargparser" / "sync-in-progress").exists()


# --- 2. The commands put back only the files the sync wrote --------------------------------------

FAKE_VENV_PYTHON = """#!/bin/sh
# Counts the help screens sync runs; from the 5th on (the test drive after the new files are
# in place) it waits while {flag} exists, so the test can kill the sync there.
n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "{count}"
while [ "$n" -ge 5 ] && [ -e "{flag}" ]; do sleep 0.1; done
exec "{python}" "$@"
"""


def kill_during_the_second_test_drive(project, tmp_path):
    """Run cli sync in another process and kill -9 it once the new files are in place."""
    count, flag = tmp_path / "help-screens", tmp_path / "hold"
    flag.write_text("")
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    if python.is_symlink():  # never write through a link: it can lead to a shared interpreter
        python.unlink()
    python.write_text(FAKE_VENV_PYTHON.format(count=count, flag=flag, python=sys.executable))
    python.chmod(0o755)
    code = (
        "import sys\nfrom ngargparser import cli\n"
        "cli._local_version_label = lambda: None\n"  # stamp the plain version, as the scaffold did
        "sys.argv = ['cli', 'sync', '--no-upgrade']\nsys.exit(cli.main())\n"
    )
    sync = subprocess.Popen([sys.executable, "-c", code], cwd=project, env=subprocess_env(), stdout=subprocess.DEVNULL)
    try:
        deadline = time.time() + 60
        while not (count.exists() and count.read_text().strip() == "5") and time.time() < deadline:
            time.sleep(0.05)
        assert count.read_text().strip() == "5", "the sync did not reach its second test drive"
        os.kill(sync.pid, signal.SIGKILL)
    finally:
        sync.wait(timeout=30)
        flag.unlink()
    return sync.pid


@needs_kill
def test_the_commands_after_a_kill_keep_edits_made_since(scaffolded_project, sync_args, strip_ansi, capsys, tmp_path):
    project = scaffolded_project
    stale_core = make_stale(project, CORE_FILE)
    stale_other = make_stale(project, OTHER_FILE)
    kill_during_the_second_test_drive(project, tmp_path)
    assert (project / OTHER_FILE).read_bytes() != stale_other  # killed with the new framework files in place
    [marker] = (project / ".ngargparser" / "sync-in-progress").glob("*.json")
    record = json.loads(marker.read_text())
    assert record["written"] == [CORE_FILE, OTHER_FILE]  # only the files it wrote, README.md not among them
    assert set(record["left"]) == {CORE_FILE, OTHER_FILE}
    # After the kill: a note in README.md, which the sync did not write, and an edit to a file it wrote.
    readme = project / "README.md"
    readme.write_text(readme.read_text() + "note\n")
    edited = make_stale(project, CORE_FILE, "# edited after the kill\n")

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    folder = out.split("The files from before it are in ", 1)[1].split("/. ", 1)[0]
    assert (
        f"/. The sync wrote {CORE_FILE}, and it changed since, so the commands leave it as it is: compare it with "
        f"its copy in that folder before you delete it. Copy back the files it changed (or run git checkout -- "
        f"{OTHER_FILE} if they are committed), then delete that folder and run cli sync again. From the tool folder:\n"
    ) in out
    commands = commands_after(out)
    assert commands == [f"cp -a {shlex.quote(folder + '/' + OTHER_FILE)} {OTHER_FILE}", f"rm -r {folder}"]
    assert (project / folder / CORE_FILE).read_bytes() == stale_core  # the copy to compare with, by hand
    run_commands(project, commands)
    assert (project / OTHER_FILE).read_bytes() == stale_other  # put back as it was before the sync
    assert (project / CORE_FILE).read_bytes() == edited  # the edit made after the kill stays
    assert readme.read_text().endswith("note\n")  # so does the note in a file the sync did not write
    assert not (project / folder).exists()


@needs_kill
def test_without_edits_since_the_commands_put_every_written_file_back(
    scaffolded_project, sync_args, strip_ansi, capsys, tmp_path
):
    project = scaffolded_project
    make_stale(project, CORE_FILE)
    make_stale(project, OTHER_FILE)
    before = tree_state(project, skip=(".ngargparser", ".venv"))
    kill_during_the_second_test_drive(project, tmp_path)

    assert cli.sync_command(sync_args()) == 1

    commands = commands_after(strip_ansi("".join(capsys.readouterr())))
    assert [command.split()[0] for command in commands] == ["cp", "cp", "rm"]  # one cp per file it wrote
    run_commands(project, commands)
    assert tree_state(project, skip=(".ngargparser", ".venv")) == before
    assert cli.sync_command(sync_args(verify=False)) == 0
    assert "did not finish" not in strip_ansi("".join(capsys.readouterr()))


# --- 4. A marker whose pid runs something else, while no sync holds the lock ----------------------


def leave_marker(project, pid, **record):
    """What a sync killed midway leaves: its pre-sync folder, with a copy of a framework file,
    and its marker, which records `pid` (and `record`), on this host."""
    name = f"20261001T000000.000000Z-{pid}"
    folder = project / ".ngargparser" / "sync-in-progress" / name
    (folder / "src" / "core").mkdir(parents=True)
    shutil.copy2(project / CORE_FILE, folder / CORE_FILE)
    files = f".ngargparser/sync-in-progress/{name}"
    marker = {"pid": pid, "host": socket.gethostname(), "started": "2026-10-01 00:00:00 UTC", "files": files}
    (folder.parent / f"{name}.json").write_text(json.dumps({**marker, **record}) + "\n")
    return files


@pytest.mark.skipif(not os.path.isdir("/proc/1"), reason="needs a pid 1 that runs")
@pytest.mark.parametrize("dry_run", [False, True])
def test_a_marker_whose_pid_runs_now_is_unfinished_when_no_sync_holds_the_lock(
    scaffolded_project, sync_args, strip_ansi, capsys, dry_run
):
    files = leave_marker(scaffolded_project, 1)  # pid 1 always runs, and it is no cli sync
    before = tree_state(scaffolded_project)

    assert cli.sync_command(sync_args(dry_run=dry_run, verify=False)) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert (
        "✗ A previous cli sync (pid 1, started 2026-10-01 00:00:00 UTC) did not finish, and may have left "
        f"framework files half updated. The files from before it are in {files}/."
    ) in out
    assert out.endswith(f"From the tool folder:\n    rm -r {files}\n")
    assert tree_state(scaffolded_project) == before


# --- 6b. A dry run while a real sync runs --------------------------------------------------------


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="reads /proc")
def test_a_dry_run_during_a_real_sync_says_a_sync_is_running(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    syncing = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "cli", "sync"])
    try:
        leave_marker(project, syncing.pid)
        lock = project / ".ngargparser" / "build.lock"
        lock.mkdir()
        (lock / "pid").write_text(f"{syncing.pid}\n")
        before = tree_state(project)

        assert cli.sync_command(sync_args(dry_run=True)) == 1

        out = strip_ansi("".join(capsys.readouterr()))
        assert out == (
            f"✗ A cli sync is running in this folder (pid {syncing.pid}, started 2026-10-01 00:00:00 UTC), so a "
            "preview now would compare files it is still changing. Wait for it to finish, then run cli sync "
            "--dry-run again.\n"
        )
        # A real run finds the lock held, and its marker is not called unfinished: that sync runs.
        assert cli.sync_command(sync_args(verify=False)) == 1
        out = strip_ansi("".join(capsys.readouterr()))
        assert "did not finish" not in out
        assert f"✗ A build or another sync is running in this folder (pid {syncing.pid})." in out
        assert tree_state(project) == before
    finally:
        syncing.kill()
        syncing.wait()


# --- 6a. Temporary files a kill leaves -----------------------------------------------------------


@pytest.mark.parametrize("dry_run", [True, False])
def test_temporary_files_a_stopped_sync_left_are_named(scaffolded_project, sync_args, strip_ansi, capsys, dry_run):
    project = scaffolded_project
    leftovers = [
        "src/core/.app_env.py.k2j3h4g5.ngargparser-sync",  # _write_atomically
        ".pyproject.toml.ab_cd123.tmp",  # _replace_file
        "README.md.ngargparser-restore",  # the undo's _put_back
    ]
    for path in leftovers:
        (project / path).write_text("half written\n")
    (project / "src" / ".paths.py.abcdefgh.tmp").write_text("not a file sync writes\n")  # control

    assert cli.sync_command(sync_args(dry_run=dry_run, verify=False)) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert (
        "⚠ A cli sync that was stopped left these temporary files: .pyproject.toml.ab_cd123.tmp, "
        "README.md.ngargparser-restore, src/core/.app_env.py.k2j3h4g5.ngargparser-sync. Sync does not use them: "
        "once you have checked them, they are safe to delete.\n"
    ) in out
    assert ".paths.py" not in out
    assert all((project / path).exists() for path in leftovers)  # named, not deleted


# --- 6c. A .ngargparser/ that is read-only -------------------------------------------------------


@needs_permissions
@pytest.mark.skipif(FLOCK is None, reason="needs the flock command")
def test_a_read_only_state_folder_stops_the_sync_in_one_line(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch
):
    monkeypatch.setattr(cli.shutil, "which", lambda cmd: FLOCK if cmd == "flock" else None)
    project = scaffolded_project
    make_stale(project, CORE_FILE)
    state = project / ".ngargparser"
    (state / "build.lock").write_text("")  # what a build leaves: the lock can still be taken
    before = tree_state(project)
    state.chmod(0o555)
    try:
        rc = cli.sync_command(sync_args(verify=False))
    finally:
        state.chmod(0o755)

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert out.endswith(
        "✗ Could not write .ngargparser/sync-in-progress: the folder .ngargparser is read-only (Permission denied). "
        "Nothing was changed.\n"
    )
    assert "Errno" not in out
    after = tree_state(project)
    after[0][".ngargparser/build.lock"] = before[0][".ngargparser/build.lock"]  # the pid the lock holder writes
    assert after == before


@needs_permissions
def test_a_read_only_state_folder_without_flock_names_who_can_fix_it(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    make_stale(project, CORE_FILE)
    state = project / ".ngargparser"
    before = tree_state(project)
    state.chmod(0o555)
    try:
        rc = cli.sync_command(sync_args(verify=False))
    finally:
        state.chmod(0o755)

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert out.endswith(
        "✗ Cannot create .ngargparser/build.lock (Permission denied): .ngargparser is read-only for you; "
        "run chmod u+rwx .ngargparser. Nothing was changed.\n"
    )
    assert tree_state(project) == before
