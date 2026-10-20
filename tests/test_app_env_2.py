"""src/core/app_env.py: an unreadable .env, another tool's Python, a redeploy of the caller, an unset path.

A .env with mode 000 ended the tool in a PermissionError traceback. A tool run
with another tool's virtualenv (`<other>/.venv/bin/python src/run_x.py`) ran
quietly with that tool's packages. dependency_command re-read the caller's own
.env at each call, so a redeploy of the caller, which removes it for a moment,
gave "is not a dependency of this tool (declared: none)". A dependency whose
path is still None in paths.py was told to run plain ./configure, which then
failed on that None.
"""

import json
import os
import subprocess
import sys
from argparse import Namespace

import dotenv
import pytest
from test_dependency_command import (
    call,
    caller_and_peer,  # noqa: F401 (a fixture)
    configure,
    outside_venvs,
)

from ngargparser import cli

needs_permission_errors = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="root ignores file permissions"
)


def real_venv(path):
    """A virtualenv at `path` made by `python -m venv`, so its python has it as sys.prefix.

    A .pth file adds the test interpreter's site-packages, for python-dotenv.
    """
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(path)], check=True, timeout=120)
    version = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    site = path / "lib" / version / "site-packages"
    (site / "test-packages.pth").write_text(os.path.dirname(os.path.dirname(dotenv.__file__)) + "\n")
    return path / "bin" / "python"


def run(python, script, *args, cwd=None):
    proc = subprocess.run(
        [str(python), str(script), *args], cwd=cwd, env=outside_venvs(), capture_output=True, text=True, timeout=60
    )
    return proc.returncode, proc.stderr


@needs_permission_errors
def test_an_env_that_cannot_be_read_stops_the_tool_with_one_line(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    demo = in_tmp_dir / "demo"
    (demo / ".env").chmod(0o000)
    try:
        returncode, stderr = run(sys.executable, demo / "src" / "run_demo.py", "--help")
    finally:
        (demo / ".env").chmod(0o644)

    assert (returncode, stderr) == (
        1,
        f"{demo / '.env'} cannot be read (Permission denied): run ./configure in {demo} to write it again, "
        "or ask its owner to make it readable\n",
    )
    # Control: readable again, the tool starts.
    assert run(sys.executable, demo / "src" / "run_demo.py", "--help") == (0, "")


# Another tool's Python: one warning ------------------------------------------

LOADS_TWICE = """\
import core.set_pythonpath
from core.app_env import load_app_env
load_app_env()
print("started")
"""


@pytest.fixture
def tool_with_a_venv(in_tmp_dir, monkeypatch):
    """Tool 'x' with a real .venv, which ./configure publishes as APP_VENV, and 'other' with its own."""
    assert not cli.startapp_command(Namespace(project_name="x"))
    x = in_tmp_dir / "x"
    own = real_venv(x / ".venv")
    # Its .venv is made with the Python running the tests: requires-python allows it.
    pyproject = x / "pyproject.toml"
    running = f">={sys.version_info[0]}.{sys.version_info[1]}"
    pyproject.write_text(
        pyproject.read_text().replace('requires-python = ">=3.11,<3.12"', f'requires-python = "{running}"')
    )
    assert running in pyproject.read_text()
    configure(x)
    assert f"APP_VENV={x / '.venv'}" in (x / ".env").read_text()
    other = real_venv(in_tmp_dir / "other" / ".venv")
    (x / "src" / "loads_twice.py").write_text(LOADS_TWICE)
    return x, own, other


def test_a_tool_run_with_another_tools_python_warns_once(tool_with_a_venv):
    x, own, other = tool_with_a_venv
    warning = (
        f"Warning: x is running with {other}, not with its own virtualenv, "
        f"so it gets that Python's packages: run it with {own}\n"
    )

    assert run(other, x / "src" / "run_x.py", "--help") == (0, warning)
    assert run(other, x / "src" / "loads_twice.py") == (0, warning)  # load_app_env runs twice, one warning
    # Control: its own Python says nothing.
    assert run(own, x / "src" / "run_x.py", "--help") == (0, "")
    assert run(own, x / "src" / "loads_twice.py") == (0, "")


def test_the_builds_startup_venv_and_a_tool_without_a_venv_get_no_warning(tool_with_a_venv, in_tmp_dir):
    x, _own, other = tool_with_a_venv
    # make build runs the help screens with .ngargparser/startup-venv.
    startup = real_venv(x / ".ngargparser" / "startup-venv")
    assert run(startup, x / "src" / "run_x.py", "--help") == (0, "")
    # A tool with no virtualenv of its own has none to name.
    assert not cli.startapp_command(Namespace(project_name="novenv"))
    novenv = in_tmp_dir / "novenv"
    configure(novenv)
    assert run(other, novenv / "src" / "run_novenv.py", "--help") == (0, "")


CALLS_X = """\
import json, subprocess, sys
import core.set_pythonpath
from core.app_env import dependency_command
argv, env = dependency_command("x", "--help")
child = subprocess.run(argv, env=env, capture_output=True, text=True)
print(json.dumps({"argv": argv, "rc": child.returncode, "err": child.stderr}))
"""


def test_a_tool_dependency_command_runs_gets_no_warning(tool_with_a_venv, in_tmp_dir, monkeypatch):
    x, own, _other = tool_with_a_venv
    assert not cli.startapp_command(Namespace(project_name="demo"))
    demo = in_tmp_dir / "demo"
    monkeypatch.chdir(demo)
    cli.add_deps_to_paths("paths.py", ["x"])
    configure(demo, f"--x-path={x}")
    (demo / "src" / "calls_x.py").write_text(CALLS_X)

    proc = subprocess.run(
        [sys.executable, str(demo / "src" / "calls_x.py")], env=outside_venvs(), capture_output=True, text=True
    )

    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out == {"argv": [str(own), str(x / "src" / "run_x.py"), "--help"], "rc": 0, "err": ""}


# A redeploy of the caller while it runs -------------------------------------

CALLER_REDEPLOYED = """\
import json, os, subprocess, sys
how = sys.argv[1]
env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if how == "missing-at-start":  # before anything loaded it
    os.rename(env_file, env_file + ".away")
else:
    import core.set_pythonpath
from core.app_env import dependency_command
if how == "missing":  # removed with the version folder, the new one not extracted yet
    os.rename(env_file, env_file + ".away")
elif how == "empty":
    open(env_file, "w").close()
try:
    argv, env = dependency_command("peer", "predict")
except RuntimeError as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(0)
child = subprocess.run(argv, env=env, capture_output=True, text=True)
print(json.dumps({"argv": argv, "rc": child.returncode}))
"""


def caller_redeployed(demo, how):
    (demo / "src" / "redeployed_caller.py").write_text(CALLER_REDEPLOYED)
    env_file = demo / ".env"
    kept = env_file.read_bytes()
    try:
        proc = subprocess.run(
            [sys.executable, str(demo / "src" / "redeployed_caller.py"), how],
            env=outside_venvs(),
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        if os.path.exists(f"{env_file}.away"):
            os.rename(f"{env_file}.away", env_file)
        env_file.write_bytes(kept)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("how", ["missing", "empty"])
def test_a_caller_whose_own_env_goes_away_while_it_runs_uses_the_values_it_started_with(caller_and_peer, how):  # noqa: F811
    demo, peer, venv, _lib = caller_and_peer

    out = caller_redeployed(demo, how)

    assert out == {"argv": [str(venv / "bin" / "python"), str(peer / "src" / "echo_peer.py"), "predict"], "rc": 0}
    # Control: with its .env in place, the same call.
    assert caller_redeployed(demo, "unchanged") == out


def test_a_caller_with_no_env_now_nor_at_start_up_says_so(caller_and_peer):  # noqa: F811
    demo, _peer, _venv, _lib = caller_and_peer

    assert caller_redeployed(demo, "missing-at-start") == {
        "error": f"cannot run peer: this tool's .env is missing (a redeploy in progress?); "
        f"try again, or run ./configure in {demo}"
    }


# A dependency whose path is None in paths.py --------------------------------


@pytest.mark.parametrize(
    "line, name, flag",
    [
        ("netmhc_path = None", "netmhc", "--netmhc-path=<path>: paths.py declares netmhc, but netmhc_path is None"),
        (
            "tcell_class_i_path: str = None",
            "tcell-class-i",
            "--tcell-class-i-path=<path>: paths.py declares tcell-class-i, but tcell_class_i_path is None",
        ),
        ("blank_path = '  '", "blank", "--blank-path=<path>: paths.py declares blank, but blank_path is None"),
    ],
    ids=["none", "annotated", "blank"],
)
def test_a_dependency_whose_path_is_none_names_the_flag_that_sets_it(caller_and_peer, line, name, flag):  # noqa: F811
    demo, _peer, _venv, _lib = caller_and_peer
    paths = demo / "paths.py"
    paths.write_text(paths.read_text() + f"{line}\n")  # no ./configure since

    assert call(demo, name) == {"error": f"run ./configure {flag} there"}


def test_a_dependency_with_a_path_in_paths_py_still_says_to_run_configure(caller_and_peer):  # noqa: F811
    # Control: a path set (or one paths.py works out) needs only a plain ./configure.
    demo, _peer, _venv, _lib = caller_and_peer
    paths = demo / "paths.py"
    paths.write_text(paths.read_text() + "netmhc_path = '/opt/netmhc'\nother_path = os.environ.get('OTHER', None)\n")

    for name in ("netmhc", "other"):
        assert call(demo, name) == {
            "error": f"run ./configure: paths.py declares {name}, but .env has no values for it yet"
        }
