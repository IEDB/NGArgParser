"""`cli sync`: two stop signals at once, links out of the tool for every path sync
writes, the entry rules of the test drive, a restore that does not finish, and the
test drive of a fresh clone.

"Nothing changed" is checked against a hash of every file in the tool, as in
test_sync_safety.py.
"""

import hashlib
import os
import shutil
import signal
import stat
import sys
import threading

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
PARSER_FILE = "src/DemoArgumentParser.py"


def tree_state(root):
    """Every file (content hash + mode) and every directory under `root`."""
    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative:
            continue
        if path.is_dir():
            dirs.add(relative)
        else:
            files[relative] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    return files, dirs


def make_stale(project):
    core = project / CORE_FILE
    pristine = core.read_text()
    core.write_text(pristine + "\n# a local edit that sync will overwrite\n")
    return pristine


def add_unlisted_dependency(project):
    """Make the tool rely on a hand-added helper in a framework file, so the sync breaks it."""
    core = project / CORE_FILE
    core.write_text(core.read_text() + '\n\ndef local_helper():\n    return "added by hand"\n')
    parser = project / PARSER_FILE
    parser.write_text("from core.NGArgumentParser import local_helper  # noqa: F401\n" + parser.read_text())


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


# --- Two stop signals at once -----------------------------------------------------------


@pytest.mark.skipif(not hasattr(signal, "pthread_sigmask"), reason="needs POSIX signal masks")
@pytest.mark.parametrize(
    "first, rc, said",
    [(signal.SIGHUP, 129, "Sync interrupted (SIGHUP)."), (signal.SIGTERM, 130, "Sync interrupted.")],
    ids=["SIGHUP+SIGINT", "SIGTERM+SIGINT"],
)
def test_two_stop_signals_at_once_undo_the_sync_once(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch, first, rc, said
):
    # A closed terminal can send SIGHUP and SIGINT together. The second one used to
    # raise inside the undo path: a KeyboardInterrupt traceback, and the new files stayed.
    make_stale(runnable_project)
    before = tree_state(runnable_project)
    real_check, calls = cli._smoke_check, []

    def both_at_once():
        calls.append(1)
        if len(calls) == 2:  # the "after" run, once the new framework files are in place
            both = {first, signal.SIGINT}
            signal.pthread_sigmask(signal.SIG_BLOCK, both)
            # To this thread, where both wait: os.kill lets the kernel hand a signal to any thread
            # that does not block it (pytest-xdist runs one), which then arrives before the other.
            main = threading.main_thread().ident
            signal.pthread_kill(main, first)
            signal.pthread_kill(main, signal.SIGINT)
            signal.pthread_sigmask(signal.SIG_UNBLOCK, both)  # both arrive now, 0 ms apart
        return real_check()

    monkeypatch.setattr(cli, "_smoke_check", both_at_once)
    try:
        got = cli.sync_command(sync_args())
    except KeyboardInterrupt:
        pytest.fail("a KeyboardInterrupt escaped the sync: the second signal stopped the undo")

    out = strip_ansi(capsys.readouterr().err)
    assert got == rc  # 128 + the signal that came first (Python handles SIGINT before SIGTERM)
    assert said in out and "Every file was restored" in out and "Cancelled; nothing changed." in out
    assert tree_state(runnable_project) == before
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler  # Ctrl-C works as before afterwards


# --- Links out of the tool, for every path sync writes -----------------------------------


def link_out(project, outside, name):
    """Move `name` (a file or folder of the tool) to `outside` and leave a link to it."""
    target = outside / name.replace("/", "_")
    shutil.move(str(project / name), str(target))
    (project / name).symlink_to(target)
    return target


LINKED = {
    # name: (prepare the copy outside so sync would write it, the path the refusal names).
    # A README.md that is a link is not here: sync skips its badge (test_sync_files.py).
    ".gitignore": (lambda t: t.write_text("node_modules/\n"), ".gitignore"),
    "pyproject.toml": (lambda t: cli.write_scaffold_version(str(t), "0.0.1"), "pyproject.toml"),
    ".ngargparser": (lambda t: None, ".ngargparser/framework-hashes.json"),
    "deploy": (lambda t: (t / "install.sh").unlink(), "deploy/install.sh"),
}


@pytest.mark.parametrize("name", list(LINKED))
def test_a_file_sync_writes_that_leads_outside_the_tool_is_refused(
    runnable_project, sync_args, strip_ansi, capsys, tmp_path_factory, name
):
    outside = tmp_path_factory.mktemp("shared")
    target = link_out(runnable_project, outside, name)
    prepare, named = LINKED[name]
    prepare(target)
    make_stale(runnable_project)
    before, outside_before = tree_state(runnable_project), tree_state(outside)

    for args in (sync_args(dry_run=True), sync_args()):
        assert cli.sync_command(args) == 1
        out = strip_ansi(capsys.readouterr().err)
        assert "✗ Sync stopped: these files sync would write are links (or sit in a linked folder) that lead" in out
        assert f"  {named} -> " in out
        assert "Nothing was changed. Replace each link with a copy of what it points to" in out
        assert tree_state(runnable_project) == before
        assert tree_state(outside) == outside_before


# --- The test drive follows the build's entry rules ----------------------------------------


@pytest.fixture
def outside_script(tmp_path_factory):
    """A script outside the tool that records each time it runs."""
    folder = tmp_path_factory.mktemp("elsewhere")
    ran = folder / "ran.log"
    script = folder / "outside_entry.py"
    script.write_text(f"import sys\nopen({str(ran)!r}, 'a').write(' '.join(sys.argv) + '\\n')\n")
    return script, ran


def set_entry(project, line):
    pyproject = project / "pyproject.toml"
    text = pyproject.read_text()
    start = text.index("\nentry = ") + 1
    end = text.index("\n", start)
    pyproject.write_text(text[:start] + line + text[end:])


@pytest.mark.parametrize(
    "entry, problem",
    [
        (
            "{outside_rel}",
            'entry = "{outside_rel}" under [tool.ngargparser] in pyproject.toml must be a path inside the tool folder, such as src/run_<name>.py',
        ),
        (
            "{outside_abs}",
            'entry = "{outside_abs}" under [tool.ngargparser] in pyproject.toml must be a path inside the tool folder, such as src/run_<name>.py',
        ),
        ("src", 'entry = "src" under [tool.ngargparser] in pyproject.toml names a folder, not a file'),
        (
            "src/run_missing.py",
            'entry = "src/run_missing.py" under [tool.ngargparser] in pyproject.toml names a file that does not exist',
        ),
        (
            None,
            'entry under [tool.ngargparser] in pyproject.toml must be a quoted path, such as entry = "src/run_<name>.py"',
        ),
    ],
    ids=["outside, relative", "outside, absolute", "a folder", "missing", "not a string"],
)
def test_an_entry_the_build_refuses_stops_a_sync_that_changes_framework_files(
    runnable_project, sync_args, strip_ansi, capsys, outside_script, entry, problem
):
    script, ran = outside_script
    names = {"outside_rel": os.path.relpath(script, runnable_project), "outside_abs": str(script)}
    if entry is None:
        set_entry(runnable_project, "entry = 5")
    else:
        set_entry(runnable_project, f'entry = "{entry.format(**names)}"')
    problem = problem.format(**names)
    make_stale(runnable_project)
    before = tree_state(runnable_project)

    assert cli.sync_command(sync_args()) == 1
    out = strip_ansi("".join(capsys.readouterr()))
    assert (
        f"✗ Sync cannot check that the tool still starts after the update: {problem}. Fix it, then run cli sync "
        "again (or cli sync --no-verify to update without the check). Nothing was changed."
    ) in out
    assert tree_state(runnable_project) == before

    assert cli.sync_command(sync_args(dry_run=True)) == 1
    out = strip_ansi("".join(capsys.readouterr()))
    assert (
        f"✗ A real run would stop here and change nothing. Sync cannot check that the tool still starts after the update: {problem}."
        in out
    )
    assert tree_state(runnable_project) == before
    assert not ran.exists()  # the script outside the tool never ran

    # Control: the entry the build accepts, written with ./, is the one the test drive runs.
    set_entry(runnable_project, 'entry = "./src/run_demo.py"')
    assert cli.sync_command(sync_args()) == 0
    assert "Verified: every help screen that worked before the sync still works (4 of 4)" in strip_ansi(
        "".join(capsys.readouterr())
    )


# --- A restore that does not finish -------------------------------------------------------


def test_a_restore_that_does_not_finish_says_to_copy_back_not_to_skip_the_checks(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch
):
    add_unlisted_dependency(runnable_project)

    def cannot_restore(self, backup_root=None):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(cli._SyncSnapshot, "restore", cannot_restore)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert "Sync rolled back" in out and "Restore did not finish (read-only file system)" in out
    # The commands that finish it by hand, at once (each file sits under its path in the tool).
    assert "  To finish it by hand, copy back the files it changed" in out
    assert "    cp -a .ngargparser/failed-restore-" in out
    assert "  Do not run cli sync --no-verify on a half-restored tool." in out
    assert "sync without these checks: cli sync --no-verify" not in out
    kept = out.split("The files before the sync are in ", 1)[1].split("; ", 1)[0]
    shutil.rmtree(kept)


# --- The test drive of a fresh clone ------------------------------------------------------


def test_a_fresh_clone_with_a_venv_is_verified(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, fake_venv, tmp_path_factory
):
    # A clone has no .env (./configure writes it on each host). The test drive points
    # APP_ROOT at the tool, so the tool still starts and the update is checked.
    for key in ("APP_ROOT", "APP_NAME", "APP_VENV", "APP_ENTRY"):
        monkeypatch.delenv(key, raising=False)
    shutil.move(str(scaffolded_project / ".env"), str(tmp_path_factory.mktemp("moved") / ".env"))
    fake_venv(scaffolded_project)
    pristine = make_stale(scaffolded_project)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "Verified: every help screen that worked before the sync still works (4 of 4)." in out
    assert (scaffolded_project / CORE_FILE).read_text() == pristine


def test_a_hangup_the_caller_ignores_does_not_stop_the_sync():
    # nohup sets SIGHUP to SIG_IGN; closing the terminal must not undo that sync.
    import signal

    before = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        previous = cli._catch_stop_signals()
        try:
            assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
            assert signal.SIGHUP not in previous
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    finally:
        signal.signal(signal.SIGHUP, before)
