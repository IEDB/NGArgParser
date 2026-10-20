"""run_x.py --version prints the tool's version.

It said "error: the following arguments are required: subcommand". It prints
the first line of the VERSION file a build writes, else version in the
[project] table of pyproject.toml, else "unknown". A --version of the tool's
own (tcell_mhcii and mhci_prediction declare one) replaces the framework's.

Each test runs inside a freshly generated project.
"""

import json
import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli


@pytest.fixture
def project(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    return in_tmp_dir / "demo"


def run(project, *argv):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60, env=env
    )


def append_to_parser(project, code):
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text() + f"        {code}\n")


def test_version_prints_the_pyproject_version(project):
    proc = run(project, "--version")

    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "run_demo.py 0.1.0\n", "")


def test_version_prefers_the_build_version_file(project):
    # make build writes the version, then the build date.
    (project / "VERSION").write_text("2.3.0\nMon Oct 19 10:00:00 UTC 2026\n")

    proc = run(project, "--version")

    assert (proc.returncode, proc.stdout) == (0, "run_demo.py 2.3.0\n")


def test_version_skips_an_empty_version_file(project):
    # An empty line is a build without a version, as 'local' is.
    (project / "VERSION").write_text("\n")

    proc = run(project, "--version")

    assert proc.stdout == "run_demo.py 0.1.0 (local build)\n"


def test_version_without_either_is_unknown(project):
    (project / "pyproject.toml").rename(project / "pyproject.toml.away")

    proc = run(project, "--version")

    assert (proc.returncode, proc.stdout) == (0, "run_demo.py unknown\n")


def test_version_is_in_the_top_level_help(project):
    proc = run(project, "-h")

    assert proc.returncode == 0, proc.stderr
    assert "[-h] [--version] {predict,preprocess,postprocess}" in proc.stdout
    assert "--version             show the tool's version and exit" in proc.stdout


@pytest.mark.parametrize(
    "code",
    [
        'self.add_argument("--version", action="version", version="own 9.9")',
        'self.add_argument_group("info").add_argument("--version", action="version", version="own 9.9")',
        'self.add_mutually_exclusive_group().add_argument("--version", action="version", version="own 9.9")',
    ],
    ids=["add_argument", "group", "exclusive group"],
)
def test_a_version_option_of_your_own_replaces_the_framework_one(project, code):
    append_to_parser(project, code)

    proc = run(project, "--version")
    shown = run(project, "-h")

    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "own 9.9\n", "")
    assert shown.stdout.count("--version") == 2  # usage and options, once each


PROBE = """\
import json, sys
sys.path.insert(0, "src")
from core.NGArgumentParser import _project_version
print(json.dumps([_project_version(text) for text in json.load(sys.stdin)]))
"""

PYPROJECTS = {
    '[project]\nname = "x"\nversion = "1.2.3"\n': "1.2.3",
    "[project]\nversion = '1.2'  # pinned\n": "1.2",
    '[tool.x]\nversion = "9"\n\n[ project ]\nversion = "1.0"\n': "1.0",
    '[project]\nname = "x"\n\n[tool.uv]\nversion = "9"\n': None,
    '[project]\ndynamic = ["version"]\n': None,
    "": None,
}


def test_project_version_reads_only_the_project_table(project):
    script = project / "probe_version.py"
    script.write_text(PROBE)

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=project,
        input=json.dumps(list(PYPROJECTS)),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == list(PYPROJECTS.values())
