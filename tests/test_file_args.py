"""File arguments must be paths, not argparse.FileType handles.

Python 3.14 deprecated argparse.FileType. The scaffold now validates file
arguments with core_validators.validate_file, which returns a pathlib.Path and
still rejects a missing file with argparse's exit 2. Callers open the file
themselves after parsing.
"""

import json
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
    assert f"'{argv[-1]}' does not exist." in result.stderr


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


# The example app gets the same base class as a blank tool. It used to ship an
# old copy that `cli sync` then replaced, after which its preprocess failed.


def test_example_app_core_parser_is_the_real_base_class(example_project):
    installed = example_project / "src" / "core" / "NGArgumentParser.py"
    assert installed.read_bytes() == (PAYLOAD_DIR / "NGArgumentParser.py").read_bytes()


def test_example_app_preprocess_writes_job_descriptions(example_project):
    result = run_script(example_project, "run_aa_counter.py", "preprocess", "-j", "src/example.json", "-o", "out")
    assert result.returncode == 0, result.stderr

    jobs = json.loads((example_project / "out" / "job_descriptions.json").read_text())
    # Leave out the list of the files preprocess wrote (.aa-counter-preprocess-files).
    params = sorted(p for p in (example_project / "out" / "predict-inputs" / "params").iterdir() if p.suffix == ".json")
    predict, final = jobs[:-1], jobs[-1]
    assert len(predict) == len(params) > 0
    assert all(j["job_type"] == "prediction" and " predict -j " in j["shell_cmd"] for j in predict)
    assert final["job_type"] == "postprocess"
    assert final["depends_on_job_ids"] == [j["job_id"] for j in predict]
    run_path = str(example_project / "src" / "run_aa_counter.py")
    assert all(Path(j["shell_cmd"].split()[0]).resolve() == Path(run_path).resolve() for j in jobs)


def test_example_app_pipeline_runs_with_a_dependency(example_project):
    # With a dependency declared and filled in, preprocess builds <out>/<dep>/ and
    # <out>/aa-counter/. Every job it writes must run: the defaults used to point at
    # out/predict-inputs/, which that layout never creates (FileNotFoundError).
    cli.add_deps_to_paths("paths.py", ["pepx"])
    paths = example_project / "paths.py"
    paths.write_text(paths.read_text().replace("pepx_path=None", f"pepx_path='{example_project}'"))

    result = run_script(example_project, "run_aa_counter.py", "preprocess", "-j", "src/example.json", "-o", "out")
    assert result.returncode == 0, result.stderr

    jobs = json.loads((example_project / "out" / "job_descriptions.json").read_text())
    assert len(jobs) > 1
    for job in jobs:
        script, *args = job["shell_cmd"].split()
        ran = subprocess.run([sys.executable, script, *args], cwd=example_project, capture_output=True, text=True)
        assert ran.returncode == 0, ran.stderr
        assert all(Path(output).exists() for output in job["expected_outputs"])
    tree = example_project / "out" / "aa-counter"
    assert sorted(p.name for p in (tree / "predict-inputs" / "params").iterdir())
    assert (tree / "aggregate" / "final-result.json").exists()
    assert not (example_project / "out" / "predict-inputs").exists()


def test_example_app_failed_preprocess_leaves_no_folders(example_project):
    result = run_script(example_project, "run_aa_counter.py", "preprocess", "-o", "out", "-j", "nonexist.json")
    assert result.returncode == 2
    assert not (example_project / "out").exists()


# The example's postprocess with --input-results-dir: -o alone used to crash with
# TypeError (it built a default path from the missing -p first).


@pytest.fixture
def example_results(example_project):
    run_script(example_project, "run_aa_counter.py", "preprocess", "-j", "src/example.json", "-o", "../jobs")
    jobs = json.loads((example_project / ".." / "jobs" / "job_descriptions.json").read_text())
    for job in jobs[:-1]:
        script, *args = job["shell_cmd"].split()
        assert subprocess.run([sys.executable, script, *args], cwd=example_project).returncode == 0
    return example_project / ".." / "jobs"


def test_example_postprocess_input_dir_with_output_prefix(example_project, example_results):
    result = run_script(
        example_project, "run_aa_counter.py", "postprocess", "-i", "../jobs/predict-outputs", "-o", "../merged"
    )

    assert result.returncode == 0, result.stderr
    assert json.loads((example_project / ".." / "merged.json").read_text())["results"][0]["table_data"]


def test_example_postprocess_input_dir_needs_an_output(example_project, example_results):
    result = run_script(example_project, "run_aa_counter.py", "postprocess", "-i", "../jobs/predict-outputs")

    assert result.returncode == 2
    assert "with --input-results-dir, give -o <prefix> or -p <folder>" in result.stderr


def test_example_postprocess_input_dir_with_results_dir(example_project, example_results):
    # Control: -p alone still writes final-result.json into that folder.
    (example_project / ".." / "final").mkdir()
    result = run_script(
        example_project, "run_aa_counter.py", "postprocess", "-i", "../jobs/predict-outputs", "-p", "../final"
    )

    assert result.returncode == 0, result.stderr
    assert (example_project / ".." / "final" / "final-result.json").exists()


# postprocess -o used to require its folder to exist ("... is not a valid
# directory."), while predict -o creates missing folders when it writes. Both
# now create them when the result is written.


def test_example_postprocess_output_prefix_creates_missing_folders(example_project, example_results):
    result = run_script(
        example_project,
        "run_aa_counter.py",
        "postprocess",
        "-j",
        "../jobs/job_descriptions.json",
        "-o",
        "../runs/nonexist/dir/prefix",
    )

    assert result.returncode == 0, result.stderr
    written = example_project / ".." / "runs" / "nonexist" / "dir" / "prefix.json"
    assert json.loads(written.read_text())["results"][0]["table_data"]
    assert "Wrote ../runs/nonexist/dir/prefix.json" in result.stderr


def test_postprocess_output_prefix_into_a_missing_folder_parses_to_text(scaffolded_project, tmp_path):
    # Text, as predict gives -o (postprocess gave a Path), and nothing is created while parsing.
    jd = tmp_path / "job_descriptions.json"
    jd.write_text("[]")

    parsed = parsed_namespace(
        scaffolded_project,
        "DemoArgumentParser",
        "DemoArgumentParser",
        "postprocess",
        "-j",
        str(jd),
        "-o",
        "missing/dir/prefix",
    )

    assert "'output_prefix': 'str'" in parsed
    assert not (scaffolded_project / "missing").exists()


# The sample app's -a had no validator, so `predict -t src/example.tsv -a '('`
# crashed with "re.error: missing ), unterminated subpattern". Its parser also
# imported core.core_validators directly, while the scaffold imports
# src/validators.py, where a tool's own validators go.


def test_example_app_refuses_a_bad_amino_acid_pattern(example_project):
    result = run_script(example_project, "run_aa_counter.py", "predict", "-t", "src/example.tsv", "-a", "(")

    assert result.returncode == 2
    assert result.stderr.splitlines()[-1] == (
        "run_aa_counter.py predict: error: argument --amino-acid/-a: '(' is not a valid regular expression: "
        "missing ), unterminated subpattern at position 0"
    )


# predict -j src/example.json -a P counted L, the amino acid the JSON names, and
# said nothing about -a.


def test_example_app_refuses_a_with_a_json_input(example_project):
    refused = run_script(example_project, "run_aa_counter.py", "predict", "-j", "src/example.json", "-a", "P")
    empty = run_script(example_project, "run_aa_counter.py", "predict", "-j", "src/example.json", "-a", "")
    # Control: -a with -t, and -j alone, still count.
    tsv = run_script(example_project, "run_aa_counter.py", "predict", "-t", "src/example.tsv", "-a", "P")
    json_alone = run_script(example_project, "run_aa_counter.py", "predict", "-j", "src/example.json")

    message = (
        "run_aa_counter.py predict: error: -a sets the amino acid for -t input; a -j input carries its own amino_acid"
    )
    assert (refused.returncode, refused.stdout, refused.stderr.splitlines()[-1:]) == (2, "", [message])
    assert (empty.returncode, empty.stderr.splitlines()[-1:]) == (2, [message])
    assert tsv.returncode == 0 and tsv.stdout.startswith("peptide\tlength\tAAcount(P)\n")
    assert json_alone.returncode == 0 and json_alone.stdout.startswith("peptide\tlength\tAAcount(L)\n")


def test_example_app_parser_imports_its_own_validators(example_project):
    parser = (example_project / "src" / "AACounterArgumentParser.py").read_text()

    assert "\nimport validators\n" in parser
    assert "core.core_validators" not in parser
    assert "def validate_amino_acid(" in (example_project / "src" / "validators.py").read_text()


def test_example_app_reports_a_setup_error_in_one_line(example_project):
    parser = example_project / "src" / "AACounterArgumentParser.py"
    parser.write_text(
        parser.read_text() + '\n        self.parser_predict.update_arguments("--amino-acd", default="L")\n'
    )
    line = len(parser.read_text().splitlines())

    result = run_script(example_project, "run_aa_counter.py", "--help")

    assert result.returncode == 2
    assert result.stderr == (
        f"src/AACounterArgumentParser.py:{line}: No option --amino-acd in predict; did you mean --amino-acid?\n"
    )
