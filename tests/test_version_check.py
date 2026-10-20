"""Unit tests for the release check that `cli --version` runs on every invocation, and for the
update notice, which a help screen reads from its cache only.

The remote lookup `_latest_release_tag` is replaced in every test and the cache
directory is redirected to a temp dir, so nothing contacts GitLab or touches the
developer's real `~/.cache/ngargparser/update-check.json`.
"""

import atexit
import json
import os
import subprocess
import sys
import time

import pytest

from ngargparser import cli

INSTALLED = "1.2.3"
NOTICE = "A new ngargparser is available"
AHEAD = "is ahead of the latest release tag"
UNREACHABLE = "Couldn't check for a newer ngargparser"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_NO_UPDATE_CHECK", raising=False)
    # Pin the installed version so the comparisons don't depend on the release under test.
    monkeypatch.setattr(cli, "__version__", INSTALLED)
    monkeypatch.setattr(cli, "_HELP_SHOWN", False, raising=False)  # main() runs once per process; here, per test


def _cache_file(tmp_path):
    return tmp_path / "ngargparser" / "update-check.json"


def _seed_cache(tmp_path, latest):
    path = _cache_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"checked_at": time.time(), "latest": latest}), encoding="utf-8")
    return path


def _lookup_returning(monkeypatch, tag):
    """Replace the remote lookup with one that returns `tag`; return the list of recorded calls."""
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return tag

    monkeypatch.setattr(cli, "_latest_release_tag", fake)
    return calls


def _lookup_must_not_run(monkeypatch):
    monkeypatch.setattr(cli, "_latest_release_tag", lambda *a, **k: pytest.fail("must not contact the remote"))


def _run_cli(monkeypatch, capsys, flag="--version"):
    monkeypatch.setattr(sys, "argv", ["cli", flag])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    captured = capsys.readouterr()
    return exc.value.code, captured.out, captured.err


def test_passive_notice_text_unchanged(monkeypatch, tmp_path, capsys):
    # Pins the wording of the cache-based notice the other `cli` commands print.
    _seed_cache(tmp_path, "v9.9.9")
    _lookup_must_not_run(monkeypatch)  # the cache is fresh, so no lookup
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)  # a terminal without NO_COLOR gets the colour codes

    cli._maybe_notify_update("generate")

    assert capsys.readouterr().err == (
        f"\n\033[33m⬆  A new ngargparser is available: {INSTALLED} → v9.9.9\033[0m\n"
        "   run `cli upgrade` to update   ·   silence: NGARGPARSER_NO_UPDATE_CHECK=1\n"
    )


def test_version_notifies_when_newer_tag(monkeypatch, capsys):
    _lookup_returning(monkeypatch, "v999.0.0")
    code, out, err = _run_cli(monkeypatch, capsys)
    assert code == 0
    assert out == f"cli {INSTALLED}\n"
    assert NOTICE in err
    assert f"{INSTALLED} → v999.0.0" in err
    assert "cli upgrade" in err


def test_version_notifies_on_every_run(monkeypatch, capsys):
    calls = _lookup_returning(monkeypatch, "v999.0.0")
    for _ in range(2):
        _, _, err = _run_cli(monkeypatch, capsys)
        assert NOTICE in err
    assert len(calls) == 2  # no throttle: one live lookup per invocation


def test_version_lookup_is_strict_and_non_interactive(monkeypatch, capsys):
    calls = _lookup_returning(monkeypatch, "v999.0.0")
    _run_cli(monkeypatch, capsys)
    args, kwargs = calls[0]
    assert args == (cli.BASE_REPO_URL[len("git+") :],)
    assert kwargs == {"timeout": kwargs["timeout"], "strict": True, "prompt": False}
    # Shorter than the wait of a dry run's lookup: GitLab answers in 1.5 to 3.1 s when it is there.
    assert (
        kwargs["timeout"] == getattr(cli, "VERSION_CHECK_TIMEOUT", None)
        and kwargs["timeout"] < cli.RELEASE_CHECK_TIMEOUT
    )


def test_version_says_when_gitlab_did_not_answer_in_time(monkeypatch, capsys):
    waited = []

    def slow(*a, **k):
        waited.append(k["timeout"])
        try:
            raise subprocess.TimeoutExpired(["git", "ls-remote"], k["timeout"])
        except subprocess.TimeoutExpired as e:
            raise cli._RemoteUnavailable(str(e)) from e

    monkeypatch.setattr(cli, "_latest_release_tag", slow)
    code, out, err = _run_cli(monkeypatch, capsys)
    assert code == 0 and out == f"cli {INSTALLED}\n"
    # A slow answer is not an unreachable GitLab.
    assert err == f"⚠ Couldn't check for a newer ngargparser (GitLab did not answer within {waited[0]} s).\n"


def test_version_never_reads_cache(monkeypatch, tmp_path, capsys):
    # A fresh cache naming another tag would satisfy the passive notifier; --version ignores it.
    _seed_cache(tmp_path, "v500.0.0")
    calls = _lookup_returning(monkeypatch, "v999.0.0")
    _, _, err = _run_cli(monkeypatch, capsys)
    assert len(calls) == 1
    assert "v999.0.0" in err
    assert "v500.0.0" not in err


def test_version_never_writes_cache(monkeypatch, tmp_path, capsys):
    _lookup_returning(monkeypatch, "v999.0.0")

    _run_cli(monkeypatch, capsys)
    assert not _cache_file(tmp_path).exists()  # none created

    path = _seed_cache(tmp_path, "v500.0.0")
    before = path.read_bytes()
    _run_cli(monkeypatch, capsys)
    assert path.read_bytes() == before  # an existing one is left alone


def test_version_silent_when_up_to_date(monkeypatch, capsys):
    _lookup_returning(monkeypatch, f"v{INSTALLED}")
    code, out, err = _run_cli(monkeypatch, capsys)
    assert code == 0
    assert out == f"cli {INSTALLED}\n"
    assert err == ""


def test_version_reports_when_installed_is_ahead(monkeypatch, capsys):
    _lookup_returning(monkeypatch, "v0.0.1")
    code, _, err = _run_cli(monkeypatch, capsys)
    assert code == 0
    assert err == f"ℹ Installed ngargparser {INSTALLED} is ahead of the latest release tag v0.0.1.\n"
    assert NOTICE not in err


def test_version_reports_a_pre_release_ahead_of_the_latest_tag(monkeypatch, capsys):
    # 0.4.2rc1 sorts after 0.4.1 (PEP 440); the X.Y.Z parser could not read it and
    # reported an update to the older tag.
    monkeypatch.setattr(cli, "__version__", "0.4.2rc1")
    _lookup_returning(monkeypatch, "v0.4.1")
    _, _, err = _run_cli(monkeypatch, capsys)
    assert err == "ℹ Installed ngargparser 0.4.2rc1 is ahead of the latest release tag v0.4.1.\n"
    assert NOTICE not in err


def test_version_notifies_when_installed_version_unknown(monkeypatch, capsys):
    monkeypatch.setattr(cli, "__version__", "unknown")
    _lookup_returning(monkeypatch, "v0.0.1")
    _, out, err = _run_cli(monkeypatch, capsys)
    assert out == "cli unknown\n"
    assert NOTICE in err


def test_version_silent_when_remote_has_no_tags(monkeypatch, capsys):
    _lookup_returning(monkeypatch, None)
    code, _, err = _run_cli(monkeypatch, capsys)
    assert code == 0
    assert err == ""


def test_version_reports_unreachable_remote(monkeypatch, capsys):
    def boom(*a, **k):
        raise cli._RemoteUnavailable("network down")

    monkeypatch.setattr(cli, "_latest_release_tag", boom)
    code, out, err = _run_cli(monkeypatch, capsys)
    assert code == 0  # a failed check never fails --version
    assert out == f"cli {INSTALLED}\n"
    assert UNREACHABLE in err
    assert NOTICE not in err


def test_version_survives_unexpected_error(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(cli, "_latest_release_tag", boom)
    code, out, _ = _run_cli(monkeypatch, capsys)
    assert code == 0
    assert out == f"cli {INSTALLED}\n"


def test_version_skips_check_in_ci(monkeypatch, capsys):
    monkeypatch.setenv("CI", "true")
    _lookup_must_not_run(monkeypatch)
    code, out, err = _run_cli(monkeypatch, capsys)
    assert (code, out, err) == (0, f"cli {INSTALLED}\n", "")


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_version_skips_check_when_opted_out(monkeypatch, capsys, value):
    monkeypatch.setenv("NGARGPARSER_NO_UPDATE_CHECK", value)
    _lookup_must_not_run(monkeypatch)
    code, out, err = _run_cli(monkeypatch, capsys)
    assert (code, out, err) == (0, f"cli {INSTALLED}\n", "")


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "False", ""])
def test_version_checks_when_opt_out_is_off(monkeypatch, capsys, value):
    # Setting the switch back to an "off" value must undo `=1`, not keep the check silenced.
    monkeypatch.setenv("NGARGPARSER_NO_UPDATE_CHECK", value)
    calls = _lookup_returning(monkeypatch, "v999.0.0")
    _, _, err = _run_cli(monkeypatch, capsys)
    assert len(calls) == 1
    assert NOTICE in err


def test_passive_notice_honors_opt_out_values(monkeypatch, tmp_path, capsys):
    # The cache-based notice on other commands reads the same switch the same way.
    _seed_cache(tmp_path, "v9.9.9")
    _lookup_must_not_run(monkeypatch)  # the cache is fresh, so no lookup
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

    monkeypatch.setenv("NGARGPARSER_NO_UPDATE_CHECK", "1")
    cli._maybe_notify_update("generate")
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("NGARGPARSER_NO_UPDATE_CHECK", "0")
    cli._maybe_notify_update("generate")
    assert NOTICE in capsys.readouterr().err


def test_version_notice_plain_when_not_a_tty(monkeypatch, capsys):
    # capsys replaces stderr with a non-terminal stream: the notice still prints, without color.
    _lookup_returning(monkeypatch, "v999.0.0")
    _, _, err = _run_cli(monkeypatch, capsys)
    assert NOTICE in err
    assert "\033[" not in err


def test_version_notice_colored_on_a_tty(monkeypatch, capsys):
    _lookup_returning(monkeypatch, "v999.0.0")
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    _, _, err = _run_cli(monkeypatch, capsys)
    assert f"\033[33m⬆  {NOTICE}" in err


def test_version_does_not_double_notify(monkeypatch, capsys):
    registered, unregistered = [], []
    monkeypatch.setattr(atexit, "register", registered.append)
    monkeypatch.setattr(atexit, "unregister", unregistered.append)
    _lookup_returning(monkeypatch, "v999.0.0")

    _, _, err = _run_cli(monkeypatch, capsys)

    assert cli._notify_at_exit in registered
    assert cli._notify_at_exit in unregistered  # the passive, cache-based notice is cancelled
    assert err.count(NOTICE) == 1


def test_short_flag_v(monkeypatch, capsys):
    _lookup_returning(monkeypatch, "v999.0.0")
    code, out, err = _run_cli(monkeypatch, capsys, flag="-v")
    assert code == 0
    assert out == f"cli {INSTALLED}\n"
    assert NOTICE in err


def test_latest_release_tag_prompt_off(monkeypatch):
    recorded = []

    def fake_check_output(cmd, **kwargs):
        recorded.append(kwargs)
        return "abc123\trefs/tags/v1.0.0\n"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    monkeypatch.setenv("GIT_ASKPASS", "/some/askpass-helper")

    assert cli._latest_release_tag("https://example.invalid/repo.git", prompt=False) == "v1.0.0"
    env = recorded[0]["env"]
    assert (env["GIT_TERMINAL_PROMPT"], env["GIT_ASKPASS"], env["SSH_ASKPASS"]) == ("0", "", "")
    assert env["PATH"] == os.environ["PATH"]  # the rest of the environment is passed through
    assert recorded[0]["stdin"] is subprocess.DEVNULL

    # Control: the default call leaves the environment and stdin alone, as before.
    assert cli._latest_release_tag("https://example.invalid/repo.git") == "v1.0.0"
    assert recorded[1]["env"] is None
    assert recorded[1]["stdin"] is None


def test_version_notice_links_release_notes(monkeypatch, capsys):
    _lookup_returning(monkeypatch, "v999.0.0")
    _, _, err = _run_cli(monkeypatch, capsys)
    assert err == (
        f"\n⬆  A new ngargparser is available: {INSTALLED} → v999.0.0\n"
        "   release notes: https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser"
        "/-/blob/v999.0.0/CHANGELOG.md\n"
        "   run `cli upgrade` to update   ·   silence: NGARGPARSER_NO_UPDATE_CHECK=1\n"
    )


def test_release_notes_url_quotes_the_tag():
    assert cli._release_notes_url("v1.2.4").endswith("/ngargparser/-/blob/v1.2.4/CHANGELOG.md")
    assert cli._release_notes_url("v1.2.4+build.5").endswith("/-/blob/v1.2.4%2Bbuild.5/CHANGELOG.md")


# --- A help screen never contacts the remote ----------------------------------
# `cli --help` used to run the live check (2 to 3 s); a help screen now reads only the cache.


def _stale_cache(tmp_path, latest):
    path = _cache_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"checked_at": time.time() - 2 * cli.UPDATE_CHECK_TTL, "latest": latest}))
    return path


@pytest.mark.parametrize("argv", [["--help"], ["-h"], ["sync", "--help"], ["deps", "add", "-h"], ["generate", "-h"]])
def test_help_never_contacts_the_remote_even_when_the_cache_is_stale(monkeypatch, tmp_path, capsys, argv):
    cache = _stale_cache(tmp_path, "v999.0.0")
    before = cache.read_bytes()
    _lookup_must_not_run(monkeypatch)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)  # where the notice may look up
    monkeypatch.setattr(sys, "argv", ["cli", *argv])

    with pytest.raises(SystemExit) as exc:
        cli.main()
    cli._notify_at_exit()  # what atexit runs as the process ends

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert captured.out.startswith("usage: cli")
    # The cached release is still news: it is shown, from the cache.
    assert f"{INSTALLED} → v999.0.0" in captured.err
    assert cache.read_bytes() == before  # not looked up, so not rewritten


def test_help_without_a_cache_is_silent_and_another_command_still_looks_up(monkeypatch, tmp_path, capsys):
    _lookup_must_not_run(monkeypatch)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    code, out, err = _run_cli(monkeypatch, capsys, flag="--help")
    cli._notify_at_exit()
    assert code == 0 and out.startswith("usage: cli ")
    assert err == "" and capsys.readouterr().err == ""

    # Control: the notice of a command that is not a help screen keeps its one lookup a day.
    monkeypatch.setattr(cli, "_HELP_SHOWN", False)
    _stale_cache(tmp_path, "v1.0.0")
    calls = _lookup_returning(monkeypatch, "v999.0.0")
    monkeypatch.setattr(sys, "argv", ["cli", "deps", "list"])

    cli._notify_at_exit()

    assert len(calls) == 1 and calls[0][1] == {"timeout": 4}
    assert f"{INSTALLED} → v999.0.0" in capsys.readouterr().err


def test_help_lists_both_flags(monkeypatch, capsys):
    # The hand-registered -h/--help keeps argparse's stock wording and position.
    _lookup_must_not_run(monkeypatch)
    _, out, _ = _run_cli(monkeypatch, capsys, flag="--help")
    assert out.splitlines()[0].startswith("usage: cli [-h] [-v] ")
    assert "-h, --help" in out and "show this help message and exit" in out
    assert "-v, --version" in out and "show program's version number and exit" in out
    for command in ("generate", "deps", "sync", "upgrade"):
        assert command in out


def test_subcommand_help_does_not_run_live_check(monkeypatch, capsys):
    registered, unregistered = [], []
    monkeypatch.setattr(atexit, "register", registered.append)
    monkeypatch.setattr(atexit, "unregister", unregistered.append)
    _lookup_must_not_run(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["cli", "sync", "--help"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert captured.out.startswith("usage: cli sync ")
    assert captured.err == ""
    assert cli._notify_at_exit in registered and unregistered == []  # passive notifier left in place


def test_bare_cli_does_not_run_live_check(monkeypatch, capsys):
    registered, unregistered = [], []
    monkeypatch.setattr(atexit, "register", registered.append)
    monkeypatch.setattr(atexit, "unregister", unregistered.append)
    _lookup_must_not_run(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["cli"])

    assert cli.main() == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("usage: cli ")
    assert captured.err == ""
    assert cli._notify_at_exit in registered and unregistered == []


# Every installed cli shares the cache file, and an older one may have stored a
# pre-release tag such as v0.4.2-test. The notice must not offer it.


def test_passive_notice_looks_up_again_over_a_cached_pre_release(monkeypatch, tmp_path, capsys):
    cache = _seed_cache(tmp_path, "v9.9.9-test")  # fresh, but not a release tag
    calls = _lookup_returning(monkeypatch, f"v{INSTALLED}")
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

    cli._maybe_notify_update("generate")

    assert len(calls) == 1
    assert capsys.readouterr().err == ""
    assert json.loads(cache.read_text(encoding="utf-8"))["latest"] == f"v{INSTALLED}"


def test_passive_notice_never_offers_a_cached_pre_release_when_offline(monkeypatch, tmp_path, capsys):
    _seed_cache(tmp_path, "v9.9.9-test")
    _lookup_returning(monkeypatch, None)  # GitLab unreachable
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

    cli._maybe_notify_update("generate")

    assert NOTICE not in capsys.readouterr().err
