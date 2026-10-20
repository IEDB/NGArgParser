""".ngargparser/paths.lock: ./configure names the fix in the words `cli deps` uses.

`cli deps` now says to make a read-only .ngargparser writable for its owner
(chmod u+rwx), or for a group (chmod g+rwx), not for everyone (chmod a+rwx), and
to move away a .ngargparser that is a file. ./configure, which takes the same
lock, still said chmod a+rwx for both.
"""

import os
import subprocess
import sys

from test_configure_env import make_tool_dir, run_configure
from test_paths_lock import CLI, lock_lines

from ngargparser import cli


def test_a_state_folder_that_is_a_file_gets_the_words_cli_deps_uses(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["alpha"])
    state = scaffolded_project / ".ngargparser"
    for path in sorted(state.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    state.rmdir()
    state.write_text("not a folder\n")
    package_root = os.path.dirname(os.path.dirname(cli.__file__))
    env = {**os.environ, "PYTHONPATH": package_root, "NGARGPARSER_NO_UPDATE_CHECK": "1"}

    deps = subprocess.run(
        [sys.executable, "-c", CLI, "deps", "add", "beta"],
        cwd=scaffolded_project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    configure = run_configure(
        scaffolded_project, f"--alpha-path={make_tool_dir(tmp_path, 'alpha')}", expect_success=False
    )

    expected = [
        "✗ Cannot create .ngargparser/paths.lock: .ngargparser is a file, not a folder. "
        "Move it away (mv .ngargparser .ngargparser.old), then run the command again. Nothing was changed."
    ]
    assert (deps.returncode, configure.returncode) == (1, 1)
    assert lock_lines(configure) == lock_lines(deps) == expected
