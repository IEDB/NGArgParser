"""./configure: stopped by a signal, run with stdout closed or into a pipe nobody reads, --<dep>-python, Latin-1.

SIGTERM or SIGHUP while a run moved its files in left the hidden temporary files
it staged (..env.*.tmp, .paths.py.*.tmp). `./configure >&-` ended in
AttributeError: 'NoneType' object has no attribute 'isatty', and
`./configure ... | :` in a BrokenPipeError and exit code 120, although every
file was written. <dep>_python is a paths.py setting with no flag, and the
typo hint for --<dep>-python named the path flag. For a paths.py that is not
UTF-8, ./configure suggested `cli deps remove`, which refuses such a file.
"""

import os
import signal
import subprocess
import sys
import time
from argparse import Namespace

import pytest
from test_configure_env import env_values, run_configure

from ngargparser import cli

pytestmark = pytest.mark.skipif(os.name != "posix", reason="needs POSIX signals and pipes")

# configure.main() with commit_file made slow: it waits at its first call (the
# .env) until the file "go" exists, every file of the run staged by then.
SLOW_COMMIT = """\
import importlib.util, os, signal, sys, time
spec = importlib.util.spec_from_file_location("configure_slow_commit", "src/core/configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)
if sys.argv[1] == "ignored":  # nohup ./configure
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
else:  # as from a terminal, whatever the test run was started with
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGHUP, signal.SIG_DFL)
commit_file = configure.commit_file
def slow_commit_file(staged, path):
    open("committing", "w").close()
    deadline = time.monotonic() + 30
    while not os.path.exists("go") and time.monotonic() < deadline:
        time.sleep(0.05)
    return commit_file(staged, path)
configure.commit_file = slow_commit_file
sys.argv = ["src/core/configure.py", *sys.argv[2:]]
configure.main()
"""


def hidden_temp_files(project):
    return sorted(path.name for path in project.iterdir() if path.name.startswith(".") and path.name.endswith(".tmp"))


def start_slow_run(project, how, *args):
    (project / "slow_commit.py").write_text(SLOW_COMMIT)
    run = subprocess.Popen(
        [sys.executable, "slow_commit.py", how, *args],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while not (project / "committing").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (project / "committing").exists(), run.communicate()
    return run


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_a_signal_during_the_commit_leaves_no_temporary_files(scaffolded_project, tmp_path, signum):
    cli.add_deps_to_paths("paths.py", ["callee"])
    paths_before = (scaffolded_project / "paths.py").read_bytes()
    run = start_slow_run(scaffolded_project, "default", f"--callee-path={tmp_path}")
    staged = hidden_temp_files(scaffolded_project)
    assert [name for name in staged if name.startswith(("..env.", ".paths.py."))], staged  # staged, not moved in yet

    run.send_signal(signum)
    out, err = run.communicate(timeout=60)

    assert run.returncode == 128 + signum, out + err
    assert hidden_temp_files(scaffolded_project) == []
    assert (scaffolded_project / "paths.py").read_bytes() == paths_before
    assert "Traceback" not in err


def test_an_ignored_sighup_stays_ignored(scaffolded_project, tmp_path):
    # Control: under nohup the run goes on, and writes every file.
    cli.add_deps_to_paths("paths.py", ["callee"])
    run = start_slow_run(scaffolded_project, "ignored", f"--callee-path={tmp_path}")

    run.send_signal(signal.SIGHUP)
    time.sleep(0.3)
    (scaffolded_project / "go").touch()
    out, err = run.communicate(timeout=60)

    assert run.returncode == 0, out + err
    assert hidden_temp_files(scaffolded_project) == []
    assert env_values(scaffolded_project)["CALLEE_PATH"] == str(tmp_path)


# Closed stdout and a pipe nobody reads -------------------------------------------


def test_a_closed_stdout_still_configures_the_tool(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["callee"])

    run = subprocess.run(
        ["sh", "-c", 'exec "$0" src/core/configure.py "$1" >&-', sys.executable, f"--callee-path={tmp_path}"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert (run.returncode, run.stderr) == (0, "")
    assert env_values(scaffolded_project)["CALLEE_PATH"] == str(tmp_path)
    assert (scaffolded_project / "setup_callee_env.sh").is_file()


def configure_into_a_closed_pipe(project, *args):
    """Run ./configure with stdout a pipe whose reading end is closed, as `./configure | :` once `:` exits."""
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        return subprocess.run(
            [sys.executable, "src/core/configure.py", *args],
            cwd=project,
            stdout=write_end,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
    finally:
        os.close(write_end)


def test_a_pipe_nobody_reads_gets_the_runs_own_exit_code_quietly(scaffolded_project, tmp_path):
    # More output than a pipe write buffer, so a print in the middle of the run meets the closed pipe too.
    names = [f"dep{i}" for i in range(60)]
    cli.add_deps_to_paths("paths.py", names)
    flags = [f"--{name}-path={tmp_path / name}" for name in names]

    run = configure_into_a_closed_pipe(scaffolded_project, *flags)

    assert (run.returncode, run.stderr) == (0, "")
    values = env_values(scaffolded_project)
    assert [values.get(f"{name.upper()}_PATH") for name in names] == [str(tmp_path / name) for name in names]
    assert all((scaffolded_project / f"setup_{name}_env.sh").is_file() for name in names)
    assert f"dep59_path='{tmp_path / 'dep59'}'" in (scaffolded_project / "paths.py").read_text()

    # A run that stops keeps its exit code (1), and --help its own (0).
    cli.add_deps_to_paths("paths.py", ["unset"])
    stopped = configure_into_a_closed_pipe(scaffolded_project)
    assert (stopped.returncode, stopped.stderr) == (1, "")
    help_run = configure_into_a_closed_pipe(scaffolded_project, "--help")
    assert (help_run.returncode, help_run.stderr) == (0, "")


# --<dep>-python ----------------------------------------------------------------


def test_the_python_setting_has_a_flag_like_the_other_fields(scaffolded_project, tmp_path, strip_ansi):
    cli.add_deps_to_paths("paths.py", ["tool-b"])
    tool = tmp_path / "tool-b"
    tool.mkdir()
    run_configure(scaffolded_project, f"--tool-b-path={tool}")

    output = strip_ansi(run_configure(scaffolded_project, "--tool-b-python=/usr/bin/python3").stdout)

    assert "* Set tool_b_python = '/usr/bin/python3' in paths.py" in output.splitlines()
    paths = (scaffolded_project / "paths.py").read_text().splitlines()
    assert f"tool_b_path='{tool}'" in paths  # the path is left alone
    assert paths[paths.index(f"tool_b_path='{tool}'") + 1] == "tool_b_python='/usr/bin/python3'"
    assert env_values(scaffolded_project)["TOOL_B_PYTHON"] == "/usr/bin/python3"
    assert "export TOOL_B_PYTHON=/usr/bin/python3" in (scaffolded_project / "setup_tool_b_env.sh").read_text()
    assert "--tool-b-python" in run_configure(scaffolded_project, "--help").stdout
    # A typo gets the python flag as its hint, not the path flag.
    typo = run_configure(scaffolded_project, "--tool-b-pyhton=/usr/bin/python3", expect_success=False)
    assert typo.returncode == 2
    assert typo.stderr.strip().endswith("did you mean --tool-b-python?")


# A paths.py that is not UTF-8 ------------------------------------------------------


def test_a_paths_py_that_is_not_utf8_is_saved_as_utf8_before_the_suggested_commands(scaffolded_project, strip_ansi):
    paths = scaffolded_project / "paths.py"
    paths.write_bytes(b"# -*- coding: latin-1 -*-\n# caf\xe9\nx_path=None\nx_venv=None\n")

    run = run_configure(scaffolded_project, expect_success=False)

    assert run.returncode == 1
    assert strip_ansi(run.stdout).splitlines()[-2:] == [
        "✗ x_path is None in paths.py. paths.py is iso-8859-1 text, and ./configure and cli deps change "
        "UTF-8 text only: save it as UTF-8 first (with no coding line, or coding: utf-8). "
        "Then set it with './configure --x-path=<path>', or run 'cli deps remove x' to drop this dependency.",
        "  Nothing was changed.",
    ]
    # Both commands do refuse it as it is.
    assert cli.deps_command(Namespace(deps_action="remove", names=["x"])) == 1
    # Control: saved as UTF-8, the line is as before.
    paths.write_text("# café\nx_path=None\nx_venv=None\n", encoding="utf-8")
    run = run_configure(scaffolded_project, expect_success=False)
    assert strip_ansi(run.stdout).splitlines()[-2] == (
        "✗ x_path is None in paths.py. Set it with './configure --x-path=<path>', "
        "or run 'cli deps remove x' to drop this dependency."
    )
