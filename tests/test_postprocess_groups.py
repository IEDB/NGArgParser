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
