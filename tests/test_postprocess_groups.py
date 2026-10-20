"""postprocess's -p must not be advertised as required.

--postprocessed-results-dir sat in a group titled "other required parameters"
while being declared without required=True, so the help text asserted
something argparse never enforced. It lives with -o/-f under "optional
parameters" instead; tools like axelf, conservancy, and rate accept -o
instead of -p or fall back to the working directory.
"""

import subprocess
import sys


def postprocess_help(project_dir):
    result = subprocess.run(
        [sys.executable, "src/run_demo.py", "postprocess", "--help"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_p_is_not_advertised_as_required(scaffolded_project):
    assert "other required parameters" not in postprocess_help(scaffolded_project)


def test_p_is_still_offered(scaffolded_project):
    assert "--postprocessed-results-dir" in postprocess_help(scaffolded_project)


def test_optional_parameters_heading_is_not_duplicated(scaffolded_project):
    # Folding -p into the existing group must not leave two identical headings.
    assert postprocess_help(scaffolded_project).count("optional parameters:") == 1


# postprocess -h described -o as "prediction result output prefix.", predict's
# text, though postprocess writes the merged result of every job. Both now read
# in sentence case, as the tool's own help does.


def test_o_help_describes_the_merged_result(scaffolded_project):
    text = " ".join(postprocess_help(scaffolded_project).split())

    assert (
        "Output prefix for the merged result files (the results of every job, combined); the extension "
        "(.tsv or .json) is added." in text
    )
    assert "prediction result" not in text


def test_predict_o_help_still_describes_the_prediction(scaffolded_project):
    # Control: predict's -o keeps its own text.
    result = subprocess.run(
        [sys.executable, "src/run_demo.py", "predict", "--help"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Output prefix for the prediction result files; the" in result.stdout
