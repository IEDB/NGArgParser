from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
FRAMEWORK_FILES = [
    "src/core/NGArgumentParser.py",
    "src/core/core_validators.py",
    "src/core/result_writer.py",
    "src/core/set_pythonpath.py",
    "src/core/configure.py",
    "configure",
    "scripts/core/build.sh",
    "Makefile",
]


def test_fresh_project_leaves_framework_files_untouched(scaffolded_project, sync_args):
    before = {f: (scaffolded_project / f).read_bytes() for f in FRAMEWORK_FILES}
    rc = cli.sync_command(sync_args())
    assert rc == 0
    for f, content in before.items():
        assert (scaffolded_project / f).read_bytes() == content, f"{f} was rewritten"
    # No framework file was overwritten, so no backup directory is created.
    assert not (scaffolded_project / ".ngargparser").exists()


def test_drifted_core_file_is_restored(scaffolded_project, sync_args):
    target = scaffolded_project / CORE_FILE
    pristine = target.read_text()
    target.write_text("# corrupted by user\n")

    rc = cli.sync_command(sync_args())
    assert rc == 0
    assert target.read_text() == pristine


def test_broken_launcher_is_repaired_by_sync(scaffolded_project, sync_args):
    # Simulate a project scaffolded before the configure-launcher fix: the
    # old broken content had no shebang and never forwarded arguments.
    launcher = scaffolded_project / "configure"
    launcher.write_text("./src/core/configure.py")

    rc = cli.sync_command(sync_args())
    assert rc == 0

    fixed = launcher.read_text()
    assert fixed.startswith("#!/bin/sh")
    assert '"$@"' in fixed


def test_stale_stamp_is_restamped(scaffolded_project, sync_args):
    pyproject = scaffolded_project / "pyproject.toml"
    cli.write_scaffold_version(str(pyproject), "0.0.1")

    cli.sync_command(sync_args())
    assert f'scaffold_version = "{cli.__version__}"' in pyproject.read_text()


def test_dry_run_reports_but_writes_nothing(scaffolded_project, sync_args, strip_ansi, capsys):
    target = scaffolded_project / CORE_FILE
    corrupted = "# corrupted by user\n"
    target.write_text(corrupted)

    rc = cli.sync_command(sync_args(dry_run=True))
    assert rc == 0
    # File is untouched and no backup directory was created.
    assert target.read_text() == corrupted
    assert not (scaffolded_project / ".ngargparser").exists()
    out = strip_ansi(capsys.readouterr().out)
    assert "DRY RUN" in out
    assert "Would update" in out


def test_backup_written_by_default_on_overwrite(scaffolded_project, sync_args):
    target = scaffolded_project / CORE_FILE
    pristine = target.read_text()
    corrupted = "# corrupted by user\n"
    target.write_text(corrupted)

    rc = cli.sync_command(sync_args())
    assert rc == 0
    assert target.read_text() == pristine  # restored

    backups = list((scaffolded_project / ".ngargparser" / "sync-backups").glob("*/" + CORE_FILE))
    assert len(backups) == 1
    assert backups[0].read_text() == corrupted  # the user's version is preserved


def test_no_backup_flag_skips_backup(scaffolded_project, sync_args):
    target = scaffolded_project / CORE_FILE
    target.write_text("# corrupted by user\n")

    rc = cli.sync_command(sync_args(backup=False))
    assert rc == 0
    assert not (scaffolded_project / ".ngargparser").exists()


def test_sync_creates_gitignore_for_older_project(scaffolded_project, sync_args):
    # Simulate a project generated before scaffolds shipped a .gitignore.
    (scaffolded_project / ".gitignore").unlink()
    cli.sync_command(sync_args())
    content = (scaffolded_project / ".gitignore").read_text()
    assert ".ngargparser/" in content and "*.bak" in content


def test_sync_appends_ignore_rules_preserving_user_entries(scaffolded_project, sync_args):
    gitignore = scaffolded_project / ".gitignore"
    gitignore.write_text("# my rules\nsecrets.txt\n")  # user's own, no managed marker
    cli.sync_command(sync_args())
    content = gitignore.read_text()
    assert "secrets.txt" in content  # preserved
    assert ".ngargparser/" in content  # appended


def test_sync_gitignore_idempotent_on_fresh_project(scaffolded_project, sync_args):
    gitignore = scaffolded_project / ".gitignore"
    before = gitignore.read_text()  # fresh scaffold already has the managed block
    cli.sync_command(sync_args())
    assert gitignore.read_text() == before
