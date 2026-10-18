import hashlib
import json

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
    assert not (scaffolded_project / ".ngargparser" / "sync-backups").exists()


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
    assert not (scaffolded_project / ".ngargparser" / "sync-backups").exists()
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
    assert not (scaffolded_project / ".ngargparser" / "sync-backups").exists()


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


# A dry run lists the legacy moves a real run makes, and the same warnings.

LEGACY_LAYOUT_MOVES = [
    "Would move scripts/build.sh → scripts/core/build.sh (framework-owned scripts now live under scripts/core/)",
    "Would move scripts/dependencies.sh → scripts/hooks.sh",
    "Would move scripts/build.conf → scripts/core/build.conf",
    "Would move scripts/do-not-distribute.txt → .distignore",
]


def make_legacy_layout(project):
    """Put every file sync migrates back under its old name; return {old name: content}."""
    (project / "scripts/core/build.sh").rename(project / "scripts/build.sh")
    (project / "scripts/core/build.conf").rename(project / "scripts/build.conf")
    (project / "scripts/dependencies.sh").write_text("echo deps\n")
    (project / "scripts/build_hooks.sh").write_text("echo hooks\n")
    (project / ".distignore").rename(project / "scripts/do-not-distribute.txt")
    old = ["scripts/build.sh", "scripts/build.conf", "scripts/dependencies.sh", "scripts/build_hooks.sh"]
    return {name: (project / name).read_bytes() for name in [*old, "scripts/do-not-distribute.txt"]}


def test_dry_run_lists_legacy_moves_and_moves_nothing(scaffolded_project, sync_args, strip_ansi, capsys):
    legacy = make_legacy_layout(scaffolded_project)

    assert cli.sync_command(sync_args(dry_run=True)) == 0

    lines = [line.strip("└ ") for line in strip_ansi(capsys.readouterr().out).splitlines()]
    assert [line for line in lines if line.startswith("Would move")] == LEGACY_LAYOUT_MOVES
    assert any("scripts/build_hooks.sh was not moved: scripts/hooks.sh already exists" in line for line in lines)
    # Compared with the build.sh a real run moves there, not reported as a new file.
    assert "scripts/core/build.sh is already up to date" in lines
    for name, content in legacy.items():
        assert (scaffolded_project / name).read_bytes() == content, f"{name} was moved or changed"
    for current in ["scripts/core/build.sh", "scripts/hooks.sh", "scripts/core/build.conf", ".distignore"]:
        assert not (scaffolded_project / current).exists(), f"{current} was created"


def test_dry_run_matches_what_the_real_run_does(scaffolded_project, sync_args, strip_ansi, capsys):
    make_legacy_layout(scaffolded_project)

    cli.sync_command(sync_args(dry_run=True))
    preview = strip_ansi(capsys.readouterr().out).splitlines()
    assert cli.sync_command(sync_args()) == 0
    real = strip_ansi(capsys.readouterr().out).splitlines()

    assert [line.replace("Would move", "Migrated") for line in preview if "Would move" in line] == [
        line for line in real if "Migrated" in line
    ]
    assert [line for line in preview if "was not moved" in line] == [line for line in real if "was not moved" in line]
    assert any("was not moved" in line for line in real)  # the comparison above is not empty


@pytest.mark.parametrize("legacy", ["scripts/do-not-distribute.txt", "do-not-distribute.txt", "scripts/.distignore"])
def test_sync_moves_a_legacy_exclusion_file_to_distignore(scaffolded_project, sync_args, strip_ansi, capsys, legacy):
    # build.sh stops while one of these exists without a .distignore ("... is no longer read").
    content = "# keep out\nsecret_data/\n!keep.txt\n"
    (scaffolded_project / ".distignore").unlink()
    (scaffolded_project / legacy).write_text(content)

    assert cli.sync_command(sync_args()) == 0

    assert not (scaffolded_project / legacy).exists()
    assert (scaffolded_project / ".distignore").read_text() == content
    assert f"Migrated {legacy} → .distignore" in strip_ansi(capsys.readouterr().out)


def test_sync_warns_about_a_legacy_exclusion_file_it_could_not_move(scaffolded_project, sync_args, strip_ansi, capsys):
    distignore = scaffolded_project / ".distignore"
    current = distignore.read_text()
    (scaffolded_project / "do-not-distribute.txt").write_text("secret_data/\n")

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert distignore.read_text() == current
    assert (scaffolded_project / "do-not-distribute.txt").read_text() == "secret_data/\n"
    assert (
        "do-not-distribute.txt was not moved: .distignore already exists. The build ignores it; "
        "merge it into .distignore and delete it."
    ) in out


# Sync warns before it replaces a framework file someone edited by hand. It
# knows the file was edited when its hash differs from the one recorded the
# last time `cli generate` or `cli sync` wrote it.

LOCAL_EDIT = "\n# a fix made by hand\n"


def edit_core_file(project):
    target = project / CORE_FILE
    target.write_text(target.read_text() + LOCAL_EDIT)
    return target.read_text()


def test_sync_warns_before_replacing_a_local_edit_and_names_the_backup(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    edited = edit_core_file(scaffolded_project)  # recorded by `cli generate`

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    warning = next(line for line in out.splitlines() if "had local edits" in line)
    assert f"{CORE_FILE} had local edits; sync replaced them. Your version is in .ngargparser/sync-backups/" in warning
    backup = warning.split("Your version is in ", 1)[1].rstrip(".")
    assert backup.endswith(CORE_FILE)
    assert (scaffolded_project / backup).read_text() == edited
    assert LOCAL_EDIT not in (scaffolded_project / CORE_FILE).read_text()


def test_sync_records_hashes_so_the_next_sync_sees_local_edits(scaffolded_project, sync_args, strip_ansi, capsys):
    (scaffolded_project / ".ngargparser" / "framework-hashes.json").unlink(missing_ok=True)  # an older tool
    assert cli.sync_command(sync_args()) == 0
    edit_core_file(scaffolded_project)
    capsys.readouterr()

    assert cli.sync_command(sync_args()) == 0

    assert f"{CORE_FILE} had local edits; sync replaced them." in strip_ansi(capsys.readouterr().out)


def test_local_edit_warning_without_backups_says_none_was_kept(scaffolded_project, sync_args, strip_ansi, capsys):
    edit_core_file(scaffolded_project)

    assert cli.sync_command(sync_args(backup=False)) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert (
        f"{CORE_FILE} had local edits; sync replaced them. Backups are off for this run, so your version was not kept."
    ) in out
    assert not (scaffolded_project / ".ngargparser" / "sync-backups").exists()


def test_dry_run_says_it_would_replace_local_edits(scaffolded_project, sync_args, strip_ansi, capsys):
    edited = edit_core_file(scaffolded_project)

    assert cli.sync_command(sync_args(dry_run=True)) == 0

    assert f"Would replace local edits in {CORE_FILE}." in strip_ansi(capsys.readouterr().out)
    assert (scaffolded_project / CORE_FILE).read_text() == edited


def test_no_local_edit_warning_without_a_record(scaffolded_project, sync_args, strip_ansi, capsys):
    # Control: a fresh clone or a tool generated before the record existed.
    (scaffolded_project / ".ngargparser" / "framework-hashes.json").unlink(missing_ok=True)
    edit_core_file(scaffolded_project)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert f"Updated {CORE_FILE}" in out
    assert "local edits" not in out


def test_no_local_edit_warning_for_an_older_framework_version(scaffolded_project, sync_args, strip_ansi, capsys):
    # Control: the file differs from this framework version but is exactly what was
    # recorded (an older version wrote it), so nobody edited it.
    target = scaffolded_project / CORE_FILE
    target.write_text("# NGArgumentParser from an older framework version\n")
    record = scaffolded_project / ".ngargparser" / "framework-hashes.json"
    record.parent.mkdir(exist_ok=True)
    record.write_text(json.dumps({CORE_FILE: hashlib.sha256(target.read_bytes()).hexdigest()}))

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert f"Updated {CORE_FILE}" in out
    assert "local edits" not in out


def test_dry_run_with_only_a_move_pending_does_not_say_nothing_would_change(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    assert cli.sync_command(sync_args()) == 0  # everything else is current
    (scaffolded_project / ".distignore").rename(scaffolded_project / "do-not-distribute.txt")
    capsys.readouterr()

    assert cli.sync_command(sync_args(dry_run=True)) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "Would move do-not-distribute.txt → .distignore" in out
    assert "nothing would change" not in out


# cli sync works only in an ngargparser tool: src/ and scripts/ alone match other
# projects, whose Makefile and README.md it used to overwrite.


def test_sync_refuses_a_folder_that_is_not_an_ngargparser_tool(in_tmp_dir, sync_args, strip_ansi, capsys):
    (in_tmp_dir / "src").mkdir()
    (in_tmp_dir / "scripts").mkdir()
    files = {
        "Makefile": "all:\n\techo mine\n",
        "README.md": "# my project\n",
        "pyproject.toml": '[project]\nname = "other"\nversion = "1.0"\n',
    }
    for name, text in files.items():
        (in_tmp_dir / name).write_text(text)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().out)
    assert "This folder is not an ngargparser tool" in out
    assert all((in_tmp_dir / name).read_text() == text for name, text in files.items())
    assert not (in_tmp_dir / "src" / "core").exists()


def test_sync_accepts_a_tool_with_only_the_core_parser(scaffolded_project, sync_args):
    # Control: phbr's shape, with no [tool.ngargparser] stamp and the legacy scripts/build.sh.
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().split("[tool.ngargparser]")[0])
    (scaffolded_project / "scripts" / "core" / "build.sh").rename(scaffolded_project / "scripts" / "build.sh")

    assert cli.sync_command(sync_args()) == 0
    assert (scaffolded_project / "scripts" / "core" / "build.sh").exists()


def test_sync_stamp_past_a_bracket_comment_keeps_one_table(scaffolded_project, sync_args):
    # The re-audit's repro: a "[" comment above an old stamp used to give two tables,
    # which uv (and so every deploy) refuses.
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            f'[tool.ngargparser]\nscaffold_version = "{cli.__version__}"',
            '[tool.ngargparser]\n# see [release notes]\nscaffold_version = "0.3.0"',
        )
    )

    assert cli.sync_command(sync_args()) == 0

    content = pyproject.read_text()
    assert content.count("[tool.ngargparser]") == 1
    assert f'scaffold_version = "{cli.__version__}"' in content
    try:
        import tomllib
    except ImportError:
        return
    assert tomllib.loads(content)["tool"]["ngargparser"]["scaffold_version"] == cli.__version__


# Tools generated before [tool.ngargparser] app_name existed get it from sync, so a
# deployed copy (no .env, installed to <name>/<version>/) keeps its name.


def drop_app_name(project):
    pyproject = project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('app_name = "demo"\n', ""))
    assert "app_name" not in pyproject.read_text()
    return pyproject


def test_sync_adds_app_name_from_env(scaffolded_project, sync_args, strip_ansi, capsys):
    pyproject = drop_app_name(scaffolded_project)
    (scaffolded_project / ".env").write_text("APP_ROOT=/x\nAPP_NAME=my-tool\n")

    assert cli.sync_command(sync_args()) == 0

    assert 'Added app_name = "my-tool" to [tool.ngargparser]' in strip_ansi(capsys.readouterr().out)
    content = pyproject.read_text()
    assert content.count("[tool.ngargparser]") == 1
    assert 'app_name = "my-tool"\n' in content


def test_sync_adds_app_name_from_folder_without_env(scaffolded_project, sync_args):
    # No .env: the name ./configure would derive from the folder (here "demo").
    pyproject = drop_app_name(scaffolded_project)
    (scaffolded_project / ".env").unlink()

    assert cli.sync_command(sync_args()) == 0

    assert 'app_name = "demo"\n' in pyproject.read_text()


def test_sync_dry_run_reports_app_name_and_writes_nothing(scaffolded_project, sync_args, strip_ansi, capsys):
    pyproject = drop_app_name(scaffolded_project)
    before = pyproject.read_text()

    assert cli.sync_command(sync_args(dry_run=True)) == 0

    assert 'Would add app_name = "demo" to [tool.ngargparser]' in strip_ansi(capsys.readouterr().out)
    assert pyproject.read_text() == before


def test_sync_keeps_an_existing_app_name(scaffolded_project, sync_args, strip_ansi, capsys):
    # Control: a second sync, or a tool that set its own name, changes nothing.
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('app_name = "demo"', 'app_name = "renamed"'))
    (scaffolded_project / ".env").write_text("APP_ROOT=/x\nAPP_NAME=other\n")
    before = pyproject.read_text()

    assert cli.sync_command(sync_args()) == 0

    assert "app_name" not in strip_ansi(capsys.readouterr().out)
    assert pyproject.read_text() == before
