"""Behavioral tests for the build.sh do-not-distribute engine.

These run the real scripts/core/build.sh in a scaffolded project and inspect
both the staged build directory and the produced tarball. The exclusion file
(.distignore at the project root) has exact .gitignore semantics (evaluated via
`git check-ignore`), with a built-in `.*` baseline (hidden files excluded unless
re-included with `!`) and force-included deploy-contract files (README,
deploy/install.sh). The old exclusion file names fail the build.
"""

import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh exclusion tests require bash and git",
)

# The legacy do-not-distribute.txt template, from before .distignore.
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


def append_rules(project_dir, *lines):
    # New scaffolds carry the exclusion file at the project root as .distignore.
    dnd = project_dir / ".distignore"
    dnd.write_text(dnd.read_text() + "\n".join(lines) + "\n")


def test_default_template_exclusions(scaffolded_project, run_build):
    members = run_build(scaffolded_project).members

    for absent in (
        "Makefile",
        "scripts/hooks.sh",
        "scripts/get_libs.sh",
        ".distignore",  # dotfile at root — auto-excluded by the '.*' baseline
        "scripts/core/build.sh",
    ):
        assert absent not in members, f"{absent} must not ship"

    shell_scripts = {m for m in members if m.endswith(".sh")}
    assert shell_scripts == {"deploy/install.sh"}, f"only the deploy hook may ship as *.sh, got: {shell_scripts}"

    for present in (
        "deploy/install.sh",
        "scripts/core/build.conf",
        "configure",
        "VERSION",
        "README.md",
        "pyproject.toml",
        "src/core/NGArgumentParser.py",
    ):
        assert present in members, f"{present} missing from tarball"


def test_hidden_files_excluded_by_default(scaffolded_project, run_build):
    (scaffolded_project / ".secret").write_text("token=abc\n")
    (scaffolded_project / "src" / ".hidden.cfg").write_text("x\n")
    data = scaffolded_project / "data"
    data.mkdir()
    (data / ".DS_Store").write_text("junk")
    (data / "keep.txt").write_text("keep\n")

    members = run_build(scaffolded_project).members

    for absent in (".secret", ".env", ".gitignore", "src/.hidden.cfg", "data/.DS_Store"):
        assert absent not in members, f"hidden path {absent} must not ship"
    assert "data/keep.txt" in members


def test_negation_reincludes_hidden(scaffolded_project, run_build):
    streamlit = scaffolded_project / ".streamlit"
    streamlit.mkdir()
    (streamlit / "config.toml").write_text("[server]\n")
    append_rules(scaffolded_project, "!.env", "!.streamlit/")

    members = run_build(scaffolded_project).members

    assert ".env" in members
    assert ".streamlit/config.toml" in members
    assert ".gitignore" not in members


def test_nested_exclusions(scaffolded_project, run_build):
    deep = scaffolded_project / "src" / "deep"
    (deep / "__pycache__").mkdir(parents=True)
    (deep / "mod.py").write_text("x = 1\n")
    (deep / "__pycache__" / "m.pyc").write_bytes(b"\x00")
    logs = scaffolded_project / "data" / "logs"
    logs.mkdir(parents=True)
    (logs / "app.log").write_text("log\n")
    (logs / "notes.txt").write_text("notes\n")
    append_rules(scaffolded_project, "*.log")

    members = run_build(scaffolded_project).members

    assert "src/deep/mod.py" in members
    assert "src/deep/__pycache__/m.pyc" not in members
    assert "data/logs/app.log" not in members
    assert "data/logs/notes.txt" in members


@pytest.mark.parametrize("legacy", ["scripts/do-not-distribute.txt", "do-not-distribute.txt", "scripts/.distignore"])
def test_legacy_exclusion_file_fails_build(scaffolded_project, run_build, legacy):
    # An old project: no root .distignore, only a file the build no longer reads.
    # Ignoring it would silently ship everything it used to exclude.
    (scaffolded_project / ".distignore").unlink()
    (scaffolded_project / legacy).write_text(LEGACY_LIST)

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert f"{legacy} is no longer read" in run.proc.stderr
    assert ".distignore" in run.proc.stderr
    assert not run.tarball.exists()


def test_legacy_exclusion_file_ignored_next_to_distignore(scaffolded_project, run_build):
    # Both files present: the root .distignore applies, the legacy file is ignored.
    (scaffolded_project / "alpha.txt").write_text("a\n")
    (scaffolded_project / "beta.txt").write_text("b\n")
    (scaffolded_project / ".distignore").write_text("alpha.txt\n")
    (scaffolded_project / "scripts" / "do-not-distribute.txt").write_text("beta.txt\n")

    members = run_build(scaffolded_project).members

    assert "alpha.txt" not in members, "root .distignore rule must apply"
    assert "beta.txt" in members, "legacy do-not-distribute.txt must be ignored when .distignore exists"


def test_contract_files_cannot_be_excluded(scaffolded_project, run_build):
    (scaffolded_project / "README").write_text("TOOL_VERSION\n")
    append_rules(scaffolded_project, "README", "deploy/", "deploy/install.sh")

    members = run_build(scaffolded_project).members

    assert "README" in members
    assert "deploy/install.sh" in members


def test_exclusion_file_without_trailing_newline(scaffolded_project, run_build):
    # Regression: an exclusion file whose LAST line has no trailing newline must
    # not fuse with the forced contract rules build.sh appends after it. Before the
    # fix, the composed pattern became "*.json!README" (a dead rule) and nothing was
    # excluded. Note: .write_text("*.json") deliberately writes NO trailing newline.
    (scaffolded_project / "top.json").write_text("{}\n")
    data = scaffolded_project / "data"
    data.mkdir()
    (data / "x.json").write_text("{}\n")
    (scaffolded_project / ".distignore").write_text("*.json")

    members = run_build(scaffolded_project).members

    assert "top.json" not in members, "top-level *.json must be excluded"
    assert "data/x.json" not in members, "nested *.json must be excluded"
    # The forced deploy-contract re-include must survive (not absorbed by the fusion).
    assert "deploy/install.sh" in members
    assert "README.md" in members


def test_git_dir_never_ships(scaffolded_project, run_build):
    subprocess.run(["git", "init", "-q", "."], cwd=scaffolded_project, check=True, timeout=60)
    append_rules(scaffolded_project, "!.git/")

    members = run_build(scaffolded_project).members

    assert not any(m == ".git" or m.startswith(".git/") for m in members)


def test_staged_dir_mirrors_tarball(scaffolded_project, run_build):
    (scaffolded_project / "data").mkdir()
    (scaffolded_project / "data" / "real.txt").write_text("x\n")
    os.symlink("real.txt", scaffolded_project / "data" / "alias.txt")

    run = run_build(scaffolded_project)

    staged_files, staged_links = set(), set()
    for dirpath, dirnames, filenames in os.walk(run.staged):
        rel = os.path.relpath(dirpath, run.staged)
        for name in filenames + dirnames:
            path = os.path.join(dirpath, name)
            relpath = name if rel == "." else f"{rel}/{name}"
            if os.path.islink(path):
                staged_links.add(relpath)
            elif os.path.isfile(path):
                staged_files.add(relpath)

    assert staged_files == {name for name, info in run.members.items() if info.isfile()}
    assert staged_links == {name for name, info in run.members.items() if info.issym()}
    assert staged_links == {"data/alias.txt"}


def test_empty_dir_semantics(scaffolded_project, run_build):
    (scaffolded_project / "outputs").mkdir()
    (scaffolded_project / "tools").mkdir()
    (scaffolded_project / "tools" / "run.sh").write_text("#!/bin/sh\n")

    members = run_build(scaffolded_project).members

    # A genuinely empty source dir ships ...
    assert "outputs" in members and members["outputs"].isdir()
    # ... but a dir emptied by exclusion (tools/, whose only content is a *.sh
    # file the template excludes) is dropped entirely.
    assert not any(m == "tools" or m.startswith("tools/") for m in members)


# 'folder/*' excludes what is inside the folder, not the folder itself, as in git,
# so a '!' rule can still bring one file back.


def test_one_file_from_a_hidden_folder(scaffolded_project, run_build):
    streamlit = scaffolded_project / ".streamlit"
    streamlit.mkdir()
    (streamlit / "config.toml").write_text("[server]\n")
    (streamlit / "secrets.toml").write_text("token = 'x'\n")
    append_rules(scaffolded_project, "!.streamlit/", ".streamlit/*", "!.streamlit/config.toml")

    members = run_build(scaffolded_project).members

    assert ".streamlit/config.toml" in members
    assert ".streamlit/secrets.toml" not in members


def test_one_file_from_a_folder(scaffolded_project, run_build):
    data = scaffolded_project / "data"
    data.mkdir()
    (data / "keep.txt").write_text("keep\n")
    (data / "other.txt").write_text("other\n")
    append_rules(scaffolded_project, "data/*", "!data/keep.txt")

    members = run_build(scaffolded_project).members

    assert "data/keep.txt" in members
    assert "data/other.txt" not in members


def test_folder_contents_rule_alone_ships_nothing_from_it(scaffolded_project, run_build):
    # Control: with nothing re-included, the emptied folder is dropped, as before.
    data = scaffolded_project / "data"
    data.mkdir()
    (data / "other.txt").write_text("other\n")
    append_rules(scaffolded_project, "data/*")

    members = run_build(scaffolded_project).members

    assert not any(name == "data" or name.startswith("data/") for name in members)
