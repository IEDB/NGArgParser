"""`cli deps`: writes that cannot cut paths.py short, a paths.py with a byte order mark,
and a title that belongs to another dependency.

paths.py and paths.py.bak are written through a temporary file renamed over them, so a
write that fails (a file size limit here, a full disk) leaves both as they were. The size
limit runs in a child process: `ulimit -f 1` there, with Python's default of ignoring
SIGXFSZ, so the write fails with "File too large".
"""

import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PYPROJECT = '[project]\nname = "demo"\n\n[tool.ngargparser]\napp_name = "demo"\n'
BOM = "﻿"

# A title above another dependency's settings: what a hand-edited file can have.
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
    (in_tmp_dir / "pyproject.toml").write_text(PYPROJECT)
    return in_tmp_dir


def deps_with_a_file_size_limit(folder, *argv, limit=1024):
    """Run `cli deps <argv>` in `folder` in a child process that may not write files over `limit` bytes."""
    package_root = os.path.dirname(os.path.dirname(cli.__file__))
    code = (
        "import resource, sys\n"
        f"resource.setrlimit(resource.RLIMIT_FSIZE, ({limit}, {limit}))\n"
        "from ngargparser.cli import main\n"
        f"sys.argv = ['cli', 'deps', *{list(argv)!r}]\n"
        "sys.exit(main())\n"
    )
    # No bytecode: under the limit, Python would write ngargparser's .pyc files cut short
    # (one short write), and every later import of the package would fail.
    env = {**os.environ, "PYTHONPATH": package_root, "NGARGPARSER_NO_UPDATE_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-c", code], cwd=folder, env=env, capture_output=True, text=True, timeout=60)


# --- 6a. A write that fails leaves paths.py and its backup whole ---------------------------


@pytest.mark.skipif(sys.platform != "linux", reason="needs RLIMIT_FSIZE as Linux applies it")
def test_a_write_cut_short_by_a_file_size_limit_changes_neither_file(tool, strip_ansi):
    assert deps("add", "pepx", "netmhcpan") == 0
    assert deps("remove", "netmhcpan") == 0  # leaves a good paths.py.bak
    assert deps("add", "netmhcpan", "mhci") == 0
    paths, backup = tool / "paths.py", tool / "paths.py.bak"
    good, good_backup = paths.read_bytes(), backup.read_bytes()
    assert len(good) > 1024 and len(good_backup) > 1024

    added = deps_with_a_file_size_limit(tool, "add", "bigger")

    assert added.returncode == 1, added.stdout + added.stderr
    assert "✗ Could not write 'paths.py': File too large." in strip_ansi(added.stderr)
    assert paths.read_bytes() == good  # was cut to 1024 bytes

    removed = deps_with_a_file_size_limit(tool, "remove", "mhci")

    assert removed.returncode == 1, removed.stdout + removed.stderr
    assert "✗ Could not write 'paths.py.bak': File too large." in strip_ansi(removed.stderr)
    assert paths.read_bytes() == good
    assert backup.read_bytes() == good_backup  # was overwritten with a partial copy
    assert [p.name for p in tool.iterdir() if p.name.endswith(".tmp")] == []  # no temporary file left


# --- 6b. A paths.py that starts with a byte order mark -------------------------------------


def test_a_paths_file_with_a_byte_order_mark_is_read_and_keeps_it(tool, strip_ansi, capsys):
    paths = tool / "paths.py"
    paths.write_text(BOM + SWAPPED_TITLES.replace("''' [ Pepx ] '''", "''' [ Tcell Class I ] '''"), encoding="utf-8")

    assert deps("list") == 0
    listed = strip_ansi(capsys.readouterr().out)
    assert "pepx" in listed and "tcell_class_i" in listed and "doesn't parse" not in listed

    paths.chmod(0o640)
    assert deps("add", "mhci") == 0
    assert paths.read_text(encoding="utf-8").startswith(BOM + "''' [ Tcell Class I ] '''")
    assert "mhci_path=None" in paths.read_text(encoding="utf-8")

    assert deps("remove", "pepx") == 0
    content = paths.read_text(encoding="utf-8")
    assert content.startswith(BOM) and content.count(BOM) == 1
    assert "pepx_path" not in content and "tcell_class_i_path='/opt/tc1'" in content
    assert (tool / "paths.py.bak").read_bytes().startswith(BOM.encode("utf-8"))
    # Rewritten through a temporary file, it keeps its mode, and the backup gets that mode too.
    assert (paths.stat().st_mode & 0o777, (tool / "paths.py.bak").stat().st_mode & 0o777) == (0o640, 0o640)


# --- 6c. A title over another dependency's settings ----------------------------------------


def test_removing_a_name_again_does_not_take_the_block_its_title_is_on(tool, strip_ansi, capsys):
    paths = tool / "paths.py"
    paths.write_text(SWAPPED_TITLES)
    assert deps("remove", "pepx") == 0  # by its variables: the block titled T Cell Class I
    after_first = paths.read_text()
    capsys.readouterr()

    assert deps("remove", "pepx") == 1

    out = strip_ansi(capsys.readouterr().err)
    assert "✗ Not removed: pepx is not declared in paths.py." in out
    assert (
        "The title Pepx is on the block of tcell_class_i; to remove that block, run: cli deps remove tcell_class_i"
        in out
    )
    assert paths.read_text() == after_first  # tcell_class_i's block is still there
    assert sorted(cli._paths_dependencies(paths.read_text())) == ["tcell_class_i"]
    # A name written as a title ("T Cell Class I") still removes the block it is on, as
    # test_deps_folder_and_matching.py checks.
