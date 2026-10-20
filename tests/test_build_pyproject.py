"""Behavioral tests for the pyproject.toml check in build.sh's preflight.

A pyproject.toml that does not parse (a merge conflict left in it, a typo)
fails `uv sync` at deploy time, so the build stops before anything is
removed. The Python that runs the lock comparison reads the file first, and a
merge conflict marker stops the build at once. For any other error, uv
decides when it is on the PATH, because it reads newer TOML than Python's
tomllib: only its "Failed to parse" stops the build. Without such a Python,
uv's message is the only signal; without uv, Python's verdict stands.
"""

import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)

MESSAGE = "ERROR: pyproject.toml does not parse ({}): fix it, then build again\n"

# What uv 0.11 prints for TYPO below (`uv lock --check --offline`, exit 2).
UV_TYPO = (
    "warning: Failed to parse `pyproject.toml` during settings discovery:\n"
    '  TOML parse error at line 9, column 5\n    |\n  9 |     "python-dotenv",\n    |     ^\n'
    "  missing comma between array elements, expected `,`\n\n"
    "error: Failed to parse: `pyproject.toml`\n"
    '  Caused by: TOML parse error at line 9, column 5\n  |\n9 |     "python-dotenv",\n  |     ^\n'
    "missing comma between array elements, expected `,`\n"
)
# What uv prints once it has read pyproject.toml in a tool without uv.lock.
UV_NO_LOCK = (
    "Using CPython 3.11.15\nerror: Unable to find lockfile at `uv.lock`, but `--check` was provided. "
    "To create a lockfile, run `uv lock` or `uv sync` without the flag.\n"
)
# An unknown [tool.uv] key: uv only warns about it and goes on.
UV_UNKNOWN_KEY = (
    "warning: Failed to parse `pyproject.toml` during settings discovery:\n"
    "  TOML parse error at line 7, column 1\n    |\n  7 | bogus-key = 1\n    | ^^^^^^^^^\n"
    "  unknown field `bogus-key`\n\n" + UV_NO_LOCK
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check.
    These tests expect a local build."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


@pytest.fixture(autouse=True)
def _test_python_first_on_path(monkeypatch):
    """The build reads TOML with the first python3 on PATH. Under `uv run` that
    is the .venv's; under `.venv/bin/python -m pytest` it may be one that reads
    no TOML (a pyenv shim, an older system Python), and these tests skipped.
    The test's own interpreter's folder comes first, so both launchers run them."""
    monkeypatch.setenv("PATH", os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""))


def find_program(name, path):
    """shutil.which without shutil: the scaffold fixture patches shutil.which."""
    for d in path.split(os.pathsep):
        candidate = os.path.join(d, name)
        if d and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def path_without_uv():
    """PATH with every directory holding a `uv` left out, or None when that
    would also hide bash, git or a python3 that reads TOML."""
    keep = [d for d in os.environ["PATH"].split(os.pathsep) if d and not os.path.exists(os.path.join(d, "uv"))]
    path = os.pathsep.join(keep)
    if find_program("bash", path) is None or find_program("git", path) is None or not toml_python(path):
        return None
    return path


def toml_python(path):
    """True when the build finds a python3 that reads TOML on `path`."""
    python = find_program("python3", path)
    if python is None:
        return False
    probe = "try:\n    import tomllib\nexcept ImportError:\n    import tomli\n"
    return subprocess.run([python, "-I", "-c", probe], capture_output=True, timeout=60).returncode == 0


def conflict(project_dir):
    """Leave a merge conflict in pyproject.toml's dependencies; returns the
    line number of its first marker."""
    pyproject = project_dir / "pyproject.toml"
    text = pyproject.read_text()
    assert 'dependencies = [\n    "python-dotenv",\n]' in text
    text = text.replace(
        'dependencies = [\n    "python-dotenv",\n',
        'dependencies = [\n<<<<<<< HEAD\n    "numpy",\n=======\n    "scipy",\n>>>>>>> other\n    "python-dotenv",\n',
        1,
    )
    pyproject.write_text(text)
    return text.splitlines().index("<<<<<<< HEAD") + 1


def typo(project_dir):
    """A missing comma between two dependencies."""
    pyproject = project_dir / "pyproject.toml"
    text = pyproject.read_text()
    pyproject.write_text(
        text.replace(
            'dependencies = [\n    "python-dotenv",\n', 'dependencies = [\n    "numpy"\n    "python-dotenv",\n', 1
        )
    )


NEWER_TOML = '\n[tool.example]\ntable = {\n    key = "an inline table over several lines (TOML 1.1)",\n}\n'


def stand_ins(tmp_path, name, *, python=None, uv=None, path=None):
    """PATH (by default the test's PATH) with a stand-in python3 and/or uv
    first, kept in tmp_path/name. python is what the stand-in python3 prints
    for the pyproject check ("ok", or "unparsable\\n<why>"), or "none" for a
    python3 that cannot read TOML. uv is (output, exit code); every call to it
    is logged."""
    bin_dir = tmp_path / name
    bin_dir.mkdir(exist_ok=True)
    log = bin_dir / "uv-calls.log"
    if python is not None:
        if python == "none":
            body = "exit 1\n"
        else:
            (bin_dir / "python.out").write_text(python + "\n")
            # The probe ends in 'import tomli'; the pyproject check passes "pyproject" last.
            body = (
                f'for last; do :; done\nif [ "$last" = pyproject ]; then cat "{bin_dir / "python.out"}"; fi\nexit 0\n'
            )
        (bin_dir / "python3").write_text("#!/bin/sh\n" + body)
        (bin_dir / "python3").chmod(0o755)
    if uv is not None:
        output, code = uv
        (bin_dir / "uv.out").write_text(output)
        (bin_dir / "uv").write_text(f'#!/bin/sh\necho "$*" >> "{log}"\ncat "{bin_dir / "uv.out"}" >&2\nexit {code}\n')
        (bin_dir / "uv").chmod(0o755)
    return f"{bin_dir}{os.pathsep}{path or os.environ['PATH']}", log


def assert_preflight_stop(first, run):
    """Nothing was removed: the previous build copy and tarball stay."""
    assert run.proc.returncode == 1
    assert first.tarball.is_file() and first.staged.is_dir()
    assert "Build failed" not in run.proc.stderr


@pytest.mark.parametrize("lock", [False, True], ids=["no-uv-lock", "uv-lock"])
def test_merge_conflict_in_pyproject_stops_the_build(scaffolded_project, run_build, tmp_path, lock):
    path = path_without_uv()
    if path is None:
        pytest.skip("cannot hide uv here, or no python3 that reads TOML")
    log = None
    if lock:
        # With uv on the PATH and a uv.lock, a marker still stops the build
        # before uv runs.
        path, log = stand_ins(tmp_path, "uv", uv=("Resolved 2 packages in 1ms\n", 0), path=path)
        (scaffolded_project / "uv.lock").write_text("version = 1\n")
    first = run_build(scaffolded_project, env={"PATH": path})
    calls = log.read_text() if log else ""
    line = conflict(scaffolded_project)

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={"PATH": path})

    assert MESSAGE.format(f"a merge conflict marker at line {line}") in run.proc.stderr
    assert_preflight_stop(first, run)
    assert "Lock check" not in run.proc.stdout
    if log:
        assert calls == "lock --check --offline\n", "the first build ran the lock check"
        assert log.read_text() == calls, "decided before uv runs"


def test_typo_without_uv_stops_with_pythons_reason(scaffolded_project, run_build):
    path = path_without_uv()
    if path is None:
        pytest.skip("cannot hide uv here, or no python3 that reads TOML")
    first = run_build(scaffolded_project, env={"PATH": path})
    typo(scaffolded_project)

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={"PATH": path})

    # The TOML reader's own words, with where it stopped first.
    assert "ERROR: pyproject.toml does not parse (line 9, column " in run.proc.stderr, run.proc.stderr
    assert "): fix it, then build again\n" in run.proc.stderr
    assert_preflight_stop(first, run)


def test_uv_has_the_last_word_on_errors_python_finds(scaffolded_project, run_build, tmp_path):
    # A Python whose reader is older than uv's, such as tomllib (TOML 1.0)
    # against an inline table over several lines (TOML 1.1).
    why = "line 26, column 5: Invalid initial character for a key part"
    first = run_build(scaffolded_project, env={"PATH": stand_ins(tmp_path, "reads", python="ok")[0]})
    with open(scaffolded_project / "pyproject.toml", "a") as f:
        f.write(NEWER_TOML)

    # uv cannot parse it either: the build stops with Python's reason.
    path, log = stand_ins(tmp_path, "older", python="unparsable\n" + why, uv=(UV_TYPO, 2))
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={"PATH": path})
    assert MESSAGE.format(why) in run.proc.stderr
    assert_preflight_stop(first, run)
    assert log.read_text() == "lock --check --offline\n"

    # uv reads it (a result, no uv.lock, or only a warning about a setting): the build goes on.
    for output, code in (("Resolved 2 packages in 1ms\n", 0), (UV_NO_LOCK, 2), (UV_UNKNOWN_KEY, 2)):
        log.unlink()
        path, log = stand_ins(tmp_path, "older", python="unparsable\n" + why, uv=(output, code))
        run = run_build(scaffolded_project, progress=True, env={"PATH": path})
        assert "pyproject.toml does not parse" not in run.proc.stderr, output
        assert log.read_text() == "lock --check --offline\n"


@pytest.mark.parametrize("damage", ["typo", "conflict"])
def test_without_a_toml_python_uv_decides(scaffolded_project, run_build, tmp_path, damage):
    # Control: uv getting past pyproject.toml lets the build go on.
    path, _ = stand_ins(tmp_path, "no-toml", python="none", uv=(UV_NO_LOCK, 2))
    first = run_build(scaffolded_project, env={"PATH": path})

    if damage == "typo":
        typo(scaffolded_project)
        reason = "TOML parse error at line 9, column 5: missing comma between array elements, expected `,`"
        output = UV_TYPO
    else:
        reason = f"a merge conflict marker at line {conflict(scaffolded_project)}"
        output = UV_TYPO.replace("line 9, column 5", "line 8, column 1")
    path, _ = stand_ins(tmp_path, "no-toml", python="none", uv=(output, 2))
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={"PATH": path})

    assert MESSAGE.format(reason) in run.proc.stderr
    assert_preflight_stop(first, run)


def test_byte_order_mark_is_not_an_error(scaffolded_project, run_build):
    # uv skips a UTF-8 byte order mark, so the check does too.
    path = path_without_uv()
    if path is None:
        pytest.skip("cannot hide uv here, or no python3 that reads TOML")
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_bytes(b"\xef\xbb\xbf" + pyproject.read_bytes())
    run = run_build(scaffolded_project, env={"PATH": path})
    assert "pyproject.toml does not parse" not in run.proc.stderr

    # Control: the same file with a conflict in it stops the build.
    line = conflict(scaffolded_project)
    run = run_build(scaffolded_project, expect_ok=False, env={"PATH": path})
    assert MESSAGE.format(f"a merge conflict marker at line {line}") in run.proc.stderr


def test_real_uv_decides_on_errors_python_finds(scaffolded_project, run_build, tmp_path):
    uv = find_program("uv", os.environ["PATH"])
    if uv is None:
        pytest.skip("uv is not installed")
    # A Python whose reader rejects the file, as tomllib (TOML 1.0) does.
    why = "line 26, column 5: Invalid initial character for a key part"
    path, _ = stand_ins(tmp_path, "older", python="unparsable\n" + why)
    env = {"PATH": path}
    pyproject = scaffolded_project / "pyproject.toml"
    original = pyproject.read_text()

    # uv reads newer TOML (an inline table over several lines): the build goes on.
    pyproject.write_text(original + NEWER_TOML)
    first = run_build(scaffolded_project, progress=True, env=env)
    assert "pyproject.toml does not parse" not in first.proc.stderr

    # A real error: uv's own "Failed to parse" stops the build.
    pyproject.write_text(original)
    typo(scaffolded_project)
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)
    assert MESSAGE.format(why) in run.proc.stderr
    assert_preflight_stop(first, run)
