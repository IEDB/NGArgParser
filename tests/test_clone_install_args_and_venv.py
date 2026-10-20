"""A fresh clone, a half-removed deployed copy, number arguments, and a tool run from another virtualenv.

1. A dependency that is a fresh git clone (no .env yet) was reported as being
   installed, "try again": trying again never helps. It is "not configured
   yet: run ./configure there".
2. A same-version redeploy can remove .env, ./configure and .ngargparser-build
   before .venv/ and src/. A caller was then told to run ./configure in the
   deploy folder, or ran the values saved before. Any file of a tool folder
   left now says it is being installed, for a dependency and for the tool.
3. dependency_command("leaf", "-n", 5), or a None argument, failed later
   inside subprocess.
4. Run as ${X_PYTHON:+"$X_PYTHON"} "${X_ENTRY:?}" from another virtualenv, a
   tool kept that VIRTUAL_ENV and PATH, so `python` it started by name was
   the caller's.
"""

import json
import os
import subprocess
import sys
from argparse import Namespace

import pytest
from test_app_env_2 import real_venv
from test_configure_env import run_configure
from test_redeploy_and_python_version import set_requires_python
from test_redeploy_messages import clean_env, deploy, from_python, not_configured

from ngargparser import cli

pytestmark = pytest.mark.skipif(os.name != "posix", reason="needs symlinks and #! lines")


def configured_caller(project, tmp_path):
    """callee 1.0 deployed behind apps/callee/current, and this tool configured for it."""
    apps = tmp_path / "apps"
    current = apps / "callee" / "current"
    version = deploy(apps, "1.0")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(project, f"--callee-path={current}")
    return current, version


def put_away(path, tmp_path):
    """Move `path` out of its folder, as `rm -rf` removing it would."""
    away = tmp_path / "away"
    away.mkdir(exist_ok=True)
    os.rename(path, away / f"{path.parent.name}-{path.name}")


# 1. A fresh clone is not configured -----------------------------------------


def test_a_dependency_that_is_a_fresh_clone_is_not_configured_yet(scaffolded_project, tmp_path):
    current, version = configured_caller(scaffolded_project, tmp_path)
    put_away(version / ".env", tmp_path)
    assert from_python(scaffolded_project) == {"error": not_configured(current)}  # control: no .git

    (version / ".git").mkdir()

    assert from_python(scaffolded_project) == {
        "error": f"callee at {version} is not configured yet: run ./configure there"
    }


def test_a_clone_the_caller_was_configured_before_is_not_configured_yet(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    current = apps / "callee" / "current"
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={current}")  # no saved values
    version = deploy(apps, "1.0")
    put_away(version / ".env", tmp_path)
    (version / ".git").write_text("gitdir: /elsewhere/.git/worktrees/callee\n")  # a worktree's .git is a file

    assert from_python(scaffolded_project) == {
        "error": f"callee at {version} is not configured yet: run ./configure there"
    }


# 2. Any file of a tool folder left counts -----------------------------------


@pytest.mark.parametrize("left", ["deploy/install.sh", ".ngargparser", ".venv", "src"])
def test_a_dependency_half_removed_by_a_redeploy_is_being_installed(scaffolded_project, tmp_path, left):
    current, version = configured_caller(scaffolded_project, tmp_path)
    (version / "deploy").mkdir()
    (version / "deploy" / "install.sh").write_text("#!/bin/sh\n")
    (version / ".ngargparser").mkdir()
    entry = str(version / "src" / "run_callee.py")
    assert from_python(scaffolded_project)["argv"][1:] == [entry, "predict"]  # control

    for name in (".env", "configure", "deploy", ".ngargparser", ".venv", "src"):
        if name != left.split("/")[0]:
            put_away(version / name, tmp_path)

    assert from_python(scaffolded_project) == {"error": not_configured(current)}


def test_a_dependency_whose_entry_paths_py_sets_is_no_tool_without_configure(scaffolded_project, tmp_path):
    # Control: src/ and .venv/ say nothing about a dependency whose entry is not
    # read from its .env: such a folder may never have one.
    current, version = configured_caller(scaffolded_project, tmp_path)
    entry = str(version / "src" / "run_callee.py")
    with open("paths.py", "a") as f:
        f.write(f"callee_entry = {entry!r}\n")
    run_configure(scaffolded_project)
    assert "CALLEE_ENTRY" not in (scaffolded_project / ".env").read_text().split("APP_KEYS_FROM_DEPS=")[-1]
    put_away(version / ".env", tmp_path)
    put_away(version / "configure", tmp_path)

    assert from_python(scaffolded_project)["argv"][1:] == [entry, "predict"]


@pytest.mark.parametrize("left", ["configure", "deploy/install.sh", ".ngargparser", ".venv"])
def test_the_tool_half_removed_by_a_redeploy_says_it_is_being_installed(in_tmp_dir, left):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    demo = in_tmp_dir / "demo"
    for name in (".env", "configure", "deploy", ".ngargparser"):
        if name != left.split("/")[0]:
            put_away(demo / name, in_tmp_dir)
    if left == ".venv":
        (demo / ".venv").mkdir()

    proc = subprocess.run(
        [sys.executable, str(demo / "src" / "run_demo.py"), "--help"],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert (proc.returncode, proc.stderr.splitlines()[:1]) == (
        1,
        ["This tool is being installed right now (a redeploy in progress?): '.env' is missing; try again."],
    )


# 3. Number arguments, and the ones argv cannot hold ------------------------

ARGS_PROBE = """\
import json, sys
from pathlib import Path
import core.set_pythonpath
from core.app_env import dependency_command
seen = []
for args in (("-n", 5, 2.5, Path("in.json"), b"raw"), ("-n", None), ("--verbose", True), ("-x", object())):
    try:
        argv, _env = dependency_command("callee", *args)
    except TypeError as e:
        seen.append({"type_error": str(e).replace(hex(id(args[-1])), "0x")})
    else:
        seen.append({"args": [str(arg) if not isinstance(arg, str) else arg for arg in argv[2:]],
                     "types": [type(arg).__name__ for arg in argv[2:]]})
print(json.dumps(seen))
"""


def test_numbers_become_text_and_other_values_raise_a_type_error_naming_them(scaffolded_project, tmp_path):
    configured_caller(scaffolded_project, tmp_path)
    (scaffolded_project / "src" / "probe_args.py").write_text(ARGS_PROBE)
    proc = subprocess.run(
        [sys.executable, str(scaffolded_project / "src" / "probe_args.py")],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    seen = json.loads(proc.stdout.strip().splitlines()[-1])

    assert seen[0] == {
        "args": ["-n", "5", "2.5", "in.json", "b'raw'"],
        "types": ["str", "str", "str", "PosixPath", "bytes"],
    }
    assert seen[1:] == [
        {
            "type_error": "dependency_command('callee', ...) got None (NoneType) as argument 2; "
            "each argument must be text (str or bytes), a path, or a number"
        },
        {
            "type_error": "dependency_command('callee', ...) got True (bool) as argument 2; "
            "each argument must be text (str or bytes), a path, or a number"
        },
        {
            "type_error": "dependency_command('callee', ...) got <object object at 0x> (object) as argument 2; "
            "each argument must be text (str or bytes), a path, or a number"
        },
    ]


# 4. Its own Python from another virtualenv ---------------------------------

VENV_PROBE = """\
import json, os, subprocess
import core.set_pythonpath
child = subprocess.run(["python", "-c", "import sys; print(sys.prefix)"], capture_output=True, text=True)
print(json.dumps({"virtual_env": os.environ.get("VIRTUAL_ENV"),
                  "path": os.environ["PATH"].split(os.pathsep),
                  "child_prefix": child.stdout.strip()}))
"""


def test_a_tool_run_with_its_own_python_from_another_venv_runs_its_children_in_its_own(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    demo = in_tmp_dir / "demo"
    set_requires_python(demo, f">={sys.version_info[0]}.{sys.version_info[1]}")
    own_python = real_venv(demo / ".venv")
    other = in_tmp_dir / "other" / ".venv"
    real_venv(other)
    (demo / "src" / "probe_venv.py").write_text(VENV_PROBE)
    base = clean_env()["PATH"]

    def probe(python, **env):
        proc = subprocess.run(
            [str(python), str(demo / "src" / "probe_venv.py")],
            env=clean_env(**env),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stderr, json.loads(proc.stdout.strip().splitlines()[-1])

    # As "$DEMO_PYTHON" "$DEMO_ENTRY" from a shell with the other virtualenv active.
    stderr, seen = probe(own_python, VIRTUAL_ENV=str(other), PATH=f"{other / 'bin'}{os.pathsep}{base}")

    assert stderr == ""
    assert seen["virtual_env"] == str(demo / ".venv")
    assert seen["path"][:2] == [str(demo / ".venv" / "bin"), str(other / "bin")]
    assert seen["child_prefix"] == str(demo / ".venv")

    # Control: already active (uv run, or bin/activate), nothing changes.
    own_bin = f"{demo / '.venv' / 'bin'}{os.pathsep}{base}"
    stderr, seen = probe(own_python, VIRTUAL_ENV=str(demo / ".venv"), PATH=own_bin)
    assert (stderr, seen["virtual_env"], seen["path"]) == ("", str(demo / ".venv"), own_bin.split(os.pathsep))
    # Control: another Python (not its own virtualenv's) is left as it is.
    stderr, seen = probe(sys.executable, VIRTUAL_ENV=str(other), PATH=f"{other / 'bin'}{os.pathsep}{base}")
    assert seen["virtual_env"] == str(other)
    assert seen["path"][0] == str(other / "bin")
