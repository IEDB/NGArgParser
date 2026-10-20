"""`cli sync`: a test drive that cannot pick the entry script, and putting a killed sync back.

With no `entry` in pyproject.toml and several src/run_*.py (or none), sync wrote the new
framework files, said "completed successfully" and only warned that it could not verify
the tool, which could then fail to start. It now stops before writing, as it does without
a .venv, and says to set `entry`.

A sync killed with kill -9 leaves the files from before it. The next sync said to copy
them back, with no command, and its `git checkout` named src/core, scripts/core, Makefile
and configure only: not pyproject.toml, README.md or .gitignore, which sync edits too, and
not the files the sync created, which copying back leaves behind. It now prints the
commands, names each file that differs from its copy, and names the files the sync
created (recorded in the marker as they are written) to delete.

A marker that does not parse, whose pid now belongs to another live process, was taken
for a running sync forever. It is now reported, unless that process runs cli sync.
"""

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
needs_proc = pytest.mark.skipif(not os.path.isfile("/proc/self/cmdline"), reason="needs Linux /proc")


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # The tests run from a git checkout; stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None, raising=False)


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


def tree_state(root, skip=()):
    """Every file (content hash + mode) and every directory under `root`, but `skip` and what is under it."""
    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative or any(relative == s or relative.startswith(f"{s}/") for s in skip):
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


def without_entry(project):
    pyproject = project / "pyproject.toml"
    pyproject.write_text(
        "".join(line for line in pyproject.read_text().splitlines(True) if not line.startswith("entry"))
    )


ENTRY_STOP = (
    'Set entry = "src/run_<name>.py" under [tool.ngargparser] in pyproject.toml, then run cli sync again '
    "(or cli sync --no-verify to update without the check)."
)


# --- 1. No entry, and not exactly one src/run_*.py ----------------------------------------


def test_several_entry_scripts_and_no_entry_stop_the_sync_before_it_writes(
    runnable_project, sync_args, strip_ansi, capsys
):
    without_entry(runnable_project)
    (runnable_project / "src/run_other.py").write_text("raise SystemExit(0)\n")
    # Control: nothing to change, so no test drive is needed and nothing stops the sync.
    assert cli.sync_command(sync_args()) == 0
    capsys.readouterr()
    edit(runnable_project, CORE_FILE)
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args())

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert (
        "✗ Sync cannot check that the tool still starts after the update: several src/run_*.py scripts were "
        "found (src/run_demo.py, src/run_other.py), and pyproject.toml names none of them as the entry script. "
        f"{ENTRY_STOP} Nothing was changed.\n"
    ) in out
    assert "completed successfully" not in out and "Could not verify" not in out
    assert tree_state(runnable_project) == before

    # The dry run stops the same way, and still previews the change.
    assert cli.sync_command(sync_args(dry_run=True)) == 1
    preview = strip_ansi("".join(capsys.readouterr()))
    assert "✗ A real run would stop here and change nothing. Sync cannot check that the tool still starts" in preview
    assert f"Would update {CORE_FILE}" in preview
    assert tree_state(runnable_project) == before

    # Naming the entry script lets it go ahead, checked.
    pyproject = runnable_project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace("[tool.ngargparser]\n", '[tool.ngargparser]\nentry = "src/run_demo.py"\n')
    )
    assert cli.sync_command(sync_args()) == 0
    assert "✓ Verified: every help screen that worked before the sync still works" in strip_ansi(
        "".join(capsys.readouterr())
    )


def test_no_entry_script_at_all_stops_the_sync_before_it_writes(runnable_project, sync_args, strip_ansi, capsys):
    without_entry(runnable_project)
    (runnable_project / "src/run_demo.py").rename(runnable_project / "src/main.py")
    edit(runnable_project, CORE_FILE)
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert (
        "✗ Sync cannot check that the tool still starts after the update: no src/run_*.py entry script was "
        f"found, and pyproject.toml names no entry script. {ENTRY_STOP} Nothing was changed.\n"
    ) in out
    assert tree_state(runnable_project) == before
    # --no-verify asks for no check, so it updates without one.
    assert cli.sync_command(sync_args(verify=False)) == 0


# --- 2. Putting a sync killed midway back by hand ---------------------------------------------

FAKE_VENV_PYTHON = """#!/bin/sh
# Counts the help screens sync runs; from the 5th on (the test drive after the new files are
# in place) it waits while {flag} exists, so the test can kill the sync there.
n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "{count}"
while [ "$n" -ge 5 ] && [ -e "{flag}" ]; do sleep 0.1; done
exec "{python}" "$@"
"""


def make_every_kind_of_change(project):
    """Give sync a framework file to replace, a file to create and every file it edits to edit."""
    edit(project, CORE_FILE)
    (project / "deploy/install.sh").unlink()  # sync creates it
    cli.write_scaffold_version(str(project / "pyproject.toml"), "0.0.1")  # sync stamps it
    readme = project / "README.md"
    readme.write_text(re.sub(r"ngargparser-[^-/\s)]+-green", "ngargparser-0.0.1-red", readme.read_text()))
    gitignore = project / ".gitignore"
    gitignore.write_text(gitignore.read_text().replace("setup_*_env.sh\n", ""))  # sync adds the rule back


@pytest.mark.skipif(os.name != "posix", reason="needs kill -9")
def test_a_sync_killed_midway_prints_the_commands_that_put_the_tool_back(
    scaffolded_project, sync_args, strip_ansi, capsys, tmp_path
):
    project = scaffolded_project
    count, flag = tmp_path / "help-screens", tmp_path / "hold"
    flag.write_text("")
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    if python.is_symlink():  # never write through a link: it can lead to a shared interpreter
        python.unlink()
    python.write_text(FAKE_VENV_PYTHON.format(count=count, flag=flag, python=sys.executable))
    python.chmod(0o755)
    make_every_kind_of_change(project)
    before = tree_state(project, skip=(".ngargparser",))
    package_root = os.path.dirname(os.path.dirname(cli.__file__))
    env = {
        **os.environ,
        "PYTHONPATH": package_root,
        "NGARGPARSER_NO_SELF_UPGRADE": "1",
        "NGARGPARSER_NO_UPDATE_CHECK": "1",
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
    assert (project / "deploy/install.sh").is_file()  # killed with every change in place
    [marker] = (project / ".ngargparser" / "sync-in-progress").glob("*.json")
    assert json.loads(marker.read_text())["created"] == ["deploy/install.sh"]  # recorded before it was written

    rc = cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    folder = out.split("The files from before it are in ", 1)[1].split("/. Copy", 1)[0]
    written = [".gitignore", "README.md", "pyproject.toml", CORE_FILE]
    assert (
        f"/. Copy back the files it changed (or run git checkout -- {' '.join(written)} if they are "
        "committed), then delete these files the sync created: deploy/install.sh. Then delete that folder and "
        "run cli sync again. From the tool folder:\n"
        + "".join(f"    cp -a {folder}/{path} {path}\n" for path in written)
        + f"    rm deploy/install.sh\n    rm -r {folder}\n"
    ) in out

    # The printed commands, as printed, put the tool back as it was before the killed sync.
    for command in out.split("From the tool folder:\n", 1)[1].splitlines():
        subprocess.run(command.strip(), shell=True, cwd=project, check=True)
    assert tree_state(project, skip=(".ngargparser",)) == before
    assert not (project / folder).exists()
    assert cli.sync_command(sync_args(verify=False)) == 0
    assert "did not finish" not in strip_ansi(capsys.readouterr().err)


def git(cwd, *args):
    config = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *config, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_git_checkout_it_suggests_restores_every_file_the_sync_edited(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    (project / "deploy/install.sh").unlink()
    git(project, "init", "-q")
    git(project, "add", "-A")
    git(project, "commit", "-qm", "the tool before the sync")
    # What a sync killed after it wrote these leaves: its pre-sync folder and marker, and the files.
    snapshot = cli._SyncSnapshot(cli._sync_touched_paths(cli._sync_framework_files()))
    edited = [CORE_FILE, "pyproject.toml", "README.md", ".gitignore"]
    snapshot.wrote(*edited, "deploy/install.sh", "Makefile")  # the Makefile is not changed
    for path in edited:
        (project / path).write_text((project / path).read_text() + "# half updated\n")
    (project / "deploy/install.sh").write_text("#!/bin/bash\n")

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    checkout = re.search(r"git checkout -- (.*?) if they are committed", out).group(1)
    assert checkout.split() == sorted(edited)  # every edited file, and not the unchanged Makefile
    subprocess.run(f"git checkout -- {checkout}", shell=True, cwd=project, check=True)
    for command in out.splitlines()[-2:]:  # rm deploy/install.sh, rm -r <the folder>
        subprocess.run(command.strip(), shell=True, cwd=project, check=True)
    assert git(project, "status", "--porcelain") == ""  # as committed; .ngargparser/ is ignored
    assert not [p for p in (project / ".ngargparser" / "sync-in-progress").glob("*") if p.is_dir()]


# --- 6b. A marker that does not parse --------------------------------------------------------


def leave_unparsable_marker(project, pid):
    name = f"20261001T000000.000000Z-{pid}"
    folder = project / ".ngargparser" / "sync-in-progress" / name
    (folder / "src" / "core").mkdir(parents=True)
    shutil.copy2(project / CORE_FILE, folder / CORE_FILE)
    (folder.parent / f"{name}.json").write_text('{"pid": ')  # cut short
    return f".ngargparser/sync-in-progress/{name}"


@needs_proc
def test_an_unparsable_marker_whose_pid_runs_something_else_is_reported(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    syncing = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "cli", "sync"])
    try:
        files = leave_unparsable_marker(project, sleeper.pid)
        before = tree_state(project)
        for run in (sync_args(dry_run=True), sync_args()):
            assert cli.sync_command(run) == 1
            out = strip_ansi("".join(capsys.readouterr()))
            assert (
                f"✗ A previous cli sync (pid {sleeper.pid}, started at an unknown time) did not finish, and may "
                f"have left framework files half updated. The files from before it are in {files}/."
            ) in out
        assert tree_state(project) == before
        shutil.rmtree(project / ".ngargparser" / "sync-in-progress")

        # Control: the pid of a process that runs cli sync and holds the build lock, as one that has
        # just made its marker does: a dry run says it runs.
        leave_unparsable_marker(project, syncing.pid)
        lock = project / ".ngargparser" / "build.lock"
        lock.mkdir()
        (lock / "pid").write_text(f"{syncing.pid}\n")
        assert cli.sync_command(sync_args(dry_run=True)) == 1
        out = strip_ansi("".join(capsys.readouterr()))
        assert "did not finish" not in out
        assert f"✗ A cli sync is running in this folder (pid {syncing.pid}, started at an unknown time)" in out
    finally:
        for process in (sleeper, syncing):
            process.kill()
            process.wait()
