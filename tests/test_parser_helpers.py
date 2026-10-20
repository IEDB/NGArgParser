"""remove_argument and update_arguments must change what parses, not only what --help shows.

argparse records an option in three places: the parser's actions, its groups (what --help
prints) and the option-string table (what each typed name maps to, and what a new
declaration conflicts with). The helpers used to edit only the first two, so a removed
option still parsed, re-adding it crashed, and a rename never parsed. Each test runs a
probe inside a freshly generated project and prints one JSON result.
"""

import json
import shutil
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PRELUDE = """\
import contextlib, io, json, sys
sys.path.insert(0, "src")
from DemoArgumentParser import DemoArgumentParser

def parse(p, *argv, input_args=("-t", "in.tsv")):
    sys.argv = ["run_demo.py", "predict", *input_args, *argv]
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            ns = p.parse_args()
        return {"ok": True, "output_format": getattr(ns, "output_format", None)}
    except SystemExit:
        return {"ok": False, "error": err.getvalue().strip().splitlines()[-1]}

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
    return root


def probe(project, body):
    script = project / "probe_helpers.py"
    script.write_text(PRELUDE + body)
    proc = subprocess.run([sys.executable, str(script)], cwd=project, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def at(body_line):
    """The file:line a parser-setup error names for line `body_line` (from 1) of a probe body."""
    return f"probe_helpers.py:{PRELUDE.count(chr(10)) + body_line}"


def test_control_untouched_parser_accepts_output_format(project):
    out = probe(project, 'print(json.dumps(parse(p, "--output-format", "json")))')
    assert out == {"ok": True, "output_format": "json"}


def test_removed_option_no_longer_parses_or_shows(project):
    out = probe(
        project,
        'p.remove_argument("--output-format", "predict")\n'
        'print(json.dumps({"parse": parse(p, "--output-format", "json"),\n'
        '                  "short": parse(p, "-f", "json"),\n'
        '                  "in_help": "--output-format" in p.parser_predict.format_help()}))',
    )
    assert out["parse"]["ok"] is False and "unrecognized arguments" in out["parse"]["error"]
    assert out["short"]["ok"] is False
    assert out["in_help"] is False


def test_removed_option_can_be_declared_again(project):
    out = probe(
        project,
        'p.remove_argument("--output-format", "predict")\n'
        'p.parser_predict.add_argument("--output-format", "-f", dest="output_format", default="json")\n'
        'print(json.dumps({"default": parse(p), "short": parse(p, "-f", "tsv")}))',
    )
    assert out["default"] == {"ok": True, "output_format": "json"}
    assert out["short"] == {"ok": True, "output_format": "tsv"}


def test_removing_a_mutually_exclusive_member(project):
    out = probe(
        project,
        'p.remove_argument("--input-tsv", "predict")\n'
        'print(json.dumps({"usage": p.parser_predict.format_usage(),\n'
        '                  "json_input": parse(p, input_args=("-j", "in.json")),\n'
        '                  "tsv_input": parse(p)}))',
    )
    assert "--input-tsv" not in out["usage"]
    assert out["json_input"]["ok"] is True
    assert out["tsv_input"]["ok"] is False


def test_remove_misspelled_option_suggests_the_right_one(project):
    out = probe(project, 'print(json.dumps(error_of(lambda: p.remove_argument("--output-fromat", "predict"))))')
    assert out == f"ParserSetupError: {at(1)}: No option --output-fromat in predict; did you mean --output-format?"


def test_remove_from_unknown_subparser_names_the_valid_ones(project):
    out = probe(project, 'print(json.dumps(error_of(lambda: p.remove_argument("--output-format", "predcit"))))')
    assert out == (
        f"ParserSetupError: {at(1)}: remove_argument: unknown subparser 'predcit'; use predict, preprocess or postprocess."
    )


def test_update_with_existing_names_still_works(project):
    out = probe(
        project,
        'ret = p.parser_predict.update_arguments("--output-format", "-f", default="json")\n'
        'print(json.dumps({"ret": ret, "default": parse(p)}))',
    )
    assert out["ret"] is True
    assert out["default"] == {"ok": True, "output_format": "json"}


def test_update_refuses_a_rename_and_leaves_the_option_alone(project):
    out = probe(
        project,
        'err = error_of(lambda: p.parser_predict.update_arguments("--output-format", "--format"))\n'
        'print(json.dumps({"err": err, "old_name": parse(p, "--output-format", "json")}))',
    )
    assert out["err"] == (
        f"ParserSetupError: {at(1)}: update_arguments can't rename --output-format/-f to --output-format/--format: "
        'remove the option with self.parser_predict.remove_argument("--output-format"), then add_argument() the new one.'
    )
    assert out["old_name"] == {"ok": True, "output_format": "json"}


def test_update_misspelled_option_suggests_the_right_one(project):
    out = probe(
        project,
        'print(json.dumps(error_of(lambda: p.parser_predict.update_arguments("--output-fromat", default="json"))))',
    )
    assert out == f"ParserSetupError: {at(1)}: No option --output-fromat in predict; did you mean --output-format?"


def test_update_rejects_an_unknown_keyword(project):
    out = probe(
        project,
        'print(json.dumps(error_of(lambda: p.parser_predict.update_arguments("--output-format", defualt="json"))))',
    )
    assert out == (
        f"ParserKeywordError: {at(1)}: predict argument --output-format: update_arguments() got an unexpected "
        "keyword argument 'defualt'; did you mean default=?"
    )


def test_update_can_move_an_option_to_another_help_section(project):
    out = probe(
        project,
        'p.parser_predict.update_arguments("--output-format", "-f", group="my options")\n'
        "print(json.dumps(p.parser_predict.format_help()))",
    )
    # Skip the usage line, which lists every option, and read only the section text.
    sections = out.split("\n\n")
    mine = next(s for s in sections if s.startswith("my options:"))
    assert "--output-format" in mine
    assert not any("--output-format" in s for s in sections if s.startswith("output options:"))


# parse_args takes an argument list, so a tool can test its own parser.


def test_parse_args_accepts_an_argument_list(project):
    out = probe(
        project,
        'ns = p.parse_args(["predict", "-t", "in.tsv", "-f", "json"])\nprint(json.dumps(ns.output_format))',
    )
    assert out == "json"


def test_parse_args_without_a_list_still_reads_the_command_line(project):
    # Control: the run scripts call parse_args() with nothing, so sys.argv must still count.
    out = probe(project, 'print(json.dumps(parse(p, "-f", "json")))')
    assert out == {"ok": True, "output_format": "json"}


def test_postprocess_check_still_runs_for_an_argument_list(project):
    out = probe(
        project,
        "err = io.StringIO()\n"
        "try:\n"
        "    with contextlib.redirect_stderr(err):\n"
        '        p.parse_args(["postprocess"])\n'
        '    result = "parsed"\n'
        "except SystemExit as e:\n"
        '    result = {"code": e.code, "error": err.getvalue().strip().splitlines()[-1]}\n'
        "print(json.dumps(result))",
    )
    assert out["code"] == 2


def test_removing_both_template_inputs_leaves_a_working_parser(project):
    # The template's input group is required; once both of its inputs are removed,
    # the empty group must go too, or every parse fails with "empty group".
    out = probe(
        project,
        'p.remove_argument("--input-tsv", "predict")\n'
        'p.remove_argument("--input-json", "predict")\n'
        'p.parser_predict.add_argument("--fasta", dest="fasta")\n'
        'print(json.dumps({"fasta": parse(p, input_args=("--fasta", "x.fa")),\n'
        '                  "help": error_of(p.parser_predict.format_help)}))',
    )
    assert out["fasta"]["ok"] is True
    assert out["help"] is None


# remove_argument used to exist only on the top-level parser, so
# self.parser_predict.remove_argument(...) failed with AttributeError, while
# update_arguments existed only on the subparser.


def test_remove_from_the_subparser_object(project):
    out = probe(
        project,
        'ret = error_of(lambda: p.parser_predict.remove_argument("--output-format"))\n'
        'print(json.dumps({"err": ret, "long": parse(p, "--output-format", "json"), "short": parse(p, "-f", "json"),\n'
        '                  "in_help": "--output-format" in p.parser_predict.format_help()}))',
    )
    assert out["err"] is None
    assert out["long"]["ok"] is False and "unrecognized arguments" in out["long"]["error"]
    assert out["short"]["ok"] is False
    assert out["in_help"] is False


def test_remove_from_the_subparser_object_then_declare_again(project):
    out = probe(
        project,
        'p.parser_postprocess.remove_argument("--output-format")\n'
        'p.parser_postprocess.add_argument("--output-format", "-f", dest="output_format", default="tsv")\n'
        'ns = p.parse_args(["postprocess", "-j", "in.json"])\n'
        "print(json.dumps(ns.output_format))",
    )
    assert out == "tsv"


def test_remove_from_the_subparser_object_suggests_the_right_name(project):
    out = probe(project, 'print(json.dumps(error_of(lambda: p.parser_predict.remove_argument("--output-fromat"))))')
    assert out == f"ParserSetupError: {at(1)}: No option --output-fromat in predict; did you mean --output-format?"


# A tool option that reused a framework option's name failed with argparse's
# traceback ending in "conflicting option string: -o", at every command and at
# the build's startup check. It now stops with one line that names the
# framework option and the fix.


def run_with(project, declaration, *argv):
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text() + declaration)
    line = len(parser_file.read_text().splitlines()) - declaration.count("\n") + 1
    proc = subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60
    )
    return proc, line


def fix(what, subcommand, option, removable=True):
    """The end of a framework clash line: pick another flag or dest, change the
    framework option, or remove it and declare your own. An option parse_args
    reads can't be removed, so its line names only the first two."""
    own = f"self.parser_{subcommand}"
    update = f'change the framework option with {own}.update_arguments("{option}", ...)'
    if not removable:
        return f"pick another {what}, or {update}"
    return f'pick another {what}, {update}, or replace it with your own: call {own}.remove_argument("{option}") first'


PREFIX_FIX = fix("flag", "predict", "--output-prefix")
DEST_FIX = fix("dest", "predict", "--output-format")

CLASHES = {
    "parser_predict": (
        '        self.parser_predict.add_argument("--organism", "-o", dest="organism")\n',
        "predict argument --organism/-o: -o belongs to the framework (--output-prefix); " + PREFIX_FIX,
    ),
    "group=": (
        '        self.parser_predict.add_argument("--organism", "-o", dest="organism", group="my options")\n',
        "predict argument --organism/-o: -o belongs to the framework (--output-prefix); " + PREFIX_FIX,
    ),
    "input group": (
        '        self.predict_input_group.add_argument("--organism", "-o", dest="organism")\n',
        "predict argument --organism/-o: -o belongs to the framework (--output-prefix); " + PREFIX_FIX,
    ),
    "exclusive group": (
        '        self.parser_predict.add_mutually_exclusive_group().add_argument("--organism", "-o", dest="organism")\n',
        "predict argument --organism/-o: -o belongs to the framework (--output-prefix); " + PREFIX_FIX,
    ),
    # The fix for a re-declared -f with more formats is remove_argument: the line
    # named only update_arguments, which refuses formats write_results can't write.
    "both names": (
        '        self.parser_predict.add_argument("--output-format", "-f", choices=["tsv", "json", "csv"])\n',
        "predict argument --output-format/-f: --output-format, -f belong to the framework (--output-format); "
        + fix("flag", "predict", "--output-format"),
    ),
    "postprocess": (
        '        self.parser_postprocess.add_argument("--inputs", "-i", dest="inputs")\n',
        "postprocess argument --inputs/-i: -i belongs to the framework (--input-results-dir); "
        + fix("flag", "postprocess", "--input-results-dir", removable=False),
    ),
    "preprocess": (
        '        self.parser_preprocess.add_argument("--inputs-dir", dest="inputs")\n',
        "preprocess argument --inputs-dir: --inputs-dir belongs to the framework (--inputs-dir); "
        + fix("flag", "preprocess", "--inputs-dir", removable=False),
    ),
    # Another flag under a framework option's dest: both write args.output_format,
    # so `predict --fmt csv` parsed and failed only when the result was written.
    "dest": (
        '        self.parser_predict.add_argument("--fmt", dest="output_format")\n',
        "predict argument --fmt: dest output_format belongs to the framework (--output-format); " + DEST_FIX,
    ),
    "dest in input group": (
        '        self.predict_input_group.add_argument("--prefix", dest="output_prefix")\n',
        "predict argument --prefix: dest output_prefix belongs to the framework (--output-prefix); "
        + fix("dest", "predict", "--output-prefix"),
    ),
    "dest in exclusive group": (
        '        self.parser_predict.add_mutually_exclusive_group().add_argument("--fmt", dest="output_format")\n',
        "predict argument --fmt: dest output_format belongs to the framework (--output-format); " + DEST_FIX,
    ),
    "positional dest": (
        '        self.parser_postprocess.add_argument("job_desc_file")\n',
        "postprocess argument job_desc_file: dest job_desc_file belongs to the framework (--job-desc-file); "
        + fix("dest", "postprocess", "--job-desc-file", removable=False),
    ),
    "preprocess dest": (
        '        self.parser_preprocess.add_argument("--out", dest="output_dir")\n',
        "preprocess argument --out: dest output_dir belongs to the framework (--output-dir); "
        + fix("dest", "preprocess", "--output-dir"),
    ),
    # args.subcommand picks the run script's branch; storing an option there made
    # predict exit 0 having run nothing.
    "subcommand dest": (
        '        self.parser_predict.add_argument("--mode", dest="subcommand")\n',
        "predict argument --mode: dest subcommand belongs to the framework (it records which subcommand ran); "
        "pick another dest",
    ),
}


@pytest.mark.parametrize("name", CLASHES)
@pytest.mark.parametrize("argv", [["--help"], ["predict", "-t", "in.tsv"]], ids=["help", "predict"])
def test_clash_with_a_framework_option_is_one_line(project, name, argv):
    declaration, message = CLASHES[name]

    proc, line = run_with(project, declaration, *argv)

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {message}\n"


# Two of the tool's own options sharing a flag failed with argparse's traceback
# ending in "conflicting option string: -x" and exit code 1. It is now one line
# naming both options, exit code 2, as for a framework option. Entry: (lines
# appended to the parser, which of them the error names (0 for the first), message).

OWN_CLASHES = {
    # -j is the template's own --input-json, not a framework option.
    "template option": (
        '        self.parser_predict.add_argument("--input-json2", "-j", dest="x")\n',
        0,
        "predict argument --input-json2/-j: -j is already taken by --input-json/-j; pick another flag",
    ),
    "two new options": (
        '        self.parser_predict.add_argument("--xa", "-x")\n        self.parser_predict.add_argument("--xb", "-x")\n',
        1,
        "predict argument --xb/-x: -x is already taken by --xa/-x; pick another flag",
    ),
    "two flags, in a group": (
        '        g = self.parser_preprocess.add_argument_group("g")\n'
        '        g.add_argument("--ya", "-y", "-Y")\n'
        '        g.add_argument("--yb", "-y", "-Y")\n',
        2,
        "preprocess argument --yb/-y/-Y: -y, -Y are already taken by --ya/-y/-Y; pick another flag",
    ),
    "two owners, exclusive group": (
        '        self.parser_postprocess.add_argument("--za", "-z")\n'
        '        self.parser_postprocess.add_argument("--zb", "-Z")\n'
        '        self.parser_postprocess.add_mutually_exclusive_group().add_argument("--zc", "-z", "-Z")\n',
        2,
        "postprocess argument --zc/-z/-Z: -z, -Z are already taken by --za/-z, --zb/-Z; pick another flag",
    ),
}


@pytest.mark.parametrize("name", OWN_CLASHES)
@pytest.mark.parametrize("argv", [["--help"], ["predict", "-t", "in.tsv"]], ids=["help", "predict"])
def test_clash_between_own_options_is_one_line(project, name, argv):
    declaration, error_line, message = OWN_CLASHES[name]

    proc, line = run_with(project, declaration, *argv)

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line + error_line}: {message}\n"


def test_own_options_with_their_own_flags_are_not_a_clash(project):
    # Control: the same declarations with no shared flag.
    proc, _ = run_with(
        project,
        '        self.parser_predict.add_argument("--xa", "-x")\n        self.parser_predict.add_argument("--xb", "-X")\n',
        "predict",
        "--help",
    )

    assert proc.returncode == 0, proc.stderr
    assert "--xa" in proc.stdout and "--xb" in proc.stdout


def test_declaring_a_removed_framework_option_is_not_a_clash(project):
    # Removing a framework option from the subparser frees its names, so this is no clash.
    proc, _ = run_with(
        project,
        '        self.parser_predict.remove_argument("--output-format")\n'
        '        self.parser_predict.add_argument("--output-format", "-f", choices=["tsv", "json", "csv"])\n',
        "predict",
        "--help",
    )

    assert proc.returncode == 0, proc.stderr
    assert "{tsv,json,csv}" in proc.stdout


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="build.sh needs bash and git")
def test_build_reports_a_clash_in_one_line(project, run_build, fake_venv):
    fake_venv(project)
    _, line = run_with(project, '        self.parser_predict.add_argument("--organism", "-o", dest="organism")\n')

    run = run_build(project, expect_ok=False)

    assert run.proc.returncode != 0
    assert (
        f"src/run_demo.py --help: src/DemoArgumentParser.py:{line}: predict argument --organism/-o: "
        "-o belongs to the framework (--output-prefix)"
    ) in run.proc.stderr
    assert "Traceback" not in run.proc.stderr


def test_dest_of_a_removed_framework_option_is_free(project):
    # Control: once the framework option is removed, its dest is the tool's to use.
    proc, _ = run_with(
        project,
        '        self.parser_predict.remove_argument("--output-format")\n'
        '        self.parser_predict.add_argument("--fmt", dest="output_format", default="json")\n',
        "predict",
        "--help",
    )

    assert proc.returncode == 0, proc.stderr
    assert "--fmt" in proc.stdout


def test_own_option_with_its_own_dest_is_not_a_clash(project):
    # Control: a new flag under a new dest.
    proc, _ = run_with(project, '        self.parser_predict.add_argument("--fmt", dest="fmt")\n', "predict", "--help")

    assert proc.returncode == 0, proc.stderr


# update_arguments(dest=...) on a framework option used to succeed, and the
# generated predict branch then failed with "AttributeError: 'Namespace' object
# has no attribute 'output_format'".


def test_update_refuses_to_change_a_framework_dest(project):
    out = probe(
        project,
        'err = error_of(lambda: p.parser_predict.update_arguments("--output-format", dest="fmt"))\n'
        'print(json.dumps({"err": err, "parse": parse(p, "-f", "json")}))',
    )
    assert out["err"] == (
        f"ParserSetupError: {at(1)}: update_arguments can't change dest of --output-format/-f to fmt: the framework reads "
        "args.output_format; dest cannot change. To replace the option, remove it with "
        'self.parser_predict.remove_argument("--output-format"), then add_argument() your own.'
    )
    assert out["parse"] == {"ok": True, "output_format": "json"}


def test_update_refuses_a_framework_dest_for_your_own_option(project):
    out = probe(
        project,
        'print(json.dumps(error_of(lambda: p.parser_predict.update_arguments("--input-tsv", dest="output_prefix"))))',
    )
    assert out == (
        f"ParserSetupError: {at(1)}: update_arguments can't set dest of --input-tsv/-t to output_prefix: dest output_prefix "
        "belongs to the framework (--output-prefix); pick another dest."
    )


def test_update_that_keeps_the_framework_dest_still_works(project):
    # Control: tools pass the framework's own dest along with other changes.
    out = probe(
        project,
        'ret = p.parser_predict.update_arguments("--output-format", "-f", dest="output_format", default="json")\n'
        'print(json.dumps({"ret": ret, "default": parse(p)}))',
    )
    assert out == {"ret": True, "default": {"ok": True, "output_format": "json"}}


def test_update_can_change_dest_of_an_option_removed_and_added_again(project):
    # Control: the re-added option is the tool's own.
    out = probe(
        project,
        'p.parser_predict.remove_argument("--output-format")\n'
        'p.parser_predict.add_argument("--output-format", "-f", dest="output_format", default="json")\n'
        'err = error_of(lambda: p.parser_predict.update_arguments("--output-format", dest="fmt"))\n'
        'sys.argv = ["run_demo.py", "predict", "-t", "in.tsv", "-f", "tsv"]\n'
        'print(json.dumps({"err": err, "fmt": p.parse_args().fmt}))',
    )
    assert out == {"err": None, "fmt": "tsv"}


# update_arguments with no option name used to return False and change nothing.


def test_update_without_an_option_name_lists_the_options(project):
    out = probe(
        project,
        'err = error_of(lambda: p.parser_predict.update_arguments(default="json"))\n'
        'print(json.dumps({"err": err, "default": parse(p)}))',
    )
    assert out["err"] == (
        f"ParserSetupError: {at(1)}: update_arguments() got no option name; pass one of predict's options first: "
        "--help, --input-json, --input-tsv, --output-format, --output-prefix, -f, -h, -j, -o, -t"
    )
    assert out["default"] == {"ok": True, "output_format": "tsv"}


# set_subcommand_order skipped a name it didn't know, and still reordered.


def test_set_subcommand_order_control_reorders(project):
    out = probe(
        project, 'p.set_subcommand_order(["postprocess", "predict", "preprocess"])\nprint(json.dumps(p.format_usage()))'
    )
    assert "{postprocess,predict,preprocess}" in out


@pytest.mark.parametrize(
    "name, message",
    [
        ("preproces", "unknown subcommand 'preproces'; did you mean 'preprocess'?"),
        # Far from every name: most likely a subcommand of the tool's own, not added yet.
        (
            "zzz",
            "subcommand 'zzz' is not added (yet); call set_subcommand_order after "
            "self.add_subcommand('zzz', ...), or use predict, preprocess or postprocess.",
        ),
    ],
    ids=["close", "far"],
)
def test_set_subcommand_order_rejects_an_unknown_name(project, name, message):
    out = probe(
        project,
        f'err = error_of(lambda: p.set_subcommand_order(["postprocess", "{name}", "predict"]))\n'
        'print(json.dumps({"err": err, "usage": p.format_usage()}))',
    )
    assert out["err"] == f"ParserSetupError: {at(1)}: set_subcommand_order: {message}"
    assert "{predict,preprocess,postprocess}" in out["usage"]  # the order the template set, unchanged


EARLY = """\
from core.NGArgumentParser import NGArgumentParser

class Early(NGArgumentParser):
    def __init__(self):
        super().__init__()
        {call}
        self.add_predict_subparser(help="h")

print(json.dumps(error_of(Early)))
"""
EARLY_CALL = 6  # the body line of {call}


def test_set_subcommand_order_before_add_predict_subparser_says_so(project):
    out = probe(project, EARLY.format(call='self.set_subcommand_order(["predict", "preprocess", "postprocess"])'))
    assert out == (
        f"ParserSetupError: {at(EARLY_CALL)}: set_subcommand_order: the predict subparser hasn't been added yet; "
        "call self.add_predict_subparser(...) first."
    )


# self.parser_predict was None until add_predict_subparser(), so using it
# early failed with "'NoneType' object has no attribute 'add_argument'".


@pytest.mark.parametrize(
    "call, name",
    [
        ('self.parser_predict.add_argument("--x")', "self.parser_predict"),
        ('self.parser_predict.help = "x"', "self.parser_predict"),
        ('self.predict_input_group.add_argument("--x")', "self.predict_input_group"),
    ],
    ids=["parser_predict", "set help", "predict_input_group"],
)
def test_predict_parser_before_add_predict_subparser_says_so(project, call, name):
    out = probe(project, EARLY.format(call=call))
    assert out == (
        f"ParserNotReadyError: {at(EARLY_CALL)}: {name} is not ready yet: call self.add_predict_subparser(...) first"
    )


def test_remove_argument_before_add_predict_subparser_still_says_not_added(project):
    # Control: remove_argument's own "not added yet" check still sees the placeholder.
    out = probe(project, EARLY.format(call='self.remove_argument("--output-format", "predict")'))
    assert out == f"ParserSetupError: {at(EARLY_CALL)}: remove_argument: the predict subparser hasn't been added yet."


# group= on the framework's argparse groups raised argparse's "TypeError:
# ... got an unexpected keyword argument 'group'", and predict_input_group is
# the first place the generated parser shows to add an option.


@pytest.mark.parametrize(
    "declaration, subcommand, title",
    [
        ('self.predict_input_group.add_argument("--extra", group="input options")', "predict", "input options"),
        (
            'self.predict_input_group.title = "Inputs"\n'
            '        self.predict_input_group.add_argument("--extra", group="Inputs")',
            "predict",
            "Inputs",
        ),
        (
            'self.preprocess_optional_group.add_argument("--extra", group="optional parameters")',
            "preprocess",
            "optional parameters",
        ),
        (
            'self.postprocess_optional_group.add_argument("--extra", group="optional parameters")',
            "postprocess",
            "optional parameters",
        ),
    ],
    ids=["input group", "renamed input group", "preprocess", "postprocess"],
)
def test_framework_group_takes_group_naming_itself(project, declaration, subcommand, title):
    proc, _ = run_with(project, f"        {declaration}\n", subcommand, "--help")

    assert proc.returncode == 0, proc.stderr
    section = next(s for s in proc.stdout.split("\n\n") if s.startswith(f"{title}:"))
    assert "--extra" in section


def test_framework_group_refuses_another_group_in_one_line(project):
    proc, line = run_with(
        project,
        '        self.predict_input_group.add_argument("--extra", dest="extra", group="my options")\n',
        "--help",
    )

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: predict argument --extra: predict_input_group is already a group; "
        'drop group=, or use self.parser_predict.add_argument(..., group="my options")\n'
    )


# group= on a group the tool makes itself (an argument group, a mutually exclusive
# group, or one made from either) raised argparse's "TypeError: _StoreAction.__init__()
# got an unexpected keyword argument 'group'". Each takes group= as the framework's
# groups do. Entry: (code that makes group g, subcommand, section title, message label).

OWN_GROUPS = {
    "argument group": ('g = self.parser_predict.add_argument_group("x")', "predict", "x", "'x'"),
    "postprocess argument group": ('g = self.parser_postprocess.add_argument_group("x")', "postprocess", "x", "'x'"),
    "exclusive group of a framework group": (
        "g = self.predict_input_group.add_mutually_exclusive_group()",
        "predict",
        "input options",
        "the mutually exclusive group in 'input options'",
    ),
    "exclusive group in your group": (
        'g = self.parser_predict.add_argument_group("x").add_mutually_exclusive_group()',
        "predict",
        "x",
        "the mutually exclusive group in 'x'",
    ),
    "exclusive group of the parser": (
        "g = self.parser_predict.add_mutually_exclusive_group()",
        "predict",
        None,
        "the mutually exclusive group",
    ),
}


@pytest.mark.parametrize("name", [name for name, entry in OWN_GROUPS.items() if entry[2]])
def test_own_group_takes_group_naming_its_section(project, name):
    make, subcommand, title, _ = OWN_GROUPS[name]

    proc, _ = run_with(
        project, f'        {make}\n        g.add_argument("--extra", group="{title}")\n', subcommand, "-h"
    )

    assert proc.returncode == 0, proc.stderr
    section = next(s for s in proc.stdout.split("\n\n") if s.startswith(f"{title}:"))
    assert "--extra" in section


@pytest.mark.parametrize("name", OWN_GROUPS)
def test_own_group_refuses_another_group_in_one_line(project, name):
    make, subcommand, _, label = OWN_GROUPS[name]

    proc, line = run_with(project, f'        {make}\n        g.add_argument("--extra", group="my options")\n', "--help")

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line + 1}: {subcommand} argument --extra: {label} is already a group; "
        f'drop group=, or use self.parser_{subcommand}.add_argument(..., group="my options")\n'
    )


def test_own_group_without_group_keyword_is_unchanged(project):
    # Control: an option added to the tool's own group, with no group=, lands there.
    proc, _ = run_with(
        project,
        '        g = self.parser_predict.add_argument_group("x").add_mutually_exclusive_group()\n'
        '        g.add_argument("--a1")\n'
        '        g.add_argument("--a2")\n',
        "predict",
        "-t",
        "in.tsv",
        "--a1",
        "1",
        "--a2",
        "2",
    )

    assert proc.returncode == 2
    assert proc.stderr.splitlines()[-1] == "run_demo.py predict: error: argument --a2: not allowed with argument --a1"


@pytest.mark.skipif(sys.version_info >= (3, 14), reason="Python 3.14 refuses an argument group inside another")
def test_group_made_inside_your_group_reports_a_clash_in_one_line(project):
    proc, line = run_with(
        project,
        '        g = self.parser_predict.add_argument_group("x")\n'
        '        g.add_argument_group("y").add_argument("--organism", "-o")\n',
        "--help",
    )

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line + 1}: predict argument --organism/-o: -o belongs to the framework "
        f"(--output-prefix); {PREFIX_FIX}\n"
    )


# A parser-setup mistake stopped every command with a traceback and exit code 1,
# while a framework option clash gave one line and exit code 2. These methods now
# raise ParserSetupError, whose message starts with the tool's file:line, and the
# run script prints that line and exits 2.

# What update_arguments takes: add_argument's keywords but action= (deprecated= from Python 3.13).
UPDATE_KEYWORDS = [
    "dest",
    "nargs",
    "const",
    "default",
    "type",
    "choices",
    "required",
    "help",
    "metavar",
    *(["deprecated"] if sys.version_info >= (3, 13) else []),
    "group",
]

SETUP_ERRORS = {
    "update_arguments": (
        'self.parser_predict.update_arguments("--output-formt", default="json")',
        "No option --output-formt in predict; did you mean --output-format?",
    ),
    "unknown keyword": (
        'self.parser_predict.update_arguments("--output-format", colour="red")',
        "predict argument --output-format: update_arguments() got an unexpected keyword argument 'colour'; "
        f"update_arguments takes {', '.join(UPDATE_KEYWORDS)}",
    ),
    "remove_argument": (
        'self.remove_argument("--output-format", "predcit")',
        "remove_argument: unknown subparser 'predcit'; use predict, preprocess or postprocess.",
    ),
    "subparser remove_argument": (
        'self.parser_postprocess.remove_argument("--output-fromat")',
        "No option --output-fromat in postprocess; did you mean --output-format?",
    ),
    "set_subcommand_order": (
        'self.set_subcommand_order(["predcit"])',
        "set_subcommand_order: unknown subcommand 'predcit'; did you mean 'predict'?",
    ),
}


@pytest.mark.parametrize("name", SETUP_ERRORS)
def test_setup_error_is_one_line_from_the_run_script(project, name):
    declaration, message = SETUP_ERRORS[name]

    proc, line = run_with(project, f"        {declaration}\n", "predict", "-t", "in.tsv")

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {message}\n"


def edit_parser(project, old, new):
    """Replace `old` with `new` in the generated parser; return the line `old` started on."""
    parser_file = project / "src" / "DemoArgumentParser.py"
    text = parser_file.read_text()
    assert old in text
    parser_file.write_text(text.replace(old, new, 1))
    return text[: text.index(old)].count("\n") + 1


ADD_PREDICT = "        self.parser_predict = self.add_predict_subparser("


def test_placeholder_use_is_one_line_from_the_run_script(project):
    line = edit_parser(project, ADD_PREDICT, '        self.predict_input_group.add_argument("--x")\n' + ADD_PREDICT)

    proc = subprocess.run([sys.executable, "src/run_demo.py", "--help"], cwd=project, capture_output=True, text=True)

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: self.predict_input_group is not ready yet: "
        "call self.add_predict_subparser(...) first\n"
    )


# A parser whose __init__ never called super().__init__() failed later with
# "AttributeError: 'DemoArgumentParser' object has no attribute 'subparser'".

NO_SUPER = (
    "DemoArgumentParser has not run NGArgumentParser.__init__: call super().__init__() at the start of {}.__init__"
)


def test_missing_super_init_is_one_line_from_the_run_script(project):
    edit_parser(project, "        super().__init__()\n", "        pass\n")
    line = edit_parser(project, ADD_PREDICT, ADD_PREDICT)

    proc = subprocess.run([sys.executable, "src/run_demo.py", "--help"], cwd=project, capture_output=True, text=True)

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {NO_SUPER.format('DemoArgumentParser')}\n"


BARE = """\
from core.NGArgumentParser import NGArgumentParser

class DemoArgumentParser(NGArgumentParser):
    def __init__(self):
        self.description = "no super().__init__()"

calls = {
    "add_predict_subparser": lambda: DemoArgumentParser().add_predict_subparser(help="h"),
    "parser_predict": lambda: DemoArgumentParser().parser_predict.add_argument("--x"),
    "parser_preprocess": lambda: DemoArgumentParser().parser_preprocess,
    "set_subcommand_order": lambda: DemoArgumentParser().set_subcommand_order(["predict"]),
    "remove_argument": lambda: DemoArgumentParser().remove_argument("-f", "predict"),
    "parse_args": lambda: DemoArgumentParser().parse_args(["predict"]),
}
print(json.dumps({name: error_of(call) for name, call in calls.items()}))
"""


def test_missing_super_init_is_named_at_the_first_framework_call(project):
    out = probe(project, BARE)

    first = PRELUDE.count("\n") + BARE.splitlines().index("calls = {") + 2
    assert out == {
        name: f"ParserNotReadyError: probe_helpers.py:{first + n}: {NO_SUPER.format('DemoArgumentParser')}"
        for n, name in enumerate(out)
    }
    assert list(out) == [
        "add_predict_subparser",
        "parser_predict",
        "parser_preprocess",
        "set_subcommand_order",
        "remove_argument",
        "parse_args",
    ]


def test_parser_that_calls_super_init_is_unchanged(project):
    # Control: the generated parser, which calls super().__init__(), parses as before.
    out = probe(project, 'print(json.dumps(parse(p, "-f", "json")))')
    assert out == {"ok": True, "output_format": "json"}


def test_setup_errors_keep_the_builtin_class_they_had(project):
    # Code that catches ValueError, TypeError or AttributeError, and hasattr(), work as before.
    out = probe(
        project,
        "from core.NGArgumentParser import ParserSetupError\n"
        "def classes(call):\n"
        "    try:\n"
        "        call()\n"
        "    except Exception as e:\n"
        "        return [c.__name__ for c in (ParserSetupError, ValueError, TypeError, AttributeError)\n"
        "                if isinstance(e, c)]\n"
        "class Early(DemoArgumentParser.__mro__[1]):\n"
        "    def __init__(self):\n"
        "        super().__init__()\n"
        "        self.result = {'placeholder': classes(lambda: self.parser_predict.add_argument('--x')),\n"
        "                       'hasattr': hasattr(self.parser_predict, 'add_argument')}\n"
        "early = Early().result\n"
        "print(json.dumps({\n"
        "    'update_arguments': classes(lambda: p.parser_predict.update_arguments('--zz')),\n"
        "    'keyword': classes(lambda: p.parser_predict.update_arguments('-f', colour='red')),\n"
        "    'remove_argument': classes(lambda: p.remove_argument('-f', 'x')),\n"
        "    'set_subcommand_order': classes(lambda: p.set_subcommand_order(['x'])),\n"
        "    **early}))",
    )

    assert out == {
        "update_arguments": ["ParserSetupError", "ValueError"],
        "keyword": ["ParserSetupError", "ValueError", "TypeError"],
        "remove_argument": ["ParserSetupError", "ValueError"],
        "set_subcommand_order": ["ParserSetupError", "ValueError"],
        "placeholder": ["ParserSetupError", "ValueError", "AttributeError"],
        "hasattr": False,
    }


# update_arguments("-f", choices=["tsv", "json", "csv"]) was accepted, and
# `-f csv` failed only when the result was written: "unsupported output format: 'csv'".


@pytest.mark.parametrize("subcommand", ["predict", "postprocess"])
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ('choices=["tsv", "json", "csv"]', "choices of --output-format/-f to tsv, json, csv"),
        ("choices=None", "choices of --output-format/-f to None"),
        ('default="csv"', "default of --output-format/-f to 'csv'"),
    ],
    ids=["choices", "no choices", "default"],
)
def test_update_refuses_an_output_format_write_results_cannot_write(project, subcommand, kwargs, message):
    out = probe(
        project,
        f'err = error_of(lambda: p.parser_{subcommand}.update_arguments("-f", {kwargs}))\n'
        f'print(json.dumps({{"err": err, "usage": p.parser_{subcommand}.format_usage()}}))',
    )

    assert out["err"] == (
        f"ParserSetupError: {at(1)}: update_arguments can't set {message}: write_results writes only tsv and json. "
        f"To offer formats you write yourself, remove the option with "
        f'self.parser_{subcommand}.remove_argument("--output-format"), then add_argument() your own.'
    )
    assert "{tsv,json}" in out["usage"]  # unchanged


def test_update_keeps_output_formats_write_results_writes(project):
    # Control: a narrower choice, a supported default, and a case write_results ignores.
    out = probe(
        project,
        'p.parser_predict.update_arguments("-f", choices=["json"], default="json")\n'
        'p.parser_postprocess.update_arguments("--output-format", choices=["TSV", "JSON"], default="TSV")\n'
        'print(json.dumps({"predict": parse(p), "post": p.parse_args(["postprocess", "-j", "in.json"]).output_format}))',
    )
    assert out == {"predict": {"ok": True, "output_format": "json"}, "post": "TSV"}


def test_update_of_your_own_output_format_is_not_checked(project):
    # Control: once the framework's -f is replaced, its choices are the tool's.
    out = probe(
        project,
        'p.parser_predict.remove_argument("-f")\n'
        'p.parser_predict.add_argument("--output-format", "-f", dest="output_format", default="tsv")\n'
        'err = error_of(lambda: p.parser_predict.update_arguments("-f", choices=["tsv", "csv"]))\n'
        'print(json.dumps({"err": err, "csv": parse(p, "-f", "csv")}))',
    )
    assert out == {"err": None, "csv": {"ok": True, "output_format": "csv"}}


# Two more parser-setup mistakes stopped every command with a traceback and exit
# code 1: a second add_predict_subparser() ("conflicting subparser: predict" on
# Python 3.11 and later; 3.9 and 3.10 listed predict twice and dropped the first
# one's options), and group= on the top-level parser ("TypeError: ... got an
# unexpected keyword argument 'group'").

TOP_LEVEL_SETUP_ERRORS = {
    "second add_predict_subparser": (
        'self.add_predict_subparser(help="again")',
        "add_predict_subparser() was called a second time: predict already exists; "
        "call it once, and add options to the self.parser_predict it returns",
    ),
    "group= on the top-level parser": (
        'self.add_argument("--min-length", type=int, group="x")',
        "top-level argument --min-length: group= works only on a subcommand's options; use "
        'self.parser_predict.add_argument(..., group="x") (or parser_preprocess, parser_postprocess), or drop group=',
    ),
}


@pytest.mark.parametrize("name", TOP_LEVEL_SETUP_ERRORS)
@pytest.mark.parametrize("argv", [["--help"], ["predict", "-t", "in.tsv"]], ids=["help", "predict"])
def test_top_level_setup_error_is_one_line_from_the_run_script(project, name, argv):
    declaration, message = TOP_LEVEL_SETUP_ERRORS[name]

    proc, line = run_with(project, f"        {declaration}\n", *argv)

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {message}\n"


def test_conflict_and_group_errors_keep_the_builtin_class_they_had(project):
    # argparse raised ArgumentError for a conflict (ValueError for a subparser on
    # 3.14), and TypeError for group=; code that catches those still works.
    out = probe(
        project,
        "import argparse\n"
        "from core.NGArgumentParser import ParserSetupError\n"
        "def classes(call):\n"
        "    try:\n"
        "        call()\n"
        "    except Exception as e:\n"
        "        return [c.__name__ for c in (ParserSetupError, ValueError, TypeError, argparse.ArgumentError)\n"
        "                if isinstance(e, c)], str(e).split(': ', 1)[1]\n"
        "p.parser_predict.add_argument('--xa', '-x')\n"
        "print(json.dumps({\n"
        "    'subparser': classes(lambda: p.add_predict_subparser(help='again')),\n"
        "    'option': classes(lambda: p.parser_predict.add_argument('--xb', '-x')),\n"
        "    'group': classes(lambda: p.add_argument('--y', group='g'))}))",
    )

    assert out == {
        "subparser": [
            ["ParserSetupError", "ValueError", "ArgumentError"],
            "add_predict_subparser() was called a second time: predict already exists; "
            "call it once, and add options to the self.parser_predict it returns",
        ],
        "option": [
            ["ParserSetupError", "ValueError", "ArgumentError"],
            "predict argument --xb/-x: -x is already taken by --xa/-x; pick another flag",
        ],
        "group": [
            ["ParserSetupError", "ValueError", "TypeError"],
            "top-level argument --y: group= works only on a subcommand's options; use "
            'self.parser_predict.add_argument(..., group="g") (or parser_preprocess, parser_postprocess), or drop group=',
        ],
    }


# A top-level option (self.add_argument) given after the subcommand, or an option
# of another subcommand, failed with only "unrecognized arguments: --min-length 3".
# The error now says where the option belongs; the exit code stays 2.

TOP_LEVEL_OPTION = '        self.add_argument("--min-length", dest="min_length", type=int)\n'
# The user's half only: the hint once went on "or add it to predict with
# self.parser_predict.add_argument(...)", advice for the tool's developer.
TOP_LEVEL_HINT = "--min-length is an option of the top-level command; put it before '{0}'"


@pytest.mark.parametrize("given", [["--min-length", "3"], ["--min-length=3"]], ids=["separate", "equals"])
def test_top_level_option_after_the_subcommand_says_where_it_goes(project, given):
    proc, _ = run_with(project, TOP_LEVEL_OPTION, "predict", "-t", "in.tsv", *given)

    assert proc.returncode == 2
    assert proc.stderr.splitlines()[-1] == (
        f"run_demo.py: error: unrecognized arguments: {' '.join(given)}; {TOP_LEVEL_HINT.format('predict')}"
    )


def test_top_level_option_after_preprocess_names_preprocess(project):
    proc, _ = run_with(project, TOP_LEVEL_OPTION, "preprocess", "-j", "in.json", "-o", "job", "--min-length", "3")

    assert proc.returncode == 2
    assert proc.stderr.splitlines()[-1] == (
        f"run_demo.py: error: unrecognized arguments: --min-length 3; {TOP_LEVEL_HINT.format('preprocess')}"
    )
    assert not (project / "job").exists()  # a failed command still creates no folders


def test_top_level_option_before_the_subcommand_still_parses(project):
    # Control: before the subcommand, the top-level option parses as it always did.
    out = probe(
        project,
        'p.add_argument("--min-length", dest="min_length", type=int)\n'
        'print(json.dumps(p.parse_args(["--min-length", "3", "predict", "-t", "in.tsv"]).min_length))',
    )
    assert out == 3


@pytest.mark.parametrize(
    "argv, hint",
    [
        (
            ["predict", "-t", "in.tsv", "--assume-valid"],
            "--assume-valid; --assume-valid is an option of preprocess, not predict",
        ),
        (
            ["preprocess", "-j", "in.json", "-o", "job", "-f", "json"],
            "-f json; -f is an option of predict and postprocess, not preprocess",
        ),
        (
            ["postprocess", "-j", "in.json", "--params-dir", "x", "-t", "in.tsv"],
            "--params-dir x -t in.tsv; --params-dir is an option of preprocess, not postprocess; "
            "-t is an option of predict, not postprocess",
        ),
    ],
    ids=["predict", "preprocess", "postprocess"],
)
def test_option_of_another_subcommand_names_it(project, argv, hint):
    proc, _ = run_with(project, "", *argv)

    assert proc.returncode == 2
    # With the usage of the subcommand given (it showed the top-level one).
    assert proc.stderr.splitlines()[-1] == f"run_demo.py {argv[0]}: error: unrecognized arguments: {hint}"
    assert not (project / "job").exists()


def test_unknown_option_keeps_the_argparse_error(project):
    # Control: an option no command has gets argparse's message, unchanged.
    proc, _ = run_with(project, "", "predict", "-t", "in.tsv", "--bogus", "3")

    assert proc.returncode == 2
    assert proc.stderr.splitlines()[-1] == "run_demo.py predict: error: unrecognized arguments: --bogus 3"


# A misspelled option failed with only "unrecognized arguments: --min-lenght 3".
# The error now names the closest option of the subcommand given, or of the
# top-level command (which goes before the subcommand); the exit code stays 2.

MISSPELLING_OPTIONS = (
    '        self.parser_predict.add_argument("--min-length", dest="min_length", type=int)\n'
    '        self.add_argument("--verbose", action="store_true")\n'
)


@pytest.mark.parametrize(
    "argv, hint",
    [
        (["predict", "-t", "in.tsv", "--min-lenght", "3"], "--min-lenght 3; did you mean --min-length?"),
        (["predict", "-t", "in.tsv", "--min-lenght=3"], "--min-lenght=3; did you mean --min-length?"),
        (["predict", "-t", "in.tsv", "-min-length", "3"], "-min-length 3; did you mean --min-length?"),
        (["predict", "-t", "in.tsv", "--outptu-prefix", "r"], "--outptu-prefix r; did you mean --output-prefix?"),
        (["predict", "-t", "in.tsv", "--verbos"], "--verbos; did you mean --verbose before 'predict'?"),
        (
            ["predict", "-t", "in.tsv", "--min-lenght", "3", "--verbos"],
            "--min-lenght 3 --verbos; did you mean --min-length?; did you mean --verbose before 'predict'?",
        ),
        (
            ["preprocess", "-j", "in.json", "-o", "job", "--assume-vaild"],
            "--assume-vaild; did you mean --assume-valid?",
        ),
        (["postprocess", "-j", "in.json", "--output-prefx", "r"], "--output-prefx r; did you mean --output-prefix?"),
    ],
    ids=["predict", "equals", "one-dash", "framework", "top-level", "two", "preprocess", "postprocess"],
)
def test_misspelled_option_names_the_closest_one(project, argv, hint):
    proc, _ = run_with(project, MISSPELLING_OPTIONS, *argv)

    assert proc.returncode == 2
    assert proc.stderr.splitlines()[-1] == f"run_demo.py {argv[0]}: error: unrecognized arguments: {hint}"
    assert not (project / "job").exists()  # a failed command still creates no folders


def test_misspelled_option_is_not_matched_to_a_hidden_one(project):
    # --debug-level is hidden from -h (help=SUPPRESS), so the hint names --debug.
    declaration = (
        '        self.parser_predict.add_argument("--debug-level", help=argparse.SUPPRESS)\n'
        '        self.parser_predict.add_argument("--debug", action="store_true")\n'
    )
    proc, _ = run_with(project, declaration, "predict", "-t", "in.tsv", "--debug-levl", "2")

    assert proc.returncode == 2
    assert proc.stderr.splitlines()[-1] == (
        "run_demo.py predict: error: unrecognized arguments: --debug-levl 2; did you mean --debug?"
    )


# update_arguments("-o", help=...) moved the option to the end of its help
# section, so predict -h listed -o below -f. It now keeps its place; group=
# naming another section still moves it there.


def section_options(project, body, title):
    """The first option string of each option `title` lists in predict -h, after `body`."""
    text = probe(project, body + "print(json.dumps(p.parser_predict.format_help()))")
    section = next(s for s in text.split("\n\n") if s.startswith(title + ":"))
    return [line.split()[0].rstrip(",") for line in section.splitlines()[1:] if line.startswith("  -")]


@pytest.mark.parametrize(
    "body, title, order",
    [
        (
            'p.parser_predict.update_arguments("-o", help="Where results go.")\n',
            "output options",
            ["--output-prefix", "--output-format"],
        ),
        (
            'p.parser_predict.update_arguments("--output-prefix", "-o", default="r", group="output options")\n',
            "output options",
            ["--output-prefix", "--output-format"],
        ),
        (
            'p.parser_predict.update_arguments("-t", help="A TSV file.")\n',
            "input options",
            ["--input-tsv", "--input-json"],
        ),
        (
            'p.parser_predict.add_argument("--a", group="mine")\n'
            'p.parser_predict.add_argument("--b", group="mine")\n'
            'p.parser_predict.update_arguments("--a", default="1")\n',
            "mine",
            ["--a", "--b"],
        ),
    ],
    ids=["help", "own-section", "exclusive-input", "own-option"],
)
def test_update_keeps_the_option_in_its_place(project, body, title, order):
    assert section_options(project, body, title) == order


def test_update_still_applies_the_change_in_place(project):
    # Control: the option keeps its place and still gets the new help.
    out = probe(
        project,
        'p.parser_predict.update_arguments("-o", help="Where results go.")\n'
        "print(json.dumps(p.parser_predict.format_help()))",
    )
    section = next(s for s in out.split("\n\n") if s.startswith("output options:"))
    assert "Where results go." in section
    assert "Output prefix for the prediction result files." not in out


def test_update_with_another_section_still_moves_the_option_to_its_end(project):
    # Control: group= naming another section moves the option there, last.
    assert section_options(
        project, 'p.parser_predict.update_arguments("-o", group="input options")\n', "input options"
    ) == ["--input-tsv", "--input-json", "--output-prefix"]
