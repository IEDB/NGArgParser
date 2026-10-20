"""Parser fixes: -o and positional inputs, defaults a check refuses, pytest collection.

Each test runs the generated run script (or pytest) in a copy of one freshly
generated project.

-o may not name a positional input either (predict seqs.tsv -o seqs wrote the
result over seqs.tsv), nor any file of a list input (nargs="+").

A default that its type= check refuses (a relative --model-dir default run from
another folder, type=int with default="abc") says it is the default, where it
is read from, and where to fix it: the message read as if the user had typed it.

A parser class named Test...ArgumentParser, imported into a test file, is no
test class: pytest warned that it could not collect it.
"""

import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from ngargparser import cli


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("template")
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(root)
        # No uv: scaffolding skips the slow `uv lock`.
        mp.setattr(cli.shutil, "which", lambda _cmd: None)
        assert not cli.startapp_command(Namespace(project_name="demo"))
    project = root / "demo"
    (project / "seqs.tsv").write_text(">a\nAC\n")
    (project / "more.tsv").write_text(">b\nDE\n")
    return project


@pytest.fixture
def project(template, tmp_path):
    copy = Path(shutil.copytree(template, tmp_path / "demo", symlinks=True))
    # .env names the folder the tool was configured in, which a copy has to say too.
    env_file = copy / ".env"
    if env_file.exists():
        env_file.write_text(env_file.read_text().replace(str(template), str(copy)))
    return copy


def run(project, *argv, cwd=None):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, str(project / "src" / "run_demo.py"), *argv],
        cwd=cwd or project,
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


POSITIONAL = """\
self.parser_predict.remove_argument("--input-tsv")
self.parser_predict.remove_argument("--input-json")
self.parser_predict.add_argument("fasta", type=validators.validate_file, nargs="+")
"""


# 1. -o naming a positional input


@pytest.mark.parametrize("files", [["seqs.tsv"], ["more.tsv", "seqs.tsv"]], ids=["one", "second-of-list"])
def test_o_naming_a_positional_input_is_refused(project, files):
    add_to_parser(project, POSITIONAL)

    proc = run(project, "predict", *files, "-o", "seqs")

    assert proc.returncode == 2, proc.stderr
    assert last_line(proc) == (
        "run_demo.py predict: error: argument --output-prefix/-o: -o seqs would write seqs.tsv over "
        "the input given to fasta; pick another prefix"
    )
    assert (project / "seqs.tsv").read_text() == ">a\nAC\n"


def test_o_naming_no_positional_input_parses(project):
    add_to_parser(project, POSITIONAL)

    assert parsed(run(project, "predict", "seqs.tsv", "-o", "seqs-result"))


# 2. A default its type= check refuses


def test_relative_default_run_from_another_folder_says_it_is_the_default(project, tmp_path):
    (project / "models").mkdir()
    add_to_parser(
        project, 'self.parser_predict.add_argument("--model-dir", type=validators.validate_directory, default="models")'
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    # From the tool's folder the default is found.
    assert parsed(run(project, "predict", "-t", str(project / "seqs.tsv")))
    proc = run(project, "predict", "-t", str(project / "seqs.tsv"), cwd=elsewhere)

    assert proc.returncode == 2, proc.stderr
    parser_file = os.path.relpath(project / "src" / "DemoArgumentParser.py", elsewhere)
    assert last_line(proc) == (
        f"run_demo.py predict: error: the default of --model-dir ('models', relative to {elsewhere}): "
        f"'models' does not exist; fix the default in {parser_file}, or pass --model-dir"
    )


def test_int_default_that_is_no_number_says_it_is_the_default(project):
    add_to_parser(project, 'self.parser_predict.add_argument("--top", type=int, default="abc")')

    proc = run(project, "predict", "-t", "seqs.tsv")

    assert proc.returncode == 2, proc.stderr
    assert last_line(proc) == (
        "run_demo.py predict: error: the default of --top ('abc'): invalid int value: 'abc'; "
        "fix the default in src/DemoArgumentParser.py, or pass --top"
    )


@pytest.mark.parametrize("given", [["--model-dir", "models"], ["--model-dir=models"]])
def test_the_same_value_typed_keeps_the_usual_message(project, tmp_path, given):
    add_to_parser(
        project, 'self.parser_predict.add_argument("--model-dir", type=validators.validate_directory, default="models")'
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    proc = run(project, "predict", "-t", str(project / "seqs.tsv"), *given, cwd=elsewhere)

    assert proc.returncode == 2, proc.stderr
    assert last_line(proc) == "run_demo.py predict: error: argument --model-dir: 'models' does not exist."


# 3. A parser class named Test...ArgumentParser in a test file


def test_parser_class_named_test_is_not_collected(project):
    (project / "tests" / "test_named.py").write_text(
        "from DemoArgumentParser import DemoArgumentParser\n\n\n"
        "class TestToolArgumentParser(DemoArgumentParser):\n"
        "    pass\n\n\n"
        "def test_parses():\n"
        "    assert TestToolArgumentParser.__test__ is False\n"
        "    assert TestToolArgumentParser().parse_args(['predict', '-t', 'seqs.tsv']).input_tsv\n"
    )
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-W",
            "error::pytest.PytestCollectionWarning",
            "tests/test_named.py",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cannot collect" not in proc.stdout
    assert "1 passed" in proc.stdout
