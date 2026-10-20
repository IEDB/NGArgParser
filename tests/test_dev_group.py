"""A generated tool keeps its test tools in the `dev` dependency group.

They were in a group named `test`, which `uv sync` leaves out: `uv sync --group
test && uv run pytest` passed, and the next plain `uv sync` removed pytest again
("pytest: command not found"). uv installs `dev` on every `uv sync`, and
deploy/install.sh leaves it out with --no-dev.
"""

import os
import shutil
import subprocess
from argparse import Namespace

import pytest

from ngargparser import cli

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib

# Found before the in_tmp_dir fixture hides uv from shutil.which.
UV = shutil.which("uv")

TOOLS = [("demo", "demo"), ("example", "aa-counter")]


@pytest.mark.parametrize("name, folder", TOOLS, ids=["blank", "example"])
def test_generated_tools_put_pytest_in_the_dev_group(in_tmp_dir, name, folder):
    assert not cli.startapp_command(Namespace(project_name=name))
    project = in_tmp_dir / folder

    pyproject = tomllib.loads((project / "pyproject.toml").read_text())
    install_sh = (project / "deploy" / "install.sh").read_text()

    assert pyproject["dependency-groups"] == {"dev": ["pytest"]}
    assert "default-groups" not in pyproject.get("tool", {}).get("uv", {})
    syncs = [line.strip() for line in install_sh.splitlines() if line.strip().startswith("uv sync")]
    assert syncs == ["uv sync --frozen --no-dev", "uv sync --no-dev"]


def test_a_routine_uv_sync_keeps_pytest_and_a_deploy_leaves_it_out(in_tmp_dir):
    # Real uv, offline: it needs pytest and python-dotenv in the uv cache and a
    # Python 3.11, so it is skipped where those are missing.
    if UV is None:
        pytest.skip("needs uv")
    assert not cli.startapp_command(Namespace(project_name="demo"))
    project = in_tmp_dir / "demo"
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
    env["UV_PYTHON_DOWNLOADS"] = "never"

    def uv(*args):
        return subprocess.run([UV, *args], cwd=project, env=env, capture_output=True, text=True, timeout=300)

    first = uv("sync", "--offline")
    if first.returncode != 0:
        pytest.skip(f"uv cannot sync offline here: {first.stderr.strip().splitlines()[-1:]}")
    pytest_bin = project / ".venv" / "bin" / "pytest"
    assert pytest_bin.exists()

    again = uv("sync", "--offline")  # the routine sync that used to remove pytest
    assert again.returncode == 0, again.stderr
    assert pytest_bin.exists()
    ran = uv("run", "--offline", "pytest", "--version")
    assert ran.returncode == 0, ran.stdout + ran.stderr

    deploy = uv("sync", "--offline", "--frozen", "--no-dev")  # what deploy/install.sh runs
    assert deploy.returncode == 0, deploy.stderr
    assert not pytest_bin.exists()
    assert (project / ".venv" / "bin" / "python").exists()
