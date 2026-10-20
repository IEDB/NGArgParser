"""The example app (aa-counter) reads a TSV file's peptide column by name.

predict -t took line 1 as a header and column 0 as the peptide, and checked
neither. A file without a header lost its first peptide, a header with the
peptide in another column counted the wrong text, and a JSON file passed to -t
was counted as text; each exited 0. validate_input_tsv now refuses a file whose
header line has no 'peptide' column in one usage line, and convert_tsv_to_json
reads the 'peptide' column wherever it is.
"""

import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

# predict -t src/example.tsv -a L, as the app printed it before this round.
EXAMPLE_TSV_RESULT = (
    "peptide\tlength\tAAcount(L)\n"
    "ADMGHLKY\t8\t1\n"
    "ELDDTLKY\t8\t2\n"
    "FMDHVLRYP\t9\t1\n"
    "FSDLPLRPTV\t10\t2\n"
    "FSDNIEPGFY\t10\t0\n"
    "FTELAILY\t8\t2\n"
    "GLDEPLLK\t8\t3\n"
    "GLDQPLLK\t8\t3\n"
    "HPIDFAIQY\t9\t0\n"
)


@pytest.fixture
def example(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="example"))
    root = in_tmp_dir / "aa-counter"
    (root / "nohdr.tsv").write_text("ADMGHLKY\nLLL\n")
    (root / "swapped.tsv").write_text("length\tpeptide\n8\tADMGHLKY\n3\tLLL\n")
    return root


def run(project, *argv):
    return subprocess.run(
        [sys.executable, "src/run_aa_counter.py", *argv], cwd=project, capture_output=True, text=True, timeout=60
    )


@pytest.mark.parametrize(
    "path, found",
    [
        ("nohdr.tsv", "'ADMGHLKY'"),
        ("src/example.json", "'{'"),
    ],
)
def test_a_tsv_without_a_peptide_header_is_refused_in_one_line(example, path, found):
    result = run(example, "predict", "-t", path, "-a", "L")

    assert (result.returncode, result.stdout) == (2, "")
    assert "Traceback" not in result.stderr
    assert result.stderr.splitlines()[-1] == (
        f"run_aa_counter.py predict: error: argument --input-tsv/-t: "
        f"'{path}' has no 'peptide' column in its header line (found: {found})."
    )


def test_the_peptide_column_is_read_by_name(example):
    result = run(example, "predict", "-t", "swapped.tsv", "-a", "L")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "peptide\tlength\tAAcount(L)\nADMGHLKY\t8\t1\nLLL\t3\t3\n"


def test_the_shipped_example_tsv_is_unchanged(example):
    # Control: src/example.tsv has 'peptide' as its first column, and counts as before.
    result = run(example, "predict", "-t", "src/example.tsv", "-a", "L")

    assert result.returncode == 0, result.stderr
    assert result.stdout == EXAMPLE_TSV_RESULT
