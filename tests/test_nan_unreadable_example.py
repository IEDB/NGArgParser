"""NaN in json, unreadable inputs, and the example app's inputs.

- write_results wrote NaN and +/-Infinity into .json as NaN/Infinity, which is
  no JSON (JavaScript's JSON.parse refuses it). They are now written as null,
  as Python floats and numpy values, at any depth; tsv is unchanged.
- validate_file let a mode-000 file through, and the run ended in a raw
  PermissionError; a file in a folder this user may not enter raised
  PermissionError from the validator itself. Both are now one usage line, and
  so is a folder validate_directory cannot read.
- The example app (aa-counter) gave tracebacks for a typo'd JSON key and
  malformed JSON (predict and preprocess), read a TSV line with an extra
  column without a word, and counted -a Z and -a LL. Each is now one usage line.
"""

import json
import os
import subprocess
import sys
from argparse import ArgumentTypeError, Namespace

import pytest

from ngargparser import cli
from ngargparser import core_validators as cv
from ngargparser.result_writer import write_results

NAN, INF = float("nan"), float("inf")


def strict_json(text):
    """json.loads that refuses NaN and Infinity, as strict JSON readers do."""

    def refuse(constant):
        raise ValueError(f"not JSON: {constant}")

    return json.loads(text, parse_constant=refuse)


class Scalar:
    """A stand-in for a numpy scalar: .item() gives the value."""

    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class Array:
    """A stand-in for a numpy array: .tolist() gives the values."""

    def __init__(self, values):
        self.values = values

    def tolist(self):
        return list(self.values)


def table(*rows, **extra):
    columns = [f"c{i}" for i in range(len(rows[0]))]
    return {"results": [{"type": "scores", "table_columns": columns, "table_data": [list(r) for r in rows], **extra}]}


# ---- 1. NaN and Infinity in json -------------------------------------------


def test_json_writes_nan_and_infinity_as_null(capsys):
    result = table(
        ["AAA", NAN, INF, -INF, Scalar(NAN), Scalar(-INF)],
        ["BBB", Array([1.5, NAN]), {"x": INF}, (2.0, NAN), 3, "NaN"],
        field_ranges={"c1": [-INF, 1.0]},
    )
    write_results(result, None, "json")

    written = strict_json(capsys.readouterr().out)
    assert written["results"][0]["table_data"] == [
        ["AAA", None, None, None, None, None],
        ["BBB", [1.5, None], {"x": None}, [2.0, None], 3, "NaN"],
    ]
    assert written["results"][0]["field_ranges"] == {"c1": [None, 1.0]}


def test_json_file_with_nan_is_valid_json(tmp_path, capsys):
    write_results(table(["AAA", NAN]), str(tmp_path / "out"), "json")

    assert strict_json((tmp_path / "out.json").read_text())["results"][0]["table_data"] == [["AAA", None]]


def test_json_writes_numpy_nan_and_infinity_as_null(capsys):
    np = pytest.importorskip("numpy")
    result = table(["AAA", np.float64("nan"), np.float32("inf"), np.array([np.nan, 0.5]), np.int64(2)])
    write_results(result, None, "json")

    assert strict_json(capsys.readouterr().out)["results"][0]["table_data"] == [["AAA", None, None, [None, 0.5], 2]]


def test_json_without_nan_is_unchanged(capsys):
    # Control: finite values are written as before, and an unwritable value is still named.
    result = table(["AAA", 0.5, Scalar(0.25), 1e308, -0.0])
    write_results(result, None, "json")
    assert capsys.readouterr().out == json.dumps(result, indent=2, default=lambda v: v.item()) + "\n"

    with pytest.raises(ValueError, match=r"results\[0\]\.table_data\[0\]\[1\] is of type set"):
        write_results(table(["AAA", {NAN}]), None, "json")


def test_tsv_still_writes_nan_as_text(capsys):
    write_results(table(["AAA", NAN, -INF]), None, "tsv")

    assert capsys.readouterr().out == "c0\tc1\tc2\nAAA\tnan\t-inf\n"


# ---- 2. Unreadable inputs ---------------------------------------------------

as_root = pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")


@pytest.fixture
def locked(tmp_path):
    """A mode-000 file and folder, and a file in a folder this user may not enter."""
    (tmp_path / "f000").write_text("x\n")
    (tmp_path / "d000").mkdir()
    (tmp_path / "noenter").mkdir()
    (tmp_path / "noenter" / "in.json").write_text("{}\n")
    modes = {"f000": 0o000, "d000": 0o000, "noenter": 0o600}
    for name, mode in modes.items():
        (tmp_path / name).chmod(mode)
    yield tmp_path
    for name in modes:
        (tmp_path / name).chmod(0o700)


@as_root
@pytest.mark.parametrize(
    "validator, name",
    [
        (cv.validate_file, "f000"),
        (cv.validate_file, "noenter/in.json"),
        (cv.validate_directory, "d000"),
        (cv.validate_directory, "noenter"),
    ],
)
def test_an_unreadable_path_is_refused_in_one_line(locked, validator, name):
    path = str(locked / name)
    with pytest.raises(ArgumentTypeError) as refused:
        validator(path)

    assert str(refused.value) == f"'{path}' cannot be read (permission denied)."


def test_readable_paths_still_pass(tmp_path):
    # Control: a readable file and folder are returned as before.
    (tmp_path / "in.json").write_text("{}\n")

    assert cv.validate_file(str(tmp_path / "in.json")) == tmp_path / "in.json"
    assert cv.validate_directory(str(tmp_path)) == tmp_path


# ---- 3. The example app's inputs ---------------------------------------------


@pytest.fixture
def example(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="example"))
    root = in_tmp_dir / "aa-counter"
    for name, text in (
        ("typo.json", '{"peptide": ["ADMGHLKY"], "amino_acd": "L"}\n'),
        ("malformed.json", '{"peptide": ["ADMGHLKY"], \n'),
        ("aa.json", '{"peptide": ["ADMGHLKY"], "amino_acid": "Z"}\n'),
        ("extra.tsv", "peptide\tlength\nADMGHLKY\t8\textra\n"),
    ):
        (root / name).write_text(text)
    return root


def run(project, *argv):
    return subprocess.run(
        [sys.executable, "src/run_aa_counter.py", *argv], cwd=project, capture_output=True, text=True, timeout=60
    )


@pytest.mark.parametrize(
    "argv, error",
    [
        (
            ["predict", "-j", "typo.json"],
            "predict: error: argument --input-json/-j: 'typo.json' has no 'amino_acid' key "
            "(its keys: 'peptide', 'amino_acd').",
        ),
        (
            ["preprocess", "-j", "typo.json", "-o", "out"],
            "preprocess: error: argument --input-json/-j: 'typo.json' has no 'amino_acid' key "
            "(its keys: 'peptide', 'amino_acd').",
        ),
        (
            ["predict", "-j", "malformed.json"],
            "predict: error: argument --input-json/-j: 'malformed.json' is not valid JSON: "
            "Expecting property name enclosed in double quotes: line 2 column 1 (char 27).",
        ),
        (
            ["preprocess", "-j", "malformed.json", "-o", "out"],
            "preprocess: error: argument --input-json/-j: 'malformed.json' is not valid JSON: "
            "Expecting property name enclosed in double quotes: line 2 column 1 (char 27).",
        ),
        (
            ["predict", "-j", "aa.json"],
            "predict: error: argument --input-json/-j: 'aa.json': amino_acid 'Z' is not an amino acid; "
            "give one letter of ACDEFGHIKLMNPQRSTVWY.",
        ),
        (
            ["predict", "-t", "extra.tsv", "-a", "L"],
            "predict: error: argument --input-tsv/-t: 'extra.tsv' line 2 has 3 columns, but its header has 2.",
        ),
        (
            ["predict", "-t", "src/example.tsv", "-a", "Z"],
            "predict: error: argument --amino-acid/-a: 'Z' is not an amino acid; give one letter of "
            "ACDEFGHIKLMNPQRSTVWY.",
        ),
        (
            ["predict", "-t", "src/example.tsv", "-a", "LL"],
            "predict: error: argument --amino-acid/-a: 'LL' is not an amino acid; give one letter of "
            "ACDEFGHIKLMNPQRSTVWY.",
        ),
    ],
)
def test_example_app_refuses_bad_input_in_one_line(example, argv, error):
    result = run(example, *argv)

    assert (result.returncode, result.stdout) == (2, "")
    assert "Traceback" not in result.stderr
    assert result.stderr.splitlines()[-1] == f"run_aa_counter.py {error}"
    assert not (example / "out").exists()


def test_example_app_still_counts_good_input(example):
    # Control: the shipped inputs, a lower-case -a, and a job file preprocess wrote.
    tsv = run(example, "predict", "-t", "src/example.tsv", "-a", "l")
    js = run(example, "predict", "-j", "src/example.json")
    pre = run(example, "preprocess", "-j", "src/example.json", "-o", "jobs")

    assert tsv.returncode == 0 and tsv.stdout.startswith("peptide\tlength\tAAcount(l)\nADMGHLKY\t8\t1\n")
    assert js.returncode == 0 and js.stdout.startswith("peptide\tlength\tAAcount(L)\nADMGHLKY\t8\t1\n")
    assert pre.returncode == 0, pre.stderr
    job = json.loads((example / "jobs" / "job_descriptions.json").read_text())[0]["shell_cmd"]
    job_file = job.split(" -j ", 1)[1].split()[0]
    from_job = run(example, "predict", "-j", job_file)
    assert from_job.returncode == 0 and from_job.stdout.startswith("peptide\tlength\tAAcount(L)\n"), from_job.stderr
