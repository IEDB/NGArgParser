"""A tool started by another tool must use its own .env for APP_ROOT and APP_NAME.

phbr starts tc1 as a child process, and the child inherits phbr's APP_ROOT and
APP_NAME. Before core/app_env.py, every core module loaded .env without
overriding them, so tc1 imported phbr's libs/. These tests scaffold a caller
('demo') and a callee ('peer') and run a probe inside the callee with the
caller's values in its environment.
"""

import json
import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PROBE = """\
import json, os, sys
import validators  # a tool's first import; it loads core.core_validators
import core.core_validators as cv
import core.NGArgumentParser as ng
import core.set_pythonpath as sp
print(json.dumps({
    "set_pythonpath": str(sp.APP_ROOT),
    "core_validators": cv.APP_ROOT,
    "app_name": cv.APP_NAME,
    "ngargparser": ng.APP_ROOT,
    "libs": sorted(p for p in sys.path if p.endswith(os.sep + "libs")),
    "foo": os.environ.get("FOO"),
}))
"""


@pytest.fixture
def caller_and_callee(in_tmp_dir):
    for name in ("demo", "peer"):
        assert not cli.startapp_command(Namespace(project_name=name))
    peer = in_tmp_dir / "peer"
    (peer / "src" / "probe_env.py").write_text(PROBE)
    return in_tmp_dir / "demo", peer


def run_probe(peer, **env):
    base = {k: v for k, v in os.environ.items() if k not in ("APP_ROOT", "APP_NAME", "FOO")}
    return subprocess.run(
        [sys.executable, str(peer / "src" / "probe_env.py")],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def seen(proc):
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_callee_uses_its_own_app_root_not_the_callers(caller_and_callee):
    demo, peer = caller_and_callee

    out = seen(run_probe(peer, APP_ROOT=str(demo), APP_NAME="demo"))

    assert out["set_pythonpath"] == out["core_validators"] == out["ngargparser"] == str(peer)
    assert out["app_name"] == "peer"
    assert out["libs"] == [str(peer / "libs")]


def test_control_nothing_inherited(caller_and_callee):
    _demo, peer = caller_and_callee

    out = seen(run_probe(peer))

    assert out["set_pythonpath"] == str(peer)
    assert out["app_name"] == "peer"


def test_unconfigured_callee_stops_instead_of_using_the_callers_libs(caller_and_callee):
    demo, peer = caller_and_callee
    (peer / ".env").unlink()

    proc = run_probe(peer, APP_ROOT=str(demo), APP_NAME="demo")

    assert proc.returncode != 0
    assert "This project has not been configured yet" in proc.stderr


def test_without_env_an_app_root_pointing_at_itself_is_kept(caller_and_callee):
    # The build's startup check runs the build copy (no .env) with APP_ROOT set to it.
    _demo, peer = caller_and_callee
    (peer / ".env").unlink()

    out = seen(run_probe(peer, APP_ROOT=str(peer), APP_NAME="peer"))

    assert out["set_pythonpath"] == str(peer)
    assert out["libs"] == [str(peer / "libs")]


def test_other_keys_keep_the_inherited_value(caller_and_callee):
    _demo, peer = caller_and_callee
    env_file = peer / ".env"
    env_file.write_text(env_file.read_text() + "FOO=from_env_file\n")

    assert seen(run_probe(peer, FOO="inherited"))["foo"] == "inherited"
    assert seen(run_probe(peer))["foo"] == "from_env_file"
