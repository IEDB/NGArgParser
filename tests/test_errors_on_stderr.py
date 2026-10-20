"""The ✗ lines of cli sync, cli deps and cli generate go to stderr; progress and success stay on stdout.

`cli sync >/dev/null` with a read-only src/core, in a folder that is not a tool, or with a
test drive that failed gave exit code 1 and an empty stderr: every error line went to
stdout. stdout is flushed before an error line, so `2>&1 | tee log` keeps the order.
"""

import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
PARSER_FILE = "src/DemoArgumentParser.py"
PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))
needs_permissions = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply"
)


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def edit(project, path):
    target = project / path
    target.write_text(target.read_text() + "# a local edit that sync will overwrite\n")


def break_after_sync(project):
    """Help screens that pass before the sync and fail after it (see test_sync_files.py)."""
    core = project / CORE_FILE
    core.write_text(core.read_text() + "\nLOCAL_THING = 1\n")
    parser = project / PARSER_FILE
    parser.write_text("from core.NGArgumentParser import LOCAL_THING  # noqa: F401\n" + parser.read_text())


def give_venv(project):
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    if python.is_symlink():  # never write through a link: it can lead to a shared interpreter
        python.unlink()
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)


# --- 1. cli sync -----------------------------------------------------------------------------------


def test_sync_outside_a_tool_says_so_on_stderr(in_tmp_dir, sync_args, strip_ansi, capsys):
    assert cli.sync_command(sync_args(verify=False)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert strip_ansi(captured.err).startswith("✗ This folder is not an ngargparser tool")


@needs_permissions
def test_sync_with_a_read_only_folder_says_so_on_stderr(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    edit(project, CORE_FILE)
    (project / "src" / "core").chmod(0o555)
    try:
        rc = cli.sync_command(sync_args(verify=False))
    finally:
        (project / "src" / "core").chmod(0o755)
    captured = capsys.readouterr()
    assert rc == 1
    assert "✗" not in captured.out
    assert strip_ansi(captured.err) == (
        f"✗ Could not write {CORE_FILE}: the folder src/core is read-only (Permission denied). Nothing was changed.\n"
    )


def test_a_failed_test_drive_is_said_on_stderr_after_the_progress(scaffolded_project, strip_ansi):
    project = scaffolded_project
    give_venv(project)
    break_after_sync(project)
    code = (
        "import sys\nfrom argparse import Namespace\nfrom ngargparser import cli\n"
        "cli._local_version_label = lambda: None\n"
        "sys.exit(cli.sync_command(Namespace(upgrade=False, dev=False, ref='latest', dry_run=False, backup=True)))\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": PACKAGE_ROOT,
        "NGARGPARSER_NO_SELF_UPGRADE": "1",
        "NGARGPARSER_NO_UPDATE_CHECK": "1",
    }

    # stdout thrown away (cli sync >/dev/null): the error is still there, on stderr.
    run = subprocess.run(
        [sys.executable, "-c", code], cwd=project, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    err = strip_ansi(run.stderr)
    assert run.returncode == 1
    assert "✗ Sync rolled back: these commands worked before the sync and fail after it." in err
    assert "ImportError: cannot import name 'LOCAL_THING'" in err
    assert "Every file was restored to its state before the sync." in err

    # Both in one place (2>&1 | tee log): the progress first, then the error, in that order.
    run = subprocess.run(
        [sys.executable, "-c", code], cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    both = strip_ansi(run.stdout)
    assert run.returncode == 1
    assert both.index("Checking that the tool starts (its help screens) after the update...") < both.index(
        "✗ Sync rolled back"
    )


# --- 2. cli deps ---------------------------------------------------------------------------------------


def test_deps_errors_go_to_stderr(scaffolded_project, strip_ansi, capsys):
    rc = cli.deps_command(Namespace(deps_action="add", names=["uv", "netmhcpan"]))
    captured = capsys.readouterr()
    assert rc == 1
    assert "✗" not in captured.out
    assert "✓ Added 1 dependency stub(s) to 'paths.py': netmhcpan" in strip_ansi(captured.out)
    assert strip_ansi(captured.err).startswith("✗ Not added: 'uv' becomes 'uv'")

    rc = cli.deps_command(Namespace(deps_action="remove", names=["netmhcpan", "pepx"]))
    captured = capsys.readouterr()
    assert rc == 1
    assert "✗" not in captured.err + captured.out  # pepx is skipped with a warning, not an error
    os.remove("paths.py")
    rc = cli.deps_command(Namespace(deps_action="remove", names=["netmhcpan"]))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert strip_ansi(captured.err) == "✗ 'paths.py' not found. Nothing to remove.\n"


def test_deps_outside_a_tool_says_so_on_stderr(in_tmp_dir, strip_ansi, capsys):
    assert cli.deps_command(Namespace(deps_action="add", names=["pepx"])) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert strip_ansi(captured.err).startswith("✗ This folder is not an ngargparser tool")


# --- 3. cli generate -----------------------------------------------------------------------------------


def test_generate_errors_go_to_stderr(in_tmp_dir, strip_ansi, capsys):
    assert cli.startapp_command(Namespace(project_name="9lives")) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert strip_ansi(captured.err).startswith("✗ Invalid tool name '9lives'.")

    (in_tmp_dir / "taken").mkdir()
    assert cli.startapp_command(Namespace(project_name="taken")) == 1
    captured = capsys.readouterr()
    assert "✗" not in captured.out
    assert strip_ansi(captured.err) == "✗ 'taken' already exists; aborting.\n"
