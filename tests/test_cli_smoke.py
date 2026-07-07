import subprocess
import sys

ENV_OVERRIDES = {
    "CI": "1",
    "NGARGPARSER_NO_UPDATE_CHECK": "1",
    "NGARGPARSER_NO_SELF_UPGRADE": "1",
}


def _run(args):
    import os

    env = {**os.environ, **ENV_OVERRIDES}
    return subprocess.run(
        [sys.executable, "-m", "ngargparser.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_version_flag():
    result = _run(["--version"])
    assert result.returncode == 0
    assert "cli" in result.stdout.lower()


def test_help_lists_subcommands():
    result = _run(["--help"])
    assert result.returncode == 0
    for command in ("generate", "deps", "sync", "upgrade"):
        assert command in result.stdout
