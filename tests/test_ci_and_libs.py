"""The generated CI build job's version, `make libs`, and the lock tests' PATH.

The CI template set TOOL_VERSION from $CI_COMMIT_REF_NAME, and the build
refused branch names git accepts (issue#42, user@fix, feat/x,y, fix(ui)): the
build job failed. The job now maps every character the build refuses to '-'.

Code scripts/get_libs.sh fetches into libs/ was there only after the first
`make build`, so a newcomer's run in the tool folder failed with
ModuleNotFoundError. `make libs` runs that fetch alone.

tests/test_build_lock.py's stand-in uv was the build's way to read TOML where
the first python3 on PATH could not (a pyenv shim outside `uv run`): its lock
tests failed under `.venv/bin/python -m pytest`.
"""

import os
import shutil
import subprocess
import sys

import pytest
from test_build_ci_template import TEMPLATE, job_script_lines
from test_build_lock import fake_uv  # noqa: F401 (a fixture)

# A generated tool has no .venv and no uv.lock, so its startup check has no Python
# and stops the build: builds run here without it.
NO_PYTHON = {"NGARGPARSER_SKIP_STARTUP_CHECK": "1"}

needs_build_tools = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or shutil.which("make") is None,
    reason="build tests require bash, git and make",
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


def ci_version(ref):
    """TOOL_VERSION after the build job's script lines before `make build`, for the git ref `ref`."""
    script = job_script_lines(TEMPLATE.read_text(), "build")
    mapping = [line for line in script[: script.index("make build")] if "TOOL_VERSION=" in line]
    assert mapping, "the build job does not map TOOL_VERSION"
    env = {k: v for k, v in os.environ.items() if k != "TOOL_VERSION"} | NO_PYTHON
    proc = subprocess.run(
        ["sh", "-e", "-c", "\n".join(mapping) + '\nprintf "%s" "$TOOL_VERSION"'],
        env={**env, "CI_COMMIT_REF_NAME": ref, "TOOL_VERSION": ref},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


BRANCHES = {"issue#42": "issue-42", "user@fix": "user-fix", "feat/x,y": "feat-x-y", "fix(ui)": "fix-ui-"}


@pytest.mark.parametrize("ref, version", sorted(BRANCHES.items()))
def test_ci_build_job_maps_a_branch_name_the_build_refuses(ref, version):
    if shutil.which("git"):
        # Branch names git accepts.
        assert subprocess.run(["git", "check-ref-format", "--branch", ref], capture_output=True).returncode == 0
    assert ci_version(ref) == version


@pytest.mark.parametrize("ref", ["v1.2.0", "1.0.0-rc.1", "release_2+build.5", "main"])
def test_ci_build_job_keeps_a_valid_name(ref):
    assert ci_version(ref) == ref


def test_ci_build_job_drops_a_leading_dash():
    # A branch may start with '#', which would become a leading '-': the
    # build refuses that too.
    assert ci_version("#42") == "42"


@needs_build_tools
@pytest.mark.parametrize("ref", sorted(BRANCHES))
def test_ci_version_of_each_branch_builds(scaffolded_project, ref):
    version = ci_version(ref)
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")} | NO_PYTHON

    proc = subprocess.run(
        ["bash", "scripts/core/build.sh"],
        cwd=scaffolded_project,
        env={**env, "TOOL_VERSION": version},
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (scaffolded_project / "build" / f"IEDB_NG_DEMO-{version}.tar.gz").is_file()


def make(project_dir, target, **extra):
    env = {
        k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK", "TOOL_VERSION")
    } | NO_PYTHON
    return subprocess.run(
        ["make", target], cwd=project_dir, env={**env, **extra}, capture_output=True, text=True, timeout=300
    )


@needs_build_tools
def test_make_libs_fetches_before_the_first_build(scaffolded_project, tmp_path):
    runs = tmp_path / "get-libs-runs"
    (scaffolded_project / "scripts" / "get_libs.sh").write_text(
        f"set -euo pipefail\necho run >> '{runs}'\nmkdir mydep && echo 'VALUE = 42' > mydep/core.py\n"
    )
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text(
        run_script.read_text().replace("import core.set_pythonpath", "import core.set_pythonpath\nimport mydep.core", 1)
    )
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ROOT", "APP_NAME", "PYTHONPATH")}

    def run_in_the_tool_folder():
        return subprocess.run(
            [sys.executable, "src/run_demo.py", "--help"],
            cwd=scaffolded_project,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    # Control: before any fetch, the run in the tool folder fails.
    assert "No module named 'mydep'" in run_in_the_tool_folder().stderr

    proc = make(scaffolded_project, "libs")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "libs/ is up to date with scripts/get_libs.sh." in proc.stdout
    assert (scaffolded_project / "libs" / "mydep" / "core.py").is_file()
    assert not (scaffolded_project / "build").exists(), "make libs builds nothing"
    assert run_in_the_tool_folder().returncode == 0
    # The build that follows finds the fetch done, as make libs recorded it.
    proc = make(scaffolded_project, "build-verbose")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Libs up to date" in proc.stdout
    assert runs.read_text() == "run\n"
    # And make libs again fetches nothing.
    proc = make(scaffolded_project, "libs")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert runs.read_text() == "run\n"


@needs_build_tools
def test_make_libs_stops_on_a_failed_fetch(scaffolded_project):
    (scaffolded_project / "scripts" / "get_libs.sh").write_text("echo 'no network' >&2\nexit 7\n")

    proc = make(scaffolded_project, "libs")

    assert proc.returncode != 0
    assert "ERROR: scripts/get_libs.sh failed (exit 7); libs/ was not changed." in proc.stderr
    assert not (scaffolded_project / "libs").exists()


def test_makefile_lists_the_libs_target():
    makefile = (TEMPLATE.parent / "Makefile").read_text()

    phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))
    assert "libs" in phony.split()
    assert "\nlibs:\n\t$(call run_build_sh,--libs-only)\n" in makefile


# The lock tests under `.venv/bin/python -m pytest`, where the first python3
# on PATH may read no TOML (a pyenv shim that names a Python not installed).


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")
def test_lock_tests_find_a_python_that_reads_toml(scaffolded_project, run_build, fake_uv, tmp_path, monkeypatch):  # noqa: F811
    probe = "try:\n    import tomllib\nexcept ImportError:\n    import tomli\n"
    python3 = os.path.join(os.path.dirname(sys.executable), "python3")
    if not os.access(python3, os.X_OK) or subprocess.run([python3, "-c", probe]).returncode != 0:
        pytest.skip("the test interpreter's folder has no python3 that reads TOML")
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "python3").write_text("#!/bin/sh\necho 'pyenv: version 3.11 is not installed' >&2\nexit 1\n")
    (shim / "python3").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    env, log = fake_uv("ok")
    (scaffolded_project / "uv.lock").write_text("version = 1\n")

    run_build(scaffolded_project, progress=True, env=env)

    assert log.read_text() == f"{scaffolded_project} lock --check --offline\n"
