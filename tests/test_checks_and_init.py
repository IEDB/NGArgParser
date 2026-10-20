"""Mistakes that passed silently, -o given a file name, and two raw tracebacks.

- A check (type=) that returned None for a value gave args.<dest> None, exit 0.
- add_argument("--method", choices=["a", "b"], default="c") was taken, though
  update_arguments refused the same mistake.
- -o res.tsv -f tsv wrote res.tsv.tsv (and -o res.json, res.json.json), and the
  -o help said only "Output prefix". update_arguments("-o", type=str) dropped
  the -o checks.
- super().__init__(description="My tool") gave a TypeError traceback.
- A positional argument in a mutually exclusive group was told it "can't be
  required=True", though no required= was given.

Each test runs inside a freshly generated project.
"""

import os
import re
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli


@pytest.fixture
def project(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    root = in_tmp_dir / "demo"
    (root / "in.tsv").write_text("a\tb\n1\t2\n")
    (root / "in.json").write_text("{}\n")
    (root / "results").mkdir()
    # predict prints what it parsed, in place of the template's "not implemented" stub.
    run_script = root / "src" / "run_demo.py"
    text = run_script.read_text()
    stub = re.search(r"^        sys\.exit\(f\"predict is not implemented yet.*$", text, re.M)
    assert stub
    run_script.write_text(text.replace(stub.group(0), "        print(repr(vars(args)))"))
    return root


def run(project, *argv):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60, env=env
    )


def run_with(project, code, *argv):
    """Append `code` (one line of the generated parser's __init__) and run the run script.
    Returns the process and the number of the parser file's last line."""
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text() + f"        {code}\n")
    line = len(parser_file.read_text().splitlines())
    return run(project, *argv), line


def last_line(proc):
    return proc.stderr.strip().splitlines()[-1]


# 3. A check that returns None ---------------------------------------------------------


def test_check_returning_none_is_refused_when_parsed(project):
    proc, _ = run_with(
        project,
        'self.parser_predict.add_argument("--min", type=lambda v: None)',
        "predict",
        "-t",
        "in.tsv",
        "--min",
        "8",
    )

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert last_line(proc) == (
        "run_demo.py predict: error: argument --min: the check <lambda> returned None for '8': return the value "
        "(or raise ArgumentTypeError)"
    )


def test_check_returning_none_names_the_function(project):
    code = 'self.parser_predict.add_argument("--min", type=validators.validate_nothing)'
    validators = project / "src" / "validators.py"
    validators.write_text(validators.read_text() + "\n\ndef validate_nothing(value):\n    int(value)\n")

    proc, _ = run_with(project, code, "predict", "-t", "in.tsv", "--min", "8")

    assert proc.returncode == 2
    assert "argument --min: the check validate_nothing returned None for '8'" in last_line(proc)


def test_check_returning_none_for_an_empty_value_is_kept(project):
    # Control: an empty value may map to None.
    proc, _ = run_with(
        project,
        'self.parser_predict.add_argument("--min", type=lambda v: None)',
        "predict",
        "-t",
        "in.tsv",
        "--min",
        "",
    )

    assert proc.returncode == 0, proc.stderr
    assert "'min': None" in proc.stdout


# 3. A default outside the choices ------------------------------------------------------


@pytest.mark.parametrize(
    "code, where, shown",
    [
        (
            'self.parser_predict.add_argument("--method", choices=["a", "b"], default="c")',
            "predict argument --method",
            "'c' is not among the choices a, b",
        ),
        (
            'self.predict_input_group.add_argument("--method", choices=["a", "b"], default="c")',
            "predict argument --method",
            "'c' is not among the choices a, b",
        ),
        (
            'self.parser_predict.add_argument("--method", choices=["a", "b"], default="c", group="speed")',
            "predict argument --method",
            "'c' is not among the choices a, b",
        ),
        (
            'self.parser_predict.add_argument("--m", nargs="+", choices=["a", "b"], default=["a", "z"])',
            "predict argument --m",
            "'z' is not among the choices a, b",
        ),
        (
            'self.add_argument("--level", type=int, choices=[1, 2], default=3)',
            "top-level argument --level",
            "3 is not among the choices 1, 2",
        ),
    ],
    ids=["predict", "group", "group=", "list", "top-level"],
)
def test_add_argument_default_outside_the_choices_is_one_line(project, code, where, shown):
    proc, line = run_with(project, code, "-h")

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: {where}: default {shown}: give a default from the choices, "
        "or add it to them\n"
    )


@pytest.mark.parametrize(
    "code, parsed",
    [
        # argparse converts a text default with type= before it is used.
        ('self.parser_predict.add_argument("--level", type=int, choices=[1, 2], default="2")', "'level': 2"),
        ('self.parser_predict.add_argument("--method", choices=["a", "b"], default=None)', "'method': None"),
        ('self.parser_predict.add_argument("--method", choices=["a", "b"], default="b")', "'method': 'b'"),
    ],
    ids=["text default with type", "None", "inside"],
)
def test_add_argument_default_inside_the_choices_still_works(project, code, parsed):
    proc, _ = run_with(project, code, "predict", "-t", "in.tsv")

    assert proc.returncode == 0, proc.stderr
    assert parsed in proc.stdout


def test_update_arguments_default_outside_the_choices_is_one_line(project):
    code = (
        'self.parser_predict.add_argument("--method", choices=["a", "b"], default="a"); '
        'self.parser_predict.update_arguments("--method", default="c")'
    )

    proc, line = run_with(project, code, "-h")

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: update_arguments can't set default of --method: 'c' is not among "
        "the choices a, b: give a default from the choices, or pass choices= too\n"
    )


# 5. -o takes a prefix ------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv, given, use",
    [
        (["predict", "-t", "in.tsv", "-f", "tsv"], "res.tsv", "res"),
        (["predict", "-t", "in.tsv", "-f", "json"], "res.json", "res"),
        (["predict", "-t", "in.tsv"], "out/Res.JSON", "out/Res"),
        (["postprocess", "-i", "results"], "res.json", "res"),
        (["postprocess", "-i", "results", "-f", "tsv"], "out/.tsv", "out/result"),
    ],
    ids=["predict tsv", "predict json", "folder and case", "postprocess", "no name"],
)
def test_o_refuses_a_name_with_an_extension(project, argv, given, use):
    proc = run(project, *argv, "-o", given)
    ending = given[-4:] if given.lower().endswith(".tsv") else given[-5:]

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert last_line(proc).endswith(
        f"error: argument --output-prefix/-o: '{given}' ends in {ending}; -o takes a prefix, and the extension "
        f"is added: use -o {use}"
    )
    assert not any(project.rglob("*.tsv.tsv")) and not any(project.rglob("*.json.json"))


def test_o_still_takes_a_prefix_with_a_dot(project):
    # Control: a dot elsewhere, or another extension, is part of the prefix.
    proc = run(project, "predict", "-t", "in.tsv", "-o", "result.0")
    other = run(project, "predict", "-t", "in.tsv", "-o", "res.csv")

    assert proc.returncode == 0, proc.stderr
    assert "'output_prefix': 'result.0'" in proc.stdout
    assert other.returncode == 0, other.stderr


@pytest.mark.parametrize("subcommand", ["predict", "postprocess"])
def test_o_help_says_the_extension_is_added(project, subcommand):
    proc = run(project, subcommand, "-h")

    assert proc.returncode == 0, proc.stderr
    assert "the extension (.tsv or .json) is added." in " ".join(proc.stdout.split())


@pytest.mark.parametrize("subcommand", ["predict", "postprocess"])
def test_update_arguments_type_keeps_the_o_checks(project, subcommand):
    argv = ["predict", "-t", "in.tsv"] if subcommand == "predict" else ["postprocess", "-i", "results"]
    code = f'self.parser_{subcommand}.update_arguments("--output-prefix", type=str)'

    proc, _ = run_with(project, code, *argv, "-o", "res.tsv")
    folder = run(project, *argv, "-o", "results/")

    assert proc.returncode == 2
    assert "'res.tsv' ends in .tsv; -o takes a prefix" in last_line(proc)
    assert folder.returncode == 2
    assert "'results/' is a folder" in last_line(folder)


def test_update_arguments_type_still_runs_your_own_check(project):
    proc, _ = run_with(
        project, 'self.parser_predict.update_arguments("-o", type=str.upper)', "predict", "-t", "in.tsv", "-o", "res"
    )

    assert proc.returncode == 0, proc.stderr
    assert "'output_prefix': 'RES'" in proc.stdout


# 8. super().__init__(description=...) and a positional in an exclusive group -----------


def set_init(project, arguments):
    parser_file = project / "src" / "DemoArgumentParser.py"
    text = parser_file.read_text()
    assert "        super().__init__()\n" in text
    # The template's own self.description = ... would replace the one given here.
    text = re.sub(r"        self\.description = textwrap\.dedent\(\n.*?\n        \)\n", "", text, flags=re.S)
    parser_file.write_text(text.replace("        super().__init__()\n", f"        super().__init__({arguments})\n"))
    return text[: text.index("super().__init__()")].count("\n") + 1


def test_init_takes_description_and_epilog(project):
    set_init(project, 'description="My tool.", epilog="Ask us."')

    proc = run(project, "-h")

    assert proc.returncode == 0, proc.stderr
    assert "\n\nMy tool.\n\n" in proc.stdout
    assert proc.stdout.endswith("\nAsk us.\n")


def test_init_keeps_the_framework_epilog_without_one(project):
    set_init(project, 'description="My tool."')

    proc = run(project, "-h")

    assert proc.returncode == 0, proc.stderr
    assert "IEDB Discussion Forum" in proc.stdout


@pytest.mark.parametrize(
    "arguments, message",
    [
        (
            'descripton="My tool."',
            "super().__init__() got an unexpected keyword argument 'descripton'; did you mean description=?",
        ),
        (
            '"My tool."',
            "DemoArgumentParser: super().__init__() takes keywords only, such as "
            'super().__init__(description="My tool")',
        ),
    ],
    ids=["misspelled", "positional"],
)
def test_init_mistakes_are_one_line(project, arguments, message):
    line = set_init(project, arguments)

    proc = run(project, "-h")

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {message}\n"


def test_positional_in_an_exclusive_group_names_the_real_reason(project):
    code = 'self.parser_predict.add_mutually_exclusive_group().add_argument("allele")'

    proc, line = run_with(project, code, "-h")

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: predict argument allele: a positional argument can't be in a mutually "
        "exclusive group unless it has nargs='?': make it an option, such as --allele, or add it outside the group\n"
    )


def test_required_option_in_an_exclusive_group_keeps_its_message(project):
    # Control: an option given required=True is told about required=.
    code = 'self.parser_predict.add_mutually_exclusive_group().add_argument("--allele", required=True)'

    proc, _ = run_with(project, code, "-h")

    assert proc.returncode == 2
    assert "an option of a mutually exclusive group can't be required=True" in proc.stderr
