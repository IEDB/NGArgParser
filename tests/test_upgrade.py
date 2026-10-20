"""Unit tests for the `cli upgrade --check` version-reporting logic.

These monkeypatch the network helper `_latest_release_tag` so no real remote is
contacted — the point is the classification of its three outcomes (a tag, no
tags, or an unreachable remote) into the right message and exit code.
"""

import subprocess
from argparse import Namespace

import pytest

from ngargparser import cli


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # These tests run from a git checkout, whose generate and sync stamp <version>+g<sha>
    # (see test_sync_checkout_stamps.py). Stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def _check_args(**overrides):
    defaults = dict(ref="latest", dev=False, check=True)
    defaults.update(overrides)
    return Namespace(**defaults)


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    # Ensure NGARGPARSER_UPGRADE_URL never leaks in and forces the override path.
    monkeypatch.delenv("NGARGPARSER_UPGRADE_URL", raising=False)
    # Guard: --check must never actually install.
    monkeypatch.setattr(cli, "_run_self_upgrade", lambda *a, **k: pytest.fail("must not self-upgrade during --check"))


def test_remote_unreachable_reports_honestly(monkeypatch, capsys):
    def boom(*a, **k):
        raise cli._RemoteUnavailable("network down")

    monkeypatch.setattr(cli, "_latest_release_tag", boom)
    rc = cli.upgrade_command(_check_args())
    out = capsys.readouterr().out
    assert rc == 2
    assert "Couldn't reach the remote" in out
    assert "Latest: unknown" in out
    assert "No semver tags" not in out  # the old, misleading line must not appear


def test_no_tags_reports_no_semver(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: None)
    rc = cli.upgrade_command(_check_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "No semver tags on remote" in out


def test_update_available(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: "v999.0.0")
    rc = cli.upgrade_command(_check_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "update available" in out
    assert f"Installed: {cli.__version__}" in out
    assert "v999.0.0" in out


def test_up_to_date(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: f"v{cli.__version__}")
    rc = cli.upgrade_command(_check_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "up to date" in out


def test_latest_release_tag_strict_raises_on_failure(monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git ls-remote", timeout=10)

    monkeypatch.setattr(subprocess, "check_output", timeout)
    with pytest.raises(cli._RemoteUnavailable):
        cli._latest_release_tag("https://example.invalid/repo.git", strict=True)
    # Non-strict swallows the same failure and returns None.
    assert cli._latest_release_tag("https://example.invalid/repo.git") is None


def test_latest_release_tag_no_tags_returns_none_even_strict(monkeypatch):
    # Reachable remote, but ls-remote output has no semver tags.
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "abc123\trefs/tags/nightly\n")
    assert cli._latest_release_tag("https://example.invalid/repo.git", strict=True) is None


@pytest.mark.parametrize(
    "tags, expected",
    [
        (["v0.4.1", "v0.4.2-test"], "v0.4.1"),
        (["v0.4.2", "v0.4.2-test"], "v0.4.2"),
        (["v0.4.2-test", "v0.4.2"], "v0.4.2"),
        (["v0.4.2", "v0.4.3-test"], "v0.4.2"),
        (["v1.0.0-rc1", "v0.9.0"], "v0.9.0"),
        (["v0.4.2-test"], None),
        (["v0.9.0", "v0.10.0"], "v0.10.0"),  # control: numeric, not text, order
    ],
)
def test_latest_release_tag_skips_pre_releases(monkeypatch, tags, expected):
    out = "".join(f"abc123\trefs/tags/{tag}\n" for tag in tags)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: out)
    assert cli._latest_release_tag("https://example.invalid/repo.git") == expected


# --- An installed version newer than the latest release is kept ------------------------------


@pytest.fixture
def installs(monkeypatch):
    """Record each install `cli upgrade` or `cli sync` starts, without installing anything,
    and stop a sync where it would re-run itself on the new install."""
    calls = []
    monkeypatch.setattr(cli, "_run_self_upgrade", lambda url: calls.append(url) or 0)

    def no_re_exec(*args):
        calls.append("re-exec")
        raise SystemExit(0)

    monkeypatch.setattr(cli.os, "execvpe", no_re_exec)
    return calls


@pytest.fixture
def pre_release_installed(monkeypatch):
    monkeypatch.setattr(cli, "__version__", "0.4.2rc1")
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: "v0.4.1")


def test_upgrade_keeps_an_installed_version_newer_than_the_latest_release(
    pre_release_installed, installs, strip_ansi, capsys, monkeypatch
):
    assert cli.upgrade_command(_check_args(check=False)) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "ℹ Installed: 0.4.2rc1    Latest: v0.4.1  (installed is newer)" in out
    assert "ℹ Installed ngargparser 0.4.2rc1 is newer than the latest release v0.4.1; not changing it." in out
    assert installs == []

    assert cli.upgrade_command(_check_args()) == 0  # --check says the same, without "update available"
    out = strip_ansi(capsys.readouterr().out)
    assert "(installed is newer)" in out and "update available" not in out

    # Control: a version named with --ref is the developer's choice, and is installed.
    assert cli.upgrade_command(_check_args(check=False, ref="v0.4.1")) == 0
    assert installs == [f"{cli.BASE_REPO_URL}@v0.4.1"]

    # Control: an older install still gets the latest release.
    monkeypatch.setattr(cli, "__version__", "0.4.0")
    assert cli.upgrade_command(_check_args(check=False)) == 0
    assert installs == [f"{cli.BASE_REPO_URL}@v0.4.1"] * 2
    assert "not changing it" not in strip_ansi(capsys.readouterr().out)


@pytest.fixture
def self_upgrading_sync(scaffolded_project, monkeypatch, sync_args):
    """A sync that starts with the self-upgrade: no NGARGPARSER_NO_SELF_UPGRADE, and not run from a checkout."""
    monkeypatch.delenv("NGARGPARSER_NO_SELF_UPGRADE", raising=False)
    monkeypatch.setattr(cli, "_local_checkout", lambda: None)
    return lambda **overrides: cli.sync_command(sync_args(upgrade=True, **overrides))


def test_sync_keeps_an_installed_version_newer_than_the_latest_release(
    self_upgrading_sync, pre_release_installed, installs, strip_ansi, capsys, scaffolded_project
):
    assert self_upgrading_sync() == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "ℹ Installed ngargparser 0.4.2rc1 is newer than the latest release v0.4.1; not changing it." in out
    assert installs == []
    assert "Framework synchronization completed successfully" in out  # it synced from what is installed
    assert 'scaffold_version = "0.4.2rc1"' in (scaffolded_project / "pyproject.toml").read_text()

    # Control: --ref names the version on purpose, so sync installs it.
    with pytest.raises(SystemExit):
        self_upgrading_sync(ref="v0.4.1")
    assert installs == [f"{cli.BASE_REPO_URL}@v0.4.1", "re-exec"]


def test_dry_run_says_a_real_sync_keeps_a_newer_install(
    self_upgrading_sync, pre_release_installed, installs, strip_ansi, capsys
):
    assert self_upgrading_sync(dry_run=True) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert (
        "ℹ A real run keeps the installed ngargparser 0.4.2rc1, which is newer than the latest release v0.4.1, "
        "and syncs from it: the same files as this preview." in out
    )
    assert "first installs" not in out and installs == []


# A folder named like a git ref must not turn the ref into a local checkout.


def test_a_folder_named_latest_does_not_become_the_upgrade_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NGARGPARSER_UPGRADE_URL", raising=False)
    (tmp_path / "latest").mkdir()
    (tmp_path / "master").mkdir()

    assert cli._resolve_upgrade_url("latest", latest_tag="v0.4.1") == (f"{cli.BASE_REPO_URL}@v0.4.1", "v0.4.1")
    assert cli._resolve_upgrade_url("latest", dev=True) == (f"{cli.BASE_REPO_URL}@master", "master")
    # Control: written as a path, the same folder is a local checkout.
    local = str(tmp_path / "latest")
    assert cli._resolve_upgrade_url("./latest") == (local, f"local:{local}")


def test_upgrade_with_the_remote_unreachable_names_ref_and_dev(monkeypatch, capsys):
    def boom(*a, **k):
        raise cli._RemoteUnavailable("network down")

    monkeypatch.setattr(cli, "_latest_release_tag", boom)
    rc = cli.upgrade_command(_check_args(check=False))
    out = capsys.readouterr().out
    assert rc == 2  # nothing installed (the autouse guard fails the test on an install)
    assert "Couldn't reach the remote" in out
    assert "  To upgrade anyway, name the version: cli upgrade --ref <tag>, or cli upgrade --dev for master." in out
    # Control: --check only reports, so it gets no hint.
    assert cli.upgrade_command(_check_args()) == 2
    assert "--ref" not in capsys.readouterr().out
