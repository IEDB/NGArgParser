"""`cli deps`: matching a block by its variable name, the tool-folder check, and the menu.

The menu tests drive `cli deps` (no names) with a fake terminal and scripted
answers to input(), the way a developer would type them.
"""

import io
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PYPROJECT = '[project]\nname = "demo"\n\n[tool.ngargparser]\napp_name = "demo"\n'

# Two blocks whose titles were swapped by hand: each title sits above the other tool's settings.
SWAPPED_TITLES = """''' [ Pepx ] '''
tcell_class_i_path='/opt/tc1'
tcell_class_i_venv=None
tcell_class_i_module=None
tcell_class_i_lib_path=None

''' [ T Cell Class I ] '''
pepx_path='/opt/pepx'
pepx_venv=None
pepx_module=None
pepx_lib_path=None
"""


def deps(action, *names):
    return cli.deps_command(Namespace(deps_action=action, names=list(names)))


@pytest.fixture
def tool(in_tmp_dir):
    """The top folder of a tool: cli deps works only there."""
    (in_tmp_dir / "pyproject.toml").write_text(PYPROJECT)
    return in_tmp_dir


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


def settings_of(content):
    return sorted(cli._paths_dependencies(content))


# --- 5a. The variable name decides, not the title ------------------------------------


def test_remove_by_name_takes_that_tools_block_even_under_another_title(tool, strip_ansi, capsys):
    paths = tool / "paths.py"
    paths.write_text(SWAPPED_TITLES)

    assert deps("remove", "pepx") == 0

    content = paths.read_text()
    assert "pepx_" not in content
    assert "tcell_class_i_path='/opt/tc1'" in content and "tcell_class_i_lib_path=None" in content
    assert settings_of(content) == ["tcell_class_i"]
    assert "Removed 1 dependency block(s) from 'paths.py': T Cell Class I (pepx)" in strip_ansi(capsys.readouterr().out)


def test_remove_by_a_title_shared_by_two_blocks_removes_nothing(tool, strip_ansi, capsys):
    content = SWAPPED_TITLES.replace("''' [ T Cell Class I ] '''", "''' [ Pepx ] '''").replace("pepx_", "other_")
    paths = tool / "paths.py"
    paths.write_text(content)

    assert deps("remove", "Pepx") == 1

    assert paths.read_text() == content
    assert "'Pepx' is the title of several dependencies (tcell_class_i, other)" in strip_ansi(capsys.readouterr().err)


def test_remove_by_a_unique_title_still_works(tool):
    # Control: the title is the fallback when no variable name matches.
    paths = tool / "paths.py"
    paths.write_text(SWAPPED_TITLES)

    assert deps("remove", "T Cell Class I") == 0
    assert settings_of(paths.read_text()) == ["tcell_class_i"]


# --- 5b. cli deps runs only in a tool's top folder -------------------------------------


@pytest.mark.parametrize("action, names", [("add", ["pepx"]), ("remove", ["pepx"]), ("list", [])])
def test_deps_outside_a_tool_folder_is_refused(in_tmp_dir, strip_ansi, capsys, action, names):
    (in_tmp_dir / "pyproject.toml").write_text('[project]\nname = "other"\n')

    assert deps(action, *names) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert "This folder is not an ngargparser tool: cli deps found no [tool.ngargparser] table" in out
    assert not (in_tmp_dir / "paths.py").exists()


def test_deps_inside_a_tools_src_folder_is_refused(scaffolded_project, monkeypatch, strip_ansi, capsys):
    # ./configure reads paths.py in the top folder; src/paths.py would be read by nothing.
    monkeypatch.chdir(scaffolded_project / "src")

    assert deps("add", "pepx") == 1

    out = strip_ansi(capsys.readouterr().err)
    assert f"✗ This folder is inside the ngargparser tool {scaffolded_project}: run cli deps there: cd " in out
    assert not (scaffolded_project / "paths.py").exists()  # nothing is done in the tool above either
    assert not (scaffolded_project / "src" / "paths.py").exists()


def test_menu_outside_a_tool_folder_is_refused_before_any_question(in_tmp_dir, answers, strip_ansi, capsys):
    answers()  # a terminal, and no replies: a question would raise StopIteration

    assert deps(None) == 1

    assert "not an ngargparser tool" in strip_ansi("".join(capsys.readouterr()))
    assert not (in_tmp_dir / "paths.py").exists()


def test_deps_in_a_generated_tool_works(scaffolded_project):
    # Control: a real tool folder.
    assert deps("add", "pepx") == 0
    assert "pepx_path=None" in (scaffolded_project / "paths.py").read_text()


# --- 5c. The interactive menu -----------------------------------------------------------


def test_menu_add_appends_new_blocks_and_skips_declared_ones(tool, answers, strip_ansi, capsys):
    cli.add_deps_to_paths("paths.py", ["peer"])
    answers("1", "pepx", "peer", "mhc-i", "")

    assert deps(None) == 0

    content = (tool / "paths.py").read_text()
    assert settings_of(content) == ["mhc_i", "peer", "pepx"]
    assert content.count("peer_path=None") == 1
    out = strip_ansi(capsys.readouterr().out)
    assert "Skipped 1 dependencies (already exist):" in out and "  - peer" in out
    assert "Updated 'paths.py' with 2 new dependencies." in out


def test_menu_add_into_an_empty_paths_file_starts_at_the_top(tool, answers):
    (tool / "paths.py").write_text("")
    answers("1", "pepx", "")

    assert deps(None) == 0

    content = (tool / "paths.py").read_text()
    assert content.startswith("''' [ Pepx ] '''\n")
    assert content.endswith("pepx_lib_path=None\n")


def test_menu_add_keeps_existing_content_above_the_first_block(tool, answers):
    (tool / "paths.py").write_text("from pathlib import Path\nAPP_ROOT = Path(__file__).parent\n\n\n")
    answers("1", "pepx", "")

    assert deps(None) == 0

    content = (tool / "paths.py").read_text()
    assert content.startswith("from pathlib import Path\nAPP_ROOT = Path(__file__).parent\n")
    assert content.index("APP_ROOT") < content.index("''' [ Pepx ] '''")
    assert settings_of(content) == ["pepx"]
    compile(content, "paths.py", "exec")


def test_menu_add_of_a_refused_name_exits_1_and_adds_the_rest(tool, answers, strip_ansi, capsys):
    cli.add_deps_to_paths("paths.py", ["peer"])
    answers("1", "foo_venv", "pepx", "")

    assert deps(None) == 1

    assert settings_of((tool / "paths.py").read_text()) == ["peer", "pepx"]
    assert "Not added: 'foo_venv' becomes 'foo_venv', which ends in '_venv'" in strip_ansi("".join(capsys.readouterr()))


def test_menu_add_with_no_names_changes_nothing(tool, answers, strip_ansi, capsys):
    cli.add_deps_to_paths("paths.py", ["peer"])
    before = (tool / "paths.py").read_text()
    answers("1", "")

    assert deps(None) == 0

    assert (tool / "paths.py").read_text() == before
    assert "No new dependencies to add." in strip_ansi(capsys.readouterr().out)


def test_menu_add_of_only_declared_names_changes_nothing(tool, answers, strip_ansi, capsys):
    cli.add_deps_to_paths("paths.py", ["peer"])
    before = (tool / "paths.py").read_text()
    answers("1", "peer", "")

    assert deps(None) == 0

    assert (tool / "paths.py").read_text() == before
    assert "No new dependencies to add after checking for duplicates." in strip_ansi(capsys.readouterr().out)


def test_menu_remove_by_number_and_range(tool, answers, strip_ansi, capsys):
    cli.add_deps_to_paths("paths.py", ["alpha", "beta", "gamma", "delta"])
    answers("2", "1, 3-4", "y")

    assert deps(None) == 0

    assert settings_of((tool / "paths.py").read_text()) == ["beta"]
    assert (tool / "paths.py.bak").exists()
    out = strip_ansi(capsys.readouterr().out)
    assert "Removed 3 dependency block(s) from 'paths.py': Alpha (alpha), Gamma (gamma), Delta (delta)" in out


def test_menu_remove_asks_again_after_a_bad_selection(tool, answers, strip_ansi, capsys):
    cli.add_deps_to_paths("paths.py", ["alpha", "beta"])
    answers("2", "5", "one", "2", "maybe", "yes")

    assert deps(None) == 0

    assert settings_of((tool / "paths.py").read_text()) == ["alpha"]
    out = strip_ansi(capsys.readouterr().out)
    assert "Invalid indices: [5]. Please use numbers 1-2." in out
    assert "Invalid format." in out
    assert "Please enter 'y' for yes or 'n' for no." in out


@pytest.mark.parametrize("replies", [("2", ""), ("2", "1", "n")], ids=["empty selection", "no at confirm"])
def test_menu_remove_can_be_cancelled(tool, answers, strip_ansi, capsys, replies):
    cli.add_deps_to_paths("paths.py", ["alpha", "beta"])
    before = (tool / "paths.py").read_text()
    answers(*replies)

    assert deps(None) == 0

    assert (tool / "paths.py").read_text() == before
    assert "Removal cancelled." in strip_ansi(capsys.readouterr().out)


def test_menu_remove_with_no_dependencies_says_so(tool, answers, strip_ansi, capsys):
    (tool / "paths.py").write_text("APP_ROOT = '/x'\n")
    answers("2")

    assert deps(None) == 0
    assert "No dependencies found to remove." in strip_ansi(capsys.readouterr().out)


def test_menu_remove_takes_the_block_of_the_entry_picked(tool, answers):
    # Entry 2 is listed under its title "T Cell Class I", and its settings are pepx_*.
    paths = tool / "paths.py"
    paths.write_text(SWAPPED_TITLES)
    answers("2", "2", "y")

    assert deps(None) == 0

    assert settings_of(paths.read_text()) == ["tcell_class_i"]


def test_menu_asks_again_after_an_unknown_choice(tool, answers, strip_ansi, capsys):
    cli.add_deps_to_paths("paths.py", ["peer"])
    answers("9", "3")

    assert deps(None) == 0
    assert "Please enter 1, 2, or 3." in strip_ansi(capsys.readouterr().out)
