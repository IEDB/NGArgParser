"""File names that are not UTF-8, and the progress bar on a terminal only.

In a UTF-8 locale (C.UTF-8 here), awk, sed and grep do not match a byte that
is not UTF-8, such as the \\xe9 of a name saved in Latin-1: an excluded
data/cl\\xe9.key shipped although the build listed it as excluded, a folder
vendor/d\\xe9p/ moved to the top of the tarball when vendor/ had an exclusion
inside, and a folder rule ('cache*/') missed such a folder. The build now
runs them in the C locale, whatever the caller's.

The progress bar (a carriage return, an escape code and block characters per
step) was drawn into every log: `CI=true make build | cat -v` showed
`^M^[[K[...`. It is drawn only when stdout is a terminal and NO_COLOR is unset
or empty; else each step is a plain line.
"""

import os
import pty
import shutil
import subprocess
import tarfile

import pytest
from test_build_lock import find_program

# A generated tool has no .venv and no uv.lock, so its startup check has no Python
# and stops the build: builds run here without it.
NO_PYTHON = {"NGARGPARSER_SKIP_STARTUP_CHECK": "1"}

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)

VERSION = "tver"
TOOL_DIR = f"ng_demo-{VERSION}"
TARBALL = f"IEDB_NG_DEMO-{VERSION}.tar.gz"


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)


def build(project_dir, *args, env=None):
    """build.sh with `args`, its output as bytes (names that are not UTF-8 stay as they are)."""
    return subprocess.run(
        ["bash", "scripts/core/build.sh", *args],
        cwd=project_dir,
        env={**os.environ, **NO_PYTHON, "TOOL_VERSION": VERSION, **(env or {})},
        capture_output=True,
        timeout=300,
    )


def members(project_dir):
    """The tarball's paths below the tool folder, as bytes."""
    with tarfile.open(project_dir / "build" / TARBALL) as tf:
        return {os.fsencode(m.name)[len(TOOL_DIR) + 1 :] for m in tf.getmembers() if m.name != TOOL_DIR}


def awk_on_path(tmp_path, name):
    """PATH with `awk` being the awk named `name`, or None when it is not installed.
    find_program, not shutil.which: the scaffold fixture hides every program from it."""
    program = find_program(name, os.environ["PATH"])
    if program is None:
        return None
    shim = tmp_path / f"awk-{name}"
    shim.mkdir()
    (shim / "awk").symlink_to(program)
    return f"{shim}{os.pathsep}{os.environ['PATH']}"


def utf8_locale():
    """A UTF-8 locale this machine has, or None."""
    names = subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.split()
    for name in ("C.UTF-8", "C.utf8", "en_US.UTF-8", "en_US.utf8"):
        if name in names:
            return name
    return None


@pytest.mark.parametrize("awk", ["gawk", "mawk"])
def test_names_that_are_not_utf8_follow_the_rules(scaffolded_project, tmp_path, awk):
    path = awk_on_path(tmp_path, awk)
    if path is None:
        pytest.skip(f"{awk} is not installed")
    locale = utf8_locale() if find_program("locale", os.environ["PATH"]) else None
    if locale is None:
        pytest.skip("no UTF-8 locale here")
    root = os.fsencode(scaffolded_project)

    def write(rel, text=b"x\n"):
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(text)

    write(b"data/ok.txt", b"KEEP\n")
    write(b"data/cl\xe9.key")
    write(b"data/x\xff.key")
    # vendor/ has an exclusion inside, so the build walks it.
    write(b"vendor/a.key")
    write(b"vendor/d\xe9p/f.txt")
    write(b"vendor/\xffdir/g.txt")
    # A folder rule: git judges the folder as a folder only if the build
    # makes it in its rules repository.
    write(b"cache\xe9/c.bin")
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\n*.key\ncache*/\n")

    proc = build(scaffolded_project, "--progress", env={"PATH": path, "LC_ALL": locale, "LANG": locale})

    assert proc.returncode == 0, (proc.stdout + proc.stderr).decode("utf-8", "replace")
    shipped = members(scaffolded_project)
    assert b"data/ok.txt" in shipped
    for name in (b"data/cl\xe9.key", b"data/x\xff.key", b"vendor/a.key", b"cache\xe9", b"cache\xe9/c.bin"):
        assert name not in shipped, name
    # Folders stay where they are.
    assert {b"vendor/d\xe9p/f.txt", b"vendor/\xffdir/g.txt"} <= shipped
    assert not {b"d\xe9p", b"d\xe9p/f.txt", b"\xffdir", b"\xffdir/g.txt"} & shipped
    # The report lists what does not ship, and grep warns about no binary file.
    assert b"\n  data/cl\xe9.key\n  data/x\xff.key\n" in proc.stdout
    assert b"\n  cache\xe9/\n" in proc.stdout
    assert b"binary file" not in proc.stderr

    # Control: in the C locale the same tree built the same way before.
    proc = build(scaffolded_project, "--progress", env={"PATH": path, "LC_ALL": "C", "LANG": "C"})
    assert proc.returncode == 0
    assert members(scaffolded_project) == shipped


def test_a_log_gets_plain_step_lines(scaffolded_project):
    # Not a terminal: no carriage return, no escape code, no bar.
    proc = build(scaffolded_project, "--progress")

    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert b"\r" not in out and b"\x1b[" not in out
    assert "█".encode() not in out
    lines = proc.stdout.decode().splitlines()
    assert " 10% - Setting up build directory" in lines
    assert "100% - Making build files read-only" in lines
    assert f"Build completed: build/{TARBALL}" in lines


def run_on_a_terminal(project_dir, env=None):
    """build.sh --progress with stdout and stderr on a pseudo-terminal: (exit code, what it printed)."""
    leader, follower = pty.openpty()
    try:
        proc = subprocess.Popen(
            ["bash", "scripts/core/build.sh", "--progress"],
            cwd=project_dir,
            env={**os.environ, **NO_PYTHON, "TOOL_VERSION": VERSION, **(env or {})},
            stdin=subprocess.DEVNULL,
            stdout=follower,
            stderr=follower,
        )
        os.close(follower)
        follower = None
        out = b""
        while True:
            try:
                chunk = os.read(leader, 65536)
            except OSError:  # EIO: the build closed the terminal
                break
            if not chunk:
                break
            out += chunk
        return proc.wait(timeout=300), out
    finally:
        os.close(leader)
        if follower is not None:
            os.close(follower)


def test_a_terminal_gets_the_bar_unless_no_color_is_set(scaffolded_project):
    code, out = run_on_a_terminal(scaffolded_project)

    assert code == 0, out
    assert b"\r\x1b[K[" in out and "█".encode() in out
    assert b"] 100% - Making build files read-only" in out

    for value, bar in (("1", False), ("", True)):
        code, out = run_on_a_terminal(scaffolded_project, env={"NO_COLOR": value})
        assert code == 0, out
        assert (b"\x1b[" in out) is bar, value
        assert ("█".encode() in out) is bar, value
        if not bar:
            assert b"100% - Making build files read-only\r\n" in out
