"""`cli generate` names, and `cli deps remove` of a variable another line still reads.

* `cli generate test-tool` (tester, testtool, Testing) was refused: pytest would collect
  TestToolArgumentParser. NGArgumentParser sets __test__ = False now, so these are tool names.
  A name that clashes with a package the tool needs (pytest) is still refused.
* `cli generate examples` (example2) was refused as too close to example. It makes the blank
  tool now, and a note names cli generate example.
* `cli deps rm base` with `netmhcpan_path = os.path.join(base_path, 'deptool')` in paths.py took
  out base_path, and ./configure then failed with a NameError. Remove now refuses, and
  changes nothing.
"""

import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

# --- 1a. Names that start with test ------------------------------------------------------------


@pytest.mark.parametrize("name", ["test-tool", "tester", "testtool", "Testing"])
def test_generate_takes_a_name_that_starts_with_test(in_tmp_dir, strip_ansi, capsys, name):
    rc = cli.startapp_command(Namespace(project_name=name))

    captured = capsys.readouterr()
    assert not rc, captured.err
    assert "Invalid tool name" not in strip_ansi(captured.err)
    parser = in_tmp_dir / name / "src" / f"{cli.format_project_name(name, capitalize=True)}ArgumentParser.py"
    assert parser.is_file()
    assert cli.format_project_name(name, capitalize=True).startswith("Test")


def test_a_name_that_clashes_with_a_package_the_tool_needs_is_still_refused(in_tmp_dir, strip_ansi, capsys):
    assert cli.startapp_command(Namespace(project_name="pytest")) == 1

    assert strip_ansi(capsys.readouterr().err) == (
        "✗ Invalid tool name 'pytest'. A new tool has pytest in its dev dependency group, and the tool's name is "
        "also its Python package name, so the two would clash. Pick another name (e.g. 'aa-counter').\n"
    )
    assert list(in_tmp_dir.iterdir()) == []


# --- 1b. Names close to example ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["examples", "example2"])
def test_generate_a_name_close_to_example_makes_the_blank_tool_and_a_note(in_tmp_dir, strip_ansi, capsys, name):
    rc = cli.startapp_command(Namespace(project_name=name))

    captured = capsys.readouterr()
    out = strip_ansi(captured.out)
    assert not rc
    assert captured.err == ""
    assert out.endswith(f"Note: '{name}' made a blank tool; for the sample app run: cli generate example\n")
    assert out.count("Note:") == 1
    assert (in_tmp_dir / name / "src" / f"run_{name}.py").is_file()
    assert not (in_tmp_dir / "aa-counter").exists()


@pytest.mark.parametrize("name", ["sample", "my-tool"])
def test_a_name_not_close_to_example_has_no_note(in_tmp_dir, strip_ansi, capsys, name):
    # Control for the note.
    assert not cli.startapp_command(Namespace(project_name=name))
    assert "Note:" not in strip_ansi(capsys.readouterr().out)


# --- 2. Remove a variable another line still reads ---------------------------------------------


def deps(action, *names):
    return cli.deps_command(Namespace(deps_action=action, names=list(names)))


def runs(project):
    """Run paths.py as ./configure runs it: (exit code, stderr)."""
    run = subprocess.run([sys.executable, "paths.py"], cwd=project, capture_output=True, text=True)
    return run.returncode, run.stderr


def test_remove_refuses_when_another_line_uses_its_path(scaffolded_project, strip_ansi, capsys):
    assert deps("add", "base") == 0
    paths = scaffolded_project / "paths.py"
    paths.write_text("import os\n" + paths.read_text() + "netmhcpan_path = os.path.join(base_path, 'deptool')\n")
    paths.write_text(paths.read_text().replace("base_path=None", "base_path='/opt/base'"))
    before = paths.read_text()
    line = before.splitlines().index("netmhcpan_path = os.path.join(base_path, 'deptool')") + 1
    assert runs(scaffolded_project)[0] == 0
    capsys.readouterr()

    assert deps("remove", "base") == 1

    captured = capsys.readouterr()
    assert strip_ansi(captured.err) == (
        f"✗ Not removed: base. In 'paths.py', line {line} uses base_path, which the removal would delete, so "
        "./configure would fail. Delete or fix that line first. Nothing was changed.\n"
    )
    assert "Removed" not in strip_ansi(captured.out)
    assert paths.read_text() == before
    assert not (scaffolded_project / "paths.py.bak").exists()
    assert runs(scaffolded_project)[0] == 0


def test_remove_goes_ahead_once_no_other_line_uses_its_variables(scaffolded_project, strip_ansi, capsys):
    # Control: the same file with the line fixed by hand.
    assert deps("add", "base") == 0
    paths = scaffolded_project / "paths.py"
    paths.write_text("import os\n" + paths.read_text() + "netmhcpan_path = os.path.join('/opt/base', 'deptool')\n")
    capsys.readouterr()

    assert deps("remove", "base") == 0

    assert "✓ Removed 1 dependency block(s) from 'paths.py': Base (base)" in strip_ansi(capsys.readouterr().out)
    assert "base_path" not in paths.read_text()
    assert runs(scaffolded_project) == (0, "")
