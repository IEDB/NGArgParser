"""A new tool's .gitattributes keeps the framework files in LF, and cli.py type-checks for
the Pythons that have tomllib.

* With core.autocrlf, a checkout had CRLF framework files, which sync replaced on every
  run. The .gitattributes `cli generate` writes now pins src/core/*.py and the Makefile
  to LF, as it pins the files bash runs.
* `mypy --python-version 3.13` stopped at the tomllib/tomli import in _toml_reader
  ("Name ... already defined"); a version check lets mypy read one import per Python.
"""

import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

from ngargparser import cli


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_gitattributes_keeps_the_framework_files_in_lf(scaffolded_project):
    lines = (scaffolded_project / ".gitattributes").read_text().splitlines()
    assert {"src/core/*.py text eol=lf", "Makefile text eol=lf"} <= set(lines)

    subprocess.run(["git", "init", "-q"], cwd=scaffolded_project, check=True)
    for path in ("src/core/NGArgumentParser.py", "src/core/configure.py", "Makefile", "configure"):
        attributes = subprocess.run(
            ["git", "check-attr", "eol", "--", path], cwd=scaffolded_project, capture_output=True, text=True, check=True
        ).stdout
        assert attributes.strip() == f"{path}: eol: lf", attributes
    # Control: a file of the tool's own is left to git's settings.
    own = subprocess.run(
        ["git", "check-attr", "eol", "--", "src/DemoArgumentParser.py"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert own.strip() == "src/DemoArgumentParser.py: eol: unspecified"


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="needs mypy (the dev group)")
def test_cli_type_checks_for_python_versions_with_and_without_tomllib(tmp_path):
    # 3.13 has tomllib; 3.9, the oldest Python ngargparser supports (the control), reads tomli.
    for version in ("3.13", "3.9"):
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--python-version",
                version,
                "--check-untyped-defs",
                "--ignore-missing-imports",
                "--follow-imports=silent",
                "--no-error-summary",
                "--cache-dir",
                str(tmp_path / "mypy-cache"),
                os.path.abspath(cli.__file__),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert run.returncode == 0, f"mypy --python-version {version}:\n{run.stdout}{run.stderr}"
