"""Parser-setup mistakes that passed silently, and a clash line that named one fix of two.

- group="output option" (a typo of the section 'output options') added a second
  section next to it in predict -h, and exited 0.
- set_subcommand_order(["predict", "predict", "postprocess"]) listed predict twice in -h.
- -o was text in predict and a Path in postprocess, so args.output_prefix + ".json"
  worked in one and failed in the other.
- Re-declaring --output-format/-f named only update_arguments, which then refused
  new formats such as csv: the remove_argument route took a second failure to find.

Each test runs inside a freshly generated project.
"""

import json
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


@pytest.fixture
def project(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    root = in_tmp_dir / "demo"
    (root / "in.tsv").write_text("a\tb\n1\t2\n")
    (root / "jd.json").write_text("[]\n")
    return root


def probe(project, body):
    script = project / "probe_r8_w4.py"
    script.write_text(PRELUDE + body)
    proc = subprocess.run([sys.executable, str(script)], cwd=project, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def at(body_line):
    """The file:line a parser-setup error names for line `body_line` (from 1) of a probe body."""
    return f"probe_r8_w4.py:{PRELUDE.count(chr(10)) + body_line}"


def run_with(project, declaration, *argv):
    """Append `declaration` (one line) to the generated parser's __init__ and run the run script."""
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text() + f"        {declaration}\n")
    line = len(parser_file.read_text().splitlines())
    proc = subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60
    )
    return proc, line


def sections(help_text):
    """The section titles of a help screen, in order."""
    return re.findall(r"^(\S[^\n]*):$", help_text, re.M)


# group= a typo of a section title --------------------------------------------

NEAR_TITLES = {
    "one s short": ("predict", "--beta", "output option", "output options"),
    "case and spaces": ("predict", "--beta", " Output Options ", "output options"),
    "input section": ("predict", "--beta", "input option", "input options"),
    "postprocess": ("postprocess", "--beta", "Optional Parameter", "optional parameters"),
    "preprocess": ("preprocess", "--beta", "required parameter", "required parameters"),
}


@pytest.mark.parametrize("name", NEAR_TITLES)
@pytest.mark.parametrize("argv", [["--help"], ["predict", "-t", "in.tsv"]], ids=["help", "predict"])
def test_group_typo_of_a_section_title_is_one_line(project, name, argv):
    subcommand, flag, label, title = NEAR_TITLES[name]

    proc, line = run_with(project, f'self.parser_{subcommand}.add_argument("{flag}", group="{label}")', *argv)

    assert proc.returncode == 2
    assert proc.stderr == (
        f'src/DemoArgumentParser.py:{line}: {subcommand} argument {flag}: group="{label}" would add a section '
        f"next to '{title}'; did you mean '{title}'?\n"
    )


def test_group_typo_of_a_section_the_tool_made_is_refused(project):
    out = probe(
        project,
        'p.parser_predict.add_argument("--alpha", group="My options")\n'
        'print(json.dumps(error_of(lambda: p.parser_predict.add_argument("--beta", group="my option"))))',
    )
    assert out == (
        f'ParserSetupError: {at(2)}: predict argument --beta: group="my option" would add a section '
        "next to 'My options'; did you mean 'My options'?"
    )


def test_update_arguments_group_typo_is_refused_and_changes_nothing(project):
    out = probe(
        project,
        'err = error_of(lambda: p.parser_predict.update_arguments("--output-format", help="NEW", group="Output option"))\n'
        'print(json.dumps({"err": err, "help": p.parser_predict.format_help()}))',
    )
    assert out["err"] == (
        f'ParserSetupError: {at(1)}: predict argument --output-format/-f: group="Output option" would add a '
        "section next to 'output options'; did you mean 'output options'?"
    )
    assert "NEW" not in out["help"]
    assert "Output option:" not in sections(out["help"])


@pytest.mark.parametrize("label", ["output optionss", "outputs", "output", "my options"])
def test_control_other_new_labels_still_make_a_section(project, label):
    # Control: a label that differs from every title in more than case, spaces or
    # one trailing s still adds its own section, as before.
    proc, _ = run_with(project, f'self.parser_predict.add_argument("--beta", group="{label}")', "predict", "-h")

    assert proc.returncode == 0, proc.stderr
    assert sections(proc.stdout)[-1:] == [label]


def test_control_the_exact_title_joins_the_section(project):
    proc, _ = run_with(project, 'self.parser_predict.add_argument("--beta", group="output options")', "predict", "-h")

    assert proc.returncode == 0, proc.stderr
    assert sections(proc.stdout).count("output options") == 1
    assert "--beta" in proc.stdout.split("output options:")[1]


# set_subcommand_order with a name twice -----------------------------------


def test_set_subcommand_order_refuses_a_name_listed_twice(project):
    out = probe(
        project,
        'err = error_of(lambda: p.set_subcommand_order(["predict", "predict", "postprocess"]))\n'
        'print(json.dumps({"err": err, "help": p.format_help()}))',
    )
    assert out["err"] == (
        f"ParserSetupError: {at(1)}: set_subcommand_order: subcommand 'predict' is listed twice; "
        "name each subcommand once."
    )
    assert out["help"].count("Run one prediction.") == 1  # the template's order, unchanged


def test_set_subcommand_order_twice_is_one_line_from_the_run_script(project):
    proc, line = run_with(project, 'self.set_subcommand_order(["predict", "predict", "postprocess"])', "--help")

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: set_subcommand_order: subcommand 'predict' is listed twice; "
        "name each subcommand once.\n"
    )


# -o is text in predict and postprocess -------------------------------------


@pytest.mark.parametrize(
    "argv",
    [["predict", "-t", "in.tsv", "-o", "out/result.0"], ["postprocess", "-j", "jd.json", "-o", "out/result.0"]],
    ids=["predict", "postprocess"],
)
def test_output_prefix_is_text_in_both_subcommands(project, argv):
    out = probe(
        project,
        f"args = p.parse_args({argv!r})\n"
        'print(json.dumps({"type": type(args.output_prefix).__name__, "json": args.output_prefix + ".json"}))',
    )
    assert out == {"type": "str", "json": "out/result.0.json"}


def test_postprocess_output_prefix_text_feeds_write_results(project):
    # The framework's writer takes the text: it writes <prefix>.json, creating out/.
    out = probe(
        project,
        "from core.result_writer import write_results\n"
        'args = p.parse_args(["postprocess", "-j", "jd.json", "-o", "out/merged"])\n'
        'envelope = {"results": [{"type": "t", "table_columns": ["a"], "table_data": [[1]]}]}\n'
        "print(json.dumps(write_results(envelope, args.output_prefix, args.output_format)))",
    )
    assert out == ["out/merged.json"]
    assert json.loads((project / "out" / "merged.json").read_text())["results"][0]["table_data"] == [[1]]


# A re-declared framework option: the clash line names remove_argument too ----


def test_redeclared_output_format_names_remove_argument(project):
    proc, line = run_with(
        project, 'self.parser_predict.add_argument("--output-format", "-f", choices=["tsv", "json", "csv"])', "--help"
    )

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: predict argument --output-format/-f: --output-format, -f belong to the "
        "framework (--output-format); pick another flag, change the framework option with "
        'self.parser_predict.update_arguments("--output-format", ...), or replace it with your own: call '
        'self.parser_predict.remove_argument("--output-format") first\n'
    )


def test_dest_clash_names_remove_argument(project):
    proc, line = run_with(project, 'self.parser_postprocess.add_argument("--out", dest="output_prefix")', "--help")

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: postprocess argument --out: dest output_prefix belongs to the framework "
        "(--output-prefix); pick another dest, change the framework option with "
        'self.parser_postprocess.update_arguments("--output-prefix", ...), or replace it with your own: call '
        'self.parser_postprocess.remove_argument("--output-prefix") first\n'
    )


def test_control_the_named_remove_argument_route_works(project):
    # Control: what the line says to do declares -f with csv, and predict takes it.
    out = probe(
        project,
        'p.parser_predict.remove_argument("--output-format")\n'
        'p.parser_predict.add_argument("--output-format", "-f", choices=["tsv", "json", "csv"], default="tsv")\n'
        'print(json.dumps(p.parse_args(["predict", "-t", "in.tsv", "-f", "csv"]).output_format))',
    )
    assert out == "csv"
