"""`uv run pytest` in a generated tool, as the README's "Test your parser" runs it.

These run pytest in a scaffolded tool, with the test interpreter, on a
tests/test_parser.py that imports the tool's parser.
"""

import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

TEST_PARSER = """\
from DemoArgumentParser import DemoArgumentParser


def test_predict_parses(tmp_path):
    job = tmp_path / "in.json"
    job.write_text("{}")
    args = DemoArgumentParser().parse_args(["predict", "-j", str(job)])
    assert args.input_json == job
"""


@pytest.fixture
def demo(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    project = in_tmp_dir / "demo"
    (project / "tests").mkdir(exist_ok=True)
    (project / "tests" / "test_parser.py").write_text(TEST_PARSER)
    return project


def run_pytest(project):
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ROOT", "APP_NAME", "PYTEST_ADDOPTS")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# A fresh clone has no .env. set_pythonpath stopped with sys.exit while pytest
# collected the tests, and pytest printed it as an INTERNALERROR traceback of
# more than 100 lines, the useful message at its very end.


def test_pytest_in_a_tool_without_env_stops_with_just_the_message(demo):
    (demo / "tests" / "test_other.py").write_text(TEST_PARSER.replace("test_predict_parses", "test_again"))
    (demo / ".env").unlink()
    # A newer tool's tests/conftest.py runs ./configure first; this is a tool without one.
    (demo / "tests" / "conftest.py").unlink(missing_ok=True)

    proc = run_pytest(demo)
    output = proc.stdout + proc.stderr

    assert proc.returncode == 1, output
    assert "INTERNALERROR" not in output
    assert "Traceback" not in output
    # Without .git it may be a deployed copy a redeploy is installing;
    # the second line still says to run ./configure.
    assert "This tool is being installed right now (a redeploy in progress?): '.env' is missing; try again." in output
    assert f"If no install is running, run './configure' in the tool folder ({demo}), then re-run this command." in (
        output
    )
    assert output.count("is being installed right now") == 1
    assert len(output.splitlines()) < 15, output


# libs/ can hold whole repositories, tests included (scripts/get_libs.sh clones
# allele-validator in the README). Without testpaths, pytest collected those too:
# 23 errors for allele-validator's tests.


def test_pytest_collects_only_the_tools_own_tests(demo):
    vendored = demo / "libs" / "vendored" / "tests"
    vendored.mkdir(parents=True)
    (vendored / "test_vendored.py").write_text("import a_module_only_its_own_repo_has\n\n\ndef test_v():\n    pass\n")

    proc = run_pytest(demo)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert " passed" in proc.stdout
    assert "vendored" not in proc.stdout
    assert "error" not in proc.stdout.lower()
