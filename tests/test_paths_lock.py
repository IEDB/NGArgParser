"""./configure and .ngargparser/paths.lock, the lock it shares with cli deps.

./configure made the lock file with the umask applied (cli deps makes it 0666),
and went on without the lock, silently, when it could not open it: with a
chmod 000 lock file, 15 of 30 racing `./configure --x-path` runs were lost. A
plain ./configure took no lock at all, so one that read paths.py just before a
flag run changed it wrote the old value to .env after that run.
"""

import os
import re
import subprocess
import sys
import time

import pytest
from test_configure_env import env_values, hold_paths_lock, make_tool_dir, run_configure

from ngargparser import cli

pytestmark = pytest.mark.skipif(os.name != "posix", reason="needs fcntl")

needs_permission_errors = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")

LOCK = os.path.join(".ngargparser", "paths.lock")
CANNOT_OPEN = (
    "✗ Cannot open .ngargparser/paths.lock (Permission denied): ask its owner to run "
    "chmod a+rw .ngargparser/paths.lock. Nothing was changed."
)


def configure_with_umask(project, umask, *args):
    return subprocess.run(
        [sys.executable, "src/core/configure.py", *args],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
        preexec_fn=lambda: os.umask(umask),
    )


def test_a_new_lock_file_is_readable_and_writable_for_everyone(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    lock = scaffolded_project / LOCK
    lock.unlink(missing_ok=True)

    result = configure_with_umask(scaffolded_project, 0o077, f"--pepx-path={make_tool_dir(tmp_path, 'pepx')}")

    assert result.returncode == 0, result.stdout + result.stderr
    assert lock.stat().st_mode & 0o777 == 0o666
    # Control: a lock file that is there keeps the mode it has.
    lock.chmod(0o644)
    assert configure_with_umask(scaffolded_project, 0o022).returncode == 0
    assert lock.stat().st_mode & 0o777 == 0o644


@needs_permission_errors
def test_a_lock_file_that_cannot_be_opened_stops_the_run_with_one_line(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    pepx = make_tool_dir(tmp_path, "pepx")
    lock = scaffolded_project / LOCK
    lock.parent.mkdir(exist_ok=True)
    lock.touch()
    lock.chmod(0o000)
    paths_before = (scaffolded_project / "paths.py").read_text()
    env_before = (scaffolded_project / ".env").read_text()
    try:
        flag_run = run_configure(scaffolded_project, f"--pepx-path={pepx}", expect_success=False)
        plain_run = run_configure(scaffolded_project, expect_success=False)
    finally:
        lock.chmod(0o666)

    for result in (flag_run, plain_run):
        assert (result.returncode, result.stderr) == (1, "")
        assert result.stdout.splitlines() == [CANNOT_OPEN]
    assert (scaffolded_project / "paths.py").read_text() == paths_before
    assert (scaffolded_project / ".env").read_text() == env_before
    assert not (scaffolded_project / "paths.py.bak").exists()
    # Control: readable again, the same run goes through.
    run_configure(scaffolded_project, f"--pepx-path={pepx}")
    assert f"pepx_path='{pepx}'" in (scaffolded_project / "paths.py").read_text()


@needs_permission_errors
def test_a_lock_file_that_cannot_be_made_in_a_writable_tool_folder_stops_the_run(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    pepx = make_tool_dir(tmp_path, "pepx")
    state = scaffolded_project / ".ngargparser"
    state.mkdir(exist_ok=True)
    (state / "paths.lock").unlink(missing_ok=True)
    paths_before = (scaffolded_project / "paths.py").read_text()
    state.chmod(0o555)
    try:
        result = run_configure(scaffolded_project, f"--pepx-path={pepx}", expect_success=False)
    finally:
        state.chmod(0o755)

    assert (result.returncode, result.stderr) == (1, "")
    assert result.stdout.splitlines() == [
        "✗ Cannot create .ngargparser/paths.lock (Permission denied): .ngargparser is read-only for you; "
        "run chmod u+rwx .ngargparser. Nothing was changed."
    ]
    assert (scaffolded_project / "paths.py").read_text() == paths_before


@needs_permission_errors
def test_racing_flag_runs_with_an_unopenable_lock_lose_no_change_silently(scaffolded_project, tmp_path):
    names = [f"dep{i}" for i in range(12)]
    cli.add_deps_to_paths("paths.py", names)
    run_configure(scaffolded_project, *(f"--{name}-path={make_tool_dir(tmp_path, name)}" for name in names))
    lock = scaffolded_project / LOCK
    lock.chmod(0o000)
    try:
        runs = [
            subprocess.Popen(
                [sys.executable, "src/core/configure.py", f"--{name}-module=mod/new"],
                cwd=scaffolded_project,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for name in names
        ]
        results = [(run.wait(timeout=120), run.communicate()) for run in runs]
    finally:
        lock.chmod(0o666)

    # Every run stops with nothing changed: none goes on without the lock and loses a change.
    for returncode, (out, _err) in results:
        assert (returncode, out.splitlines()[-1:]) == (1, [CANNOT_OPEN]), out
    assert "mod/new" not in (scaffolded_project / "paths.py").read_text()


def test_a_plain_run_waits_for_the_paths_lock(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    run_configure(scaffolded_project, f"--pepx-path={make_tool_dir(tmp_path, 'pepx')}")
    held = hold_paths_lock(scaffolded_project)
    try:
        run = subprocess.Popen(
            [sys.executable, "src/core/configure.py"],
            cwd=scaffolded_project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(1.5)
        assert run.poll() is None, run.communicate()  # still waiting
    finally:
        held.close()
    out, err = run.communicate(timeout=60)

    assert run.returncode == 0, out + err


# A plain run that read paths.py, then took a while (configure.py's load_config
# is made to sleep here), while a flag run changed pepx_path.
SLOW_PLAIN_RUN = """\
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location("configure_slow", "src/core/configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)
load_config = configure.load_config
def slow_load_config(path):
    config = load_config(path)
    open("read-paths-py", "w").close()
    time.sleep(2)
    return config
configure.load_config = slow_load_config
sys.argv = ["src/core/configure.py"]
configure.main()
"""


def test_a_plain_run_never_writes_a_value_a_flag_run_changed_meanwhile(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["p1"])
    old, fresh = make_tool_dir(tmp_path, "old"), make_tool_dir(tmp_path, "fresh")
    run_configure(scaffolded_project, f"--p1-path={old}")
    (scaffolded_project / "slow_plain_run.py").write_text(SLOW_PLAIN_RUN)
    marker = scaffolded_project / "read-paths-py"

    plain = subprocess.Popen(
        [sys.executable, "slow_plain_run.py"],
        cwd=scaffolded_project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.exists(), plain.communicate()
    flag_run = run_configure(scaffolded_project, f"--p1-path={fresh}")
    out, err = plain.communicate(timeout=60)

    assert plain.returncode == 0, out + err
    assert f"p1_path='{fresh}'" in (scaffolded_project / "paths.py").read_text()
    assert env_values(scaffolded_project)["P1_PATH"] == str(fresh), flag_run.stdout


# ./configure and cli deps go on without the lock in the same cases: only where
# the tool folder cannot be written. Each case runs `cli deps add` and
# `./configure --<dep>-path`, and both must stop with the same lock line, or
# both go on (to their own error about paths.py in a read-only folder).

CLI = "import sys\nfrom ngargparser.cli import main\nsys.argv = ['cli'] + sys.argv[1:]\nsys.exit(main())\n"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def lock_lines(result):
    """The lines of a run's output that name the lock: [] when the run went on."""
    text = ANSI.sub("", result.stdout + result.stderr)
    return [line for line in text.splitlines() if "paths.lock" in line]


@needs_permission_errors
def test_configure_and_cli_deps_go_on_without_the_lock_in_the_same_cases(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["alpha"])
    run_configure(scaffolded_project, f"--alpha-path={make_tool_dir(tmp_path, 'alpha')}")
    state = scaffolded_project / ".ngargparser"
    lock = state / "paths.lock"
    package_root = os.path.dirname(os.path.dirname(cli.__file__))
    env = {**os.environ, "PYTHONPATH": package_root, "NGARGPARSER_NO_UPDATE_CHECK": "1"}
    cannot_open = [CANNOT_OPEN]
    cannot_create = [
        "✗ Cannot create .ngargparser/paths.lock (Permission denied): .ngargparser is read-only for you; "
        "run chmod u+rwx .ngargparser. Nothing was changed."
    ]
    expected = {  # (tool folder writable, .ngargparser writable, lock file): the lock lines
        (True, True, None): [],
        (True, True, 0o000): cannot_open,
        (True, True, 0o666): [],
        (True, False, None): cannot_create,
        (True, False, 0o000): cannot_open,
        (True, False, 0o666): [],
        (False, True, None): [],
        (False, True, 0o000): [],  # a read-only tool folder: paths.py cannot be written anyway
        (False, True, 0o666): [],
        (False, False, None): [],
        (False, False, 0o000): [],
        (False, False, 0o666): [],
    }
    seen = {}
    for number, (tool_writable, state_writable, lock_mode) in enumerate(expected):
        if lock.exists():
            lock.chmod(0o666)
            lock.unlink()
        if lock_mode is not None:
            lock.touch()
            lock.chmod(lock_mode)
        state.chmod(0o755 if state_writable else 0o555)
        scaffolded_project.chmod(0o755 if tool_writable else 0o555)
        try:
            deps = subprocess.run(
                [sys.executable, "-c", CLI, "deps", "add", f"dep{number}"],
                cwd=scaffolded_project,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            configure = run_configure(
                scaffolded_project, f"--alpha-path={tmp_path / f'alpha{number}'}", expect_success=False
            )
        finally:
            scaffolded_project.chmod(0o755)
            state.chmod(0o755)
        seen[(tool_writable, state_writable, lock_mode)] = (lock_lines(deps), lock_lines(configure))

    assert seen == {case: (lines, lines) for case, lines in expected.items()}
