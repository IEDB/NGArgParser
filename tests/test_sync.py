import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
FRAMEWORK_FILES = [
    "src/core/NGArgumentParser.py",
    "src/core/core_validators.py",
    "src/core/result_writer.py",
    "src/core/app_env.py",
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


def test_legacy_build_conf_is_moved_into_core(scaffolded_project, sync_args):
    core_conf = scaffolded_project / "scripts/core/build.conf"
    legacy = scaffolded_project / "scripts/build.conf"
    content = 'TOOL_NAME="ng_custom"\nTARBALL_PREFIX="LJI_"\n'
    core_conf.unlink()
    legacy.write_text(content)

    rc = cli.sync_command(sync_args())

    assert rc == 0
    assert not legacy.exists()
    assert core_conf.read_text() == content


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


def test_sync_adds_configure_rules_to_an_older_managed_block(scaffolded_project, sync_args):
    gitignore = scaffolded_project / ".gitignore"
    old_block = cli.GITIGNORE_MANAGED_BLOCK.split("# Written by ./configure")[0]
    gitignore.write_text("secrets.txt\n\n" + old_block)

    cli.sync_command(sync_args())
    after = gitignore.read_text()
    assert after.startswith("secrets.txt\n\n" + old_block)
    assert ".env\n" in after and "setup_*_env.sh\n" in after

    cli.sync_command(sync_args())
    assert gitignore.read_text() == after


def test_sync_gitignore_idempotent_on_fresh_project(scaffolded_project, sync_args):
    gitignore = scaffolded_project / ".gitignore"
    before = gitignore.read_text()  # fresh scaffold already has the managed block
    cli.sync_command(sync_args())
    assert gitignore.read_text() == before


def test_sync_does_not_create_get_libs_sh(scaffolded_project, sync_args):
    # scripts/get_libs.sh is user-owned and new projects get it from `cli generate`;
    # existing projects add it themselves when they migrate.
    (scaffolded_project / "scripts" / "get_libs.sh").unlink()
    cli.sync_command(sync_args())
    assert not (scaffolded_project / "scripts" / "get_libs.sh").exists()


# --dry-run skips the self-upgrade; these check it says what a real run installs first.


@pytest.fixture
def no_install(monkeypatch):
    monkeypatch.delenv("NGARGPARSER_UPGRADE_URL", raising=False)
    monkeypatch.delenv("NGARGPARSER_NO_SELF_UPGRADE", raising=False)
    monkeypatch.setattr(cli, "_run_self_upgrade", lambda *a, **k: pytest.fail("dry-run must not install"))
    monkeypatch.setattr(cli, "_local_checkout", lambda: None)


def test_dry_run_names_the_version_a_real_run_installs(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, no_install
):
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: "v9.9.9")

    rc = cli.sync_command(sync_args(dry_run=True, upgrade=True))

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 0
    assert f"A real run first installs ngargparser v9.9.9 (installed: {cli.__version__})" in out
    assert "local checkout" not in out


def test_dry_run_with_no_upgrade_does_not_look_up_a_version(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, no_install
):
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: pytest.fail("must not contact the remote"))

    rc = cli.sync_command(sync_args(dry_run=True, upgrade=False))

    assert rc == 0
    assert "A real run first installs" not in strip_ansi(capsys.readouterr().out)


def test_dry_run_from_a_local_checkout_says_a_real_run_keeps_it(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, no_install
):
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: pytest.fail("must not contact the remote"))
    monkeypatch.setattr(cli, "_local_checkout", lambda: "/src/ngargparser")

    cli.sync_command(sync_args(dry_run=True, upgrade=True))

    out = strip_ansi(capsys.readouterr().out)
    assert "A real run syncs from your local checkout at /src/ngargparser (no upgrade)" in out
    assert "a real run replaces it" not in out


def test_dry_run_warns_before_a_named_version_replaces_a_local_checkout(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, no_install
):
    monkeypatch.setattr(cli, "_local_checkout", lambda: "/src/ngargparser")

    cli.sync_command(sync_args(dry_run=True, upgrade=True, ref="v9.9.9"))

    out = strip_ansi(capsys.readouterr().out)
    assert "The installed ngargparser is your local checkout at /src/ngargparser" in out
    assert "a real run replaces it with v9.9.9. To sync from the checkout, use --no-upgrade." in out


def test_dry_run_with_a_local_ref_names_that_checkout(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, no_install, tmp_path
):
    checkout = tmp_path / "ngargparser-src"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text('[project]\nname = "ngargparser"\n')
    # Syncing from the same checkout that is installed is not a replacement: no warning.
    monkeypatch.setattr(cli, "_local_checkout", lambda: str(checkout))

    cli.sync_command(sync_args(dry_run=True, upgrade=True, ref=str(checkout)))

    out = strip_ansi(capsys.readouterr().out)
    assert f"A real run first installs ngargparser from the local checkout {checkout}" in out
    assert "a real run replaces it" not in out


def test_dry_run_reports_an_unreachable_remote(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, no_install
):
    def boom(*a, **k):
        raise cli._RemoteUnavailable("network down")

    monkeypatch.setattr(cli, "_latest_release_tag", boom)

    rc = cli.sync_command(sync_args(dry_run=True, upgrade=True))

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 0
    assert "Couldn't check which ngargparser a real run would install (GitLab unreachable)." in out
    assert "DRY RUN" in out


# A real sync from a local checkout syncs from it; the latest release would
# replace the working copy and copy older framework files into the project.


@pytest.fixture
def upgrade_spy(monkeypatch):
    monkeypatch.delenv("NGARGPARSER_UPGRADE_URL", raising=False)
    monkeypatch.delenv("NGARGPARSER_NO_SELF_UPGRADE", raising=False)
    seen = {"installed": [], "re_exec": False}

    def fake_install(url):
        seen["installed"].append(url)
        return 0

    def fake_exec(*a, **k):
        seen["re_exec"] = True
        raise SystemExit(0)

    monkeypatch.setattr(cli, "_run_self_upgrade", fake_install)
    monkeypatch.setattr(cli.os, "execvpe", fake_exec)
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: "v9.9.9")
    return seen


def test_real_sync_from_a_local_checkout_does_not_upgrade(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, upgrade_spy
):
    monkeypatch.setattr(cli, "_local_checkout", lambda: "/src/ngargparser")

    rc = cli.sync_command(sync_args(upgrade=True))

    out = strip_ansi(capsys.readouterr().out)
    assert rc == 0
    assert upgrade_spy == {"installed": [], "re_exec": False}
    assert "Using your local checkout at /src/ngargparser (no upgrade)." in out
    assert "Synchronizing framework files" in out


def test_real_sync_from_a_released_install_still_upgrades(scaffolded_project, sync_args, monkeypatch, upgrade_spy):
    # Control: an install in site-packages is not a checkout.
    monkeypatch.setattr(cli, "_local_checkout", lambda: None)

    with pytest.raises(SystemExit):
        cli.sync_command(sync_args(upgrade=True))

    assert upgrade_spy["installed"] == [f"{cli.BASE_REPO_URL}@v9.9.9"]
    assert upgrade_spy["re_exec"]


@pytest.mark.parametrize("named, expected", [({"ref": "v1.2.3"}, "@v1.2.3"), ({"dev": True}, "@master")])
def test_real_sync_from_a_local_checkout_still_installs_a_named_version(
    scaffolded_project, sync_args, monkeypatch, upgrade_spy, named, expected
):
    monkeypatch.setattr(cli, "_local_checkout", lambda: "/src/ngargparser")

    with pytest.raises(SystemExit):
        cli.sync_command(sync_args(upgrade=True, **named))

    assert upgrade_spy["installed"] == [f"{cli.BASE_REPO_URL}{expected}"]
    assert upgrade_spy["re_exec"]


# A legacy file sync cannot move (its new name already exists) is never run, or
# stops the build; sync says so instead of leaving it in place without a word.


def test_sync_warns_about_a_legacy_hook_it_could_not_move(scaffolded_project, sync_args, strip_ansi, capsys):
    scripts = scaffolded_project / "scripts"
    (scripts / "dependencies.sh").write_text("echo deps\n")
    (scripts / "build_hooks.sh").write_text("echo hooks\n")

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert (scripts / "hooks.sh").read_text() == "echo deps\n"
    assert "scripts/build_hooks.sh was not moved: scripts/hooks.sh already exists. The build never runs it" in out


def test_sync_warns_about_a_legacy_build_conf_it_could_not_move(scaffolded_project, sync_args, strip_ansi, capsys):
    (scaffolded_project / "scripts" / "build.conf").write_text('TARBALL_PREFIX="LJI_"\n')

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "scripts/build.conf was not moved: scripts/core/build.conf already exists. The build stops" in out


def test_sync_prints_no_legacy_warning_for_a_current_tool(scaffolded_project, sync_args, strip_ansi, capsys):
    assert cli.sync_command(sync_args()) == 0

    assert "was not moved" not in strip_ansi(capsys.readouterr().out)
