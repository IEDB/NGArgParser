"""`cli sync` and the files it writes and puts back: a stdout that is closed, files edited
while the sync runs, a kill -9 while the pre-sync copy is made or while the undo puts a
file back, and a README.md that is a link.

"Nothing changed" is checked against a hash of every file in the project, as in
test_sync_safety.py.
"""

import hashlib
import os
import re
import shlex
import signal
import stat
import subprocess
import sys

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
PARSER_FILE = "src/DemoArgumentParser.py"
PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # Generate and sync stamp the same plain version, so pyproject.toml needs no new stamp.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def tree_state(root, skip=()):
    """Every file (content hash + mode) and every directory under `root`, but those under `skip`."""
    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative or relative.split("/")[0] in skip:
            continue
        if path.is_dir():
            dirs.add(relative)
        else:
            files[relative] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    return files, dirs


def break_after_sync(project):
    """A change that passes the help screens before a sync and fails them after it: the
    project's parser imports a name it added to a framework file, which sync replaces."""
    core = project / CORE_FILE
    core.write_text(core.read_text() + "\nLOCAL_THING = 1\n")
    parser = project / PARSER_FILE
    parser.write_text("from core.NGArgumentParser import LOCAL_THING  # noqa: F401\n" + parser.read_text())


def make_stale(project):
    core = project / CORE_FILE
    core.write_text(core.read_text() + "\n# a local edit that sync will overwrite\n")


def give_venv(project):
    """A .venv/bin/python that runs the test interpreter (a wrapper, not a link)."""
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    if python.is_symlink():  # never write through a link: it can lead to a shared interpreter
        python.unlink()
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)


def subprocess_env():
    return {
        **os.environ,
        "PYTHONPATH": PACKAGE_ROOT,
        "NGARGPARSER_NO_SELF_UPGRADE": "1",
        "NGARGPARSER_NO_UPDATE_CHECK": "1",
    }


def run_commands(project, out):
    """Run the commands the unfinished-sync message ends with (the indented lines after it)."""
    commands = out.split("From the tool folder:\n", 1)[1].splitlines()
    for command in commands:
        assert command.startswith("    "), command
        subprocess.run(command.strip(), shell=True, cwd=project, check=True)
    return [command.strip() for command in commands]


SYNC = "sys.exit(cli.sync_command(Namespace(upgrade=False, dev=False, ref='latest', dry_run=False, backup=True, verify={verify})))\n"


# --- 1. A stdout that is closed ------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="closes stdout with the shell")
def test_with_stdout_closed_a_failed_test_drive_still_undoes_the_sync(scaffolded_project, strip_ansi):
    project = scaffolded_project
    give_venv(project)
    break_after_sync(project)
    before = tree_state(project, skip=(".ngargparser",))
    code = "import sys\nfrom argparse import Namespace\nfrom ngargparser import cli\n" + SYNC.format(verify=True)

    # cli sync >&- : Python starts with no stdout at all (sys.stdout is None).
    run = subprocess.run(
        ["sh", "-c", 'exec "$@" >&-', "sh", sys.executable, "-c", code],
        cwd=project,
        env=subprocess_env(),
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
    )

    err = strip_ansi(run.stderr)
    assert run.returncode == 1, err
    assert "Traceback" not in err
    # The rollback is said on stderr, which is there: the screens that broke, their error, and the undo.
    assert err.startswith("\n✗ Sync rolled back: these commands worked before the sync and fail after it.\n"), err
    assert re.search(
        r"\n  src/run_demo\.py --help\n      ImportError: cannot import name 'LOCAL_THING' from 'core\.NGArgumentParser' ",
        err,
    ), err
    assert err.count("ImportError: cannot import name 'LOCAL_THING'") == 4  # and 3 other help screens
    assert err.endswith(
        "  Every file was restored to its state before the sync.\n"
        "  Fix the cause, or sync without these checks: cli sync --no-verify\n"
    ), err
    assert tree_state(project, skip=(".ngargparser",)) == before
    assert not (project / ".ngargparser" / "sync-in-progress").exists()

    # Both closed (cli sync >&- 2>&-): nothing to say it on, and the undo still runs.
    run = subprocess.run(
        ["sh", "-c", 'exec "$@" >&- 2>&-', "sh", sys.executable, "-c", code],
        cwd=project,
        env=subprocess_env(),
        timeout=300,
    )
    assert run.returncode == 1
    assert tree_state(project, skip=(".ngargparser",)) == before


# --- 2. Files edited while the sync runs --------------------------------------------------------


def test_edits_made_while_the_sync_runs_survive_its_rollback(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch
):
    project = scaffolded_project
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    break_after_sync(project)
    before = tree_state(project, skip=(".ngargparser",))
    core_before = (project / CORE_FILE).read_bytes()
    edited = ["README.md", ".gitignore", "pyproject.toml", CORE_FILE]
    real_check, calls = cli._smoke_check, []

    def edit_during_the_screens_after_the_update():
        calls.append(1)
        if len(calls) == 2:  # the new files are in: someone edits these meanwhile
            for path in edited:
                with open(project / path, "a", encoding="utf-8") as f:
                    f.write("\n# edited while the sync ran\n")
        return real_check()

    monkeypatch.setattr(cli, "_smoke_check", edit_during_the_screens_after_the_update)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert "  └ README.md badge is already current (or no README found)\n" in out  # this run did not write it
    # Files this run did not write keep the edit: the undo leaves them alone.
    for path in ("README.md", ".gitignore", "pyproject.toml"):
        assert (project / path).read_text().endswith("\n# edited while the sync ran\n"), path
    # A file it wrote and that was edited since is moved aside, named, and the file from before the sync put back.
    aside = re.search(
        rf"  ⚠ {re.escape(CORE_FILE)} changed after the sync wrote it \(an edit made while the sync ran\); "
        r"that version was moved to (\.ngargparser/edited-during-sync-\d{8}T\d{6}Z/\S+)\.\n",
        out,
    )
    assert aside, out
    assert aside.group(1) == f"{aside.group(1).split('/src/')[0]}/{CORE_FILE}"
    kept = (project / aside.group(1)).read_text()
    assert "LOCAL_THING" not in kept and kept.endswith("\n# edited while the sync ran\n")  # the new file, edited
    assert (project / CORE_FILE).read_bytes() == core_before
    assert "  Every file was restored to its state before the sync.\n" in out
    # Everything else is as it was before the sync.
    files_before, dirs_before = before
    files_after, dirs_after = tree_state(project, skip=(".ngargparser",))
    assert {p: h for p, h in files_after.items() if p not in edited} == {
        p: h for p, h in files_before.items() if p not in edited
    }
    assert dirs_after == dirs_before


def test_a_rollback_without_edits_moves_nothing_aside(scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch):
    # Control: the same rollback with no edit during the sync puts every file back and keeps no copy.
    project = scaffolded_project
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    break_after_sync(project)
    before = tree_state(project, skip=(".ngargparser",))

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert "changed after the sync wrote it" not in out
    assert "  Every file was restored to its state before the sync.\n" in out
    assert tree_state(project, skip=(".ngargparser",)) == before
    assert not list((project / ".ngargparser").glob("edited-during-sync-*"))


# --- 4. A kill -9 while the pre-sync copy is made -----------------------------------------------

KILLED_WHILE_COPYING = """\
import os, shutil, signal, sys
from argparse import Namespace
from ngargparser import cli

real = shutil.copyfile


def copyfile(src, dst, *args, **kwargs):
    # kill -9 halfway through the copy of README.md: half of it is on disk where the copy goes.
    if os.path.abspath(src) == os.path.abspath("README.md"):
        with open(src, "rb") as f:
            data = f.read()
        with open(dst, "wb") as f:
            f.write(data[: len(data) // 2])
        os.kill(os.getpid(), signal.SIGKILL)
    return real(src, dst, *args, **kwargs)


shutil.copyfile = copyfile
"""


@pytest.mark.skipif(os.name != "posix", reason="needs kill -9")
def test_a_kill_while_the_first_copy_is_made_gives_commands_that_keep_every_file(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    make_stale(project)
    readme = project / "README.md"
    readme.write_text(readme.read_text() + "".join(f"line {n} of a long README\n" for n in range(2000)))
    before = tree_state(project, skip=(".ngargparser",))

    run = subprocess.run(
        [sys.executable, "-c", KILLED_WHILE_COPYING + SYNC.format(verify=False)],
        cwd=project,
        env=subprocess_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == -signal.SIGKILL, run.stderr
    assert tree_state(project, skip=(".ngargparser",)) == before  # killed before it wrote any file

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    folder = out.split("The files from before it are in ", 1)[1].split("/. ", 1)[0]
    assert (
        f"The files from before it are in {folder}/. It stopped while it copied README.md, before it wrote any "
        "file, so that copy is incomplete and is left out. None of the files it wrote differs from its copy "
        "there, so none needs copying back, then delete that folder and run cli sync again. From the tool folder:\n"
    ) in out
    assert "cp -a" not in out
    ran = run_commands(project, out)
    assert ran == [f"rm -r {shlex.quote(folder)} {shlex.quote(folder + '.partial')}"]
    assert tree_state(project, skip=(".ngargparser",)) == before  # README.md is whole

    # The next sync goes ahead, and cleans up the marker that was left.
    assert cli.sync_command(sync_args(verify=False)) == 0
    capsys.readouterr()
    assert not (project / ".ngargparser" / "sync-in-progress").exists()


def test_a_whole_pre_sync_copy_leaves_no_partial_folder(scaffolded_project, monkeypatch):
    # Control: every copy made in full lands in the folder under its path, and the partial folder goes.
    (scaffolded_project / CORE_FILE).write_text("# hand edited\n")
    snapshot = cli._SyncSnapshot(cli._sync_touched_paths(cli._sync_framework_files()))
    store = scaffolded_project / snapshot._store
    try:
        assert (store / CORE_FILE).read_text() == "# hand edited\n"
        assert (store / "README.md").read_bytes() == (scaffolded_project / "README.md").read_bytes()
        assert not (scaffolded_project / f"{snapshot._store}.partial").exists()
    finally:
        snapshot.discard()
    assert not (scaffolded_project / ".ngargparser" / "sync-in-progress").exists()


# --- 6b. A kill -9 while the undo puts a file back -----------------------------------------------

KILLED_WHILE_PUTTING_BACK = """\
import os, shutil, signal, sys
from argparse import Namespace
from ngargparser import cli

real = shutil.copy2


def copy2(src, dst, *args, **kwargs):
    # kill -9 halfway through the first file the undo puts back.
    if "restore" in os.path.basename(str(dst)):
        with open(src, "rb") as f:
            data = f.read()
        with open(dst, "wb") as f:
            f.write(data[: len(data) // 2])
        os.kill(os.getpid(), signal.SIGKILL)
    return real(src, dst, *args, **kwargs)


def badge_that_fails(*args, **kwargs):
    raise RuntimeError("boom")  # an error after the framework files are written: the sync is undone


real_link = os.link


def link(src, dst, *args, **kwargs):
    # kill -9 once the undo gave the first file it puts back its temporary name (a hard link).
    real_link(src, dst, *args, **kwargs)
    if "restore" in os.path.basename(str(dst)):
        os.kill(os.getpid(), signal.SIGKILL)


shutil.copy2 = copy2
os.link = link
cli.upsert_readme_badge = badge_that_fails
"""


@pytest.mark.skipif(os.name != "posix", reason="needs kill -9")
def test_a_kill_during_the_undo_leaves_no_temporary_file_in_the_tool(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    make_stale(project)
    names_before = set(tree_state(project, skip=(".ngargparser",))[0])

    run = subprocess.run(
        [sys.executable, "-c", KILLED_WHILE_PUTTING_BACK + SYNC.format(verify=False)],
        cwd=project,
        env=subprocess_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == -signal.SIGKILL, run.stdout + run.stderr
    assert "Error during synchronization: boom" in run.stderr

    # No file the tool did not have before, beside the file the undo was putting back.
    assert set(tree_state(project, skip=(".ngargparser",))[0]) == names_before
    leftovers = list((project / ".ngargparser" / "sync-in-progress").glob("*.restore"))
    assert len(leftovers) == 1  # out of sight, under .ngargparser/

    # Finished by hand as the next sync says, then a sync after that deletes the leftover.
    assert cli.sync_command(sync_args()) == 1
    run_commands(project, strip_ansi(capsys.readouterr().err))
    assert cli.sync_command(sync_args(verify=False)) == 0
    capsys.readouterr()
    assert not list(project.rglob("*.restore")) and not list(project.rglob("*ngargparser-restore"))
    assert not (project / ".ngargparser" / "sync-in-progress").exists()


# --- 6c. A README.md that is a link -------------------------------------------------------------


def old_badge(path):
    path.write_text(re.sub(r"ngargparser-[^-/\s)]+-green", "ngargparser-0.0.1-red", path.read_text()))


@pytest.mark.skipif(os.name == "nt", reason="needs symlinks")
def test_a_readme_md_that_links_to_the_readme_gets_no_badge(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    readme = project / "README"  # the README end users get
    readme.write_text("# demo\n\n[![ngargparser](https://img.shields.io/badge/ngargparser-0.0.1-red.svg)](x)\n")
    (project / "README.md").unlink()
    (project / "README.md").symlink_to("README")
    make_stale(project)
    readme_before = readme.read_bytes()

    for args in (sync_args(dry_run=True, verify=False), sync_args(verify=False)):
        assert cli.sync_command(args) == 0
        out = strip_ansi(capsys.readouterr().out)
        assert (
            "Updating README version badges...\n  └ ⚠ Skipped the README.md badge: README.md is a link to README, "
            "and sync does not write the badge through a link.\n"
        ) in out
        assert "README badge:" not in out
        assert readme.read_bytes() == readme_before
        assert (project / "README.md").is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="needs symlinks")
def test_a_readme_md_that_links_out_of_the_tool_no_longer_stops_the_sync(
    scaffolded_project, sync_args, strip_ansi, capsys, tmp_path_factory
):
    # Its badge is skipped, so nothing is written through the link: no reason to stop.
    project = scaffolded_project
    outside = tmp_path_factory.mktemp("shared") / "README.md"
    outside.write_bytes((project / "README.md").read_bytes())
    old_badge(outside)
    (project / "README.md").unlink()
    (project / "README.md").symlink_to(outside)
    make_stale(project)
    outside_before = outside.read_bytes()

    assert cli.sync_command(sync_args(verify=False)) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "Sync stopped" not in out
    assert f"  └ ⚠ Skipped the README.md badge: README.md is a link to {outside}" in out
    assert outside.read_bytes() == outside_before
    assert "# a local edit" not in (project / CORE_FILE).read_text()  # the sync went ahead


def test_a_readme_md_that_is_a_file_still_gets_its_badge(scaffolded_project, sync_args, strip_ansi, capsys):
    # Control: a plain README.md with an old badge is updated, as before.
    old_badge(scaffolded_project / "README.md")

    assert cli.sync_command(sync_args(verify=False)) == 0

    assert "  └ Updated README.md badge\n" in strip_ansi(capsys.readouterr().out)
    assert f"ngargparser-{cli.__version__}-green" in (scaffolded_project / "README.md").read_text()
