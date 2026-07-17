"""Unit tests for the `cli upgrade --check` version-reporting logic.

These monkeypatch the network helper `_latest_release_tag` so no real remote is
contacted — the point is the classification of its three outcomes (a tag, no
tags, or an unreachable remote) into the right message and exit code.
"""

import subprocess
from argparse import Namespace

import pytest

from ngargparser import cli


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
