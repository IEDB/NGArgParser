"""remove_argument in either form, a subcommand of the tool's own, and its branch.

- remove_argument had two forms, and the framework's messages named the one
  that failed: following "remove_argument('--output-format', 'predict')" as
  self.parser_predict.remove_argument("--output-format", "predict") gave a
  TypeError traceback, and self.remove_argument("--output-format") one for the
  missing subcommand. Both forms now work, and every message names one form:
  self.parser_<subcommand>.remove_argument("<flag>").
- A subcommand of the tool's own was hard to find: self.add_subparsers() gave
  "error: cannot have multiple subparser arguments", as for a command-line
  mistake, and self.subparsers a traceback suggesting argparse's _subparsers.
  self.add_subcommand(name, help=...) adds one, and both mistakes name it.
- A subcommand with no branch in the run script printed nothing and exited 0.

Each test runs inside a freshly generated project.
"""

import json
import os
import re
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PRELUDE = """\
import json, sys
sys.path.insert(0, "src")
from DemoArgumentParser import DemoArgumentParser

def error_of(call):
    try:
        call()
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return None

p = DemoArgumentParser()
"""

# The one form every message names.
ONE_FORM = re.compile(r'self\.parser_\w+\.remove_argument\("[^"]+"\)')


@pytest.fixture
def project(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    root = in_tmp_dir / "demo"
    (root / "in.tsv").write_text("a\tb\n1\t2\n")
    return root


def run(project, *argv, script="src/run_demo.py"):
    # No .pyc: two edits of the same size within one second read the old one.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, script, *argv], cwd=project, capture_output=True, text=True, timeout=60, env=env
    )


def run_with(project, code, *argv):
    """Append `code` (lines of the generated parser's __init__) and run the run script.
    Returns the process and the number of the parser file's last line."""
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text() + code)
    line = len(parser_file.read_text().splitlines())
    return run(project, *argv), line


def probe(project, body):
    script = project / "probe_r10_w4.py"
    script.write_text(PRELUDE + body)
    proc = subprocess.run([sys.executable, str(script)], cwd=project, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def options(help_text):
    return set(re.findall(r"--[a-z][a-z-]*", help_text))


# 1. remove_argument: both forms work -------------------------------------------------


def test_subparser_remove_argument_takes_its_own_subcommand_name(project):
    proc, _ = run_with(
        project, '        self.parser_predict.remove_argument("--output-format", "predict")\n', "predict", "-h"
    )

    assert proc.returncode == 0, proc.stderr
    assert "--output-format" not in options(proc.stdout)
    assert "--output-prefix" in options(proc.stdout)


def test_subparser_remove_argument_refuses_another_subcommand_in_one_line(project):
    proc, line = run_with(
        project, '        self.parser_predict.remove_argument("--output-format", "postprocess")\n', "-h"
    )

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: self.parser_predict.remove_argument('--output-format', 'postprocess'): "
        'this is the predict parser, not postprocess; use self.parser_postprocess.remove_argument("--output-format")\n'
    )


def test_top_level_remove_argument_without_a_subcommand_removes_the_only_one(project):
    proc, _ = run_with(project, '        self.remove_argument("--input-tsv")\n', "predict", "-h")

    assert proc.returncode == 0, proc.stderr
    assert "--input-tsv" not in options(proc.stdout)
    assert "--input-json" in options(proc.stdout)


def test_top_level_remove_argument_without_a_subcommand_asks_which_of_two(project):
    proc, line = run_with(project, '        self.remove_argument("--output-format")\n', "-h")

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: remove_argument('--output-format'): predict and postprocess each have "
        '--output-format; say which: self.parser_predict.remove_argument("--output-format") or '
        'self.parser_postprocess.remove_argument("--output-format")\n'
    )


def test_top_level_remove_argument_without_a_subcommand_names_the_closest_option(project):
    out = probe(project, 'print(json.dumps(error_of(lambda: p.remove_argument("--input-tvs"))))')

    assert out == (
        f"ParserSetupError: probe_r10_w4.py:{PRELUDE.count(chr(10)) + 1}: remove_argument('--input-tvs'): "
        "no subcommand has --input-tvs; did you mean --input-tsv?"
    )


def test_top_level_remove_argument_with_a_subcommand_still_works(project):
    # Control: the two-argument form is unchanged.
    proc, _ = run_with(project, '        self.remove_argument("--output-format", "predict")\n', "predict", "-h")

    assert proc.returncode == 0, proc.stderr
    assert "--output-format" not in options(proc.stdout)


# 1. Every message names the one form, and that call, pasted as-is, works -------------


ADVISING = {
    "dest": 'self.parser_predict.update_arguments("--output-format", dest="fmt")',
    "rename": 'self.parser_predict.update_arguments("--output-format", "--format")',
    "action": 'self.parser_predict.update_arguments("--output-format", action="append")',
    "nargs": 'self.parser_postprocess.update_arguments("-f", nargs="+")',
    "formats": 'self.parser_predict.update_arguments("-f", choices=["tsv", "json", "csv"])',
    "clash": 'self.parser_predict.add_argument("--output-format", "-f", choices=["csv"])',
}


@pytest.mark.parametrize("call", ADVISING.values(), ids=ADVISING.keys())
def test_the_remove_argument_a_message_names_works_as_written(project, call):
    proc, _ = run_with(project, f"        {call}\n", "-h")
    advice = ONE_FORM.findall(proc.stderr)

    assert proc.returncode == 2
    assert len(advice) == 1, proc.stderr
    assert "remove_argument('" not in proc.stderr

    # The advice, pasted in place of the mistake, runs.
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text().replace(call, advice[0]))
    fixed = run(project, "-h")
    assert fixed.returncode == 0, fixed.stderr


# 2. add_subcommand -------------------------------------------------------------------

LIST_ALLELES = (
    '        self.parser_list_alleles = self.add_subcommand("list-alleles", help="List the alleles.")\n'
    '        self.parser_list_alleles.add_argument("--species", "-s", default="human", group="filters")\n'
)

BRANCH = """    elif args.subcommand == 'list-alleles':
        print(f"alleles of {args.species}")

"""


def add_branch(project, branch=BRANCH, script="run_demo.py"):
    run_script = project / "src" / script
    marker = "    elif args.subcommand == 'preprocess':"
    text = run_script.read_text()
    assert marker in text
    run_script.write_text(text.replace(marker, branch + marker, 1))


def test_add_subcommand_adds_a_listed_subcommand_that_runs(project):
    add_branch(project)

    proc, _ = run_with(project, LIST_ALLELES, "list-alleles", "-s", "mouse")
    shown = run(project, "-h")
    own = run(project, "list-alleles", "-h")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "alleles of mouse\n"
    assert re.search(r"^    list-alleles +List the alleles\.$", shown.stdout, re.M), shown.stdout
    # Its add_argument takes group=, as predict's does.
    assert "\nfilters:\n" in own.stdout


@pytest.mark.parametrize(
    "code, message",
    [
        (
            'self.add_subcommand("preprocess", help="x")',
            "self.add_subcommand('preprocess'): preprocess is a subcommand of the framework; change its options "
            "with self.parser_preprocess, or pick another name",
        ),
        (
            'self.add_subcommand("x", hepl="x")',
            "self.add_subcommand() got an unexpected keyword argument 'hepl'; did you mean help=?",
        ),
        (
            "self.add_subparsers()",
            "add_subparsers(): the framework has added the subcommands already (self.subparser); add one of your "
            "own with self.add_subcommand(name, help=...)",
        ),
        (
            'self.subparsers.add_parser("x")',
            "self.subparsers does not exist: add a subcommand of your own with self.add_subcommand(name, help=...)",
        ),
    ],
    ids=["taken", "keyword", "add_subparsers", "subparsers"],
)
def test_subcommand_setup_mistakes_are_one_line_naming_add_subcommand(project, code, message):
    proc, line = run_with(project, f"        {code}\n", "-h")

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {message}\n"


def test_add_subparsers_message_is_no_command_line_error(project):
    proc, _ = run_with(project, "        self.add_subparsers()\n", "-h")

    assert "usage:" not in proc.stderr
    assert "cannot have multiple subparser arguments" not in proc.stderr


def test_generated_parser_shows_add_subcommand_and_its_example_works(project):
    parser_file = project / "src" / "DemoArgumentParser.py"
    example = re.search(r"^ *# +(self\.parser_\w+ = self\.add_subcommand\(.*\))$", parser_file.read_text(), re.M)
    assert example, "the generated parser has no add_subcommand example"

    proc, _ = run_with(project, f"        {example.group(1)}\n", "-h")

    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^    list-alleles +List the alleles\.$", proc.stdout, re.M), proc.stdout


# 6. A subcommand without a branch in the run script ----------------------------------


def test_subcommand_without_a_branch_exits_1_and_says_so(project):
    proc, _ = run_with(project, LIST_ALLELES, "list-alleles")

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr == "list-alleles has no branch in src/run_demo.py: add one in main()\n"


def test_framework_subcommands_still_reach_their_branch(project):
    # Control: the template's own stubs still run (exit 1 with their own message).
    (project / "in.json").write_text("{}\n")
    predict = run(project, "predict", "-t", "in.tsv")
    preprocess = run(project, "preprocess", "-j", "in.json", "-o", "out")

    assert (predict.returncode, preprocess.returncode) == (1, 1)
    assert "predict is not implemented yet" in predict.stderr
    assert "preprocess is not implemented yet" in preprocess.stderr


def test_example_app_subcommand_without_a_branch_exits_1_and_says_so(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="example"))
    root = in_tmp_dir / "aa-counter"
    parser_file = root / "src" / "AACounterArgumentParser.py"
    # The example's last line has no line break of its own.
    parser_file.write_text(
        parser_file.read_text().rstrip("\n") + '\n        self.add_subcommand("stats", help="Show stats.")\n'
    )

    proc = run(root, "stats", script="src/run_aa_counter.py")
    counted = run(root, "predict", "-t", "src/example.tsv", "-a", "A", script="src/run_aa_counter.py")

    assert proc.returncode == 1
    assert proc.stderr == "stats has no branch in src/run_aa_counter.py: add one in main()\n"
    # Control: predict still runs to the end.
    assert counted.returncode == 0, counted.stderr
    assert counted.stdout.startswith("peptide\tlength\tAAcount(A)\n")
