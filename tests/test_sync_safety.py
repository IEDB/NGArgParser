"""Tests for the safety nets around `cli sync`.

Sync stops before writing when the project has code the framework is known to
break, test-drives the project's help screens before and after, and restores
every touched file when a screen that worked before fails after (or when sync
itself errors out). "Nothing changed" is always checked against a hash of every
file in the project, not just the files a test happens to name.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import sys

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
PARSER_FILE = "src/DemoArgumentParser.py"

# What scaffolds generated before 0.3.0 put in the project's own parser: the two
# predict options the framework base class has declared itself since then.
OLD_STYLE_OPTIONS = """
        self.parser_predict.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                help="prediction result output prefix.",
                                metavar="OUTPUT_PREFIX")
        self.parser_predict.add_argument("--output-format", "-f",
                                dest="output_format",
                                default="tsv",
                                help="prediction result output format.")
"""


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
    """Give sync something to do: a framework file whose content has drifted."""
    core = project / CORE_FILE
    pristine = core.read_text()
    core.write_text(pristine + "\n# a local edit that sync will overwrite\n")
    return pristine


def add_unlisted_dependency(project):
    """Make the project rely on a hand-added helper inside a framework-owned file.

    It runs fine as is. Sync overwrites the framework file, the helper is gone,
    and the project can no longer be imported: a break no known-clash entry covers.
    """
    core = project / CORE_FILE
    core.write_text(core.read_text() + '\n\ndef local_helper():\n    return "added by hand"\n')
    parser = project / PARSER_FILE
    parser.write_text("from core.NGArgumentParser import local_helper  # noqa: F401\n" + parser.read_text())


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    """A scaffolded project whose test drive really runs, on the test interpreter
    (a fresh scaffold has no `.venv` of its own)."""
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


# --- Known clashes ------------------------------------------------------------


def test_fresh_scaffold_has_no_known_clash(scaffolded_project):
    assert cli._find_known_clashes() == []


def test_clash_detector_reports_file_and_lines(scaffolded_project):
    parser = scaffolded_project / PARSER_FILE
    first_new_line = len(parser.read_text().splitlines()) + 2  # after the blank line OLD_STYLE_OPTIONS starts with
    parser.write_text(parser.read_text() + OLD_STYLE_OPTIONS)

    findings = cli._clash_predict_output_options()

    assert [(f["path"], f["what"]) for f in findings] == [
        (PARSER_FILE, "declares --output-prefix on predict"),
        (PARSER_FILE, "declares --output-format on predict"),
    ]
    assert (findings[0]["first"], findings[0]["last"]) == (first_new_line, first_new_line + 3)
    assert (findings[1]["first"], findings[1]["last"]) == (first_new_line + 4, first_new_line + 7)


@pytest.mark.parametrize("option", ["--output-prefix", "--output-format"])
def test_clash_detector_finds_each_option_alone(scaffolded_project, option):
    parser = scaffolded_project / PARSER_FILE
    parser.write_text(parser.read_text() + f'\n        self.parser_predict.add_argument("{option}", dest="x")\n')
    assert [f["what"] for f in cli._clash_predict_output_options()] == [f"declares {option} on predict"]


@pytest.mark.parametrize("letter", ["-o", "-f"])
def test_clash_detector_finds_a_reused_short_letter(runnable_project, letter):
    # A different option that borrows the base class's short letter conflicts just the same.
    parser = runnable_project / PARSER_FILE
    parser.write_text(
        parser.read_text() + f'\n        self.parser_predict.add_argument("--my-own", "{letter}", dest="mine")\n'
    )

    assert [f["what"] for f in cli._clash_predict_output_options()] == [f"declares {letter} on predict"]
    # The claim behind the entry, checked by running the project: it no longer starts.
    passed, error = cli._smoke_check()["results"]["src/run_demo.py --help"]
    owner = {"-o": "--output-prefix", "-f": "--output-format"}[letter]
    assert not passed and f"{letter} belongs to the framework ({owner})" in error


def test_clash_detector_ignores_mentions_that_are_not_declarations(scaffolded_project):
    # Control for a plain text search, which would flag all of these.
    parser = scaffolded_project / PARSER_FILE
    parser.write_text(
        parser.read_text()
        + "\n        # --output-prefix and --output-format come from the base class\n"
        + '        self.parser_predict.add_argument("--note", help="see --output-prefix / --output-format")\n'
        + '        self.parser_predict.update_arguments("--output-format", "-f", default="json")\n'
        + '        self.parser_preprocess.add_argument("--output-format-hint", dest="hint")\n'
    )
    assert cli._clash_predict_output_options() == []


def test_remove_then_redeclare_is_not_a_clash(runnable_project):
    # remove_argument() frees every name of the option (here -f too), so the project
    # may declare its own afterwards: the README's recipe for replacing an option.
    parser = runnable_project / PARSER_FILE
    parser.write_text(
        parser.read_text()
        + '\n        self.remove_argument("--output-format", "predict")\n'
        + '        self.parser_predict.add_argument("--output-format", "-f", dest="output_format", default="json")\n'
    )

    assert cli._clash_predict_output_options() == []
    # Checked by running the project, not assumed: it starts.
    passed, error = cli._smoke_check()["results"]["src/run_demo.py --help"]
    assert passed, error


def test_remove_on_the_subparser_then_redeclare_is_not_a_clash(runnable_project):
    # The same recipe through the subparser's own remove_argument(<option>).
    parser = runnable_project / PARSER_FILE
    parser.write_text(
        parser.read_text()
        + '\n        self.parser_predict.remove_argument("--output-format")\n'
        + '        self.parser_predict.add_argument("--output-format", "-f", dest="output_format", default="json")\n'
    )

    assert cli._clash_predict_output_options() == []
    passed, error = cli._smoke_check()["results"]["src/run_demo.py --help"]
    assert passed, error


def test_redeclare_before_the_removal_is_still_a_clash(runnable_project):
    # Control: the removal has to come first; declaring before it conflicts at once.
    parser = runnable_project / PARSER_FILE
    parser.write_text(
        parser.read_text()
        + '\n        self.parser_predict.add_argument("--output-format", "-f", dest="output_format", default="json")\n'
        + '        self.remove_argument("--output-format", "predict")\n'
    )

    assert [f["what"] for f in cli._clash_predict_output_options()] == ["declares --output-format on predict"]
    passed, error = cli._smoke_check()["results"]["src/run_demo.py --help"]
    assert not passed and "belong to the framework (--output-format)" in error


def test_clash_detector_skips_a_file_it_cannot_parse_and_says_which(scaffolded_project):
    (scaffolded_project / PARSER_FILE).write_text("def broken(:\n")
    skipped = []
    assert cli._clash_predict_output_options(skipped) == []
    assert skipped == [PARSER_FILE]


def test_known_clash_stops_sync_and_writes_nothing(runnable_project, sync_args, strip_ansi, capsys):
    parser = runnable_project / PARSER_FILE
    parser.write_text(parser.read_text() + OLD_STYLE_OPTIONS)
    make_stale(runnable_project)
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert "Sync stopped" in out
    assert f"{PARSER_FILE}:" in out and "declares --output-prefix on predict" in out
    assert "Nothing was changed" in out and "--no-verify" in out
    assert tree_state(runnable_project) == before
    assert not (runnable_project / ".ngargparser" / "sync-backups").exists()


def test_known_clash_is_reported_in_dry_run(runnable_project, sync_args, strip_ansi, capsys):
    parser = runnable_project / PARSER_FILE
    parser.write_text(parser.read_text() + OLD_STYLE_OPTIONS)
    make_stale(runnable_project)
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args(dry_run=True))

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 0  # a real run decides by running the help screens, which a dry run cannot
    assert "Sync may stop" in out and "declares --output-prefix on predict" in out
    # The preview goes on past the clash. A real run can run the help screens, so it
    # decides by running them; the dry run must not say it stops.
    assert "A real run first runs the tool's help screens: if one fails, it stops here" in out
    assert "A real run stops here and changes nothing" not in out
    assert f"Would update {CORE_FILE}" in out
    assert (
        "DRY RUN: a dry run runs no tool code, so it cannot tell whether the known clash reported above is real. "
        "A real run first runs the help screens: if every one passes, it goes ahead; if one fails, it stops at the "
        "clash and changes nothing."
    ) in out
    assert "Fix it (or sync with --no-verify)" not in out
    assert tree_state(runnable_project) == before


def test_known_clash_in_dry_run_of_a_tool_whose_help_screens_cannot_run_says_the_real_run_stops(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    # Control: without a .venv a real run cannot run the help screens, so the finding stands,
    # whatever the stamp says.
    parser = scaffolded_project / PARSER_FILE
    parser.write_text(parser.read_text() + OLD_STYLE_OPTIONS)
    make_stale(scaffolded_project)
    cli.write_scaffold_version(str(scaffolded_project / "pyproject.toml"), "0.2.4")

    rc = cli.sync_command(sync_args(dry_run=True))

    captured = capsys.readouterr()
    out = strip_ansi(captured.err)
    assert rc == 1
    assert "Sync would stop" in out and "A real run stops here and changes nothing." in out
    assert (
        "DRY RUN: a real run stops at the known clash reported above and changes nothing. "
        "Fix it (or sync with --no-verify) to make the changes listed here."
    ) in out
    assert "a real run first runs the help screens" not in out + captured.out


# --- Test drive and rollback ----------------------------------------------------


def test_healthy_project_is_synced_and_verified(runnable_project, sync_args, strip_ansi, capsys):
    pristine = make_stale(runnable_project)

    rc = cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 0
    assert (runnable_project / CORE_FILE).read_text() == pristine
    assert "Verified: every help screen that worked before the sync still works (4 of 4)" in out


def test_unlisted_break_is_rolled_back(runnable_project, sync_args, strip_ansi, capsys):
    add_unlisted_dependency(runnable_project)
    assert cli._find_known_clashes() == []  # nothing on the list covers this
    assert all(passed for passed, _ in cli._smoke_check()["results"].values())  # and it runs today
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args())

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert "Sync rolled back" in out
    assert "src/run_demo.py --help" in out and "local_helper" in out  # the command and its error
    assert "Every file was restored" in out
    assert tree_state(runnable_project) == before


def test_rollback_restores_a_legacy_layout(runnable_project, sync_args, capsys):
    # Rebuild the pre-scripts/core layout sync migrates, plus state sync creates or edits.
    project = runnable_project
    (project / "scripts/core/build.sh").rename(project / "scripts/build.sh")
    (project / "scripts/core/build.conf").rename(project / "scripts/build.conf")
    (project / "scripts/core").rmdir()
    (project / "scripts/dependencies.sh").write_text("#!/bin/bash\n")
    (project / ".distignore").rename(project / "scripts/do-not-distribute.txt")
    shutil.rmtree(project / ".ngargparser", ignore_errors=True)  # an older tool has no framework hash record
    (project / "deploy/install.sh").unlink()
    (project / "deploy").rmdir()
    (project / ".gitignore").unlink()
    cli.write_scaffold_version(str(project / "pyproject.toml"), "0.0.1")
    add_unlisted_dependency(project)
    before = tree_state(project)

    rc = cli.sync_command(sync_args())

    assert rc == 1
    assert "Sync rolled back" in capsys.readouterr().err
    files, dirs = tree_state(project)
    assert (files, dirs) == before
    assert "scripts/build.sh" in files and "scripts/dependencies.sh" in files
    assert "scripts/build.conf" in files
    assert "scripts/do-not-distribute.txt" in files and ".distignore" not in files
    assert not {"scripts/core", "deploy", ".ngargparser"} & dirs
    assert ".gitignore" not in files and "scripts/hooks.sh" not in files


def test_project_broken_before_sync_is_synced_with_a_warning(runnable_project, sync_args, strip_ansi, capsys):
    pristine = make_stale(runnable_project)
    (runnable_project / PARSER_FILE).write_text("def broken(:\n")  # does not start, before or after

    rc = cli.sync_command(sync_args())

    out, err = (strip_ansi(text) for text in capsys.readouterr())
    assert rc == 0
    assert (runnable_project / CORE_FILE).read_text() == pristine  # the sync itself went ahead
    assert "Could not verify that the tool still runs: it did not start before the sync either" in err
    assert "Verified" not in out
    assert f"Could not check {PARSER_FILE} for known clashes" in out  # the skipped file is named


NO_VENV_STOP = (
    "✗ Sync needs the tool's .venv to check that the tool still starts after the update. Run 'uv sync', "
    "then cli sync again (or cli sync --no-verify to update without the check). Nothing was changed."
)


def test_project_without_interpreter_stops_before_writing(scaffolded_project, sync_args, strip_ansi, capsys):
    # A fresh clone: no .venv, and _project_python is not patched. The sync used to go ahead
    # with "Could not verify" and exit 0, so nothing showed whether the update broke the tool.
    # Control: with nothing to change, no test drive is needed, so there is no stop.
    assert cli.sync_command(sync_args()) == 0
    assert "Sync needs the tool's .venv" not in strip_ansi(capsys.readouterr().err)
    pristine = make_stale(scaffolded_project)
    before = tree_state(scaffolded_project)

    rc = cli.sync_command(sync_args())

    captured = capsys.readouterr()
    out = strip_ansi(captured.err)
    assert rc == 1
    assert NO_VENV_STOP in out
    assert tree_state(scaffolded_project) == before
    assert "Updating framework files" not in captured.out and "Could not verify" not in captured.out

    # --no-verify asks for no test drive, so it updates without one.
    assert cli.sync_command(sync_args(verify=False)) == 0
    assert "Sync needs the tool's .venv" not in strip_ansi(capsys.readouterr().err)
    assert (scaffolded_project / CORE_FILE).read_text() == pristine


def test_project_without_interpreter_dry_run_reports_the_stop(scaffolded_project, sync_args, strip_ansi, capsys):
    make_stale(scaffolded_project)
    before = tree_state(scaffolded_project)

    rc = cli.sync_command(sync_args(dry_run=True))

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert "✗ A real run would stop here and change nothing. Sync needs the tool's .venv to check" in out
    assert f"Would update {CORE_FILE}" in out  # the preview still lists what a real run changes
    assert "DRY RUN: a real run stops before the update" in out
    assert tree_state(scaffolded_project) == before


def test_error_midway_is_rolled_back(runnable_project, sync_args, strip_ansi, capsys, monkeypatch):
    cli.write_scaffold_version(str(runnable_project / "pyproject.toml"), "0.0.1")
    make_stale(runnable_project)
    before = tree_state(runnable_project)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cli, "write_scaffold_version", boom)  # fails after the framework files were written

    rc = cli.sync_command(sync_args())

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert "Error during synchronization: disk full" in out
    assert "Every file was restored" in out
    assert tree_state(runnable_project) == before


def test_a_pending_exclusion_file_move_is_test_driven(scaffolded_project):
    # Like the other legacy moves, moving an old exclusion file to .distignore counts as a change.
    framework_files = cli._sync_framework_files()
    assert not cli._framework_changes_pending(framework_files)  # control: a fresh scaffold
    (scaffolded_project / ".distignore").rename(scaffolded_project / "scripts/do-not-distribute.txt")
    assert cli._framework_changes_pending(framework_files)


def test_nothing_to_change_skips_the_test_drive(runnable_project, sync_args, monkeypatch):
    monkeypatch.setattr(cli, "_smoke_check", lambda: pytest.fail("no framework file changes, nothing to verify"))
    assert cli.sync_command(sync_args()) == 0


def test_dry_run_does_not_test_drive_or_write(runnable_project, sync_args, monkeypatch):
    make_stale(runnable_project)
    before = tree_state(runnable_project)
    monkeypatch.setattr(cli, "_smoke_check", lambda: pytest.fail("dry-run must not run the project"))

    assert cli.sync_command(sync_args(dry_run=True)) == 0
    assert tree_state(runnable_project) == before


# --- Opting out -----------------------------------------------------------------


def _clashing_stale_project(project):
    parser = project / PARSER_FILE
    parser.write_text(parser.read_text() + OLD_STYLE_OPTIONS)
    return make_stale(project)


def test_no_verify_skips_both_checks(runnable_project, sync_args, strip_ansi, capsys, monkeypatch):
    pristine = _clashing_stale_project(runnable_project)
    monkeypatch.setattr(cli, "_smoke_check", lambda: pytest.fail("--no-verify must not run the project"))

    rc = cli.sync_command(sync_args(verify=False))

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 0
    assert (runnable_project / CORE_FILE).read_text() == pristine
    assert "Sync stopped" not in out and "Verified" not in out and "Could not verify" not in out
    assert "Safety checks are off for this run" in out  # never skipped silently


def test_no_verify_environment_variable_skips_both_checks(runnable_project, sync_args, strip_ansi, capsys, monkeypatch):
    pristine = _clashing_stale_project(runnable_project)
    monkeypatch.setenv("NGARGPARSER_SYNC_NO_VERIFY", "1")
    monkeypatch.setattr(cli, "_smoke_check", lambda: pytest.fail("must not run the project"))

    assert cli.sync_command(sync_args()) == 0
    assert (runnable_project / CORE_FILE).read_text() == pristine
    assert "Safety checks are off for this run" in strip_ansi(capsys.readouterr().out)


@pytest.mark.parametrize("verify, carried", [(False, "1"), (True, None)])
def test_self_upgrade_re_exec_carries_no_verify(scaffolded_project, sync_args, monkeypatch, verify, carried):
    # The re-exec passes a bare `cli s`, which drops flags, so --no-verify travels in the environment.
    seen = {}

    def fake_exec(file, argv, env):
        seen["argv"], seen["env"] = argv, env
        raise SystemExit(0)

    monkeypatch.delenv("NGARGPARSER_NO_SELF_UPGRADE", raising=False)
    monkeypatch.delenv("NGARGPARSER_SYNC_NO_VERIFY", raising=False)
    monkeypatch.setattr(cli, "_local_checkout", lambda: None)  # a released install; a checkout skips the upgrade
    monkeypatch.setattr(
        cli, "_resolve_upgrade_url", lambda *a, **k: ("https://example.invalid/repo.git@v9.9.9", "v9.9.9")
    )
    monkeypatch.setattr(cli, "_run_self_upgrade", lambda url: 0)
    monkeypatch.setattr(cli.os, "execvpe", fake_exec)

    with pytest.raises(SystemExit):
        cli.sync_command(sync_args(upgrade=True, verify=verify))

    assert seen["argv"][1:] == ["s"]
    assert seen["env"].get("NGARGPARSER_SYNC_NO_VERIFY") == carried
    assert seen["env"]["NGARGPARSER_NO_SELF_UPGRADE"] == "1"


@pytest.mark.parametrize("backup, carried", [(False, "1"), (True, None)])
def test_self_upgrade_re_exec_carries_no_backup(scaffolded_project, sync_args, monkeypatch, backup, carried):
    # The re-exec passes a bare `cli s`, which drops flags, so --no-backup travels in the environment.
    seen = {}

    def fake_exec(file, argv, env):
        seen["env"] = env
        raise SystemExit(0)

    monkeypatch.delenv("NGARGPARSER_NO_SELF_UPGRADE", raising=False)
    monkeypatch.delenv("NGARGPARSER_SYNC_NO_BACKUP", raising=False)
    monkeypatch.setattr(cli, "_local_checkout", lambda: None)  # a released install
    monkeypatch.setattr(
        cli, "_resolve_upgrade_url", lambda *a, **k: ("https://example.invalid/repo.git@v9.9.9", "v9.9.9")
    )
    monkeypatch.setattr(cli, "_run_self_upgrade", lambda url: 0)
    monkeypatch.setattr(cli.os, "execvpe", fake_exec)

    with pytest.raises(SystemExit):
        cli.sync_command(sync_args(upgrade=True, backup=backup))

    assert seen["env"].get("NGARGPARSER_SYNC_NO_BACKUP") == carried


def test_no_backup_from_the_environment_writes_no_backups(runnable_project, sync_args, monkeypatch):
    # What the re-exec'd sync sees: no --no-backup flag, only the variable.
    monkeypatch.setenv("NGARGPARSER_SYNC_NO_BACKUP", "1")
    core = runnable_project / "src" / "core" / "result_writer.py"
    core.write_text(core.read_text() + "# stale\n")

    assert cli.sync_command(sync_args()) == 0

    assert "# stale" not in core.read_text()  # control: the file was overwritten
    assert not (runnable_project / ".ngargparser" / "sync-backups").exists()


def test_no_verify_still_rolls_back_an_error(runnable_project, sync_args, monkeypatch):
    cli.write_scaffold_version(str(runnable_project / "pyproject.toml"), "0.0.1")
    make_stale(runnable_project)
    before = tree_state(runnable_project)
    monkeypatch.setattr(cli, "write_scaffold_version", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    assert cli.sync_command(sync_args(verify=False)) == 1
    assert tree_state(runnable_project) == before


def test_failed_restore_keeps_the_pre_image_and_says_where(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch
):
    add_unlisted_dependency(runnable_project)
    core_before = (runnable_project / CORE_FILE).read_bytes()

    def cannot_restore(self, backup_root=None):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(cli._SyncSnapshot, "restore", cannot_restore)

    rc = cli.sync_command(sync_args())

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert "Restore did not finish (read-only file system). The files before the sync are in " in out
    kept = out.split("The files before the sync are in ", 1)[1].split("; ", 1)[0]
    # The pre-image keeps project-relative paths, so it can be copied back by hand.
    assert open(os.path.join(kept, CORE_FILE), "rb").read() == core_before
    assert os.path.isfile(os.path.join(kept, "pyproject.toml"))
    shutil.rmtree(kept)


# The pre-image is kept in the tool's .ngargparser/ (a reboot can clear the system temp
# folder); a failed restore renames it so it stands out, and its marker follows it.


def test_a_failed_restore_keeps_the_pre_image_in_the_tool_folder(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch
):
    add_unlisted_dependency(runnable_project)
    core_before = (runnable_project / CORE_FILE).read_bytes()

    def cannot_restore(self, backup_root=None):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(cli._SyncSnapshot, "restore", cannot_restore)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    kept = out.split("The files before the sync are in ", 1)[1].split("; ", 1)[0]
    assert re.fullmatch(r"\.ngargparser/failed-restore-\d{8}T\d{6}Z", kept), kept
    assert (runnable_project / kept / CORE_FILE).read_bytes() == core_before
    assert (runnable_project / kept / "pyproject.toml").is_file()
    in_progress = runnable_project / ".ngargparser" / "sync-in-progress"
    [marker] = in_progress.glob("*.json")
    assert json.loads(marker.read_text())["files"] == kept
    assert [p for p in in_progress.iterdir() if p.is_dir()] == []  # moved, not copied

    # When it cannot be renamed, it stays whole where it is, and its marker still names it.
    marker.unlink()
    monkeypatch.setattr(cli.os, "rename", lambda *a: (_ for _ in ()).throw(OSError(18, "Invalid cross-device link")))
    snapshot = cli._SyncSnapshot([CORE_FILE, "pyproject.toml"])

    kept_here = snapshot.keep()

    assert kept_here.startswith(".ngargparser/sync-in-progress/"), kept_here
    assert open(os.path.join(kept_here, CORE_FILE), "rb").read() == (runnable_project / CORE_FILE).read_bytes()
    [marker] = in_progress.glob("*.json")
    assert json.loads(marker.read_text())["files"] == kept_here
    shutil.rmtree(runnable_project / kept)


def test_snapshot_that_cannot_be_taken_leaves_no_pre_image_or_marker(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(PermissionError("unreadable")))

    with pytest.raises(PermissionError):
        cli._SyncSnapshot([CORE_FILE])
    assert not (scaffolded_project / ".ngargparser" / "sync-in-progress").exists()


# --- Links, interruption, several entry scripts ---------------------------------------


def test_rollback_restores_content_written_through_a_symlink(runnable_project, sync_args, capsys):
    # A framework file that is a link to a shared copy inside the tool: sync writes through
    # the link, so undoing the sync has to put the shared copy's content back, not just the link.
    # (A link that leads outside the tool is refused before anything is written.)
    shared = runnable_project / "shared" / "core_validators.py"
    shared.parent.mkdir()
    linked = runnable_project / "src/core/core_validators.py"
    shared.write_text(linked.read_text() + "\n# hand-edited shared copy\n")
    linked.unlink()
    linked.symlink_to("../../shared/core_validators.py")
    shared_before = shared.read_bytes()
    add_unlisted_dependency(runnable_project)
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args())

    assert rc == 1 and "Sync rolled back" in capsys.readouterr().err
    assert linked.is_symlink() and os.readlink(linked) == "../../shared/core_validators.py"
    assert shared.read_bytes() == shared_before
    assert tree_state(runnable_project) == before
    assert list(shared.parent.iterdir()) == [shared]  # no leftover temporary file next to it


def test_rollback_removes_the_target_a_dangling_symlink_gained(runnable_project, sync_args, capsys):
    target = runnable_project / "elsewhere" / "result_writer.py"  # inside the tool; does not exist
    target.parent.mkdir()
    linked = runnable_project / "src/core/result_writer.py"
    linked.unlink()
    linked.symlink_to(target)
    add_unlisted_dependency(runnable_project)

    rc = cli.sync_command(sync_args())

    assert rc == 1 and "Sync rolled back" in capsys.readouterr().err
    assert linked.is_symlink() and not target.exists()  # the file sync created through the link is gone


def test_interrupt_during_the_test_drive_is_rolled_back(runnable_project, sync_args, strip_ansi, capsys, monkeypatch):
    make_stale(runnable_project)
    before = tree_state(runnable_project)
    real_check, calls = cli._smoke_check, []

    def interrupted_second_time():
        calls.append(1)
        if len(calls) == 2:  # the "after" run, once the new files are in place
            raise KeyboardInterrupt
        return real_check()

    monkeypatch.setattr(cli, "_smoke_check", interrupted_second_time)

    try:
        rc = cli.sync_command(sync_args())  # Ctrl-C exits 130 (128 + SIGINT), with no traceback
    except KeyboardInterrupt:
        pytest.fail("the interrupt escaped cli sync (a traceback)")

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 130
    assert "Sync interrupted" in out and "Every file was restored" in out
    assert "Cancelled; nothing changed." in out
    assert tree_state(runnable_project) == before


def test_several_entry_scripts_are_not_run(runnable_project, sync_args, strip_ansi, capsys):
    # Without [tool.ngargparser] entry, sync can't tell which one is the entry script, and running
    # the other with --help would execute whatever it does. Here it would leave a marker file behind.
    # It used to sync anyway, unchecked, and exit 0; now it stops before writing (test_sync_entry_choice.py).
    pyproject = runnable_project / "pyproject.toml"
    pyproject.write_text(
        "".join(line for line in pyproject.read_text().splitlines(True) if not line.startswith("entry"))
    )
    pristine = make_stale(runnable_project)
    marker = runnable_project / "side-effect-ran"
    (runnable_project / "src/run_cleanup.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args())

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert "✗ Sync cannot check that the tool still starts after the update: several src/run_*.py scripts" in out
    assert tree_state(runnable_project) == before
    assert not marker.exists()

    # --no-verify asks for no test drive, so it updates without one, and runs neither script.
    assert cli.sync_command(sync_args(verify=False)) == 0
    assert (runnable_project / CORE_FILE).read_text() == pristine
    assert not marker.exists()
