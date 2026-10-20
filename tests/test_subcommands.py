"""A subcommand of the tool's own, added with self.subparser.add_parser(...).

- set_subcommand_order gave argparse a new map of subcommands, while add_parser
  kept writing to the old one: a subcommand added after the call showed in -h,
  and running it gave "invalid choice". So did predict, when the call came
  before add_predict_subparser().
- Its add_argument was argparse's: group= and a misspelled keyword stopped the
  tool with a traceback (exit 1), where predict takes group= and reports a
  misspelling in one line (exit 2).
- A name already taken (preprocess, or one of the tool's own twice) replaced
  the parser on Python 3.9 and 3.10, and gave a traceback on later ones.

Each test runs inside a freshly generated project.
"""

import re
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

LIST_ALLELES = """
        self.parser_list_alleles = self.subparser.add_parser("list-alleles", help="List alleles.")
        self.parser_list_alleles.add_argument("--species", "-s", default="human", help="Species.")
"""

BRANCH = """    elif args.subcommand == 'list-alleles':
        print(f"alleles of {args.species}")

"""


@pytest.fixture
def project(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    root = in_tmp_dir / "demo"
    (root / "in.tsv").write_text("a\tb\n1\t2\n")
    # The run script's branch for list-alleles, as the tool writes it.
    run_script = root / "src" / "run_demo.py"
    text = run_script.read_text()
    marker = "    elif args.subcommand == 'preprocess':"
    assert marker in text
    run_script.write_text(text.replace(marker, BRANCH + marker, 1))
    return root


def run_with(project, code, *argv):
    """Append `code` to the generated parser's __init__ and run the run script.
    Returns the process and the number of the parser file's last line."""
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text() + code)
    line = len(parser_file.read_text().splitlines())
    proc = subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60
    )
    return proc, line


def listed(help_text):
    """The subcommands -h lists, in order."""
    return re.findall(r"^    (\S+) {2,}\S", help_text, re.M)


# set_subcommand_order, then a subcommand of the tool's own ---------------------


def test_subcommand_added_after_set_subcommand_order_runs(project):
    # The generated parser calls set_subcommand_order before this code runs.
    proc, _ = run_with(project, LIST_ALLELES, "list-alleles", "-s", "mouse")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "alleles of mouse\n"


def test_subcommand_added_after_set_subcommand_order_is_listed_last(project):
    proc, _ = run_with(project, LIST_ALLELES, "--help")

    assert proc.returncode == 0, proc.stderr
    assert listed(proc.stdout) == ["predict", "preprocess", "postprocess", "list-alleles"]
    assert "{predict,preprocess,postprocess,list-alleles}" in proc.stdout


def test_set_subcommand_order_puts_the_unnamed_ones_after_in_the_order_added(project):
    code = LIST_ALLELES + '        self.set_subcommand_order(["postprocess", "list-alleles"])\n'

    proc, _ = run_with(project, code, "--help")
    ran, _ = run_with(project, "", "list-alleles")

    assert proc.returncode == 0, proc.stderr
    assert listed(proc.stdout) == ["postprocess", "list-alleles", "predict", "preprocess"]
    assert "{postprocess,list-alleles,predict,preprocess}" in proc.stdout
    assert (ran.returncode, ran.stdout) == (0, "alleles of human\n"), ran.stderr


def test_predict_added_after_set_subcommand_order_runs(project):
    parser_file = project / "src" / "DemoArgumentParser.py"
    text = parser_file.read_text()
    order = "        self.set_subcommand_order(['predict', 'preprocess', 'postprocess'])\n"
    add_predict = "        self.parser_predict = self.add_predict_subparser("
    assert order in text and add_predict in text
    text = text.replace(order, "").replace(
        add_predict, "        self.set_subcommand_order(['preprocess', 'postprocess'])\n" + add_predict
    )
    parser_file.write_text(text)

    proc, _ = run_with(project, "", "predict", "-t", "in.tsv")
    shown, _ = run_with(project, "", "--help")

    # The template's predict stub ran (exit 1, its own message), not argparse's exit 2.
    assert proc.returncode == 1, proc.stderr
    assert "predict is not implemented yet" in proc.stderr
    assert listed(shown.stdout) == ["preprocess", "postprocess", "predict"]


# Its add_argument works as predict's -------------------------------------------


def test_subcommand_of_your_own_takes_group(project):
    code = LIST_ALLELES + '        self.parser_list_alleles.add_argument("--min-length", group="filter options")\n'

    proc, _ = run_with(project, code, "list-alleles", "-h")

    assert proc.returncode == 0, proc.stderr
    assert "--min-length" in proc.stdout.split("filter options:")[1]


@pytest.mark.parametrize(
    "code, message",
    [
        (
            '        self.parser_list_alleles.add_argument("--min-length", hepl="Shortest.")\n',
            "list-alleles argument --min-length: add_argument() got an unexpected keyword argument 'hepl'; "
            "did you mean help=?",
        ),
        (
            '        self.parser_list_alleles.add_argument("--min-length", group="Filter option")\n'
            '        self.parser_list_alleles.add_argument("--max-length", group="filter option")\n',
            'list-alleles argument --max-length: group="filter option" would add a section next to '
            "'Filter option'; did you mean 'Filter option'?",
        ),
        (
            '        self.parser_list_alleles.add_argument_group("g").add_argument("--x", group="other")\n',
            "list-alleles argument --x: 'g' is already a group; drop group=, or use the list-alleles parser's "
            'add_argument(..., group="other")',
        ),
        (
            '        self.parser_list_alleles.add_argument("--x", dest="subcommand")\n',
            "list-alleles argument --x: dest subcommand belongs to the framework (it records which subcommand "
            "ran); pick another dest",
        ),
    ],
    ids=["misspelled keyword", "near title", "group in a group", "dest subcommand"],
)
def test_setup_mistake_in_your_subcommand_is_one_line(project, code, message):
    proc, line = run_with(project, LIST_ALLELES + code, "--help")

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {message}\n"


# A name already taken ------------------------------------------------------------


@pytest.mark.parametrize(
    "code, message",
    [
        (
            '        self.subparser.add_parser("preprocess", help="Mine.")\n',
            "self.subparser.add_parser('preprocess'): preprocess is a subcommand of the framework; change its "
            "options with self.parser_preprocess, or pick another name",
        ),
        (
            '        self.subparser.add_parser("merge", aliases=["postprocess"])\n',
            "self.subparser.add_parser('merge'): postprocess is a subcommand of the framework; change its "
            "options with self.parser_postprocess, or pick another name",
        ),
        (
            '        self.subparser.add_parser("predict")\n',
            "self.subparser.add_parser('predict'): predict is a subcommand of the framework; "
            "self.add_predict_subparser(...) adds it, or pick another name",
        ),
        (
            LIST_ALLELES + '        self.subparser.add_parser("list-alleles")\n',
            "self.subparser.add_parser('list-alleles'): subcommand list-alleles exists already; add it once, "
            "or pick another name",
        ),
    ],
    ids=["preprocess", "alias postprocess", "predict", "twice"],
)
def test_subcommand_name_taken_is_one_line(project, code, message):
    proc, line = run_with(project, code, "--help")

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {message}\n"


def test_control_the_framework_subcommands_are_unchanged(project):
    # Control: the three framework subcommands still parse and list as before.
    proc, _ = run_with(project, "", "--help")
    pre, _ = run_with(project, "", "preprocess", "-h")

    assert proc.returncode == 0, proc.stderr
    assert listed(proc.stdout) == ["predict", "preprocess", "postprocess"]
    assert pre.returncode == 0, pre.stderr
    assert "required parameters:" in pre.stdout
