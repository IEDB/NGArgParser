"""dependency_command in src/core/app_env.py runs a dependency from Python.

Python callers built the command themselves from <DEP>_VENV and <DEP>_ENTRY, and
so skipped <dep>_lib_path, which only the bash setup script put on
LD_LIBRARY_PATH. These tests scaffold a caller ('demo') and a dependency ('peer'):
peer gets a .venv whose python marks that it ran, and an entry script that
prints what it was started with; demo declares peer with a lib path and calls it
through dependency_command.
"""

import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from ngargparser import cli

# The in_tmp_dir fixture hides every program from shutil.which; these tests look up python3.
WHICH = shutil.which

ECHO_PEER = """\
import json, os, shutil, sys
print(json.dumps({
    "args": sys.argv[1:],
    "peer_python": os.environ.get("PEER_PYTHON_RAN"),
    "caller_python": os.environ.get("CALLER_PYTHON_RAN"),
    "ld_library_path": os.environ.get("LD_LIBRARY_PATH"),
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
    "python_by_name": shutil.which("python"),
}))
"""

# PYTHONHOME is set inside the caller, from CALLER_PYTHONHOME: the caller's own
# Python would not start with it.
CALLER = """\
import json, os, subprocess, sys
import core.set_pythonpath
from core.app_env import dependency_command
if os.environ.get("CALLER_PYTHONHOME"):
    os.environ["PYTHONHOME"] = os.environ["CALLER_PYTHONHOME"]
try:
    argv, env = dependency_command(sys.argv[1], "predict", "-j", "job.json")
except RuntimeError as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(0)
child = subprocess.run(argv, env=env, capture_output=True, text=True)
print(json.dumps({
    "argv": argv,
    "python": sys.executable,
    "env": {k: env.get(k) for k in ("VIRTUAL_ENV", "PATH", "PYTHONHOME")},
    "peer_python_var": os.environ.get("PEER_PYTHON"),
    "child": json.loads(child.stdout.strip().splitlines()[-1]) if child.returncode == 0 else child.stderr,
}))
"""


def configure(project, *args):
    proc = subprocess.run(
        [sys.executable, "src/core/configure.py", *args], cwd=project, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def give_venv(project):
    """A .venv that ./configure publishes, whose python runs the test interpreter with PEER_PYTHON_RAN=1."""
    venv = project / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (venv / "bin" / "activate").write_text(f'VIRTUAL_ENV="{venv}"\nexport VIRTUAL_ENV\n')
    python = venv / "bin" / "python"
    if python.is_symlink():  # never write through a symlink into a real interpreter
        python.unlink()
    python.write_text(f'#!/bin/sh\nPEER_PYTHON_RAN=1 exec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    return venv


@pytest.fixture
def caller_and_peer(in_tmp_dir, monkeypatch):
    for name in ("demo", "peer"):
        assert not cli.startapp_command(Namespace(project_name=name))
    demo, peer = in_tmp_dir / "demo", in_tmp_dir / "peer"
    (peer / "src" / "echo_peer.py").write_text(ECHO_PEER)
    pyproject = peer / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('entry = "src/run_peer.py"', 'entry = "src/echo_peer.py"'))
    venv = give_venv(peer)
    configure(peer)
    monkeypatch.chdir(demo)
    cli.add_deps_to_paths("paths.py", ["peer"])
    lib = in_tmp_dir / "peer-lib"
    configure(demo, f"--peer-path={peer}", f"--peer-lib-path={lib}")
    (demo / "src" / "call_peer.py").write_text(CALLER)
    return demo, peer, venv, lib


def run_caller(demo, name="peer", **env):
    base = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("APP_", "PEER_")) and k not in ("LD_LIBRARY_PATH", "LOADEDMODULES")
    }
    proc = subprocess.run(
        [sys.executable, str(demo / "src" / "call_peer.py"), name],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def call(demo, name="peer", **env):
    return json.loads(run_caller(demo, name, **env).stdout.strip().splitlines()[-1])


def test_a_dependency_runs_with_its_python_entry_and_lib_path(caller_and_peer):
    demo, peer, venv, lib = caller_and_peer

    out = call(demo)

    assert out["argv"] == [
        str(venv / "bin" / "python"),
        str(peer / "src" / "echo_peer.py"),
        "predict",
        "-j",
        "job.json",
    ]
    child = {key: out["child"][key] for key in ("args", "peer_python", "ld_library_path")}
    assert child == {"args": ["predict", "-j", "job.json"], "peer_python": "1", "ld_library_path": str(lib)}
    # An LD_LIBRARY_PATH already set comes after the dependency's lib path.
    assert call(demo, LD_LIBRARY_PATH="/opt/other/lib")["child"]["ld_library_path"] == f"{lib}:/opt/other/lib"


def caller_venv(tmp_path):
    """A virtualenv the caller runs in, as `uv run` sets it up, whose python and python3 mark that they ran."""
    venv = tmp_path / "caller-venv"
    (venv / "bin").mkdir(parents=True)
    for name in ("python", "python3"):
        python = venv / "bin" / name
        if python.is_symlink():  # never write through a symlink into a real interpreter
            python.unlink()
        python.write_text(f'#!/bin/sh\nCALLER_PYTHON_RAN=1 exec "{sys.executable}" "$@"\n')
        python.chmod(0o755)
    return venv, f"{venv / 'bin'}{os.pathsep}{os.environ['PATH']}"


# The environment was the caller's own, with only LD_LIBRARY_PATH changed. A
# dependency that runs `python` or a console script by name got the caller's
# virtualenv: VIRTUAL_ENV and PATH still named it.


def test_the_dependency_runs_in_its_virtualenv_as_activating_it_does(caller_and_peer, tmp_path):
    demo, _peer, venv, _lib = caller_and_peer
    callers, path = caller_venv(tmp_path)

    out = call(demo, VIRTUAL_ENV=str(callers), PATH=path, CALLER_PYTHONHOME="/opt/caller-home")

    assert out["env"] == {"VIRTUAL_ENV": str(venv), "PATH": f"{venv / 'bin'}{os.pathsep}{path}", "PYTHONHOME": None}
    assert out["child"]["virtual_env"] == str(venv)
    assert out["child"]["python_by_name"] == str(venv / "bin" / "python")
    assert (out["child"]["peer_python"], out["child"]["caller_python"]) == ("1", None)


# Without a virtualenv the dependency ran under the caller's Python
# (sys.executable), inside the caller's virtualenv.


def test_a_dependency_without_a_venv_runs_with_python3_outside_the_callers_venv(caller_and_peer, tmp_path):
    demo, peer, venv, _lib = caller_and_peer
    venv.rename(peer / "no-venv")
    configure(peer)  # publishes no APP_VENV now
    callers, path = caller_venv(tmp_path)

    proc = run_caller(demo, VIRTUAL_ENV=str(callers), PATH=path)
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    env_path = out["env"]["PATH"].split(os.pathsep)
    assert str(callers / "bin") not in env_path
    assert out["env"]["VIRTUAL_ENV"] is None
    python3 = WHICH("python3", path=out["env"]["PATH"])
    if python3 is None:
        pytest.skip("no python3 on PATH outside the virtualenvs")
    assert out["argv"][:2] == [python3, str(peer / "src" / "echo_peer.py")]
    assert proc.stderr.splitlines() == [f"peer publishes no virtualenv; running it with {python3}"]
    assert (out["child"]["peer_python"], out["child"]["caller_python"], out["child"]["virtual_env"]) == (
        None,
        None,
        None,
    )


def test_a_dependency_without_an_entry_script_says_how_to_set_one(caller_and_peer):
    demo, peer, _venv, _lib = caller_and_peer
    env_file = peer / ".env"
    env_file.write_text("".join(line for line in env_file.read_text().splitlines(True) if "APP_ENTRY=" not in line))

    out = call(demo)

    # Not the --peer-path flag: the path is set, and peer publishes no entry.
    assert out == {
        "error": f"peer publishes no entry script (APP_ENTRY in {env_file}, or [tool.ngargparser] entry): "
        "set peer_entry in paths.py, then run ./configure"
    }


def test_a_dependency_not_deployed_yet_says_its_env_is_missing(caller_and_peer):
    demo, peer, _venv, _lib = caller_and_peer
    (peer / ".env").unlink()
    configure(demo)  # peer's entry script is no longer known

    # peer has a ./configure, so a .env that is not there is most often a redeploy.
    assert call(demo) == {
        "error": f"peer is being installed at {os.path.realpath(peer)} right now (a redeploy in progress?); try again"
    }


# A name that is not a dependency was told to set its path with a flag that
# does not exist: ./configure --aa-countr-path=...


def test_a_name_that_is_not_a_dependency_says_so_and_names_a_close_one(caller_and_peer):
    demo, _peer, _venv, _lib = caller_and_peer

    assert call(demo, "peerr") == {
        "error": "peerr is not a dependency of this tool (declared: peer): "
        "check the name (did you mean peer?), or add it with cli deps add peerr"
    }
    assert call(demo, "some-tool") == {
        "error": "some-tool is not a dependency of this tool (declared: peer): "
        "check the name, or add it with cli deps add some-tool"
    }


def test_a_dependency_paths_py_declares_before_configure_runs_says_to_run_it(caller_and_peer):
    demo, _peer, _venv, _lib = caller_and_peer
    cli.add_deps_to_paths("paths.py", ["other-tool"])  # no ./configure since
    paths = demo / "paths.py"
    paths.write_text(paths.read_text().replace("other_tool_path=None", "other_tool_path='/opt/other-tool'"))

    assert call(demo, "other-tool") == {
        "error": "run ./configure: paths.py declares other-tool, but .env has no values for it yet"
    }
    # With its path still None, the flag that sets it: tests/test_app_env_2.py.


# A dependency with <dep>_module ran without its module, silently.


def test_a_dependency_with_a_module_warns_that_it_is_not_loaded(caller_and_peer):
    demo, _peer, _venv, _lib = caller_and_peer
    configure(demo, "--peer-module=peer/1.0")

    assert run_caller(demo).stderr.splitlines() == [
        "peer needs the environment module peer/1.0, which dependency_command cannot load: "
        "load it before starting this tool, or call the dependency through setup_peer_env.sh"
    ]
    # Control: loaded before the tool started, as the warning says.
    assert run_caller(demo, LOADEDMODULES="gcc/9:peer/1.0").stderr == ""


# The two routes disagreed without a virtualenv: Python ran the dependency with
# the caller's Python, the shell's "$PEER_VENV/bin/python" became "/bin/python".
# Both now use <DEP>_PYTHON: the virtualenv's python, else python3.

PYTHON_PROBE = """\
import json, os, sys
import core.set_pythonpath
from core.app_env import dependency_command
argv, env = dependency_command("peer")
print(json.dumps({"PEER_PYTHON": os.environ.get("PEER_PYTHON"), "argv0": argv[0], "PATH": env.get("PATH")}))
"""


def outside_venvs():
    """This environment without a virtualenv: no VIRTUAL_ENV, and no venv bin/ on PATH.

    That is this Python's, VIRTUAL_ENV's, and the bin/ folder of every other
    virtualenv on PATH (its parent folder holds a pyvenv.cfg), as
    dependency_command leaves them out.
    """
    bins = {
        os.path.realpath(os.path.join(prefix, "bin"))
        for prefix in (sys.prefix, os.environ.get("VIRTUAL_ENV"))
        if prefix
    }

    def in_a_venv(folder):
        real = os.path.realpath(folder)
        return real in bins or os.path.isfile(os.path.join(os.path.dirname(real), "pyvenv.cfg"))

    path = os.pathsep.join(d for d in os.environ["PATH"].split(os.pathsep) if not (d and in_a_venv(d)))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("APP_", "PEER_")) and k != "VIRTUAL_ENV"}
    return {**env, "PATH": path}


@pytest.mark.skipif(WHICH("bash") is None, reason="needs bash")
@pytest.mark.parametrize("venv_kind", ["published", "set-in-paths-py", "none"])
def test_python_and_the_setup_script_pick_the_same_python(caller_and_peer, tmp_path, venv_kind):
    demo, peer, venv, _lib = caller_and_peer
    if venv_kind == "set-in-paths-py":
        venv = give_venv(tmp_path / "elsewhere")
        configure(demo, f"--peer-venv={venv}")
    elif venv_kind == "none":
        venv.rename(peer / "no-venv")
        configure(peer)
        configure(demo)
    env = outside_venvs()
    (demo / "src" / "probe_python.py").write_text(PYTHON_PROBE)
    python = subprocess.run(
        [sys.executable, str(demo / "src" / "probe_python.py")], env=env, capture_output=True, text=True, timeout=60
    )
    assert python.returncode == 0, python.stderr
    seen = json.loads(python.stdout.strip().splitlines()[-1])
    if venv_kind == "none" and WHICH("python3", path=seen["PATH"]) is None:
        pytest.skip("no python3 on PATH outside the virtualenvs")
    shell = subprocess.run(
        [
            "bash",
            "-c",
            '. ./setup_peer_env.sh; echo "${PEER_PYTHON-<unset>}"; command -v "$PEER_PYTHON" || echo "<not found>"',
        ],
        cwd=demo,
        env={**env, "PATH": seen["PATH"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert shell.returncode == 0, shell.stderr

    def resolved(program):
        """The real path of `program`, a path or a name looked up on the PATH dependency_command gives."""
        return os.path.realpath(program if os.sep in program else WHICH(program, path=seen["PATH"]) or program)

    # The same program, as a name or the absolute path of the first python3 on that PATH.
    expected = str(venv / "bin" / "python") if venv_kind != "none" else "python3"
    shell_python, shell_found = shell.stdout.splitlines()[:2]
    assert resolved(shell_python) == resolved(seen["PEER_PYTHON"]) == resolved(expected)
    assert resolved(shell_found) == resolved(seen["argv0"])

    # With a caller's virtualenv first on PATH, as `uv run` leaves it, run_peer
    # runs what dependency_command runs, in the same environment. Without a
    # virtualenv of its own, run_peer ran python3 from the caller's.
    callers, _path = caller_venv(tmp_path)
    in_a_venv = {**env, "VIRTUAL_ENV": str(callers), "PATH": f"{callers / 'bin'}{os.pathsep}{env['PATH']}"}
    in_a_venv.pop("LD_LIBRARY_PATH", None)
    from_python = run_caller(demo, **{k: v for k, v in in_a_venv.items() if k in ("VIRTUAL_ENV", "PATH")})
    from_python = json.loads(from_python.stdout.strip().splitlines()[-1])["child"]
    from_shell = subprocess.run(
        ["bash", "-c", '. ./setup_peer_env.sh; run_peer predict -j job.json; echo "$VIRTUAL_ENV"'],
        cwd=demo,
        env=in_a_venv,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert from_shell.returncode == 0, from_shell.stderr
    child, virtual_env = from_shell.stdout.strip().splitlines()[-2:]
    assert json.loads(child) == from_python
    assert from_python["caller_python"] is None
    # Sourcing leaves the shell's own virtualenv as it was; run_peer activates peer's in its own subshell.
    assert virtual_env == str(callers)


def test_an_inherited_python_value_is_replaced(caller_and_peer):
    # PEER_PYTHON from a calling tool names its own peer, maybe another install.
    demo, _peer, venv, _lib = caller_and_peer

    out = call(demo, PEER_PYTHON="/callers/peer/.venv/bin/python")

    assert out["peer_python_var"] == str(venv / "bin" / "python")


# A dependency is "another tool, or any program", but dependency_command ran
# every entry with python: a shell script ended in a SyntaxError, and so did
# "$PEER_PYTHON" "$PEER_ENTRY" after sourcing the setup script.

SHELL_PEER = """\
#!/bin/sh
printf '%s\\n' "shell peer ran with: $*"
"""

PYTHON_PEER_NO_SUFFIX = """\
#!/usr/bin/env python3
import os, sys
print("python peer ran with: " + " ".join(sys.argv[1:]) + " PEER_PYTHON_RAN=" + str(os.environ.get("PEER_PYTHON_RAN")))
"""

ENTRY_PROBE = """\
import json, os, subprocess, sys
import core.set_pythonpath
from core.app_env import dependency_command
try:
    argv, env = dependency_command("peer", "predict", "-j", "job.json")
except RuntimeError as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(0)
child = subprocess.run(argv, env=env, capture_output=True, text=True)
print(json.dumps({"argv": argv, "PEER_PYTHON": os.environ.get("PEER_PYTHON"), "out": child.stdout, "err": child.stderr}))
"""


def publish_entry(peer, rel, text, mode=0o755):
    """Make `rel` peer's start-up script, published in its .env as APP_ENTRY."""
    script = peer / rel
    script.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        script.write_bytes(text)
    else:
        script.write_text(text)
    script.chmod(mode)
    pyproject = peer / "pyproject.toml"
    lines = [
        f'entry = "{rel}"\n' if line.startswith("entry = ") else line for line in pyproject.read_text().splitlines(True)
    ]
    pyproject.write_text("".join(lines))
    configure(peer)
    return script


def probe_entry(demo):
    (demo / "src" / "probe_entry.py").write_text(ENTRY_PROBE)
    proc = subprocess.run(
        [sys.executable, str(demo / "src" / "probe_entry.py")], env=outside_venvs(), capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def shell_call(demo, shell):
    """Source setup_peer_env.sh in `shell` and run peer the one way the docs give: ${PEER_PYTHON:+...}."""
    call = '. ./setup_peer_env.sh; echo "PEER_PYTHON=[$PEER_PYTHON]"; ${PEER_PYTHON:+"$PEER_PYTHON"} "$PEER_ENTRY" predict -j job.json'
    proc = subprocess.run(
        [shell, "-c", f"set -eu; {call}"], cwd=demo, env=outside_venvs(), capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.splitlines()


@pytest.mark.skipif(WHICH("bash") is None or WHICH("echo") is None, reason="needs bash and echo")
def test_an_entry_that_is_not_python_runs_by_itself_from_python_and_the_shell(caller_and_peer):
    demo, peer, venv, _lib = caller_and_peer
    shells = [shell for shell in ("bash", "dash") if WHICH(shell)]

    # A shell script: its #! line does not name python.
    script = publish_entry(peer, "bin/peer.sh", SHELL_PEER)
    out = probe_entry(demo)
    assert out["argv"] == [str(script), "predict", "-j", "job.json"]
    assert (out["out"], out["PEER_PYTHON"]) == ("shell peer ran with: predict -j job.json\n", "")
    for shell in shells:
        assert shell_call(demo, shell) == ["PEER_PYTHON=[]", "shell peer ran with: predict -j job.json"], shell

    # A program: no #! line, and a name without .py.
    program = publish_entry(peer, "bin/peer-echo", Path(WHICH("echo")).read_bytes())
    out = probe_entry(demo)
    assert out["argv"] == [str(program), "predict", "-j", "job.json"]
    assert out["out"] == "predict -j job.json\n"
    for shell in shells:
        assert shell_call(demo, shell) == ["PEER_PYTHON=[]", "predict -j job.json"], shell

    # Control: a #! line that names python is a Python script, whatever its name,
    # and runs with the dependency's virtualenv, the same one call in the shell.
    python_script = publish_entry(peer, "bin/peer", PYTHON_PEER_NO_SUFFIX)
    out = probe_entry(demo)
    python = str(venv / "bin" / "python")
    assert out["argv"] == [python, str(python_script), "predict", "-j", "job.json"]
    assert (out["out"], out["PEER_PYTHON"]) == ("python peer ran with: predict -j job.json PEER_PYTHON_RAN=1\n", python)
    for shell in shells:
        assert shell_call(demo, shell) == [
            f"PEER_PYTHON=[{python}]",
            "python peer ran with: predict -j job.json PEER_PYTHON_RAN=1",
        ], shell


# The only shell call the docs gave was the idiom ${PEER_PYTHON:+"$PEER_PYTHON"}
# "${PEER_ENTRY:?}" predict ... The setup script now defines run_<dep> too,
# which runs the dependency the same way: run_peer predict -j job.json.

ARGS_PEER = """\
#!/bin/sh
printf '[%s]' "$@"; echo
exit 3
"""


@pytest.mark.parametrize("shell", [shell for shell in ("sh", "bash", "dash") if WHICH(shell)])
def test_run_dep_runs_the_dependency_as_the_one_line_call_does(caller_and_peer, shell):
    demo, peer, _venv, _lib = caller_and_peer

    env = {k: v for k, v in outside_venvs().items() if k != "LD_LIBRARY_PATH"}  # as run_caller gives it

    def run(call):
        proc = subprocess.run(
            [shell, "-c", f"set -eu; . ./setup_peer_env.sh; {call}"],
            cwd=demo,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc.returncode, proc.stdout, proc.stderr

    # A Python entry: run_peer runs what dependency_command runs, in the same environment.
    seen = run("run_peer predict -j job.json")
    assert (seen[0], seen[2]) == (0, "")
    from_python = json.loads(run_caller(demo, PATH=env["PATH"]).stdout.strip().splitlines()[-1])["child"]
    assert json.loads(seen[1].splitlines()[-1]) == from_python
    assert from_python["peer_python"] == "1"
    # Each argument as given. The one-line form runs the same entry with $PEER_PYTHON, the
    # virtualenv's python, but not inside the virtualenv: sourcing does not activate it.
    seen = run("run_peer predict -j 'job 1.json' '$HOME'")
    raw = run("${PEER_PYTHON:+\"$PEER_PYTHON\"} \"${PEER_ENTRY:?}\" predict -j 'job 1.json' '$HOME'")
    assert (seen[0], seen[2], raw[0], raw[2]) == (0, "", 0, "")
    child, raw_child = (json.loads(out[1].splitlines()[-1]) for out in (seen, raw))
    assert child["args"] == raw_child["args"] == ["predict", "-j", "job 1.json", "$HOME"]
    assert child["peer_python"] == raw_child["peer_python"] == "1"
    # An entry that runs by itself. Its exit status is run_peer's, so `set -e` stops the caller there.
    publish_entry(peer, "bin/peer.sh", ARGS_PEER)
    assert run("run_peer a 'b c'; echo not reached") == (3, "[a][b c]\n", "")
    assert run('run_peer || echo "status $?"') == (0, "[]\nstatus 3\n", "")
    # No entry known: a message, and 127, as for a command that is not found.
    env_file = peer / ".env"
    env_file.write_text("".join(line for line in env_file.read_text().splitlines(True) if "APP_ENTRY=" not in line))
    rc, out, err = run("run_peer predict; echo not reached")
    assert (rc, out) == (127, "")
    assert err.splitlines()[-1] == (
        "run_peer: peer has no entry script (PEER_ENTRY is empty): deploy and configure peer, "
        "or set peer_entry in paths.py and run ./configure, then source setup_peer_env.sh again"
    )


def test_an_entry_that_is_neither_python_nor_executable_says_how_to_fix_it(caller_and_peer):
    demo, peer, _venv, _lib = caller_and_peer
    script = publish_entry(peer, "bin/peer.sh", SHELL_PEER, mode=0o644)

    assert probe_entry(demo) == {
        "error": f"peer's entry {script} is not a Python script and not executable: chmod +x it, "
        "or point peer_entry in paths.py at its start-up script"
    }


# The caller's identity reached the dependency: a plain script printed the
# caller's APP_ROOT and APP_NAME, and what the caller publishes to its own callers.

IDENTITY_PEER = """\
import json, os
print(json.dumps({k: os.environ.get(k) for k in ("APP_ROOT", "APP_NAME", "APP_VENV", "APP_ENTRY", "APP_KEYS_FROM_DEPS")}))
"""


def test_the_dependency_does_not_get_the_callers_identity(caller_and_peer):
    demo, peer, _venv, _lib = caller_and_peer
    (peer / "src" / "echo_peer.py").write_text(IDENTITY_PEER)
    give_venv(demo)
    configure(demo)  # demo now publishes APP_VENV and APP_ENTRY, and follows PEER_VENV, PEER_ENTRY
    demo_env = (demo / ".env").read_text()
    assert f"APP_VENV={demo / '.venv'}" in demo_env and "APP_KEYS_FROM_DEPS=" in demo_env

    out = probe_entry(demo)

    assert json.loads(out["out"]) == dict.fromkeys(
        ("APP_ROOT", "APP_NAME", "APP_VENV", "APP_ENTRY", "APP_KEYS_FROM_DEPS")
    )


# A <dep>_python set in paths.py got three answers: os.environ had it, but
# dependency_command and the setup script ran the dependency's virtualenv python.


@pytest.mark.skipif(WHICH("bash") is None, reason="needs bash")
def test_a_python_set_in_paths_py_wins_everywhere(caller_and_peer, tmp_path):
    demo, _peer, _venv, _lib = caller_and_peer
    chosen = tmp_path / "chosen" / "python"
    chosen.parent.mkdir()
    chosen.write_text(f'#!/bin/sh\nCHOSEN_PYTHON_RAN=1 exec "{sys.executable}" "$@"\n')
    chosen.chmod(0o755)
    paths = demo / "paths.py"
    paths.write_text(paths.read_text() + f"peer_python = '{chosen}'\n")
    configure(demo)

    out = probe_entry(demo)
    shell = subprocess.run(
        ["bash", "-c", '. ./setup_peer_env.sh; echo "$PEER_PYTHON"'],
        cwd=demo,
        env=outside_venvs(),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert out["argv"][0] == out["PEER_PYTHON"] == shell.stdout.strip() == str(chosen)
    # Control: without it, all three are the virtualenv's python again.
    paths.write_text(paths.read_text().replace(f"peer_python = '{chosen}'\n", ""))
    configure(demo)
    venv_python = str(_venv / "bin" / "python")
    shell = subprocess.run(
        ["bash", "-c", '. ./setup_peer_env.sh; echo "$PEER_PYTHON"'],
        cwd=demo,
        env=outside_venvs(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = probe_entry(demo)
    assert out["argv"][0] == out["PEER_PYTHON"] == shell.stdout.strip() == venv_python


# A copy of a dependency that was not configured again kept the original's .env:
# ./configure said "using virtualenv declared in its .env: <original>/.venv", and
# dependency_command and the setup script ran the original folder's script.


@pytest.mark.skipif(WHICH("bash") is None, reason="needs bash")
def test_a_copied_dependency_runs_from_the_copy_not_the_original(caller_and_peer, strip_ansi):
    demo, peer, _venv, _lib = caller_and_peer
    copy = peer.parent / "peer-copy"
    shutil.copytree(peer, copy, symlinks=True)

    proc = subprocess.run(
        [sys.executable, "src/core/configure.py", f"--peer-path={copy}"],
        cwd=demo,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = strip_ansi(proc.stdout).splitlines()
    assert (
        f"⚠  peer's .env was written for {peer}: run ./configure in {copy} first; its values are not used until then."
    ) in lines
    assert f"* peer: using start-up script found at {copy / 'src' / 'echo_peer.py'}" in lines
    assert not [line for line in lines if "declared in its .env" in line]
    # From Python, and from the shell: the copy's virtualenv and script.
    assert probe_entry(demo)["argv"][:2] == [str(copy / ".venv" / "bin" / "python"), str(copy / "src" / "echo_peer.py")]
    shell = subprocess.run(
        ["bash", "-c", '. ./setup_peer_env.sh; echo "$PEER_VENV"; echo "$PEER_ENTRY"'],
        cwd=demo,
        env=outside_venvs(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert shell.stdout.splitlines() == [str(copy / ".venv"), str(copy / "src" / "echo_peer.py")]
    # Control: the original, whose .env was written for it, is still read from its .env.
    configure(demo, f"--peer-path={peer}")
    assert probe_entry(demo)["argv"][:2] == [str(peer / ".venv" / "bin" / "python"), str(peer / "src" / "echo_peer.py")]


# The setup script counts a .env written for another folder as missing
# (_ng_env=''). Dropping that rule broke no test: with a relative APP_ENTRY and a
# .venv in the copy, the script ended up with the copy's files anyway. A copy
# with no .venv of its own, whose .env names the original's virtualenv and an
# absolute entry script, ran the original's.


@pytest.mark.parametrize("shell", [shell for shell in ("sh", "bash", "dash") if WHICH(shell)])
def test_a_copy_without_its_own_venv_uses_none_of_the_originals_files_in_the_shell(caller_and_peer, shell):
    demo, peer, venv, _lib = caller_and_peer
    env_file = peer / ".env"
    env_file.write_text(
        env_file.read_text().replace("APP_ENTRY=src/echo_peer.py", f"APP_ENTRY={peer}/src/echo_peer.py")
    )
    assert f"APP_VENV={venv}\n" in env_file.read_text()
    copy = peer.parent / "peer-copy"
    shutil.copytree(peer, copy, symlinks=True, ignore=shutil.ignore_patterns(".venv"))
    configure(demo, f"--peer-path={copy}")

    proc = subprocess.run(
        [shell, "-c", 'set -eu; . ./setup_peer_env.sh; echo "${PEER_VENV-<unset>}"; echo "$PEER_ENTRY"'],
        cwd=demo,
        env=outside_venvs(),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert (proc.returncode, proc.stderr) == (0, "")
    assert proc.stdout.splitlines() == ["<unset>", str(copy / "src" / "echo_peer.py")]


def test_a_relative_entry_set_in_paths_py_is_relative_to_the_dependency(caller_and_peer):
    # ./configure --peer-entry=src/echo_peer.py keeps the path as given; the setup
    # script joins it to the dependency's folder, and dependency_command must too.
    demo, peer, venv, _lib = caller_and_peer
    configure(demo, "--peer-entry=src/echo_peer.py")

    out = call(demo)

    assert out["argv"][1] == str(peer / "src" / "echo_peer.py")
    assert out["child"]["args"] == ["predict", "-j", "job.json"]


# A helper or a test that imports only core.app_env, and not core.set_pythonpath,
# called dependency_command before this tool's .env was loaded: it said "peer
# publishes no entry script ..., set peer_entry in paths.py" while paths.py set it.

ONLY_APP_ENV = """\
import json, sys
from core.app_env import dependency_command
try:
    argv, env = dependency_command("peer", "predict")
except RuntimeError as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(0)
print(json.dumps({"argv": argv, "virtual_env": env.get("VIRTUAL_ENV")}))
"""


def test_dependency_command_loads_the_env_when_nothing_did_yet(caller_and_peer):
    demo, peer, venv, _lib = caller_and_peer
    entry = peer / "src" / "echo_peer.py"
    configure(demo, f"--peer-entry={entry}")  # set in paths.py: only this tool's .env has it
    (demo / "src" / "only_app_env.py").write_text(ONLY_APP_ENV)

    proc = subprocess.run(
        [sys.executable, str(demo / "src" / "only_app_env.py")], env=outside_venvs(), capture_output=True, text=True
    )

    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out == {"argv": [str(venv / "bin" / "python"), str(entry), "predict"], "virtual_env": str(venv)}


# A tool kept the dependency values it loaded at start-up. The deploy orchestrator
# redeploys a version by removing its folder and extracting the new tarball in its
# place: with a renamed entry script, the next call failed with "can't open file
# .../src/echo_peer.py". dependency_command reads them from the dependency's .env
# again at each call.

REDEPLOYED_WHILE_RUNNING = """\
import json, os, subprocess, sys
import core.set_pythonpath
from core.app_env import dependency_command
peer, how, new_venv = sys.argv[1:4]
loaded = {key: os.environ.get(key) for key in ("PEER_ENTRY", "PEER_VENV")}
env_file = os.path.join(peer, ".env")
lines = open(env_file).read().splitlines(True)
if how == "renamed":  # what ./configure writes in the new version
    os.rename(os.path.join(peer, "src", "echo_peer.py"), os.path.join(peer, "src", "third.py"))
    lines = [
        "APP_ENTRY=src/third.py\\n" if line.startswith("APP_ENTRY=")
        else f"APP_VENV={new_venv}\\n" if line.startswith("APP_VENV=")
        else line
        for line in lines
    ]
    open(env_file, "w").write("".join(lines))
elif how == "no-env":  # removed, and not extracted yet
    os.rename(env_file, env_file + ".away")
elif how == "no-entry":  # the new version publishes none
    open(env_file, "w").write("".join(line for line in lines if not line.startswith("APP_ENTRY=")))
elif how == "no-folder":  # the whole version folder removed, the new one not extracted yet
    os.rename(peer, peer + ".away")
elif how == "no-python":  # the folder is back, its .venv not made again yet
    os.rename(os.path.join(peer, ".venv", "bin", "python"), os.path.join(peer, ".venv", "bin", "python.away"))
try:
    argv, env = dependency_command("peer", "predict")
except RuntimeError as e:
    print(json.dumps({"loaded": loaded, "error": str(e)}))
    sys.exit(0)
child = subprocess.run(argv, env=env, capture_output=True, text=True)
print(json.dumps({"loaded": loaded, "argv": argv, "rc": child.returncode, "err": child.stderr.strip()}))
"""


def redeploy_while_running(demo, peer, how, new_venv=""):
    (demo / "src" / "redeployed.py").write_text(REDEPLOYED_WHILE_RUNNING)
    proc = subprocess.run(
        [sys.executable, str(demo / "src" / "redeployed.py"), str(peer), how, str(new_venv)],
        env=outside_venvs(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_dependency_command_follows_a_dependency_redeployed_while_the_tool_runs(caller_and_peer, tmp_path):
    demo, peer, venv, _lib = caller_and_peer
    new_venv = give_venv(tmp_path / "peer-2")

    out = redeploy_while_running(demo, peer, "renamed", new_venv)

    assert out["loaded"] == {"PEER_ENTRY": str(peer / "src" / "echo_peer.py"), "PEER_VENV": str(venv)}
    assert out["argv"] == [str(new_venv / "bin" / "python"), str(peer / "src" / "third.py"), "predict"]
    assert (out["rc"], out["err"]) == (0, "")
    # With the dependency's .env gone at call time (extracted again, its install.sh
    # not done yet), the values loaded at start-up do not stand in: it is being installed.
    (peer / "src" / "third.py").rename(peer / "src" / "echo_peer.py")
    configure(peer)
    out = redeploy_while_running(demo, peer, "no-env")
    assert out["error"] == (
        f"peer is being installed at {os.path.realpath(peer)} right now (a redeploy in progress?); try again"
    )
    # A same-version redeploy may remove ./configure first: a folder whose .env
    # publishes the entry and that still has src/ is being installed too. (A
    # dependency whose entry paths.py sets is the control: test_clone_install_args_and_venv.py.)
    (peer / ".env.away").rename(peer / ".env")
    (peer / "configure").rename(peer / "configure.away")
    out = redeploy_while_running(demo, peer, "no-env")
    assert out["error"] == (
        f"peer is being installed at {os.path.realpath(peer)} right now (a redeploy in progress?); try again"
    )


def test_a_dependency_that_publishes_no_entry_at_call_time_says_so(caller_and_peer):
    demo, peer, _venv, _lib = caller_and_peer

    out = redeploy_while_running(demo, peer, "no-entry")

    assert out["error"] == (
        f"peer publishes no entry script (APP_ENTRY in {peer / '.env'}, or [tool.ngargparser] entry): "
        "set peer_entry in paths.py, then run ./configure"
    )


# The orchestrator removes the whole version folder before it extracts the new
# tarball. A call in that window got the values loaded at start-up, which name
# the removed folder: subprocess raised FileNotFoundError for .venv/bin/python, a
# traceback. dependency_command now says so in one line.


def test_a_dependency_folder_removed_while_the_tool_runs_raises_a_clean_error(caller_and_peer):
    demo, peer, venv, _lib = caller_and_peer

    out = redeploy_while_running(demo, peer, "no-folder")

    assert out["loaded"]["PEER_VENV"] == str(venv)  # what start-up saw, now gone
    assert out["error"] == (
        f"peer is not deployed at {peer} right now (a redeploy in progress?); try again. "
        "If peer moved, run ./configure --peer-path=<new folder>"
    )
    # The folder is back, but its virtualenv's python is not there yet.
    Path(f"{peer}.away").rename(peer)
    out = redeploy_while_running(demo, peer, "no-python")
    assert out["error"] == (
        f"peer is being installed at {os.path.realpath(peer)} right now (a redeploy in progress?); try again"
    )
    # Control: with the folder and its python in place, the call runs.
    (venv / "bin" / "python.away").rename(venv / "bin" / "python")
    out = redeploy_while_running(demo, peer, "unchanged")
    assert (out["argv"][:2], out["rc"]) == ([str(venv / "bin" / "python"), str(peer / "src" / "echo_peer.py")], 0)


# Each error dependency_command raises is a DependencyError, so a tool's start-up
# script can print it as one line; it is still a RuntimeError for older callers.

ERROR_KIND_PROBE = """\
import json, sys
import core.set_pythonpath
from core.app_env import DependencyError, dependency_command
try:
    dependency_command(sys.argv[1], "predict")
except RuntimeError as e:
    print(json.dumps({"type": type(e).__name__, "is_dependency_error": isinstance(e, DependencyError), "error": str(e)}))
else:
    print(json.dumps({"type": None}))
"""


def test_every_dependency_command_error_is_a_dependency_error_and_a_runtime_error(caller_and_peer):
    demo, peer, venv, _lib = caller_and_peer
    (demo / "src" / "error_kind.py").write_text(ERROR_KIND_PROBE)

    def raised(name="peer"):
        proc = subprocess.run(
            [sys.executable, str(demo / "src" / "error_kind.py"), name],
            env=outside_venvs(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    assert raised() == {"type": None}  # control: peer runs
    seen = {"not a dependency": raised("peerr")}
    (venv / "bin" / "python").rename(venv / "bin" / "python.away")
    seen["not there"] = raised()
    (venv / "bin" / "python.away").rename(venv / "bin" / "python")
    publish_entry(peer, "bin/peer.sh", SHELL_PEER, mode=0o644)
    seen["not executable"] = raised()
    env_file = peer / ".env"
    env_file.write_text("".join(line for line in env_file.read_text().splitlines(True) if "APP_ENTRY=" not in line))
    seen["no entry"] = raised()

    for case, out in seen.items():
        assert (out["type"], out["is_dependency_error"]) == ("DependencyError", True), (case, out)
    assert "is not a dependency" in seen["not a dependency"]["error"]
    assert "is being installed at" in seen["not there"]["error"]
    assert "not a Python script and not executable" in seen["not executable"]["error"]
    assert "publishes no entry script" in seen["no entry"]["error"]


# A dependency .env that is not UTF-8 text was read three ways: ./configure did
# not use it, the setup script did, and dependency_command said "publishes no
# entry script (APP_ENTRY in ...)" about a .env that sets APP_ENTRY. It now
# counts as missing everywhere, and the message names the encoding.


@pytest.mark.skipif(WHICH("iconv") is None, reason="the setup script tells with iconv")
def test_a_dependency_env_that_is_not_utf8_is_not_used_and_named(caller_and_peer):
    demo, peer, venv, _lib = caller_and_peer
    env_file = peer / ".env"
    text = env_file.read_bytes().replace(b"APP_NAME=peer", b"APP_NAME=p\xe9er")
    env_file.write_bytes(text.replace(b"APP_ENTRY=src/echo_peer.py", b"APP_ENTRY=src/other.py"))
    entry = str(peer / "src" / "echo_peer.py")

    # Configured while it was UTF-8: what ./configure saw then is the fallback, as for a missing .env.
    out = probe_entry(demo)
    assert out["argv"][:2] == [str(venv / "bin" / "python"), entry]
    for shell in [shell for shell in ("sh", "bash", "dash") if WHICH(shell)]:
        proc = subprocess.run(
            [shell, "-c", 'set -eu; . ./setup_peer_env.sh; echo "$PEER_ENTRY"'],
            cwd=demo,
            env=outside_venvs(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert (proc.returncode, proc.stdout.splitlines()) == (0, [entry]), shell
        assert proc.stderr.splitlines() == [
            f"setup_peer_env.sh: {env_file} is not UTF-8 text, so its values are not used: "
            f"save it as UTF-8, or run ./configure in {peer} to write it again"
        ], shell

    configure(demo)  # sees no entry now
    assert probe_entry(demo) == {
        "error": f"peer publishes no entry script: {env_file} is not UTF-8 text, so its values are not used. "
        f"Save it as UTF-8, or run ./configure in {peer} to write it again; "
        "or set peer_entry in paths.py, then run ./configure"
    }


# A dependency .env this user cannot read set nothing for app_env.py, so even the
# values ./configure saw were dropped: "publishes no entry script (APP_ENTRY in
# ...)". It counts as missing now, as in ./configure and the setup script.


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="root ignores file permissions")
def test_a_dependency_env_that_cannot_be_read_counts_as_missing(caller_and_peer):
    demo, peer, venv, _lib = caller_and_peer
    env_file = peer / ".env"
    env_file.chmod(0)
    try:
        # Configured while it could be read: what ./configure saw then is the fallback, as for a missing .env.
        assert probe_entry(demo)["argv"][:2] == [str(venv / "bin" / "python"), str(peer / "src" / "echo_peer.py")]
        configure(demo)  # sees no entry now
        out = probe_entry(demo)
    finally:
        env_file.chmod(0o644)

    assert out == {
        "error": f"peer publishes no entry script: {env_file} cannot be read (Permission denied), "
        "so its values are not used. Ask its owner to make it readable; "
        "or set peer_entry in paths.py, then run ./configure"
    }


# A tool that runs under `uv run` has its virtualenv on PATH. It starts a tool with
# a virtualenv (VIRTUAL_ENV and its bin/ come first), which starts one without a
# virtualenv: only the middle tool's virtualenv was left out of PATH, so the last
# one ran with the first tool's python3.


def test_a_grandchild_without_a_venv_does_not_run_in_the_top_tools_venv(caller_and_peer, tmp_path):
    demo, peer, venv, _lib = caller_and_peer
    venv.rename(peer / "no-venv")
    configure(peer)  # publishes no APP_VENV now
    top, middle = tmp_path / "top-venv", tmp_path / "middle-venv"
    for folder in (top, middle):
        (folder / "bin").mkdir(parents=True)
        (folder / "pyvenv.cfg").write_text("home = /usr/bin\n")
        python3 = folder / "bin" / "python3"
        if python3.is_symlink():  # never write through a symlink into a real interpreter
            python3.unlink()
        python3.write_text(f'#!/bin/sh\nCALLER_PYTHON_RAN=1 exec "{sys.executable}" "$@"\n')
        python3.chmod(0o755)
    outside = outside_venvs()["PATH"]
    path = os.pathsep.join([str(middle / "bin"), str(top / "bin"), outside])

    proc = run_caller(demo, VIRTUAL_ENV=str(middle), PATH=path)
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    assert out["env"]["PATH"] == outside
    assert out["env"]["VIRTUAL_ENV"] is None
    python3 = WHICH("python3", path=outside)
    if python3 is None:
        pytest.skip("no python3 on PATH outside the virtualenvs")
    assert out["argv"][0] == python3
    assert out["child"]["caller_python"] is None
    # Control: a folder named bin/ without a pyvenv.cfg next to it stays on PATH.
    (top / "pyvenv.cfg").unlink()
    out = json.loads(run_caller(demo, VIRTUAL_ENV=str(middle), PATH=path).stdout.strip().splitlines()[-1])
    assert out["env"]["PATH"] == os.pathsep.join([str(top / "bin"), outside])


# LOADEDMODULES lists each loaded module as <name>/<version>. A module counts as
# loaded when it is listed exactly, or with a version below it: fake/1.0.2 is
# another version, not fake/1.0.


def test_a_module_counts_as_loaded_only_when_listed_or_below(caller_and_peer):
    demo, _peer, _venv, _lib = caller_and_peer
    configure(demo, "--peer-module=fake/1.0")
    warning = [
        "peer needs the environment module fake/1.0, which dependency_command cannot load: "
        "load it before starting this tool, or call the dependency through setup_peer_env.sh"
    ]

    for loaded in ("fake/1.0.2", "fake/1.01", "fake", "other/fake/1.0", "gcc/9"):
        assert run_caller(demo, LOADEDMODULES=loaded).stderr.splitlines() == warning, loaded
    for loaded in ("fake/1.0", "gcc/9:fake/1.0", "fake/1.0/cuda"):
        assert run_caller(demo, LOADEDMODULES=loaded).stderr == "", loaded
