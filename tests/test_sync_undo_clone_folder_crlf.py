"""`cli sync`: a second Ctrl-C during the undo, an undo that does not finish, the framework
hash record in a fresh clone, the tool-folder check, a subfolder of a tool, a pyproject.toml
written with CRLF line endings, and the first sync of a freshly generated tool.

"Nothing changed" is checked against a hash of every file in the project, as in
test_sync_safety.py.
"""

import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # These tests run from a git checkout, whose generate and sync stamp <version>+g<sha>
    # (see test_sync_checkout_stamps.py). Stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


CORE_FILE = "src/core/NGArgumentParser.py"
HASHES = ".ngargparser/framework-hashes.json"


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


def make_stale(project, *also):
    """Edit CORE_FILE, and each framework file in `also`, so sync overwrites them. The undo puts
    back only the files the sync changed, so a test of its steps needs more than one."""
    for path in also:
        other = project / path
        other.write_text(other.read_text() + "\n# a local edit that sync will overwrite\n")
    core = project / CORE_FILE
    pristine = core.read_text()
    core.write_text(pristine + "\n# a local edit that sync will overwrite\n")
    return pristine


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


@pytest.fixture
def ctrl_c_once_the_new_files_are_in(monkeypatch):
    """Press Ctrl-C (a real SIGINT) during the test drive that runs after the new framework files are in place."""
    real_check, calls = cli._smoke_check, []

    def interrupted_second_time():
        calls.append(1)
        if len(calls) == 2:
            os.kill(os.getpid(), signal.SIGINT)
        return real_check()

    monkeypatch.setattr(cli, "_smoke_check", interrupted_second_time)


@pytest.fixture
def pre_images(monkeypatch):
    """The folders sync keeps its pre-image in (under .ngargparser/sync-in-progress/), as they are made."""
    real_new, stores = cli._new_sync_in_progress, []

    def recording_new():
        folder, marker = real_new()
        stores.append(os.path.abspath(folder))
        return folder, marker

    monkeypatch.setattr(cli, "_new_sync_in_progress", recording_new)
    return stores


def git(cwd, *args):
    config = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *config, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


# --- 2. The undo runs to the end ------------------------------------------------------------


def test_a_second_ctrl_c_during_the_undo_is_ignored(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch, ctrl_c_once_the_new_files_are_in, pre_images
):
    make_stale(runnable_project, "src/core/app_env.py")
    before = tree_state(runnable_project)
    real_put_back, put_backs = cli._SyncSnapshot._put_back, []

    def put_back_with_a_second_ctrl_c(stored, destination, *temporary):
        put_backs.append(destination)
        if len(put_backs) == 1:
            os.kill(os.getpid(), signal.SIGINT)  # Ctrl-C again, while the files are being put back
        real_put_back(stored, destination, *temporary)

    monkeypatch.setattr(cli._SyncSnapshot, "_put_back", staticmethod(put_back_with_a_second_ctrl_c))
    handler_before = signal.getsignal(signal.SIGINT)

    try:
        rc = cli.sync_command(sync_args())
    except KeyboardInterrupt:
        pytest.fail("the second Ctrl-C stopped the undo")

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 130
    assert "Sync interrupted." in out
    assert "Every file was restored" in out and "Cancelled; nothing changed." in out
    assert len(put_backs) > 1  # the undo went on after the second Ctrl-C
    assert tree_state(runnable_project) == before
    assert signal.getsignal(signal.SIGINT) is handler_before  # Ctrl-C works again afterwards
    assert pre_images and not any(os.path.exists(store) for store in pre_images)


def test_a_ctrl_c_during_the_clean_up_after_the_undo_is_ignored(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch, ctrl_c_once_the_new_files_are_in, pre_images
):
    # Ctrl-C pressed again and again: one lands after the undo, while the pre-image is deleted.
    make_stale(runnable_project)
    before = tree_state(runnable_project)
    real_discard = cli._SyncSnapshot.discard

    def discard_with_a_ctrl_c(self):
        os.kill(os.getpid(), signal.SIGINT)
        real_discard(self)

    monkeypatch.setattr(cli._SyncSnapshot, "discard", discard_with_a_ctrl_c)
    handler_before = signal.getsignal(signal.SIGINT)

    try:
        rc = cli.sync_command(sync_args())
    except KeyboardInterrupt:
        pytest.fail("a Ctrl-C after the undo ended the sync with a traceback")

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 130 and "Cancelled; nothing changed." in out
    assert tree_state(runnable_project) == before
    assert pre_images and not any(os.path.exists(store) for store in pre_images)
    assert signal.getsignal(signal.SIGINT) is handler_before


def test_an_undo_that_does_not_finish_keeps_the_pre_image_and_names_the_backups(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch, ctrl_c_once_the_new_files_are_in, pre_images
):
    pristine = make_stale(runnable_project, "src/core/app_env.py")
    stale = (runnable_project / CORE_FILE).read_bytes()
    real_put_back, put_backs = cli._SyncSnapshot._put_back, []

    def put_back_that_stops(stored, destination, *temporary):
        put_backs.append(destination)
        if len(put_backs) == 2:
            raise RuntimeError("the disk went away")
        real_put_back(stored, destination, *temporary)

    monkeypatch.setattr(cli._SyncSnapshot, "_put_back", staticmethod(put_back_that_stops))

    rc = cli.sync_command(sync_args())

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 130
    assert "Restore did not finish (the disk went away). The files before the sync are in " in out
    assert "Cancelled; nothing changed." not in out
    store = out.split("The files before the sync are in ", 1)[1].split("; ", 1)[0]
    backups = out.split("backups of overwritten files are in ", 1)[1].split("\n", 1)[0].rstrip(".")
    try:
        # In the tool's .ngargparser/, where a reboot does not clear it, renamed so it stands out.
        assert store.startswith(".ngargparser/failed-restore-"), store
        assert pre_images and not any(os.path.exists(path) for path in pre_images)
        # Its marker stays and names it, so the next sync stops until it is dealt with.
        markers = list((runnable_project / ".ngargparser" / "sync-in-progress").glob("*.json"))
        assert len(markers) == 1 and json.loads(markers[0].read_text())["files"] == store
        # Both are complete and keep project-relative paths, so the undo can be finished by hand.
        assert open(os.path.join(store, CORE_FILE), "rb").read() == stale
        assert os.path.isfile(os.path.join(store, "pyproject.toml"))
        assert (runnable_project / backups / CORE_FILE).read_bytes() == stale
        assert pristine.encode() != stale
    finally:
        shutil.rmtree(store, ignore_errors=True)


# --- 3. The framework hash record travels with the tool -------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_tracks_the_hash_record_and_nothing_else_under_ngargparser(scaffolded_project, run_build):
    project = scaffolded_project
    git(project, "init", "-q")
    git(project, "add", "-A")

    staged = git(project, "diff", "--cached", "--name-only").split()
    assert [path for path in staged if path.startswith(".ngargparser/")] == [HASHES]

    # What a sync and a build leave there stays out of git.
    (project / ".ngargparser" / "sync-backups" / "20261018T000000.000000Z-1" / "src").mkdir(parents=True)
    (project / ".ngargparser" / "sync-backups" / "20261018T000000.000000Z-1" / "src" / "x.py").write_text("x\n")
    (project / ".ngargparser" / "libs-stamp").write_text("stamp\n")
    git(project, "add", "-A")
    staged = git(project, "diff", "--cached", "--name-only").split()
    assert [path for path in staged if path.startswith(".ngargparser/")] == [HASHES]

    # The build leaves hidden files out of the tarball, so the record does not ship.
    shutil.rmtree(project / ".ngargparser" / "sync-backups")
    build = run_build(project, env={"NGARGPARSER_SKIP_STARTUP_CHECK": "1"})
    assert not any(name.startswith(".ngargparser/") for name in build.members)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_local_edit_is_named_in_a_fresh_clone(scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch):
    git(scaffolded_project, "init", "-q")
    git(scaffolded_project, "add", "-A")
    git(scaffolded_project, "commit", "-q", "-m", "first")
    clone = scaffolded_project.parent / "clone"
    git(scaffolded_project.parent, "clone", "-q", str(scaffolded_project), str(clone))
    build_sh = clone / "scripts" / "core" / "build.sh"
    edited = build_sh.read_text() + "# my change in the clone\n"
    build_sh.write_text(edited)
    monkeypatch.chdir(clone)
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)  # a clone has no .venv of its own

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "scripts/core/build.sh had local edits; sync replaced them. Your version is in " in out
    backup = out.split("scripts/core/build.sh had local edits; sync replaced them. Your version is in ", 1)[1]
    assert (clone / backup.split("\n", 1)[0].rstrip(".")).read_text() == edited


# --- 4. scripts/core/build.sh alone does not make a tool ------------------------------------


@pytest.fixture
def only_build_sh(in_tmp_dir):
    """A folder with src/ and scripts/core/build.sh, and its own Makefile and README.md."""
    (in_tmp_dir / "src").mkdir()
    (in_tmp_dir / "scripts" / "core").mkdir(parents=True)
    (in_tmp_dir / "scripts" / "core" / "build.sh").write_text("#!/bin/bash\necho mine\n")
    (in_tmp_dir / "Makefile").write_text("all:\n\techo mine\n")
    (in_tmp_dir / "README.md").write_text("# mine\n\nMy project.\n")
    return in_tmp_dir


def test_sync_refuses_a_folder_with_only_scripts_core_build_sh(only_build_sh, sync_args, strip_ansi, capsys):
    before = tree_state(only_build_sh)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert (
        "This folder is not an ngargparser tool: cli sync found no [tool.ngargparser] table in pyproject.toml "
        "and no src/core/NGArgumentParser.py." in out
    )
    assert tree_state(only_build_sh) == before


def test_deps_refuses_and_generate_allows_a_folder_with_only_scripts_core_build_sh(only_build_sh, strip_ansi, capsys):
    assert cli.deps_command(Namespace(deps_action="add", names=["pepx"])) == 1
    assert "cli deps found no [tool.ngargparser] table" in strip_ansi("".join(capsys.readouterr()))
    assert not (only_build_sh / "paths.py").exists()

    # Not a tool, so a tool may be generated here.
    assert not cli.startapp_command(Namespace(project_name="inner"))
    assert (only_build_sh / "inner" / "src" / "run_inner.py").is_file()


# --- 6d. A subfolder of a tool ---------------------------------------------------------------


def test_sync_in_a_subfolder_of_a_tool_names_the_tool(scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch):
    make_stale(scaffolded_project)
    before = tree_state(scaffolded_project)
    monkeypatch.chdir(scaffolded_project / "src")

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert f"✗ This folder is inside the ngargparser tool {scaffolded_project}: run cli sync there: cd " in out
    assert out.rstrip().endswith(f"cd {scaffolded_project}")
    assert tree_state(scaffolded_project) == before  # the tool above is not synced either


# --- 5. A pyproject.toml written with CRLF line endings ----------------------------------------


@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"], ids=["plain", "byte-order-mark"])
def test_sync_keeps_crlf_line_endings_in_pyproject(scaffolded_project, sync_args, bom):
    # An older stamp to replace, and no app_name, which sync adds right below [tool.ngargparser].
    pyproject = scaffolded_project / "pyproject.toml"
    old = pyproject.read_text()
    stamp = f'scaffold_version = "{cli.__version__}"\n'
    assert stamp in old and 'app_name = "demo"\n' in old
    written = old.replace('app_name = "demo"\n', "").replace(stamp, 'scaffold_version = "0.3.0"\n')
    pyproject.write_bytes(bom + written.replace("\n", "\r\n").encode())

    assert cli.sync_command(sync_args()) == 0

    expected = written.replace('scaffold_version = "0.3.0"\n', 'app_name = "demo"\n' + stamp)
    assert pyproject.read_bytes() == bom + expected.replace("\n", "\r\n").encode()


def test_a_key_added_to_a_crlf_pyproject_gets_crlf(in_tmp_dir):
    pyproject = in_tmp_dir / "pyproject.toml"
    pyproject.write_bytes(b'[project]\r\nname = "x"\r\n')

    cli.write_ngargparser_key(str(pyproject), "app_name", "x")

    assert pyproject.read_bytes() == b'[project]\r\nname = "x"\r\n\r\n[tool.ngargparser]\r\napp_name = "x"\r\n'
    # Control: a file written with LF keeps LF.
    pyproject.write_bytes(b'[project]\nname = "x"\n')
    cli.write_ngargparser_key(str(pyproject), "app_name", "x")
    assert pyproject.read_bytes() == b'[project]\nname = "x"\n\n[tool.ngargparser]\napp_name = "x"\n'


# --- 7c. The first sync of a new tool changes nothing --------------------------------------------


def test_sync_on_a_freshly_generated_tool_changes_nothing(scaffolded_project, sync_args, strip_ansi, capsys):
    capsys.readouterr()
    before = tree_state(scaffolded_project)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert tree_state(scaffolded_project) == before
    assert "README.md badge is already current" in out
    assert "No files needed updating - your tool is already up to date!" in out
    summary = out.split("Synchronization Summary:", 1)[1]
    assert "Framework files changed: 0" in summary and "README badge" not in summary and "stamps" not in summary
