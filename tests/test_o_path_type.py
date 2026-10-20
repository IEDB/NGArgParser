"""-o typed as a Path gets the checks a text -o gets.

Each test runs the generated run script in a copy of one freshly generated project.

A tool that gives -o a type= returning a Path (update_arguments("-o",
type=pathlib.Path), or validators.validate_directory_given_filename) lost the
check that -o names no input: predict -t data/in.tsv -o data/in parsed, and the
result was written over data/in.tsv. The check skipped any prefix that was not
text. The parsed value stays the Path the tool's type= gave.
"""

import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from ngargparser import cli

INPUT = "a\tAC\n"

TYPES = {
    "pathlib.Path": 'import pathlib\nself.parser_predict.update_arguments("-o", type=pathlib.Path)\n',
    "validate_directory_given_filename": (
        'self.parser_predict.update_arguments("-o", type=validators.validate_directory_given_filename)\n'
    ),
}

POSITIONAL = """\
self.parser_predict.remove_argument("--input-tsv")
self.parser_predict.remove_argument("--input-json")
self.parser_predict.add_argument("fasta", type=validators.validate_file, nargs="+")
"""

# How each form gives data/in.tsv, and the name the message gives that input.
FORMS = {
    "-t": ("", ["-t", "data/in.tsv"], "--input-tsv/-t"),
    "positional": (POSITIONAL, ["data/in.tsv"], "fasta"),
}

SHOW_PREFIX = """\
import pathlib
import sys

from DemoArgumentParser import DemoArgumentParser

args = DemoArgumentParser().parse_args(sys.argv[1:])
print(isinstance(args.output_prefix, pathlib.Path), args.output_prefix)
"""


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("template")
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(root)
        # No uv: scaffolding skips the slow `uv lock`.
        mp.setattr(cli.shutil, "which", lambda _cmd: None)
        assert not cli.startapp_command(Namespace(project_name="demo"))
    project = root / "demo"
    (project / "data").mkdir()
    (project / "data" / "in.tsv").write_text(INPUT)
    (project / "src" / "show_prefix.py").write_text(SHOW_PREFIX)
    return project


@pytest.fixture
def project(template, tmp_path):
    copy = Path(shutil.copytree(template, tmp_path / "demo", symlinks=True))
    # .env names the folder the tool was configured in, which a copy has to say too.
    env_file = copy / ".env"
    if env_file.exists():
        env_file.write_text(env_file.read_text().replace(str(template), str(copy)))
    return copy


def run(project, *argv, script="run_demo.py"):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, str(project / "src" / script), *argv],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def add_to_parser(project, code):
    """Append `code` to the end of DemoArgumentParser.__init__."""
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text() + "".join(f"        {line}\n" for line in code.splitlines()))


def last_line(proc):
    return proc.stderr.strip().splitlines()[-1]


def parsed(proc):
    """True when the command line parsed: the template's predict branch then stops."""
    return proc.returncode == 1 and "predict is not implemented yet" in proc.stderr


def refused_once(proc):
    """Exit code 2 with a single error line, never a traceback."""
    return proc.returncode == 2 and proc.stderr.count("error:") == 1 and "Traceback" not in proc.stderr


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("o_type", TYPES)
def test_path_typed_o_naming_the_input_is_refused(project, o_type, form):
    extra, given, name = FORMS[form]
    add_to_parser(project, extra + TYPES[o_type])

    proc = run(project, "predict", *given, "-o", "data/in")

    assert refused_once(proc), proc.stderr
    assert last_line(proc) == (
        "run_demo.py predict: error: argument --output-prefix/-o: -o data/in would write data/in.tsv over "
        f"the input given to {name}; pick another prefix"
    )
    assert (project / "data" / "in.tsv").read_text() == INPUT
    assert sorted(p.name for p in (project / "data").iterdir()) == ["in.tsv"]


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("o_type", TYPES)
def test_path_typed_o_naming_no_input_parses_and_stays_a_path(project, o_type, form):
    extra, given, _name = FORMS[form]
    add_to_parser(project, extra + TYPES[o_type])

    assert parsed(run(project, "predict", *given, "-o", "data/out"))
    proc = run(project, "predict", *given, "-o", "data/out", script="show_prefix.py")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "True data/out\n"


@pytest.mark.parametrize(
    "value, problem",
    [
        ("", "the value is empty"),
        ("data/", "'data/' is a folder"),
        ("tsv", "'tsv' is a format name"),
        ("data/in.tsv", "'data/in.tsv' ends in .tsv"),
        ("data/in.tsv/x", "'data/in.tsv' is a file, not a folder"),
    ],
    ids=["empty", "folder", "format-name", "extension", "unwritable-folder"],
)
@pytest.mark.parametrize("o_type", TYPES)
def test_path_typed_o_keeps_the_text_checks(project, o_type, value, problem):
    add_to_parser(project, TYPES[o_type])

    proc = run(project, "predict", "-t", "data/in.tsv", "-o", value)

    assert refused_once(proc), proc.stderr
    assert last_line(proc).startswith(f"run_demo.py predict: error: argument --output-prefix/-o: {problem}")
    assert (project / "data" / "in.tsv").read_text() == INPUT
