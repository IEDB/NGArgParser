"""`cli sync`'s test drive (the tool's help screens before and after the update): the
environment it runs them in, an entry script that is not a Python script, the timeout's
range, a help screen that starts a program in the background, and a .venv python that
cannot be run.
"""

import hashlib
import os
import re
import stat
import sys
import time

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
PARSER_FILE = "src/DemoArgumentParser.py"
ENTRY = "src/run_demo.py"


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def tree_state(root):
    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative:
            continue
        if path.is_dir():
            dirs.add(relative)
        else:
            files[relative] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    return files, dirs


def break_after_sync(project):
    """Passes the help screens before a sync and fails them after it (see test_sync_files.py)."""
    core = project / CORE_FILE
    core.write_text(core.read_text() + "\nLOCAL_THING = 1\n")
    parser = project / PARSER_FILE
    parser.write_text("from core.NGArgumentParser import LOCAL_THING  # noqa: F401\n" + parser.read_text())


def make_stale(project):
    core = project / CORE_FILE
    core.write_text(core.read_text() + "\n# a local edit that sync will overwrite\n")


def set_entry(project, entry):
    pyproject = project / "pyproject.toml"
    pyproject.write_text(re.sub(r'(?m)^entry = ".*"$', f'entry = "{entry}"', pyproject.read_text()))


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


# --- 3. The shell's PYTHONPATH -------------------------------------------------------------------


def test_the_shells_pythonpath_does_not_reach_the_help_screens(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch, tmp_path_factory
):
    shadow = tmp_path_factory.mktemp("shadow")
    (shadow / "dotenv.py").write_text('raise ImportError("shadow")\n')
    monkeypatch.setenv("PYTHONPATH", str(shadow))
    # The shadow module really stops the tool when PYTHONPATH reaches it.
    import subprocess

    shadowed = subprocess.run([sys.executable, ENTRY, "--help"], cwd=runnable_project, capture_output=True, text=True)
    assert shadowed.returncode != 0 and "ImportError: shadow" in shadowed.stderr
    break_after_sync(runnable_project)
    before = tree_state(runnable_project)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert "✗ Sync rolled back: these commands worked before the sync and fail after it." in out
    assert "shadow" not in out
    assert tree_state(runnable_project) == before


def test_the_help_screens_run_as_make_builds_startup_check_runs_them(runnable_project, monkeypatch, tmp_path):
    # The environment each help screen gets: the shell's, without what make build leaves out.
    seen = tmp_path / "env.txt"
    (runnable_project / ENTRY).write_text(
        "import os, sys\n"
        f"open({str(seen)!r}, 'a').write(repr({{k: os.environ.get(k) for k in "
        "('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONNOUSERSITE', 'APP_ROOT', 'KEEP_ME')}) + '\\n')\n"
    )
    for name in ("PYTHONPATH", "PYTHONSTARTUP"):
        monkeypatch.setenv(name, "/nowhere")
    monkeypatch.setenv("KEEP_ME", "kept")

    results = cli._smoke_check()["results"]

    assert len(results) == 4 and all(passed for passed, _error in results.values())
    first = eval(seen.read_text().splitlines()[0])  # noqa: S307 (a dict the script above wrote)
    assert os.path.realpath(first.pop("APP_ROOT")) == os.path.realpath(runnable_project)
    assert first == {
        "PYTHONPATH": None,
        "PYTHONHOME": None,
        "PYTHONSTARTUP": None,
        "PYTHONNOUSERSITE": "1",
        "KEEP_ME": "kept",
    }


# --- 5. An entry that is not a Python script ------------------------------------------------------


def shell_entry(project, executable=True):
    script = project / "bin" / "run.sh"
    script.parent.mkdir(exist_ok=True)
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" {ENTRY} "$@"\n')
    script.chmod(0o755 if executable else 0o644)
    set_entry(project, "bin/run.sh")


@pytest.mark.skipif(os.name != "posix", reason="runs a #!/bin/sh entry")
def test_a_shell_script_entry_runs_by_itself_and_its_break_rolls_the_sync_back(
    runnable_project, sync_args, strip_ansi, capsys
):
    shell_entry(runnable_project)
    break_after_sync(runnable_project)
    before = tree_state(runnable_project)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert "✗ Sync rolled back: these commands worked before the sync and fail after it.\n" in out
    assert "\n  bin/run.sh --help\n      ImportError: cannot import name 'LOCAL_THING'" in out
    assert "SyntaxError" not in out
    assert tree_state(runnable_project) == before


@pytest.mark.skipif(os.name != "posix", reason="runs a #!/bin/sh entry")
def test_a_shell_script_entry_that_works_verifies_the_sync(runnable_project, sync_args, strip_ansi, capsys):
    shell_entry(runnable_project)
    make_stale(runnable_project)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "✓ Verified: every help screen that worked before the sync still works (4 of 4)." in out


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_a_shell_script_entry_that_is_not_executable_stops_the_sync(runnable_project, sync_args, strip_ansi, capsys):
    shell_entry(runnable_project, executable=False)
    make_stale(runnable_project)
    before = tree_state(runnable_project)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert (
        '✗ Sync cannot check that the tool still starts after the update: entry = "bin/run.sh" under '
        "[tool.ngargparser] in pyproject.toml is not a Python script and not executable (chmod +x bin/run.sh). "
        "Fix it, then run cli sync again (or cli sync --no-verify to update without the check). Nothing was changed.\n"
    ) in out
    assert tree_state(runnable_project) == before


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_an_entry_with_a_python_line_and_no_py_name_still_runs_with_the_venv_python(
    runnable_project, sync_args, strip_ansi, capsys
):
    # Control for the rule: the #! line decides, so this one runs with python although it is not executable.
    script = runnable_project / "bin" / "run"
    script.parent.mkdir()
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, runpy, sys\n"
        "src = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'src')\n"
        "sys.path.insert(0, src)\n"
        "runpy.run_path(os.path.join(src, 'run_demo.py'), run_name='__main__')\n"
    )
    script.chmod(0o644)
    set_entry(runnable_project, "bin/run")

    results = cli._smoke_check()["results"]

    assert results and all(passed for passed, _error in results.values()), results


# --- 6a. The timeout's range -----------------------------------------------------------------------


@pytest.mark.parametrize("value", ["9999999", "86401", "0", "-5", "inf"])
def test_a_timeout_out_of_range_is_said_up_front_and_the_default_is_used(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch, value
):
    make_stale(runnable_project)
    monkeypatch.setenv("NGARGPARSER_SYNC_HELP_TIMEOUT", value)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert (
        f"Tool: demo\n⚠ NGARGPARSER_SYNC_HELP_TIMEOUT={value} is not a number of seconds above 0 and at most 86400, "
        "so each help screen may take 60s.\n"
    ) in out
    assert "Error during synchronization" not in out
    assert "✓ Verified" in out


def test_the_largest_timeout_allowed_is_used(monkeypatch):
    monkeypatch.setenv("NGARGPARSER_SYNC_HELP_TIMEOUT", "86400")
    assert cli._help_screen_timeout() == (86400, None)


# --- 6d. A program a help screen starts in the background -------------------------------------------


def gone(pid):
    """True when process `pid` has ended (a zombie waiting to be reaped counts as ended)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="reads /proc")
def test_a_program_a_help_screen_starts_does_not_outlive_its_timeout(runnable_project, monkeypatch, tmp_path):
    pid_file = tmp_path / "child.pid"
    (runnable_project / ENTRY).write_text(
        "import subprocess, time\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setenv("NGARGPARSER_SYNC_HELP_TIMEOUT", "1")
    child = None
    try:
        started = time.monotonic()
        results = cli._smoke_check()["results"]
        assert results == {f"{ENTRY} --help": (False, "timed out after 1s")}
        assert time.monotonic() - started < 30
        child = int(pid_file.read_text())
        deadline = time.monotonic() + 10
        while not gone(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert gone(child), "the program the help screen started is still running"
    finally:
        if child and not gone(child):
            os.kill(child, 9)


# --- 6e. A .venv python that cannot be run -----------------------------------------------------------


def broken_venv_python(project, content, mode):
    python = project / ".venv" / "bin" / "python"  # a file of its own, never a link to a real python
    python.parent.mkdir(parents=True, exist_ok=True)
    if python.is_symlink():  # never write through a link: it can lead to a shared interpreter
        python.unlink()
    python.write_text(content)
    python.chmod(mode)
    return python


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="file modes")
def test_a_venv_python_that_is_not_executable_stops_the_sync_and_names_uv_sync(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    broken_venv_python(scaffolded_project, f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', 0o644)
    make_stale(scaffolded_project)
    before = tree_state(scaffolded_project)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert (
        "✗ Sync needs the tool's .venv to check that the tool still starts after the update, and .venv/bin/python is "
        "not executable. Run 'uv sync', then cli sync again (or cli sync --no-verify to update without the check). "
        "Nothing was changed.\n"
    ) in out
    assert tree_state(scaffolded_project) == before


@pytest.mark.skipif(os.name != "posix", reason="exec format errors")
def test_a_venv_python_that_cannot_start_names_uv_sync_in_the_could_not_verify_line(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    broken_venv_python(scaffolded_project, "not a program\n", 0o755)  # no #! line: exec format error
    make_stale(scaffolded_project)

    assert cli.sync_command(sync_args()) == 0

    err = strip_ansi(capsys.readouterr().err)  # a warning: on stderr
    line = re.search(r"⚠ Could not verify that the tool still runs: .*\n", err).group(0)
    assert line.startswith("⚠ Could not verify that the tool still runs: it did not start before the sync either (")
    assert "Exec format error" in line and line.endswith(
        ".venv/bin/python' (run `uv sync` to make the .venv again)).\n"
    )
