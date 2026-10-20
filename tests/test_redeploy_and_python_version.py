"""A tool being installed, a moved dependency, the wrong Python, and an entry with its own #! line.

1. During a redeploy a dependency's tool folder is there, but has no .env (or
   no .venv/bin/python) until its install.sh finishes. dependency_command ran
   the values ./configure saved in its place, and the dependency stopped with
   "This tool has not been configured yet", or ran a half-extracted tree.
2. A dependency that moved, or a mistyped <dep>_python, was only told to try
   again.
3. A Python that requires-python leaves out ran the tool without a word.
4. `uv run --with x` gave a false warning: its Python's sys.path has the
   tool's own virtualenv, under another sys.prefix.
5. A dependency with no virtualenv whose executable entry names its Python in
   its #! line was run with python3.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from argparse import Namespace

import pytest
from test_app_env_2 import real_venv
from test_configure_env import run_configure
from test_redeploy_messages import MOVED, clean_env, deploy, from_python, not_configured, not_deployed

from ngargparser import app_env, cli

WHICH = shutil.which  # the in_tmp_dir fixture hides every program from shutil.which

pytestmark = pytest.mark.skipif(os.name != "posix", reason="needs symlinks and #! lines")

RUNNING = ".".join(str(part) for part in sys.version_info[:3])


def set_requires_python(tool, spec):
    pyproject = tool / "pyproject.toml"
    text, count = re.subn(r'(?m)^requires-python = ".*"$', f'requires-python = "{spec}"', pyproject.read_text())
    assert count == 1
    pyproject.write_text(text)


def run(python, script, *args):
    proc = subprocess.run(
        [str(python), str(script), *args], env=clean_env(), capture_output=True, text=True, timeout=60
    )
    return proc.returncode, proc.stderr


# 1. A tool folder being installed -------------------------------------------


@pytest.mark.parametrize("marker", ["configure", ".ngargparser-build"])
def test_a_tool_folder_without_its_env_is_being_installed_even_with_saved_values(scaffolded_project, tmp_path, marker):
    apps = tmp_path / "apps"
    current = apps / "callee" / "current"
    version = deploy(apps, "1.0")
    if marker != "configure":
        (version / "configure").rename(version / marker)
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={current}")  # saves values that lie in 1.0
    entry = str(version / "src" / "run_callee.py")
    assert from_python(scaffolded_project)["argv"][1:] == [entry, "predict"]  # control

    (version / ".env").rename(version / ".env.away")  # extracted again; install.sh has not finished

    assert from_python(scaffolded_project) == {"error": not_configured(current)}
    assert not_configured(current) == (
        f"callee is being installed at {version} right now (a redeploy in progress?); try again"
    )
    # A same-version redeploy may remove ./configure and .ngargparser-build first:
    # the src/ and .venv/ left still say it is being installed.
    (version / marker).unlink()
    assert from_python(scaffolded_project) == {"error": not_configured(current)}


def test_a_tool_whose_published_venv_has_no_python_yet_is_being_installed(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    current = apps / "callee" / "current"
    version = deploy(apps, "1.0")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={current}")

    (version / ".venv" / "bin" / "python").rename(version / "python.away")

    assert from_python(scaffolded_project) == {"error": not_configured(current)}


@pytest.mark.parametrize("marker", ["", ".ngargparser-build"])
def test_a_deployed_copy_without_env_says_it_is_being_installed(in_tmp_dir, marker):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    demo = in_tmp_dir / "demo"
    (demo / ".env").unlink()
    if marker:
        (demo / marker).write_text("TOOL_NAME=demo\nTOOL_VERSION=1.0.0\n")

    returncode, stderr = run(sys.executable, demo / "src" / "run_demo.py", "--help")

    assert returncode == 1
    # Without .git, any file of a tool folder left counts: a
    # same-version redeploy may remove .ngargparser-build first.
    assert stderr.splitlines() == [
        "This tool is being installed right now (a redeploy in progress?): '.env' is missing; try again.",
        f"If no install is running, run './configure' in the tool folder ({demo}), then re-run this command.",
    ]
    (demo / ".git").mkdir()  # control: a clone, not a deployed copy
    returncode, stderr = run(sys.executable, demo / "src" / "run_demo.py", "--help")
    assert returncode == 1
    assert stderr.startswith("This tool has not been configured yet")


# 2. A dependency that moved, or a mistyped <dep>_python ---------------------


def test_the_try_again_messages_name_the_fix_for_a_move_or_a_mistyped_python(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    current = apps / "callee" / "current"
    version = deploy(apps, "1.0")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={current}")
    assert MOVED == "If callee moved, run ./configure --callee-path=<new folder>"

    version.rename(apps / "callee" / "1.0.away")
    assert from_python(scaffolded_project) == {
        "error": f"callee is not deployed at {current} right now (a redeploy in progress?); try again. "
        "If callee moved, run ./configure --callee-path=<new folder>"
    }
    assert not_deployed(current) == from_python(scaffolded_project)["error"]

    (apps / "callee" / "1.0.away").rename(version)
    env_file = scaffolded_project / ".env"
    env_file.write_text(env_file.read_text() + "CALLEE_PYTHON=/nowhere/python\n")  # callee_python mistyped
    assert from_python(scaffolded_project) == {
        "error": "callee cannot run right now: /nowhere/python does not exist (a redeploy in progress?); "
        "try again. If callee moved, run ./configure --callee-path=<new folder> or fix callee_python in paths.py"
    }


# 3. requires-python ---------------------------------------------------------


@pytest.mark.parametrize(
    "version, spec, expected",
    [
        ((3, 11, 4), ">=3.11,<3.12", True),
        ((3, 12, 0), ">=3.11,<3.12", False),
        ((3, 9, 25), ">=3.11, <3.12", False),
        ((3, 11, 9), "==3.11", True),
        ((3, 11, 9), "==3.11.*", True),
        ((3, 10, 1), "==3.11", False),
        ((3, 12, 1), "~=3.11", True),
        ((4, 0, 0), "~=3.11", False),
        ((3, 11, 1), "~=3.11.2", False),
        ((3, 10, 0), "!=3.10.*", False),
        ((3, 11, 0), "<=3.11", True),
        ((3, 11, 1), ">3.11", True),
        ((3, 11, 4), ">=3.11; python_version", None),
        ((3, 11, 4), "~=3", None),
        ((3, 11, 4), ">=3.11.*", None),
    ],
)
def test_requires_python_is_read_for_major_minor_versions(version, spec, expected):
    assert app_env._satisfies(version, spec) is expected


def test_requires_python_comes_from_the_project_table_only(tmp_path, monkeypatch):
    monkeypatch.setattr(app_env, "PROJECT_ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.other]\nrequires-python = ">=2.7"\n\n'
        "[project]  # the tool\nname = 'x'\nrequires-python = '>=3.11,<3.12'  # pinned\n\n"
        '[dependency-groups]\nrequires-python = "<3"\n'
    )
    assert app_env._requires_python() == ">=3.11,<3.12"
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert app_env._requires_python() is None


def test_a_tool_in_its_own_venv_of_the_wrong_python_stops_with_the_fix(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="x"))
    x = in_tmp_dir / "x"
    own = real_venv(x / ".venv")
    set_requires_python(x, f">={sys.version_info[0]}.{sys.version_info[1]}")
    assert run(own, x / "src" / "run_x.py", "--help") == (0, "")  # control: allowed

    excluded = f"<{sys.version_info[0]}.{sys.version_info[1]}"
    set_requires_python(x, excluded)

    assert run(own, x / "src" / "run_x.py", "--help") == (
        1,
        f"x needs Python {excluded}, but its virtualenv {x / '.venv'} has Python {RUNNING}: "
        f"recreate it: rm -r .venv && uv sync, in {x}\n",
    )


def test_another_python_stops_when_the_tools_own_venv_has_the_right_one(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="x"))
    x = in_tmp_dir / "x"
    set_requires_python(x, "==3.7")
    # Control: with no virtualenv of its own, there is no right Python to name.
    assert run(sys.executable, x / "src" / "run_x.py", "--help") == (0, "")
    venv = x / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.7.17\n")
    (venv / "bin" / "python").write_text("#!/bin/sh\n")  # a regular file, never a link to a real one

    assert run(sys.executable, x / "src" / "run_x.py", "--help") == (
        1,
        f"x needs Python ==3.7, not {RUNNING} ({sys.executable}): run it with {venv / 'bin' / 'python'}\n",
    )


# 4. `uv run --with`: an environment over the tool's own -----------------------


def test_a_python_whose_sys_path_has_the_tools_venv_gets_no_warning(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="x"))
    x = in_tmp_dir / "x"
    own = real_venv(x / ".venv")
    set_requires_python(x, f">={sys.version_info[0]}.{sys.version_info[1]}")
    overlay = real_venv(in_tmp_dir / "overlay")
    warning = (
        f"Warning: x is running with {overlay}, not with its own virtualenv, "
        f"so it gets that Python's packages: run it with {own}\n"
    )
    assert run(overlay, x / "src" / "run_x.py", "--help") == (0, warning)  # control: an unrelated one

    site = f"lib/python{sys.version_info[0]}.{sys.version_info[1]}/site-packages"
    (in_tmp_dir / "overlay" / site / "_overlay.pth").write_text(str(x / ".venv" / site) + "\n")

    assert run(overlay, x / "src" / "run_x.py", "--help") == (0, "")


# 5. An executable entry with its own #! line, no virtualenv -------------------

SHOWS_PYTHON = "import sys\nprint('ran with', sys.executable)\n"


def test_an_executable_entry_runs_with_the_python_its_first_line_names(scaffolded_project, tmp_path):
    tool = tmp_path / "callee"
    entry = tool / "src" / "run_callee.py"
    entry.parent.mkdir(parents=True)
    entry.write_text(f"#!{sys.executable}\n{SHOWS_PYTHON}")
    entry.chmod(0o755)
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={tool}", f"--callee-entry={entry}")

    argv = from_python(scaffolded_project)["argv"]

    assert argv == [str(entry), "predict"]
    child = subprocess.run(argv, env=clean_env(), capture_output=True, text=True, timeout=60)
    assert child.stdout == f"ran with {sys.executable}\n"
    # Control: not executable, python3 from PATH runs it.
    entry.chmod(0o644)
    assert from_python(scaffolded_project)["argv"][1:] == [str(entry), "predict"]
    assert json.dumps(from_python(scaffolded_project)["argv"][0]).endswith('python3"')
