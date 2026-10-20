"""Behavioral tests for build.sh's build lock.

One build runs at a time in a tool folder: two builds share libs/, the fetch's
temp folders and build/, so a second build stops at once and names the pid of
the one running. With flock the lock is held on .ngargparser/build.lock;
without it (macOS), .ngargparser/build.lock is a folder holding the pid, and
a folder whose build is gone is taken over.
"""

import os
import shutil
import signal
import subprocess
import time

import pytest
from conftest import BUILD_VERSION

# A generated tool has no .venv and no uv.lock, so its startup check has no Python
# and stops the build: builds run here without it.
NO_PYTHON = {"NGARGPARSER_SKIP_STARTUP_CHECK": "1"}

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or os.name != "posix",
    reason="build.sh tests require bash, git and POSIX processes",
)

BUSY = "ERROR: Another build or a cli sync is running in this folder (pid {}). Wait for it to finish."


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check.
    These tests expect a local build."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


def on_path(name):
    """shutil.which without shutil: the scaffold fixture patches shutil.which
    to hide uv from cli, and that patch reaches every caller."""
    return any(os.access(os.path.join(d, name), os.X_OK) for d in os.environ["PATH"].split(os.pathsep) if d)


def path_without_flock(tmp_path):
    """The current PATH with each folder that holds flock replaced by a folder
    of links to its other programs, or None when flock is not on PATH."""
    dirs = [d for d in os.environ["PATH"].split(os.pathsep) if d]
    if not on_path("flock"):
        return None
    farm = tmp_path / "no-flock-bin"
    farm.mkdir()
    keep = []
    for d in dirs:
        if not os.access(os.path.join(d, "flock"), os.X_OK):
            keep.append(d)
            continue
        for name in os.listdir(d):
            if name != "flock" and not os.path.lexists(farm / name):
                (farm / name).symlink_to(os.path.join(d, name))
    return os.pathsep.join([str(farm)] + keep)


def start_held_build(project_dir, env=None):
    """Start a build whose hook waits until the file 'release' appears next to
    the project, and return it once the hook is running."""
    started = project_dir.parent / "hook-started"
    release = project_dir.parent / "release"
    (project_dir / "scripts" / "hooks.sh").write_text(
        'if [ -n "${HOLD_BUILD:-}" ]; then\n'
        f'    touch "{started}"\n'
        f'    for i in $(seq 600); do [ -e "{release}" ] && break; sleep 0.1; done\n'
        "fi\n"
    )
    outer = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")} | NO_PYTHON
    proc = subprocess.Popen(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=project_dir,
        env={**outer, "TOOL_VERSION": BUILD_VERSION, "HOLD_BUILD": "1", **(env or {})},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 60
    while not started.exists():
        if proc.poll() is not None:
            out, err = proc.communicate()
            pytest.fail(f"the first build ended before its hook ran:\n{out}\n{err}")
        assert time.monotonic() < deadline, "the hook did not start"
        time.sleep(0.05)
    return proc, release


def finish(proc, release):
    release.touch()
    out, err = proc.communicate(timeout=120)
    return proc.returncode, out, err


# "flock-fails": a flock that cannot lock on this file system (some network
# file systems), which exits with another code than 1.
@pytest.mark.parametrize("lock", ["flock", "folder", "flock-fails"])
def test_second_build_stops_at_once(scaffolded_project, run_build, tmp_path, lock):
    env = {}
    if lock == "folder":
        path = path_without_flock(tmp_path)
        if path is None:
            pytest.skip("flock is not installed, so every build here uses the folder lock")
        env = {"PATH": path}
    elif lock == "flock-fails":
        bin_dir = tmp_path / "failing-flock"
        bin_dir.mkdir()
        (bin_dir / "flock").write_text("#!/bin/sh\necho 'flock: 9: No locks available' >&2\nexit 71\n")
        (bin_dir / "flock").chmod(0o755)
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    # A build with flock first: the lock file it leaves holds a pid that is gone.
    run_build(scaffolded_project, progress=True)
    lock_path = scaffolded_project / ".ngargparser" / "build.lock"
    assert lock_path.is_file()
    first, release = start_held_build(scaffolded_project, env)
    try:
        held_as_folder = lock_path.is_dir()
        second = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)
    finally:
        rc, _, err = finish(first, release)

    assert held_as_folder == (lock != "flock")

    assert second.proc.returncode != 0
    assert BUSY.format(first.pid) in second.proc.stderr
    assert "Build failed" not in second.proc.stderr, "the second build stops before it changes anything"
    assert rc == 0, err
    assert second.tarball.is_file(), "the first build finishes undisturbed"
    assert not lock_path.is_dir(), "a folder lock is removed when its build ends"
    # The lock is free again.
    run_build(scaffolded_project, progress=True, env=env)


def test_folder_lock_of_a_build_that_is_gone_is_taken_over(scaffolded_project, run_build):
    gone = subprocess.Popen(["true"])
    gone.wait()
    lock = scaffolded_project / ".ngargparser" / "build.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(f"{gone.pid}\n")

    run_build(scaffolded_project, progress=True)

    assert not lock.exists()


def test_folder_lock_of_a_running_build_stops_the_build(scaffolded_project, run_build):
    lock = scaffolded_project / ".ngargparser" / "build.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(f"{os.getpid()}\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert BUSY.format(os.getpid()) in run.proc.stderr
    assert (lock / "pid").read_text() == f"{os.getpid()}\n", "another build's lock is left alone"
    assert not run.staged.exists()


# The busy message told everyone to remove .ngargparser/build.lock, even with
# flock, where removing it lets a second build run alongside the first. It
# suggests removing the lock only for a folder lock that may be left over.


def test_folder_lock_without_a_pid_may_be_left_over(scaffolded_project, run_build):
    lock = scaffolded_project / ".ngargparser" / "build.lock"
    lock.mkdir(parents=True)  # a build killed before it wrote its pid, or one starting

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        "ERROR: Another build or a cli sync may be running in this folder (its pid is not known). "
        "Wait for it to finish, or remove .ngargparser/build.lock if none is running.\n"
    ) in run.proc.stderr
    assert lock.is_dir(), "the lock is left alone"
    assert not run.staged.exists()


@pytest.mark.skipif(os.name == "posix" and os.geteuid() == 0, reason="root removes the folder anyway")
def test_folder_lock_of_a_gone_build_that_cannot_be_removed(scaffolded_project, run_build):
    gone = subprocess.Popen(["true"])
    gone.wait()
    state = scaffolded_project / ".ngargparser"
    lock = state / "build.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(f"{gone.pid}\n")
    state.chmod(0o555)  # another user's state folder, say
    try:
        run = run_build(scaffolded_project, expect_ok=False, progress=True)
    finally:
        state.chmod(0o755)

    assert run.proc.returncode == 1
    assert (
        f"ERROR: A build or a cli sync that is no longer running (pid {gone.pid}) left .ngargparser/build.lock, "
        "and this build could not remove it. Remove .ngargparser/build.lock (or ask its owner to), then build again.\n"
    ) in run.proc.stderr
    assert "rm:" not in run.proc.stderr, "no raw rm error"
    # Control: the advice works.
    shutil.rmtree(lock)
    run_build(scaffolded_project, progress=True)


# A TERM during the preflight's 'uv lock --check' killed the build but not
# the shell of the $(...) running uv, which kept fd 9, the flock: the next
# build was refused, naming the pid of the build that was gone.


def blocking_uv(tmp_path):
    """A stand-in uv whose first 'lock' call blocks while tmp_path/hold exists."""
    bin_dir = tmp_path / "blocking-uv"
    bin_dir.mkdir()
    once, started, hold = tmp_path / "uv-once", tmp_path / "uv-started", tmp_path / "hold"
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = lock ] && [ ! -e "{once}" ]; then\n'
        f'    touch "{once}" "{started}"\n'
        f'    while [ -e "{hold}" ]; do sleep 0.1; done\n'
        "fi\n"
        "exit 0\n"
    )
    (bin_dir / "uv").chmod(0o755)
    hold.touch()
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, started, hold


def test_term_during_the_lock_check_leaves_no_lock_behind(scaffolded_project, run_build, tmp_path):
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    env, started, hold = blocking_uv(tmp_path)
    outer = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")} | NO_PYTHON
    first = subprocess.Popen(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env={**outer, "TOOL_VERSION": BUILD_VERSION, **env},
        # Not pipes: what outlives the build would hold them open.
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            assert first.poll() is None, "the build ended before its lock check"
            assert time.monotonic() < deadline, "the lock check did not start"
            time.sleep(0.05)
        first.send_signal(signal.SIGTERM)  # the build alone, as `kill <pid>` does
        first.wait(timeout=20)

        second = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)
    finally:
        hold.unlink()
        if first.poll() is None:
            os.killpg(first.pid, signal.SIGKILL)
            first.wait()

    assert "Another build or a cli sync" not in second.proc.stderr, "the lock outlived the build"
    assert second.proc.returncode == 0, second.proc.stderr
    assert second.tarball.is_file()
    # The trap was set before the preflight: the build stopped as a signal stops it.
    assert first.returncode == 143


# A lock file another user made, readable but not writable for the rest (a
# tool folder a group shares), stopped the build with bash's raw
# "build.lock: Permission denied". The build opens it read-only for flock.

needs_flock_and_no_root = pytest.mark.skipif(
    not on_path("flock") or os.geteuid() == 0, reason="needs flock, and a user that file modes apply to"
)


@needs_flock_and_no_root
def test_lock_file_only_its_owner_may_write_still_locks(scaffolded_project, run_build):
    run_build(scaffolded_project, progress=True)
    lock_path = scaffolded_project / ".ngargparser" / "build.lock"
    lock_path.chmod(0o444)
    try:
        run = run_build(scaffolded_project, progress=True)
        assert "Permission denied" not in run.proc.stderr
        # The lock still works: a second build stops while the first runs.
        first, release = start_held_build(scaffolded_project)
        try:
            second = run_build(scaffolded_project, expect_ok=False, progress=True)
        finally:
            rc, _, err = finish(first, release)
    finally:
        lock_path.chmod(0o644)

    assert rc == 0, err
    assert second.proc.returncode == 1
    # The file kept the pid of a build that is gone, which is not named.
    assert BUSY.format("unknown") in second.proc.stderr


@needs_flock_and_no_root
def test_lock_file_that_cannot_be_opened_says_what_to_do(scaffolded_project, run_build):
    run_build(scaffolded_project, progress=True)
    lock_path = scaffolded_project / ".ngargparser" / "build.lock"
    lock_path.chmod(0o000)
    try:
        run = run_build(scaffolded_project, expect_ok=False, progress=True)
    finally:
        lock_path.chmod(0o644)

    assert run.proc.returncode == 1
    assert (
        "ERROR: Cannot open .ngargparser/build.lock (Permission denied): "
        "ask its owner to run chmod a+rw .ngargparser/build.lock. Nothing was changed.\n"
    ) in run.proc.stderr
    assert "line " not in run.proc.stderr, "no raw bash error"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_lock_file_that_cannot_be_created_names_the_real_reason(scaffolded_project, run_build):
    # .ngargparser/ is there but not writable, and has no lock file yet: the reason is
    # the one creating the file gives, not "No such file or directory".
    run_build(scaffolded_project, progress=True)
    state = scaffolded_project / ".ngargparser"
    (state / "build.lock").unlink()
    state.chmod(0o555)
    try:
        run = run_build(scaffolded_project, expect_ok=False, progress=True)
    finally:
        state.chmod(0o755)

    if os.geteuid() == 0:
        pytest.skip("root can write a read-only folder")
    assert run.proc.returncode == 1
    assert (
        "ERROR: Cannot create .ngargparser/build.lock (Permission denied): "
        ".ngargparser is read-only for you; run chmod u+rwx .ngargparser. Nothing was changed.\n"
    ) in run.proc.stderr


@needs_flock_and_no_root
def test_new_lock_file_is_writable_for_everyone(scaffolded_project):
    outer = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")} | NO_PYTHON
    proc = subprocess.run(
        ["bash", "-c", "umask 077 && exec bash scripts/core/build.sh --progress"],
        cwd=scaffolded_project,
        env={**outer, "TOOL_VERSION": BUILD_VERSION},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    lock_path = scaffolded_project / ".ngargparser" / "build.lock"
    assert lock_path.stat().st_mode & 0o777 == 0o666
