"""Two tools deployed with the real uv, and a third program with no virtualenv.

test_deploy_two_tools.py deploys a caller and a callee with a stub uv, whose
.venv/bin/python runs the test interpreter. Here both are locked, built and
installed with the real `uv sync --frozen --no-dev`, offline from the uv cache,
so each has a virtualenv of its own. The caller runs under `uv run`, as a
developer runs a tool, and calls the callee from Python (dependency_command)
and from a shell (the setup script). The callee calls a grandchild, a script
with no virtualenv, through dependency_command. Each prints the Python it runs
with. Opt-in, like test_install_real_uv.py: set NGARGPARSER_REAL_UV=1
(`pytest -k real_uv`).
"""

import json
import os
import shutil
import subprocess
import sys
import tarfile

import pytest
from test_deploy_two_tools import build, flip, generate, set_predict

from ngargparser import cli

# Found before the in_tmp_dir fixture hides every program from shutil.which.
WHICH = shutil.which
UV = WHICH("uv")
HAS_BASH_AND_GIT = shutil.which("bash") is not None and shutil.which("git") is not None

pytestmark = pytest.mark.skipif(
    os.environ.get("NGARGPARSER_REAL_UV") != "1",
    reason="opt-in: set NGARGPARSER_REAL_UV=1 to deploy with the real uv",
)

# Prints the Python it runs with, and that of the grandchild it calls.
CALLEE_PREDICT = """\
        import json
        import subprocess
        from core.app_env import dependency_command
        argv, env = dependency_command("gc")
        child = subprocess.run(argv, env=env, capture_output=True, text=True)
        print(json.dumps({
            "executable": sys.executable,
            "prefix": sys.prefix,
            "gc": json.loads(child.stdout) if child.returncode == 0 else child.stderr,
        }))
        sys.exit(0)
"""

CALLER_PREDICT = """\
        import json
        import subprocess
        from core.app_env import dependency_command
        argv, env = dependency_command("callee", "predict", "-j", str(args.input_json))
        child = subprocess.run(argv, env=env, capture_output=True, text=True)
        print(json.dumps({
            "prefix": sys.prefix,
            "callee": json.loads(child.stdout.strip().splitlines()[-1]) if child.returncode == 0 else child.stderr,
            "callee_stderr": child.stderr,
        }))
        sys.exit(0)
"""

GRANDCHILD = """\
import json, sys
print(json.dumps({"executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix}))
"""


def outer_env():
    """This environment without a virtualenv or a tool's values, with uv offline and on PATH."""
    path = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not os.path.isfile(os.path.join(os.path.dirname(os.path.realpath(entry)), "pyvenv.cfg"))
    ]
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("APP_", "CALLEE_", "CALLER_", "GC_"))
        and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "CI", "PYTHONPATH", "PYTHONHOME")
    }
    env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never", PATH=os.pathsep.join([os.path.dirname(UV), *path]))
    return env


def configure(project, *args):
    proc = subprocess.run(
        [sys.executable, "src/core/configure.py", *args], cwd=project, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def lock_and_build(project, run_build, env):
    """uv lock and uv sync --frozen --no-dev, offline, then the real build. Skips where the uv cache lacks a package."""
    for step in (["lock"], ["sync", "--frozen", "--no-dev"]):
        proc = subprocess.run([UV, *step], cwd=project, env=env, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            pytest.skip(f"uv {step[0]} fails offline here: {proc.stderr.strip().splitlines()[-1:]}")
    return build(project, run_build)


def deploy_with_real_uv(tarball, version_dir, tmp_path, env):
    """Extract `tarball` into `version_dir` and run its deploy/install.sh there with the real uv."""
    unpacked = tmp_path / f"unpacked-{version_dir.parent.name}"
    with tarfile.open(tarball) as tf:
        tf.extractall(unpacked, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    (top,) = unpacked.iterdir()
    version_dir.parent.mkdir(parents=True, exist_ok=True)
    top.rename(version_dir)
    name, version = version_dir.parent.name, version_dir.name
    install = subprocess.run(
        ["bash", "deploy/install.sh"],
        cwd=version_dir,
        env={
            **env,
            "IEDB_STANDALONE_NAME": name,
            "IEDB_VERSION": version,
            "IEDB_VERSION_DIR": str(version_dir),
            "IEDB_STANDALONE_DIR": str(version_dir.parent.parent),
            "IEDB_PREVIOUS_VERSION_DIR": "",
        },
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    assert (version_dir / ".venv" / "pyvenv.cfg").is_file()
    return version_dir


def same(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def test_real_uv_tools_each_run_in_their_own_venv_and_a_grandchild_in_none(
    in_tmp_dir, monkeypatch, run_build, tmp_path
):
    if UV is None or not HAS_BASH_AND_GIT:
        pytest.skip("needs uv, bash and git")
    env = outer_env()
    if WHICH("python3", path=env["PATH"]) is None:
        pytest.skip("no python3 on PATH outside the virtualenvs")
    apps = tmp_path / "apps"
    grandchild = tmp_path / "gc"
    grandchild.mkdir()
    (grandchild / "run_gc.py").write_text(GRANDCHILD)

    # The build's lock check runs uv too: offline, like the rest.
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    sources = in_tmp_dir / "sources"
    callee = generate(sources, "callee", monkeypatch)
    set_predict(callee / "src" / "run_callee.py", CALLEE_PREDICT)
    monkeypatch.chdir(callee)
    cli.add_deps_to_paths("paths.py", ["gc"])
    configure(callee, f"--gc-path={grandchild}", f"--gc-entry={grandchild / 'run_gc.py'}")
    caller = generate(sources, "caller", monkeypatch)
    set_predict(caller / "src" / "run_caller.py", CALLER_PREDICT)
    monkeypatch.chdir(caller)
    cli.add_deps_to_paths("paths.py", ["callee"])
    configure(caller, f"--callee-path={apps / 'callee' / 'current'}")

    callee_dir = deploy_with_real_uv(lock_and_build(callee, run_build, env), apps / "callee" / "1.0.0", tmp_path, env)
    flip(apps, "callee", "1.0.0")
    caller_dir = deploy_with_real_uv(lock_and_build(caller, run_build, env), apps / "caller" / "1.0.0", tmp_path, env)
    caller_current = flip(apps, "caller", "1.0.0")
    callee_venv, caller_venv = callee_dir / ".venv", caller_dir / ".venv"
    job = tmp_path / "job.json"
    job.write_text("{}\n")

    def under_uv_run(*command):
        """Run `command` in the deployed caller under `uv run`, which puts the caller's virtualenv on PATH."""
        proc = subprocess.run(
            [UV, "run", "--no-sync", *command],
            cwd=caller_current,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        # Each tool runs with its own virtualenv, so none warns that it does not.
        assert "not with its own virtualenv" not in proc.stderr, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    from_python = under_uv_run("python", "src/run_caller.py", "predict", "-j", str(job))
    one_call = '. ./setup_callee_env.sh; ${CALLEE_PYTHON:+"$CALLEE_PYTHON"} "$CALLEE_ENTRY" predict -j "$1"'
    from_a_shell = {"callee": under_uv_run("sh", "-c", one_call, "sh", str(job))}

    assert same(from_python["prefix"], caller_venv)  # the caller ran under uv run, in its own virtualenv
    for route, out in (("dependency_command", from_python), ("setup script", from_a_shell)):
        callee_out = out["callee"]
        assert isinstance(callee_out, dict), (route, callee_out)
        # The callee runs with its own virtualenv's python, not the caller's.
        assert same(callee_out["executable"], callee_venv / "bin" / "python"), route
        assert same(callee_out["prefix"], callee_venv), route
        # The grandchild has no virtualenv: it runs in none, not in the caller's.
        gc = callee_out["gc"]
        assert isinstance(gc, dict), (route, gc)
        assert not same(gc["prefix"], caller_venv) and not same(gc["prefix"], callee_venv), (route, gc)
        assert gc["prefix"] == gc["base_prefix"], (route, gc)
    assert "not with its own virtualenv" not in from_python["callee_stderr"], from_python["callee_stderr"]

    # The callee started with the caller's Python instead, as [sys.executable, entry]
    # in the caller does: one warning names the callee's own.
    wrong = subprocess.run(
        [str(caller_venv / "bin" / "python"), "src/run_callee.py", "--help"],
        cwd=callee_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert wrong.returncode == 0, wrong.stderr
    assert wrong.stderr.splitlines() == [
        f"Warning: callee is running with {caller_venv / 'bin' / 'python'}, not with its own virtualenv, "
        f"so it gets that Python's packages: run it with {callee_venv / 'bin' / 'python'}"
    ]
