"""`cli sync`: a help screen that times out before the update, a live sync's files, an undo on a
full disk, a folder inside a tool, and a .gitignore that is not UTF-8.

* A help screen that timed out before the update turned the check off: the sync went ahead and
  exited 0 even when the update broke the tool. It now stops, naming
  NGARGPARSER_SYNC_HELP_TIMEOUT, and "Could not verify" goes to stderr.
* A sync that looked at the lock before it listed the markers took a sync that started in
  between for one that did not finish, and printed rm -r for its copy of the files.
* On a nearly full disk the undo could not copy the files back, though they were saved on the
  same file system: it now gives each one a second name (a hard link) instead.
* Run in .ngargparser/sync-backups/<run>/, sync took the saved copy for a tool and synced it.
* A .gitignore that is not UTF-8 made the run write every file, then roll back.
"""

import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
ENTRY = "src/run_demo.py"


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    monkeypatch.setattr(cli, "_local_version_label", lambda: None, raising=False)


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


def tree_state(root):
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            stat.S_IMODE(path.stat().st_mode),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and ".ngargparser" not in path.parts
    }


def edit(project, path):
    target = project / path
    target.write_text(target.read_text() + "# a local edit that sync will overwrite\n")


# --- 1. A help screen that times out before the update stops the sync -------------------------------


def test_a_help_screen_that_times_out_before_the_update_stops_the_sync(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch
):
    entry = runnable_project / ENTRY
    entry.write_text("import sys, time\nif 'predict' in sys.argv:\n    time.sleep(5)\n" + entry.read_text())
    edit(runnable_project, CORE_FILE)
    before = tree_state(runnable_project)
    monkeypatch.setenv("NGARGPARSER_SYNC_HELP_TIMEOUT", "1")

    rc = cli.sync_command(sync_args())

    out, err = (strip_ansi(text) for text in capsys.readouterr())
    assert rc == 1
    assert (
        f"✗ {ENTRY} predict --help timed out before the update, so sync cannot check that the tool still starts "
        "after it. Set NGARGPARSER_SYNC_HELP_TIMEOUT to the seconds each help screen may take, then run cli sync "
        "again (or cli sync --no-verify to update without the check). Nothing was changed.\n"
    ) in err
    assert "Updating framework files" not in out
    assert tree_state(runnable_project) == before


def test_could_not_verify_goes_to_stderr(runnable_project, sync_args, strip_ansi, capsys):
    entry = runnable_project / ENTRY
    entry.write_text("import sys\nsys.exit('it does not start')\n")
    edit(runnable_project, CORE_FILE)

    rc = cli.sync_command(sync_args())

    out, err = (strip_ansi(text) for text in capsys.readouterr())
    assert rc == 0
    assert "⚠ Could not verify that the tool still runs: it did not start before the sync either (" in err
    assert "Could not verify" not in out


# --- 2. A sync that runs is never taken for one that did not finish ---------------------------------


@pytest.fixture
def live_sync(scaffolded_project):
    """A process that stands in for a cli sync that runs: its marker (with its start time),
    its folder and the .partial folder of a copy it is making."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pid = str(process.pid)
        deadline = time.monotonic() + 5
        while cli._process_start_time(pid) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        folder = os.path.join(cli._SYNC_IN_PROGRESS, f"20261019T000000.000000Z-{pid}")

        def start(take_lock):
            if take_lock:
                os.makedirs(cli._BUILD_LOCK)
                with open(os.path.join(cli._BUILD_LOCK, "pid"), "w") as f:
                    f.write(f"{pid}\n")
            os.makedirs(os.path.join(f"{folder}{cli._PARTIAL_SUFFIX}", "src", "core"), exist_ok=True)
            with open(os.path.join(f"{folder}{cli._PARTIAL_SUFFIX}", CORE_FILE), "w") as f:
                f.write("half a copy")
            os.makedirs(folder, exist_ok=True)
            record = {
                "pid": process.pid,
                "host": cli._host_name(),
                "started": "2026-10-19 00:00:00 UTC",
                "files": folder,
                "written": [],
                "created": [],
            }
            boot, started = cli._boot_id(), cli._process_start_time(pid)
            if boot and started:
                record.update(boot_id=boot, start_time=started)
            with open(f"{folder}.json", "w") as f:
                json.dump(record, f)

        yield start, folder
    finally:
        process.kill()
        process.wait()


needs_proc = pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="needs /proc to tell processes apart")


@needs_proc
def test_a_sync_that_takes_the_lock_while_markers_are_read_is_not_called_unfinished(
    live_sync, sync_args, strip_ansi, capsys, monkeypatch
):
    start, folder = live_sync
    listed = cli._sync_in_progress_records

    def records():
        # The other sync takes the lock and writes its marker after the lock was found free.
        if not os.path.exists(f"{folder}.json"):
            start(take_lock=True)
        return listed()

    monkeypatch.setattr(cli, "_sync_in_progress_records", records)

    rc = cli.sync_command(sync_args(verify=False))

    err = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert "did not finish" not in err and "rm -r" not in err
    assert os.path.isdir(f"{folder}{cli._PARTIAL_SUFFIX}") and os.path.isdir(folder)


@needs_proc
def test_a_running_sync_without_the_lock_gets_no_commands_and_stops_the_run(live_sync, sync_args, strip_ansi, capsys):
    start, folder = live_sync
    start(take_lock=False)  # its lock removed by hand

    rc = cli.sync_command(sync_args(verify=False))

    err = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert "did not finish" not in err and "rm -r" not in err
    assert "✗ A cli sync is running in this folder (pid " in err
    assert "Nothing was changed. Wait for it to finish, then run cli sync again." in err
    assert os.path.isdir(f"{folder}{cli._PARTIAL_SUFFIX}") and os.path.isdir(folder)


# --- 4. An undo on a full disk ------------------------------------------------------------------------


def test_an_undo_puts_files_back_on_a_full_disk(scaffolded_project, monkeypatch):
    path = "Makefile"
    before = (scaffolded_project / path).read_bytes()
    mode = stat.S_IMODE((scaffolded_project / path).stat().st_mode)
    snapshot = cli._SyncSnapshot([path])
    snapshot.wrote(path)
    new = scaffolded_project / "Makefile.new"
    new.write_text("what the sync wrote\n")
    os.replace(new, scaffolded_project / path)
    snapshot.written(path)

    def no_space(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(cli.shutil, "copy2", no_space)
    try:
        snapshot.restore()
    finally:
        monkeypatch.undo()
        snapshot.discard()

    assert (scaffolded_project / path).read_bytes() == before
    assert stat.S_IMODE((scaffolded_project / path).stat().st_mode) == mode
    assert not list(scaffolded_project.glob("Makefile.ngargparser-restore"))


# --- 5. A folder inside a tool ----------------------------------------------------------------------


def test_sync_refuses_a_saved_copy_inside_a_tool(scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch):
    copy = scaffolded_project / ".ngargparser" / "sync-backups" / "20261019T000000.000000Z-1"
    shutil.copytree(scaffolded_project, copy, ignore=shutil.ignore_patterns(".ngargparser"))
    edit(copy, CORE_FILE)
    saved = tree_state(copy)
    monkeypatch.chdir(copy)

    rc = cli.sync_command(sync_args(verify=False))

    err = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert f"✗ This folder is inside the ngargparser tool {scaffolded_project.resolve()} (it has " in err
    assert f"Nothing was changed. Run cli sync in the tool's folder: cd {scaffolded_project.resolve()}\n" in err
    assert tree_state(copy) == saved


# --- 6. A .gitignore that is not UTF-8 ----------------------------------------------------------------


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_gitignore_that_is_not_utf8_stops_the_sync_first(scaffolded_project, sync_args, strip_ansi, capsys, dry_run):
    gitignore = scaffolded_project / ".gitignore"
    gitignore.write_bytes(b"# caf\xe9\n*.log\n")
    edit(scaffolded_project, CORE_FILE)
    before = tree_state(scaffolded_project)

    rc = cli.sync_command(sync_args(verify=False, dry_run=dry_run))

    out, err = (strip_ansi(text) for text in capsys.readouterr())
    assert rc == 1
    assert err == (
        "✗ .gitignore is not UTF-8 text (byte 0xe9 at offset 5): save it as UTF-8, then run cli sync again. "
        "Nothing was changed.\n"
    )
    assert "Updating framework files" not in out
    assert tree_state(scaffolded_project) == before
