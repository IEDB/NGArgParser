"""Framework options parse_args reads, an empty -o, the group= suggestion, and a
dependency that can't run.

- remove_argument("--params-dir") on preprocess (and --inputs-dir, and postprocess
  --job-desc-file or --input-results-dir) was taken, then every command that
  reached parse_args failed with "AttributeError: 'Namespace' object has no
  attribute 'preprocess_parameters_dir'".
- -o "" passed the -o check, and write_results wrote the hidden file .tsv (or .json).
- On a group's own add_argument, group="output option" got the advice to use
  group="output option" on the subcommand, which then refused it as a typo.
- dependency_command called before ./configure ended in a 7-line traceback.

Each test runs inside a freshly generated project.
"""

import json
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PRELUDE = """\
import argparse, contextlib, io, json, sys
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
    (root / "in.json").write_text("{}\n")
    (root / "results").mkdir()
    return root


def probe(project, body):
    script = project / "probe_r8_w4_followup.py"
    script.write_text(PRELUDE + body)
    proc = subprocess.run([sys.executable, str(script)], cwd=project, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def at(body_line):
    """The file:line a parser-setup error names for line `body_line` (from 1) of a probe body."""
    return f"probe_r8_w4_followup.py:{PRELUDE.count(chr(10)) + body_line}"


def run_with(project, declaration, *argv):
    """Append `declaration` (one line) to the generated parser's __init__ and run the run script."""
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text() + f"        {declaration}\n")
    line = len(parser_file.read_text().splitlines())
    return run(project, "run_demo.py", *argv), line


def run(project, script, *argv):
    return subprocess.run(
        [sys.executable, f"src/{script}", *argv], cwd=project, capture_output=True, text=True, timeout=60
    )


# remove_argument refuses an option parse_args reads --------------------------

READ_BY_PARSE_ARGS = {
    "params dir": ("preprocess", "--params-dir", "--params-dir", "preprocess_parameters_dir"),
    "inputs dir": ("preprocess", "--inputs-dir", "--inputs-dir", "preprocess_inputs_dir"),
    "job description": ("postprocess", "-j", "--job-desc-file/-j", "job_desc_file"),
    "results dir": ("postprocess", "--input-results-dir", "--input-results-dir/-i", "postprocess_input_dir"),
}


def kept_message(subcommand, names, dest):
    long_name = names.split("/")[0]
    return (
        f"remove_argument can't remove {names} from {subcommand}: the framework reads args.{dest}; change the "
        f'option with self.parser_{subcommand}.update_arguments("{long_name}", ...) instead, or hide it from -h '
        "with help=argparse.SUPPRESS"
    )


@pytest.mark.parametrize("name", READ_BY_PARSE_ARGS)
def test_removing_an_option_parse_args_reads_is_one_line(project, name):
    subcommand, flag, names, dest = READ_BY_PARSE_ARGS[name]

    proc, line = run_with(project, f'self.parser_{subcommand}.remove_argument("{flag}")', "--help")

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {kept_message(subcommand, names, dest)}\n"


@pytest.mark.parametrize("name", READ_BY_PARSE_ARGS)
def test_top_level_remove_argument_refuses_it_too_and_changes_nothing(project, name):
    subcommand, flag, names, dest = READ_BY_PARSE_ARGS[name]
    out = probe(
        project,
        f'err = error_of(lambda: p.remove_argument("{flag}", "{subcommand}"))\n'
        f'print(json.dumps({{"err": err, "help": p.parser_{subcommand}.format_help()}}))',
    )
    assert out["err"] == f"ParserSetupError: {at(1)}: {kept_message(subcommand, names, dest)}"
    assert names.split("/")[0] in out["help"]


def test_a_read_option_keeps_its_names_and_dest(project):
    out = probe(
        project,
        'print(json.dumps([error_of(lambda: p.parser_preprocess.update_arguments("--params-dir", "--params")),\n'
        '                  error_of(lambda: p.parser_postprocess.update_arguments("-j", dest="jd"))]))',
    )
    # The rename and dest messages no longer send you to remove_argument, which refuses these.
    assert out == [
        f"ParserSetupError: {at(1)}: update_arguments can't rename --params-dir to --params-dir/--params: "
        "the framework reads args.preprocess_parameters_dir, so it keeps its names.",
        f"ParserSetupError: {at(2)}: update_arguments can't change dest of --job-desc-file/-j to jd: "
        "the framework reads args.job_desc_file; dest cannot change.",
    ]


def test_control_the_named_update_arguments_route_hides_the_option(project):
    # Control: what the line says to do instead hides --params-dir, and preprocess
    # still fills in its default.
    out = probe(
        project,
        'p.parser_preprocess.update_arguments("--params-dir", help=argparse.SUPPRESS)\n'
        "with contextlib.redirect_stdout(io.StringIO()):\n"
        '    args = p.parse_args(["preprocess", "-j", "in.json", "-o", "job"])\n'
        'print(json.dumps({"help": p.parser_preprocess.format_help(), "params": args.preprocess_parameters_dir}))',
    )
    assert "--params-dir" not in out["help"]
    assert out["params"] == "job/predict-inputs/params"


REMOVABLE = {
    "predict -o": ("predict", "--output-prefix", ["predict", "-t", "in.tsv"]),
    "predict -f": ("predict", "--output-format", ["predict", "-t", "in.tsv"]),
    "preprocess -j": ("preprocess", "--input-json", ["preprocess", "-o", "job"]),
    "preprocess -o": ("preprocess", "--output-dir", ["preprocess", "-j", "in.json"]),
    "preprocess --assume-valid": ("preprocess", "--assume-valid", ["preprocess", "-j", "in.json", "-o", "job"]),
    "postprocess -p": ("postprocess", "--postprocessed-results-dir", ["postprocess", "-j", "in.json"]),
    "postprocess -o": ("postprocess", "--output-prefix", ["postprocess", "-j", "in.json"]),
    "postprocess -f": ("postprocess", "--output-format", ["postprocess", "-j", "in.json"]),
}


@pytest.mark.parametrize("name", REMOVABLE)
def test_control_options_parse_args_does_not_need_can_still_go(project, name):
    # Control: parse_args reads none of these (it checks for args.output_dir
    # first), so removing one still works and the command still parses.
    subcommand, flag, argv = REMOVABLE[name]
    out = probe(
        project,
        f'p.remove_argument("{flag}", "{subcommand}")\n'
        "with contextlib.redirect_stdout(io.StringIO()):\n"
        f"    args = p.parse_args({argv!r})\n"
        "print(json.dumps(args.subcommand))",
    )
    assert out == subcommand


# -o "" ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["predict", "-t", "in.tsv", "-o", ""],
        ["predict", "-t", "in.tsv", "--output-prefix="],
        ["postprocess", "-j", "in.json", "-o", ""],
    ],
    ids=["predict", "predict =", "postprocess"],
)
def test_empty_output_prefix_is_a_usage_error(project, argv):
    proc = run(project, "run_demo.py", *argv)

    assert proc.returncode == 2
    assert proc.stderr.splitlines()[-1] == (
        f"run_demo.py {argv[0]}: error: argument --output-prefix/-o: the value is empty; "
        "-o takes an output prefix such as 'result'"
    )


def test_empty_output_prefix_writes_no_hidden_file(project):
    # The predict branch of the README: write_results(result, args.output_prefix, ...).
    out = probe(
        project,
        "from core.result_writer import write_results\n"
        "err = io.StringIO()\n"
        "try:\n"
        "    with contextlib.redirect_stderr(err):\n"
        '        args = p.parse_args(["predict", "-t", "in.tsv", "-o", ""])\n'
        '    write_results({"results": []}, args.output_prefix, args.output_format)\n'
        "except SystemExit as e:\n"
        '    print(json.dumps({"code": e.code}))',
    )
    assert out == {"code": 2}
    assert not (project / ".tsv").exists()


# The "already a group" advice names the existing title ------------------------


@pytest.mark.parametrize(
    "label, title",
    [("output option", "output options"), ("Input options", "input options")],
    ids=["another section", "its own section"],
)
def test_already_a_group_suggests_the_title_a_typo_was_meant_for(project, label, title):
    proc, line = run_with(project, f'self.predict_input_group.add_argument("--alleles", group="{label}")', "--help")

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: predict argument --alleles: predict_input_group is already a group; "
        f'drop group=, or use self.parser_predict.add_argument(..., group="{title}")\n'
    )


def test_following_the_already_a_group_advice_works(project):
    proc, _ = run_with(
        project, 'self.parser_predict.add_argument("--alleles", group="output options")', "predict", "-h"
    )

    assert proc.returncode == 0, proc.stderr
    assert "--alleles" in proc.stdout.split("output options:")[1]


def test_control_already_a_group_keeps_a_label_that_is_no_typo(project):
    proc, _ = run_with(project, 'self.predict_input_group.add_argument("--alleles", group="speed")', "--help")

    assert proc.returncode == 2
    assert proc.stderr.endswith('self.parser_predict.add_argument(..., group="speed")\n')


# A dependency that can't run: one line, exit code 1 ---------------------------

CALL_ALPHA = "from core.app_env import dependency_command\ndependency_command('alpha', 'predict')\n"


def with_predict_code(script, marker, code, indent="        "):
    """Insert `code` into the predict branch of entry script `script`, before `marker`."""
    text = script.read_text()
    assert marker in text
    lines = "".join(indent + line + "\n" for line in code.splitlines())
    script.write_text(text.replace(marker, lines + marker, 1))


TEMPLATE_MARKER = '        sys.exit(f"predict is not implemented yet'


def test_dependency_not_configured_is_one_line_from_the_run_script(project):
    (project / "paths.py").write_text('alpha_path = "/nowhere/alpha"\n')
    with_predict_code(project / "src" / "run_demo.py", TEMPLATE_MARKER, CALL_ALPHA)

    proc = run(project, "run_demo.py", "predict", "-t", "in.tsv")

    assert proc.returncode == 1
    assert proc.stderr == "run ./configure: paths.py declares alpha, but .env has no values for it yet\n"


def test_control_another_runtime_error_keeps_its_traceback(project):
    with_predict_code(project / "src" / "run_demo.py", TEMPLATE_MARKER, "raise RuntimeError('a bug in predict')\n")

    proc = run(project, "run_demo.py", "predict", "-t", "in.tsv")

    assert proc.returncode == 1
    assert proc.stderr.startswith("Traceback (most recent call last):")
    assert proc.stderr.endswith("RuntimeError: a bug in predict\n")


@pytest.fixture
def example_project(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="example"))
    return in_tmp_dir / "aa-counter"


EXAMPLE_MARKER = "        # ADD CODE LOGIC HERE.\n        # Unify inputs into a JSON format.\n"


def test_example_app_dependency_not_configured_is_one_line(example_project):
    (example_project / "paths.py").write_text('alpha_path = "/nowhere/alpha"\n')
    with_predict_code(example_project / "src" / "run_aa_counter.py", EXAMPLE_MARKER, CALL_ALPHA)

    proc = run(example_project, "run_aa_counter.py", "predict", "-j", "src/example.json")

    assert proc.returncode == 1
    assert proc.stderr == "run ./configure: paths.py declares alpha, but .env has no values for it yet\n"


def test_control_example_app_runtime_error_keeps_its_traceback(example_project):
    script = example_project / "src" / "run_aa_counter.py"
    with_predict_code(script, EXAMPLE_MARKER, "raise RuntimeError('a bug in predict')\n")

    proc = run(example_project, "run_aa_counter.py", "predict", "-j", "src/example.json")

    assert proc.returncode == 1
    assert proc.stderr.startswith("Traceback (most recent call last):")
    assert proc.stderr.endswith("RuntimeError: a bug in predict\n")
