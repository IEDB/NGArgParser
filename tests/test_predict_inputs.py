"""The scaffolded predict subcommand must reject two inputs at once.

Passing both --input-tsv and --input-json used to be silently accepted: the
run script's `if args.input_tsv: ... elif args.input_json:` chain simply used
the TSV and dropped the JSON, exiting 0 with believable output computed from
only one of the two inputs. Nothing downstream could detect that.
"""

import subprocess
import sys


def run_predict(project_dir, *args):
    return subprocess.run(
        [sys.executable, "src/run_demo.py", "predict", *args],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )


def _inputs(tmp_path):
    tsv = tmp_path / "in.tsv"
    tsv.write_text("peptide\tx\nAAK\t1\n")
    json_file = tmp_path / "in.json"
    json_file.write_text('{"peptide": ["AAK"], "amino_acid": "A"}')
    return tsv, json_file


def test_both_inputs_are_rejected(scaffolded_project, tmp_path):
    tsv, json_file = _inputs(tmp_path)

    result = run_predict(scaffolded_project, "-t", str(tsv), "-j", str(json_file))

    assert result.returncode == 2
    assert "not allowed with" in result.stderr


def test_single_input_still_parses(scaffolded_project, tmp_path):
    tsv, json_file = _inputs(tmp_path)

    for flag, value in (("-t", tsv), ("-j", json_file)):
        result = run_predict(scaffolded_project, flag, str(value))
        assert result.returncode == 0, f"{flag} alone should parse: {result.stderr}"


def test_help_still_lists_both_inputs(scaffolded_project):
    result = run_predict(scaffolded_project, "--help")

    assert result.returncode == 0
    assert "--input-tsv" in result.stdout
    assert "--input-json" in result.stdout
    # argparse renders exclusivity as (a | b) in the usage line.
    assert "--input-tsv INPUT_TSV | --input-json INPUT_JSON" in result.stdout


def test_input_options_render_above_output_options(scaffolded_project):
    # You supply input before output, so the help should read in that order.
    # argparse orders groups by creation, so this only holds if the framework
    # base class creates the input group before adding the output arguments.
    stdout = run_predict(scaffolded_project, "--help").stdout

    assert stdout.index("input options:") < stdout.index("output options:")
