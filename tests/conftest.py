import os
import re
import subprocess
import sys
import tarfile
from argparse import Namespace
from pathlib import Path
from typing import NamedTuple

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


BUILD_VERSION = "tver"
BUILD_TOOL_DIR = f"ng_demo-{BUILD_VERSION}"
BUILD_TARBALL = f"IEDB_NG_DEMO-{BUILD_VERSION}.tar.gz"


class BuildRun(NamedTuple):
    proc: subprocess.CompletedProcess
    staged: Path  # build/<tool>-<version>/
    tarball: Path  # may not exist after a failed build
    members: dict  # {path relative to the tool dir: TarInfo}; empty when no tarball


@pytest.fixture
def run_build():
    """Return a function that runs scripts/core/build.sh in a project the way
    `make build` does, with TOOL_VERSION=tver. By default it asserts the build
    succeeded and produced the tarball; pass expect_ok=False to inspect a failure."""

    def _run(project_dir, *, expect_ok=True, progress=False, env=None):
        cmd = ["bash", "scripts/core/build.sh"] + (["--progress"] if progress else [])
        # A local build unless the test says otherwise: GitLab sets CI, and in CI
        # a build without .venv fails its startup check.
        outer = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}
        extra = dict(env or {})
        # A generated project has no .venv and no uv.lock, so the startup check
        # has no Python and stops the build: skipped, unless the test gives the
        # project a .venv (fake_venv) or sets CI or the switch itself.
        no_venv = not (project_dir / ".venv" / "bin" / "python").exists()
        if no_venv and not {"CI", "NGARGPARSER_SKIP_STARTUP_CHECK"} & extra.keys():
            extra["NGARGPARSER_SKIP_STARTUP_CHECK"] = "1"
        proc = subprocess.run(
            cmd,
            cwd=project_dir,
            env={**outer, "TOOL_VERSION": BUILD_VERSION, **extra},
            capture_output=True,
            text=True,
            timeout=120,
        )
        tarball = project_dir / "build" / BUILD_TARBALL
        if expect_ok:
            assert proc.returncode == 0, (
                f"build.sh failed (rc={proc.returncode})\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
            )
            assert tarball.exists(), "tarball not produced"
        members = {}
        if tarball.exists():
            with tarfile.open(tarball) as tf:
                members = {m.name[len(BUILD_TOOL_DIR) + 1 :]: m for m in tf.getmembers() if m.name != BUILD_TOOL_DIR}
        return BuildRun(proc, project_dir / "build" / BUILD_TOOL_DIR, tarball, members)

    return _run


@pytest.fixture
def fake_venv():
    """Return a function that gives a project a .venv/bin/python which runs the
    test interpreter (so python-dotenv is importable), for the build's startup
    check. A wrapper script, not a symlink: Python finds its venv through the
    path it was started from."""

    def _make(project_dir):
        python = project_dir / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        # A uv-made .venv/bin/python links to a shared interpreter: replace the
        # link, never write through it.
        if python.is_symlink():
            python.unlink()
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        python.chmod(0o755)
        return python

    return _make
