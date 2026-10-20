"""Tests for templates/gitlab-ci.yml, the starter .gitlab-ci.yml for a new tool.

The build job must create the .venv (`uv sync`) before `make build`, because a
CI build without one fails its startup check. It names the version after the
git ref and keeps the tarball as an artifact of the job named "build", which
is where the nxg-tools-deployments orchestrator downloads it from. A test job
in an earlier stage runs the tool's tests (./configure, then pytest), so the
build runs only when they pass. Every job installs the uv the repository's own
CI pins.
"""

import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ngargparser import cli

TEMPLATE = Path(cli.__file__).parent / "templates" / "gitlab-ci.yml"
REPO_CI = Path(cli.__file__).parents[1] / ".gitlab-ci.yml"
PINNED_UV = 'pip install --quiet "uv==$UV_VERSION"'


def job_script_lines(text, job):
    """The `- ...` items under the `script:` key of the top-level `job`, without YAML."""
    lines = text.splitlines()
    start = lines.index(f"{job}:")
    items, in_script = [], False
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break  # the next top-level key
        if line.strip() == "script:":
            in_script = True
            continue
        if in_script:
            stripped = line.strip()
            if stripped.startswith("- "):
                items.append(stripped[2:])
            elif stripped and not stripped.startswith("#"):
                break
    return items


def test_template_is_valid_yaml():
    text = TEMPLATE.read_text().replace("{TOOL_NAME}", "my-tool")
    assert "\t" not in text, "YAML forbids tabs for indentation"
    yaml = pytest.importorskip("yaml")

    data = yaml.safe_load(text)

    job = data["build"]
    assert job["stage"] in data["stages"]
    assert job["variables"]["TOOL_VERSION"] == "$CI_COMMIT_REF_NAME"
    assert job["artifacts"]["paths"] == ["build/*.tar.gz"]
    assert job["artifacts"]["name"] == "my-tool-$CI_COMMIT_REF_SLUG"
    assert job["script"].index("uv sync --locked") < job["script"].index("make build")
    # Every job installs the pinned uv (the default before_script; no job replaces it).
    assert PINNED_UV in data["default"]["before_script"]
    assert not any("before_script" in data[name] for name in ("test", "build"))


def test_template_syncs_before_it_builds():
    text = TEMPLATE.read_text()

    script = job_script_lines(text, "build")

    syncs = [i for i, line in enumerate(script) if re.match(r"uv sync\b", line)]
    builds = [i for i, line in enumerate(script) if line == "make build"]
    assert syncs and builds and syncs[0] < builds[0], script
    assert "TOOL_VERSION: $CI_COMMIT_REF_NAME" in text
    assert "- build/*.tar.gz" in text
    assert f"- {PINNED_UV}" in text
    assert not re.search("[–—]", text), "no en or em dashes"


# The test job runs the tool's tests before the build: the build stage runs
# only when the test stage passed, so a failing test leaves no tarball for the
# orchestrator.


def test_template_runs_the_tests_before_the_build():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(TEMPLATE.read_text())

    stages = data["stages"]
    assert stages.index(data["test"]["stage"]) < stages.index(data["build"]["stage"])
    assert data["test"]["script"] == ["uv sync --locked", "./configure", "uv run pytest"]
    assert "allow_failure" not in data["test"], "a failing test must stop the pipeline"


def test_template_pins_the_uv_the_repository_ci_pins():
    if not REPO_CI.is_file():
        pytest.skip("not run from a checkout of the ngargparser repository")
    yaml = pytest.importorskip("yaml")

    pinned = yaml.safe_load(TEMPLATE.read_text())["variables"]["UV_VERSION"]

    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned), pinned
    assert pinned == yaml.safe_load(REPO_CI.read_text())["variables"]["UV_VERSION"]
    assert "pip install --quiet uv\n" not in TEMPLATE.read_text(), "no unpinned install"


# The test job's commands in a fresh checkout: no .env (it is gitignored), so
# ./configure writes it, then pytest runs the tool's tests. uv sync is left
# out (the .venv is a stand-in) and pytest runs with the test interpreter.


def run_test_job(project, env):
    script = job_script_lines(TEMPLATE.read_text(), "test")
    assert script[0] == "uv sync --locked"
    commands = [line.replace("uv run pytest", f"{sys.executable} -m pytest -p no:cacheprovider") for line in script[1:]]
    return subprocess.run(
        ["sh", "-e", "-c", "\n".join(commands)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.skipif(os.name != "posix", reason="runs the job's shell commands")
def test_test_job_configures_then_runs_the_tests(scaffolded_project, fake_venv):
    fake_venv(scaffolded_project)
    (scaffolded_project / ".env").unlink()
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ROOT", "APP_NAME", "PYTEST_ADDOPTS", "VIRTUAL_ENV")}

    proc = run_test_job(scaffolded_project, env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (scaffolded_project / ".env").is_file()
    assert re.search(r"\d+ passed", proc.stdout), proc.stdout

    # A dependency whose path paths.py leaves as None stops ./configure, and
    # the job with it, before pytest: the comment in the job says what to do.
    (scaffolded_project / "paths.py").write_text("pepx_path=None\npepx_venv=None\n")
    (scaffolded_project / ".env").unlink()

    proc = run_test_job(scaffolded_project, env)

    assert proc.returncode != 0
    assert "pepx_path" in proc.stdout and "is None in paths.py" in proc.stdout
    assert " passed" not in proc.stdout
    assert "./configure --pepx-path=" in TEMPLATE.read_text()


def test_template_is_packaged():
    # setuptools ships templates by the package-data globs in pyproject.toml.
    pyproject = (Path(cli.__file__).parents[1] / "pyproject.toml").read_text()
    globs_line = re.search(r'^"ngargparser\.templates" = \[(.*)\]$', pyproject, re.M)
    assert globs_line, "package-data for ngargparser.templates not found"
    globs = re.findall(r'"([^"]+)"', globs_line.group(1))
    assert any(fnmatch.fnmatch(TEMPLATE.name, g) for g in globs), globs
    assert TEMPLATE.is_file()
