"""Tests for the safety nets around `cli sync`.

Sync stops before writing when the project has code the framework is known to
break, test-drives the project's help screens before and after, and restores
every touched file when a screen that worked before fails after (or when sync
itself errors out). "Nothing changed" is always checked against a hash of every
file in the project, not just the files a test happens to name.
"""

import hashlib
import os
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
    assert not passed and "conflicting option string" in error


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


def test_remove_then_redeclare_is_still_a_clash(runnable_project):
    # remove_argument() leaves the option strings registered with argparse, so declaring the
    # option again conflicts exactly as if it had never been removed.
    parser = runnable_project / PARSER_FILE
    parser.write_text(
        parser.read_text()
        + '\n        self.remove_argument("--output-format", "predict")\n'
        + '        self.parser_predict.add_argument("--output-format", "-f", dest="output_format", default="json")\n'
    )

    assert [f["what"] for f in cli._clash_predict_output_options()] == ["declares --output-format on predict"]
    # Checked by running the project, not assumed: it no longer starts.
    passed, error = cli._smoke_check()["results"]["src/run_demo.py --help"]
    assert not passed and "conflicting option strings" in error


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

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 1
    assert "Sync stopped" in out
    assert f"{PARSER_FILE}:" in out and "declares --output-prefix on predict" in out
    assert "Nothing was changed" in out and "--no-verify" in out
    assert tree_state(runnable_project) == before
    assert not (runnable_project / ".ngargparser").exists()


def test_known_clash_is_reported_in_dry_run(runnable_project, sync_args, strip_ansi, capsys):
    parser = runnable_project / PARSER_FILE
    parser.write_text(parser.read_text() + OLD_STYLE_OPTIONS)
    make_stale(runnable_project)
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args(dry_run=True))

    assert rc == 1
    assert "Sync stopped" in strip_ansi(capsys.readouterr().out)
    assert tree_state(runnable_project) == before


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

    out = strip_ansi(capsys.readouterr().out)
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
    (project / "deploy/install.sh").unlink()
    (project / "deploy").rmdir()
    (project / ".gitignore").unlink()
    cli.write_scaffold_version(str(project / "pyproject.toml"), "0.0.1")
    add_unlisted_dependency(project)
    before = tree_state(project)

    rc = cli.sync_command(sync_args())

    assert rc == 1
    assert "Sync rolled back" in capsys.readouterr().out
    files, dirs = tree_state(project)
    assert (files, dirs) == before
    assert "scripts/build.sh" in files and "scripts/dependencies.sh" in files
    assert "scripts/build.conf" in files
    assert not {"scripts/core", "deploy", ".ngargparser"} & dirs
    assert ".gitignore" not in files and "scripts/hooks.sh" not in files


def test_project_broken_before_sync_is_synced_with_a_warning(runnable_project, sync_args, strip_ansi, capsys):
    pristine = make_stale(runnable_project)
    (runnable_project / PARSER_FILE).write_text("def broken(:\n")  # does not start, before or after

    rc = cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 0
    assert (runnable_project / CORE_FILE).read_text() == pristine  # the sync itself went ahead
    assert "Could not verify that the project still runs: it did not start before the sync either" in out
    assert "Verified" not in out
    assert f"Could not check {PARSER_FILE} for known clashes" in out  # the skipped file is named


def test_project_without_interpreter_is_synced_with_a_warning(scaffolded_project, sync_args, strip_ansi, capsys):
    pristine = make_stale(scaffolded_project)  # no .venv, and _project_python is not patched

    rc = cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 0
    assert (scaffolded_project / CORE_FILE).read_text() == pristine
    assert "Could not verify that the project still runs: the project has no .venv interpreter" in out


def test_error_midway_is_rolled_back(runnable_project, sync_args, strip_ansi, capsys, monkeypatch):
    cli.write_scaffold_version(str(runnable_project / "pyproject.toml"), "0.0.1")
    make_stale(runnable_project)
    before = tree_state(runnable_project)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cli, "write_scaffold_version", boom)  # fails after the framework files were written

    rc = cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 1
    assert "Error during synchronization: disk full" in out
    assert "Every file was restored" in out
    assert tree_state(runnable_project) == before


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

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 1
    assert "Could not restore every file (read-only file system)" in out
    kept = out.split("are in: ", 1)[1].splitlines()[0].strip()
    # The pre-image keeps project-relative paths, so it can be copied back by hand.
    assert open(os.path.join(kept, CORE_FILE), "rb").read() == core_before
    assert os.path.isfile(os.path.join(kept, "pyproject.toml"))
    shutil.rmtree(kept)


def test_snapshot_that_cannot_be_taken_leaves_no_temp_dir(scaffolded_project, tmp_path_factory, monkeypatch):
    store = tmp_path_factory.mktemp("pre-image")
    monkeypatch.setattr("tempfile.mkdtemp", lambda **kwargs: str(store))
    monkeypatch.setattr(cli.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(PermissionError("unreadable")))

    with pytest.raises(PermissionError):
        cli._SyncSnapshot([CORE_FILE])
    assert not store.exists()


# --- Links, interruption, several entry scripts ---------------------------------------


def test_rollback_restores_content_written_through_a_symlink(runnable_project, sync_args, tmp_path_factory, capsys):
    # A framework file that is a link to a shared copy outside the project: sync writes through
    # the link, so undoing the sync has to put the shared copy's content back, not just the link.
    shared = tmp_path_factory.mktemp("shared") / "core_validators.py"
    linked = runnable_project / "src/core/core_validators.py"
    shared.write_text(linked.read_text() + "\n# hand-edited shared copy\n")
    linked.unlink()
    linked.symlink_to(shared)
    shared_before = shared.read_bytes()
    add_unlisted_dependency(runnable_project)
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args())

    assert rc == 1 and "Sync rolled back" in capsys.readouterr().out
    assert linked.is_symlink() and os.readlink(linked) == str(shared)
    assert shared.read_bytes() == shared_before
    assert tree_state(runnable_project) == before
    assert list(shared.parent.iterdir()) == [shared]  # no leftover temporary file next to it


def test_rollback_removes_the_target_a_dangling_symlink_gained(runnable_project, sync_args, tmp_path_factory, capsys):
    target = tmp_path_factory.mktemp("elsewhere") / "result_writer.py"  # does not exist
    linked = runnable_project / "src/core/result_writer.py"
    linked.unlink()
    linked.symlink_to(target)
    add_unlisted_dependency(runnable_project)

    rc = cli.sync_command(sync_args())

    assert rc == 1 and "Sync rolled back" in capsys.readouterr().out
    assert linked.is_symlink() and not target.exists()  # the file sync created outside the project is gone


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

    with pytest.raises(KeyboardInterrupt):
        cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().out)
    assert "Sync interrupted" in out and "Every file was restored" in out
    assert tree_state(runnable_project) == before


def test_several_entry_scripts_are_not_run(runnable_project, sync_args, strip_ansi, capsys):
    # Sync can't tell which one is the entry script, and running the other with --help would
    # execute whatever it does. Here it would leave a marker file behind.
    pristine = make_stale(runnable_project)
    marker = runnable_project / "side-effect-ran"
    (runnable_project / "src/run_cleanup.py").write_text(f"open({str(marker)!r}, 'w').close()\n")

    rc = cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 0
    assert (runnable_project / CORE_FILE).read_text() == pristine
    assert "Could not verify that the project still runs: several src/run_*.py scripts were found" in out
    assert not marker.exists()
