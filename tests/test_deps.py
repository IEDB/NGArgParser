import importlib.util
import io
import sys
from argparse import Namespace

import pytest

from ngargparser import cli


def test_add_creates_file_with_blocks(in_tmp_dir):
    added = cli.add_deps_to_paths("paths.py", ["mhci", "mhcii"])
    assert added == 2
    content = (in_tmp_dir / "paths.py").read_text()
    assert "''' [ Mhci ] '''" in content
    assert "''' [ Mhcii ] '''" in content


def test_add_skips_duplicate_names(in_tmp_dir):
    cli.add_deps_to_paths("paths.py", ["mhci"])
    added = cli.add_deps_to_paths("paths.py", ["mhci"])
    assert added == 0


def test_remove_deletes_targeted_block_only(in_tmp_dir):
    cli.add_deps_to_paths("paths.py", ["mhci", "mhcii"])
    removed = cli.remove_deps_from_paths("paths.py", ["mhci"])
    assert removed == 1
    content = (in_tmp_dir / "paths.py").read_text()
    assert "''' [ Mhci ] '''" not in content
    assert "''' [ Mhcii ] '''" in content


def test_remove_missing_name_is_noop(in_tmp_dir):
    cli.add_deps_to_paths("paths.py", ["mhci"])
    removed = cli.remove_deps_from_paths("paths.py", ["nonexistent"])
    assert removed == 0
    assert "''' [ Mhci ] '''" in (in_tmp_dir / "paths.py").read_text()


def test_list_reports_declared_count(in_tmp_dir, capsys):
    cli.add_deps_to_paths("paths.py", ["mhci", "mhcii"])
    capsys.readouterr()  # clear add output
    count = cli.list_deps_in_paths("paths.py")
    assert count == 2
    out = capsys.readouterr().out
    assert "Mhci" in out and "Mhcii" in out


def test_add_then_remove_round_trip(in_tmp_dir):
    cli.add_deps_to_paths("paths.py", ["mhci", "mhcii"])
    cli.remove_deps_from_paths("paths.py", ["mhci", "mhcii"])
    assert cli.list_deps_in_paths("paths.py") == 0


def test_remove_backs_up_before_rewrite(in_tmp_dir):
    cli.add_deps_to_paths("paths.py", ["mhci", "mhcii"])
    before = (in_tmp_dir / "paths.py").read_text()

    cli.remove_deps_from_paths("paths.py", ["mhci"])

    backup = in_tmp_dir / "paths.py.bak"
    assert backup.exists()
    assert backup.read_text() == before  # pre-removal content is recoverable


# cli deps reads paths.py by ./configure's rule: a tool is a `<name>_path`
# variable (not ending in _lib/_venv/_module); a ''' [ Title ] ''' line is only a
# label. Before, it keyed on titles, so hand-edited files went wrong.


PHBR_STYLE = """from pathlib import Path

# Get the absolute path to the project root directory
APP_ROOT = Path(__file__).parent.absolute()

''' [ T Cell Class I ] '''
# Path to the IEDB T cell class I tool (required)
tcell_class_i_path='/opt/tc1'

# Path to the virtual environment (optional)
tcell_class_i_venv='/opt/tc1/.venv'

# tcell_class_i_module='python/3.10.5'
tcell_class_i_module=None

tcell_class_i_lib_path=None

''' [ T Cell Class II ] '''
tcell_class_ii_path='/opt/tc2'
tcell_class_ii_venv=None
tcell_class_ii_module=None
tcell_class_ii_lib_path=None
"""

PVC_STYLE = """tcell_class_i_path='/opt/tc1'
tcell_class_i_venv=None
tcell_class_ii_path='/opt/tc2'
pepx_db_path='/data/pepx.sqlite'
icerfire_python_path='/venvs/icerfire/bin/python3'
icerfire_python_lib_path=None
"""

BCELL_STRUCTURE_STYLE = """discotope_3_0_executable_path='libs/discotope3_web/src/predict_webserver.py'
discotope_3_0_python_lib_path=None
discotope_3_0_module=None
discotope_3_0_executable_python_path=None
"""

INTERLEAVED = """alpha_path='/a'
beta_path='/b'
alpha_venv=None
beta_venv=None
"""


def write_paths(directory, content):
    (directory / "paths.py").write_text(content)
    return directory / "paths.py"


def deps(action, *names):
    return cli.deps_command(Namespace(deps_action=action, names=list(names)))


def configure_module():
    spec = importlib.util.spec_from_file_location("configure_template", cli.TEMPLATE_DIR / "configure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_list_shows_the_real_paths_of_a_hand_edited_file(in_tmp_dir, capsys):
    write_paths(in_tmp_dir, PHBR_STYLE)

    assert deps("list") == 0

    out = capsys.readouterr().out
    assert "T Cell Class I (tcell_class_i)" in out and "_path = '/opt/tc1'" in out
    assert "T Cell Class II (tcell_class_ii)" in out and "_path = '/opt/tc2'" in out


def test_list_sees_tools_without_titles(in_tmp_dir):
    write_paths(in_tmp_dir, PVC_STYLE)
    assert cli.list_deps_in_paths("paths.py") == 4


@pytest.mark.parametrize("name", ["tcell_class_i", "T Cell Class I"])
def test_remove_by_name_or_title_deletes_the_whole_block(in_tmp_dir, name):
    paths = write_paths(in_tmp_dir, PHBR_STYLE)

    assert deps("remove", name) == 0

    content = paths.read_text()
    assert "tcell_class_i_" not in content and "T Cell Class I ]" not in content
    assert "tcell_class_ii_path='/opt/tc2'" in content and "APP_ROOT" in content
    compile(content, "paths.py", "exec")  # still valid Python
    configure = configure_module()
    assert list(configure.detect_dependency_tools(configure.load_config(str(paths)))) == ["tcell_class_ii"]


def test_remove_a_tool_without_a_title(in_tmp_dir):
    paths = write_paths(in_tmp_dir, PVC_STYLE)

    assert deps("remove", "pepx_db") == 0

    assert "pepx_db" not in paths.read_text()
    assert cli.list_deps_in_paths("paths.py") == 3


def test_remove_that_finds_nothing_exits_1(in_tmp_dir):
    paths = write_paths(in_tmp_dir, PHBR_STYLE)

    assert deps("remove", "nonexistent") == 1
    assert paths.read_text() == PHBR_STYLE


def test_remove_refuses_blocks_mixed_with_another_tool(in_tmp_dir):
    paths = write_paths(in_tmp_dir, INTERLEAVED)

    assert deps("remove", "alpha") == 1
    assert paths.read_text() == INTERLEAVED


@pytest.mark.parametrize("name", ["netmhc_lib", "tool_venv", "tool_module", "tool_path"])
def test_add_refuses_names_configure_cannot_see_as_a_tool(in_tmp_dir, name):
    assert deps("add", name) == 1
    assert not (in_tmp_dir / "paths.py").exists()


def test_add_never_overwrites_a_paths_file_that_does_not_parse(in_tmp_dir):
    broken = "tcell_class_i_path = '/opt/tc1\n"  # unterminated string
    paths = write_paths(in_tmp_dir, broken)

    assert deps("add", "pepx") == 1
    assert paths.read_text() == broken


@pytest.mark.parametrize("sample", ["phbr", "pvc", "bcell-structure", "generated"])
def test_cli_deps_and_configure_see_the_same_tools(in_tmp_dir, sample):
    if sample == "generated":
        cli.add_deps_to_paths("paths.py", ["mhci", "pepx"])
        paths = in_tmp_dir / "paths.py"
    else:
        content = {"phbr": PHBR_STYLE, "pvc": PVC_STYLE, "bcell-structure": BCELL_STRUCTURE_STYLE}[sample]
        paths = write_paths(in_tmp_dir, content)

    seen_by_cli = list(cli._paths_dependencies(paths.read_text()))
    configure = configure_module()
    seen_by_configure = list(configure.detect_dependency_tools(configure.load_config(str(paths))))

    assert seen_by_cli == seen_by_configure


def test_first_run_menu_refuses_names_configure_cannot_see(in_tmp_dir, monkeypatch, capsys):
    # With no paths.py, the menu asks for names and creates the file; it used to
    # write `foo_venv` as a tool, which ./configure reads as a setting instead.
    answers = iter(["foo_venv", "peer", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))

    cli.create_paths_file("paths.py")

    content = (in_tmp_dir / "paths.py").read_text()
    assert "peer_path=None" in content
    assert "foo_venv" not in content
    assert "Not added: 'foo_venv' becomes 'foo_venv', which ends in '_venv'" in capsys.readouterr().out


# The interactive menu (`cli deps` with no names) exits 1 when it changed nothing
# because of an error, like `cli deps add/remove/list`; it used to exit 0 always.


class _Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def terminal(monkeypatch):
    def _answers(*answers):
        monkeypatch.setattr(sys, "stdin", _Terminal())
        replies = iter(answers)
        monkeypatch.setattr("builtins.input", lambda *_: next(replies))

    return _answers


def test_menu_without_a_terminal_stops_with_exit_1(in_tmp_dir, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # a script or CI job: not a terminal

    assert deps(None) == 1
    assert "needs a terminal" in capsys.readouterr().out
    assert not (in_tmp_dir / "paths.py").exists()


def test_menu_on_a_broken_paths_py_exits_1(in_tmp_dir, terminal):
    write_paths(in_tmp_dir, "mhci_path = '/opt/mhci\n")
    terminal()

    assert deps(None) == 1


def test_first_run_menu_with_a_refused_name_exits_1(in_tmp_dir, terminal):
    terminal("foo_venv", "")

    assert deps(None) == 1


def test_first_run_menu_with_a_good_name_exits_0(in_tmp_dir, terminal):
    terminal("peer", "")

    assert deps(None) == 0  # control
    assert "peer_path=None" in (in_tmp_dir / "paths.py").read_text()


def test_menu_cancel_exits_0(in_tmp_dir, terminal):
    cli.add_deps_to_paths("paths.py", ["peer"])
    terminal("3")

    assert deps(None) == 0  # control: cancelling is not an error


def test_end_of_input_at_a_menu_prompt_exits_1(in_tmp_dir, monkeypatch, capsys):
    cli.add_deps_to_paths("paths.py", ["peer"])
    monkeypatch.setattr(sys, "stdin", _Terminal())

    def eof(*_):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)

    assert deps(None) == 1
    assert "Cancelled." in capsys.readouterr().out
