"""Behavioral tests for how build.sh fails.

An interrupted build, a failing scripts/hooks.sh and a .distignore rule that
drops the entry script must each stop the build, remove the half-built copy,
leave no tarball and say why.
"""

import os
import platform
import re
import shutil
import signal
import subprocess
import time

import pytest

# A generated tool has no .venv and no uv.lock, so its startup check has no Python
# and stops the build: builds run here without it.
NO_PYTHON = {"NGARGPARSER_SKIP_STARTUP_CHECK": "1"}

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check.
    These tests expect a local build unless they set CI themselves."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


def write_hook(project_dir, body):
    (project_dir / "scripts" / "hooks.sh").write_text(body)


def default_sigint():
    """preexec_fn for a build the test stops with SIGINT, as Ctrl-C does. A suite
    started with SIGINT ignored (in the background, as in `( pytest ... & )`)
    passes that on to every child, and a bash started with SIGINT ignored can
    neither trap nor reset it: the build ran on to its end."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)


# Controls: the failure handling must not change a build that works.


def test_ordinary_build_succeeds(scaffolded_project, run_build):
    run = run_build(scaffolded_project, progress=True)

    assert "Build completed" in run.proc.stdout
    assert "src/run_demo.py" in run.members


def test_passing_hook_still_runs(scaffolded_project, run_build):
    write_hook(scaffolded_project, 'echo "hook says hi"\necho made > "$BUILD_DIR/hook-made.txt"\n')

    run = run_build(scaffolded_project, progress=True)

    assert "hook-made.txt" in run.members
    assert "hook says hi" not in run.proc.stdout + run.proc.stderr, "progress mode keeps hook output off the terminal"


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX process groups")
# HUP: a dropped ssh session.
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP], ids=["TERM", "INT", "HUP"])
def test_interrupted_build_removes_the_copy(scaffolded_project, run_build, sig):
    # A finished build first, so there is an old tarball for the next build to remove.
    run = run_build(scaffolded_project)
    started = scaffolded_project.parent / "hook-started"
    write_hook(scaffolded_project, f'touch "{started}"\nsleep 30\n')

    # Its own process group, so the signal reaches the build and the hook, as
    # Ctrl-C in a terminal does.
    proc = subprocess.Popen(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env={**os.environ, "TOOL_VERSION": run.staged.name.rpartition("-")[2]},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        preexec_fn=default_sigint,
    )
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            assert proc.poll() is None, "the build ended before the hook ran"
            assert time.monotonic() < deadline, "the hook did not start"
            time.sleep(0.05)
        os.killpg(proc.pid, sig)
        _, stderr = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    assert not run.staged.exists(), "the half-built copy must be removed"
    assert not run.tarball.exists()
    assert "Build failed" in stderr
    assert proc.returncode == 128 + sig, stderr


def test_failing_hook_shows_its_log(scaffolded_project, run_build):
    write_hook(scaffolded_project, 'for i in {1..30}; do echo "line $i"; done\necho "no route to host" >&2\nexit 3\n')

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert "ERROR: scripts/hooks.sh failed (exit 3)." in run.proc.stderr
    shown = run.proc.stderr.splitlines()
    assert "no route to host" in shown
    assert "line 30" in shown and "line 12" in shown
    assert "line 11" not in shown, "only the last 20 lines of the log are shown"
    log = (scaffolded_project / ".ngargparser" / "hooks.log").read_text()
    assert "line 1\n" in log and "no route to host" in log
    assert "Build failed" in run.proc.stderr
    assert not run.staged.exists()
    assert not run.tarball.exists()


def test_rule_that_drops_the_only_run_script_stops_before_anything_is_removed(scaffolded_project, run_build):
    # A tool without [tool.ngargparser] entry: its only src/run_*.py is the entry
    # script, checked in the preflight like a named entry, so the previous build stays.
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(
        "".join(line for line in pyproject.read_text().splitlines(True) if not line.startswith("entry ="))
    )
    first = run_build(scaffolded_project)
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\nsrc/run_*.py\n")

    run = run_build(scaffolded_project, expect_ok=False)

    assert "ERROR: src/run_demo.py is excluded by .distignore, so the tarball would have no entry script." in (
        run.proc.stderr
    )
    assert first.tarball.is_file() and first.staged.is_dir(), "the previous build stays"
    assert "Build failed" not in run.proc.stderr


def test_distignore_rule_that_drops_the_entry_script_fails(scaffolded_project, run_build):
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\nsrc/run_*.py\n")

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "src/run_demo.py is excluded by .distignore, so the tarball would have no entry script." in run.proc.stderr
    assert "Startup check skipped" not in run.proc.stdout
    assert not run.staged.exists()
    assert not run.tarball.exists()


# TOOL_NAME and TOOL_VERSION name the build copy that a failed build removes, so
# a bad value must stop the build before anything is created or removed.


def test_tool_name_outside_build_is_rejected(scaffolded_project, run_build):
    # build/../../pf-victim-tver is a folder next to the project, not in build/.
    victim = scaffolded_project.parent / "pf-victim-tver"
    victim.mkdir()
    (victim / "keep.txt").write_text("not the build's\n")
    with open(scaffolded_project / "scripts" / "core" / "build.conf", "a") as f:
        f.write('\nTOOL_NAME="../../pf-victim"\n')

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert (
        "ERROR: TOOL_NAME '../../pf-victim' is not allowed: use only letters, digits, '.', '_', '+' and '-' "
        "(not '.' or '..' alone, and not starting with '-'). It comes from scripts/core/build.conf"
    ) in run.proc.stderr
    assert sorted(p.name for p in victim.iterdir()) == ["keep.txt"]
    assert not (scaffolded_project / "build").exists()
    assert "Build failed; removing" not in run.proc.stderr


@pytest.mark.parametrize("name", [".", "..", "my tool", "a;b", "é", "-x"])
def test_bad_tool_name_is_rejected(scaffolded_project, run_build, name):
    with open(scaffolded_project / "scripts" / "core" / "build.conf", "a") as f:
        f.write(f'\nTOOL_NAME="{name}"\n')

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert f"ERROR: TOOL_NAME '{name}' is not allowed" in run.proc.stderr
    assert not (scaffolded_project / "build").exists()


@pytest.mark.parametrize("version", ["..", "1 0", "a&b", "x\\y", "v1;rm", "v1\nv2", "$HOME"])
def test_bad_tool_version_is_rejected(scaffolded_project, run_build, version):
    # Control: the allowed characters, and a '/' (made '-'), still build and
    # reach the README version stamp intact.
    run = run_build(scaffolded_project, expect_ok=False, env={"TOOL_VERSION": "feature/1.0.0+build_5-rc.1"})
    assert run.proc.returncode == 0, run.proc.stderr
    staged = scaffolded_project / "build" / "ng_demo-feature-1.0.0+build_5-rc.1"
    assert "feature-1.0.0+build_5-rc.1" in (staged / "README").read_text()
    assert (scaffolded_project / "build" / "IEDB_NG_DEMO-feature-1.0.0+build_5-rc.1.tar.gz").is_file()
    before = sorted(p.name for p in (scaffolded_project / "build").iterdir())

    run = run_build(scaffolded_project, expect_ok=False, env={"TOOL_VERSION": version})

    assert run.proc.returncode != 0
    assert f"ERROR: TOOL_VERSION '{version}' is not allowed" in run.proc.stderr
    assert "It comes from the TOOL_VERSION environment variable" in run.proc.stderr
    assert sorted(p.name for p in (scaffolded_project / "build").iterdir()) == before


# TOOL_VERSION from the environment (CI passes the git ref) wins over one in
# build.conf, which applies only when the environment does not set it.


def test_build_conf_cannot_override_the_environment_version(scaffolded_project, run_build):
    with open(scaffolded_project / "scripts" / "core" / "build.conf", "a") as f:
        f.write("\nTOOL_VERSION=9.9.9\n")

    run = run_build(scaffolded_project, progress=True)  # TOOL_VERSION=tver from the environment

    assert run.tarball.is_file() and run.staged.is_dir()
    assert not (scaffolded_project / "build" / "IEDB_NG_DEMO-9.9.9.tar.gz").exists()
    assert (
        "Warning: scripts/core/build.conf sets TOOL_VERSION=9.9.9, which is ignored: "
        "the TOOL_VERSION environment variable (tver) wins. Remove TOOL_VERSION from build.conf."
    ) in run.proc.stdout

    # Control: without TOOL_VERSION in the environment, build.conf's applies, quietly.
    outer = {k: v for k, v in os.environ.items() if k not in ("TOOL_VERSION", "CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}
    outer |= NO_PYTHON
    proc = subprocess.run(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env=outer,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert (scaffolded_project / "build" / "IEDB_NG_DEMO-9.9.9.tar.gz").is_file()
    assert "Warning" not in proc.stdout


def test_bad_version_from_build_conf_names_build_conf(scaffolded_project, run_build):
    with open(scaffolded_project / "scripts" / "core" / "build.conf", "a") as f:
        f.write('\nTOOL_VERSION="a b"\n')
    outer = {k: v for k, v in os.environ.items() if k not in ("TOOL_VERSION", "CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}
    outer |= NO_PYTHON

    proc = subprocess.run(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env=outer,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode != 0
    assert "ERROR: TOOL_VERSION 'a b' is not allowed" in proc.stderr
    assert (
        "It comes from scripts/core/build.conf, which applies only when the TOOL_VERSION environment variable is not set"
    ) in proc.stderr


# TARBALL_PREFIX starts the tarball's name, so it follows the same character
# rule as TOOL_NAME (it may be empty), checked before anything is removed.


@pytest.mark.parametrize("prefix", ["out/IEDB_", "../IEDB_", "-IEDB_", "IEDB _", "IEDB_;x"])
def test_bad_tarball_prefix_is_rejected(scaffolded_project, run_build, prefix):
    conf = scaffolded_project / "scripts" / "core" / "build.conf"
    original = conf.read_text()
    # Controls: an empty prefix falls back to IEDB_, and the allowed characters build.
    conf.write_text(original + 'TARBALL_PREFIX=""\n')
    first = run_build(scaffolded_project)
    conf.write_text(original + 'TARBALL_PREFIX="LJI.v2+x-"\n')
    run = run_build(scaffolded_project, expect_ok=False)
    assert run.proc.returncode == 0, run.proc.stderr
    assert (scaffolded_project / "build" / "LJI.v2+x-NG_DEMO-tver.tar.gz").is_file()
    before = sorted(p.name for p in (scaffolded_project / "build").iterdir())

    conf.write_text(original + f'TARBALL_PREFIX="{prefix}"\n')
    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        f"ERROR: TARBALL_PREFIX '{prefix}' is not allowed: use only letters, digits, '.', '_', '+' and '-' "
        "(not starting with '-' or '.'), or leave it empty for IEDB_. It comes from scripts/core/build.conf.\n"
    ) in run.proc.stderr
    # A preflight stop: the previous build copy and tarballs stay.
    assert sorted(p.name for p in (scaffolded_project / "build").iterdir()) == before
    assert first.staged.is_dir()
    assert "Build failed" not in run.proc.stderr


# Windows line endings in a file bash reads stop the build with the fix,
# instead of bash's "$'\r': command not found".


@pytest.mark.parametrize(
    "rel, pattern",
    [
        # The pattern the .gitattributes of a new tool has.
        ("scripts/core/build.conf", "build.conf"),
        ("scripts/get_libs.sh", "*.sh"),
        ("scripts/hooks.sh", "*.sh"),
        # The two the deploy host runs.
        ("deploy/install.sh", "*.sh"),
        ("configure", "configure"),
    ],
    ids=["build.conf", "get_libs.sh", "hooks.sh", "install.sh", "configure"],
)
def test_windows_line_endings_stop_the_build(scaffolded_project, run_build, rel, pattern):
    if rel == "scripts/hooks.sh":
        write_hook(scaffolded_project, "#!/bin/bash\nset -euo pipefail\n\necho hi\n")
    first = run_build(scaffolded_project)
    path = scaffolded_project / rel
    lf = path.read_bytes()
    path.write_bytes(lf.replace(b"\n", b"\r\n"))

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    if platform.system() == "Darwin":
        fix = f"perl -pi -e 's/\\r$//' {rel}"
    else:
        fix = f'sed -i "s/\\r$//" {rel}'
    assert run.proc.returncode == 1
    assert (
        f"ERROR: {rel} has Windows line endings (CRLF): convert it with '{fix}' "
        f"(and add '{pattern} text eol=lf' to .gitattributes)\n"
    ) in run.proc.stderr
    assert "command not found" not in run.proc.stderr
    # A preflight stop: the previous build copy and tarball stay.
    assert first.tarball.is_file() and first.staged.is_dir()
    assert "Build failed" not in run.proc.stderr

    # The command the message gives fixes the file.
    subprocess.run(["sh", "-c", fix], cwd=scaffolded_project, check=True, timeout=60)
    assert path.read_bytes() == lf
    run_build(scaffolded_project)
    # The .gitattributes line it names is the one a new tool has, and git applies it to the file.
    assert f"{pattern} text eol=lf" in (scaffolded_project / ".gitattributes").read_text().splitlines()
    subprocess.run(["git", "init", "-q"], cwd=scaffolded_project, check=True, timeout=60)
    eol = subprocess.run(
        ["git", "check-attr", "eol", "--", rel], cwd=scaffolded_project, capture_output=True, text=True, timeout=60
    ).stdout
    assert eol.strip() == f"{rel}: eol: lf"


# In progress mode a command's own error message starts on a line of its
# own, not at the end of the progress bar.


def test_raw_error_does_not_run_into_the_progress_bar(scaffolded_project):
    # A folder where the previous tarball goes, so rm -f cannot remove it once
    # the bar is drawn. (A file named build now stops the build before it.)
    (scaffolded_project / "build" / "IEDB_NG_DEMO-tver.tar.gz").mkdir(parents=True)
    outer = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}

    # stderr into stdout, as on a terminal; bytes, so a carriage return stays one.
    proc = subprocess.run(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env={**outer, "TOOL_VERSION": "tver"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
    )

    out = proc.stdout.decode()
    assert proc.returncode != 0
    match = re.search(r"(mkdir|rm): ", out)
    assert match, out
    before = out[: match.start()]
    assert "Setting up build directory" in before
    # The bar's line is ended or erased first.
    assert before.endswith(("\n", "\r\x1b[K")), repr(out[max(0, match.start() - 60) : match.end() + 40])
    assert re.search(r"(?:^|[\n\r]|\r\x1b\[K)Build failed; removing ", out)


# A build stopped by a signal printed "Build failed; removing ..." at the end of
# the progress bar's line: no error had ended it.


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX process groups")
def test_interrupted_build_does_not_run_into_the_progress_bar(scaffolded_project, tmp_path):
    started = tmp_path / "hook-started"
    write_hook(scaffolded_project, f'touch "{started}"\nsleep 30\n')
    outer = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}

    # stderr into stdout, as on a terminal; bytes, so a carriage return stays one.
    proc = subprocess.Popen(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env={**outer, "TOOL_VERSION": "tver"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=default_sigint,
    )
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            assert proc.poll() is None, "the build ended before the hook ran"
            assert time.monotonic() < deadline, "the hook did not start"
            time.sleep(0.05)
        os.killpg(proc.pid, signal.SIGINT)
        raw, _ = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    out = raw.decode()
    assert proc.returncode == 130, out
    before = out[: out.index("Build failed; removing ")]
    assert "Running build hooks" in before
    assert before.endswith(("\n", "\r\x1b[K")), repr(before[-80:])


# The build says "tool" or "tool folder" where it said "project".


def test_build_messages_say_tool_not_project(scaffolded_project, run_build):
    project_word = re.compile(r"(?<![A-Za-z_])project(?![A-Za-z_])", re.I)  # not pyproject, PROJECT_ROOT

    verbose = run_build(scaffolded_project)
    progress = run_build(scaffolded_project, progress=True)

    assert "Copying tool files" in verbose.proc.stdout
    assert "Startup check skipped: NGARGPARSER_SKIP_STARTUP_CHECK is set." in progress.proc.stdout
    no_python = run_build(scaffolded_project, expect_ok=False, env={"NGARGPARSER_SKIP_STARTUP_CHECK": "0"})
    assert "ERROR: The startup check has no Python to run the tool with" in no_python.proc.stderr
    for run in (verbose, progress, no_python):
        assert not project_word.search(run.proc.stdout + run.proc.stderr), run.proc.stdout + run.proc.stderr

    os.symlink("/etc/hostname", scaffolded_project / "hostlink")
    run = run_build(scaffolded_project, expect_ok=False)
    assert (
        "Fix these in the tool folder, scripts/get_libs.sh or scripts/hooks.sh, or keep the tool folder's links "
        "out with .distignore:"
    ) in run.proc.stderr
    assert not project_word.search(run.proc.stdout + run.proc.stderr), run.proc.stderr
    (scaffolded_project / "hostlink").unlink()

    (scaffolded_project / ".distignore").rename(scaffolded_project / "do-not-distribute.txt")
    run = run_build(scaffolded_project, expect_ok=False)
    assert "Move it to .distignore at the top of the tool folder: git mv do-not-distribute.txt .distignore." in (
        run.proc.stderr
    )
    assert not project_word.search(run.proc.stdout + run.proc.stderr), run.proc.stderr


# A TERM to the build itself (`kill <pid>`) stops a running get_libs.sh or
# hooks.sh, and whatever it started, at once instead of after it ends.


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie has ended; it waits only for its parent to collect it.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
        return bool(state.strip()) and not state.strip().startswith("Z")


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX signals")
@pytest.mark.parametrize(
    "script, progress",
    [("get_libs", True), ("get_libs", False), ("hooks", True), ("hooks", False)],
    ids=["get_libs-progress", "get_libs-verbose", "hooks-progress", "hooks-verbose"],
)
def test_kill_stops_the_running_script_at_once(scaffolded_project, run_build, tmp_path, script, progress):
    run = run_build(scaffolded_project)
    started = tmp_path / "started"
    child_pid = tmp_path / "child.pid"
    body = f'#!/bin/bash\nsleep 60 &\necho $! > "{child_pid}"\ntouch "{started}"\nwait\n'
    (scaffolded_project / "scripts" / f"{script}.sh").write_text(body)

    proc = subprocess.Popen(
        ["bash", "scripts/core/build.sh"] + (["--progress"] if progress else []),
        cwd=scaffolded_project,
        env={**os.environ, "TOOL_VERSION": "tver"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            assert proc.poll() is None, "the build ended before the script ran"
            assert time.monotonic() < deadline, "the script did not start"
            time.sleep(0.05)
        sent = time.monotonic()
        proc.send_signal(signal.SIGTERM)  # the build alone, as `kill <pid>` does
        _, stderr = proc.communicate(timeout=20)
        took = time.monotonic() - sent
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    assert proc.returncode == 143, stderr
    assert took < 10, f"the build waited {took:.1f}s for {script}.sh"
    assert "Build failed" in stderr
    assert not run.staged.exists() and not run.tarball.exists()
    pid = int(child_pid.read_text())
    deadline = time.monotonic() + 10
    while process_alive(pid):
        assert time.monotonic() < deadline, f"what {script}.sh started still runs"
        time.sleep(0.05)


# Without ps (python:3.11-slim has none), a TERM to the build stopped
# get_libs.sh or hooks.sh but not what it started. The build now reads the
# process table from /proc there.


def path_without_ps(tmp_path):
    """PATH with each folder that holds a ps replaced by a copy of it, made of
    links, without ps."""
    folders = []
    for n, folder in enumerate(os.environ["PATH"].split(os.pathsep)):
        if folder and os.path.exists(os.path.join(folder, "ps")):
            mirror = tmp_path / f"no-ps-{n}"
            mirror.mkdir()
            for name in os.listdir(folder):
                if name != "ps":
                    os.symlink(os.path.join(folder, name), mirror / name)
            folder = str(mirror)
        folders.append(folder)
    return os.pathsep.join(folders)


@pytest.mark.skipif(not os.path.exists("/proc/self/stat"), reason="needs Linux /proc")
@pytest.mark.parametrize("script", ["get_libs", "hooks"])
def test_kill_without_ps_stops_what_the_script_started(scaffolded_project, run_build, tmp_path, script):
    path = path_without_ps(tmp_path)
    assert subprocess.run(["sh", "-c", "command -v ps"], env={"PATH": path}).returncode != 0
    run = run_build(scaffolded_project, env={"PATH": path})  # control: the build works without ps
    started = tmp_path / "started"
    child_pid = tmp_path / "child.pid"
    body = f'#!/bin/bash\nsleep 60 &\necho $! > "{child_pid}"\ntouch "{started}"\nwait\n'
    (scaffolded_project / "scripts" / f"{script}.sh").write_text(body)

    proc = subprocess.Popen(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env={**os.environ, "TOOL_VERSION": "tver", "PATH": path},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            assert proc.poll() is None, "the build ended before the script ran"
            assert time.monotonic() < deadline, "the script did not start"
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        _, stderr = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    assert proc.returncode == 143, stderr
    assert not run.staged.exists()
    pid = int(child_pid.read_text())
    deadline = time.monotonic() + 10
    try:
        while process_alive(pid):
            assert time.monotonic() < deadline, f"what {script}.sh started still runs"
            time.sleep(0.05)
    finally:
        if process_alive(pid):
            os.kill(pid, signal.SIGKILL)


# Ctrl-C signals the build's whole process group: get_libs.sh's own shell
# died of it, but a job it started with '&' ignores it, and ran on after the
# build, out of the build's reach (a TERM to the build alone stopped it). A
# job started from a subshell that has ended, '( cmd & )', was out of reach
# for either script, Ctrl-C or TERM. The scripts now run in a process group of
# their own, which the build stops; without setsid (macOS) the same holds,
# through a subshell with job control on. hooks.sh keeps the build's stdin.


def path_without(tmp_path, name):
    """PATH with each folder that holds `name` replaced by links to its other programs."""
    folders = []
    for n, folder in enumerate(os.environ["PATH"].split(os.pathsep)):
        if folder and os.path.exists(os.path.join(folder, name)):
            mirror = tmp_path / f"no-{name}-{n}"
            mirror.mkdir()
            for entry in os.listdir(folder):
                if entry != name:
                    os.symlink(os.path.join(folder, entry), mirror / entry)
            folder = str(mirror)
        folders.append(folder)
    return os.pathsep.join(folders)


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX signals and process groups")
@pytest.mark.parametrize("group", ["setsid", "no-setsid"])
@pytest.mark.parametrize("script", ["get_libs", "hooks"])
def test_ctrl_c_stops_what_the_script_started(scaffolded_project, run_build, tmp_path, script, group):
    env = {} if group == "setsid" else {"PATH": path_without(tmp_path, "setsid")}
    run = run_build(scaffolded_project, env=env)
    started = tmp_path / "started"
    child_pid = tmp_path / "child.pid"
    orphan_pid = tmp_path / "orphan.pid"
    stdin_seen = tmp_path / "stdin.txt"
    body = (
        f'#!/bin/bash\nread -r line || true\necho "read: $line" > "{stdin_seen}"\n'
        f'(sleep 61 & echo $! > "{orphan_pid}")\n'
        f'sleep 60 &\necho $! > "{child_pid}"\ntouch "{started}"\nwait\n'
    )
    (scaffolded_project / "scripts" / f"{script}.sh").write_text(body)

    proc = subprocess.Popen(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env={**os.environ, "TOOL_VERSION": "tver", **env},
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        preexec_fn=default_sigint,
    )
    proc.stdin.write("from the terminal\n")
    proc.stdin.close()
    pids = []
    try:
        deadline = time.monotonic() + 60
        while not (started.exists() and child_pid.exists()):
            assert proc.poll() is None, "the build ended before the script ran"
            assert time.monotonic() < deadline, "the script did not start"
            time.sleep(0.05)
        pids = [int(child_pid.read_text()), int(orphan_pid.read_text())]
        # get_libs.sh reads from /dev/null; hooks.sh gets the build's stdin.
        assert stdin_seen.read_text() == ("read: \n" if script == "get_libs" else "read: from the terminal\n")
        os.killpg(proc.pid, signal.SIGINT)  # Ctrl-C: the whole process group
        proc.wait(timeout=20)
        stderr = proc.stderr.read()

        assert proc.returncode == 130, stderr
        assert "Build failed" in stderr
        assert not run.staged.exists() and not run.tarball.exists()
        deadline = time.monotonic() + 10
        while any(process_alive(pid) for pid in pids):
            alive = [
                "the job" if pids.index(pid) == 0 else "the job from an ended subshell"
                for pid in pids
                if process_alive(pid)
            ]
            assert time.monotonic() < deadline, f"{' and '.join(alive)} {script}.sh started still runs after Ctrl-C"
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        for pid in pids:
            if process_alive(pid):
                os.kill(pid, signal.SIGKILL)


# A TARBALL_PREFIX starting with '.' built a hidden build/.NG_... tarball, which
# the CI job's artifact pattern (build/*.tar.gz) can miss.


@pytest.mark.parametrize("prefix", [".", ".IEDB_"])
def test_hidden_tarball_prefix_is_rejected(scaffolded_project, run_build, prefix):
    conf = scaffolded_project / "scripts" / "core" / "build.conf"
    conf.write_text(conf.read_text() + f'TARBALL_PREFIX="{prefix}"\n')

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        f"ERROR: TARBALL_PREFIX '{prefix}' is not allowed: use only letters, digits, '.', '_', '+' and '-' "
        "(not starting with '-' or '.'), or leave it empty for IEDB_. It comes from scripts/core/build.conf.\n"
    ) in run.proc.stderr
    assert not run.staged.exists()
    assert not list(scaffolded_project.glob("build/.*.tar.gz")), "no hidden tarball"


# A build.conf the build cannot read was reported as one that exits.


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="root reads any file")
def test_unreadable_build_conf_says_so(scaffolded_project, run_build):
    conf = scaffolded_project / "scripts" / "core" / "build.conf"
    conf.chmod(0o000)
    try:
        run = run_build(scaffolded_project, expect_ok=False, progress=True)
    finally:
        conf.chmod(0o644)

    assert run.proc.returncode == 1
    assert "ERROR: scripts/core/build.conf cannot be read (Permission denied).\n" in run.proc.stderr
    assert "exited (code" not in run.proc.stderr
    # Control: readable again, it builds.
    run_build(scaffolded_project, progress=True)


# In make build-verbose, a failing hook ended the build with no line naming it.


def test_failing_hook_is_named_in_verbose_mode(scaffolded_project, run_build):
    write_hook(scaffolded_project, 'echo "hook says hello"\nexit 3\n')

    run = run_build(scaffolded_project, expect_ok=False)

    assert "ERROR: scripts/hooks.sh failed (exit 3).\n" in run.proc.stderr
    assert run.proc.returncode == 1
    assert "hook says hello" in run.proc.stdout, "verbose mode shows the hook's output as it runs"
    assert "hooks.log" not in run.proc.stderr
    assert "Build failed" in run.proc.stderr
    assert not run.tarball.exists()


# A job a successful get_libs.sh or hooks.sh started with '&' ran on after
# the build, writing into libs/ or the build copy whenever it liked. The
# build now stops what is left in the script's process group once the script
# exits, and says so.


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX signals and process groups")
@pytest.mark.parametrize("group", ["setsid", "no-setsid"])
@pytest.mark.parametrize("script", ["get_libs", "hooks"])
def test_a_job_the_script_leaves_running_is_stopped(scaffolded_project, run_build, tmp_path, script, group):
    env = {} if group == "setsid" else {"PATH": path_without(tmp_path, "setsid")}
    left = tmp_path / "left.pid"
    # Its output goes elsewhere, so the build's own output ends with the build.
    body = f'#!/bin/bash\nsleep 120 >/dev/null 2>&1 </dev/null &\necho $! > "{left}"\nmkdir -p dep\n'
    (scaffolded_project / "scripts" / f"{script}.sh").write_text(body)
    pid = None
    try:
        started = time.monotonic()
        run = run_build(scaffolded_project, progress=True, env=env)
        pid = int(left.read_text())

        assert not process_alive(pid), f"what {script}.sh left running runs on after the build"
        assert f"Notice: scripts/{script}.sh left background jobs running; the build stopped them.\n" in (
            run.proc.stdout
        )
        assert time.monotonic() - started < 60
    finally:
        if pid is not None and process_alive(pid):
            os.kill(pid, signal.SIGKILL)

    # Control: a script that leaves nothing running gets no notice.
    (scaffolded_project / "scripts" / f"{script}.sh").write_text("#!/bin/bash\nsleep 0.1 &\nwait\nmkdir -p dep\n# 2\n")
    run = run_build(scaffolded_project, progress=True, env=env)
    assert "left background jobs running" not in run.proc.stdout
