"""The local shellcheck fallback runs the shellcheck-py version CI installs.

Without shellcheck on PATH, the setup-script test ran `uvx --from shellcheck-py`,
whichever version the uv cache held, while CI installs SHELLCHECK_PY_VERSION:
the two could check with different shellchecks.
"""

import os
import re
from pathlib import Path

import pytest
from test_setup_script_modules import find_shellcheck

CI = Path(__file__).resolve().parents[1] / ".gitlab-ci.yml"


@pytest.mark.skipif(os.name != "posix", reason="runs a stand-in uvx")
def test_the_uvx_fallback_runs_the_shellcheck_py_version_ci_installs(tmp_path):
    (version,) = re.findall(r'^\s*SHELLCHECK_PY_VERSION:\s*"([^"]+)"', CI.read_text(), flags=re.MULTILINE)
    uvx = tmp_path / "uvx"
    uvx.write_text("#!/bin/sh\nexit 0\n")  # its --version probe passes
    uvx.chmod(0o755)

    command = find_shellcheck(lambda name: str(uvx) if name == "uvx" else None)

    assert command == [str(uvx), "--offline", "--from", f"shellcheck-py=={version}", "shellcheck"]
