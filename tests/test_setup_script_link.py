"""setup_<dep>_env.sh reads every value from the .env of the version `current` named when it started.

It finds the real folder behind <dep>_path once (_ng_real) and reads its .env
with sed, once per value. tests/test_setup_script.py moves `current`
before each sed call, so the APP_ROOT check already throws away a .env read
through the link. Here `current` moves after each call: the APP_ROOT read
still matches, and only reading every later value from _ng_real keeps
APP_VENV and APP_ENTRY from two versions apart. Reading them through the
link passed every other test.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_configure_env import deploy_version, run_configure

from ngargparser import cli

# The in_tmp_dir fixture hides every program from shutil.which.
WHICH = shutil.which
SHELLS = [shell for shell in ("sh", "bash", "dash") if WHICH(shell)]

pytestmark = pytest.mark.skipif(os.name != "posix" or WHICH("bash") is None, reason="needs bash and symlinks")

# A sed that reads first, then moves `current` to the other version.
SED_THEN_FLIP = """\
#!/bin/sh
{sed} "$@"
status=$?
case $({readlink} {link}) in
    1.0) {ln} -sfn 1.1 {link} ;;
    *) {ln} -sfn 1.0 {link} ;;
esac
exit $status
"""


@pytest.mark.parametrize("shell", SHELLS)
def test_values_come_from_the_version_current_named_first_while_it_moves_after_each_read(
    scaffolded_project, tmp_path, shell
):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")
    deploy_version(apps, "1.1", "run_callee_v2.py")  # `current` names 1.1
    current = apps / "callee" / "current"
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={current}")
    shim = tmp_path / "flip-after-bin"
    shim.mkdir()
    sed = shim / "sed"
    sed.write_text(SED_THEN_FLIP.format(readlink=WHICH("readlink"), ln=WHICH("ln"), sed=WHICH("sed"), link=current))
    sed.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}

    proc = subprocess.run(
        [WHICH(shell), "-c", 'set -eu; . ./setup_callee_env.sh; echo "$CALLEE_VENV|$CALLEE_ENTRY"'],
        cwd=scaffolded_project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert Path(os.readlink(current)).name != "1.1"  # it did move while the script read
    version = apps / "callee" / "1.1"
    assert proc.stdout.strip().split("|") == [str(version / ".venv"), str(version / "src" / "run_callee_v2.py")]
