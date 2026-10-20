"""`cli sync` and the version stamp: a development checkout, a stamp that is a number, and
[tool.ngargparser] keys written in any form TOML allows.

A checkout of ngargparser reports the version in its pyproject.toml (0.4.1) while its
files can differ from the 0.4.1 release's, so a sync from the release over a tool synced
from the checkout replaced a 2305-line build.sh with the 593-line release one and said
"Verified". A sync from a checkout now stamps 0.4.1+g<short sha> (.dirty with uncommitted
changes, +local without git), and a release sync over such a stamp stops.
"""

import hashlib
import re
import shutil
import stat
import subprocess
import sys

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
BUILD_SH = "scripts/core/build.sh"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def tree_state(root):
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


@pytest.fixture
def release_install(monkeypatch):
    """ngargparser as a release installs it: not a checkout, not from a local folder."""
    monkeypatch.setattr(cli, "_local_checkout", lambda: None)
    monkeypatch.setattr(cli, "_installed_from_local_folder", lambda: False, raising=False)


def git(cwd, *args):
    config = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *config, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path_factory, monkeypatch):
    """A git checkout of ngargparser, with one commit, that this ngargparser runs from."""
    repo = tmp_path_factory.mktemp("ngargparser-src")
    (repo / "pyproject.toml").write_text('[project]\nname = "ngargparser"\nversion = "0.4.1"\n')
    (repo / "ngargparser").mkdir()
    (repo / "ngargparser" / "cli.py").write_text("# cli\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "one")
    monkeypatch.setattr(cli, "_local_checkout", lambda: str(repo))
    return repo


def stamp_of(project):
    return re.search(r'(?m)^scaffold_version = "([^"]*)"', (project / "pyproject.toml").read_text()).group(1)


def set_stamp_line(project, line):
    pyproject = project / "pyproject.toml"
    pyproject.write_text(re.sub(r"(?m)^scaffold_version = .*$", line, pyproject.read_text()))


# --- 2. A release does not replace what a checkout synced -----------------------------------


def test_a_release_sync_over_a_checkout_stamp_stops(scaffolded_project, release_install, sync_args, strip_ansi, capsys):
    version = cli.__version__
    cli.write_scaffold_version(str(scaffolded_project / "pyproject.toml"), f"{version}+g123abcd")
    build = scaffolded_project / BUILD_SH
    build.write_text(build.read_text() + "# a line the checkout's build.sh has\n")
    before = tree_state(scaffolded_project)
    message = (
        f"✗ This tool was last synced from a development checkout of ngargparser ({version}+g123abcd); the "
        f"installed ngargparser is the {version} release, whose framework files may be older. Nothing was changed. "
        "Sync from that checkout again, or pass --allow-downgrade to take the release's files."
    )

    for dry_run in (True, False):
        assert cli.sync_command(sync_args(dry_run=dry_run)) == 1
        assert message in strip_ansi("".join(capsys.readouterr()))
    assert tree_state(scaffolded_project) == before

    assert cli.sync_command(sync_args(allow_downgrade=True, verify=False)) == 0

    out = strip_ansi("".join(capsys.readouterr()))
    assert (
        f"⚠ Downgrading: this tool was last synced from a development checkout of ngargparser ({version}+g123abcd); "
        f"syncing it to the installed {version} (--allow-downgrade)."
    ) in out
    assert build.read_bytes() == (cli.TEMPLATE_DIR / "build.sh").read_bytes()
    assert stamp_of(scaffolded_project) == version


@needs_git
def test_a_sync_from_a_checkout_stamps_its_commit(scaffolded_project, checkout, sync_args, monkeypatch):
    version = cli.__version__
    cli.write_scaffold_version(str(scaffolded_project / "pyproject.toml"), version)
    head = git(checkout, "rev-parse", "--short", "HEAD")

    assert cli.sync_command(sync_args(verify=False)) == 0
    assert stamp_of(scaffolded_project) == f"{version}+g{head}"

    (checkout / "ngargparser" / "cli.py").write_text("# an uncommitted edit\n")
    assert cli.sync_command(sync_args(verify=False)) == 0
    assert stamp_of(scaffolded_project) == f"{version}+g{head}.dirty"

    # A checkout git cannot read is still not the release.
    shutil.rmtree(checkout / ".git")
    cli.write_scaffold_version(str(scaffolded_project / "pyproject.toml"), version)
    assert cli.sync_command(sync_args(verify=False)) == 0
    assert stamp_of(scaffolded_project) == f"{version}+local"


@needs_git
def test_a_later_commit_of_the_checkout_syncs_and_an_unknown_one_stops(
    scaffolded_project, checkout, sync_args, strip_ansi, capsys
):
    version = cli.__version__
    first = git(checkout, "rev-parse", "--short", "HEAD")
    (checkout / "ngargparser" / "cli.py").write_text("# cli, later\n")
    git(checkout, "commit", "-q", "-am", "two")
    second = git(checkout, "rev-parse", "--short", "HEAD")
    pyproject = str(scaffolded_project / "pyproject.toml")

    # Synced from the first commit; this checkout has it in its history, so its files are newer.
    cli.write_scaffold_version(pyproject, f"{version}+g{first}")
    assert cli.sync_command(sync_args(verify=False)) == 0
    assert stamp_of(scaffolded_project) == f"{version}+g{second}"
    capsys.readouterr()

    # Synced from a commit this checkout does not have: its files may be older.
    cli.write_scaffold_version(pyproject, f"{version}+gdeadbee")
    before = tree_state(scaffolded_project)
    assert cli.sync_command(sync_args(verify=False)) == 1
    out = strip_ansi(capsys.readouterr().err)
    assert (
        f"✗ This tool was last synced from a development checkout of ngargparser ({version}+gdeadbee); the "
        f"installed ngargparser is another checkout or local copy ({version}+g{second}), whose framework files "
        "may be older. Nothing was changed."
    ) in out
    assert tree_state(scaffolded_project) == before


# --- 2. A stamp that is not a string ----------------------------------------------------------


def test_a_number_stamp_is_compared_as_the_version_it_spells(
    scaffolded_project, release_install, sync_args, strip_ansi, capsys
):
    set_stamp_line(scaffolded_project, "scaffold_version = 9.5")
    before = tree_state(scaffolded_project)

    assert cli.sync_command(sync_args()) == 1
    out = strip_ansi("".join(capsys.readouterr()))
    assert (
        f"✗ This tool was last synced with ngargparser 9.5, which is newer than the installed ngargparser "
        f"{cli.__version__}."
    ) in out
    assert tree_state(scaffolded_project) == before

    # An older number goes on, and the warning says what is wrong with the value.
    set_stamp_line(scaffolded_project, "scaffold_version = 0.1")
    assert cli.sync_command(sync_args()) == 0
    out = strip_ansi("".join(capsys.readouterr()))
    assert (
        "pyproject.toml sets scaffold_version under [tool.ngargparser] to a value that is not a quoted string (0.1), "
        f'which cli sync does not edit. Set scaffold_version = "{cli.__version__}" in it by hand.'
    ) in out
    assert "an inline table, a dotted key" not in out


def test_a_stamp_of_another_type_is_named_and_the_sync_goes_on(
    scaffolded_project, release_install, sync_args, strip_ansi, capsys
):
    set_stamp_line(scaffolded_project, "scaffold_version = true")

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert (
        "⚠ pyproject.toml sets scaffold_version = true under [tool.ngargparser], which is not a version string, so "
        "sync cannot tell which ngargparser last synced this tool; it goes on without that check."
    ) in out


def test_a_stamp_like_line_inside_a_multi_line_string_is_not_the_stamp(
    scaffolded_project, release_install, sync_args, strip_ansi, capsys
):
    pyproject = scaffolded_project / "pyproject.toml"
    set_stamp_line(scaffolded_project, f'scaffold_version = "{cli.__version__}"')
    pyproject.write_text(
        pyproject.read_text().replace(
            "[tool.ngargparser]\n", '[tool.ngargparser]\nnotes = """\nscaffold_version = "9.9.9"\n"""\n', 1
        )
    )
    before = pyproject.read_bytes()
    assert b'scaffold_version = "9.9.9"' in before

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "newer than the installed" not in out
    assert pyproject.read_bytes() == before  # the string stays as written


# --- 3. entry in an inline table or as a dotted key ----------------------------------------------

PYPROJECT_HEAD = '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["python-dotenv"]\n\n'


@pytest.mark.parametrize(
    "table",
    [
        '[tool]\nngargparser = {{ scaffold_version = "{v}", app_name = "demo", entry = "{entry}" }}\n',
        '[tool]\nngargparser.scaffold_version = "{v}"\nngargparser.app_name = "demo"\nngargparser.entry = "{entry}"\n',
    ],
    ids=["inline table", "dotted key"],
)
def test_an_entry_in_any_toml_form_is_the_entry_the_test_drive_runs(
    scaffolded_project, release_install, sync_args, strip_ansi, capsys, monkeypatch, table
):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    pyproject = scaffolded_project / "pyproject.toml"
    (scaffolded_project / CORE_FILE).write_text((scaffolded_project / CORE_FILE).read_text() + "# stale\n")

    pyproject.write_text(PYPROJECT_HEAD + table.format(v=cli.__version__, entry="src/run_demo.py"))
    assert cli.sync_command(sync_args()) == 0
    out = strip_ansi("".join(capsys.readouterr()))
    assert "must be a quoted path" not in out
    assert "✓ Verified: every help screen that worked before the sync still works (4 of 4)." in out

    # The build's rules still apply to it: a file that does not exist stops the sync.
    (scaffolded_project / CORE_FILE).write_text((scaffolded_project / CORE_FILE).read_text() + "# stale\n")
    pyproject.write_text(PYPROJECT_HEAD + table.format(v=cli.__version__, entry="src/run_gone.py"))
    assert cli.sync_command(sync_args()) == 1
    assert (
        'entry = "src/run_gone.py" under [tool.ngargparser] in pyproject.toml names a file that does not exist'
    ) in strip_ansi("".join(capsys.readouterr()))
