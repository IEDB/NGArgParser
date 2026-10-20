"""What a caller is told while its dependency is redeployed, from Python and from the shell.

The deploy orchestrator removes a version folder, extracts the new one and runs
its install.sh, which runs ./configure there last. A caller configured before
its dependency was deployed has no fallback values, and was told "callee
publishes no entry script: .../.env does not exist. Deploy and configure callee
there, or set callee_entry in paths.py, then run ./configure" in those windows,
where a caller configured after it was told to try again. run_<dep> checked its
entry only for an entry that runs by itself: with the version folder gone, a
Python entry ended in the shell's "not found". dependency_command took a list
as one argument, and subprocess failed on it with a TypeError.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest
from test_configure_env import deploy_version, run_configure

from ngargparser import cli

# The in_tmp_dir fixture hides every program from shutil.which.
WHICH = shutil.which
SHELLS = [shell for shell in ("sh", "bash", "dash") if WHICH(shell)]

pytestmark = pytest.mark.skipif(os.name != "posix" or WHICH("bash") is None, reason="needs bash and symlinks")

PROBE = """\
import json, sys
import core.set_pythonpath
from core.app_env import dependency_command
try:
    argv, env = dependency_command(sys.argv[1], "predict")
except RuntimeError as e:
    print(json.dumps({"error": str(e)}))
else:
    print(json.dumps({"argv": argv}))
"""


def clean_env(**extra):
    """This environment without the values a test run may carry, and no virtualenv."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("APP_", "CALLEE_")) and k != "VIRTUAL_ENV"}
    return {**env, **extra}


def deploy(apps, version):
    """callee <version> in apps/callee/<version>, `current` pointing at it, with a .venv python that runs."""
    venv = deploy_version(apps, version, "run_callee.py")
    python = venv / "bin" / "python"
    if python.is_symlink():  # never write through a symlink into a real interpreter
        python.unlink()
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    (venv.parent / "src" / "run_callee.py").write_text("import sys\nprint('callee ran:', *sys.argv[1:])\n")
    (venv.parent / "configure").write_text("#!/bin/sh\n")  # it is a tool: install.sh runs this last
    return venv.parent


def from_python(project, name="callee", env=None):
    (project / "src" / "probe_redeploy.py").write_text(PROBE)
    proc = subprocess.run(
        [sys.executable, str(project / "src" / "probe_redeploy.py"), name],
        env=env or clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def from_shell(project, shell, env=None):
    """Source setup_callee_env.sh and run run_callee in `shell`: (exit code, stdout, stderr lines)."""
    proc = subprocess.run(
        [WHICH(shell), "-c", "set -u; . ./setup_callee_env.sh; run_callee predict"],
        cwd=project,
        env=env or clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr.splitlines()


MOVED = "If callee moved, run ./configure --callee-path=<new folder>"


def not_deployed(current):
    return f"callee is not deployed at {current} right now (a redeploy in progress?); try again. {MOVED}"


def not_configured(current):
    """A tool folder with no .env (or no .venv/bin/python) yet: its install.sh has not finished."""
    return f"callee is being installed at {os.path.realpath(current)} right now (a redeploy in progress?); try again"


@pytest.mark.parametrize("shell", SHELLS)
def test_a_caller_configured_first_is_told_to_try_again_during_a_redeploy(scaffolded_project, tmp_path, shell):
    apps = tmp_path / "apps"
    current = apps / "callee" / "current"
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={current}")  # callee is not deployed yet
    version = deploy(apps, "1.0")
    # Control: deployed, it runs from both.
    assert from_python(scaffolded_project)["argv"][1:] == [str(version / "src" / "run_callee.py"), "predict"]
    assert from_shell(scaffolded_project, shell) == (0, "callee ran: predict\n", [])

    # The version folder is removed.
    version.rename(apps / "callee" / "1.0.away")
    assert from_python(scaffolded_project) == {"error": not_deployed(current)}
    assert from_shell(scaffolded_project, shell) == (
        127,
        "",
        [f"setup_callee_env.sh: {not_deployed(current)}", not_deployed(current)],
    )

    # The new one is extracted; its install.sh has not run ./configure yet.
    version.mkdir()
    (version / "configure").write_text("#!/bin/sh\n")
    assert from_python(scaffolded_project) == {"error": not_configured(current)}
    assert from_shell(scaffolded_project, shell) == (
        127,
        "",
        [f"setup_callee_env.sh: {not_configured(current)}", not_configured(current)],
    )

    # Control: a folder that is not a tool (no ./configure) and has no .env keeps the old advice.
    (version / "configure").unlink()
    advice = from_python(scaffolded_project)["error"]
    assert advice.startswith("callee publishes no entry script: ") and "set callee_entry in paths.py" in advice
    rc, out, err = from_shell(scaffolded_project, shell)
    assert (rc, out) == (127, "")
    assert err[0].startswith("setup_callee_env.sh: callee publishes no entry script yet")
    assert err[1].startswith("run_callee: callee has no entry script (CALLEE_ENTRY is empty)")


@pytest.mark.parametrize("shell", SHELLS)
def test_run_dep_names_a_python_entry_that_is_gone_as_dependency_command_does(scaffolded_project, tmp_path, shell):
    apps = tmp_path / "apps"
    current = apps / "callee" / "current"
    version = deploy(apps, "1.0")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={current}")  # callee first: its values are the fallback
    python = version / ".venv" / "bin" / "python"
    assert from_shell(scaffolded_project, shell) == (0, "callee ran: predict\n", [])  # control

    # The version folder is removed: its Python went with it.
    version.rename(apps / "callee" / "1.0.away")
    assert from_python(scaffolded_project) == {"error": not_deployed(current)}
    assert from_shell(scaffolded_project, shell) == (127, "", [not_deployed(current)])

    # Extracted again, before uv sync made its .venv: being installed, the saved values do not stand in.
    (apps / "callee" / "1.0.away").rename(version)
    (version / ".env").rename(version / ".env.away")
    python.rename(version / "python.away")
    assert from_python(scaffolded_project) == {"error": not_configured(current)}
    assert from_shell(scaffolded_project, shell) == (
        127,
        "",
        [f"setup_callee_env.sh: {not_configured(current)}", not_configured(current)],
    )

    # Installed, but the entry script is not there.
    (version / "python.away").rename(python)
    (version / ".env.away").rename(version / ".env")
    entry = version / "src" / "run_callee.py"
    entry.rename(version / "src" / "run_callee.py.away")
    missing = f"callee cannot run right now: {entry} does not exist (a redeploy in progress?); try again. {MOVED}"
    assert from_python(scaffolded_project) == {"error": missing}
    assert from_shell(scaffolded_project, shell) == (127, "", [missing])


@pytest.mark.parametrize("shell", SHELLS)
def test_run_dep_names_a_python_that_is_not_on_path_as_dependency_command_does(scaffolded_project, tmp_path, shell):
    # No virtualenv: python3 from PATH runs it, and this PATH has none.
    tool = tmp_path / "callee"
    (tool / "src").mkdir(parents=True)
    (tool / "src" / "run_callee.py").write_text("print('callee ran')\n")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={tool}", f"--callee-entry={tool / 'src' / 'run_callee.py'}")
    tools = tmp_path / "no-python-bin"
    tools.mkdir()
    for program in ("sed", "tail"):  # what the setup script runs
        (tools / program).symlink_to(WHICH(program))
    env = clean_env(PATH=str(tools))

    not_on_path = (
        "callee runs with python3, which is not on PATH: put it there, "
        "or set callee_python in paths.py to its full path, then run ./configure"
    )
    assert from_python(scaffolded_project, env=env) == {"error": not_on_path}
    assert from_shell(scaffolded_project, shell, env=env) == (127, "", [not_on_path])
    # Control: with python3 on PATH, it runs.
    (tools / "python3").symlink_to(sys.executable)
    assert from_shell(scaffolded_project, shell, env=env) == (0, "callee ran\n", [])


# dependency_command('my-app', ['predict', '-h']) returned [..., ['predict', '-h']].

LIST_PROBE = """\
import json, sys
import core.set_pythonpath
from core.app_env import dependency_command
seen = {}
for label, args in (("list", (["predict", "-h"],)), ("tuple", (("predict", "-h"),)), ("unpacked", ("predict", "-h"))):
    try:
        argv, _env = dependency_command("callee", *args)
    except TypeError as e:
        seen[label] = {"TypeError": str(e)}
    else:
        seen[label] = {"argv": argv[1:]}
print(json.dumps(seen))
"""


def test_dependency_command_refuses_a_list_of_arguments_with_one_clear_error(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    version = deploy(apps, "1.0")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    (scaffolded_project / "src" / "probe_list.py").write_text(LIST_PROBE)

    proc = subprocess.run(
        [sys.executable, str(scaffolded_project / "src" / "probe_list.py")],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    seen = json.loads(proc.stdout.strip().splitlines()[-1])
    for kind in ("list", "tuple"):
        assert seen[kind] == {
            "TypeError": f"dependency_command('callee', ...) got a {kind} as one argument; "
            "pass the arguments unpacked: dependency_command('callee', *args)"
        }
    # Control: the arguments unpacked.
    assert seen["unpacked"] == {"argv": [str(version / "src" / "run_callee.py"), "predict", "-h"]}
