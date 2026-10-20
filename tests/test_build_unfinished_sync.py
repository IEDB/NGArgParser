"""A build in a tool folder where a cli sync did not finish.

A cli sync keeps the files it may change, as they were before it, in
.ngargparser/sync-in-progress/<time>Z-<pid>/ with a marker beside it, until it
ends; one whose undo failed leaves them in .ngargparser/failed-restore-<time>/,
which its marker names. A build there built the half-synced tool. The
preflight now stops, as cli sync does, and names the folder.
"""

import os
import shutil

import pytest

from ngargparser import cli

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


def stopped(folder):
    return f"ERROR: a cli sync did not finish here ({folder}/): run cli sync to see what to do, then build again.\n"


def test_a_sync_that_did_not_finish_stops_the_build(scaffolded_project, run_build):
    first = run_build(scaffolded_project)
    # What a sync killed with kill -9 leaves: cli sync's own pre-sync copy and its marker.
    snapshot = cli._SyncSnapshot(["src/core/app_env.py"])
    folder = os.path.relpath(snapshot._store, scaffolded_project)
    assert folder.startswith(os.path.join(".ngargparser", "sync-in-progress", ""))

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert stopped(folder) in run.proc.stderr
    # A preflight stop: the previous build copy and tarball stay.
    assert first.tarball.is_file() and first.staged.is_dir()
    assert "Build failed" not in run.proc.stderr

    # A restore that failed: the files sit in failed-restore-<time>/, which the marker names.
    kept = os.path.relpath(snapshot.keep(), scaffolded_project)
    assert kept.startswith(os.path.join(".ngargparser", "failed-restore-"))

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert stopped(kept) in run.proc.stderr

    # Control: once that folder is gone (copied back, then deleted, as cli sync
    # says), the marker left beside it does not stop the build.
    shutil.rmtree(scaffolded_project / kept)
    assert list((scaffolded_project / ".ngargparser" / "sync-in-progress").glob("*.json"))
    run_build(scaffolded_project)


def test_a_marker_that_does_not_parse_names_the_folder_beside_it(scaffolded_project, run_build):
    store = scaffolded_project / ".ngargparser" / "sync-in-progress" / "20261018T101400.805606Z-4242"
    (store / "src" / "core").mkdir(parents=True)
    store.with_name(store.name + ".json").write_text('{"pid": 4242, "files"')  # cut short

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert stopped(".ngargparser/sync-in-progress/20261018T101400.805606Z-4242") in run.proc.stderr
