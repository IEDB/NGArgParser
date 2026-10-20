"""Problems of the build machine are no longer blamed on the deploy host.

`UV_LOCKED=1 make build` failed with "'uv sync --frozen --no-dev' fails in the
build copy (exit 2), so deploy/install.sh would fail on the deploy host too:
error: the argument UV_LOCKED (environment variable) cannot be used with
--frozen". The build now leaves out the uv settings that pick another lock
mode or another folder (UV_LOCKED, UV_FROZEN, UV_PROJECT, UV_WORKING_DIR) for
the uv commands it runs: UV_FROZEN also made 'uv lock --check' pass a uv.lock
that is out of date. A .ngargparser/startup-venv uv may not write (a shared
tool folder, another user's venv) is reported as a problem of this machine.
"""

import os
import shutil
import subprocess
import sys

import pytest
from test_build_lock import STALE_MESSAGE, find_program
from test_build_startup import dev_venv

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)

OVERRIDES = {"UV_LOCKED": "1", "UV_FROZEN": "1", "UV_PROJECT": "/nonexistent", "UV_WORKING_DIR": "/nonexistent"}


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)
    for name in OVERRIDES:
        monkeypatch.delenv(name, raising=False)


def strict_uv(tmp_path, *, lock_stale=False, sync_output="", sync_code=0):
    """A stand-in uv that fails as uv 0.11 does when one of OVERRIDES reaches it.
    Its `lock --check` says the lock is out of date when `lock_stale`, unless
    UV_FROZEN is set (then uv only checks that the lock parses, and exits 0).
    Its `sync` prints `sync_output` and exits with `sync_code`, or makes the
    environment (the test interpreter) when that is 0."""
    bin_dir = tmp_path / "strict-uv"
    bin_dir.mkdir(parents=True)
    log = bin_dir / "calls.log"
    (bin_dir / "sync-out.txt").write_text(sync_output)
    stale = (
        'echo "The lockfile at \\`uv.lock\\` needs to be updated, but \\`--check\\` was provided." >&2; exit 1'
        if lock_stale
        else "exit 0"
    )
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        f'echo "$* | UV_LOCKED=$UV_LOCKED UV_FROZEN=$UV_FROZEN UV_PROJECT=$UV_PROJECT UV_WORKING_DIR=$UV_WORKING_DIR" >> "{log}"\n'
        'if [ "$1" = lock ]; then\n'
        '    if [ -n "$UV_FROZEN" ]; then echo "warning: The lockfile at \\`uv.lock\\` was only checked for validity" >&2; exit 0; fi\n'
        f"    {stale}\n"
        "fi\n"
        '[ "$1" = sync ] || exit 0\n'
        'if [ -n "$UV_LOCKED" ]; then\n'
        '    echo "error: the argument \\`UV_LOCKED\\` (environment variable) cannot be used with \\`--frozen\\`" >&2\n'
        "    exit 2\n"
        "fi\n"
        'if [ -n "$UV_PROJECT$UV_WORKING_DIR" ]; then\n'
        '    echo "error: No \\`pyproject.toml\\` found in current directory or any parent directory" >&2\n'
        "    exit 2\n"
        "fi\n"
        f'cat "{bin_dir / "sync-out.txt"}" >&2\n'
        f"[ {sync_code} -eq 0 ] || exit {sync_code}\n"
        'mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
        # Never through a link (a real uv's venv): that would overwrite the interpreter.
        'rm -f "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        f'printf \'#!/bin/sh\\nexec "%s" "$@"\\n\' "{sys.executable}" > "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        'chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
    )
    (bin_dir / "uv").chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, log


@pytest.mark.parametrize("name", sorted(OVERRIDES))
def test_uv_settings_of_the_build_machine_do_not_reach_the_startup_sync(scaffolded_project, run_build, tmp_path, name):
    dev_venv(scaffolded_project, tmp_path)
    env, log = strict_uv(tmp_path)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")

    run = run_build(scaffolded_project, progress=True, env={**env, name: OVERRIDES[name]})

    assert "deploy/install.sh would fail on the deploy host" not in run.proc.stderr
    assert "Startup check environment" not in run.proc.stdout  # progress mode
    assert "the startup check uses the tool's .venv" not in run.proc.stdout
    calls = [line for line in log.read_text().splitlines() if line.startswith(("sync ", "lock "))]
    assert calls[0].startswith("lock --check --offline | "), calls
    assert calls[1].startswith("sync --frozen --no-dev --offline"), calls
    assert all(f"{name}= " in line or line.endswith(f"{name}=") for line in calls), calls


def test_uv_frozen_does_not_let_a_stale_lock_through(scaffolded_project, run_build, tmp_path):
    env, log = strict_uv(tmp_path, lock_stale=True)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={**env, "UV_FROZEN": "1"})

    assert run.proc.returncode == 1
    assert STALE_MESSAGE in run.proc.stderr
    # Not the `uv run` that finds a Python for the lock comparison when
    # python3 on the PATH reads no TOML (as under .venv/bin/python -m pytest).
    calls = [line for line in log.read_text().splitlines() if line.startswith(("sync ", "lock "))]
    assert calls[0].startswith("lock --check --offline | "), calls


# What uv 0.11.14 prints when the kept startup venv has a folder it may not
# write, as in a tool folder another user built first.
UV_PERMISSION = (
    "Using CPython 3.11.15\n"
    "error: failed to remove file `{venv}/lib/python3.11/site-packages/../../../bin/dotenv`: "
    "Permission denied (os error 13)\n"
)


def test_an_unwritable_startup_venv_is_a_problem_of_this_machine(scaffolded_project, run_build, tmp_path):
    dev_venv(scaffolded_project, tmp_path)
    venv = scaffolded_project / ".ngargparser" / "startup-venv"
    env, _log = strict_uv(tmp_path, sync_output=UV_PERMISSION.format(venv=venv), sync_code=2)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode == 1
    assert (
        "ERROR: uv cannot write .ngargparser/startup-venv, where the startup check makes its environment "
        "(Permission denied). This is a problem of this machine, not of the tool or the deploy: remove "
        ".ngargparser/startup-venv (or ask its owner to), then build again.\n"
        f"    error: failed to remove file `{venv}/lib/"
    ) in run.proc.stderr
    assert "deploy host" not in run.proc.stderr

    # Control: any other uv error still names the deploy.
    env, _log = strict_uv(tmp_path / "other", sync_output="error: some other failure\n", sync_code=2)
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)
    assert "so deploy/install.sh would fail on the deploy host too" in run.proc.stderr


# With the real uv, offline from its cache. Opt-in, as the other real_uv
# tests: set NGARGPARSER_REAL_UV=1 (the CI job pytest-real-uv does).


@pytest.mark.skipif(os.environ.get("NGARGPARSER_REAL_UV") != "1", reason="opt-in: set NGARGPARSER_REAL_UV=1")
def test_real_uv_locked_setting_does_not_fail_the_build(scaffolded_project, run_build):
    uv = find_program("uv", os.environ["PATH"])
    if uv is None:
        pytest.skip("needs uv")
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "CI")}
    env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    for args in (["lock"], ["sync", "--frozen"]):
        proc = subprocess.run([uv, *args], cwd=scaffolded_project, env=env, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            pytest.skip(f"uv {args[0]} cannot run offline here: {proc.stderr.strip().splitlines()[-1:]}")

    run = run_build(scaffolded_project, env={"UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", "UV_LOCKED": "1"})

    assert "Startup check environment: .ngargparser/startup-venv (uv sync --frozen --no-dev)." in run.proc.stdout
    assert "Startup check passed." in run.proc.stdout
