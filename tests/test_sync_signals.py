"""`cli sync` and a stop signal near its end: held while the run cleans up, then delivered.

A SIGINT while a finished sync deleted its saved copies printed "completed successfully",
then a KeyboardInterrupt traceback (SIGTERM: `_SyncStopSignal: 15`), and left the marker,
so make build refused and the next sync gave cp -a commands that undid the finished sync.
The write of the hash record was not held like every other write: a SIGINT right after it
undid the sync but kept the new record, and the next dry run reported local edits that
were not there. SIGQUIT (Ctrl-\\) was not caught: the process died mid-sync.
"""

import hashlib
import json
import os
import signal
import subprocess
import sys

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
OLDER_FILE = "src/core/app_env.py"
HASHES = ".ngargparser/framework-hashes.json"
PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))
needs_signals = pytest.mark.skipif(os.name != "posix", reason="needs POSIX signals")

# Each child sync: no core file for SIGQUIT, the plain version stamp (as the scaffold wrote it), and
# a hook that sends the signal; then a real sync without the test drive.
CHILD = """
import os, resource, signal, sys
from argparse import Namespace
from ngargparser import cli
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
cli._local_version_label = lambda: None
{hook}
sys.exit(cli.sync_command(Namespace(upgrade=False, dev=False, ref="latest", dry_run=False, backup=True, verify=False)))
"""

BEFORE_DISCARD = """
real = cli._SyncSnapshot.discard
def discard(self):
    os.kill(os.getpid(), signal.{name})
    return real(self)
cli._SyncSnapshot.discard = discard
"""

AFTER_HASH_RECORD = """
real = cli._write_framework_hashes
def write(*args, **kwargs):
    real(*args, **kwargs)
    os.kill(os.getpid(), signal.SIGINT)
cli._write_framework_hashes = write
"""

AFTER_FIRST_WRITE = """
real = cli._sync_file
def sync_file(src, dst, **kwargs):
    action = real(src, dst, **kwargs)
    if dst == {core!r}:
        os.kill(os.getpid(), signal.{name})
    return action
cli._sync_file = sync_file
"""


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def run_child(project, hook):
    env = {
        **os.environ,
        "PYTHONPATH": PACKAGE_ROOT,
        "NGARGPARSER_NO_SELF_UPGRADE": "1",
        "NGARGPARSER_NO_UPDATE_CHECK": "1",
    }
    return subprocess.run(
        [sys.executable, "-c", CHILD.format(hook=hook)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def tree_state(root, skip=()):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.relative_to(root).parts[0] not in skip
    }


def edit(project, path):
    target = project / path
    target.write_text(target.read_text() + "# a local edit that sync will overwrite\n")


def make_older(project, path):
    """`path` as an older framework version left it: changed, and recorded as written."""
    target = project / path
    target.write_text(target.read_text() + "# from an older framework version\n")
    record = json.loads((project / HASHES).read_text())
    record[path] = hashlib.sha256(target.read_bytes()).hexdigest()
    (project / HASHES).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


# --- 1. A stop signal while a finished sync cleans up ---------------------------------------------


@needs_signals
@pytest.mark.parametrize("name", ["SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT"])
def test_a_stop_while_a_finished_sync_cleans_up_keeps_it_finished(
    scaffolded_project, sync_args, strip_ansi, capsys, name
):
    project = scaffolded_project
    edit(project, CORE_FILE)

    run = run_child(project, BEFORE_DISCARD.format(name=name))

    signum = getattr(signal, name)
    out, err = strip_ansi(run.stdout), strip_ansi(run.stderr)
    assert run.returncode == 128 + signum, (run.returncode, out, err)
    assert "✓ Framework synchronization completed successfully!" in out
    assert "Traceback" not in err and "_SyncStopSignal" not in err
    assert err.endswith(f"✗ Sync interrupted ({name}) after it ended: the result above stands.\n"), err
    # The sync stands, and nothing says it did not finish.
    assert (project / CORE_FILE).read_bytes() == (cli.NGPARSER_DIR / "NGArgumentParser.py").read_bytes()
    assert not (project / ".ngargparser" / "sync-in-progress").exists()
    assert cli.sync_command(sync_args(verify=False)) == 0
    captured = capsys.readouterr()
    assert "did not finish" not in captured.out + captured.err
    assert "No files needed updating" in strip_ansi(captured.out)


# --- 2. A stop right after the hash record is written ---------------------------------------------


@needs_signals
def test_a_stop_right_after_the_hash_record_puts_the_record_back_too(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    make_older(project, OLDER_FILE)
    record_before = (project / HASHES).read_bytes()
    before = tree_state(project, skip=(".ngargparser",))

    run = run_child(project, AFTER_HASH_RECORD)

    err = strip_ansi(run.stderr)
    assert run.returncode == 130, (run.stdout, err)
    assert (project / HASHES).read_bytes() == record_before
    assert tree_state(project, skip=(".ngargparser",)) == before
    assert "Every file was restored to its state before the sync." in err
    assert "Cancelled; nothing changed." in err

    # The record still matches the older file: the next dry run sees no local edit in it.
    assert cli.sync_command(sync_args(verify=False, dry_run=True)) == 0
    out = strip_ansi(capsys.readouterr().out)
    assert f"Would update {OLDER_FILE}" in out
    assert "Would replace local edits" not in out


# --- 3. SIGQUIT (Ctrl-\) undoes the sync like SIGTERM ------------------------------------------------


@needs_signals
def test_sigquit_undoes_the_sync_like_sigterm(scaffolded_project, strip_ansi):
    project = scaffolded_project
    edit(project, CORE_FILE)
    edit(project, OLDER_FILE)
    before = tree_state(project, skip=(".ngargparser",))

    run = run_child(project, AFTER_FIRST_WRITE.format(core=CORE_FILE, name="SIGQUIT"))

    err = strip_ansi(run.stderr)
    assert run.returncode == 128 + signal.SIGQUIT, (run.returncode, run.stdout, err)
    assert "✗ Sync interrupted (SIGQUIT)." in err
    assert "Every file was restored to its state before the sync." in err
    assert "Traceback" not in err
    assert tree_state(project, skip=(".ngargparser",)) == before
    assert not (project / ".ngargparser" / "sync-in-progress").exists()
