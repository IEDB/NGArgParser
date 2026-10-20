"""`cli deps`: a name entered twice in the menu, removing a dependency declared twice, names
that would take over a system variable, a subfolder of a tool, a build copy, and the
./configure command printed after `cli deps add`.

The menu tests drive `cli deps` (no names) with a fake terminal and scripted answers to
input(), as in test_deps_folder_and_matching.py.
"""

import io
import sys
from argparse import Namespace

import pytest

from ngargparser import cli


def deps(action, *names):
    return cli.deps_command(Namespace(deps_action=action, names=list(names)))


class _Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def answers(monkeypatch):
    """Make stdin a terminal and feed `input()` these replies in order."""

    def _answers(*replies):
        monkeypatch.setattr(sys, "stdin", _Terminal())
        queue = iter(replies)
        monkeypatch.setattr("builtins.input", lambda *_: next(queue))

    return _answers


def path_lines(content):
    return [line for line in content.splitlines() if line.endswith("_path=None") and "_lib_path" not in line]


# --- 6a. The menu adds a name entered twice once ---------------------------------------------


def test_menu_adds_a_name_entered_twice_once(scaffolded_project, answers, strip_ansi, capsys):
    answers("foo", "foo", "Bar", "bar", "baz", "")

    assert deps(None) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert path_lines((scaffolded_project / "paths.py").read_text()) == [
        "foo_path=None",
        "bar_path=None",
        "baz_path=None",
    ]
    assert "⚠  foo was entered twice; added once." in out
    assert "⚠  Bar was entered twice (as Bar, bar); added once." in out
    assert "baz was entered" not in out  # control: a name given once


def test_menu_adds_to_an_existing_paths_file_once(scaffolded_project, answers, strip_ansi, capsys):
    assert deps("add", "pepx") == 0
    answers("1", "foo", "foo", "foo", "")

    assert deps(None) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert path_lines((scaffolded_project / "paths.py").read_text()) == ["pepx_path=None", "foo_path=None"]
    assert "⚠  foo was entered 3 times; added once." in out
    assert "with 1 new dependencies" in out


# --- 6b. Removing a dependency declared twice -------------------------------------------------


def test_remove_takes_every_block_of_a_name_declared_twice(scaffolded_project, strip_ansi, capsys):
    assert deps("add", "foo", "bar", "baz") == 0
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text() + cli.generate_dependency_section("foo") + "\n")  # what the old menu wrote
    capsys.readouterr()

    # Control: a name declared once reads as before.
    assert deps("remove", "baz") == 0
    assert "✓ Removed 1 dependency block(s) from 'paths.py': Baz (baz)\n" in strip_ansi(capsys.readouterr().out)

    assert deps("remove", "foo") == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "✓ Removed 2 dependency block(s) from 'paths.py': Foo (foo, 2 blocks)\n" in out
    assert sorted(cli._paths_dependencies(paths.read_text())) == ["bar"]
    assert "foo" not in paths.read_text().lower()
    # The other dependency's settings and its title are all still there.
    assert path_lines(paths.read_text()) == ["bar_path=None"] and "''' [ Bar ] '''" in paths.read_text()


# --- 6c. Names that would take over a system variable -----------------------------------------


@pytest.mark.parametrize(
    "name, variable",
    [("ld-library", "LD_LIBRARY_PATH"), ("library", "LIBRARY_PATH"), ("PKG_config", "PKG_CONFIG_PATH")],
)
def test_add_refuses_a_name_whose_settings_are_a_system_variable(
    scaffolded_project, strip_ansi, capsys, name, variable
):
    assert deps("add", name, "pepx") == 1

    out = strip_ansi(capsys.readouterr().err)
    var = cli.normalize_name(name)
    assert (
        f"✗ Not added: '{name}' becomes '{var}', and ./configure would write its settings to .env as {variable}, "
        f"{var.upper()}_VENV, {var.upper()}_ENTRY and so on. {variable} is a variable the system or Python reads, "
        f"and sourcing setup_{var}_env.sh would replace yours. Pick another name." in out
    )
    assert path_lines((scaffolded_project / "paths.py").read_text()) == ["pepx_path=None"]  # control: pepx is added


def test_menu_refuses_a_name_whose_settings_are_a_system_variable(scaffolded_project, answers, strip_ansi, capsys):
    answers("ld-library", "")

    assert deps(None) == 1

    assert "LD_LIBRARY_PATH is a variable the system or Python reads" in strip_ansi("".join(capsys.readouterr()))
    assert path_lines((scaffolded_project / "paths.py").read_text()) == []


# --- 6d. A subfolder of a tool ----------------------------------------------------------------


@pytest.mark.parametrize("action, names", [("add", ["pepx"]), ("remove", ["pepx"]), ("list", []), (None, [])])
def test_deps_in_a_subfolder_names_the_tool_and_does_nothing(
    scaffolded_project, monkeypatch, answers, strip_ansi, capsys, action, names
):
    answers()  # a terminal, and no replies: a question would raise StopIteration
    (scaffolded_project / "src" / "core").mkdir(exist_ok=True)
    monkeypatch.chdir(scaffolded_project / "src" / "core")

    assert deps(action, *names) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert f"✗ This folder is inside the ngargparser tool {scaffolded_project}: run cli deps there: " in out
    assert out.rstrip().endswith(f"cd {scaffolded_project}")
    assert not (scaffolded_project / "paths.py").exists()
    assert not (scaffolded_project / "src" / "core" / "paths.py").exists()


# --- 6e. A build or deployed copy -------------------------------------------------------------


def test_deps_refuses_to_write_in_a_build_copy(scaffolded_project, answers, strip_ansi, capsys):
    assert deps("add", "pepx") == 0
    paths = scaffolded_project / "paths.py"
    before = paths.read_text()
    (scaffolded_project / ".ngargparser-build").write_text("TOOL_NAME=demo\nTOOL_VERSION=1.0.0\n")
    answers()
    capsys.readouterr()

    for action, names in (("add", ["foo"]), ("remove", ["pepx"]), (None, [])):
        assert deps(action, *names) == 1
        out = strip_ansi("".join(capsys.readouterr()))
        assert (
            "✗ This folder is a build or deployed copy of a tool (it has .ngargparser-build), not its source. "
            "Nothing was changed. Run cli deps in the tool's source folder, then build again." in out
        )
        assert paths.read_text() == before
        assert not (scaffolded_project / "paths.py.bak").exists()

    # Listing writes nothing, so it still works there.
    assert deps("list") == 0
    assert "pepx" in strip_ansi("".join(capsys.readouterr()))


# --- 6f. The ./configure command that sets the path -------------------------------------------


def test_add_prints_the_configure_command_for_each_dependency(scaffolded_project, strip_ansi, capsys):
    assert deps("add", "pepx") == 0
    out = strip_ansi(capsys.readouterr().out)
    assert "   Set its path: ./configure --pepx-path=/path/to/pepx\n   Or edit 'paths.py' by hand.\n" in out
    assert "to fill in the actual paths" not in out

    # A name is one word: "T Cell" would be two dependencies (see test_deps_split_names.py).
    assert deps("add", "mhci-predictor", "T_Cell") == 0
    out = strip_ansi(capsys.readouterr().out)
    # One command for both: ./configure stops while any dependency's path is still None.
    assert (
        "   Set their paths: ./configure --mhci-predictor-path=/path/to/mhci-predictor --t-cell-path=/path/to/t-cell\n"
    ) in out


def test_add_with_a_name_given_twice_adds_it_once_and_says_so(scaffolded_project, strip_ansi, capsys):
    assert deps("add", "foo", "Foo") == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "⚠  Foo was given twice (as foo, Foo); added once." in out
    assert "already declared" not in out
    assert (scaffolded_project / "paths.py").read_text().count("foo_path") == 1
