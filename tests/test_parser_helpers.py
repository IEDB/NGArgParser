"""remove_argument and update_arguments must change what parses, not only what --help shows.

argparse records an option in three places: the parser's actions, its groups (what --help
prints) and the option-string table (what each typed name maps to, and what a new
declaration conflicts with). The helpers used to edit only the first two, so a removed
option still parsed, re-adding it crashed, and a rename never parsed. Each test runs a
probe inside a freshly generated project and prints one JSON result.
"""

import json
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
    assert out.startswith("ValueError: No option --output-fromat in predict; did you mean --output-format?")


def test_remove_from_unknown_subparser_names_the_valid_ones(project):
    out = probe(project, 'print(json.dumps(error_of(lambda: p.remove_argument("--output-format", "predcit"))))')
    assert out.startswith("ValueError:") and "use predict, preprocess or postprocess" in out


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
    assert out["err"].startswith("ValueError: update_arguments can't rename")
    assert "remove_argument('--output-format', 'predict')" in out["err"]
    assert out["old_name"] == {"ok": True, "output_format": "json"}


def test_update_misspelled_option_suggests_the_right_one(project):
    out = probe(
        project,
        'print(json.dumps(error_of(lambda: p.parser_predict.update_arguments("--output-fromat", default="json"))))',
    )
    assert out.startswith("ValueError: No option --output-fromat in predict; did you mean --output-format?")


def test_update_rejects_an_unknown_keyword(project):
    out = probe(
        project,
        'print(json.dumps(error_of(lambda: p.parser_predict.update_arguments("--output-format", defualt="json"))))',
    )
    assert out.startswith("TypeError:") and "defualt" in out


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
