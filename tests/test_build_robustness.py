"""Behavioral tests for how build.sh fails.

An interrupted build, a failing scripts/hooks.sh and a .distignore rule that
drops the entry script must each stop the build, remove the half-built copy,
leave no tarball and say why.
"""

import os
import shutil
import signal
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)


def write_hook(project_dir, body):
    (project_dir / "scripts" / "hooks.sh").write_text(body)


# Controls: the failure handling must not change a build that works.


def test_ordinary_build_succeeds(scaffolded_project, run_build):
    run = run_build(scaffolded_project, progress=True)

    assert "Build completed" in run.proc.stdout
    assert "src/run_demo.py" in run.members


def test_passing_hook_still_runs(scaffolded_project, run_build):
    write_hook(scaffolded_project, 'echo "hook says hi"\necho made > "$BUILD_DIR/hook-made.txt"\n')

    run = run_build(scaffolded_project, progress=True)

    assert "hook-made.txt" in run.members
    assert "hook says hi" not in run.proc.stdout + run.proc.stderr, "progress mode keeps hook output off the terminal"


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX process groups")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT], ids=["TERM", "INT"])
def test_interrupted_build_removes_the_copy(scaffolded_project, run_build, sig):
    # A finished build first, so there is an old tarball for the next build to remove.
    run = run_build(scaffolded_project)
    started = scaffolded_project.parent / "hook-started"
    write_hook(scaffolded_project, f'touch "{started}"\nsleep 30\n')

    # Its own process group, so the signal reaches the build and the hook, as
    # Ctrl-C in a terminal does.
    proc = subprocess.Popen(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env={**os.environ, "TOOL_VERSION": run.staged.name.rpartition("-")[2]},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            assert proc.poll() is None, "the build ended before the hook ran"
            assert time.monotonic() < deadline, "the hook did not start"
            time.sleep(0.05)
        os.killpg(proc.pid, sig)
        _, stderr = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    assert not run.staged.exists(), "the half-built copy must be removed"
    assert not run.tarball.exists()
    assert "Build failed" in stderr
    assert proc.returncode == 128 + sig, stderr


def test_failing_hook_shows_its_log(scaffolded_project, run_build):
    write_hook(scaffolded_project, 'for i in {1..30}; do echo "line $i"; done\necho "no route to host" >&2\nexit 3\n')

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert "ERROR: scripts/hooks.sh failed (exit 3)." in run.proc.stderr
    shown = run.proc.stderr.splitlines()
    assert "no route to host" in shown
    assert "line 30" in shown and "line 12" in shown
    assert "line 11" not in shown, "only the last 20 lines of the log are shown"
    log = (scaffolded_project / ".ngargparser" / "hooks.log").read_text()
    assert "line 1\n" in log and "no route to host" in log
    assert "Build failed" in run.proc.stderr
    assert not run.staged.exists()
    assert not run.tarball.exists()


def test_distignore_rule_that_drops_the_entry_script_fails(scaffolded_project, run_build):
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\nsrc/run_*.py\n")

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "src/run_demo.py is excluded by .distignore, so the tarball would have no entry script." in run.proc.stderr
    assert "Startup check skipped" not in run.proc.stdout
    assert not run.staged.exists()
    assert not run.tarball.exists()
