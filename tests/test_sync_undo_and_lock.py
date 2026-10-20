"""`cli sync`: an undo that cannot put a file back, the folder lock, and .ngargparser itself.

An undo stopped at the first file it could not put back (a test drive that made src/core
read-only), left the rest as the sync wrote them, and said only "Restore did not finish
([Errno 13] ...)": the commands came with the next sync, without the folder to fix. Now it
puts back every file it can, names the read-only folder, and prints the commands at once.

A lock folder that holds no pid made sync say "Wait for it" forever, where make build says
to remove it if nothing runs. Two syncs that both found a stale lock both deleted it and
both went on. The fix hint for a read-only .ngargparser was chmod a+rwx (writable for
everyone), and a .ngargparser that is a file gave "[Errno 17] File exists".
"""

import hashlib
import os
import subprocess
import sys
import time

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))
needs_permissions = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply"
)


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def tree_state(root, skip=()):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.relative_to(root).parts[0] not in skip
    }


def edit(project, path):
    target = project / path
    target.write_text(target.read_text() + "# a local edit that sync will overwrite\n")


# --- 1. An undo that cannot put every file back -------------------------------------------------------

# Runs the help screens with the test interpreter; once the sync has replaced the edited
# framework file, it makes src/core read-only and fails, so the undo cannot write there.
FAKE_PYTHON = """#!/bin/sh
if ! grep -q "a local edit" "{core}"; then chmod 555 "{folder}"; echo "it broke" >&2; exit 1; fi
exec "{python}" "$@"
"""


@needs_permissions
def test_an_undo_puts_back_every_file_it_can_and_prints_the_commands_for_the_rest(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    edit(project, CORE_FILE)
    edit(project, "Makefile")  # outside src/core: the undo can put it back
    makefile = (project / "Makefile").read_bytes()
    before = tree_state(project, skip=(".ngargparser", ".venv"))
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    if python.is_symlink():  # never write through a link: it can lead to a shared interpreter
        python.unlink()
    python.write_text(
        FAKE_PYTHON.format(core=project / CORE_FILE, folder=project / "src" / "core", python=sys.executable)
    )
    python.chmod(0o755)

    try:
        rc = cli.sync_command(sync_args())
    finally:
        (project / "src" / "core").chmod(0o755)

    err = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    # The undo went on past the file it could not put back.
    assert (project / "Makefile").read_bytes() == makefile
    assert (
        f"✗ Restore did not finish. Could not put back {CORE_FILE}: the folder src/core is read-only "
        "(Permission denied). The files before the sync are in .ngargparser/failed-restore-"
    ) in err
    assert "To finish it by hand, make src/core writable (it is read-only for you: chmod u+w src/core" in err
    commands = [
        line.strip() for line in err.split("From the tool folder:\n", 1)[1].splitlines() if line.startswith("    ")
    ]
    assert commands[0].startswith("cp -a .ngargparser/failed-restore-") and commands[0].endswith(f" {CORE_FILE}")
    assert not any("Makefile" in command for command in commands)  # put back already

    for command in commands:
        subprocess.run(command, shell=True, cwd=project, check=True)
    assert tree_state(project, skip=(".ngargparser", ".venv")) == before


# --- 2. The folder lock -------------------------------------------------------------------------------


def test_a_lock_folder_without_a_pid_says_to_remove_it_if_nothing_runs(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    edit(project, CORE_FILE)
    (project / ".ngargparser" / "build.lock").mkdir()

    assert cli.sync_command(sync_args(verify=False)) == 1

    assert strip_ansi(capsys.readouterr().err) == (
        "✗ Another build or a cli sync may be running in this folder (its pid is not known). Nothing was changed. "
        "Wait for it to finish, or remove .ngargparser/build.lock if none is running, then run cli sync again.\n"
    )
    assert (project / ".ngargparser" / "build.lock").is_dir()  # not taken away


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX processes")
def test_a_lock_folder_whose_pid_comes_just_after_names_that_pid(scaffolded_project, sync_args, strip_ansi, capsys):
    import threading

    project = scaffolded_project
    edit(project, CORE_FILE)
    lock = project / ".ngargparser" / "build.lock"
    lock.mkdir()  # a build has just made it, and writes its pid next
    build = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    writer = threading.Timer(0.3, lambda: (lock / "pid").write_text(f"{build.pid}\n"))
    writer.start()
    try:
        rc = cli.sync_command(sync_args(verify=False))
    finally:
        writer.join()
        build.kill()
        build.wait()

    assert rc == 1
    captured = capsys.readouterr()
    busy = (
        f"✗ A build or another sync is running in this folder (pid {build.pid}). Nothing was changed. "
        "Wait for it, then run cli sync again.\n"
    )
    assert busy in strip_ansi(captured.out + captured.err)  # named, not "its pid is not known"
    assert strip_ansi(captured.err) == busy


def test_a_lock_that_goes_and_comes_back_on_both_tries_is_busy_not_a_crash(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch
):
    project = scaffolded_project
    edit(project, CORE_FILE)
    (project / ".ngargparser" / "build.lock").mkdir()  # each mkdir finds it there...
    monkeypatch.setattr(cli, "_settled_lock_holder", lambda _path: None)  # ...and gone when read

    assert cli.sync_command(sync_args(verify=False)) == 1

    captured = capsys.readouterr()
    assert "unreachable" not in captured.out + captured.err and "Error during" not in captured.out + captured.err
    assert strip_ansi(captured.err) == (
        "✗ A build or another sync is running in this folder. Nothing was changed. "
        "Wait for it, then run cli sync again.\n"
    )


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX processes")
def test_a_takeover_folder_a_killed_run_left_does_not_stop_the_takeover(scaffolded_project, sync_args):
    project = scaffolded_project
    edit(project, CORE_FILE)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    lock = project / ".ngargparser" / "build.lock"
    lock.mkdir()
    (lock / "pid").write_text(f"{gone.pid}\n")  # a build that was killed
    takeover = project / ".ngargparser" / "build.lock.takeover"
    takeover.mkdir()  # a run killed while it took a lock over, two minutes ago
    two_minutes_ago = time.time() - 120
    os.utime(takeover, (two_minutes_ago, two_minutes_ago))

    assert cli.sync_command(sync_args(verify=False)) == 0

    assert (project / CORE_FILE).read_bytes() == (cli.NGPARSER_DIR / "NGArgumentParser.py").read_bytes()
    assert [p.name for p in (project / ".ngargparser").iterdir() if p.name.startswith("build.lock")] == []


# Two syncs that both find the lock stale: the check waits a second, as a slow file system can.
RACING_SYNC = """
import sys, time
from argparse import Namespace
from ngargparser import cli
real = cli._pid_alive
def slow(pid):
    alive = real(pid)
    time.sleep(1)
    return alive
cli._pid_alive = slow
cli._local_version_label = lambda: None
sys.exit(cli.sync_command(Namespace(upgrade=False, dev=False, ref="latest", dry_run=False, backup=True, verify=False)))
"""


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX processes")
def test_two_syncs_that_find_a_stale_lock_take_it_over_one_at_a_time(scaffolded_project, strip_ansi):
    project = scaffolded_project
    edit(project, CORE_FILE)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    lock = project / ".ngargparser" / "build.lock"
    lock.mkdir()
    (lock / "pid").write_text(f"{gone.pid}\n")  # a build that was killed
    env = {
        **os.environ,
        "PYTHONPATH": PACKAGE_ROOT,
        "NGARGPARSER_NO_SELF_UPGRADE": "1",
        "NGARGPARSER_NO_UPDATE_CHECK": "1",
    }

    syncs = [
        subprocess.Popen(
            [sys.executable, "-c", RACING_SYNC],
            cwd=project,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [(sync.wait(timeout=120), *sync.communicate()) for sync in syncs]

    went_on = [out for _rc, out, _err in results if "Updating framework files" in out]
    assert len(went_on) == 1, results
    assert sorted(rc for rc, _out, _err in results) == [0, 1], results
    stopped = next(strip_ansi(err) for rc, _out, err in results if rc == 1)
    assert "A build or another sync is running in this folder" in stopped
    assert [p.name for p in (project / ".ngargparser").iterdir() if p.name.startswith("build.lock.")] == []


# --- 3. .ngargparser that cannot hold the lock ------------------------------------------------------------


@needs_permissions
def test_a_read_only_state_folder_of_yours_gets_the_chmod_for_you_only(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    edit(project, CORE_FILE)
    state = project / ".ngargparser"
    state.chmod(0o555)
    try:
        rc = cli.sync_command(sync_args(verify=False))
    finally:
        state.chmod(0o755)

    err = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert err == (
        "✗ Cannot create .ngargparser/build.lock (Permission denied): .ngargparser is read-only for you; "
        "run chmod u+rwx .ngargparser. Nothing was changed.\n"
    )
    assert "a+rwx" not in err


def test_a_state_folder_that_is_someone_elses_gets_the_group_chmod(in_tmp_dir, monkeypatch):
    os.mkdir(".ngargparser")
    real_stat = os.stat
    someone_else = os.stat_result((0o40555, 0, 0, 0, os.getuid() + 1, 0, 0, 0, 0, 0))

    def stat(path, *args, **kwargs):
        return someone_else if path == ".ngargparser" else real_stat(path, *args, **kwargs)

    monkeypatch.setattr(cli.os, "stat", stat)
    message = str(cli._LockUnusable(PermissionError(13, "Permission denied"), created=True))
    assert message == (
        "Cannot create .ngargparser/build.lock (Permission denied): ask the owner of .ngargparser to run "
        "chmod g+rwx .ngargparser, which lets a group you share write there. Nothing was changed."
    )


def test_a_state_folder_that_is_a_file_says_to_move_it_away(scaffolded_project, sync_args, strip_ansi, capsys):
    from argparse import Namespace

    project = scaffolded_project
    edit(project, CORE_FILE)
    os.rename(project / ".ngargparser", project / "state-aside")
    (project / ".ngargparser").write_text("not a folder\n")

    assert cli.sync_command(sync_args(verify=False)) == 1
    expected = (
        "✗ Cannot create .ngargparser/{lock}: .ngargparser is a file, not a folder. Move it away "
        "(mv .ngargparser .ngargparser.old), then run the command again. Nothing was changed.\n"
    )
    captured = capsys.readouterr()
    assert strip_ansi(captured.err) == expected.format(lock="build.lock")
    assert "Errno" not in captured.out + captured.err

    assert cli.deps_command(Namespace(deps_action="add", names=["pepx"])) == 1
    assert strip_ansi(capsys.readouterr().err) == expected.format(lock="paths.lock")
