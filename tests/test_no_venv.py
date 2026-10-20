"""A dependency without a virtualenv: the Python run_<dep> runs it with, and ./configure's warning about it.

dependency_command runs such a dependency outside every virtualenv: VIRTUAL_ENV
is left out, and so is the bin/ of every virtualenv on PATH, so python3 is not
the caller's. run_<dep> ran it with the caller's PATH as it was: under `uv run`,
the caller's virtualenv python3. ./configure warned "callee has no virtualenv.
It will run under whatever Python is active" for a dependency not deployed yet,
whose virtualenv is read from its .env when it arrives.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest
from test_configure_env import make_tool_dir, run_configure
from test_dependency_command import outside_venvs

from ngargparser import cli

# The in_tmp_dir fixture hides every program from shutil.which.
WHICH = shutil.which
SHELLS = [shell for shell in ("sh", "bash", "dash") if WHICH(shell)]

pytestmark = pytest.mark.skipif(os.name != "posix" or WHICH("bash") is None, reason="needs bash")

# What the dependency was started with.
REPORT = """\
import json, os, sys
print(json.dumps({
    "args": sys.argv[1:],
    "caller_python": os.environ.get("CALLER_PYTHON_RAN"),
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
    "path": os.environ.get("PATH"),
}))
"""

PROBE = """\
import subprocess, sys
import core.set_pythonpath
from core.app_env import dependency_command
argv, env = dependency_command("novenv", "predict")
sys.exit(subprocess.run(argv, env=env).returncode)
"""


def caller_venvs(tmp_path):
    """Two virtualenvs whose python3 marks that it ran: one named by VIRTUAL_ENV, one found by its pyvenv.cfg."""
    bins = []
    for name in ("caller-venv", "grandparent-venv"):
        venv = tmp_path / name
        (venv / "bin").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        for program in ("python", "python3"):
            python = venv / "bin" / program
            if python.is_symlink():  # never write through a symlink into a real interpreter
                python.unlink()
            python.write_text(f'#!/bin/sh\nCALLER_PYTHON_RAN=1 exec "{sys.executable}" "$@"\n')
            python.chmod(0o755)
        bins.append(venv / "bin")
    return bins


@pytest.mark.parametrize("shell", SHELLS)
def test_run_dep_runs_a_dependency_without_a_venv_as_dependency_command_does(scaffolded_project, tmp_path, shell):
    tool = make_tool_dir(tmp_path, name="novenv", with_venv=False)
    (tool / "run.py").write_text(REPORT)
    cli.add_deps_to_paths("paths.py", ["novenv"])
    run_configure(scaffolded_project, f"--novenv-path={tool}", "--novenv-entry=run.py")
    caller, grandparent = caller_venvs(tmp_path)
    outside = outside_venvs()
    outside.pop("LD_LIBRARY_PATH", None)
    if WHICH("python3", path=outside["PATH"]) is None:
        pytest.skip("no python3 on PATH outside the virtualenvs")
    # As `uv run` leaves it, with the venv of a tool that started this one behind it.
    path = os.pathsep.join([str(caller), str(grandparent), outside["PATH"]])
    env = {**outside, "VIRTUAL_ENV": str(caller.parent), "PATH": path}

    (scaffolded_project / "src" / "probe_novenv.py").write_text(PROBE)
    python = subprocess.run(
        [sys.executable, str(scaffolded_project / "src" / "probe_novenv.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert python.returncode == 0, python.stderr
    shell_run = subprocess.run(
        [WHICH(shell), "-c", '. ./setup_novenv_env.sh; run_novenv predict; echo "$VIRTUAL_ENV|$PATH"'],
        cwd=scaffolded_project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert shell_run.returncode == 0, shell_run.stderr

    from_python = json.loads(python.stdout.strip().splitlines()[-1])
    child, after = shell_run.stdout.strip().splitlines()[-2:]
    assert json.loads(child) == from_python
    assert (from_python["args"], from_python["caller_python"], from_python["virtual_env"]) == (["predict"], None, None)
    # (A python3 that is a pyenv shim puts its own folders in front.)
    assert not {str(caller), str(grandparent)} & set(from_python["path"].split(os.pathsep))
    # Only the dependency's environment: the sourcing shell keeps its virtualenv and PATH.
    assert after == f"{caller.parent}|{path}"


def configure_output(project, strip_ansi, *args):
    return strip_ansi(run_configure(project, *args).stdout)


def test_no_venv_warning_only_when_the_dependency_publishes_none(scaffolded_project, tmp_path, strip_ansi):
    cli.add_deps_to_paths("paths.py", ["callee"])
    warning = "callee has no virtualenv. It will run under whatever Python is active."
    apps = tmp_path / "apps"

    # Not deployed yet: its .env is read when it arrives, with the APP_VENV it publishes.
    assert warning not in configure_output(scaffolded_project, strip_ansi, f"--callee-path={apps / 'callee'}")
    # Deployed, with a ./configure that has not written its .env yet: the same.
    tool = apps / "callee"
    tool.mkdir(parents=True)
    (tool / "configure").write_text("#!/bin/sh\n")
    assert warning not in configure_output(scaffolded_project, strip_ansi)

    # Control: its .env is there and publishes none; and a folder with neither .env nor
    # ./configure (a conda tool, say) publishes none either.
    (tool / ".env").write_text(f"APP_ROOT={tool}\nAPP_NAME=callee\nAPP_ENTRY=run.py\n")
    assert warning in configure_output(scaffolded_project, strip_ansi)
    (tool / ".env").unlink()
    (tool / "configure").unlink()
    assert warning in configure_output(scaffolded_project, strip_ansi)
