"""Behavioral tests for the build.sh do-not-distribute engine.

These run the real scripts/core/build.sh in a scaffolded project and inspect
both the staged build directory and the produced tarball. The exclusion file
(.distignore at the project root; legacy alias scripts/do-not-distribute.txt)
has exact .gitignore semantics (evaluated via `git check-ignore`), with a
built-in `.*` baseline (hidden files excluded unless re-included with `!`) and
force-included deploy-contract files (README, deploy/install.sh).
"""

import os
import shutil
import subprocess
import tarfile

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh exclusion tests require bash and git",
)

VERSION = "tver"
TOOL_DIR = f"ng_demo-{VERSION}"
TARBALL = f"IEDB_NG_DEMO-{VERSION}.tar.gz"

# The template shipped before the switch to .gitignore semantics.
LEGACY_LIST = """\
# list of files to remove from the distributed package
.env
.gitmodules
.gitignore
.gitlab-ci.yml
.git/*
.python-version
.git/
__pycache__/*
__pycache__
build.sh
build.md
build/*
build/
do-not-distribute.txt
Makefile
**.sh
"""


def run_build(project_dir):
    """Run build.sh, return (staged_build_dir, {relpath: TarInfo})."""
    result = subprocess.run(
        ["bash", "scripts/core/build.sh"],
        cwd=project_dir,
        env={**os.environ, "TOOL_VERSION": VERSION},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"build.sh failed (rc={result.returncode})\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    tar_path = project_dir / "build" / TARBALL
    assert tar_path.exists(), "tarball not produced"
    with tarfile.open(tar_path) as tf:
        members = {m.name[len(TOOL_DIR) + 1 :]: m for m in tf.getmembers() if m.name != TOOL_DIR}
    return project_dir / "build" / TOOL_DIR, members


def append_rules(project_dir, *lines):
    # New scaffolds carry the exclusion file at the project root as .distignore.
    dnd = project_dir / ".distignore"
    dnd.write_text(dnd.read_text() + "\n".join(lines) + "\n")


def test_default_template_exclusions(scaffolded_project):
    _, members = run_build(scaffolded_project)

    for absent in (
        "Makefile",
        "scripts/hooks.sh",
        ".distignore",  # dotfile at root — auto-excluded by the '.*' baseline
        "scripts/core/build.sh",
        "scripts/core",
    ):
        assert absent not in members, f"{absent} must not ship"

    shell_scripts = {m for m in members if m.endswith(".sh")}
    assert shell_scripts == {"deploy/install.sh"}, f"only the deploy hook may ship as *.sh, got: {shell_scripts}"

    for present in (
        "deploy/install.sh",
        "scripts/build.conf",
        "configure",
        "VERSION",
        "README.md",
        "pyproject.toml",
        "src/core/NGArgumentParser.py",
    ):
        assert present in members, f"{present} missing from tarball"


def test_hidden_files_excluded_by_default(scaffolded_project):
    (scaffolded_project / ".secret").write_text("token=abc\n")
    (scaffolded_project / "src" / ".hidden.cfg").write_text("x\n")
    data = scaffolded_project / "data"
    data.mkdir()
    (data / ".DS_Store").write_text("junk")
    (data / "keep.txt").write_text("keep\n")

    _, members = run_build(scaffolded_project)

    for absent in (".secret", ".env", ".gitignore", "src/.hidden.cfg", "data/.DS_Store"):
        assert absent not in members, f"hidden path {absent} must not ship"
    assert "data/keep.txt" in members


def test_negation_reincludes_hidden(scaffolded_project):
    streamlit = scaffolded_project / ".streamlit"
    streamlit.mkdir()
    (streamlit / "config.toml").write_text("[server]\n")
    append_rules(scaffolded_project, "!.env", "!.streamlit/")

    _, members = run_build(scaffolded_project)

    assert ".env" in members
    assert ".streamlit/config.toml" in members
    assert ".gitignore" not in members


def test_nested_exclusions(scaffolded_project):
    deep = scaffolded_project / "src" / "deep"
    (deep / "__pycache__").mkdir(parents=True)
    (deep / "mod.py").write_text("x = 1\n")
    (deep / "__pycache__" / "m.pyc").write_bytes(b"\x00")
    logs = scaffolded_project / "data" / "logs"
    logs.mkdir(parents=True)
    (logs / "app.log").write_text("log\n")
    (logs / "notes.txt").write_text("notes\n")
    append_rules(scaffolded_project, "*.log")

    _, members = run_build(scaffolded_project)

    assert "src/deep/mod.py" in members
    assert "src/deep/__pycache__/m.pyc" not in members
    assert "data/logs/app.log" not in members
    assert "data/logs/notes.txt" in members


def test_legacy_literal_list_still_works(scaffolded_project):
    # Simulate an old project: no root .distignore, only the legacy file.
    (scaffolded_project / ".distignore").unlink()
    dnd = scaffolded_project / "scripts" / "do-not-distribute.txt"
    dnd.write_text(LEGACY_LIST)

    _, members = run_build(scaffolded_project)

    for absent in ("Makefile", ".env", ".gitignore", "scripts/hooks.sh", "scripts/core/build.sh"):
        assert absent not in members, f"{absent} must not ship with the legacy list"
    assert "README.md" in members
    # The deploy contract survives even the legacy `**.sh` pattern.
    assert "deploy/install.sh" in members


def test_distignore_takes_precedence_over_legacy(scaffolded_project):
    # Both files present: the root .distignore wins, the legacy file is ignored.
    (scaffolded_project / "alpha.txt").write_text("a\n")
    (scaffolded_project / "beta.txt").write_text("b\n")
    (scaffolded_project / ".distignore").write_text("alpha.txt\n")
    (scaffolded_project / "scripts" / "do-not-distribute.txt").write_text("beta.txt\n")

    _, members = run_build(scaffolded_project)

    assert "alpha.txt" not in members, "root .distignore rule must apply"
    assert "beta.txt" in members, "legacy do-not-distribute.txt must be ignored when .distignore exists"


def test_contract_files_cannot_be_excluded(scaffolded_project):
    (scaffolded_project / "README").write_text("TOOL_VERSION\n")
    append_rules(scaffolded_project, "README", "deploy/", "deploy/install.sh")

    _, members = run_build(scaffolded_project)

    assert "README" in members
    assert "deploy/install.sh" in members


def test_git_dir_never_ships(scaffolded_project):
    subprocess.run(["git", "init", "-q", "."], cwd=scaffolded_project, check=True, timeout=60)
    append_rules(scaffolded_project, "!.git/")

    _, members = run_build(scaffolded_project)

    assert not any(m == ".git" or m.startswith(".git/") for m in members)


def test_staged_dir_mirrors_tarball(scaffolded_project):
    staged, members = run_build(scaffolded_project)

    staged_files = set()
    for dirpath, _dirnames, filenames in os.walk(staged, followlinks=True):
        rel = os.path.relpath(dirpath, staged)
        for name in filenames:
            staged_files.add(name if rel == "." else f"{rel}/{name}")

    tar_files = {name for name, info in members.items() if info.isfile()}
    assert staged_files == tar_files


def test_empty_dir_semantics(scaffolded_project):
    (scaffolded_project / "outputs").mkdir()

    _, members = run_build(scaffolded_project)

    # A genuinely empty source dir ships ...
    assert "outputs" in members and members["outputs"].isdir()
    # ... but a dir emptied by exclusion (scripts/core, whose only content is
    # the excluded build.sh) is dropped entirely.
    assert not any(m == "scripts/core" or m.startswith("scripts/core/") for m in members)
