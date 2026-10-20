"""The self-upgrade a plain `cli sync` starts with, with the installer and the re-exec
stubbed: nothing is ever installed for real.

With the latest release already installed, sync used to install it again on every
run ("A real run first installs ngargparser v0.4.1 (installed: 0.4.1)").
"""

import pytest

from ngargparser import cli


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # These tests run from a git checkout, whose generate and sync stamp <version>+g<sha>
    # (see test_sync_checkout_stamps.py). Stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


LATEST = "v0.4.1"


class ReExec(BaseException):
    """What the stubbed os.execvpe raises: the real one never returns."""


@pytest.fixture
def stubs(monkeypatch):
    """The installer _run_self_upgrade picks (uv tool install here) and os.execvpe, stubbed."""
    monkeypatch.delenv("NGARGPARSER_UPGRADE_URL", raising=False)
    monkeypatch.delenv("NGARGPARSER_NO_SELF_UPGRADE", raising=False)
    for flag in ("NGARGPARSER_SYNC_NO_VERIFY", "NGARGPARSER_SYNC_NO_BACKUP", "NGARGPARSER_SYNC_ALLOW_DOWNGRADE"):
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setattr(cli, "_local_checkout", lambda: None)  # an installed release, not a checkout
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: LATEST)
    monkeypatch.setattr(cli, "_is_uv_tool_install", lambda: True)
    monkeypatch.setattr(cli.shutil, "which", lambda cmd: "/stub/bin/uv" if cmd == "uv" else None)
    seen = {"installed": [], "exec": []}

    def fake_check_call(cmd, *a, **k):
        seen["installed"].append(cmd)
        return 0

    def fake_exec(file, argv, env):
        seen["exec"].append((file, argv, env))
        raise ReExec

    monkeypatch.setattr("subprocess.check_call", fake_check_call)
    monkeypatch.setattr(cli.os, "execvpe", fake_exec)
    return seen


def test_sync_installs_nothing_when_the_latest_release_is_not_newer(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, stubs
):
    for installed, said in (
        ("0.5.0", f"Installed ngargparser 0.5.0 is newer than the latest release {LATEST}; not changing it."),
        ("0.4.1", f"Installed ngargparser 0.4.1 is the latest release ({LATEST}); not reinstalling it."),
    ):
        monkeypatch.setattr(cli, "__version__", installed)

        assert cli.sync_command(sync_args(upgrade=True, allow_downgrade=True)) == 0

        out = strip_ansi(capsys.readouterr().out)
        assert said in out
        assert stubs == {"installed": [], "exec": []}
        assert "Upgrading ngargparser" not in out and "Synchronizing framework files" in out

        # The dry run says the same about the real run.
        assert cli.sync_command(sync_args(upgrade=True, dry_run=True, allow_downgrade=True)) == 0
        preview = strip_ansi(capsys.readouterr().out)
        assert f"A real run keeps the installed ngargparser {installed}, which is " in preview
        assert "A real run first installs" not in preview


def test_sync_installs_a_newer_latest_release_and_re_execs_with_the_flags(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, stubs
):
    # Also the equal case above: without it this test alone passes before and after the change.
    monkeypatch.setattr(cli, "__version__", "0.4.1")
    assert cli.sync_command(sync_args(upgrade=True)) == 0
    assert stubs["installed"] == []
    capsys.readouterr()

    monkeypatch.setattr(cli, "__version__", "0.4.0")
    with pytest.raises(ReExec):
        cli.sync_command(sync_args(upgrade=True, verify=False, backup=False, allow_downgrade=True))

    out = strip_ansi(capsys.readouterr().out)
    assert f"Latest release tag: {LATEST}" in out
    url = f"{cli.BASE_REPO_URL}@{LATEST}"
    assert stubs["installed"] == [["uv", "tool", "install", "--force", "--reinstall", url]]
    ((_file, argv, env),) = stubs["exec"]
    assert argv[1:] == ["s"]
    assert env["NGARGPARSER_NO_SELF_UPGRADE"] == "1"
    assert env["NGARGPARSER_SYNC_NO_VERIFY"] == "1"
    assert env["NGARGPARSER_SYNC_NO_BACKUP"] == "1"
    assert env["NGARGPARSER_SYNC_ALLOW_DOWNGRADE"] == "1"
    # The dry run names the release a real run installs.
    assert cli.sync_command(sync_args(upgrade=True, dry_run=True, allow_downgrade=True)) == 0
    assert f"A real run first installs ngargparser {LATEST} (installed: 0.4.0)" in strip_ansi(capsys.readouterr().out)


# With GitLab unreachable, the failed tag lookup read as "no release tags", so sync
# installed master over the release. A failed lookup now skips the upgrade; a remote
# that answers with no release tags still gets master, and sync says so.


def lookup_that_fails(url, timeout=10, *, strict=False, prompt=True):
    """_latest_release_tag with the remote unreachable: None, or _RemoteUnavailable in strict mode."""
    if strict:
        raise cli._RemoteUnavailable("network down")
    return None


def test_sync_with_gitlab_unreachable_syncs_from_the_installed_version(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, stubs
):
    monkeypatch.setattr(cli, "_latest_release_tag", lookup_that_fails)

    assert cli.sync_command(sync_args(upgrade=True)) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert (
        "⚠ Couldn't check for a newer ngargparser (GitLab unreachable: network/VPN, credentials, or git missing); "
        f"syncing from the installed version {cli.__version__}."
    ) in out
    assert stubs == {"installed": [], "exec": []}
    assert "master" not in out and "Upgrading ngargparser" not in out
    assert "Synchronizing framework files" in out


def test_sync_with_no_release_tags_installs_master_and_says_so(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, stubs
):
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: None)  # reachable, no release tags

    with pytest.raises(ReExec):
        cli.sync_command(sync_args(upgrade=True))

    out = strip_ansi(capsys.readouterr().out)
    assert "⚠ No semver tags on remote; 'latest' resolves to master." in out
    assert stubs["installed"] == [["uv", "tool", "install", "--force", "--reinstall", f"{cli.BASE_REPO_URL}@master"]]
