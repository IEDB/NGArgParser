"""`cli deps` and `cli generate`: a ';'-joined line, names that break paths.py, the paths.py
lock wait, a symlinked paths.py.bak, and names close to example.

* `cli deps remove pepx` deleted `pepx_venv=None; netmhcpan_entry="x"` whole, with another
  dependency's setting. The line now stays, and is named, as a chained assignment is.
* `cli deps add "a'''b"` wrote a paths.py that does not parse. Names now take letters, digits,
  spaces, '_', '.' and '-', and text that does not parse is never written.
* The wait for paths.lock (up to 30 s) was silent, and Ctrl-C printed a traceback.
* A backup written through a symlinked paths.py.bak would change the file it leads to.
* `cli generate examples` (or Example) made a blank tool. It still does, with a note
  naming cli generate example.
"""

import ast
import os
import signal
import subprocess
import sys
import time
from argparse import Namespace

import pytest

from ngargparser import cli

pytestmark = pytest.mark.skipif(os.name != "posix", reason="needs fcntl and symlinks")

LOCK = os.path.join(".ngargparser", "paths.lock")


def configure_reads(path):
    """The variables paths.py at `path` sets, as ./configure runs it."""
    found: dict = {}
    exec(compile(path.read_text(), str(path), "exec"), found)
    return {k: v for k, v in found.items() if not k.startswith("__")}


# --- 3. A setting on a ';'-joined line --------------------------------------------------------------


def test_remove_keeps_a_semicolon_line_with_another_dependencys_setting(scaffolded_project, strip_ansi, capsys):
    cli.add_deps_to_paths("paths.py", ["pepx", "netmhcpan"])
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text().replace("pepx_venv=None", 'pepx_venv=None; netmhcpan_entry="x"'))
    capsys.readouterr()

    result = cli._remove_deps("paths.py", ["pepx"])

    out = strip_ansi(capsys.readouterr().out)
    assert result["removed"] == ["pepx"] and not result["error"]
    values = configure_reads(paths)
    assert values["netmhcpan_entry"] == "x"
    assert "pepx_path" not in values
    assert "⚠  Kept 1 line(s) that cli deps did not write:\n" in out
    assert '     pepx_venv=None; netmhcpan_entry="x"\n' in out


def test_remove_still_drops_a_semicolon_line_of_its_own_settings(scaffolded_project, capsys):
    # Control: a line that holds only settings of the dependency removed goes with it.
    cli.add_deps_to_paths("paths.py", ["pepx", "netmhcpan"])
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text().replace("pepx_venv=None", "pepx_venv=None; pepx_entry='y'"))

    assert cli._remove_deps("paths.py", ["pepx"])["removed"] == ["pepx"]

    assert "pepx" not in paths.read_text()
    assert "Kept" not in capsys.readouterr().out


# --- 7. Names that would break paths.py -------------------------------------------------------------


@pytest.mark.parametrize("name", ["a'''b", 'a"b', "a\\b", "a\nb"])
def test_add_refuses_a_name_that_would_break_paths_py(scaffolded_project, strip_ansi, capsys, name):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    paths = scaffolded_project / "paths.py"
    before = paths.read_bytes()
    capsys.readouterr()

    rc = cli.deps_command(Namespace(deps_action="add", names=[name]))

    err = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert "has characters a dependency name cannot have" in err
    assert "Use letters, digits, spaces, '_', '.' and '-' (e.g. 'netmhcpan')." in err
    assert paths.read_bytes() == before
    ast.parse(paths.read_text())


@pytest.mark.parametrize("name", ["NetMHCpan 4.1", "mhc-i", "T_cell"])
def test_add_takes_letters_digits_spaces_and_dots(scaffolded_project, name):
    # Control: the names cli deps took before still work.
    assert cli.add_deps_to_paths("paths.py", [name]) == 1
    ast.parse((scaffolded_project / "paths.py").read_text())


def test_add_never_writes_text_that_does_not_parse(scaffolded_project, strip_ansi, capsys, monkeypatch):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    paths = scaffolded_project / "paths.py"
    before = paths.read_bytes()
    monkeypatch.setattr(cli, "generate_dependency_section", lambda name: f"\n{name}_path = (\n")
    capsys.readouterr()

    rc = cli.deps_command(Namespace(deps_action="add", names=["netmhcpan"]))

    err = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert "✗ Adding would break 'paths.py' (paths.py doesn't parse (line " in err
    assert err.rstrip().endswith("nothing was changed.")
    assert paths.read_bytes() == before


# --- 9. The paths.py lock wait ----------------------------------------------------------------------

HOLD = """
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o666)
fcntl.flock(fd, fcntl.LOCK_EX)
os.ftruncate(fd, 0)
if sys.argv[2] == "pid":
    os.pwrite(fd, f"{os.getpid()}\\n".encode(), 0)
print("held", flush=True)
time.sleep(60)
"""


@pytest.fixture
def hold_lock(scaffolded_project):
    held = []

    def hold(with_pid):
        os.makedirs(scaffolded_project / ".ngargparser", exist_ok=True)
        process = subprocess.Popen(
            [sys.executable, "-c", HOLD, str(scaffolded_project / LOCK), "pid" if with_pid else "none"],
            stdout=subprocess.PIPE,
            text=True,
        )
        held.append(process)
        assert process.stdout.readline() == "held\n"
        return process.pid

    yield hold
    for process in held:
        process.kill()
        process.wait()


@pytest.mark.parametrize("with_pid", [True, False])
def test_the_lock_wait_is_said_after_a_second(scaffolded_project, hold_lock, strip_ansi, capsys, monkeypatch, with_pid):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    pid = hold_lock(with_pid)
    monkeypatch.setattr(cli, "_PATHS_LOCK_WAIT", 2)
    capsys.readouterr()

    result = cli._add_deps("paths.py", ["netmhcpan"])

    out = strip_ansi(capsys.readouterr().out)
    assert result["error"]
    who = f"pid {pid} (cli deps or ./configure)" if with_pid else "another cli deps or ./configure"
    assert out == f"Waiting for {who} to finish with paths.py...\n"


def test_a_lock_that_comes_back_at_once_says_nothing(scaffolded_project, capsys):
    # Control: no wait, no line.
    cli.add_deps_to_paths("paths.py", ["pepx"])
    capsys.readouterr()
    cli._add_deps("paths.py", ["netmhcpan"])
    assert "Waiting" not in capsys.readouterr().out


def test_ctrl_c_while_waiting_for_the_lock_is_one_line(scaffolded_project, hold_lock):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    before = (scaffolded_project / "paths.py").read_bytes()
    hold_lock(True)
    env = {**os.environ, "NGARGPARSER_NO_UPDATE_CHECK": "1", "NO_COLOR": "1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "ngargparser.cli", "deps", "add", "netmhcpan"],
        cwd=scaffolded_project,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # The wait line says it is waiting: Ctrl-C then reaches the wait.
    assert process.stdout.readline().startswith("Waiting for pid ")
    time.sleep(0.2)
    process.send_signal(signal.SIGINT)
    out, err = process.communicate(timeout=30)

    assert process.returncode == 130
    assert cli._COLOUR_CODE_RE.sub("", err) == "✗ Stopped while waiting for paths.py. Nothing was changed.\n"
    assert (scaffolded_project / "paths.py").read_bytes() == before


# --- 10. A symlinked paths.py.bak -------------------------------------------------------------------


def test_remove_replaces_a_symlinked_backup_and_leaves_its_target(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["pepx", "netmhcpan"])
    paths = scaffolded_project / "paths.py"
    before = paths.read_bytes()
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours\n")
    backup = scaffolded_project / "paths.py.bak"
    backup.symlink_to(outside)

    assert cli._remove_deps("paths.py", ["pepx"])["removed"] == ["pepx"]

    assert outside.read_text() == "not yours\n"
    assert not backup.is_symlink() and backup.is_file()
    assert backup.read_bytes() == before


# --- 8. Names close to example ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["examples", "exampel", "Examples"])
def test_generate_a_name_close_to_example_makes_a_blank_tool_with_a_note(in_tmp_dir, strip_ansi, capsys, name):
    rc = cli.startapp_command(Namespace(project_name=name))

    out = strip_ansi(capsys.readouterr().out)
    assert not rc
    assert out.endswith(f"Note: '{name}' made a blank tool; for the sample app run: cli generate example\n")
    assert sorted(p.name for p in in_tmp_dir.iterdir()) == [name]


@pytest.mark.parametrize("name", ["Example", "EXAMPLE"])
def test_generate_example_in_any_case_makes_the_example(in_tmp_dir, name):
    rc = cli.startapp_command(Namespace(project_name=name))

    assert not rc
    assert sorted(p.name for p in in_tmp_dir.iterdir()) == ["aa-counter"]


def test_a_name_that_is_not_close_to_example_makes_a_tool(in_tmp_dir):
    # Control: example-app and sample are tool names of their own.
    assert not cli.startapp_command(Namespace(project_name="sample"))
    assert (in_tmp_dir / "sample" / "src" / "run_sample.py").is_file()
