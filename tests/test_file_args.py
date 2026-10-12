"""File arguments must be paths, not argparse.FileType handles.

Python 3.14 deprecated argparse.FileType. The scaffold now validates file
arguments with core_validators.validate_file, which returns a pathlib.Path and
still rejects a missing file with argparse's exit 2. Callers open the file
themselves after parsing.
"""

import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from ngargparser import cli

PAYLOAD_DIR = Path(cli.__file__).resolve().parent


def run_script(project_dir, script, *args):
    return subprocess.run(
        [sys.executable, f"src/{script}", *args],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )


def parsed_namespace(project_dir, parser_module, parser_class, *argv):
    """Parse argv with the project's own parser and return repr() of each value."""
    code = (
        "import sys\n"
        "sys.path.insert(0, 'src')\n"
        f"from {parser_module} import {parser_class}\n"
        f"sys.argv = ['prog', *{list(argv)!r}]\n"
        f"args = {parser_class}().parse_args()\n"
        "print({k: type(v).__name__ for k, v in vars(args).items()})\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=project_dir, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def example_project(in_tmp_dir, monkeypatch):
    rc = cli.startapp_command(Namespace(project_name="example"))
    assert not rc
    project_dir = in_tmp_dir / "aa-counter"
    monkeypatch.chdir(project_dir)
    return project_dir


def test_no_filetype_left_in_scaffold_payload():
    offenders = [
        str(p.relative_to(PAYLOAD_DIR))
        for p in PAYLOAD_DIR.rglob("*")
        if p.is_file() and p.suffix in {".py", ".md"} and "FileType" in p.read_text()
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "argv",
    [
        ("predict", "-j", "missing.json"),
        ("predict", "-t", "missing.tsv"),
        ("postprocess", "--job-desc-file", "missing.json"),
    ],
)
def test_missing_file_is_rejected_by_argparse(scaffolded_project, argv):
    result = run_script(scaffolded_project, "run_demo.py", *argv)

    assert result.returncode == 2
    assert "is not a valid file" in result.stderr


def test_file_arguments_parse_to_paths(scaffolded_project, tmp_path):
    tsv = tmp_path / "in.tsv"
    tsv.write_text("peptide\nAAK\n")
    jd = tmp_path / "job_descriptions.json"
    jd.write_text("[]")

    predict = parsed_namespace(
        scaffolded_project, "DemoArgumentParser", "DemoArgumentParser", "predict", "-t", str(tsv)
    )
    postprocess = parsed_namespace(
        scaffolded_project,
        "DemoArgumentParser",
        "DemoArgumentParser",
        "postprocess",
        "--job-desc-file",
        str(jd),
    )

    assert "'input_tsv': 'PosixPath'" in predict
    assert "'job_desc_file': 'PosixPath'" in postprocess


def test_example_app_predict_reads_path_inputs(example_project):
    for flag, value in (("-t", "src/example.tsv"), ("-j", "src/example.json")):
        extra = ("-a", "L") if flag == "-t" else ()
        result = run_script(example_project, "run_aa_counter.py", "predict", flag, value, *extra)
        assert result.returncode == 0, f"{flag}: {result.stderr}"
        assert "AAcount" in result.stdout
