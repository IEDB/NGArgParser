"""The version a CI build job names the tarball after, the tarball name's
length, and the checks ngargparser's own CI runs.

The generated CI build job maps every character the build refuses in a
version to '-', and drops a leading '-': a branch named '#', '@@' or 'ü' maps
to nothing, and the build quietly used the version "local". The job now
stops with one line. A 240-character branch name made a tarball name longer
than a file name may be, and the build failed late with tar's "File name too
long", after the previous build was removed: the preflight now refuses it.

ngargparser's CI ran no pre-commit hook, so the whitespace and end-of-file
hooks were local only; a lint job now runs the hooks no other job runs. The
pre-commit-hooks release the config pinned made pre-commit warn that it
"uses deprecated stage names", and the classifiers stopped at Python 3.12
while CI tests up to 3.14.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_build_ci_template import TEMPLATE

from ngargparser import cli

# A generated tool has no .venv and no uv.lock, so its startup check has no Python
# and stops the build: builds run here without it.
NO_PYTHON = {"NGARGPARSER_SKIP_STARTUP_CHECK": "1"}

REPO = Path(cli.__file__).parents[1]
CI_FILE = REPO / ".gitlab-ci.yml"
PRE_COMMIT = REPO / ".pre-commit-config.yaml"

needs_build_tools = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


def build_job_until_make(ref):
    """The build job's script lines before `uv sync`, run for the git ref `ref`,
    then a line that prints TOOL_VERSION: the CompletedProcess."""
    yaml = pytest.importorskip("yaml")
    script = yaml.safe_load(TEMPLATE.read_text().replace("{TOOL_NAME}", "demo"))["build"]["script"]
    before = script[: script.index("uv sync --locked")]
    env = {k: v for k, v in os.environ.items() if k != "TOOL_VERSION"} | NO_PYTHON
    return subprocess.run(
        ["sh", "-e", "-c", "\n".join(before) + '\nprintf "%s" "$TOOL_VERSION"'],
        env={**env, "CI_COMMIT_REF_NAME": ref, "TOOL_VERSION": ref},
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("ref", ["#", "@@", "ü", "#@!", "-", "--"])
def test_ci_build_job_stops_when_the_branch_name_maps_to_nothing(ref):
    proc = build_job_until_make(ref)

    assert proc.returncode == 1
    assert proc.stderr == (
        f"ERROR: the branch or tag name {ref} has no letter, digit, dot, underscore or plus sign (leading dashes "
        "are dropped), so it names no version: rename it, or run the pipeline with a TOOL_VERSION variable.\n"
    )
    assert proc.stdout == ""


@pytest.mark.parametrize("ref, version", [("v1.2.0", "v1.2.0"), ("#42", "42"), ("feat/x", "feat-x"), ("_", "_")])
def test_ci_build_job_goes_on_with_a_version(ref, version):
    # Control: a name that maps to a version builds as before.
    proc = build_job_until_make(ref)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == version


def build(project_dir, version):
    return subprocess.run(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=project_dir,
        env={**os.environ, **NO_PYTHON, "TOOL_VERSION": version},
        capture_output=True,
        text=True,
        timeout=300,
    )


@needs_build_tools
def test_a_version_too_long_for_the_tarball_name_stops_the_preflight(scaffolded_project):
    first = build(scaffolded_project, "tver")
    assert first.returncode == 0, first.stderr
    # IEDB_NG_DEMO-<version>.tar.gz: 20 characters besides the version, and
    # .<name>.partial adds 9, so 226 characters is the most.
    longest = "v" * 226

    proc = build(scaffolded_project, longest + "x")

    assert proc.returncode == 1
    assert proc.stderr == (
        "ERROR: TOOL_VERSION is 227 characters long, which makes the tarball's name 247 characters, and at most "
        "246 fit (a file name may have 255 bytes, and the build first writes .<name>.partial): use a TOOL_VERSION "
        "of at most 226 characters. It comes from the TOOL_VERSION environment variable (default local; a '/' "
        "becomes '-').\n"
    )
    # A preflight stop: the previous build stays.
    assert (scaffolded_project / "build" / "IEDB_NG_DEMO-tver.tar.gz").is_file()
    assert (scaffolded_project / "build" / "ng_demo-tver").is_dir()

    # The 240 characters of a long branch name.
    proc = build(scaffolded_project, "b" * 240)
    assert proc.returncode == 1
    assert "use a TOOL_VERSION of at most 226 characters" in proc.stderr
    assert "File name too long" not in proc.stderr

    # Control: the longest version that fits builds.
    proc = build(scaffolded_project, longest)
    assert proc.returncode == 0, proc.stderr
    assert (scaffolded_project / "build" / f"IEDB_NG_DEMO-{longest}.tar.gz").is_file()


@needs_build_tools
def test_a_tool_name_too_long_for_the_tarball_name_stops_the_preflight(scaffolded_project):
    conf = scaffolded_project / "scripts" / "core" / "build.conf"
    conf.write_text(conf.read_text() + f'TOOL_NAME="{"n" * 240}"\n')

    proc = build(scaffolded_project, "tver")

    assert proc.returncode == 1
    assert (
        "ERROR: TOOL_NAME and TARBALL_PREFIX in scripts/core/build.conf make the tarball's name 257 characters "
        "with TOOL_VERSION, and at most 246 fit (a file name may have 255 bytes, and the build first writes "
        ".<name>.partial): shorten them.\n"
    ) == proc.stderr
    assert not (scaffolded_project / "build").exists()


# ngargparser's own CI.


@pytest.fixture
def ci():
    if not CI_FILE.is_file():
        pytest.skip("not run from a checkout of the ngargparser repository")
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CI_FILE.read_text())


def pre_commit_hooks():
    yaml = pytest.importorskip("yaml")
    config = yaml.safe_load(PRE_COMMIT.read_text())
    return config, [(repo, hook["id"]) for repo in config["repos"] for hook in repo["hooks"]]


def test_a_lint_job_runs_the_pre_commit_hooks_no_other_job_runs(ci):
    job = ci["pre-commit"]
    assert job["stage"] == "lint"
    version = ci["variables"]["PRE_COMMIT_VERSION"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    assert 'pip install --quiet "pre-commit==$PRE_COMMIT_VERSION"' in job["before_script"]
    # pre-commit lists the files with git, which python:*-slim lacks.
    assert any("apt-get" in line and " git" in line for line in job["before_script"])
    run = [line for line in job["script"] if " pre-commit run " in f" {line} "]
    assert len(run) == 1, job["script"]
    match = re.fullmatch(r"SKIP=(\S+) pre-commit run --all-files --show-diff-on-failure", run[0])
    assert match, run[0]
    skipped = set(match.group(1).split(","))

    # It skips exactly the hooks of the tools other jobs run, and runs the rest.
    _config, hooks = pre_commit_hooks()
    ids = {hook_id for _repo, hook_id in hooks}
    assert skipped == {"ruff-check", "ruff-format", "mypy"}
    assert skipped <= ids
    assert any(line.startswith("uv run ruff check") for line in ci["ruff"]["script"])
    assert "uv run ruff format --check ." in ci["ruff"]["script"]
    assert ci["mypy"]["script"] == ["uv run mypy"]
    assert {"trailing-whitespace", "end-of-file-fixer"} <= ids - skipped


def test_pre_commit_hooks_rev_uses_the_current_stage_names():
    config, _hooks = pre_commit_hooks()
    rev = next(r["rev"] for r in config["repos"] if r["repo"] == "https://github.com/pre-commit/pre-commit-hooks")
    # v5.0.0 and later use the stage names of pre-commit 3.2 (pre-commit, pre-push).
    assert re.fullmatch(r"v\d+\.\d+\.\d+", rev), rev
    assert tuple(map(int, rev[1:].split("."))) >= (5, 0, 0), rev


def test_classifiers_list_every_python_ci_tests(ci):
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    classifiers = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["classifiers"]
    listed = {
        c.rsplit(" :: ", 1)[1] for c in classifiers if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)
    }
    tested = {v for entry in ci["pytest"]["parallel"]["matrix"] for v in entry["PYVER"]}

    assert listed == tested, (sorted(listed), sorted(tested))
    assert {"3.13", "3.14"} <= listed


@pytest.mark.skipif(os.environ.get("NGARGPARSER_RUN_PRE_COMMIT") != "1", reason="opt-in: needs github.com")
def test_the_pre_commit_job_passes_on_this_checkout(ci, tmp_path):
    # The job's command in a copy of this checkout's files, as git lists them.
    pre_commit = shutil.which("pre-commit")
    if pre_commit is None:
        pytest.skip("pre-commit is not installed here")
    files = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=REPO, capture_output=True, timeout=60, check=True
    ).stdout.split(b"\0")
    copy = tmp_path / "checkout"
    for name in filter(None, files):
        source = REPO / os.fsdecode(name)
        if source.is_file():
            target = copy / os.fsdecode(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=copy, check=True, timeout=60)
    command = next(line for line in ci["pre-commit"]["script"] if "pre-commit run" in line)
    proc = subprocess.run(["sh", "-c", command], cwd=copy, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "deprecated stage names" not in proc.stdout + proc.stderr
