"""`cli sync` and `make build` exclude each other, and so do two syncs; sync replaces
framework files instead of rewriting them in place.

Two syncs at once used to leave a broken tool (both rolled back, the new files
stayed, both deleted their backups), and a sync during `make build` rewrote
scripts/core/build.sh in place while bash was reading it ("syntax error near
unexpected token"). Sync now holds the lock make build takes,
.ngargparser/build.lock, the same way build.sh takes it: an flock on the file
where the build uses flock, else a folder holding the pid.
"""

import hashlib
import os
import shutil
import signal
import stat
import subprocess
import sys
import time

import pytest
from conftest import BUILD_VERSION

from ngargparser import cli


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # These tests run from a git checkout, whose generate and sync stamp <version>+g<sha>
    # (see test_sync_checkout_stamps.py). Stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash, git and POSIX processes",
)

CORE_FILE = "src/core/NGArgumentParser.py"
BUILD_SH = "scripts/core/build.sh"
LOCK = ".ngargparser/build.lock"
# Captured before the scaffold fixture hides every program from cli's shutil.which.
FLOCK = shutil.which("flock")
needs_flock = pytest.mark.skipif(FLOCK is None, reason="needs the flock command")


def busy(pid=None):
    where = f" (pid {pid})" if pid is not None else ""
    return (
        f"✗ A build or another sync is running in this folder{where}. Nothing was changed. "
        "Wait for it, then run cli sync again."
    )


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


def make_stale(project, path=CORE_FILE):
    target = project / path
    pristine = target.read_text()
    target.write_text(pristine + "\n# a local edit that sync will overwrite\n")
    return pristine


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch, fake_venv):
    """A scaffolded tool whose test drive runs on the test interpreter, with a .venv/bin/python
    for the startup check of the builds these tests run."""
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    fake_venv(scaffolded_project)
    return scaffolded_project


@pytest.fixture(params=["flock", "folder"])
def lock_kind(request, monkeypatch):
    """Sync with the flock command on PATH (an flock on the file) or without it (the
    folder lock). The scaffold fixture hides every program from shutil.which."""
    if request.param == "flock":
        if FLOCK is None:
            pytest.skip("needs the flock command")
        monkeypatch.setattr(cli.shutil, "which", lambda cmd: FLOCK if cmd == "flock" else None)
    return request.param


def outer_env(**extra):
    """The environment for a build or a second sync: a local build, no self-upgrade."""
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}
    return {**env, "TOOL_VERSION": BUILD_VERSION, "NGARGPARSER_NO_SELF_UPGRADE": "1", **extra}


def during_the_test_drive(monkeypatch, action):
    """Run `action()` while the sync runs its first test drive (it holds the lock by then),
    and return the list its results are appended to."""
    real_check, seen = cli._smoke_check, []

    def check():
        if not seen:
            seen.append(action())
        return real_check()

    monkeypatch.setattr(cli, "_smoke_check", check)
    return seen


# --- A build and a sync exclude each other -----------------------------------------------


def start_held_build(project, tmp_path):
    """Start a real build that waits in its hook until 'release' appears; return it once it waits."""
    started, release = tmp_path / "hook-started", tmp_path / "release"
    (project / "scripts" / "hooks.sh").write_text(
        f'touch "{started}"\nfor i in $(seq 600); do [ -e "{release}" ] && break; sleep 0.1; done\n'
    )
    build = subprocess.Popen(
        ["bash", BUILD_SH],
        cwd=project,
        env=outer_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 60
    while not started.exists():
        if build.poll() is not None or time.monotonic() > deadline:
            build.kill()
            pytest.fail(f"the build did not reach its hook:\n{build.communicate()[0]}")
        time.sleep(0.05)
    return build, release


@needs_flock
def test_sync_stops_while_make_build_runs(runnable_project, sync_args, strip_ansi, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda cmd: FLOCK if cmd == "flock" else None)
    build, release = start_held_build(runnable_project, tmp_path)
    try:
        make_stale(runnable_project)
        before = tree_state(runnable_project)

        rc = cli.sync_command(sync_args())

        captured = capsys.readouterr()
        out = strip_ansi(captured.err)
        assert rc == 1
        assert busy(build.pid) in out
        assert "Updating framework files" not in captured.out
        assert tree_state(runnable_project) == before
    finally:
        release.touch()
        build.communicate(timeout=120)
    assert build.returncode == 0  # the build it waited for was left alone


def test_sync_stops_at_a_folder_lock_whose_build_runs(runnable_project, sync_args, strip_ansi, capsys, lock_kind):
    # A build without flock (macOS) holds .ngargparser/build.lock as a folder with its pid;
    # a build with flock that finds that folder honors it the same way.
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (runnable_project / LOCK).mkdir(parents=True)
        (runnable_project / LOCK / "pid").write_text(f"{holder.pid}\n")
        make_stale(runnable_project)
        before = tree_state(runnable_project)

        # A dry run writes nothing, the lock included, so it runs while a build does.
        assert cli.sync_command(sync_args(dry_run=True)) == 0
        assert tree_state(runnable_project) == before
        capsys.readouterr()

        rc = cli.sync_command(sync_args())

        out = strip_ansi(capsys.readouterr().err)
        assert rc == 1 and busy(holder.pid) in out
        assert tree_state(runnable_project) == before
    finally:
        holder.kill()
        holder.wait()


def test_make_build_stops_while_a_sync_runs(runnable_project, sync_args, strip_ansi, capsys, monkeypatch, lock_kind):
    def build():
        return subprocess.run(
            ["bash", BUILD_SH], cwd=runnable_project, env=outer_env(), capture_output=True, text=True, timeout=120
        )

    seen = during_the_test_drive(monkeypatch, build)
    make_stale(runnable_project)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "Verified: every help screen that worked before the sync still works (4 of 4)" in out
    assert seen[0].returncode == 1
    assert (
        f"Another build or a cli sync is running in this folder (pid {os.getpid()})" in seen[0].stdout + seen[0].stderr
    )
    assert not (runnable_project / "build").exists()
    if lock_kind == "folder":
        assert not (runnable_project / LOCK).exists()  # the folder lock went with the sync
    # The sync gave the lock back: the next build goes ahead.
    after = build()
    assert after.returncode == 0, after.stdout + after.stderr


def test_a_second_sync_stops_while_the_first_runs(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch, lock_kind
):
    def second_sync():
        return subprocess.run(
            [sys.executable, "-m", "ngargparser.cli", "sync", "--no-upgrade"],
            cwd=runnable_project,
            env=outer_env(NGARGPARSER_NO_UPDATE_CHECK="1", PATH=os.environ["PATH"] if lock_kind == "flock" else ""),
            capture_output=True,
            text=True,
            timeout=120,
        )

    seen = during_the_test_drive(monkeypatch, second_sync)
    pristine = make_stale(runnable_project)

    assert cli.sync_command(sync_args()) == 0

    second = seen[0]
    assert second.returncode == 1
    assert busy(os.getpid()) in strip_ansi(second.stderr)
    assert "Updating framework files" not in second.stdout
    assert (runnable_project / CORE_FILE).read_text() == pristine  # the first sync stands
    assert "Verified" in strip_ansi(capsys.readouterr().out)


def test_a_folder_lock_left_by_a_killed_build_is_taken_over(runnable_project, sync_args, strip_ansi, capsys):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()  # a pid that no longer runs
    (runnable_project / LOCK).mkdir(parents=True)
    (runnable_project / LOCK / "pid").write_text(f"{gone.pid}\n")
    pristine = make_stale(runnable_project)

    assert cli.sync_command(sync_args()) == 0

    assert (runnable_project / CORE_FILE).read_text() == pristine
    assert not (runnable_project / LOCK).exists()  # taken over, then given back


@needs_flock
def test_the_lock_names_no_pid_it_does_not_know(runnable_project, sync_args, strip_ansi, capsys, monkeypatch):
    # flock(1) holding the lock file, as another program might: no pid in the file.
    monkeypatch.setattr(cli.shutil, "which", lambda cmd: FLOCK if cmd == "flock" else None)
    (runnable_project / ".ngargparser").mkdir(exist_ok=True)
    holder = subprocess.Popen([FLOCK, str(runnable_project / LOCK), "sleep", "60"])
    try:
        deadline = time.monotonic() + 10
        while subprocess.run([FLOCK, "-n", str(runnable_project / LOCK), "true"]).returncode == 0:
            assert time.monotonic() < deadline, "flock never took the lock"
            time.sleep(0.05)
        make_stale(runnable_project)

        assert cli.sync_command(sync_args()) == 1
        assert busy() in strip_ansi(capsys.readouterr().err)
    finally:
        holder.send_signal(signal.SIGTERM)
        holder.wait()


# --- Framework files are replaced, not rewritten in place ---------------------------------


def test_a_reader_of_the_old_build_sh_keeps_reading_the_old_file(runnable_project, sync_args):
    # bash reads a script while it runs it; a rewrite in place hands it the new file
    # from the middle. A new file renamed over the old one leaves the old one to it.
    build_sh = runnable_project / BUILD_SH
    stale = build_sh.read_text() + "\n# the build.sh a running build has open\n"
    build_sh.write_text(stale)
    old_inode = build_sh.stat().st_ino
    # A replaced file keeps its own mode (only execute bits are fixed), not the template's.
    core = runnable_project / CORE_FILE
    make_stale(runnable_project)
    core.chmod(0o600)

    with open(build_sh) as running:
        assert cli.sync_command(sync_args()) == 0
        assert running.read() == stale

    assert build_sh.read_text() == (cli.TEMPLATE_DIR / "build.sh").read_text()
    assert build_sh.stat().st_ino != old_inode
    assert stat.S_IMODE(build_sh.stat().st_mode) == 0o755
    assert stat.S_IMODE(core.stat().st_mode) == 0o600
    assert not [p for p in build_sh.parent.iterdir() if p.name.endswith(".ngargparser-sync")]  # no temporary left
