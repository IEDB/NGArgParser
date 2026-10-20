"""An empty path is refused, and so is a bare table given to write_results.

- Path('') is the current folder, so an empty path (an unset shell variable)
  counted as it: postprocess -i '' -o merged merged whatever was in the current
  folder and exited 0, preprocess -o '' filled it with the job folders, and
  predict -j '' said "'' is a folder". Every path check of the framework now
  says "the path is empty".
- write_results({"type": ..., "table_columns": ..., "table_data": ...}, ...), one
  table without the envelope, wrote a 1-byte .tsv (an empty line) and a .json
  with no "results". It now raises ValueError naming the fix, before anything
  is written.
"""

import argparse
import json
import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli
from ngargparser import core_validators as cv
from ngargparser.result_writer import write_results

# 1. Empty paths ----------------------------------------------------------------


@pytest.mark.parametrize(
    "check, wanted",
    [
        (cv.validate_file, "a file"),
        (cv.validate_directory, "a folder"),
        (cv.validate_directory_given_filename, "a file path"),
    ],
)
def test_each_path_validator_refuses_an_empty_path(check, wanted):
    with pytest.raises(argparse.ArgumentTypeError) as e:
        check("")

    assert str(e.value) == f"the path is empty; give {wanted}."


def test_the_current_folder_named_as_dot_is_still_a_folder(tmp_path, monkeypatch):
    # Control: '.' names the current folder on purpose, and stays valid.
    monkeypatch.chdir(tmp_path)

    assert cv.validate_directory(".").is_dir()


@pytest.fixture
def project(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    root = in_tmp_dir / "demo"
    (root / "in.json").write_text("{}\n")
    return root


def run(project, *argv):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60, env=env
    )


def listing(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if "__pycache__" not in p.parts)


@pytest.mark.parametrize(
    "argv, message",
    [
        (
            ["postprocess", "-i", "", "-o", "merged"],
            "argument --input-results-dir/-i: the path is empty; give a folder.",
        ),
        (["postprocess", "-j", ""], "argument --job-desc-file/-j: the path is empty; give a file."),
        (
            ["postprocess", "-j", "in.json", "-p", ""],
            "argument --postprocessed-results-dir/-p: the path is empty; give a folder.",
        ),
        (["predict", "-j", ""], "argument --input-json/-j: the path is empty; give a file."),
        (["predict", "-t", ""], "argument --input-tsv/-t: the path is empty; give a file."),
        (["preprocess", "-j", "", "-o", "out"], "argument --input-json/-j: the path is empty; give a file."),
        (["preprocess", "-j", "in.json", "-o", ""], "argument --output-dir/-o: the path is empty; give a folder."),
        (
            ["preprocess", "-j", "in.json", "-o", "out", "--params-dir", ""],
            "argument --params-dir: the path is empty; give a folder.",
        ),
        (
            ["preprocess", "-j", "in.json", "-o", "out", "--inputs-dir", ""],
            "argument --inputs-dir: the path is empty; give a folder.",
        ),
    ],
    ids=lambda value: " ".join(value) if isinstance(value, list) else None,
)
def test_an_empty_path_on_the_command_line_stops_with_one_line(project, argv, message):
    before = listing(project)

    proc = run(project, *argv)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stderr.splitlines()[-1].endswith(f"error: {message}")
    assert "Traceback" not in proc.stderr
    assert listing(project) == before, "nothing is created in the current folder"


def test_preprocess_with_a_named_folder_still_runs(project):
    # Control: the same command with a folder name gets past the parser.
    proc = run(project, "preprocess", "-j", "in.json", "-o", "out")

    assert "preprocess is not implemented yet" in proc.stderr
    assert (project / "out" / "predict-inputs" / "params").is_dir()


# 2. A bare table ---------------------------------------------------------------

BARE = {"type": "t", "table_columns": ["a"], "table_data": [[1]]}
WRAP = '{"results": [table]}'


@pytest.mark.parametrize("output_format", ["tsv", "json"])
@pytest.mark.parametrize(
    "result",
    [BARE, {"table_columns": ["a"], "table_data": [[1]]}, {"table_data": [[1]]}, {"type": "t", "table_columns": ["a"]}],
    ids=["whole table", "no type", "rows only", "columns only"],
)
def test_a_bare_table_is_refused_before_anything_is_written(tmp_path, capsys, result, output_format):
    prefix = tmp_path / "res"

    with pytest.raises(ValueError) as e:
        write_results(dict(result), str(prefix), output_format)

    assert str(e.value).startswith(f"write_results: the result is one table, not the envelope: wrap it as {WRAP}")
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("output_format", ["tsv", "json"])
def test_a_bare_table_to_stdout_is_refused_too(capsys, output_format):
    with pytest.raises(ValueError, match=r"wrap it as \{\"results\": \[table\]\}"):
        write_results(dict(BARE), None, output_format)

    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("output_format", ["tsv", "json"])
def test_the_wrapped_table_is_written(tmp_path, output_format):
    # Control: the fix the message names writes the table.
    written = write_results({"results": [dict(BARE)]}, str(tmp_path / "res"), output_format)

    text = (tmp_path / f"res.{output_format}").read_text()
    assert written == [str(tmp_path / f"res.{output_format}")]
    if output_format == "tsv":
        assert text == "a\n1\n"
    else:
        assert json.loads(text) == {"results": [BARE]}


def test_an_envelope_with_extra_top_level_table_keys_is_still_written(tmp_path):
    # Control: with "results" present, other top-level keys stay extra keys, as before.
    result = {"results": [dict(BARE)], "table_columns": ["x"]}

    write_results(result, str(tmp_path / "res"), "tsv")

    assert (tmp_path / "res.tsv").read_text() == "a\n1\n"
