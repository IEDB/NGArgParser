"""The build tests under other launchers than `uv run pytest`.

Under `.venv/bin/python -m pytest`, the .venv's bin/ is not on PATH, and the
first python3 there may read no TOML (a pyenv shim naming a Python that is
not installed): 19 tests of tests/test_build_lock.py skipped with "no python3
with tomllib or tomli on PATH" and 4 of tests/test_build_pyproject.py with
"cannot hide uv here, or no python3 that reads TOML". Both files now put the
test interpreter's folder first on PATH.

A suite started with SIGINT ignored (in the background, as `( pytest ... &
wait )` starts it) passed that on to the builds the tests stop with SIGINT,
and a bash started so can neither trap nor reset it: the build ran on.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)


def inner_env():
    """This run's environment, for a pytest run inside it: no CI (the build
    tests expect a local build) and no addopts from the outer run."""
    drop = ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK", "PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")
    return {k: v for k, v in os.environ.items() if k not in drop}


SKIP_REASONS = ("no python3 with tomllib or tomli on PATH", "cannot hide uv here, or no python3 that reads TOML")


def test_lock_and_pyproject_tests_run_without_uv_run(tmp_path):
    # As `.venv/bin/python -m pytest` runs them: the interpreter's folder is
    # not on PATH, and the first python3 there reads no TOML.
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "python3").write_text("#!/bin/sh\necho 'pyenv: python3: command not found' >&2\nexit 127\n")
    (shim / "python3").chmod(0o755)
    here = os.path.dirname(sys.executable)
    path = os.pathsep.join([str(shim)] + [d for d in os.environ["PATH"].split(os.pathsep) if d and d != here])
    env = {k: v for k, v in inner_env().items() if k != "VIRTUAL_ENV"}

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider", "-q", "-rs"]
        + ["-k", "dependency_added_by_hand or merge_conflict_in_pyproject or typo_without_uv_stops"]
        + ["tests/test_build_lock.py", "tests/test_build_pyproject.py"],
        cwd=REPO,
        env={**env, "PATH": path},
        capture_output=True,
        text=True,
        timeout=600,
    )

    out = proc.stdout + proc.stderr
    for reason in SKIP_REASONS:
        assert reason not in out, out
    assert proc.returncode == 0, out
    assert re.search(r"\b4 passed\b", out), out


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX signals")
def test_sigint_tests_pass_when_the_suite_ignores_sigint():
    tests = [
        "tests/test_build_robustness.py::test_interrupted_build_removes_the_copy[INT]",
        "tests/test_build_robustness.py::test_interrupted_build_does_not_run_into_the_progress_bar",
        "tests/test_build_robustness.py::test_ctrl_c_stops_what_the_script_started[hooks-setsid]",
    ]
    command = [sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider", "-q", *tests]

    # In the background of a non-interactive shell, SIGINT is ignored.
    proc = subprocess.run(
        ["bash", "-c", '( "$@" & wait $! )', "bash", *command],
        cwd=REPO,
        env=inner_env(),
        capture_output=True,
        text=True,
        timeout=600,
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert re.search(r"\b3 passed\b", out), out
