"""Dependency names (one rule, in dep_names.py), the cli deps remove menu and report, a lost
output with --no-verify, the help-screen timeout in a dry run, and what cli generate makes.

./configure accepted dependency names cli deps refuses: the rule lived only in cli.py. It is
now ngargparser/dep_names.py, which cli.py imports and every tool gets as src/core/dep_names.py.
"""

import ast
import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def edit(project, path):
    target = project / path
    target.write_text(target.read_text() + "# a local edit that sync will overwrite\n")


# --- 1. One rule for dependency names ------------------------------------------------------------


def test_the_name_rule_is_one_module_cli_imports_and_every_tool_gets(scaffolded_project):
    from ngargparser import dep_names

    assert cli.invalid_dep_name is dep_names.invalid_dep_name
    assert cli.normalize_name is dep_names.normalize_name
    # ./configure reads it before the tool has an environment: plain Python, nothing to install.
    tree = ast.parse((cli.NGPARSER_DIR / "dep_names.py").read_text())
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    imported |= {
        node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    allowed = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else {"re", "typing"}
    assert imported and imported <= allowed, imported
    for name in ("ld_library", "app", "uv"):
        assert dep_names.invalid_dep_name(name), name
    assert dep_names.invalid_dep_name("netmhcpan") is None

    shipped = scaffolded_project / "src" / "core" / "dep_names.py"
    assert shipped.read_bytes() == (cli.NGPARSER_DIR / "dep_names.py").read_bytes()
    assert (f"{cli.NGPARSER_DIR}/dep_names.py", "src/core/dep_names.py", False) in cli._sync_framework_files()


def test_sync_gives_a_tool_without_it_the_name_rule(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    (project / "src" / "core" / "dep_names.py").unlink()

    assert cli.sync_command(sync_args(verify=False)) == 0

    assert "Created src/core/dep_names.py" in strip_ansi(capsys.readouterr().out)
    assert (project / "src" / "core" / "dep_names.py").read_bytes() == (cli.NGPARSER_DIR / "dep_names.py").read_bytes()


def test_deps_list_warns_about_names_written_by_hand_that_the_rule_refuses(scaffolded_project, strip_ansi, capsys):
    (scaffolded_project / "paths.py").write_text(
        "ld_library_path = None\napp_path = None\nuv_path = None\nnetmhcpan_path = None\n"
    )

    assert cli.deps_command(Namespace(deps_action="list", names=[])) == 0

    out = strip_ansi(capsys.readouterr().out)
    for name in ("ld_library", "app", "uv"):
        assert f"⚠  'paths.py' declares {name}, a name cli deps refuses: {cli.invalid_dep_name(name)}\n" in out
    assert "declares netmhcpan" not in out
    assert "○ netmhcpan" in out  # every block is still listed


# --- 2. cli deps remove ----------------------------------------------------------------------------------


def test_the_menu_refuses_a_reversed_range_in_one_line(scaffolded_project, monkeypatch, capsys):
    (scaffolded_project / "paths.py").write_text("a_path = None\nb_path = None\nc_path = None\n")
    answers = iter(["3-2", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    rc = cli.remove_dependencies_from_file("paths.py", *cli.parse_existing_paths_file("paths.py"))

    out = capsys.readouterr().out
    assert rc == 0
    assert "Invalid range 3-2: write the smaller number first (e.g., 1-3).\n" in out
    assert "Remove 0 dependencies" not in out
    assert "Removal cancelled." in out


def test_removing_a_block_inside_another_does_not_report_its_lines_as_kept(scaffolded_project, strip_ansi, capsys):
    (scaffolded_project / "paths.py").write_text(
        "''' [ T Cell ] '''\nt_cell_path = None\n\n''' [ B ] '''\nb_path = None\nb_venv = None\n\n"
        "# a note of mine\nt_cell_venv = None\n"
    )

    assert cli.deps_command(Namespace(deps_action="remove", names=["t_cell", "b"])) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "⚠  Kept 1 line(s) that cli deps did not write:\n     # a note of mine\n" in out
    assert "[ B ]" not in out and "b_path" not in out
    assert (scaffolded_project / "paths.py").read_text() == "# a note of mine\n"


# --- 3. cli sync -------------------------------------------------------------------------------------------

# A child sync without the test drive whose stdout is a pipe the test closes after the first line, as
# `cli sync --no-verify | head -1` does. It waits at the point the sync stands until the test has closed it.
HEAD_ONE = """
import os, sys, time
from argparse import Namespace
from ngargparser import cli
cli._local_version_label = lambda: None
real = getattr(cli, "_flush_output", None)  # where the sync stands; an ngargparser without it does not wait
def flush():
    while os.path.exists({flag!r}):
        time.sleep(0.05)
    real()
if real is not None:
    cli._flush_output = flush
sys.exit(cli.sync_command(Namespace(upgrade=False, dev=False, ref="latest", dry_run=False, backup=True, verify=False)))
"""


@pytest.mark.skipif(os.name != "posix", reason="needs a POSIX pipe")
def test_no_verify_with_a_reader_that_quits_rolls_back_like_a_plain_sync(scaffolded_project, strip_ansi, tmp_path):
    project = scaffolded_project
    edit(project, CORE_FILE)
    before = (project / CORE_FILE).read_bytes()
    flag = tmp_path / "hold"
    flag.write_text("")
    env = {
        **os.environ,
        "PYTHONPATH": PACKAGE_ROOT,
        "NGARGPARSER_NO_SELF_UPGRADE": "1",
        "NGARGPARSER_NO_UPDATE_CHECK": "1",
    }
    sync = subprocess.Popen(
        [sys.executable, "-c", HEAD_ONE.format(flag=str(flag))],
        cwd=project,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        first = sync.stdout.readline()
        sync.stdout.close()  # head -1 has its line, and quits
    finally:
        flag.unlink()
        err = strip_ansi(sync.stderr.read())
        rc = sync.wait(timeout=120)

    assert first == "Synchronizing framework files to latest version...\n"
    assert rc == 1, err
    assert err == (
        "✗ cli sync stopped: its output could not be written (Broken pipe).\n"
        "  Every file was restored to its state before the sync.\n"
    )
    assert (project / CORE_FILE).read_bytes() == before


def test_a_help_screen_timeout_that_is_not_used_is_said_in_a_dry_run_too(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch
):
    edit(scaffolded_project, CORE_FILE)
    monkeypatch.setenv("NGARGPARSER_SYNC_HELP_TIMEOUT", "abc")

    assert cli.sync_command(sync_args(dry_run=True)) == 1  # no .venv: a real run would stop

    out = strip_ansi(capsys.readouterr().out)
    assert (
        "⚠ NGARGPARSER_SYNC_HELP_TIMEOUT=abc is not a number of seconds above 0 and at most 86400, so each help "
        "screen may take 60s.\n"
    ) in out


# --- 4. cli generate -----------------------------------------------------------------------------------------


def test_a_blank_tool_has_the_input_its_next_steps_run(in_tmp_dir, strip_ansi, capsys):
    assert not cli.startapp_command(Namespace(project_name="my-tool"))

    out = strip_ansi(capsys.readouterr().out)
    assert "  uv run python src/run_my_tool.py predict -j src/example.json\n" in out
    assert "<input.json>" not in out
    sample = in_tmp_dir / "my-tool" / "src" / "example.json"
    assert sample.read_text() == '{"peptide": ["ADMGHLKY"]}\n'
    # The generated parser reads it, as its own test reads the same input.
    assert repr('{"peptide": ["ADMGHLKY"]}') in (in_tmp_dir / "my-tool" / "tests" / "test_parser.py").read_text()
    run = subprocess.run(
        [sys.executable, "src/run_my_tool.py", "predict", "-j", "src/example.json"],
        cwd=in_tmp_dir / "my-tool",
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1
    assert run.stderr == "predict is not implemented yet: edit src/run_my_tool.py\n"


@pytest.mark.parametrize("name", ["test", "testing", "test-tool", "Test_x"])
def test_a_name_whose_parser_class_starts_with_test_makes_a_tool(in_tmp_dir, name):
    # NGArgumentParser sets __test__ = False, so pytest never collects a parser class
    # (TestToolArgumentParser), and the name is a tool name like any other.
    assert cli.validate_project_name(name) is None
    assert not cli.startapp_command(Namespace(project_name=name))
    assert (in_tmp_dir / name / "src" / f"run_{cli.format_project_name(name)}.py").is_file()
