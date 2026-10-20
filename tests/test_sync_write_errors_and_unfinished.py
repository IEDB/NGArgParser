"""`cli sync`: a write that fails, the undo, and a sync that never finished.

The undo puts back only the files the run wrote, so a folder sync never wrote to (a
read-only src/core) no longer turns a clean stop into "Restore did not finish". A run
keeps the files from before it in .ngargparser/sync-in-progress/, with a marker naming
its process, until it ends; a sync killed with kill -9 leaves both, and the next sync
stops and says where they are.

"Nothing changed" is checked against a hash of every file in the project, as in
test_sync_safety.py.
"""

import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import time

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
needs_permissions = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply"
)


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # The tests run from a git checkout; stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None, raising=False)


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


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


def edit(project, path, note="# a local edit that sync will overwrite\n"):
    target = project / path
    target.write_text(target.read_text() + note)
    return target.read_bytes()


# --- 1. A write that fails, and an undo of only what was written ---------------------------


@needs_permissions
def test_a_write_into_a_read_only_folder_is_reported_and_nothing_changes(
    runnable_project, sync_args, strip_ansi, capsys
):
    edit(runnable_project, "src/core/result_writer.py")
    before = tree_state(runnable_project)
    core = runnable_project / "src" / "core"
    core.chmod(0o555)
    try:
        rc = cli.sync_command(sync_args())
    finally:
        core.chmod(0o755)

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert (
        "✗ Could not write src/core/result_writer.py: the folder src/core is read-only (Permission denied). "
        "Nothing was changed.\n"
    ) in out
    assert "Restore did not finish" not in out and "failed-restore" not in out
    assert tree_state(runnable_project) == before  # no backup or pre-sync folder left either


def test_a_write_that_fails_after_another_file_changed_names_it_and_restores_the_other(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch
):
    edit(runnable_project, "src/core/app_env.py")  # written first, then put back
    edit(runnable_project, "scripts/core/build.sh")
    before = tree_state(runnable_project)
    real_write = cli._write_atomically

    def disk_fills_up(src, dst, mode=None):
        # A failure that shows only while it writes (a read-only folder is refused before the first write).
        if dst == "scripts/core/build.sh":
            raise OSError(28, "No space left on device", dst)
        return real_write(src, dst, mode)

    monkeypatch.setattr(cli, "_write_atomically", disk_fills_up)
    rc = cli.sync_command(sync_args())

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert "✗ Could not write scripts/core/build.sh: No space left on device.\n" in out
    assert "  Every file was restored to its state before the sync." in out
    assert "Nothing was changed." not in out and "Restore did not finish" not in out
    assert tree_state(runnable_project) == before


@needs_permissions
def test_the_undo_leaves_alone_a_read_only_folder_the_sync_did_not_write(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch
):
    cli.write_scaffold_version(str(runnable_project / "pyproject.toml"), "0.0.1")
    edit(runnable_project, "scripts/core/build.sh")  # the only framework file sync writes
    before = tree_state(runnable_project)
    monkeypatch.setattr(cli, "write_scaffold_version", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    core = runnable_project / "src" / "core"
    core.chmod(0o555)  # nothing to change there; an undo that put every file back failed here
    try:
        rc = cli.sync_command(sync_args())
    finally:
        core.chmod(0o755)

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert "Error during synchronization: boom" in out
    assert "  Every file was restored to its state before the sync." in out
    assert "Restore did not finish" not in out
    assert tree_state(runnable_project) == before


# --- 4. The pre-sync copy lives in the tool until the sync ends ---------------------------


def test_the_pre_sync_copy_is_kept_in_the_tool_while_the_sync_runs(runnable_project, sync_args, monkeypatch, tmp_path):
    stale = edit(runnable_project, CORE_FILE)
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(system_temp))
    real_check, seen = cli._smoke_check, {}

    def look_while_the_new_files_are_in():
        if seen.get("calls"):  # the second test drive: the new framework files are in place
            folder = runnable_project / ".ngargparser" / "sync-in-progress"
            seen["markers"] = [json.loads(m.read_text()) for m in folder.glob("*.json")]
            seen["copies"] = [p.read_bytes() for p in folder.glob(f"*/{CORE_FILE}")]
        seen["calls"] = seen.get("calls", 0) + 1
        return real_check()

    monkeypatch.setattr(cli, "_smoke_check", look_while_the_new_files_are_in)

    assert cli.sync_command(sync_args()) == 0

    [marker] = seen["markers"]
    assert marker["pid"] == os.getpid() and marker["files"].startswith(".ngargparser/sync-in-progress/")
    assert seen["copies"] == [stale]
    assert not (runnable_project / ".ngargparser" / "sync-in-progress").exists()  # gone once the sync stands
    assert list(system_temp.iterdir()) == []  # nothing in the system temp folder, which a reboot clears


def test_the_link_check_covers_the_pre_sync_folder(scaffolded_project, sync_args, strip_ansi, capsys, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (scaffolded_project / ".ngargparser" / "sync-in-progress").symlink_to(outside)
    edit(scaffolded_project, CORE_FILE)

    assert cli.sync_command(sync_args(verify=False)) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert f".ngargparser/sync-in-progress -> {outside.resolve()}" in out
    assert list(outside.iterdir()) == []


FAKE_VENV_PYTHON = """#!/bin/sh
# Counts the help screens sync runs; from the 5th on (the test drive after the new files are
# in place) it waits while {flag} exists, so the test can kill the sync there.
n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "{count}"
while [ "$n" -ge 5 ] && [ -e "{flag}" ]; do sleep 0.1; done
exec "{python}" "$@"
"""


@pytest.mark.skipif(os.name != "posix", reason="needs kill -9")
def test_a_sync_killed_midway_stops_the_next_one(scaffolded_project, sync_args, strip_ansi, capsys, tmp_path):
    project = scaffolded_project
    count, flag = tmp_path / "help-screens", tmp_path / "hold"
    flag.write_text("")
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    if python.is_symlink():  # never write through a link: it can lead to a shared interpreter
        python.unlink()
    python.write_text(FAKE_VENV_PYTHON.format(count=count, flag=flag, python=sys.executable))
    python.chmod(0o755)
    stale = edit(project, CORE_FILE)
    package_root = os.path.dirname(os.path.dirname(cli.__file__))
    env = {
        **os.environ,
        "PYTHONPATH": package_root,
        "NGARGPARSER_NO_SELF_UPGRADE": "1",
        "NGARGPARSER_NO_UPDATE_CHECK": "1",
        "TMPDIR": str(tmp_path),  # where an older sync kept its copy: inside this test's folder
    }
    code = (
        "import sys\nfrom ngargparser.cli import main\nsys.argv = ['cli', 'sync', '--no-upgrade']\nsys.exit(main())\n"
    )
    sync = subprocess.Popen([sys.executable, "-c", code], cwd=project, env=env, stdout=subprocess.DEVNULL)
    try:
        deadline = time.time() + 60
        while not (count.exists() and count.read_text().strip() == "5") and time.time() < deadline:
            time.sleep(0.05)
        assert count.read_text().strip() == "5", "the sync did not reach its second test drive"
        os.kill(sync.pid, signal.SIGKILL)
    finally:
        sync.wait(timeout=30)
        flag.unlink()
    assert (project / CORE_FILE).read_bytes() != stale  # killed with the new framework file in place
    before = tree_state(project)

    preview_rc = cli.sync_command(sync_args(dry_run=True))
    preview = strip_ansi("".join(capsys.readouterr()))
    real_rc = cli.sync_command(sync_args())
    real = strip_ansi("".join(capsys.readouterr()))

    folder = real.split("The files from before it are in ", 1)[1].split("/. Copy", 1)[0]
    for rc, out in ((preview_rc, preview), (real_rc, real)):
        assert rc == 1, out
        assert f"✗ A previous cli sync (pid {sync.pid}, started " in out
        assert "did not finish, and may have left framework files half updated." in out
        # The files that differ from their copy now, the core file among them; then the commands.
        assert "Copy back the files it changed (or run git checkout -- " in out and f" {CORE_FILE} " in out
        assert "if they are committed), then delete that folder and run cli sync again. From the tool folder:\n" in out
        # One cp for each file it wrote, the core file among them.
        assert f"\n    cp -a {folder}/{CORE_FILE} {CORE_FILE}\n" in out
        assert out.endswith(f"\n    rm -r {folder}\n")
    assert tree_state(project) == before  # neither run changed anything
    assert (project / folder / CORE_FILE).read_bytes() == stale  # the copy to put back by hand

    # Done by hand as the message says, with the commands it prints. The next sync goes on.
    for command in real.split("From the tool folder:\n", 1)[1].splitlines():
        subprocess.run(command.strip(), shell=True, cwd=project, check=True)
    assert (project / CORE_FILE).read_bytes() == stale
    assert not (project / folder).exists()
    assert cli.sync_command(sync_args(verify=False)) == 0
    assert "did not finish" not in strip_ansi("".join(capsys.readouterr()))
    assert not (project / ".ngargparser" / "sync-in-progress").exists()  # its marker went too
