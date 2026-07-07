import re
from argparse import Namespace

import pytest

from ngargparser import cli

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def strip_ansi():
    """Return a function that removes ANSI color codes from output."""
    return lambda text: ANSI_RE.sub("", text)


@pytest.fixture
def sync_args():
    """Return a factory building a sync_command Namespace that never triggers
    the self-upgrade/re-exec path (upgrade=False)."""

    def _make(**overrides):
        defaults = dict(upgrade=False, dev=False, ref="latest", dry_run=False, backup=True)
        defaults.update(overrides)
        return Namespace(**defaults)

    return _make


@pytest.fixture
def in_tmp_dir(tmp_path, monkeypatch):
    """Run the test from an empty temp directory with `uv` hidden from PATH
    lookups, so scaffolding skips the slow `uv lock` subprocess."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda _cmd: None)
    return tmp_path


@pytest.fixture
def scaffolded_project(in_tmp_dir, monkeypatch):
    """A freshly generated project named 'demo', with the CWD inside it
    (where `cli sync` and `cli deps` expect to run)."""
    rc = cli.startapp_command(Namespace(project_name="demo"))
    assert not rc
    project_dir = in_tmp_dir / "demo"
    monkeypatch.chdir(project_dir)
    return project_dir
