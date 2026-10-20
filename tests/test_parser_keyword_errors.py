"""Parser-setup mistakes must stop the tool with one line naming the tool's file:line.

- group= given an argument group, not its title, made a help section titled
  "<argparse._ArgumentGroup object at 0x...>:". A group that is a help section of
  that subcommand, such as self.predict_input_group, now counts as its title; a
  group of another subcommand is refused with one line naming it.
- add_predict_subparser(help=..., epilog=...) failed with "unexpected keyword
  argument 'epilog'": it took only help, description and formatter_class.
- A misspelled add_argument keyword (hepl=), and required=True in a mutually
  exclusive group, gave argparse's traceback and exit code 1.
- update_arguments("-f", choices=["json"]) kept the default tsv, so -h said
  "(choices: json; default: tsv)" and predict wrote TSV.
- update_arguments("--input-json", required=True) on one of the exclusive inputs
  made the other one unusable.
- write_results refuses "tsv" or "json" as the prefix, so predict -o tsv, which
  wrote tsv.tsv, is a usage error now.

Each probe runs inside a freshly generated project and prints one JSON result.
"""

import json
import re
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PRELUDE = """\
import contextlib, io, json, sys
sys.path.insert(0, "src")
from DemoArgumentParser import DemoArgumentParser

def parse(p, *argv, input_args=("-t", "in.tsv"), subcommand="predict"):
    sys.argv = ["run_demo.py", subcommand, *input_args, *argv]
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
    script = project / "probe_keywords.py"
    script.write_text(PRELUDE + body)
    proc = subprocess.run([sys.executable, str(script)], cwd=project, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def at(body_line):
    """The file:line a parser-setup error names for line `body_line` (from 1) of a probe body."""
    return f"probe_keywords.py:{PRELUDE.count(chr(10)) + body_line}"


def run_with(project, declaration, *argv):
    """Append `declaration` to the generated parser's __init__ and run the run script."""
    parser_file = project / "src" / "DemoArgumentParser.py"
    parser_file.write_text(parser_file.read_text() + declaration)
    line = len(parser_file.read_text().splitlines()) - declaration.count("\n") + 1
    proc = subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60
    )
    return proc, line


def edit_parser(project, old, new):
    """Replace `old` with `new` in the generated parser; return the line `old` started on."""
    parser_file = project / "src" / "DemoArgumentParser.py"
    text = parser_file.read_text()
    assert old in text
    parser_file.write_text(text.replace(old, new, 1))
    return text[: text.index(old)].count("\n") + 1


TAKES_A_TITLE = "group= takes a section title (a str), "

# group=<a group object> --------------------------------------------------------


def sections(help_text):
    """{section title: [its long options]} of a help screen."""
    found = {}
    for block in help_text.split("\n\n"):
        title, _, body = block.partition(":\n")
        found[title.strip()] = re.findall(r"^  (--[\w-]+)", body, re.M)
    return found


def test_group_object_of_the_subcommand_is_its_section(project):
    # group=<a section of this subcommand> is the same as its title, on the
    # subcommand's parser, in update_arguments and on that group itself.
    out = probe(
        project,
        'p.parser_predict.add_argument("--beta", group=p.predict_input_group)\n'
        'p.predict_input_group.add_argument("--gamma", group=p.predict_input_group)\n'
        'p.parser_predict.update_arguments("--output-prefix", group=p.predict_input_group)\n'
        'mine = p.parser_predict.add_argument_group("mine")\n'
        'p.parser_predict.add_argument("--delta", group=mine)\n'
        'p.parser_postprocess.add_argument("--epsilon", group=p.postprocess_optional_group)\n'
        # Control: a title still works.
        'p.parser_predict.add_argument("--zeta", group="input options")\n'
        'print(json.dumps({"predict": p.parser_predict.format_help(), "post": p.parser_postprocess.format_help(),\n'
        '                  "parsed": parse(p, "--beta", "1", "--delta", "2")}))',
    )

    assert "object at 0x" not in out["predict"]
    predict = sections(out["predict"])
    assert predict["input options"] == ["--input-tsv", "--input-json", "--beta", "--gamma", "--output-prefix", "--zeta"]
    assert predict["output options"] == ["--output-format"]
    assert predict["mine"] == ["--delta"]
    assert sections(out["post"])["optional parameters"][-1] == "--epsilon"
    assert out["parsed"] == {"ok": True, "output_format": "tsv"}


def test_another_section_on_a_group_is_refused_as_its_title_is(project):
    # The same as group="input source (choose exactly one)" on that group's own add_argument.
    proc, line = run_with(
        project,
        "        self.postprocess_optional_group.add_argument('--beta', group=self.postprocess_required_input_group)\n",
        "--help",
    )

    assert proc.returncode == 2
    assert proc.stderr == (
        f"src/DemoArgumentParser.py:{line}: postprocess argument --beta: postprocess_optional_group is already a "
        'group; drop group=, or use self.parser_postprocess.add_argument(..., group="input source (choose exactly one)")\n'
    )


ANOTHER_SUBCOMMAND = "group= takes a section of {0} (a title, or one of its groups), not a group of {1}: "

GROUP_OBJECTS = {
    "a group of another subcommand": (
        "p.parser_predict.add_argument('--beta', group=p.preprocess_optional_group)",
        "predict argument --beta: " + ANOTHER_SUBCOMMAND.format("predict", "preprocess") + 'use group="optional '
        "parameters\", or call that group's own add_argument",
    ),
    "a group of another subcommand, on a group": (
        "p.postprocess_optional_group.add_argument('--beta', group=p.predict_input_group)",
        "postprocess argument --beta: "
        + ANOTHER_SUBCOMMAND.format("postprocess", "predict")
        + "call that group's own add_argument",
    ),
    "a group of another subcommand, in update_arguments": (
        "p.parser_predict.update_arguments('--output-prefix', group=p.postprocess_optional_group)",
        "predict argument --output-prefix/-o: "
        + ANOTHER_SUBCOMMAND.format("predict", "postprocess")
        + 'use group="optional parameters", or call that group\'s own add_argument',
    ),
    "a group of another parser": (
        "p.parser_predict.add_argument('--beta', group=__import__('argparse').ArgumentParser().add_argument_group('x'))",
        "predict argument --beta: group= takes a section of predict (a title, or one of its groups), not a group of "
        'another parser: use group="x", or call that group\'s own add_argument',
    ),
    "a mutually exclusive group": (
        "p.parser_predict.add_argument('--beta', group=p.parser_predict.add_mutually_exclusive_group())",
        f"predict argument --beta: {TAKES_A_TITLE}not a mutually exclusive group: call that group's own add_argument",
    ),
    "top-level": (
        "p.add_argument('--beta', group=p.predict_input_group)",
        f'top-level argument --beta: {TAKES_A_TITLE}not an argument group: use group="input options", '
        "or call that group's own add_argument",
    ),
    "not a group": (
        "p.parser_preprocess.add_argument('--beta', group=1)",
        f'preprocess argument --beta: {TAKES_A_TITLE}not int: use group="my options"',
    ),
}


@pytest.mark.parametrize("name", GROUP_OBJECTS)
def test_group_keyword_that_is_no_title_names_the_fix(project, name):
    call, message = GROUP_OBJECTS[name]

    out = probe(project, f"print(json.dumps(error_of(lambda: {call})))")

    assert out == f"ParserKeywordError: {at(1)}: {message}"


# add_predict_subparser keywords ------------------------------------------------

ADD_PREDICT = "        self.parser_predict = self.add_predict_subparser(\n"
DESCRIPTION = "            description=''\n"


def test_add_predict_subparser_passes_on_argparse_keywords(project):
    edit_parser(
        project,
        DESCRIPTION,
        "            description='Report lengths.',\n"
        "            epilog='Exit codes: 0 ok, 2 usage error.',\n"
        "            usage='%(prog)s (-t TSV | -j JSON) [options]',\n",
    )

    help_screen = subprocess.run(
        [sys.executable, "src/run_demo.py", "predict", "-h"], cwd=project, capture_output=True, text=True
    )
    top = subprocess.run([sys.executable, "src/run_demo.py", "-h"], cwd=project, capture_output=True, text=True)

    assert help_screen.returncode == 0, help_screen.stderr
    assert help_screen.stdout.startswith("usage: run_demo.py predict (-t TSV | -j JSON) [options]\n\nReport lengths.")
    assert help_screen.stdout.rstrip().endswith("Exit codes: 0 ok, 2 usage error.")
    assert "Run one prediction." in top.stdout


def test_add_predict_subparser_still_takes_its_arguments_by_position(project):
    out = probe(
        project,
        "import argparse\n"
        "from core.NGArgumentParser import NGArgumentParser\n"
        "class Positional(NGArgumentParser):\n"
        "    def __init__(self):\n"
        "        super().__init__()\n"
        "        self.add_predict_subparser('Short help.', 'Long text.', argparse.RawDescriptionHelpFormatter,\n"
        "                                   prog='lengths predict', epilog='Bye.')\n"
        "q = Positional()\n"
        "predict = q.subparser.choices['predict']\n"
        "print(json.dumps({'formatter': predict.formatter_class.__name__, 'prog': predict.prog,\n"
        "                  'help': q.parser_predict.help, 'epilog': predict.epilog,\n"
        "                  'description': predict.description}))",
    )

    assert out == {
        "formatter": "RawDescriptionHelpFormatter",
        "prog": "lengths predict",
        "help": "Short help.",
        "epilog": "Bye.",
        "description": "Long text.",
    }


SUBPARSER_KEYWORDS = {
    "misspelled": (
        "            epilgo='Bye.',\n",
        "add_predict_subparser() got an unexpected keyword argument 'epilgo'; did you mean epilog=?",
    ),
    "aliases": (
        "            aliases=['p'],\n",
        "add_predict_subparser() takes no aliases=: the run script runs predict only when args.subcommand is "
        "'predict', and an alias would set args.subcommand to the alias",
    ),
    "conflict_handler": (
        "            conflict_handler='resolve',\n",
        "add_predict_subparser() takes no conflict_handler=: it would let an option of yours take a flag of a "
        "framework option; to replace one, remove it with self.parser_predict.remove_argument(...) first",
    ),
}


@pytest.mark.parametrize("name", SUBPARSER_KEYWORDS)
def test_add_predict_subparser_keyword_mistake_is_one_line(project, name):
    keyword, message = SUBPARSER_KEYWORDS[name]
    line = edit_parser(project, ADD_PREDICT, ADD_PREDICT)
    edit_parser(project, DESCRIPTION, "            description='',\n" + keyword)

    proc = subprocess.run([sys.executable, "src/run_demo.py", "--help"], cwd=project, capture_output=True, text=True)

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {message}\n"


# argparse's own TypeError and ValueError from add_argument ---------------------

ARGPARSE_ERRORS = {
    "misspelled keyword": (
        "p.parser_predict.add_argument('--beta', hepl='x')",
        "ParserKeywordError",
        "predict argument --beta: add_argument() got an unexpected keyword argument 'hepl'; did you mean help=?",
    ),
    "misspelled keyword, with group=": (
        "p.parser_predict.add_argument('--beta', defualt=1, group='my options')",
        "ParserKeywordError",
        "predict argument --beta: add_argument() got an unexpected keyword argument 'defualt'; did you mean default=?",
    ),
    "misspelled keyword in a group": (
        "p.predict_input_group.add_argument('--beta', choises=['a'])",
        "ParserKeywordError",
        "predict argument --beta: add_argument() got an unexpected keyword argument 'choises'; did you mean choices=?",
    ),
    "misspelled keyword on the top-level parser": (
        "p.add_argument('--beta', hepl='x')",
        "ParserKeywordError",
        "top-level argument --beta: add_argument() got an unexpected keyword argument 'hepl'; did you mean help=?",
    ),
    "keyword the action takes no": (
        "p.parser_postprocess.add_argument('--fast', action='store_true', type=int)",
        "ParserKeywordError",
        "postprocess argument --fast: action='store_true' takes no type=",
    ),
    "required in a mutually exclusive group": (
        "p.parser_predict.add_mutually_exclusive_group().add_argument('--beta', required=True)",
        "ParserSetupError",
        "predict argument --beta: an option of a mutually exclusive group can't be required=True: make the group "
        "required instead, with add_mutually_exclusive_group(required=True)",
    ),
    "required in the input group's exclusive group": (
        "p.predict_input_group.add_mutually_exclusive_group().add_argument('--beta', required=True)",
        "ParserSetupError",
        "predict argument --beta: an option of a mutually exclusive group can't be required=True: make the group "
        "required instead, with add_mutually_exclusive_group(required=True)",
    ),
    # A ValueError on every Python (type='int' is one up to 3.13, a TypeError on 3.14).
    "value argparse refuses": (
        "p.parser_preprocess.add_argument('--beta', nargs=0)",
        "ParserSetupError",
        "preprocess argument --beta: nargs for store actions must be != 0; if you have nothing to store, actions "
        "such as store true or store const may be more appropriate",
    ),
}


@pytest.mark.parametrize("name", ARGPARSE_ERRORS)
def test_argparse_add_argument_error_names_the_line(project, name):
    call, error_class, message = ARGPARSE_ERRORS[name]

    out = probe(
        project,
        f"err = error_of(lambda: {call})\n"
        # Control: a correct declaration after it still parses.
        "p.parser_predict.add_argument('--ok', help='fine', group='my options')\n"
        "print(json.dumps({'err': err, 'ok': parse(p, '--ok', '1')}))",
    )

    assert out == {"err": f"{error_class}: {at(1)}: {message}", "ok": {"ok": True, "output_format": "tsv"}}


def test_argparse_errors_keep_the_class_argparse_raised(project):
    # Code that catches TypeError or ValueError still catches them, with argparse's error as the
    # cause. Every ParserSetupError is a ValueError, so ParserKeywordError is both.
    out = probe(
        project,
        "from core.NGArgumentParser import ParserSetupError\n"
        "def caught(call):\n"
        "    try:\n"
        "        call()\n"
        "    except Exception as e:\n"
        "        return [c.__name__ for c in (ParserSetupError, TypeError, ValueError) if isinstance(e, c)] + [\n"
        "            type(e.__cause__).__name__]\n"
        "print(json.dumps({\n"
        "    'keyword': caught(lambda: p.parser_predict.add_argument('--a', hepl='x')),\n"
        "    'value': caught(lambda: p.parser_predict.add_argument('--b', nargs=0)),\n"
        "}))",
    )

    assert out == {
        "keyword": ["ParserSetupError", "TypeError", "ValueError", "TypeError"],
        "value": ["ParserSetupError", "ValueError", "ValueError"],
    }


RUN_SCRIPT_ERRORS = {
    "group of another subcommand": (
        "self.parser_predict.add_argument('--beta', group=self.preprocess_optional_group)",
        "predict argument --beta: " + ANOTHER_SUBCOMMAND.format("predict", "preprocess") + 'use group="optional '
        "parameters\", or call that group's own add_argument",
    ),
    "misspelled keyword": (
        "self.parser_predict.add_argument('--beta', hepl='x')",
        "predict argument --beta: add_argument() got an unexpected keyword argument 'hepl'; did you mean help=?",
    ),
    "required in a mutually exclusive group": (
        "self.parser_predict.add_mutually_exclusive_group().add_argument('--beta', required=True)",
        "predict argument --beta: an option of a mutually exclusive group can't be required=True: make the group "
        "required instead, with add_mutually_exclusive_group(required=True)",
    ),
    "choices without the default": (
        "self.parser_predict.update_arguments('--output-format', choices=['json'])",
        "update_arguments can't set choices of --output-format/-f: default 'tsv' is not among the new choices "
        "json: pass default= too",
    ),
    "required exclusive input": (
        "self.parser_predict.update_arguments('--input-json', required=True)",
        "--input-json is one of a group of mutually exclusive options (-t / -j): make the group required instead, "
        "or remove the other option",
    ),
}


@pytest.mark.parametrize("name", RUN_SCRIPT_ERRORS)
@pytest.mark.parametrize("argv", [["--help"], ["predict", "-t", "in.tsv"]], ids=["help", "predict"])
def test_setup_error_is_one_line_from_the_run_script(project, name, argv):
    declaration, message = RUN_SCRIPT_ERRORS[name]

    proc, line = run_with(project, f"        {declaration}\n", *argv)

    assert proc.returncode == 2
    assert proc.stderr == f"src/DemoArgumentParser.py:{line}: {message}\n"


# update_arguments: choices without the default, required in an exclusive group --


def test_new_choices_without_the_default_are_refused(project):
    out = probe(
        project,
        'err = error_of(lambda: p.parser_predict.update_arguments("--output-format", choices=["json"]))\n'
        'p.parser_predict.add_argument("--mode", choices=["a", "b"], default="a")\n'
        'own = error_of(lambda: p.parser_predict.update_arguments("--mode", choices=["b", "c"]))\n'
        "usage = p.parser_predict.format_usage()\n"
        # Control: with default=, with the default among the new choices, or with no default.
        'p.parser_predict.update_arguments("--output-format", choices=["json"], default="json")\n'
        'p.parser_postprocess.update_arguments("--output-format", choices=["json"])\n'
        'p.parser_predict.add_argument("--level", type=int, choices=[1, 2])\n'
        'p.parser_predict.update_arguments("--level", choices=[3])\n'
        'print(json.dumps({"err": err, "own": own, "usage": usage, "predict": parse(p),\n'
        '                  "post": p.parse_args(["postprocess", "-j", "in.json"]).output_format}))',
    )

    assert out == {
        "err": f"ParserSetupError: {at(1)}: update_arguments can't set choices of --output-format/-f: default 'tsv' "
        "is not among the new choices json: pass default= too",
        "own": f"ParserSetupError: {at(3)}: update_arguments can't set choices of --mode: default 'a' is not among "
        "the new choices b, c: pass default= too",
        "usage": out["usage"],
        "predict": {"ok": True, "output_format": "json"},
        "post": "json",
    }
    assert "{tsv,json}" in out["usage"] and "{a,b}" in out["usage"]  # unchanged by the refused calls


def test_required_option_of_an_exclusive_group_is_refused(project):
    out = probe(
        project,
        'err = error_of(lambda: p.parser_predict.update_arguments("--input-json", required=True))\n'
        'by_letter = error_of(lambda: p.parser_predict.update_arguments("-t", required=True))\n'
        "tsv, json_in = parse(p), parse(p, input_args=('-j', 'in.json'))\n"
        # Control: required=False, and required=True on an option of no exclusive group.
        'p.parser_predict.update_arguments("--input-json", required=False)\n'
        'p.parser_predict.add_argument("--alleles")\n'
        'p.parser_predict.update_arguments("--alleles", required=True)\n'
        'print(json.dumps({"err": err, "by_letter": by_letter, "tsv": tsv, "json": json_in,\n'
        '                  "alleles": parse(p)["error"]}))',
    )

    assert out == {
        "err": f"ParserSetupError: {at(1)}: --input-json is one of a group of mutually exclusive options (-t / -j): "
        "make the group required instead, or remove the other option",
        "by_letter": f"ParserSetupError: {at(2)}: --input-tsv is one of a group of mutually exclusive options "
        "(-t / -j): make the group required instead, or remove the other option",
        "tsv": {"ok": True, "output_format": "tsv"},
        "json": {"ok": True, "output_format": "tsv"},
        "alleles": "probe_keywords.py predict: error: the following arguments are required: --alleles",
    }

    # Control: once the other option is removed, as the message suggests, required=True is allowed.
    alone = probe(
        project,
        'p.parser_predict.remove_argument("--input-tsv")\n'
        'err = error_of(lambda: p.parser_predict.update_arguments("--input-json", required=True))\n'
        'print(json.dumps({"err": err, "json": parse(p, input_args=("-j", "in.json")),\n'
        '                  "none": parse(p, input_args=())["error"]}))',
    )

    assert alone["err"] is None
    assert alone["json"] == {"ok": True, "output_format": "tsv"}
    assert alone["none"].startswith("probe_keywords.py predict: error: ")
    assert "--input-json" in alone["none"]


# -o tsv: write_results refuses a format name as the prefix ----------------------


@pytest.mark.parametrize(
    "subcommand, input_args, value",
    [
        ("predict", ("-t", "in.tsv"), "tsv"),
        ("predict", ("-t", "in.tsv"), "JSON"),
        ("postprocess", ("-j", "in.json"), "json"),
    ],
    ids=["predict tsv", "predict JSON", "postprocess json"],
)
def test_format_name_as_the_output_prefix_is_a_usage_error(project, subcommand, input_args, value):
    out = probe(
        project,
        f"print(json.dumps({{'name': parse(p, '-o', {value!r}, input_args={input_args!r}, subcommand={subcommand!r}),\n"
        f"                  'path': parse(p, '-o', './{value}', input_args={input_args!r}, subcommand={subcommand!r})}}))",
    )

    assert out["name"] == {
        "ok": False,
        "error": f"probe_keywords.py {subcommand}: error: argument --output-prefix/-o: '{value}' is a format name; "
        f"-o takes an output prefix such as 'result' (give the format with -f {value.lower()}, or name the "
        f"files {value}.* with -o ./{value})",
    }
    assert out["path"]["ok"] is True
