"""Gaps left around a subcommand of the tool's own, framework options and the path checks.

- self.subparser.add_parser("list-alleles", hepl="x") (or dest=) stopped the tool
  with a TypeError traceback, exit 1.
- The parser it returned had no update_arguments or remove_argument (AttributeError),
  and setting its .help changed nothing.
- set_subcommand_order(["list-alleles", ...]) before its add_parser call said
  "unknown subcommand ...; use preprocess, postprocess or predict".
- update_arguments("-f", nargs="+") made args.output_format a list, and
  write_results stopped with AttributeError; dest="subcommand" took the run
  script's branch away.
- validate_file said "'src' is not a valid file." for a folder and for a missing
  file alike; validate_directory likewise for a file.
- set_subcommand_order left an alias at the end of the usage braces.

Each parser test runs inside a freshly generated project.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli
from ngargparser import core_validators as cv

LIST_ALLELES = """
        self.parser_list_alleles = self.subparser.add_parser("list-alleles", help="List alleles.")
        self.parser_list_alleles.add_argument("--species", "-s", default="human", help="Species.")
"""

BRANCH = """    elif args.subcommand in ('list-alleles', 'la'):
        print(f"alleles of {args.species}")

"""

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
    (root / "in.json").write_text("{}\n")
    run_script = root / "src" / "run_demo.py"
    marker = "    elif args.subcommand == 'preprocess':"
    assert marker in run_script.read_text()
    run_script.write_text(run_script.read_text().replace(marker, BRANCH + marker, 1))
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


def probe(project, body):
    script = project / "probe_r9_w4b.py"
    script.write_text(PRELUDE + body)
    proc = subprocess.run([sys.executable, str(script)], cwd=project, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def at(body_line):
    """The file:line a parser-setup error names for line `body_line` (from 1) of a probe body."""
    return f"probe_r9_w4b.py:{PRELUDE.count(chr(10)) + body_line}"


def listed(help_text):
    """The subcommands -h lists, in order."""
    return re.findall(r"^    (\S+(?: \([^)]*\))?) {2,}\S", help_text, re.M)


# 1. add_parser keywords -------------------------------------------------------------


@pytest.mark.parametrize(
    "keyword, hint",
    [("hepl", "did you mean help=?"), ("descripton", "did you mean description=?"), ("dest", "it takes ")],
)
def test_add_parser_keyword_mistake_is_one_line(project, keyword, hint):
    proc, line = run_with(project, f'        self.subparser.add_parser("list-alleles", {keyword}="x")\n', "--help")

    assert proc.returncode == 2
    assert proc.stderr.startswith(
        f"src/DemoArgumentParser.py:{line}: self.subparser.add_parser() got an unexpected keyword argument "
        f"'{keyword}'; {hint}"
    )
    assert "Traceback" not in proc.stderr and proc.stderr.count("\n") == 1


def test_add_parser_takes_the_keywords_argparse_takes(project):
    code = '        self.subparser.add_parser("list-alleles", aliases=["la"], description="All.", epilog="End.")\n'

    proc, _ = run_with(project, code, "list-alleles", "-h")

    assert proc.returncode == 0, proc.stderr
    assert "All." in proc.stdout and "End." in proc.stdout


# 2. update_arguments, remove_argument and .help on your own subcommand -----------------


def test_update_and_remove_arguments_work_on_your_own_subcommand(project):
    code = (
        LIST_ALLELES
        + '        self.parser_list_alleles.add_argument("--min-length", type=int, default=8)\n'
        + '        self.parser_list_alleles.update_arguments("--species", default="mouse", help="Which species.")\n'
        + '        self.parser_list_alleles.remove_argument("--min-length")\n'
    )

    shown, _ = run_with(project, code, "list-alleles", "-h")
    ran, _ = run_with(project, "", "list-alleles")

    assert shown.returncode == 0, shown.stderr
    assert "Which species." in shown.stdout and "--min-length" not in shown.stdout
    assert (ran.returncode, ran.stdout) == (0, "alleles of mouse\n"), ran.stderr


def test_remove_argument_by_name_works_on_your_own_subcommand(project):
    code = LIST_ALLELES + '        self.remove_argument("--species", "list-alleles")\n'

    proc, _ = run_with(project, code, "list-alleles", "-h")

    assert proc.returncode == 0, proc.stderr
    assert "--species" not in proc.stdout


def test_remove_argument_unknown_subparser_names_yours(project):
    out = probe(
        project,
        'p.parser_la = p.subparser.add_parser("list-alleles")\n'
        'print(json.dumps(error_of(lambda: p.remove_argument("--species", "list-allele"))))',
    )

    assert out == (
        f"ParserSetupError: {at(2)}: remove_argument: unknown subparser 'list-allele'; "
        "use predict, preprocess, postprocess or list-alleles."
    )


@pytest.mark.parametrize(
    "call, message",
    [
        (
            'q.update_arguments("--species", defualt="mouse")',
            "list-alleles argument --species: update_arguments() got an unexpected keyword argument 'defualt'; "
            "did you mean default=?",
        ),
        (
            'q.remove_argument("--specie")',
            "No option --specie in list-alleles; did you mean --species?",
        ),
        (
            'q.update_arguments("--species", dest="subcommand")',
            "update_arguments can't set dest of --species/-s to subcommand: dest subcommand belongs to the "
            "framework (it records which subcommand ran); pick another dest.",
        ),
    ],
    ids=["update typo", "remove typo", "dest subcommand"],
)
def test_mistakes_on_your_own_subcommand_are_one_line(project, call, message):
    out = probe(
        project,
        'q = p.subparser.add_parser("list-alleles")\n'
        'q.add_argument("--species", "-s")\n'
        f"print(json.dumps(error_of(lambda: {call})))",
    )

    assert out.split(": ", 1)[1] == f"{at(3)}: {message}"


def test_help_changes_the_line_in_the_listing(project):
    code = LIST_ALLELES + '        self.parser_list_alleles.help = "Print the alleles."\n'

    proc, _ = run_with(project, code, "--help")

    assert proc.returncode == 0, proc.stderr
    assert "Print the alleles." in proc.stdout and "List alleles." not in proc.stdout


def test_help_lists_a_subcommand_added_without_help(project):
    code = (
        '        self.parser_a = self.subparser.add_parser("aa", aliases=["a"])\n'
        '        self.parser_b = self.subparser.add_parser("bb", help="B.")\n'
        '        self.parser_a.help = "A."\n'
    )

    proc, _ = run_with(project, code, "--help")

    assert proc.returncode == 0, proc.stderr
    # In the order the subcommands were added, with its alias.
    assert listed(proc.stdout) == ["predict", "preprocess", "postprocess", "aa (a)", "bb"]
    assert re.search(r"^    aa \(a\) +A\.$", proc.stdout, re.M)


def test_control_predict_help_still_changes(project):
    proc, _ = run_with(project, '        self.parser_predict.help = "Predict one."\n', "--help")

    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^    predict +Predict one\.$", proc.stdout, re.M)


# 3. set_subcommand_order before the add_parser call --------------------------------------


def test_set_subcommand_order_before_add_parser_says_not_added_yet(project):
    code = (
        '        self.set_subcommand_order(["predict", "list-alleles", "preprocess", "postprocess"])\n' + LIST_ALLELES
    )

    proc, line = run_with(project, code, "--help")

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line - 3}: set_subcommand_order: subcommand 'list-alleles' is not added "
        "(yet); call set_subcommand_order after self.add_subcommand('list-alleles', ...), or use predict, "
        "preprocess or postprocess.\n"
    )


def test_set_subcommand_order_after_add_parser_works(project):
    code = (
        LIST_ALLELES + '        self.set_subcommand_order(["predict", "list-alleles", "preprocess", "postprocess"])\n'
    )

    proc, _ = run_with(project, code, "--help")

    assert proc.returncode == 0, proc.stderr
    assert listed(proc.stdout) == ["predict", "list-alleles", "preprocess", "postprocess"]


# 4. nargs, const and dest of a framework option --------------------------------------------


@pytest.mark.parametrize(
    "call, message",
    [
        (
            'p.parser_predict.update_arguments("-f", nargs="+")',
            "update_arguments can't set nargs of --output-format/-f to '+': the framework reads "
            "args.output_format as one value. To replace the option, remove it with "
            'self.parser_predict.remove_argument("-f"), then add_argument() your own.',
        ),
        (
            'p.parser_postprocess.update_arguments("--job-desc-file", nargs="*")',
            "update_arguments can't set nargs of --job-desc-file/-j to '*': the framework reads "
            "args.job_desc_file as one value.",
        ),
        (
            'p.parser_preprocess.update_arguments("--assume-valid", const=False)',
            "update_arguments can't set const of --assume-valid to False: the framework reads "
            "args.assume_valid_flag as the value it stores now. To replace the option, remove it with "
            'self.parser_preprocess.remove_argument("--assume-valid"), then add_argument() your own.',
        ),
        (
            'p.parser_predict.update_arguments("--input-json", dest="subcommand")',
            "update_arguments can't set dest of --input-json/-j to subcommand: dest subcommand belongs to the "
            "framework (it records which subcommand ran); pick another dest.",
        ),
    ],
    ids=["nargs -f", "nargs read by parse_args", "const", "dest subcommand"],
)
def test_update_arguments_keeps_what_the_framework_reads(project, call, message):
    out = probe(
        project,
        f"err = error_of(lambda: {call})\n"
        'args = p.parse_args(["predict", "-j", "in.json", "-f", "json"])\n'
        'print(json.dumps({"err": err, "format": args.output_format, "subcommand": args.subcommand}))',
    )

    assert out["err"] == f"ParserSetupError: {at(1)}: {message}"
    assert out["format"] == "json" and out["subcommand"] == "predict"  # nothing changed


def test_control_update_arguments_may_change_type_and_repeat_nargs(project):
    out = probe(
        project,
        'p.parser_preprocess.update_arguments("--input-json", type=str)\n'
        'p.parser_predict.update_arguments("-f", nargs=None, help="Format.")\n'
        'args = p.parse_args(["predict", "-j", "in.json", "-f", "json"])\n'
        'print(json.dumps({"format": args.output_format}))',
    )

    assert out == {"format": "json"}


# 5. The path checks say what the path is ----------------------------------------------------


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "adir").mkdir()
    (tmp_path / "a.txt").write_text("x\n")
    os.symlink("gone.txt", tmp_path / "dangling")
    return tmp_path


CHECKS = {
    "file: a folder": (cv.validate_file, "adir", "'adir' is a folder, not a file."),
    "file: missing": (cv.validate_file, "nope.json", "'nope.json' does not exist."),
    "file: missing folder": (cv.validate_file, "no/x.json", "'no/x.json' does not exist."),
    "file: broken link": (
        cv.validate_file,
        "dangling",
        "'dangling' is a broken link: its target 'gone.txt' does not exist.",
    ),
    "folder: a file": (cv.validate_directory, "a.txt", "'a.txt' is a file, not a folder."),
    "folder: missing": (cv.validate_directory, "nope", "'nope' does not exist."),
    "folder: broken link": (
        cv.validate_directory,
        "dangling",
        "'dangling' is a broken link: its target 'gone.txt' does not exist.",
    ),
    "file path: a folder": (cv.validate_directory_given_filename, "adir", "'adir' is a folder, not a file."),
}


@pytest.mark.parametrize("name", CHECKS)
def test_path_check_says_what_the_path_is(paths, name):
    check, value, message = CHECKS[name]

    with pytest.raises(argparse.ArgumentTypeError) as raised:
        check(value)

    assert str(raised.value) == message


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a named pipe")
def test_path_check_names_another_kind_of_file(paths):
    os.mkfifo("pipe")

    for check, message in (
        (cv.validate_file, "'pipe' is not a regular file."),
        (cv.validate_directory, "'pipe' is not a folder."),
    ):
        with pytest.raises(argparse.ArgumentTypeError) as raised:
            check("pipe")
        assert str(raised.value) == message


def test_control_path_checks_still_pass_good_paths(paths):
    assert cv.validate_file("a.txt").name == "a.txt"
    assert cv.validate_directory("adir").name == "adir"
    assert cv.validate_directory_given_filename("adir/new.txt").name == "new.txt"
    assert cv.validate_directory_given_filename("new.txt").name == "new.txt"


@pytest.mark.parametrize(
    "argv, message",
    [
        (["predict", "-j", "src"], "argument --input-json/-j: 'src' is a folder, not a file."),
        (["predict", "-j", "missing.json"], "argument --input-json/-j: 'missing.json' does not exist."),
        (["postprocess", "-i", "in.json"], "argument --input-results-dir/-i: 'in.json' is a file, not a folder."),
        (
            ["preprocess", "-j", "in.json", "-o", "out", "--params-dir", "../nope"],
            "argument --params-dir: '../nope' does not exist.",
        ),
    ],
    ids=["folder for a file", "missing file", "file for a folder", "missing params folder"],
)
def test_command_line_path_errors_say_what_the_path_is(project, argv, message):
    proc = subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60
    )

    assert proc.returncode == 2
    assert proc.stderr.splitlines()[-1] == f"run_demo.py {argv[0]}: error: {message}"


# 6. Aliases ---------------------------------------------------------------------------------

ALIASED = """
        self.parser_list_alleles = self.subparser.add_parser("list-alleles", aliases=["la"], help="List alleles.")
        self.parser_list_alleles.add_argument("--species", "-s", default="human", help="Species.")
        self.set_subcommand_order(["predict", "list-alleles", "preprocess", "postprocess"])
"""


def test_set_subcommand_order_keeps_an_alias_next_to_its_subcommand(project):
    shown, _ = run_with(project, ALIASED, "--help")
    by_name, _ = run_with(project, "", "list-alleles", "-s", "mouse")
    by_alias, _ = run_with(project, "", "la", "-s", "rat")

    assert shown.returncode == 0, shown.stderr
    assert "{predict,list-alleles,la,preprocess,postprocess}" in shown.stdout
    assert listed(shown.stdout) == ["predict", "list-alleles (la)", "preprocess", "postprocess"]
    assert (by_name.returncode, by_name.stdout) == (0, "alleles of mouse\n"), by_name.stderr
    assert (by_alias.returncode, by_alias.stdout) == (0, "alleles of rat\n"), by_alias.stderr


def test_help_screens_of_sync_pass_with_an_aliased_subcommand(project, monkeypatch):
    # cli sync's test drive: --help and each subcommand's --help (it may read
    # the subcommands, aliases too, from --help).
    run_with(project, ALIASED)
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)

    results = cli._smoke_check()["results"]

    framework = {f"src/run_demo.py {screen}--help" for screen in ("", "predict ", "preprocess ", "postprocess ")}
    assert framework <= set(results), results
    assert all(passed for passed, _error in results.values()), results


def test_build_startup_check_passes_with_an_aliased_subcommand(project, run_build, fake_venv):
    run_with(project, ALIASED)
    fake_venv(project)

    run = run_build(project)

    assert "Startup check passed." in run.proc.stdout
