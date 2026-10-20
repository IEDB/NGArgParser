"""write_results took a folder for a prefix, and three parser messages said less than their neighbours.

- write_results(r, "a/b/", "tsv") wrote the hidden file a/b/.tsv, and with a
  folder adir, write_results(r, "adir", "tsv") wrote adir.tsv next to it, while
  -o refuses both. An empty prefix wrote the hidden file .tsv.
- update_arguments("--output-format", defualt=...) named the keyword without the
  suggestion add_argument gives (did you mean default=?), and took any attribute
  of the option, so option_strings= renamed it in -h only.
- postprocess said "argument --input-results-dir: not allowed with argument
  --job-desc-file", where predict names both forms (--input-tsv/-t).
- validators.py called validate_file "an existing file (its folder too)", which
  reads as if a folder passed.
"""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from ngargparser import cli
from ngargparser.result_writer import write_results

ENVELOPE = {"results": [{"type": "t", "table_columns": ["a"], "table_data": [[1]]}]}


def everything_in(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


# write_results: the prefixes -o refuses ----------------------------------------


@pytest.mark.parametrize(
    "prefix, message",
    [
        ("", "output_prefix is empty; pass an output prefix such as 'result', or None to print to stdout"),
        ("a/b/", "output_prefix 'a/b/' is a folder; pass an output prefix such as 'a/b/result'"),
        ("adir", "output_prefix 'adir' is a folder; pass an output prefix such as 'adir/result'"),
        ("adir/", "output_prefix 'adir/' is a folder; pass an output prefix such as 'adir/result'"),
        (Path("adir"), "output_prefix 'adir' is a folder; pass an output prefix such as 'adir/result'"),
        (Path(""), "output_prefix '.' is a folder; pass an output prefix such as './result'"),
    ],
    ids=["empty", "ends in a separator", "existing folder", "existing folder and separator", "Path", "Path empty"],
)
@pytest.mark.parametrize("output_format", ["tsv", "json"])
def test_write_results_refuses_a_folder_prefix(tmp_path, monkeypatch, prefix, message, output_format):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "adir").mkdir()

    with pytest.raises(ValueError) as raised:
        write_results(ENVELOPE, prefix, output_format)

    assert str(raised.value) == f"write_results: {message}"
    assert everything_in(tmp_path) == ["adir"]  # nothing written


@pytest.mark.parametrize(
    "prefix, written",
    [
        ("adir/result", "adir/result.tsv"),
        ("new/result", "new/result.tsv"),
        ("./tsv", "./tsv.tsv"),
        (Path("adir") / "result", "adir/result.tsv"),
        (Path("tsv"), "tsv.tsv"),
    ],
    ids=["in a folder", "in a new folder", "./tsv", "Path in a folder", "Path tsv"],
)
def test_control_a_prefix_still_writes(tmp_path, monkeypatch, prefix, written):
    # Control: a prefix in a folder, new or not, still writes; so do ./tsv and
    # Path("tsv"), which name the files tsv.* on purpose.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "adir").mkdir()

    assert write_results(ENVELOPE, prefix, "tsv") == [written]
    assert (tmp_path / written).read_text() == "a\n1\n"


def test_control_no_prefix_still_prints(capsys):
    assert write_results(ENVELOPE, None, "tsv") == []
    assert capsys.readouterr().out == "a\n1\n"


# The parser, in a generated project ----------------------------------------------

PRELUDE = """\
import contextlib, io, json, sys
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
    (root / "jd.json").write_text("[]\n")
    (root / "results").mkdir()
    return root


def probe(project, body):
    script = project / "probe_r9_w4.py"
    script.write_text(PRELUDE + body)
    proc = subprocess.run([sys.executable, str(script)], cwd=project, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def at(body_line):
    """The file:line a parser-setup error names for line `body_line` (from 1) of a probe body."""
    return f"probe_r9_w4.py:{PRELUDE.count(chr(10)) + body_line}"


# update_arguments: the keywords it takes -----------------------------------------

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


@pytest.mark.parametrize(
    "call, message",
    [
        (
            'p.parser_predict.update_arguments("--output-format", defualt="json")',
            "predict argument --output-format: update_arguments() got an unexpected keyword argument 'defualt'; "
            "did you mean default=?",
        ),
        (
            'p.parser_predict.update_arguments("-o", hlep="Prefix.")',
            "predict argument -o: update_arguments() got an unexpected keyword argument 'hlep'; did you mean help=?",
        ),
        (
            'p.parser_predict.update_arguments("--output-prefix", option_strings=["--out"])',
            "predict argument --output-prefix: update_arguments() got an unexpected keyword argument "
            f"'option_strings'; update_arguments takes {', '.join(UPDATE_KEYWORDS)}",
        ),
        (
            'p.parser_predict.update_arguments("--output-prefix", container=None)',
            "predict argument --output-prefix: update_arguments() got an unexpected keyword argument "
            f"'container'; update_arguments takes {', '.join(UPDATE_KEYWORDS)}",
        ),
        (
            'p.parser_predict.update_arguments("--output-format", action="store_true")',
            "predict argument --output-format: update_arguments() can't change action=: remove the option with "
            'self.parser_predict.remove_argument("--output-format"), then add_argument() the new one',
        ),
        (
            'p.parser_postprocess.update_arguments("-j", action="append")',
            "postprocess argument -j: update_arguments() can't change action=: the framework reads "
            "args.job_desc_file, so it keeps its action",
        ),
        (
            'p.parser_predict.update_arguments("--input-json", version="1.0")',
            "predict argument --input-json: update_arguments() can't set version=: only an option with "
            "action='version' has one",
        ),
    ],
    ids=["defualt", "hlep", "option_strings", "container", "action", "action read by parse_args", "version"],
)
def test_update_arguments_keyword_mistakes(project, call, message):
    out = probe(
        project,
        f"err = error_of(lambda: {call})\n"
        'print(json.dumps({"err": err, "help": p.parser_predict.format_help(), '
        '"post": p.parser_postprocess.format_help()}))',
    )

    assert out["err"] == f"ParserKeywordError: {at(1)}: {message}"
    assert "--out " not in out["help"] and "--output-prefix" in out["help"]  # nothing changed


def test_control_update_arguments_takes_add_argument_keywords(project):
    # Control: each keyword add_argument takes still changes the option.
    out = probe(
        project,
        'p.parser_predict.update_arguments("--output-prefix", help="Where.", metavar="PREFIX", default="res")\n'
        'p.parser_predict.update_arguments("--input-json", type=str, required=False)\n'
        'args = p.parse_args(["predict", "-j", "missing.json"])\n'
        'print(json.dumps({"help": p.parser_predict.format_help(), "input": args.input_json, '
        '"prefix": args.output_prefix}))',
    )

    assert "[--output-prefix PREFIX]" in out["help"]  # the usage line, the same on every Python
    assert "Where." in out["help"]
    assert out["input"] == "missing.json"  # type=str: no file check any more
    assert out["prefix"] == "res"


# postprocess names both forms of its inputs --------------------------------------


@pytest.mark.parametrize(
    "argv, message",
    [
        (
            ["postprocess", "-j", "jd.json", "-i", "results"],
            "argument --input-results-dir/-i: not allowed with argument --job-desc-file/-j",
        ),
        (["postprocess"], "one of the arguments --job-desc-file/-j --input-results-dir/-i is required"),
    ],
    ids=["both", "neither"],
)
def test_postprocess_inputs_error_names_both_forms(project, argv, message):
    proc = subprocess.run(
        [sys.executable, "src/run_demo.py", *argv], cwd=project, capture_output=True, text=True, timeout=60
    )

    assert proc.returncode == 2
    assert proc.stderr.splitlines()[-1] == f"run_demo.py postprocess: error: {message}"


def test_control_predict_inputs_error_has_the_same_form(project):
    (project / "in.tsv").write_text("a\n")
    (project / "in.json").write_text("{}\n")

    proc = subprocess.run(
        [sys.executable, "src/run_demo.py", "predict", "-t", "in.tsv", "-j", "in.json"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 2
    assert proc.stderr.splitlines()[-1] == (
        "run_demo.py predict: error: argument --input-json/-j: not allowed with argument --input-tsv/-t"
    )


# validate_file: an existing file, not a folder -----------------------------------


@pytest.mark.parametrize(
    "path", ["ngargparser/validators.py", "ngargparser/templates/example-app/validators.py"], ids=["new", "example"]
)
def test_validators_say_a_folder_does_not_pass(path):
    text = (Path(cli.__file__).resolve().parents[1] / path).read_text()

    assert "# an existing file, not a folder; returns a Path" in text
    assert "its folder too" not in text


def test_generated_validators_say_a_folder_does_not_pass(project):
    text = (project / "src" / "validators.py").read_text()

    assert "validate_file,                      # an existing file, not a folder; returns a Path" in text
