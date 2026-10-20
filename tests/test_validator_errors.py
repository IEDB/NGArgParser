"""A type= check of the tool's own must fail as a usage error, never a traceback.

- A validator that let a ValueError or TypeError escape, such as int("abc") with
  no try, gave argparse's "invalid validate_peptide_length value: 'abc'", which
  names the Python function, not what is wrong with the value.
- A validator that raised any other exception, such as RuntimeError, gave a
  traceback and exit code 1.

Both hold for the subcommands' options and the top-level ones. An
ArgumentTypeError keeps its message, Python's own int keeps argparse's
"invalid int value", and the framework's own options keep their messages.
"""

import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

VALIDATORS = """
from fractions import Fraction  # a class, but not one of Python's own int, float and complex


def validate_peptide_length(value):
    return int(value)


def validate_lookup(value):
    raise RuntimeError("the allele database is down")


def validate_quiet(value):
    raise ValueError()


def validate_named(value):
    if value != "ok":
        raise argparse.ArgumentTypeError(f"'{value}' is not ok")
    return value
"""

OPTIONS = """
        self.parser_predict.add_argument("--peptide-length", "-n", dest="peptide_length",
                                         type=validators.validate_peptide_length, help="Length.")
        self.parser_predict.add_argument("--allele", type=validators.validate_lookup, help="Allele.")
        self.parser_predict.add_argument("--quiet", type=validators.validate_quiet, help="Quiet.")
        self.parser_predict.add_argument("--named", type=validators.validate_named, help="Named.")
        self.parser_predict.add_argument("--count", type=int, help="Count.")
        self.parser_predict.add_argument("--ratio", type=validators.Fraction, help="Ratio.")
        self.parser_preprocess.add_argument("--allele", type=validators.validate_lookup, group="mine")
        self.parser_postprocess.add_argument("--peptide-length", type=validators.validate_peptide_length)
        self.add_argument("--level", type=validators.validate_peptide_length, help="Level.")
        self.add_argument("--server", type=validators.validate_lookup, help="Server.")
"""


@pytest.fixture
def project(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    root = in_tmp_dir / "demo"
    (root / "in.tsv").write_text("peptide\nAAK\n")
    (root / "in.json").write_text("[]\n")
    with open(root / "src" / "validators.py", "a") as f:
        f.write(VALIDATORS)
    with open(root / "src" / "DemoArgumentParser.py", "a") as f:
        f.write(OPTIONS)
    return root


def run(project, *argv):
    return subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60
    )


def last_line(proc):
    return proc.stderr.strip().splitlines()[-1]


CASES = {
    "ValueError, predict": (
        ["predict", "-t", "in.tsv", "-n", "abc"],
        "run_demo.py predict: error: argument --peptide-length/-n: 'abc' is not valid: "
        "invalid literal for int() with base 10: 'abc'",
    ),
    "ValueError, postprocess": (
        ["postprocess", "-j", "in.json", "--peptide-length", "x"],
        "run_demo.py postprocess: error: argument --peptide-length: 'x' is not valid: "
        "invalid literal for int() with base 10: 'x'",
    ),
    "ValueError, top-level": (
        ["--level", "abc", "predict", "-t", "in.tsv"],
        "run_demo.py: error: argument --level: 'abc' is not valid: invalid literal for int() with base 10: 'abc'",
    ),
    "ValueError with no text": (
        ["predict", "-t", "in.tsv", "--quiet", "q"],
        "run_demo.py predict: error: argument --quiet: 'q' is not valid: ValueError",
    ),
    "ValueError from a class": (
        ["predict", "-t", "in.tsv", "--ratio", "x"],
        "run_demo.py predict: error: argument --ratio: 'x' is not valid: Invalid literal for Fraction: 'x'",
    ),
    "RuntimeError, predict": (
        ["predict", "-t", "in.tsv", "--allele", "HLA-A*02:01"],
        "run_demo.py predict: error: argument --allele: the check validate_lookup failed on 'HLA-A*02:01': "
        "RuntimeError: the allele database is down",
    ),
    "RuntimeError, preprocess": (
        ["preprocess", "-j", "in.json", "-o", "out", "--allele", "A"],
        "run_demo.py preprocess: error: argument --allele: the check validate_lookup failed on 'A': "
        "RuntimeError: the allele database is down",
    ),
    "RuntimeError, top-level": (
        ["--server", "s1", "predict", "-t", "in.tsv"],
        "run_demo.py: error: argument --server: the check validate_lookup failed on 's1': "
        "RuntimeError: the allele database is down",
    ),
}


@pytest.mark.parametrize("name", CASES)
def test_a_check_that_fails_is_one_usage_line(project, name):
    argv, message = CASES[name]

    proc = run(project, *argv)

    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert last_line(proc) == message
    assert not (project / "out").exists()  # preprocess failed before it created anything


def test_good_values_and_messages_stay_as_they_were(project):
    # An ArgumentTypeError keeps its own message, int keeps argparse's, and the
    # framework's own checks keep theirs.
    named = run(project, "predict", "-t", "in.tsv", "--named", "bad")
    count = run(project, "predict", "-t", "in.tsv", "--count", "abc")
    missing = run(project, "predict", "-t", "missing.tsv")
    prefix = run(project, "predict", "-t", "in.tsv", "-o", "json")
    folder = run(project, "postprocess", "-j", "in.json", "-p", "nope")

    assert [p.returncode for p in (named, count, missing, prefix, folder)] == [2] * 5
    assert last_line(named) == "run_demo.py predict: error: argument --named: 'bad' is not ok"
    assert last_line(count) == "run_demo.py predict: error: argument --count: invalid int value: 'abc'"
    assert last_line(missing) == "run_demo.py predict: error: argument --input-tsv/-t: 'missing.tsv' does not exist."
    assert last_line(prefix).startswith(
        "run_demo.py predict: error: argument --output-prefix/-o: 'json' is a format name"
    )
    assert last_line(folder) == (
        "run_demo.py postprocess: error: argument --postprocessed-results-dir/-p: 'nope' does not exist."
    )
    # Values the checks accept reach the unedited predict stub, and a failing
    # check next to them is still one line.
    good = run(
        project, "--level", "3", "predict", "-t", "in.tsv", "-n", "9", "--named", "ok", "--count", "2", "--ratio", "1/3"
    )
    failing = run(project, "predict", "-t", "in.tsv", "--named", "ok", "-n", "abc")
    assert good.returncode == 1
    assert "predict is not implemented yet" in good.stderr
    assert failing.returncode == 2
    assert "'abc' is not valid" in last_line(failing)


def test_a_check_given_to_a_framework_option_is_one_usage_line(project):
    # update_arguments(type=...) gives a framework option a check of the tool's own.
    parser = project / "src" / "DemoArgumentParser.py"
    with open(parser, "a") as f:
        f.write('        self.parser_predict.update_arguments("--output-prefix", type=validators.validate_lookup)\n')

    proc = run(project, "predict", "-t", "in.tsv", "-o", "result")

    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert last_line(proc) == (
        "run_demo.py predict: error: argument --output-prefix/-o: the check validate_lookup failed on 'result': "
        "RuntimeError: the allele database is down"
    )


def test_a_string_default_goes_through_the_check_too(project):
    parser = project / "src" / "DemoArgumentParser.py"
    with open(parser, "a") as f:
        f.write(
            '        self.parser_predict.add_argument("--window", type=validators.validate_peptide_length, default="w")\n'
        )

    proc = run(project, "predict", "-t", "in.tsv")

    assert proc.returncode == 2
    assert last_line(proc) == (
        "run_demo.py predict: error: the default of --window ('w'): 'w' is not valid: invalid literal for int() "
        "with base 10: 'w'; fix the default in src/DemoArgumentParser.py, or pass --window"
    )
