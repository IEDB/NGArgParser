"""Behavioral tests for the uv.lock check in build.sh's preflight.

When the project has a uv.lock, the build compares the dependency lists in
pyproject.toml with the ones uv.lock records (no uv, no network, no package
cache needed), and runs `uv lock --check --offline` when uv is on PATH. A
stale lock stops the build; uv's verdict wins when it can run. A uv check that
cannot run (no cached package, no matching Python, an older uv) leaves the
verdict to the comparison, or prints a notice. Without uv.lock, nothing
changes. Most tests use a stand-in `uv` so they need no network and no cache.
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

STALE_MESSAGE = "ERROR: uv.lock is out of date with pyproject.toml: run 'uv lock' and commit uv.lock"


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


@pytest.fixture
def fake_uv(tmp_path):
    """Return a function that puts a stand-in `uv` first on PATH. It logs its
    arguments, prints what real uv prints for `mode`, and exits as real uv does
    (checked against uv 0.11): 1 for a stale lock, 2 for other errors. The
    test interpreter's folder comes next, so the build finds a python3 that
    reads TOML there, as under `uv run pytest` (the .venv's bin/ is first on
    PATH then), and does not run its lock comparison through the stand-in."""

    def _make(mode):
        bin_dir = tmp_path / f"fake-uv-{mode}"
        bin_dir.mkdir()
        log = bin_dir / "calls.log"
        out = {
            "ok": "Resolved 2 packages in 1ms\n",
            "stale": "Resolved 3 packages in 2ms\n"
            "The lockfile at `uv.lock` needs to be updated, but `--check` was provided. "
            "To update the lockfile, run `uv lock`.\n",
            "error": "Using CPython 3.11.15\nerror: Failed to parse `uv.lock`\n  Caused by: TOML parse error\n",
            # A uv that cannot run here at all.
            "nopython": "Using CPython 3.11.15\nerror: No interpreter found for Python >=3.14 in managed installations\n",
            # What uv prints for a dependency added by hand that is not in its cache.
            "nocache": "Using CPython 3.11.15\n  x No solution found when resolving dependencies:\n"
            "  `-> Because numpy was not found in the cache and your project depends on numpy>=1.26, we can\n"
            "      conclude that your project's requirements are unsatisfiable.\n",
        }[mode]
        code = {"ok": 0, "stale": 1, "error": 2, "nopython": 2, "nocache": 1}[mode]
        (bin_dir / "out.txt").write_text(out)
        uv = bin_dir / "uv"
        uv.write_text(f'#!/bin/sh\necho "$PWD $*" >> "{log}"\ncat "{bin_dir / "out.txt"}" >&2\nexit {code}\n')
        uv.chmod(0o755)
        python_dir = os.path.dirname(sys.executable)
        return {"PATH": os.pathsep.join([str(bin_dir), python_dir, os.environ["PATH"]])}, log

    return _make


def find_program(name, path):
    """shutil.which without shutil: the scaffold fixture patches shutil.which
    to hide uv from cli, and that patch reaches every caller."""
    for d in path.split(os.pathsep):
        candidate = os.path.join(d, name)
        if d and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def path_without_uv():
    """PATH with every directory holding a `uv` left out, or None when that
    would also hide bash or git."""
    keep = [d for d in os.environ["PATH"].split(os.pathsep) if d and not os.path.exists(os.path.join(d, "uv"))]
    path = os.pathsep.join(keep)
    if find_program("bash", path) is None or find_program("git", path) is None:
        return None
    return path


def test_stale_lock_stops_the_build(scaffolded_project, run_build, fake_uv):
    env, log = fake_uv("stale")
    # Controls: no uv.lock means no check, even with uv on PATH ...
    run_build(scaffolded_project, env=env)
    assert not log.exists()
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    # ... and no uv on PATH means no check, silently.
    path = path_without_uv()
    if path is not None:
        run = run_build(scaffolded_project, progress=True, env={"PATH": path})
        assert "uv.lock" not in run.proc.stdout + run.proc.stderr

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode != 0
    assert STALE_MESSAGE in run.proc.stderr
    assert log.read_text() == f"{scaffolded_project} lock --check --offline\n"
    # A preflight stop: the previous build copy and tarball stay.
    assert run.staged.is_dir()
    assert run.tarball.is_file()
    assert "Build failed" not in run.proc.stderr


def test_up_to_date_lock_builds_quietly(scaffolded_project, run_build, fake_uv):
    env, log = fake_uv("ok")
    (scaffolded_project / "uv.lock").write_text("version = 1\n")

    run = run_build(scaffolded_project, progress=True, env=env)

    assert log.read_text() == f"{scaffolded_project} lock --check --offline\n"
    assert "uv.lock" not in run.proc.stdout + run.proc.stderr
    assert "Lock check" not in run.proc.stdout


def test_check_that_cannot_run_is_a_notice(scaffolded_project, run_build, fake_uv):
    # Not uv's "Failed to parse `uv.lock`", which stops the build (see
    # test_lock_uv_rejects_stops_the_build).
    env, _ = fake_uv("nopython")
    (scaffolded_project / "uv.lock").write_text("version = 1\n")

    run = run_build(scaffolded_project, progress=True, env=env)

    assert (
        "Lock check skipped: 'uv lock --check --offline' could not run (exit 2):\n"
        "    error: No interpreter found for Python >=3.14 in managed installations\n"
    ) in run.proc.stdout
    assert "Using CPython" not in run.proc.stdout


def test_real_uv_finds_a_stale_lock(scaffolded_project, run_build):
    uv = find_program("uv", os.environ["PATH"])
    if uv is None:
        pytest.skip("uv is not installed")
    pyproject = scaffolded_project / "pyproject.toml"
    # No dependencies, so the lock resolves offline from an empty cache.
    pyproject.write_text('[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.8"\ndependencies = []\n')
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    lock = subprocess.run(
        [uv, "lock", "--offline"], cwd=scaffolded_project, env=env, capture_output=True, text=True, timeout=120
    )
    if lock.returncode != 0:
        pytest.skip(f"uv lock --offline failed here: {lock.stderr.strip()[-200:]}")
    run_build(scaffolded_project)

    pyproject.write_text(pyproject.read_text().replace('version = "0.1.0"', 'version = "0.2.0"'))
    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert STALE_MESSAGE in run.proc.stderr


# The comparison of pyproject.toml with uv.lock, which needs no uv and no cache.

PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "python-dotenv",
]

[dependency-groups]
test = ["pytest"]

[tool.ngargparser]
app_name = "demo"
"""

# A uv.lock (uv 0.11) for PYPROJECT, cut to the parts the comparison reads
# and the package entries without which uv rejects it ("Failed to parse").
LOCK = """\
version = 1
revision = 3
requires-python = ">=3.11"

[[package]]
name = "demo"
version = "0.1.0"
source = { virtual = "." }
dependencies = [
    { name = "python-dotenv" },
]

[package.dev-dependencies]
test = [
    { name = "pytest" },
]

[package.metadata]
requires-dist = [{ name = "python-dotenv" }]

[package.metadata.requires-dev]
test = [{ name = "pytest" }]

[[package]]
name = "python-dotenv"
version = "1.2.4"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pytest"
version = "9.1.1"
source = { registry = "https://pypi.org/simple" }
"""


def toml_python(path):
    """True when the build finds a python3 that reads TOML on `path`."""
    python = find_program("python3", path)
    if python is None:
        return False
    probe = "try:\n    import tomllib\nexcept ImportError:\n    import tomli\n"
    return subprocess.run([python, "-I", "-c", probe], capture_output=True, timeout=60).returncode == 0


@pytest.fixture
def locked_project(scaffolded_project):
    if not toml_python(os.environ["PATH"]):
        pytest.skip("no python3 with tomllib or tomli on PATH")
    (scaffolded_project / "pyproject.toml").write_text(PYPROJECT)
    (scaffolded_project / "uv.lock").write_text(LOCK)
    return scaffolded_project


def edit_pyproject(project_dir, old, new):
    pyproject = project_dir / "pyproject.toml"
    text = pyproject.read_text()
    assert old in text
    pyproject.write_text(text.replace(old, new))


def test_dependency_added_by_hand_stops_the_build(locked_project, run_build, fake_uv):
    first = run_build(locked_project)
    edit_pyproject(locked_project, '"python-dotenv",', '"python-dotenv",\n    "numpy>=1.26",')
    # Control: when uv can run, its verdict wins, so a lock uv accepts builds.
    env, _ = fake_uv("ok")
    run = run_build(locked_project, progress=True, env=env)
    assert "uv.lock" not in run.proc.stdout + run.proc.stderr

    # uv cannot check it offline (numpy is not in its cache): the comparison decides.
    env, log = fake_uv("nocache")
    run = run_build(locked_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode != 0
    assert f"{STALE_MESSAGE}\n  added to dependencies: numpy>=1.26\n" in run.proc.stderr
    assert "Lock check skipped" not in run.proc.stdout
    assert log.read_text() == f"{locked_project} lock --check --offline\n"
    # A preflight stop: the previous build copy and tarball stay.
    assert first.tarball.is_file() and first.staged.is_dir()


@pytest.mark.parametrize(
    "old, new, line",
    [
        ('    "python-dotenv",\n', "", "  removed from dependencies: python-dotenv"),
        (
            '"python-dotenv",',
            '"python-dotenv >= 1.0",',
            "  changed in dependencies: python-dotenv >= 1.0 (uv.lock has python-dotenv)",
        ),
        (
            '"python-dotenv",',
            '"python-dotenv[cli]",',
            "  changed in dependencies: python-dotenv[cli] (uv.lock has python-dotenv)",
        ),
        ('test = ["pytest"]', 'test = ["pytest", "pytest-cov"]', "  added to dependency-groups.test: pytest-cov"),
        ('test = ["pytest"]', 'test = ["pytest"]\ndocs = ["sphinx"]', "  added to dependency-groups.docs: sphinx"),
        ('name = "demo"', 'name = "demo2"', "  project name: pyproject.toml has demo2, uv.lock has demo"),
    ],
    ids=["removed", "version", "extras", "group", "new-group", "name"],
)
def test_stale_lock_is_found_without_uv(locked_project, run_build, old, new, line):
    path = path_without_uv()
    if path is None or not toml_python(path):
        pytest.skip("cannot hide uv here")
    env = {"PATH": path}
    run_build(locked_project, env=env)  # control: the lock matches
    edit_pyproject(locked_project, old, new)

    run = run_build(locked_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode != 0
    assert f"{STALE_MESSAGE}\n{line}\n" in run.proc.stderr


def test_spellings_uv_normalizes_are_not_a_change(locked_project, run_build):
    path = path_without_uv()
    if path is None or not toml_python(path):
        pytest.skip("cannot hide uv here")
    # uv.lock spells names, extras and versions in their normal form and sorts
    # the version clauses; the comparison reads them the same way.
    edit_pyproject(
        locked_project, '"python-dotenv",', "\"Python_Dotenv[CLI] (>= 01.0.0-alpha1, <2) ; python_version < '3.12'\","
    )
    lock = locked_project / "uv.lock"
    lock.write_text(
        lock.read_text().replace(
            'requires-dist = [{ name = "python-dotenv" }]',
            'requires-dist = [{ name = "python-dotenv", extras = ["cli"], specifier = "<2,>=1.0.0a1", '
            "marker = \"python_full_version < '3.12'\" }]",
        )
    )

    run = run_build(locked_project, progress=True, env={"PATH": path})

    assert "uv.lock" not in run.proc.stdout + run.proc.stderr

    # Control: another version is a change.
    edit_pyproject(locked_project, "01.0.0-alpha1", "1.0.0")
    run = run_build(locked_project, expect_ok=False, progress=True, env={"PATH": path})
    assert STALE_MESSAGE in run.proc.stderr


def test_matching_lists_say_what_uv_could_not_check(locked_project, run_build, fake_uv):
    env, _ = fake_uv("nocache")

    run = run_build(locked_project, progress=True, env=env)

    assert (
        "Lock check partly skipped: the dependencies in pyproject.toml match uv.lock, but "
        "'uv lock --check --offline' could not run (exit 1), so other changes such as requires-python "
        "were not checked:\n"
        "      x No solution found when resolving dependencies:\n"
    ) in run.proc.stdout


def real_uv_lock(project_dir, dependencies):
    """Write pyproject.toml with `dependencies` and lock it offline with the
    real uv, or skip when uv cannot (not installed, nothing cached)."""
    uv = find_program("uv", os.environ["PATH"])
    if uv is None:
        pytest.skip("uv is not installed")
    if not toml_python(os.environ["PATH"]):
        pytest.skip("no python3 with tomllib or tomli on PATH")
    deps = "".join(f'    "{d}",\n' for d in dependencies)
    (project_dir / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.9"\ndependencies = [\n{deps}]\n'
    )
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    lock = subprocess.run(
        [uv, "lock", "--offline"], cwd=project_dir, env=env, capture_output=True, text=True, timeout=120
    )
    if lock.returncode != 0:
        pytest.skip(f"uv lock --offline failed here: {lock.stderr.strip()[-200:]}")


def test_real_uv_hand_added_dependency_is_caught(scaffolded_project, run_build):
    # No dependencies, so the lock resolves offline from an empty cache.
    real_uv_lock(scaffolded_project, [])
    run_build(scaffolded_project)
    edit_pyproject(scaffolded_project, "dependencies = [\n]", 'dependencies = [\n    "zzz-not-a-real-package>=1",\n]')

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert f"{STALE_MESSAGE}\n  added to dependencies: zzz-not-a-real-package>=1\n" in run.proc.stderr


def test_real_uv_removal_with_an_empty_cache_is_caught(scaffolded_project, run_build, tmp_path):
    # Both are cached wherever ngargparser's own dependencies were installed.
    real_uv_lock(scaffolded_project, ["python-dotenv", "pytest"])
    empty_cache = tmp_path / "empty-uv-cache"
    empty_cache.mkdir()
    edit_pyproject(scaffolded_project, '    "python-dotenv",\n', "")
    env = {"UV_CACHE_DIR": str(empty_cache)}
    # uv cannot check this offline: pytest, still a dependency, is not in that cache.
    uv = subprocess.run(
        ["uv", "lock", "--check", "--offline"],
        cwd=scaffolded_project,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert uv.returncode == 1 and "not found in the cache" in uv.stderr, uv.stderr

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode != 0
    assert f"{STALE_MESSAGE}\n  removed from dependencies: python-dotenv\n" in run.proc.stderr


# A uv.lock that does not parse stops the preflight. uv sync --frozen fails on
# it at deploy time, and 'uv lock' cannot read it either (checked against uv
# 0.11: it exits 2 with "Failed to parse `uv.lock`").

UNPARSABLE = (
    "ERROR: uv.lock does not parse ({}), so 'uv lock' cannot read it either: restore it "
    "(after a merge: git checkout --theirs uv.lock) or delete it, then run 'uv lock' and commit uv.lock"
)

CONFLICTED_LOCK = LOCK.replace(
    'version = "1.2.4"\n', '<<<<<<< HEAD\nversion = "1.2.4"\n=======\nversion = "1.2.5"\n>>>>>>> other\n'
)
# Cut inside an inline table, as a lost tail of the file leaves it.
TRUNCATED_LOCK = LOCK[: LOCK.index('source = { registry = "https://pypi.org/simple"') + 20]


@pytest.mark.parametrize(
    "text, reason",
    [
        (CONFLICTED_LOCK, "a merge conflict marker at line 26"),
        ("", "the file is empty"),
        ("\n\n", "the file is empty"),
        ('revision = 3\nrequires-python = ">=3.11"\n', "it has no version field"),
        (TRUNCATED_LOCK, None),
    ],
    ids=["conflict", "empty", "blank", "no-version", "truncated"],
)
def test_lock_that_does_not_parse_stops_the_build(locked_project, run_build, fake_uv, text, reason):
    first = run_build(locked_project)
    env, log = fake_uv("error")
    (locked_project / "uv.lock").write_text(text)

    run = run_build(locked_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode != 0
    if reason is None:
        # The TOML reader's own words, with where it stopped first.
        assert "ERROR: uv.lock does not parse (the end of the file: " in run.proc.stderr or (
            "ERROR: uv.lock does not parse (line " in run.proc.stderr
        ), run.proc.stderr
        assert UNPARSABLE.split("({})")[1] in run.proc.stderr
    else:
        assert UNPARSABLE.format(reason) + "\n" in run.proc.stderr
    assert "Lock check skipped" not in run.proc.stdout
    assert not log.exists(), "decided before uv runs"
    # A preflight stop: the previous build copy and tarball stay.
    assert first.tarball.is_file() and first.staged.is_dir()
    assert "Build failed" not in run.proc.stderr


def no_toml_python_path(tmp_path, uv_output, uv_code):
    """PATH with a python3 that cannot read TOML and a stand-in uv first."""
    bin_dir = tmp_path / "no-toml-python"
    bin_dir.mkdir()
    (bin_dir / "python3").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "uv.out").write_text(uv_output)
    (bin_dir / "uv").write_text(f'#!/bin/sh\ncat "{bin_dir / "uv.out"}" >&2\nexit {uv_code}\n')
    for name in ("python3", "uv"):
        (bin_dir / name).chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


# What uv 0.11 prints for TRUNCATED_LOCK-like damage and for a conflict marker.
UV_TRUNCATED = (
    "Using CPython 3.11.15\nerror: Failed to parse `uv.lock`\n  Caused by: TOML parse error at line 9, column 35\n"
    '  |\n9 | source = { registry = "https://p\n  |                                   ^\nunclosed inline table, expected `}`\n'
)
UV_CONFLICT = (
    "Using CPython 3.11.15\nerror: Failed to parse `uv.lock`\n  Caused by: TOML parse error at line 26, column 9\n"
    "   |\n26 | <<<<<<< HEAD\n   |         ^\nkey with no value, expected `=`\n"
)


@pytest.mark.parametrize(
    "text, uv_output, reason",
    [
        (TRUNCATED_LOCK, UV_TRUNCATED, "TOML parse error at line 9, column 35: unclosed inline table, expected `}`"),
        (CONFLICTED_LOCK, UV_CONFLICT, "a merge conflict marker at line 26"),
    ],
    ids=["truncated", "conflict"],
)
def test_without_a_toml_python_uv_decides(scaffolded_project, run_build, tmp_path, text, uv_output, reason):
    # Control: uv failing for another reason means only that its check could
    # not run, as before.
    path = no_toml_python_path(
        tmp_path, "Using CPython 3.11.15\nerror: No interpreter found for Python >=3.14 in managed installations\n", 2
    )
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    run = run_build(scaffolded_project, progress=True, env={"PATH": path})
    assert (
        "Lock check skipped: 'uv lock --check --offline' could not run (exit 2):\n"
        "    error: No interpreter found for Python >=3.14 in managed installations\n"
    ) in run.proc.stdout

    (tmp_path / "no-toml-python" / "uv.out").write_text(uv_output)
    (scaffolded_project / "uv.lock").write_text(text)
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={"PATH": path})

    assert run.proc.returncode != 0
    assert UNPARSABLE.format(reason) + "\n" in run.proc.stderr
    assert "Lock check skipped" not in run.proc.stdout


# Without uv.lock, deploy/install.sh runs a plain 'uv sync --no-dev', which
# installs the newest versions pyproject.toml allows on each deploy.

NO_LOCK_NOTICE = (
    "Notice: the tool has no uv.lock, so the deploy installs the newest versions pyproject.toml allows: "
    "run 'uv lock' and commit uv.lock."
)


def test_tool_without_uv_lock_gets_a_notice(scaffolded_project, run_build, fake_uv):
    env, _ = fake_uv("ok")
    assert not (scaffolded_project / "uv.lock").exists()

    run = run_build(scaffolded_project, progress=True, env=env)

    assert NO_LOCK_NOTICE in run.proc.stdout
    # Control: with a uv.lock, no notice.
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    run = run_build(scaffolded_project, progress=True, env=env)
    assert NO_LOCK_NOTICE not in run.proc.stdout


# A uv.lock that is TOML but that uv rejects, such as one where a merge
# resolved by hand changed a version without its wheels and hashes, built
# green with "Lock check partly skipped": the comparison read it, and uv's
# "Failed to parse `uv.lock`" counted as a check that could not run. uv sync
# --frozen fails on it at deploy time, and 'uv lock' cannot read it either.

UV_REJECTS = (
    "Using CPython 3.11.15\n"
    "error: Failed to parse `uv.lock`\n"
    "  Caused by: The entry for package `python-dotenv` (1.2.3) has wheel `python_dotenv-1.2.4-py3-none-any.whl` "
    "with inconsistent version (1.2.4), which indicates a malformed wheel. If this is intentional, set "
    "`UV_SKIP_WHEEL_FILENAME_CHECK=1`.\n"
)
REJECTED = UNPARSABLE.format("uv rejects what it holds: see below") + (
    "\n    error: Failed to parse `uv.lock`\n      Caused by: The entry for package `python-dotenv` (1.2.3) has wheel"
)
# The lock a merge resolved by hand leaves: another version, the old wheel.
REJECTED_LOCK = LOCK.replace(
    'name = "python-dotenv"\nversion = "1.2.4"\nsource = { registry = "https://pypi.org/simple" }\n',
    'name = "python-dotenv"\nversion = "1.2.3"\nsource = { registry = "https://pypi.org/simple" }\nwheels = [\n'
    '    { url = "https://files.pythonhosted.org/packages/00/00/python_dotenv-1.2.4-py3-none-any.whl", '
    'hash = "sha256:' + "0" * 64 + '", size = 1 },\n]\n',
)


def accepting_uv(tmp_path):
    """The environment for a build with a stand-in uv that accepts every lock."""
    bin_dir = tmp_path / "accepting-uv"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text("#!/bin/sh\necho 'Resolved 3 packages in 1ms' >&2\nexit 0\n")
    (bin_dir / "uv").chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


@pytest.mark.parametrize("lists", ["matching", "stale"])
def test_lock_uv_rejects_stops_the_build(locked_project, run_build, tmp_path, lists):
    first = run_build(locked_project, env=accepting_uv(tmp_path))
    if lists == "stale":
        edit_pyproject(locked_project, '"python-dotenv",', '"python-dotenv",\n    "numpy>=1.26",')
    bin_dir = tmp_path / "rejecting-uv"
    bin_dir.mkdir()
    (bin_dir / "out.txt").write_text(UV_REJECTS)
    (bin_dir / "uv").write_text(f'#!/bin/sh\ncat "{bin_dir / "out.txt"}" >&2\nexit 2\n')
    (bin_dir / "uv").chmod(0o755)

    run = run_build(
        locked_project, expect_ok=False, progress=True, env={"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    )

    assert run.proc.returncode == 1
    assert REJECTED in run.proc.stderr
    assert "Using CPython" not in run.proc.stderr
    assert "Lock check" not in run.proc.stdout
    # Before the out-of-date stop: 'uv lock' cannot fix a lock it cannot read.
    assert STALE_MESSAGE not in run.proc.stderr
    # A preflight stop: the previous build copy and tarball stay.
    assert first.tarball.is_file() and first.staged.is_dir()
    assert "Build failed" not in run.proc.stderr


def test_real_uv_rejected_lock_stops_the_build(locked_project, run_build):
    uv = find_program("uv", os.environ["PATH"])
    if uv is None:
        pytest.skip("uv is not installed")
    (locked_project / "uv.lock").write_text(REJECTED_LOCK)
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
    check = subprocess.run(
        [uv, "lock", "--check", "--offline"],
        cwd=locked_project,
        env={**env, "UV_PYTHON_DOWNLOADS": "never"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    if "Failed to parse `uv.lock`" not in check.stderr:
        pytest.skip(f"this uv does not reject the lock here: {check.stderr.strip().splitlines()[-1:]}")

    run = run_build(locked_project, expect_ok=False, progress=True, env={"UV_PYTHON_DOWNLOADS": "never"})

    assert run.proc.returncode == 1
    assert REJECTED in run.proc.stderr
    assert "Lock check partly skipped" not in run.proc.stdout
