"""`cli deps`: the name 'app', the developer's own lines in a block, read-only files,
and Ctrl-D or Ctrl-C at the first-run menu."""

import importlib.util
import io
import os
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PYPROJECT = '[project]\nname = "demo"\n\n[tool.ngargparser]\napp_name = "demo"\n'


def deps(action, *names):
    return cli.deps_command(Namespace(deps_action=action, names=list(names)))


@pytest.fixture
def tool(in_tmp_dir):
    """The top folder of a tool: cli deps works only there."""
    (in_tmp_dir / "pyproject.toml").write_text(PYPROJECT)
    return in_tmp_dir


def configure_module():
    spec = importlib.util.spec_from_file_location("configure_template", cli.TEMPLATE_DIR / "configure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- 8a. 'app' would take over the tool's own APP_* settings ----------------------------


@pytest.mark.parametrize("name", ["app", "App", "app-"])
def test_a_dependency_named_app_is_refused(tool, strip_ansi, capsys, name):
    assert deps("add", name) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert "Not added:" in out and "APP_VENV and APP_ENTRY are this tool's own settings" in out
    assert not (tool / "paths.py").exists()

    assert deps("add", "apps") == 0  # control: a name that only starts with app is fine
    assert "apps_path=None" in (tool / "paths.py").read_text()


# --- 8b. cli deps remove keeps the lines it did not write ---------------------------------


def test_remove_keeps_the_developers_own_lines_and_names_them(tool, strip_ansi, capsys):
    # Control: a block holding only what cli deps wrote goes entirely, as before.
    assert deps("add", "foo", "bar") == 0
    assert deps("remove", "foo") == 0
    assert (tool / "paths.py").read_text() == cli.normalize_content_ending(
        cli.generate_dependency_section("bar").lstrip("\n")
    )
    assert "Kept" not in strip_ansi(capsys.readouterr().out)

    # Now with a setting and a note of the developer's inside foo's block, and foo_entry below it.
    assert deps("add", "foo") == 0
    paths = tool / "paths.py"
    content = paths.read_text().replace("foo_venv=None\n", "foo_venv=None\nMY_TIMEOUT = 300\n# keep my note\n")
    paths.write_text(content + "foo_entry='src/run_foo.py'\n")
    capsys.readouterr()

    assert deps("remove", "foo") == 0

    out = strip_ansi(capsys.readouterr().out)
    after = paths.read_text()
    assert "MY_TIMEOUT = 300\n" in after and "# keep my note\n" in after
    assert "foo_" not in after and "[ Foo ]" not in after and "Path to the Foo" not in after
    assert "bar_path=None" in after
    assert "Kept 2 line(s) that cli deps did not write:\n     MY_TIMEOUT = 300\n     # keep my note\n" in out
    configure = configure_module()
    assert list(configure.detect_dependency_tools(configure.load_config(str(paths)))) == ["bar"]


# --- 8c. A read-only paths.py -------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply")
def test_read_only_paths_file_is_a_one_line_error_not_a_traceback(tool, strip_ansi, capsys):
    # A read-only folder that is not a build copy (cli deps refuses those before writing).
    assert deps("add", "foo") == 0
    paths = tool / "paths.py"
    before = paths.read_text()
    paths.chmod(0o444)
    tool.chmod(0o555)
    capsys.readouterr()
    try:
        assert deps("add", "bar") == 1
        added = strip_ansi("".join(capsys.readouterr()))
        assert deps("remove", "foo") == 1
        removed = strip_ansi("".join(capsys.readouterr()))
    finally:
        tool.chmod(0o755)
        paths.chmod(0o644)

    assert "✗ Could not write 'paths.py': Permission denied." in added
    assert "✗ Could not write 'paths.py.bak': Permission denied." in removed
    assert "Added" not in added and "Removed" not in removed
    assert paths.read_text() == before


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply")
def test_a_read_only_backup_left_by_a_failed_run_does_not_block_the_next(tool, strip_ansi, capsys):
    # copy2 copies the mode: a failed run on a read-only paths.py left a read-only
    # paths.py.bak, and every later run then failed writing the backup.
    assert deps("add", "foo") == 0
    paths = tool / "paths.py"
    paths.chmod(0o444)
    try:
        assert deps("remove", "foo") == 1
    finally:
        paths.chmod(0o644)
    capsys.readouterr()

    assert deps("remove", "foo") == 0

    assert "foo_path" not in paths.read_text()
    assert "Could not write" not in strip_ansi(capsys.readouterr().out)


# --- 8d. Ctrl-D or Ctrl-C at the first-run menu ---------------------------------------------


class _Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("stop", [EOFError, KeyboardInterrupt], ids=["ctrl-d", "ctrl-c"])
def test_stopping_the_first_run_menu_says_cancelled_and_writes_nothing(tool, monkeypatch, strip_ansi, capsys, stop):
    monkeypatch.setattr(sys, "stdin", _Terminal())

    def stopped(*_):
        raise stop

    monkeypatch.setattr("builtins.input", stopped)

    assert deps(None) == 1

    out = strip_ansi(capsys.readouterr().out)
    assert "Cancelled." in out.splitlines()
    assert "Error creating paths.py" not in out
    assert not (tool / "paths.py").exists()


def test_remove_takes_the_dependencys_python_setting_with_it(tool, strip_ansi, capsys):
    # <name>_python is a setting ./configure reads, like <name>_entry: it is the
    # dependency's, not a line of the developer's own.
    assert deps("add", "pepx") == 0
    paths = tool / "paths.py"
    paths.write_text(paths.read_text() + "pepx_python = '/usr/bin/python3'\n")
    capsys.readouterr()

    assert deps("remove", "pepx") == 0

    assert "pepx_python" not in paths.read_text()
    assert "did not write" not in strip_ansi(capsys.readouterr().out)
