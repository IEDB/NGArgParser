"""Tests for the generated Makefile.

make build and make build-verbose run scripts/core/build.sh with bash, so a
build.sh that lost its exec bit (a copy, an unzip, a Windows checkout) still
builds.
"""

import os
import shutil
import signal
import subprocess
import time

import pytest

# A generated tool has no .venv and no uv.lock, so its startup check has no Python
# and stops the build: builds run here without it.
NO_PYTHON = {"NGARGPARSER_SKIP_STARTUP_CHECK": "1"}

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or shutil.which("make") is None,
    reason="Makefile tests require bash, git and make",
)


@pytest.mark.parametrize("target", ["build", "build-verbose"])
def test_make_runs_build_sh_without_its_exec_bit(scaffolded_project, target):
    build_sh = scaffolded_project / "scripts" / "core" / "build.sh"
    build_sh.chmod(0o644)
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")} | NO_PYTHON

    proc = subprocess.run(
        ["make", target],
        cwd=scaffolded_project,
        env={**env, "TOOL_VERSION": "tver"},
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Permission denied" not in proc.stderr
    assert (scaffolded_project / "build" / "IEDB_NG_DEMO-tver.tar.gz").is_file()


# make clean took no lock, so it deleted the build copy a running build was
# writing. It now runs build.sh --clean, which takes the build lock: it waits
# for nothing, and refuses while a build or a cli sync holds the lock.


def test_make_clean_refuses_while_a_build_runs(scaffolded_project, tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")} | NO_PYTHON
    env["TOOL_VERSION"] = "tver"
    started = tmp_path / "hook-started"
    release = tmp_path / "hook-release"
    (scaffolded_project / "scripts" / "hooks.sh").write_text(
        f'touch "{started}"\nfor i in $(seq 600); do [ -e "{release}" ] && break; sleep 0.1; done\n'
    )
    build = subprocess.Popen(
        ["make", "build"],
        cwd=scaffolded_project,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            assert build.poll() is None, "the build ended before its hook ran"
            assert time.monotonic() < deadline, "the hook did not start"
            time.sleep(0.05)

        clean = subprocess.run(
            ["make", "clean"], cwd=scaffolded_project, env=env, capture_output=True, text=True, timeout=60
        )

        assert clean.returncode != 0
        assert "ERROR: Another build or a cli sync is running in this folder (pid " in clean.stderr
        assert "Build directory cleaned." not in clean.stdout
        assert (scaffolded_project / "build" / "ng_demo-tver").is_dir(), "the running build's copy stays"
        release.touch()
        out, err = build.communicate(timeout=120)
    finally:
        if build.poll() is None:
            os.killpg(build.pid, signal.SIGKILL)
            build.wait()

    assert build.returncode == 0, out + err
    assert (scaffolded_project / "build" / "IEDB_NG_DEMO-tver.tar.gz").is_file()

    # With no build running, make clean removes build/, read-only files and all.
    clean = subprocess.run(
        ["make", "clean"], cwd=scaffolded_project, env=env, capture_output=True, text=True, timeout=60
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "Build directory cleaned." in clean.stdout
    assert not (scaffolded_project / "build").exists()


# A build.sh saved with Windows line endings failed in bash with
# "$'\r': command not found" and no hint: make now checks it first.


@pytest.mark.parametrize("target", ["build", "build-verbose", "clean"])
def test_make_stops_on_a_build_sh_with_windows_line_endings(scaffolded_project, target):
    build_sh = scaffolded_project / "scripts" / "core" / "build.sh"
    build_sh.write_bytes(build_sh.read_bytes().replace(b"\n", b"\r\n"))
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")} | NO_PYTHON

    proc = subprocess.run(
        ["make", target],
        cwd=scaffolded_project,
        env={**env, "TOOL_VERSION": "tver"},
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert proc.returncode != 0
    fix = (
        "perl -pi -e 's/\\r$//' scripts/core/build.sh"
        if os.uname().sysname == "Darwin"
        else 'sed -i "s/\\r$//" scripts/core/build.sh'
    )
    assert (
        f"ERROR: scripts/core/build.sh has Windows line endings (CRLF): convert it with '{fix}' "
        "(and add '*.sh text eol=lf' to .gitattributes)\n"
    ) in proc.stderr
    assert "command not found" not in proc.stderr
    # The fix the message names works.
    subprocess.run(["bash", "-c", fix], cwd=scaffolded_project, check=True)
    proc = subprocess.run(
        ["make", target],
        cwd=scaffolded_project,
        env={**env, "TOOL_VERSION": "tver"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
