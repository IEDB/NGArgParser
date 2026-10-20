"""Parser fixes: setup mistakes stop at setup, typos get a hint, -o is checked.

Each test runs the generated run script in a copy of one freshly generated project.

Setup (one line naming the file:line, exit 2, as for any parser setup mistake):
type=bool (--keep-ties False gave True), a lone % in help= (-h stopped with a
TypeError traceback), choices that are not text without type= (every value was
an invalid choice), update_argument and self.update_arguments (AttributeError
tracebacks), and a misspelled method of self.parser_predict.

Command line (one usage line, exit 2): an unrecognized option is reported before
a required argument left out, with did-you-mean or where it goes; a mistyped
subcommand gets did-you-mean; the subcommand's usage shows for its options; -o
may not name an input, nor a folder that can't be written.

Also: an alias reaches the run script as its subcommand's name, --version of a
local build shows the pyproject version, and a multi-line predict description
keeps its line breaks.
"""

import os
import re
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
    (project / "peptides.tsv").write_text("a\tb\n1\t2\n")
    (project / "x.json").write_text("{}\n")
    return project


@pytest.fixture
def project(template, tmp_path):
    copy = Path(shutil.copytree(template, tmp_path / "demo", symlinks=True))
    # .env names the folder the tool was configured in, which a copy has to say too.
    env_file = copy / ".env"
    if env_file.exists():
        env_file.write_text(env_file.read_text().replace(str(template), str(copy)))
    return copy


def run(project, *argv):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60, env=env
    )


def add_to_parser(project, code):
    """Append `code` to the end of DemoArgumentParser.__init__."""
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text() + "".join(f"        {line}\n" for line in code.splitlines()))


def setup_error(proc):
    """The one line a parser setup mistake prints, without its file:line."""
    lines = proc.stderr.strip().splitlines()
    assert (proc.returncode, len(lines)) == (2, 1), proc.stderr
    found = re.match(r"src/DemoArgumentParser\.py:\d+: (.*)$", lines[0])
    assert found, lines[0]
    return found.group(1)


def last_line(proc):
    return proc.stderr.strip().splitlines()[-1]


def parsed(proc):
    """True when the command line parsed: the template's predict branch then stops."""
    return proc.returncode == 1 and "predict is not implemented yet" in proc.stderr


# 1. type=bool


def test_type_bool_is_refused_at_setup(project):
    add_to_parser(project, 'self.parser_predict.add_argument("--keep-ties", type=bool, default=False)')

    message = setup_error(run(project, "predict", "-t", "peptides.tsv", "--keep-ties", "False"))

    assert message.startswith("predict argument --keep-ties: type=bool makes every value given true")
    assert 'use action="store_true" for a flag' in message


def test_type_bool_given_to_update_arguments_is_refused(project):
    add_to_parser(
        project,
        'self.parser_predict.add_argument("--keep-ties", default=False)\n'
        'self.parser_predict.update_arguments("--keep-ties", type=bool)',
    )

    assert "type=bool makes every value given true" in setup_error(run(project, "predict", "-h"))


def test_store_true_flag_still_parses(project):
    # Control: the fix the message names.
    add_to_parser(project, 'self.parser_predict.add_argument("--keep-ties", action="store_true")')

    assert parsed(run(project, "predict", "-t", "peptides.tsv", "--keep-ties"))


# 5. % in help=


@pytest.mark.parametrize("text", ["top 10% of peptides", "default %(defualt)s"], ids=["lone", "misspelled"])
def test_help_that_dash_h_cannot_show_is_refused_at_setup(project, text):
    add_to_parser(project, f'self.parser_predict.add_argument("--top", help="{text}")')

    message = setup_error(run(project, "predict", "-h"))

    assert message.startswith("predict argument --top: -h can't show help=")
    assert message.endswith("use %% for a literal %")


def test_help_with_double_percent_and_named_values_still_shows(project):
    # Control: %% and %(default)s are what argparse expands.
    add_to_parser(
        project, 'self.parser_predict.add_argument("--top", type=int, default=10, help="top %(default)s%% only")'
    )

    proc = run(project, "predict", "-h")

    assert proc.returncode == 0, proc.stderr
    assert "top 10% only" in proc.stdout


def test_help_update_that_dash_h_cannot_show_is_refused(project):
    add_to_parser(project, 'self.parser_predict.update_arguments("-o", help="top 10% of peptides")')

    assert setup_error(run(project, "predict", "-h")).endswith("use %% for a literal %")


# 6. choices that are not text need a type=


def test_choices_that_are_not_text_without_type_are_refused(project):
    add_to_parser(project, 'self.parser_predict.add_argument("--length", choices=[8, 9, 10], default=9)')

    message = setup_error(run(project, "predict", "-t", "peptides.tsv", "--length", "8"))

    assert message == (
        "predict argument --length: choices 8, 9, 10 are not text, and without type= every value given is "
        "text, so none would match: add type=int"
    )


def test_choices_with_a_type_still_parse(project):
    # Control.
    add_to_parser(project, 'self.parser_predict.add_argument("--length", type=int, choices=[8, 9, 10], default=9)')

    assert parsed(run(project, "predict", "-t", "peptides.tsv", "--length", "8"))


# 7. update_argument, self.update_arguments, a misspelled method


def test_update_argument_singular_changes_the_option(project):
    add_to_parser(project, 'self.parser_predict.update_argument("-o", help="Where the results go.")')

    proc = run(project, "predict", "-h")

    assert proc.returncode == 0, proc.stderr
    assert "Where the results go." in proc.stdout


def test_top_level_update_arguments_takes_the_subcommand(project):
    add_to_parser(project, 'self.update_arguments("--output-format", "predict", default="json")')

    proc = run(project, "predict", "-h")

    assert proc.returncode == 0, proc.stderr
    assert "default: json" in proc.stdout


def test_top_level_update_arguments_finds_the_only_subcommand(project):
    add_to_parser(project, 'self.update_arguments("--input-tsv", help="Peptides, one per row.")')

    proc = run(project, "predict", "-h")

    assert proc.returncode == 0, proc.stderr
    assert "Peptides, one per row." in proc.stdout


def test_top_level_update_arguments_says_which_when_two_have_it(project):
    add_to_parser(project, 'self.update_arguments("-o", help="x")')

    message = setup_error(run(project, "predict", "-h"))

    assert message.startswith("update_arguments('-o'): predict, preprocess and postprocess each have -o; say which:")


def test_misspelled_parser_method_is_one_line(project):
    add_to_parser(project, 'self.parser_predict.update_argumens("-o", help="x")')

    assert setup_error(run(project, "predict", "-h")) == (
        "self.parser_predict.update_argumens does not exist; did you mean update_arguments?"
    )


# 3. subcommand aliases


def test_alias_reaches_the_run_script_as_the_subcommand_name(project):
    add_to_parser(project, 'self.add_subcommand("list-alleles", help="List the alleles.", aliases=["la"])')

    proc = run(project, "la")

    assert proc.returncode == 1
    assert "list-alleles has no branch in src/run_demo.py" in proc.stderr


# 4. an unrecognized option comes before a required argument left out


@pytest.mark.parametrize(
    "argv, line",
    [
        (
            ["predict", "--input_json", "x.json"],
            "run_demo.py predict: error: unrecognized arguments: --input_json; did you mean --input-json?",
        ),
        (
            ["predict", "--input-jsn", "x.json"],
            "run_demo.py predict: error: unrecognized arguments: --input-jsn; did you mean --input-json?",
        ),
        (["predict", "-J", "x.json"], "run_demo.py predict: error: unrecognized arguments: -J; did you mean -j?"),
        (
            ["-j", "x.json", "predict"],
            "run_demo.py: error: unrecognized arguments: -j; -j is an option of predict; put it after 'predict'",
        ),
        (
            ["-j", "predict"],
            "run_demo.py predict: error: unrecognized arguments: -j; -j is an option of predict; put it after 'predict'",
        ),
        (["--versoin"], "run_demo.py: error: unrecognized arguments: --versoin; did you mean --version?"),
    ],
    ids=["underscore", "typo", "case", "before-with-value", "before", "top-level"],
)
def test_unrecognized_option_is_reported_first(project, argv, line):
    proc = run(project, *argv)

    assert proc.returncode == 2
    assert last_line(proc) == line


def test_required_input_left_out_keeps_the_argparse_error(project):
    # Control: with no unrecognized option, argparse's message stays.
    proc = run(project, "predict")

    assert proc.returncode == 2
    assert last_line(proc) == (
        "run_demo.py predict: error: one of the arguments --input-tsv/-t --input-json/-j is required"
    )


# 9. a mistyped subcommand


@pytest.mark.parametrize("typed", ["predcit", "Predict"])
def test_mistyped_subcommand_gets_did_you_mean(project, typed):
    proc = run(project, typed, "-t", "peptides.tsv")

    assert proc.returncode == 2
    assert last_line(proc).startswith(f"run_demo.py: error: argument subcommand: invalid choice: '{typed}'")
    assert last_line(proc).endswith("; did you mean 'predict'?")


def test_unknown_subcommand_close_to_none_gets_no_hint(project):
    # Control.
    proc = run(project, "bogus")

    assert proc.returncode == 2
    assert "invalid choice: 'bogus'" in last_line(proc)
    assert "did you mean" not in last_line(proc)


# 10. the subcommand's usage for its options


def test_error_about_a_subcommand_option_shows_its_usage(project):
    proc = run(project, "predict", "-t", "peptides.tsv", "--bogus", "3")

    assert proc.returncode == 2
    assert proc.stderr.startswith("usage: run_demo.py predict [-h]")
    assert last_line(proc) == "run_demo.py predict: error: unrecognized arguments: --bogus 3"


# 2. -o naming an input


@pytest.mark.parametrize(
    "argv, written, flags",
    [
        (["predict", "-t", "peptides.tsv", "-o", "peptides"], "peptides.tsv", "--input-tsv/-t"),
        (["predict", "-t", "peptides.tsv", "-o", "./peptides"], "./peptides.tsv", "--input-tsv/-t"),
        (["predict", "-j", "x.json", "-o", "x", "-f", "json"], "x.json", "--input-json/-j"),
        (["postprocess", "-j", "x.json", "-o", "x"], "x.json", "--job-desc-file/-j"),
    ],
    ids=["tsv", "dot-slash", "json", "postprocess"],
)
def test_output_that_names_an_input_is_refused(project, argv, written, flags):
    before = {name: (project / name).read_text() for name in ("peptides.tsv", "x.json")}

    proc = run(project, *argv)

    prefix = argv[argv.index("-o") + 1]
    assert proc.returncode == 2
    assert last_line(proc) == (
        f"run_demo.py {argv[0]}: error: argument --output-prefix/-o: -o {prefix} would write {written} over "
        f"the input given to {flags}; pick another prefix"
    )
    assert {name: (project / name).read_text() for name in before} == before


def test_output_beside_the_input_still_parses(project):
    # Control.
    assert parsed(run(project, "predict", "-t", "peptides.tsv", "-o", "peptides-result"))


# 8. -o in a folder that can't be written


@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0, reason="root writes in any folder")
def test_output_in_a_folder_you_cannot_write_is_refused(project):
    locked = project / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        proc = run(project, "predict", "-t", "peptides.tsv", "-o", "locked/sub/result")
    finally:
        locked.chmod(0o755)

    assert proc.returncode == 2
    assert last_line(proc) == (
        "run_demo.py predict: error: argument --output-prefix/-o: can't write in the folder 'locked' "
        "(permission denied); give -o a prefix in a folder you can write"
    )


def test_output_under_a_file_is_refused(project):
    proc = run(project, "predict", "-t", "peptides.tsv", "-o", "peptides.tsv/result")

    assert proc.returncode == 2
    assert last_line(proc) == (
        "run_demo.py predict: error: argument --output-prefix/-o: 'peptides.tsv' is a file, not a folder, "
        "so -o peptides.tsv/result can't write there"
    )


def test_output_in_a_missing_folder_still_parses_and_creates_nothing(project):
    # Control: write_results creates the folders, so parsing leaves them alone.
    assert parsed(run(project, "predict", "-t", "peptides.tsv", "-o", "new/sub/result"))
    assert not (project / "new").exists()


# 11. --version of a local build


def test_local_build_version_shows_the_project_version(project):
    (project / "VERSION").write_text("local\nMon Oct 19 10:00:00 UTC 2026\n")

    proc = run(project, "--version")

    assert (proc.returncode, proc.stdout) == (0, "run_demo.py 0.1.0 (local build)\n")


def test_release_build_version_is_unchanged(project):
    # Control.
    (project / "VERSION").write_text("2.3.0\nMon Oct 19 10:00:00 UTC 2026\n")

    assert run(project, "--version").stdout == "run_demo.py 2.3.0\n"


# 12. a multi-line predict description


def test_multi_line_predict_description_keeps_its_line_breaks(project):
    parser_file = project / "src" / "DemoArgumentParser.py"
    text = parser_file.read_text()
    assert text.count("description=''") == 1
    parser_file.write_text(text.replace("description=''", "description='First line.\\nSecond line.'"))

    proc = run(project, "predict", "-h")

    assert proc.returncode == 0, proc.stderr
    assert "\n\nFirst line.\nSecond line.\n" in proc.stdout


def test_framework_descriptions_still_fit_the_screen(project):
    for subcommand in ("preprocess", "postprocess"):
        proc = run(project, subcommand, "-h")

        assert proc.returncode == 0, proc.stderr
        description = proc.stdout.split("\n\n")[1]
        assert description.splitlines() and all(len(line) <= 80 for line in description.splitlines())
