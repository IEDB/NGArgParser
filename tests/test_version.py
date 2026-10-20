"""`cli --version` with stdout closed (`cli --version >&-`): no traceback, exit 0."""

import os
import subprocess
import sys

import pytest

from ngargparser import cli

PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))
VERSION = "import sys\nfrom ngargparser.cli import main\nsys.argv = ['cli', '--version']\nsys.exit(main())\n"


def run_version(redirect, tmp_path):
    env = {**os.environ, "PYTHONPATH": PACKAGE_ROOT, "NGARGPARSER_NO_UPDATE_CHECK": "1"}
    return subprocess.run(
        ["sh", "-c", f'exec "$@" {redirect}', "sh", sys.executable, "-c", VERSION],
        cwd=tmp_path,
        env=env,
        stdout=None if redirect else subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )


@pytest.mark.skipif(os.name != "posix", reason="closes stdout with the shell")
def test_version_with_stdout_closed_is_quiet(tmp_path):
    run = run_version(">&-", tmp_path)

    assert run.returncode == 0, run.stderr
    assert run.stderr == ""


@pytest.mark.skipif(os.name != "posix", reason="runs through the shell")
def test_version_with_stdout_open_prints_the_version(tmp_path):
    # Control: the same command with stdout open prints the version line, as before.
    run = run_version("", tmp_path)

    assert run.returncode == 0, run.stderr
    assert run.stdout == f"cli {cli.__version__}\n"
