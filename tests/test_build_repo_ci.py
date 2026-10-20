"""Tests for ngargparser's own .gitlab-ci.yml and the dev group it installs.

The test matrix covers every Python the package supports, one job runs the
suite as an ordinary user (tests that need a permission error skip as root),
and the dev group carries pyyaml, so the YAML checks of the generated CI file
run instead of skipping.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ngargparser import cli

REPO = Path(cli.__file__).parents[1]
CI_FILE = REPO / ".gitlab-ci.yml"


@pytest.fixture
def ci():
    if not CI_FILE.is_file():
        pytest.skip("not run from a checkout of the ngargparser repository")
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CI_FILE.read_text())


def test_matrix_covers_python_3_9_to_3_14(ci):
    versions = [v for entry in ci["pytest"]["parallel"]["matrix"] for v in entry["PYVER"]]
    assert sorted(versions, key=lambda v: tuple(map(int, v.split(".")))) == [
        "3.9",
        "3.10",
        "3.11",
        "3.12",
        "3.13",
        "3.14",
    ]
    assert ci["pytest"]["image"] == "python:$PYVER-slim"


def apt_installs(job):
    """The packages the job's apt-get install lines name."""
    names = []
    for line in job["before_script"]:
        if "apt-get" in line and " install " in line:
            names += [word for word in line.split("install", 1)[1].split() if not word.startswith("-")]
    return names


# python:*-slim has no make, so tests/test_build_makefile.py skipped in every
# pytest job: the jobs that run the suite install make with git.


@pytest.mark.parametrize("name", ["pytest", "pytest-non-root"])
def test_the_suite_jobs_install_git_and_make(ci, name):
    assert {"git", "make"} <= set(apt_installs(ci[name])), apt_installs(ci[name])


def test_one_job_runs_the_suite_as_a_non_root_user(ci):
    job = ci["pytest-non-root"]

    assert job["stage"] in ci["stages"]
    setup = job["before_script"]
    assert any(line.startswith("apt-get") and " git" in line for line in setup), "the build tests need git"
    assert any(line.startswith("apt-get") and " make" in line for line in setup), "the Makefile tests need make"
    assert "useradd --create-home tester" in setup
    assert 'chown -R tester "$CI_PROJECT_DIR"' in setup
    assert job["script"] == ["runuser -u tester -- env HOME=/home/tester sh -c 'uv sync --locked && uv run pytest'"]
    # On the oldest and the newest Python: a permission error can read differently.
    assert job["image"] == "python:$PYVER-slim"
    assert job["parallel"]["matrix"] == [{"PYVER": ["3.9", "3.12", "3.14"]}]


def all_commands(ci):
    """Every before_script and script line of every job, the default included."""
    for name, job in ci.items():
        if isinstance(job, dict):
            for key in ("before_script", "script"):
                yield from ((name, line) for line in job.get(key, []))


def test_every_uv_sync_is_locked(ci):
    # uv sync --locked installs uv.lock as it is and stops when it is out of
    # date with pyproject.toml, instead of rewriting it in the job.
    syncs = [(job, line) for job, line in all_commands(ci) if "uv sync" in line]

    assert {job for job, _ in syncs} >= {"default", "pytest", "pytest-non-root", "pytest-real-uv"}
    for job, line in syncs:
        assert [s.strip() for s in re.findall(r"uv sync\b[^&;']*", line)] == ["uv sync --locked"], (job, line)


def test_every_job_installs_the_pinned_uv(ci):
    version = ci["variables"]["UV_VERSION"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    installs = [(job, line) for job, line in all_commands(ci) if "pip install" in line and "uv" in line]

    assert {job for job, _ in installs} >= {"default", "pytest", "pytest-non-root", "pytest-real-uv"}
    for job, line in installs:
        assert line == 'pip install --quiet "uv==$UV_VERSION"', (job, line)

    # The pin is the uv this checkout was locked with, where uv is installed.
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed here")
    local = subprocess.run([uv, "--version"], capture_output=True, text=True, timeout=60).stdout.split()
    if local[1:2] != [version]:
        pytest.skip(f"uv here is {' '.join(local[1:2])}, the pin is {version}")


# No "real_uv" in these tests' names, so the job's -k real_uv does not pick them.
REAL_UV_COMMAND = "NGARGPARSER_REAL_UV=1 uv run pytest -q -rs -k real_uv"


def real_uv_line(ci):
    return next(line for line in ci["pytest-real-uv"]["script"] if REAL_UV_COMMAND in line)


def run_real_uv_line(line, tmp_path, output, code):
    """The job's command, with pytest replaced by a stand-in that prints
    `output` and exits with `code`."""
    fake = tmp_path / "fake-pytest"
    fake.write_text(output)
    stand_in = f"sh -c 'cat \"{fake}\"; exit {code}'"
    return subprocess.run(
        ["sh", "-e", "-c", line.replace(REAL_UV_COMMAND, stand_in, 1)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_one_job_runs_the_tests_that_need_uv_itself(ci, tmp_path):
    job = ci["pytest-real-uv"]

    assert job["stage"] in ci["stages"]
    assert any(line.startswith("apt-get") and " git" in line for line in job["before_script"]), (
        "the build tests need git"
    )
    line = real_uv_line(ci)
    # pytest exits 5 when no test matches, which the job accepts; any other
    # failure fails it.
    for code, ok in ((0, True), (5, True), (1, False), (2, False)):
        proc = run_real_uv_line(line, tmp_path, f"exit {code}\n", code)
        assert (proc.returncode == 0) is ok, (code, proc.returncode, proc.stdout)

    # Tests to run are there: names with real_uv.
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "--collect-only", "-q", "-k", "real_uv"]
        + ["-p", "no:cacheprovider", "tests"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert re.search(r"::test_\w*real_uv", collected.stdout), collected.stdout


# The opt-in deploy test needs the Python 3.11 generated tools ask for, found
# without a download, and skips without it: on python:3.12-slim the job passed
# without running it. A skipped test now fails the job.


def test_the_real_uv_job_runs_on_python_3_11(ci):
    assert ci["pytest-real-uv"]["image"] == "python:3.11-slim"


def test_a_skipped_real_uv_test_fails_the_job(ci, tmp_path):
    line = real_uv_line(ci)
    passed = ".....                                                     [100%]\n5 passed in 9.12s\n"
    skipped = (
        "....s                                                     [100%]\n"
        "=========================== short test summary info ============================\n"
        "SKIPPED [1] tests/test_install_real_uv.py:47: uv cannot sync offline here\n"
        "4 passed, 1 skipped in 8.70s\n"
    )

    proc = run_real_uv_line(line, tmp_path, passed, 0)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "5 passed" in proc.stdout, "the job shows pytest's output"

    proc = run_real_uv_line(line, tmp_path, skipped, 0)
    assert proc.returncode != 0
    assert "SKIPPED [1] tests/test_install_real_uv.py:47" in proc.stdout
    assert "ERROR: a real_uv test was skipped" in proc.stdout

    # A real pytest run with -rs prints the SKIPPED line the job looks for.
    (tmp_path / "test_skip.py").write_text("import pytest\n\n\ndef test_real_uv_x():\n    pytest.skip('why')\n")
    real = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider", "-q", "-rs", "test_skip.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert re.search(r"^SKIPPED \[1\] test_skip\.py:\d+: why$", real.stdout, re.M), real.stdout


def test_ci_comments_name_current_files(ci):
    text = CI_FILE.read_text()

    assert "do-not-distribute.txt" not in text
    assert ".distignore" in text
    assert not re.search("[\u2013\u2014]", text), "no en or em dashes"


def test_dev_group_installs_pyyaml():
    pyproject = (REPO / "pyproject.toml").read_text()
    dev = re.search(r"^\[dependency-groups\]\ndev = \[(.*?)\]$", pyproject, re.M | re.S)
    assert dev, "dev dependency group not found"
    assert re.search(r'^\s*"pyyaml\b', dev.group(1), re.M)
    assert importlib.util.find_spec("yaml") is not None, "run the tests with the dev group (uv run pytest)"


# The lint stage also checks the templates every generated tool runs: the
# shell scripts with shellcheck, and the Python templates for errors with ruff
# (the main ruff run leaves them out).

SHELL_TEMPLATES = [
    "ngargparser/templates/build.sh",
    "ngargparser/templates/deploy/install.sh",
    "ngargparser/templates/get_libs.sh",
    "ngargparser/templates/hooks.sh",
    "ngargparser/templates/configure",
]
TEMPLATE_RUFF = "ruff check --select F --ignore F401 ngargparser/templates"


def run_tool(args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=300)


def test_lint_stage_checks_the_python_templates(ci):
    assert f"uv run {TEMPLATE_RUFF}" in ci["ruff"]["script"]

    # The check passes on the templates as they are.
    proc = run_tool([sys.executable, "-m", "ruff", *TEMPLATE_RUFF.split()[1:]])
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_lint_stage_runs_shellcheck_on_the_shell_templates(ci):
    job = ci["shellcheck"]

    assert job["stage"] == "lint"
    # A pinned shellcheck, from PyPI, so the result does not move with the image.
    version = ci["variables"]["SHELLCHECK_PY_VERSION"]
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version), version
    assert job["before_script"] == ['pip install --quiet "shellcheck-py==$SHELLCHECK_PY_VERSION"']
    command = next(line for line in job["script"] if line.startswith("shellcheck -S warning"))
    assert command.split()[3:] == SHELL_TEMPLATES
    for path in SHELL_TEMPLATES:
        assert (REPO / path).is_file(), f"{path} is gone: update the shellcheck job"

    # The job passes on the templates as they are, where shellcheck is installed.
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed here (the CI lint job installs it)")
    proc = run_tool([shellcheck, *command.split()[1:]])
    assert proc.returncode == 0, proc.stdout + proc.stderr


# The real_uv tests run uv offline, from its cache, which is empty on a fresh
# runner: three of them skipped there, and the job failed its own skip guard.
# The job warms the cache first, with a throwaway tool from this checkout's
# cli generate, locked and synced online, then locked for requires-python
# >=3.9, the range the tests' own projects ask for.


def test_real_uv_job_warms_the_uv_cache(ci, tmp_path):
    warm_up = ci["pytest-real-uv"]["before_script"][-1]
    assert (
        ci["pytest-real-uv"]["before_script"].index("uv sync --locked") < len(ci["pytest-real-uv"]["before_script"]) - 1
    )

    # Run the line with a stand-in uv that logs each call, as the job's shell runs it.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv.log"
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        f'echo "$(pwd)|$*" >> "{log}"\n'
        'if [ "$1" = run ]; then\n'
        '    mkdir warmup && printf \'[project]\\nname = "warmup"\\nrequires-python = ">=3.11,<3.12"\\n\' > warmup/pyproject.toml\n'
        "fi\n"
        'if [ "$1" = lock ]; then grep requires-python pyproject.toml >> "' + str(log) + '"; fi\n'
    )
    (bin_dir / "uv").chmod(0o755)
    project = tmp_path / "checkout"
    project.mkdir()
    proc = subprocess.run(
        ["sh", "-e", "-c", warm_up + '\npwd > "$OUT"'],
        cwd=project,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "CI_PROJECT_DIR": str(project),
            "OUT": str(tmp_path / "pwd"),
            "TMPDIR": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = [line.split("|", 1) for line in log.read_text().splitlines() if "|" in line]
    assert [args for _, args in calls] == [
        f"run --project {project} cli generate warmup",
        "lock",
        "sync --locked",
        "lock",
    ]
    assert all(cwd != str(project) for cwd, _ in calls), "the throwaway tool lives outside the checkout"
    assert calls[1][0].endswith("/warmup") and calls[3][0] == calls[1][0]
    assert 'requires-python = ">=3.9"' in log.read_text().splitlines()[-1], "the second lock covers Python 3.9"
    assert (tmp_path / "pwd").read_text().strip() == str(project), "the job's script runs in the checkout again"


# mypy 2 cannot check for Python 3.9 ("python_version: Python 3.9 is not
# supported"), so on the 3.12 image the mypy job checked for 3.10 instead.


def test_mypy_stays_below_2_so_the_3_9_target_holds():
    pyproject = (REPO / "pyproject.toml").read_text()
    dev = re.search(r"^\[dependency-groups\]\ndev = \[(.*?)\n\]$", pyproject, re.M | re.S)
    assert dev, "dev dependency group not found"
    assert '"mypy>=1.10,<2"' in dev.group(1)
    assert 'python_version = "3.9"' in pyproject
    lock = (REPO / "uv.lock").read_text()
    versions = re.findall(r'^name = "mypy"\nversion = "([^"]+)"$', lock, re.M)
    assert versions and all(int(v.split(".")[0]) < 2 for v in versions), versions
    # A newer pytest (on 3.10+) uses syntax 3.9 lacks, which mypy checking for 3.9 cannot read.
    assert re.search(
        r'\[\[tool\.mypy\.overrides\]\]\nmodule = \["pytest", "_pytest\.\*"\]\nfollow_imports = "skip"', pyproject
    )


# The shellcheck test of the setup scripts a generated tool writes skipped in
# every pytest job, which had no shellcheck. The 3.11 leg installs the pinned
# one and sets NGARGPARSER_REQUIRE_SHELLCHECK, under which that test must not skip.


def test_one_pytest_leg_requires_shellcheck(ci, tmp_path):
    legs = [entry for entry in ci["pytest"]["parallel"]["matrix"] if "NGARGPARSER_REQUIRE_SHELLCHECK" in entry]
    assert legs == [{"PYVER": ["3.11"], "NGARGPARSER_REQUIRE_SHELLCHECK": ["1"]}]
    line = next(line for line in ci["pytest"]["before_script"] if "shellcheck-py" in line)

    # The line installs the pinned shellcheck-py in that leg only.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "pip.log"
    (bin_dir / "pip").write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n')
    (bin_dir / "shellcheck").write_text("#!/bin/sh\necho 'ShellCheck - shell script analysis tool'\n")
    for name in ("pip", "shellcheck"):
        (bin_dir / name).chmod(0o755)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "SHELLCHECK_PY_VERSION": "9.9.9.9"}
    for value, installs in ((None, False), ("1", True)):
        leg_env = {**os.environ, **env}
        leg_env.pop("NGARGPARSER_REQUIRE_SHELLCHECK", None)
        if value:
            leg_env["NGARGPARSER_REQUIRE_SHELLCHECK"] = value
        proc = subprocess.run(["sh", "-e", "-c", line], env=leg_env, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert log.exists() == installs
    assert log.read_text() == "install --quiet shellcheck-py==9.9.9.9\n"


# The pre-commit mypy hook skipped three of the files [tool.mypy] checks
# (NGArgumentParser.py, NGChildArgumentParser.py and templates/configure.py):
# the config's top-level exclude dropped them before the hook's files filter.


def test_pre_commit_mypy_hook_checks_every_mypy_file():
    yaml = pytest.importorskip("yaml")
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
    mypy_files = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["mypy"]["files"]
    hooks = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}

    def selected(hook_id, path):
        # As pre-commit picks a hook's files: the config's exclude, then the hook's files and exclude.
        hook = hooks[hook_id]
        return (
            not re.search(config.get("exclude", "^$"), path)
            and re.search(hook.get("files", ""), path) is not None
            and not re.search(hook.get("exclude", "^$"), path)
        )

    assert len(mypy_files) == 7
    assert [path for path in mypy_files if not selected("mypy", path)] == []
    # The scaffold payload stays out of the hooks that would rewrite it.
    for hook_id in ("ruff-check", "ruff-format", "trailing-whitespace", "end-of-file-fixer"):
        for path in (
            "ngargparser/NGArgumentParser.py",
            "ngargparser/templates/configure.py",
            "ngargparser/validators.py",
        ):
            assert not selected(hook_id, path), (hook_id, path)
        assert selected(hook_id, "ngargparser/cli.py"), hook_id
