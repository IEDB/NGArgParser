"""deploy/install.sh with the real uv, as a deploy host runs it.

test_install_sh.py runs install.sh with a stub uv, which only records its
arguments. This test locks, builds and deploys a scaffolded tool with the real
uv, offline from the uv cache, and checks the virtualenv it made. Opt-in, since
it needs uv, a Python 3.11 that uv can find offline, and python-dotenv in the uv
cache: set NGARGPARSER_REAL_UV=1 to run it (`pytest -k real_uv`).
"""

import os
import shutil
import subprocess

import pytest
from test_install_sh import deploy_tarball

# Found before the in_tmp_dir fixture hides every program from shutil.which.
UV = shutil.which("uv")
HAS_BASH_AND_GIT = shutil.which("bash") is not None and shutil.which("git") is not None

pytestmark = pytest.mark.skipif(
    os.environ.get("NGARGPARSER_REAL_UV") != "1",
    reason="opt-in: set NGARGPARSER_REAL_UV=1 to deploy with the real uv",
)


# uv settings a deploy host may export, which install.sh unsets: with
# UV_PROJECT_ENVIRONMENT, uv made the virtualenv there, and install.sh's
# `source .venv/bin/activate` failed.
HOST_UV_SETTINGS = {
    "UV_PROJECT_ENVIRONMENT": "{tmp}/elsewhere",
    "UV_RESOLUTION": "lowest",
    "UV_PRERELEASE": "allow",
    "UV_FORK_STRATEGY": "fewest",
    "UV_EXCLUDE_NEWER": "2000-01-01T00:00:00Z",
    "UV_NO_SOURCES": "1",
}


@pytest.mark.parametrize("host", ["clean", "UV_PROJECT_ENVIRONMENT", "every-setting"])
def test_a_real_uv_deploy_installs_the_tool_without_its_dev_group(scaffolded_project, run_build, tmp_path, host):
    if UV is None or not HAS_BASH_AND_GIT:
        pytest.skip("needs uv, bash and git")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "APP_ROOT", "APP_NAME", "CI", "PYTHONPATH")
    }
    env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    env["PATH"] = f"{os.path.dirname(UV)}{os.pathsep}{env.get('PATH', '')}"

    def uv(*args, cwd):
        return subprocess.run([UV, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=300)

    lock = uv("lock", cwd=scaffolded_project)
    if lock.returncode != 0:
        pytest.skip(f"uv cannot lock offline here: {lock.stderr.strip().splitlines()[-1:]}")
    # What install.sh runs, tried in the source tree first: skip where the cache lacks a package.
    trial = uv("sync", "--frozen", "--no-dev", cwd=scaffolded_project)
    if trial.returncode != 0:
        pytest.skip(f"uv cannot sync offline here: {trial.stderr.strip().splitlines()[-1:]}")
    run = run_build(scaffolded_project, env={"UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never"})
    version_dir = deploy_tarball(run.tarball, tmp_path / "apps" / "demo" / "1.0.0", tmp_path)
    assert not (version_dir / ".venv").exists()  # the tarball ships none

    host_settings = {k: v.format(tmp=tmp_path) for k, v in HOST_UV_SETTINGS.items() if host in (k, "every-setting")}
    install = subprocess.run(
        ["bash", "deploy/install.sh"],
        cwd=version_dir,
        env={
            **env,
            **host_settings,
            "IEDB_STANDALONE_NAME": "demo",
            "IEDB_VERSION": "1.0.0",
            "IEDB_VERSION_DIR": str(version_dir),
            "IEDB_STANDALONE_DIR": str(version_dir.parent.parent),
            "IEDB_PREVIOUS_VERSION_DIR": "",
        },
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert install.returncode == 0, install.stdout + install.stderr
    python = version_dir / ".venv" / "bin" / "python"
    assert python.exists()
    assert not (tmp_path / "elsewhere").exists()

    def imports(module):
        return subprocess.run([str(python), "-c", f"import {module}"], env=env, capture_output=True).returncode == 0

    assert imports("dotenv")
    # --no-dev: the dev group (pytest) stays out of a deploy.
    assert not imports("pytest")
    assert not (version_dir / ".venv" / "bin" / "pytest").exists()
    assert f"APP_VENV={python.parent.parent}" in (version_dir / ".env").read_text().splitlines()
    help_screen = subprocess.run(
        [str(python), "src/run_demo.py", "--help"], cwd=version_dir, env=env, capture_output=True, text=True, timeout=60
    )
    assert help_screen.returncode == 0, help_screen.stderr
    assert "usage:" in help_screen.stdout
