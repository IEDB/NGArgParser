"""`cli sync`: stamp quoting, the downgrade guard, the tool-folder check, and its messages.

Each test names the behavior it pins. "Nothing changed" is checked against a hash
of every file in the project, as in test_sync_safety.py.
"""

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

from ngargparser import cli


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # These tests run from a git checkout, whose generate and sync stamp <version>+g<sha>
    # (see test_sync_checkout_stamps.py). Stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


CORE_FILE = "src/core/NGArgumentParser.py"
PARSER_FILE = "src/DemoArgumentParser.py"
VERSION = cli.__version__


def _has_toml_parser():
    try:
        import tomllib  # noqa: F401
    except ImportError:
        try:
            import tomli  # noqa: F401
        except ImportError:
            return False
    return True


# Sync finds a stamp written as a dotted key or an inline table only through a TOML parser.
needs_toml = pytest.mark.skipif(not _has_toml_parser(), reason="needs tomllib or tomli")


def tree_state(root):
    """Every file (content hash + mode) and every directory under `root`."""
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


def make_stale(project):
    core = project / CORE_FILE
    core.write_text(core.read_text() + "\n# a local edit that sync will overwrite\n")


def add_unlisted_dependency(project):
    """A hand-added helper inside a framework file: sync overwrites it and the tool stops starting."""
    core = project / CORE_FILE
    core.write_text(core.read_text() + '\n\ndef local_helper():\n    return "added by hand"\n')
    parser = project / PARSER_FILE
    parser.write_text("from core.NGArgumentParser import local_helper  # noqa: F401\n" + parser.read_text())


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


def set_ngargparser_table(project, table):
    """Replace the generated [tool.ngargparser] table (the file's last one) with `table`."""
    pyproject = project / "pyproject.toml"
    head = pyproject.read_text().split("[tool.ngargparser]")[0]
    pyproject.write_text(head + table)
    return pyproject


# --- 1. Stamp quoting ---------------------------------------------------------------
# app_name = 'base-tool' is valid TOML. Sync used to print "Added app_name", fail on
# "Cannot overwrite a value", and roll back, while the dry run predicted success.


def test_single_quoted_app_name_is_read_and_kept(scaffolded_project, sync_args, strip_ansi, capsys):
    pyproject = set_ngargparser_table(
        scaffolded_project, f"[tool.ngargparser]\nscaffold_version = \"{VERSION}\"\napp_name = 'base-tool'\n"
    )
    before = pyproject.read_text()

    assert cli.sync_command(sync_args(dry_run=True)) == 0
    preview = strip_ansi(capsys.readouterr().out)
    assert cli.sync_command(sync_args()) == 0
    real = strip_ansi(capsys.readouterr().out)

    assert "app_name" not in preview and "app_name" not in real
    assert pyproject.read_text() == before


def test_single_quoted_stamp_is_replaced_in_its_own_quotes(scaffolded_project, sync_args, strip_ansi, capsys):
    pyproject = set_ngargparser_table(
        scaffolded_project, "[tool.ngargparser]\nscaffold_version = '0.3.0'  # by hand\napp_name = 'demo'\n"
    )

    assert cli.sync_command(sync_args()) == 0

    assert f"scaffold_version: 0.3.0 → {VERSION}" in strip_ansi(capsys.readouterr().out)
    content = pyproject.read_text()
    assert content.endswith(f"[tool.ngargparser]\nscaffold_version = '{VERSION}'  # by hand\napp_name = 'demo'\n")


def test_writer_keeps_a_literal_string_literal(tmp_path):
    target = tmp_path / "pyproject.toml"
    target.write_text("[tool.ngargparser]\nscaffold_version = '0.9.0'\n")

    assert cli.write_scaffold_version(str(target), "1.0.0") == "0.9.0"
    assert target.read_text() == "[tool.ngargparser]\nscaffold_version = '1.0.0'\n"


def test_project_stamp_is_read_from_single_quotes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ngargparser]\nscaffold_version = '0.3.0'\napp_name = 'x'\n")

    assert cli._project_scaffold_version() == "0.3.0"
    assert cli._project_ngargparser_key("app_name") == "x"


# Forms the line editor does not edit: sync warns with the value to set by hand and goes on.
UNEDITABLE_STAMPS = {
    "dotted key": '[tool]\nngargparser.scaffold_version = "0.3.0"\nngargparser.app_name = "demo"\n',
    "inline table": '[tool]\nngargparser = { scaffold_version = "0.3.0", app_name = "demo" }\n',
    "multi-line string": '[tool.ngargparser]\nscaffold_version = """0.3.0"""\napp_name = "demo"\n',
}


@needs_toml
@pytest.mark.parametrize("form", list(UNEDITABLE_STAMPS))
def test_stamp_in_a_form_the_editor_does_not_edit_warns_and_goes_on(
    runnable_project, sync_args, strip_ansi, capsys, form
):
    pyproject = set_ngargparser_table(runnable_project, UNEDITABLE_STAMPS[form])
    before = pyproject.read_text()
    make_stale(runnable_project)
    warning = f'Set scaffold_version = "{VERSION}" in it by hand.'

    assert cli.sync_command(sync_args(dry_run=True)) == 0
    preview = strip_ansi(capsys.readouterr().out)
    assert cli.sync_command(sync_args()) == 0
    real = strip_ansi(capsys.readouterr().out)

    assert warning in preview and warning in real
    assert "Error during synchronization" not in real
    assert pyproject.read_text() == before
    assert "a local edit that sync will overwrite" not in (runnable_project / CORE_FILE).read_text()


@needs_toml
def test_app_name_missing_from_a_dotted_table_warns_and_goes_on(scaffolded_project, sync_args, strip_ansi, capsys):
    pyproject = set_ngargparser_table(scaffolded_project, f'[tool]\nngargparser.scaffold_version = "{VERSION}"\n')
    before = pyproject.read_text()

    assert cli.sync_command(sync_args(dry_run=True)) == 0
    preview = strip_ansi(capsys.readouterr().out)
    assert cli.sync_command(sync_args()) == 0
    real = strip_ansi(capsys.readouterr().out)

    assert 'Set app_name = "demo" in it by hand.' in preview
    assert 'Set app_name = "demo" in it by hand.' in real
    assert "Added app_name" not in real
    assert pyproject.read_text() == before


# --- 2. Downgrade guard ---------------------------------------------------------------

NEWER = "99.0.0"


def test_sync_refuses_to_downgrade_and_writes_nothing(scaffolded_project, sync_args, strip_ansi, capsys):
    cli.write_scaffold_version(str(scaffolded_project / "pyproject.toml"), NEWER)
    make_stale(scaffolded_project)
    before = tree_state(scaffolded_project)

    rc = cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert f"last synced with ngargparser {NEWER}, which is newer than the installed ngargparser {VERSION}" in out
    assert "cli upgrade" in out and "cli sync --allow-downgrade" in out
    assert tree_state(scaffolded_project) == before


def test_dry_run_refuses_to_downgrade_too(scaffolded_project, sync_args, strip_ansi, capsys):
    cli.write_scaffold_version(str(scaffolded_project / "pyproject.toml"), NEWER)

    assert cli.sync_command(sync_args(dry_run=True)) == 1
    assert "which is newer than the installed ngargparser" in strip_ansi("".join(capsys.readouterr()))


def test_allow_downgrade_syncs_and_restamps(scaffolded_project, sync_args, strip_ansi, capsys):
    pyproject = scaffolded_project / "pyproject.toml"
    cli.write_scaffold_version(str(pyproject), NEWER)

    assert cli.sync_command(sync_args(allow_downgrade=True)) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert f"Downgrading: this tool was last synced with ngargparser {NEWER}" in out
    assert cli._project_scaffold_version() == VERSION


def test_allow_downgrade_from_the_environment(scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch):
    # What the re-exec'd sync sees: no flag, only the variable.
    monkeypatch.setenv("NGARGPARSER_SYNC_ALLOW_DOWNGRADE", "1")
    cli.write_scaffold_version(str(scaffolded_project / "pyproject.toml"), NEWER)

    assert cli.sync_command(sync_args()) == 0
    assert "Downgrading" in strip_ansi(capsys.readouterr().out)


@pytest.mark.parametrize("stamp", ["0.0.1", "not-a-version", VERSION])
def test_older_equal_or_unparseable_stamp_is_not_a_downgrade(scaffolded_project, sync_args, strip_ansi, capsys, stamp):
    # Control: only a newer stamp is refused.
    cli.write_scaffold_version(str(scaffolded_project / "pyproject.toml"), stamp)

    assert cli.sync_command(sync_args()) == 0
    out = strip_ansi(capsys.readouterr().out)
    assert "newer than the installed" not in out and "Downgrading" not in out


@pytest.mark.parametrize("allow, carried", [(True, "1"), (False, None)])
def test_self_upgrade_re_exec_carries_allow_downgrade(scaffolded_project, sync_args, monkeypatch, allow, carried):
    seen = {}

    def fake_exec(file, argv, env):
        seen["argv"], seen["env"] = argv, env
        raise SystemExit(0)

    monkeypatch.delenv("NGARGPARSER_NO_SELF_UPGRADE", raising=False)
    monkeypatch.delenv("NGARGPARSER_SYNC_ALLOW_DOWNGRADE", raising=False)
    monkeypatch.setattr(cli, "_local_checkout", lambda: None)
    monkeypatch.setattr(
        cli, "_resolve_upgrade_url", lambda *a, **k: ("https://example.invalid/repo.git@v9.9.9", "v9.9.9")
    )
    monkeypatch.setattr(cli, "_run_self_upgrade", lambda url: 0)
    monkeypatch.setattr(cli.os, "execvpe", fake_exec)

    with pytest.raises(SystemExit):
        cli.sync_command(sync_args(upgrade=True, allow_downgrade=allow))

    assert seen["argv"][1:] == ["s"]
    assert seen["env"].get("NGARGPARSER_SYNC_ALLOW_DOWNGRADE") == carried


def test_sync_help_lists_allow_downgrade():
    env = {**os.environ, "CI": "1", "NGARGPARSER_NO_UPDATE_CHECK": "1", "NGARGPARSER_NO_SELF_UPGRADE": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "ngargparser.cli", "sync", "--help"], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0
    assert "--allow-downgrade" in result.stdout


# --- 3. Tool-folder check ---------------------------------------------------------------


def test_sync_refuses_a_build_copy_and_writes_nothing(scaffolded_project, sync_args, strip_ansi, capsys):
    (scaffolded_project / ".ngargparser-build").write_text("TOOL_NAME=demo\nTOOL_VERSION=1.0.0\n")
    make_stale(scaffolded_project)
    before = tree_state(scaffolded_project)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert "This folder is a build or deployed copy of a tool (it has .ngargparser-build)" in out
    assert "Run cli sync in the tool's source folder" in out
    assert tree_state(scaffolded_project) == before


def test_sync_refuses_a_deployed_copy(scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch):
    # What a deployed tool looks like: <name>/<version>/, no .env, no .gitignore, no record.
    deployed = scaffolded_project.parent / "deployed" / "demo" / "1.0.0"
    shutil.copytree(scaffolded_project, deployed)
    for name in (".env", ".gitignore", "Makefile", "scripts/core/build.sh"):
        (deployed / name).unlink()
    shutil.rmtree(deployed / ".ngargparser")
    (deployed / ".ngargparser-build").write_text("TOOL_NAME=demo\nTOOL_VERSION=1.0.0\n")
    monkeypatch.chdir(deployed)
    before = tree_state(deployed)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert "build or deployed copy" in out and "project now has the latest framework files" not in out
    assert tree_state(deployed) == before


def test_tool_without_scripts_is_synced_and_gets_scripts_core(runnable_project, sync_args):
    shutil.rmtree(runnable_project / "scripts")

    assert cli.sync_command(sync_args()) == 0

    assert (runnable_project / "scripts" / "core" / "build.sh").is_file()


def test_rollback_of_a_tool_without_scripts_removes_the_scripts_it_made(
    runnable_project, sync_args, strip_ansi, capsys
):
    shutil.rmtree(runnable_project / "scripts")
    add_unlisted_dependency(runnable_project)
    before = tree_state(runnable_project)

    assert cli.sync_command(sync_args()) == 1

    assert "Sync rolled back" in strip_ansi("".join(capsys.readouterr()))
    assert tree_state(runnable_project) == before
    assert not (runnable_project / "scripts").exists()


def test_tool_without_src_is_refused_naming_src(scaffolded_project, sync_args, strip_ansi, capsys):
    shutil.rmtree(scaffolded_project / "src")
    shutil.rmtree(scaffolded_project / "scripts")
    before = tree_state(scaffolded_project)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert "This folder has a [tool.ngargparser] table in pyproject.toml, but no src/ folder" in out
    assert "found no [tool.ngargparser] table" not in out  # the old, false reason
    assert tree_state(scaffolded_project) == before


@needs_toml
def test_an_inline_ngargparser_table_marks_a_tool(in_tmp_dir):
    (in_tmp_dir / "pyproject.toml").write_text('[tool]\nngargparser = { app_name = "x" }\n')
    assert cli._is_ngargparser_tool()
    (in_tmp_dir / "pyproject.toml").write_text('[tool.other]\nngargparser = "no"\n')
    assert not cli._is_ngargparser_tool()  # control


# --- 4. Messages ------------------------------------------------------------------------


def test_interrupt_before_any_write_exits_130_without_a_traceback(scaffolded_project, sync_args, capsys, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_find_known_clashes", interrupted)
    before = tree_state(scaffolded_project)

    try:
        rc = cli.sync_command(sync_args())
    except KeyboardInterrupt:
        pytest.fail("the interrupt escaped cli sync (a traceback)")
    assert rc == 130

    assert "Cancelled; nothing changed." in "".join(capsys.readouterr())
    assert tree_state(scaffolded_project) == before


def test_rollback_names_no_backup_it_deletes(runnable_project, sync_args, strip_ansi, capsys):
    # The core file is edited by hand (generate recorded its hash), so it is a local edit,
    # and the edit breaks the tool after the sync, so the sync rolls back.
    add_unlisted_dependency(runnable_project)
    before = tree_state(runnable_project)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert "Sync rolled back" in out and "Every file was restored" in out
    named = re.findall(r"Your version is in (\S+?)\.?$", out, flags=re.M)
    assert all((runnable_project / path).exists() for path in named), named
    assert ".ngargparser/sync-backups" not in out
    assert tree_state(runnable_project) == before


def test_local_edit_is_reported_in_the_summary_of_a_sync_that_stands(runnable_project, sync_args, strip_ansi, capsys):
    # Control for the test above: when the sync stands, the backup is named and exists.
    make_stale(runnable_project)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    named = re.findall(r"Your version is in (\S+?)\.?$", out, flags=re.M)
    assert len(named) == 1 and (runnable_project / named[0]).is_file()


def test_build_conf_left_behind_says_to_merge_by_hand(scaffolded_project, sync_args, strip_ansi, capsys):
    (scaffolded_project / "scripts" / "build.conf").write_text('TARBALL_PREFIX="LJI_"\n')

    assert cli.sync_command(sync_args()) == 0

    assert (
        "scripts/build.conf was not moved: scripts/core/build.conf already exists. The build stops while it "
        "exists; merge it into scripts/core/build.conf by hand and delete it."
    ) in strip_ansi(capsys.readouterr().out)
