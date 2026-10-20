"""The build lock in cli sync's words, a stale lock two builds find at once,
src/core/dep_names.py, and build.conf's temp file.

The lock hints said chmod a+rwx .ngargparser (writable for everyone), where
cli sync now says chmod u+rwx for the owner, or chmod g+rwx for a group that
shares the tool, and a .ngargparser that is a file stopped the build with
mkdir's "File exists". Without flock, a .ngargparser the user may not write
was reported as a lock left by a killed build. Two builds that both found a
stale lock both deleted it (the second one deleting the first one's new
lock) and both went on: the lock is now renamed aside, and one that is not
the stale one any more is put back. ./configure now imports
src/core/dep_names.py, so a tarball without it cannot deploy. build.conf was
read into /tmp/ngbuild-conf.*, before the lock, which a kill -9 left there.
"""

import os
import shutil
import subprocess
import threading
import time

import pytest
from test_build_one_at_a_time import BUSY, on_path, path_without_flock

from ngargparser import cli

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or os.name != "posix",
    reason="build.sh tests require bash, git and POSIX processes",
)

LOCK = os.path.join(".ngargparser", "build.lock")
needs_no_root = pytest.mark.skipif(os.geteuid() == 0, reason="needs file modes that apply")


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


def sync_words(error, created):
    """What cli sync says for the same lock problem, run in the tool folder (the
    scaffold fixture's current folder)."""
    return str(cli._LockUnusable(error, created=created))


def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


# --- src/core/dep_names.py ---------------------------------------------------------------


def test_dep_names_is_a_file_a_deploy_needs(scaffolded_project, run_build):
    (scaffolded_project / "src" / "core" / "dep_names.py").unlink(missing_ok=True)

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        "ERROR: The tool folder lacks files a deploy needs, so nothing was built:\n"
        "  src/core/dep_names.py: framework code the tool runs on (cli sync restores it)\n"
    ) in run.proc.stderr


def test_a_hook_that_removes_dep_names_fails_the_build(scaffolded_project, run_build):
    (scaffolded_project / "scripts" / "hooks.sh").write_text('rm -f "$BUILD_DIR/src/core/dep_names.py"\n')

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert (
        "ERROR: scripts/hooks.sh removed files a deploy needs from the build copy:\n"
        "  src/core/dep_names.py: framework code the tool runs on (cli sync restores it)\n"
    ) in run.proc.stderr
    assert not run.tarball.exists()


# --- the lock hints, in cli sync's words --------------------------------------------------


@needs_no_root
@pytest.mark.parametrize("lock", ["flock", "folder"])
def test_a_read_only_ngargparser_gets_sync_s_hint(scaffolded_project, run_build, tmp_path, lock):
    env = {}
    if lock == "folder":
        path = path_without_flock(tmp_path)
        if path is None:
            pytest.skip("flock is not installed, so every build here uses the folder lock")
        env["PATH"] = path
    elif not on_path("flock"):
        pytest.skip("needs flock")
    run_build(scaffolded_project, env=env)
    state = scaffolded_project / ".ngargparser"
    lock_path = state / "build.lock"
    if lock_path.is_dir():
        shutil.rmtree(lock_path)
    lock_path.unlink(missing_ok=True)
    state.chmod(0o555)
    try:
        run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)
    finally:
        state.chmod(0o755)

    words = sync_words(PermissionError(13, "Permission denied"), created=True)
    assert words == (
        "Cannot create .ngargparser/build.lock (Permission denied): .ngargparser is read-only for you; "
        "run chmod u+rwx .ngargparser. Nothing was changed."
    )
    assert run.proc.returncode == 1
    assert run.proc.stderr.endswith(f"ERROR: {words}\n"), run.proc.stderr
    assert "no longer running" not in run.proc.stderr


@needs_no_root
def test_a_lock_file_that_cannot_be_opened_gets_sync_s_hint(scaffolded_project, run_build):
    if not on_path("flock"):
        pytest.skip("needs flock")
    run_build(scaffolded_project)
    lock_path = scaffolded_project / ".ngargparser" / "build.lock"
    lock_path.chmod(0o000)
    try:
        run = run_build(scaffolded_project, expect_ok=False, progress=True)
    finally:
        lock_path.chmod(0o644)

    words = sync_words(PermissionError(13, "Permission denied"), created=False)
    assert run.proc.stderr.endswith(f"ERROR: {words}\n"), run.proc.stderr


def test_a_ngargparser_that_is_a_file_gets_sync_s_hint(scaffolded_project, run_build, tmp_path):
    (scaffolded_project / ".ngargparser").rename(tmp_path / "state-folder")
    (scaffolded_project / ".ngargparser").write_text("not a folder\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    words = sync_words(FileExistsError(17, "File exists"), created=True)
    assert words.startswith("Cannot create .ngargparser/build.lock: .ngargparser is a file, not a folder. Move it away")
    assert run.proc.returncode == 1
    assert run.proc.stderr == f"ERROR: {words}\n"
    assert (scaffolded_project / ".ngargparser").read_text() == "not a folder\n"


def test_another_user_s_ngargparser_gets_sync_s_group_hint(scaffolded_project, monkeypatch):
    # Another user's folder cannot be made here without root, and root writes any
    # folder: the words of both are compared as written.
    (scaffolded_project / ".ngargparser").mkdir(exist_ok=True)
    uid = os.stat(".ngargparser").st_uid
    monkeypatch.setattr(cli.os, "getuid", lambda: uid + 1)
    words = sync_words(PermissionError(13, "Permission denied"), created=True)
    fix = words.split("(Permission denied): ", 1)[1].rsplit(". Nothing was changed.", 1)[0]
    assert fix == (
        "ask the owner of .ngargparser to run chmod g+rwx .ngargparser, which lets a group you share write there"
    )
    build_sh = (scaffolded_project / "scripts" / "core" / "build.sh").read_text()
    assert f'fix="{fix}"' in build_sh
    assert 'error "Cannot create .ngargparser/build.lock ($reason): $fix. Nothing was changed."' in build_sh


# --- a stale lock: cli sync's protocol --------------------------------------------------


def folder_lock_path(tmp_path):
    """PATH on which the build takes the folder lock, as cli sync does without flock."""
    return path_without_flock(tmp_path) if on_path("flock") else os.environ["PATH"]


def stale_lock(project_dir):
    """.ngargparser/build.lock as a build that was killed left it."""
    lock = project_dir / LOCK
    lock.parent.mkdir(exist_ok=True)
    lock.mkdir()
    (lock / "pid").write_text(f"{dead_pid()}\n")
    return lock


def later(seconds, action):
    timer = threading.Timer(seconds, action)
    timer.start()
    return timer


def test_a_build_waits_for_another_run_s_takeover(scaffolded_project, run_build, tmp_path):
    # Another build or sync holds the takeover folder: it takes the stale lock away and
    # makes its own. The build waits for it, then finds that lock in use.
    lock = stale_lock(scaffolded_project)
    takeover = lock.parent / "build.lock.takeover"
    takeover.mkdir()
    live = os.getpid()

    def other_run():
        shutil.rmtree(lock)
        lock.mkdir()
        (lock / "pid").write_text(f"{live}\n")
        takeover.rmdir()

    timer = later(1.0, other_run)
    try:
        run = run_build(scaffolded_project, expect_ok=False, progress=True, env={"PATH": folder_lock_path(tmp_path)})
    finally:
        timer.join()

    assert run.proc.returncode == 1
    assert BUSY.format(live) in run.proc.stderr
    assert (lock / "pid").read_text() == f"{live}\n"


def test_a_takeover_folder_left_by_a_killed_run_is_removed(scaffolded_project, run_build, tmp_path):
    lock = stale_lock(scaffolded_project)
    takeover = lock.parent / "build.lock.takeover"
    takeover.mkdir()
    old = time.time() - 120
    os.utime(takeover, (old, old))

    run_build(scaffolded_project, env={"PATH": folder_lock_path(tmp_path)})

    assert not takeover.exists()
    assert not lock.exists(), "the build gives its folder lock back"
    assert not list(lock.parent.glob("build.lock.stale-*"))


@needs_no_root
def test_an_old_takeover_folder_that_cannot_be_removed_stops_the_build(scaffolded_project, tmp_path):
    # It would be found again and again: the build stops at once with the
    # left-over message, instead of looping.
    lock = stale_lock(scaffolded_project)
    pid = (lock / "pid").read_text().strip()
    takeover = lock.parent / "build.lock.takeover"
    takeover.mkdir()
    old = time.time() - 120
    os.utime(takeover, (old, old))
    lock.parent.chmod(0o555)
    outer = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}
    try:
        started = time.monotonic()
        proc = subprocess.run(
            ["bash", "scripts/core/build.sh", "--progress"],
            cwd=scaffolded_project,
            env={**outer, "PATH": folder_lock_path(tmp_path), "TOOL_VERSION": "tver"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        took = time.monotonic() - started
    finally:
        lock.parent.chmod(0o755)

    assert proc.returncode == 1
    assert proc.stderr == (
        f"ERROR: A build or a cli sync that is no longer running (pid {pid}) left .ngargparser/build.lock, and "
        "this build could not remove it. Remove .ngargparser/build.lock (or ask its owner to), then build again.\n"
    )
    assert took < 10, f"the build took {took:.1f}s"
    assert takeover.is_dir() and (lock / "pid").read_text().strip() == pid


def test_a_lock_that_gets_its_pid_a_moment_later_is_named(scaffolded_project, run_build, tmp_path):
    # A build or sync that has just made the lock folder writes its pid next.
    lock = scaffolded_project / LOCK
    lock.parent.mkdir(exist_ok=True)
    lock.mkdir()
    live = os.getpid()
    timer = later(0.6, lambda: (lock / "pid").write_text(f"{live}\n"))
    try:
        run = run_build(scaffolded_project, expect_ok=False, progress=True, env={"PATH": folder_lock_path(tmp_path)})
    finally:
        timer.join()

    assert run.proc.returncode == 1
    assert BUSY.format(live) in run.proc.stderr
    assert "its pid is not known" not in run.proc.stderr


def test_a_stale_lock_is_taken_over(scaffolded_project, run_build, tmp_path):
    # Control: a lock no build holds any more is taken over, and nothing is left aside.
    lock = stale_lock(scaffolded_project)

    run_build(scaffolded_project, env={"PATH": folder_lock_path(tmp_path)})

    assert not lock.exists(), "the build gives its folder lock back"
    assert not list(lock.parent.glob("build.lock.stale-*"))
    assert not (lock.parent / "build.lock.takeover").exists()


# --- build.conf's temp file ---------------------------------------------------------------


def test_a_build_killed_while_reading_build_conf_leaves_nothing_in_tmpdir(scaffolded_project, run_build, tmp_path):
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    conf = scaffolded_project / "scripts" / "core" / "build.conf"
    kept = conf.read_text()
    # build.conf is read in a subshell, where $$ is the build's own pid.
    conf.write_text(kept + "\nkill -9 $$\n")
    proc = subprocess.run(
        ["bash", "scripts/core/build.sh"],
        cwd=scaffolded_project,
        env={**os.environ, "TOOL_VERSION": "tver", "TMPDIR": str(tmpdir)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == -9

    assert not list(tmpdir.iterdir())

    # The next build removes what the killed one left in .ngargparser/.
    conf.write_text(kept)
    run_build(scaffolded_project, env={"TMPDIR": str(tmpdir)})
    assert not list((scaffolded_project / ".ngargparser").glob("build-tmp.*"))
    assert not list(tmpdir.iterdir())
