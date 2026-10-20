"""`cli sync` and the files it edits beside the framework files: README.md and
.gitignore keep their line endings and byte order mark, a README.md that is not
UTF-8 is skipped the same way in a dry run, the managed .gitignore section keeps
its rules in order, a hash record that does not parse is named, and a framework
file with the wrong execute bits gets them fixed.
"""

import os
import stat
import subprocess
import sys

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
BADGE_RULE = "ngargparser-{}-green.svg"
HASH_RULE = "!.ngargparser/framework-hashes.json"


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


def old_badge(project):
    readme = project / "README.md"
    readme.write_bytes(
        readme.read_bytes().replace(BADGE_RULE.format(cli.__version__).encode(), b"ngargparser-0.0.1-green.svg")
    )
    return readme


def git_ignores(repo, name):
    """Whether git ignores `name` in `repo`, as an untracked file (a tracked one never is)."""
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return subprocess.run(["git", "check-ignore", "-q", "--no-index", name], cwd=repo).returncode == 0


# --- README.md ----------------------------------------------------------------------------


def test_a_readme_that_is_not_utf8_is_skipped_by_the_dry_run_too(scaffolded_project, sync_args, strip_ansi, capsys):
    readme = scaffolded_project / "README.md"
    readme.write_bytes(b"T\xe9st\n====\n")

    assert cli.sync_command(sync_args(dry_run=True)) == 0
    preview = strip_ansi(capsys.readouterr().out)
    assert cli.sync_command(sync_args()) == 0
    real = strip_ansi(capsys.readouterr().out)

    warning = "⚠ Skipped updating README badge for README.md: 'utf-8' codec can't decode byte 0xe9"
    assert warning in preview and warning in real
    assert "Error during synchronization" not in preview
    assert readme.read_bytes() == b"T\xe9st\n====\n"


@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"], ids=["no BOM", "BOM"])
def test_a_crlf_readme_keeps_its_line_endings(scaffolded_project, sync_args, bom):
    readme = old_badge(scaffolded_project)
    readme.write_bytes(bom + readme.read_bytes().replace(b"\n", b"\r\n"))
    readme_without = scaffolded_project / "no-badge.md"

    assert cli.sync_command(sync_args()) == 0

    content = readme.read_bytes()
    assert content.startswith(bom) and content.count(b"\xef\xbb\xbf") == len(bom) // 3  # the mark stays first
    assert BADGE_RULE.format(cli.__version__).encode() in content
    assert b"\n" not in content.replace(b"\r\n", b"")  # every line still ends in CRLF
    # A badge added to a CRLF README without one gets CRLF too, after the mark.
    readme_without.write_bytes(bom + b"# Title\r\n=====\r\nText\r\n")
    assert cli.upsert_readme_badge(str(readme_without), cli.__version__)
    added = readme_without.read_bytes()
    assert added.startswith(bom + b"# Title\r\n=====\r\n\r\n[![ngargparser]")
    assert b"\n" not in added.replace(b"\r\n", b"")


# --- .gitignore ---------------------------------------------------------------------------


@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"], ids=["no BOM", "BOM"])
def test_a_crlf_gitignore_keeps_its_line_endings(tmp_path, bom):
    gi = tmp_path / ".gitignore"
    managed = cli.GITIGNORE_MANAGED_BLOCK.replace(f"{HASH_RULE}\n", "")  # sync adds the rule again
    gi.write_bytes(bom + ("build/\n\n" + managed).replace("\n", "\r\n").encode())

    assert cli.ensure_gitignore(str(gi)) == "updated"

    content = gi.read_bytes()
    assert content.startswith(bom + b"build/\r\n")
    assert content.endswith(f"{HASH_RULE}\r\n".encode())
    assert b"\n" not in content.replace(b"\r\n", b"")
    assert cli.ensure_gitignore(str(gi)) == "unchanged"
    # Without a managed section, the block appended gets CRLF as well.
    gi.write_bytes(bom + b"node_modules/\r\n")
    assert cli.ensure_gitignore(str(gi)) == "updated"
    content = gi.read_bytes()
    assert content.startswith(bom + b"node_modules/\r\n\r\n# --- ngargparser (managed) ---\r\n")
    assert b"\n" not in content.replace(b"\r\n", b"")


def test_a_missing_ngargparser_rule_goes_before_the_hash_record_rule(tmp_path):
    # The rule used to be appended after the `!` line, so git ignored the hash record again.
    # A line of your own inside the section stays, after the managed rules.
    gi = tmp_path / ".gitignore"
    section = cli.GITIGNORE_MANAGED_BLOCK.replace(".ngargparser/*\n", "").replace("*.bak\n", "*.bak\nmy-scratch/\n")
    gi.write_text("notes/\n\n" + section + "\n# mine\nlocal.txt\n")

    assert cli.ensure_gitignore(str(gi), dry_run=True) == "updated"
    assert cli.ensure_gitignore(str(gi)) == "updated"

    assert gi.read_text() == "notes/\n\n" + cli.GITIGNORE_MANAGED_BLOCK + "my-scratch/\n\n# mine\nlocal.txt\n"
    assert not git_ignores(tmp_path, ".ngargparser/framework-hashes.json")
    assert git_ignores(tmp_path, ".ngargparser/sync-backups/x/src/core/app_env.py")
    assert git_ignores(tmp_path, "my-scratch/x")
    assert cli.ensure_gitignore(str(gi)) == "unchanged"


def test_a_section_an_earlier_sync_left_out_of_order_is_put_back_in_order(tmp_path):
    # What the earlier sync wrote: the ignore rule after the `!` rule. Nothing is missing,
    # but git ignores the hash record, so sync puts the managed rules back in order.
    comment, rules = cli.GITIGNORE_MANAGED_RULES[0]
    broken = cli.GITIGNORE_MANAGED_BLOCK.replace(".ngargparser/*\n", "") + f"{comment}\n.ngargparser/*\n"
    gi = tmp_path / ".gitignore"
    gi.write_text(broken)
    assert git_ignores(tmp_path, ".ngargparser/framework-hashes.json")  # the damage

    assert cli.ensure_gitignore(str(gi)) == "updated"

    assert gi.read_text() == cli.GITIGNORE_MANAGED_BLOCK
    assert not git_ignores(tmp_path, ".ngargparser/framework-hashes.json")


def test_sync_keeps_a_crlf_gitignore_crlf(scaffolded_project, sync_args):
    gi = scaffolded_project / ".gitignore"
    gi.write_bytes(gi.read_bytes().replace(f"{HASH_RULE}\n".encode(), b"").replace(b"\n", b"\r\n"))

    assert cli.sync_command(sync_args()) == 0

    content = gi.read_bytes()
    assert content.endswith(f"{HASH_RULE}\r\n".encode())
    assert b"\n" not in content.replace(b"\r\n", b"")


# --- The hash record ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "record",
    ['<<<<<<< HEAD\n{"src/core/app_env.py": "aa"}\n=======\n{}\n>>>>>>> other\n', "[1, 2]\n", "{not json"],
    ids=["conflict markers", "not an object", "invalid JSON"],
)
def test_a_hash_record_that_does_not_parse_is_named(runnable_project, sync_args, strip_ansi, capsys, record):
    hashes = runnable_project / ".ngargparser" / "framework-hashes.json"
    hashes.write_text(record)
    core = runnable_project / CORE_FILE
    core.write_text(core.read_text() + "# my local edit\n")
    warning = (
        "⚠ .ngargparser/framework-hashes.json does not parse; local edits to framework files cannot be detected "
        "this run (backups are still made)."
    )

    assert cli.sync_command(sync_args(dry_run=True)) == 0
    # A dry run makes no backups: it says the real run does.
    assert warning.replace("backups are still made", "a real run still makes backups") in strip_ansi(
        capsys.readouterr().out
    )
    assert cli.sync_command(sync_args()) == 0
    out = strip_ansi(capsys.readouterr().out)

    assert warning in out
    assert "Backups of overwritten files" in out  # the backup was made
    assert cli._framework_hashes_problem() is None  # the sync wrote a record that parses
    assert cli.sync_command(sync_args()) == 0
    assert "does not parse" not in strip_ansi(capsys.readouterr().out)


def test_a_missing_hash_record_is_not_named(scaffolded_project, sync_args, strip_ansi, capsys):
    # A tool from before the record existed has none; nothing to warn about. A record that
    # cannot be read is named, with the backups off for this run.
    (scaffolded_project / ".ngargparser" / "framework-hashes.json").unlink()
    assert cli.sync_command(sync_args()) == 0
    assert "framework-hashes.json" not in strip_ansi(capsys.readouterr().out)

    hashes = scaffolded_project / ".ngargparser" / "framework-hashes.json"
    hashes.unlink()
    hashes.mkdir()
    assert cli.sync_command(sync_args(dry_run=True, backup=False)) == 0
    assert (
        "⚠ .ngargparser/framework-hashes.json cannot be read; local edits to framework files cannot be detected "
        "this run (backups are off for this run)."
    ) in strip_ansi(capsys.readouterr().out)


# A record that cannot be read (not yours, mode 000) or is a folder made the dry run
# pass and the real run fail with Errno 13 or 21, rolled back. Both now warn and go on
# without local-edit detection, and a record that cannot be written is named.


needs_permission_errors = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="root ignores file permissions"
)


@pytest.mark.parametrize(
    "make_record, reason",
    [
        pytest.param(
            lambda path: path.chmod(0o000), "Permission denied", marks=needs_permission_errors, id="unreadable"
        ),
        pytest.param(lambda path: (path.unlink(), path.mkdir()), "Is a directory", id="folder"),
    ],
)
def test_a_hash_record_that_cannot_be_read_does_not_stop_the_sync(
    runnable_project, sync_args, strip_ansi, capsys, make_record, reason
):
    hashes = runnable_project / ".ngargparser" / "framework-hashes.json"
    core = runnable_project / CORE_FILE
    pristine = core.read_bytes()
    core.write_text(core.read_text() + "# an older framework version\n")
    make_record(hashes)
    warning = (
        "⚠ .ngargparser/framework-hashes.json cannot be read; local edits to framework files cannot be detected "
        "this run ({})."
    )
    try:
        assert cli.sync_command(sync_args(dry_run=True)) == 0
        assert warning.format("a real run still makes backups") in strip_ansi(capsys.readouterr().out)

        assert cli.sync_command(sync_args()) == 0
        out = strip_ansi(capsys.readouterr().out)
    finally:
        if hashes.is_file():
            hashes.chmod(0o644)

    assert warning.format("backups are still made") in out
    assert (
        f"⚠ Could not update .ngargparser/framework-hashes.json ({reason}). The framework files are synced, but "
        "local edits to them cannot be detected reliably until the record can be written: make it a writable "
        "file, or delete it."
    ) in out
    assert "completed successfully" in out and "Error during synchronization" not in out
    assert core.read_bytes() == pristine  # the sync stands


# --- File modes ---------------------------------------------------------------------------


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_a_framework_file_with_the_wrong_mode_gets_it_back(scaffolded_project, sync_args, strip_ansi, capsys):
    build_sh, configure, core = (scaffolded_project / p for p in ("scripts/core/build.sh", "configure", CORE_FILE))
    build_sh.chmod(0o664)  # chmod -x on a group-writable clone
    configure.chmod(0o644)
    core.chmod(0o755)  # its template is not executable
    before_build_sh = build_sh.read_bytes()

    assert cli.sync_command(sync_args(dry_run=True)) == 0
    preview = strip_ansi(capsys.readouterr().out)
    assert "Would fix the file mode of scripts/core/build.sh (664 → 775)" in preview
    assert "Framework files to change: 3" in preview
    assert mode(build_sh) == 0o664

    assert cli.sync_command(sync_args()) == 0
    out = strip_ansi(capsys.readouterr().out)

    assert "Fixed the file mode of scripts/core/build.sh (664 → 775)" in out
    assert "Fixed the file mode of configure (644 → 755)" in out
    assert f"Fixed the file mode of {CORE_FILE} (755 → 644)" in out
    assert "Framework files changed: 3" in out and "completed successfully" in out
    assert (mode(build_sh), mode(configure), mode(core)) == (0o775, 0o755, 0o644)
    assert build_sh.read_bytes() == before_build_sh
    assert not (scaffolded_project / ".ngargparser" / "sync-backups").exists()  # no content changed, no backup

    assert cli.sync_command(sync_args()) == 0
    assert "No files needed updating" in strip_ansi(capsys.readouterr().out)


# A content update wrote 755 or the template's mode (644), so a group-writable clone
# lost its group write bit, which a mode-only fix keeps.


def test_an_updated_framework_file_keeps_its_mode(runnable_project, sync_args, strip_ansi, capsys):
    modes = {
        "scripts/core/build.sh": (0o775, 0o775),
        "configure": (0o664, 0o775),  # its execute bits are fixed, as a mode-only fix does
        CORE_FILE: (0o664, 0o664),
        "Makefile": (0o664, 0o664),
    }
    for rel, (before, _after) in modes.items():
        path = runnable_project / rel
        path.write_bytes(path.read_bytes() + b"\n# an older framework version\n")
        path.chmod(before)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    for rel in modes:
        assert f"Updated {rel}" in out
    assert {rel: mode(runnable_project / rel) for rel in modes} == {rel: after for rel, (_b, after) in modes.items()}


def test_the_makefile_mode_is_left_alone(scaffolded_project, sync_args, strip_ansi, capsys):
    # Only the files whose mode matters are checked; make reads the Makefile. The
    # build.sh fix is the control.
    makefile = scaffolded_project / "Makefile"
    makefile.chmod(0o755)
    (scaffolded_project / "scripts/core/build.sh").chmod(0o644)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert mode(makefile) == 0o755 and "file mode of Makefile" not in out
    assert "Fixed the file mode of scripts/core/build.sh (644 → 755)" in out


def test_a_rolled_back_sync_puts_the_mode_back(runnable_project, sync_args, strip_ansi, capsys, monkeypatch):
    build_sh = runnable_project / "scripts/core/build.sh"
    build_sh.chmod(0o644)
    core = runnable_project / CORE_FILE
    core.write_text(core.read_text() + '\n\ndef local_helper():\n    return "added by hand"\n')
    parser = runnable_project / "src/DemoArgumentParser.py"
    parser.write_text("from core.NGArgumentParser import local_helper  # noqa: F401\n" + parser.read_text())
    real_check, modes = cli._smoke_check, []

    def check():
        modes.append(mode(build_sh))
        return real_check()

    monkeypatch.setattr(cli, "_smoke_check", check)

    assert cli.sync_command(sync_args()) == 1

    assert "Sync rolled back" in strip_ansi(capsys.readouterr().err)
    assert modes == [0o644, 0o755]  # fixed by the sync, before the test drive after it
    assert mode(build_sh) == 0o644  # and put back with everything else


def test_a_mode_that_is_not_yours_to_change_is_named_not_fatal(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch
):
    # A group-shared tool folder: build.sh belongs to a colleague, so chmod is refused.
    build_sh = scaffolded_project / "scripts/core/build.sh"
    build_sh.chmod(0o644)
    real_chmod = cli.os.chmod

    def chmod(path, *args, **kwargs):
        if os.path.abspath(path) == str(build_sh):
            raise PermissionError(1, "Operation not permitted")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr(cli.os, "chmod", chmod)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert (
        "⚠ scripts/core/build.sh has mode 644, and it is not yours to change. Its owner can fix it: "
        "chmod 755 scripts/core/build.sh"
    ) in out
    assert "Framework files changed: 0" in out and mode(build_sh) == 0o644
