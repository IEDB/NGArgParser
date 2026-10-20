"""./configure: a paths.py in another encoding, an unreadable .env, flag typos, and colours.

A flag run on a Latin-1 paths.py (a PEP 263 coding line; a plain run works)
ended in a UnicodeDecodeError traceback. A .env with mode 000 was "written
again", with mode 000 again. A misspelled flag (--alpah-path, the paths.py
spelling --alpha_path, or the flag of a dependency not declared yet) got only
argparse's usage screen and "unrecognized arguments". Colour codes went into
pipes and log files, and NO_COLOR was ignored.
"""

import os
import pty
import re
import subprocess
import sys

import pytest
from test_configure_env import make_tool_dir, run_configure

from ngargparser import cli

ANSI = re.compile(r"\x1b\[[0-9;]*m")

needs_permission_errors = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="root ignores file permissions"
)


def test_a_flag_on_a_paths_py_in_another_encoding_stops_with_one_line(scaffolded_project, tmp_path):
    alpha = make_tool_dir(tmp_path, "alpha")
    paths = scaffolded_project / "paths.py"
    content = f"# -*- coding: latin-1 -*-\n# Andr\xe9's tools\nalpha_path = {str(alpha)!r}\n".encode("latin-1")
    paths.write_bytes(content)
    run_configure(scaffolded_project)  # a plain run reads it as Python does

    result = run_configure(scaffolded_project, f"--alpha-path={tmp_path / 'y'}", expect_success=False)

    assert (result.returncode, result.stderr) == (1, "")
    assert result.stdout.splitlines()[-1] == (
        "✗ Can't update paths.py: it is iso-8859-1 text, and ./configure rewrites UTF-8 only. "
        "Save it as UTF-8 (with no coding line, or coding: utf-8), or edit it by hand. Nothing was changed."
    )
    assert paths.read_bytes() == content
    assert not (scaffolded_project / "paths.py.bak").exists()
    # Control: saved as UTF-8, the same flag rewrites it.
    paths.write_text(content.decode("latin-1").replace("latin-1", "utf-8"), encoding="utf-8")
    run_configure(scaffolded_project, f"--alpha-path={tmp_path / 'y'}")
    assert f"alpha_path='{tmp_path / 'y'}'" in paths.read_text(encoding="utf-8")


@needs_permission_errors
def test_an_env_that_cannot_be_read_is_written_again_readable(scaffolded_project, strip_ansi):
    env_file = scaffolded_project / ".env"
    env_file.chmod(0o000)
    try:
        result = subprocess.run(
            [sys.executable, "src/core/configure.py"],
            cwd=scaffolded_project,
            capture_output=True,
            text=True,
            timeout=60,
            preexec_fn=lambda: os.umask(0o027),
        )
        mode = env_file.stat().st_mode & 0o777
    finally:
        env_file.chmod(0o644)

    assert (result.returncode, result.stderr) == (0, "")
    assert "⚠  Could not read .env: Permission denied. ./configure writes it again from paths.py." in (
        strip_ansi(result.stdout).splitlines()
    )
    assert mode == 0o640  # a new file's mode under that umask
    # Control: a .env that could be read keeps its mode when it is written again.
    env_file.chmod(0o600)
    env_file.write_text(env_file.read_text() + "STALE=1\n")
    run_configure(scaffolded_project)
    assert env_file.stat().st_mode & 0o777 == 0o600


# Flag typos: one line, with a hint, exit code 2 ------------------------------


def configure_error(project, *args):
    """Run ./configure with `args`, which it should refuse: its exit code and stderr lines; paths.py must not change."""
    paths = project / "paths.py"
    before = paths.read_text() if paths.exists() else None
    result = run_configure(project, *args, expect_success=False)
    assert (paths.read_text() if paths.exists() else None) == before
    return result.returncode, result.stderr.splitlines()


@pytest.mark.parametrize(
    "args, hint",
    [
        (["--alpah-path=/x"], "--alpah-path=/x; did you mean --alpha-path?"),
        (["--alpha_path=/x"], "--alpha_path=/x; did you mean --alpha-path?"),
        (["--alpha_lib_path=/x"], "--alpha_lib_path=/x; did you mean --alpha-lib-path?"),
        (["--alpah-path", "/x"], "--alpah-path /x; did you mean --alpha-path?"),
        (["--alpha-pth=/x"], "--alpha-pth=/x; did you mean --alpha-path?"),
        (["--pepx-path=/x"], "--pepx-path=/x; pepx is not declared in paths.py: cli deps add pepx"),
        (
            ["--alpah-venv=/v", "--pepx-path=/x"],
            "--alpah-venv=/v --pepx-path=/x; did you mean --alpha-venv?; "
            "pepx is not declared in paths.py: cli deps add pepx",
        ),
    ],
    ids=["typo", "underscores", "lib-path underscores", "space", "field typo", "undeclared", "two"],
)
def test_a_misspelled_flag_gets_one_line_with_a_hint(scaffolded_project, tmp_path, args, hint):
    cli.add_deps_to_paths("paths.py", ["alpha"])
    run_configure(scaffolded_project, f"--alpha-path={make_tool_dir(tmp_path, 'alpha')}")

    assert configure_error(scaffolded_project, *args) == (2, [f"./configure: error: unrecognized arguments: {hint}"])


def test_a_dependency_flag_before_any_dependency_is_declared_names_cli_deps_add(scaffolded_project):
    assert configure_error(scaffolded_project, "--tcell-class-i-path=/opt/tc1") == (
        2,
        [
            "./configure: error: unrecognized arguments: --tcell-class-i-path=/opt/tc1; "
            "tcell-class-i is not declared in paths.py: cli deps add tcell-class-i"
        ],
    )


def test_an_argument_with_no_hint_still_gets_the_usage_screen(scaffolded_project, tmp_path):
    # Control: a word that is no flag gets argparse's usage line and error, as before.
    cli.add_deps_to_paths("paths.py", ["alpha"])
    run_configure(scaffolded_project, f"--alpha-path={make_tool_dir(tmp_path, 'alpha')}")

    returncode, lines = configure_error(scaffolded_project, "--alpha-path=/x", "extra")

    assert returncode == 2
    assert lines[0].startswith("usage: ./configure [-h]")
    assert lines[-1] == "./configure: error: unrecognized arguments: extra"


# Colours: only on a terminal, and never with NO_COLOR set -------------------


def configure_output(project, env, terminal):
    """The stdout of a ./configure run that warns (a dependency without a virtualenv), on a terminal or a pipe."""
    command = [sys.executable, "src/core/configure.py"]
    if not terminal:
        result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout
    leader, follower = pty.openpty()
    try:
        proc = subprocess.Popen(command, cwd=project, env=env, stdout=follower, stderr=subprocess.DEVNULL)
        os.close(follower)
        chunks = []
        while True:
            try:
                chunk = os.read(leader, 4096)
            except OSError:  # the run ended and closed the terminal
                break
            if not chunk:
                break
            chunks.append(chunk)
        assert proc.wait(timeout=60) == 0
    finally:
        os.close(leader)
    return b"".join(chunks).decode()


@pytest.mark.skipif(os.name != "posix", reason="needs a pseudo-terminal")
def test_colours_only_on_a_terminal_without_no_color(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["alpha"])
    run_configure(scaffolded_project, f"--alpha-path={make_tool_dir(tmp_path, 'alpha', with_venv=False)}")
    env = {key: value for key, value in os.environ.items() if key != "NO_COLOR"}

    piped = configure_output(scaffolded_project, env, terminal=False)
    assert "has no virtualenv" in piped and not ANSI.search(piped)
    assert ANSI.search(configure_output(scaffolded_project, env, terminal=True))
    no_color = configure_output(scaffolded_project, {**env, "NO_COLOR": "1"}, terminal=True)
    assert "has no virtualenv" in no_color and not ANSI.search(no_color)
    # An empty NO_COLOR counts as unset (no-color.org).
    assert ANSI.search(configure_output(scaffolded_project, {**env, "NO_COLOR": ""}, terminal=True))
