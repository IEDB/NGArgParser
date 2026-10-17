"""Behavioral tests for the dependency fetch step of build.sh (scripts/get_libs.sh).

get_libs.sh runs in an empty temp dir; its top-level entries replace libs/<entry>
in the project. The step is skipped while get_libs.sh is unchanged and everything
it fetched is still there, and a failed fetch leaves libs/ as it was.
"""

import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)

MARKER = "# Managed by scripts/core/build.sh: entries fetched by scripts/get_libs.sh. Do not edit."


def write_get_libs(project_dir, body):
    (project_dir / "scripts" / "get_libs.sh").write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n")


def stamp(project_dir):
    return (project_dir / ".ngargparser" / "libs-stamp").read_text().splitlines()


def test_fetch_lands_in_project_libs(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir -p depA/pkg && echo 'x = 1' > depA/pkg/mod.py\necho blob > blob.bin")

    run = run_build(scaffolded_project)

    libs = scaffolded_project / "libs"
    assert (libs / "depA" / "pkg" / "mod.py").is_file()
    assert (libs / "depA" / "__init__.py").is_file(), "project-folder runs need the build's package layout"
    assert (libs / "depA" / "pkg" / "__init__.py").is_file()
    assert (libs / "blob.bin").read_text() == "blob\n"
    gitignore = (libs / ".gitignore").read_text().splitlines()
    assert gitignore[:2] == [MARKER, "/.gitignore"]
    assert set(gitignore[2:]) == {"/depA", "/blob.bin"}
    lines = stamp(scaffolded_project)
    assert lines[0] == "ngargparser-libs-stamp 1"
    assert lines[1].startswith("hash ") and lines[1] != "hash pending"
    assert set(lines[2:]) == {"depA", "blob.bin"}
    assert not list((scaffolded_project / ".ngargparser").glob("libs-fetch.*")), "temp dir must be cleaned up"
    assert "libs/depA/pkg/mod.py" in run.members
    assert "libs/blob.bin" in run.members


def test_unchanged_deps_are_not_fetched_again(scaffolded_project, run_build):
    runs = scaffolded_project.parent / "runs.log"
    write_get_libs(scaffolded_project, f"echo run >> {runs}\nmkdir depA")

    run_build(scaffolded_project)
    second = run_build(scaffolded_project)
    assert runs.read_text().count("run") == 1
    assert "Libs up to date" in second.proc.stdout

    get_libs_sh = scaffolded_project / "scripts" / "get_libs.sh"
    get_libs_sh.write_text(get_libs_sh.read_text() + "# a comment changes the content\n")
    run_build(scaffolded_project)
    assert runs.read_text().count("run") == 2


def test_missing_entry_is_fetched_again(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir depA && touch depA/a.py")
    run_build(scaffolded_project)

    shutil.rmtree(scaffolded_project / "libs" / "depA")
    run_build(scaffolded_project)

    assert (scaffolded_project / "libs" / "depA" / "a.py").is_file()


def test_entry_dropped_from_deps_is_removed(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir depA depB")
    run_build(scaffolded_project)

    write_get_libs(scaffolded_project, "mkdir depB")
    run = run_build(scaffolded_project)

    libs = scaffolded_project / "libs"
    assert not (libs / "depA").exists()
    assert (libs / "depB").is_dir()
    assert "/depA" not in (libs / ".gitignore").read_text()
    assert not any(name.startswith("libs/depA") for name in run.members)


# A lost stamp (libs/ copied into a fresh clone, or .ngargparser/ deleted) falls
# back on the managed libs/.gitignore, which lists the same fetched names.


def test_missing_stamp_falls_back_on_libs_gitignore(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir depA && echo v1 > depA/version")
    run_build(scaffolded_project)
    (scaffolded_project / ".ngargparser" / "libs-stamp").unlink()

    write_get_libs(scaffolded_project, "mkdir depA && echo v2 > depA/version")
    run_build(scaffolded_project)

    assert (scaffolded_project / "libs" / "depA" / "version").read_text() == "v2\n"
    assert stamp(scaffolded_project)[2:] == ["depA"]


def test_missing_stamp_removes_dropped_entries(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir depA depB")
    run_build(scaffolded_project)
    (scaffolded_project / ".ngargparser" / "libs-stamp").unlink()

    write_get_libs(scaffolded_project, "mkdir depA")
    run_build(scaffolded_project)

    assert (scaffolded_project / "libs" / "depA").is_dir()
    assert not (scaffolded_project / "libs" / "depB").exists()


def test_missing_stamp_still_protects_hand_made_entries(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir depA")
    run_build(scaffolded_project)
    hand_made = scaffolded_project / "libs" / "depB"
    hand_made.mkdir()
    (hand_made / "notes.txt").write_text("mine\n")
    (scaffolded_project / ".ngargparser" / "libs-stamp").unlink()

    write_get_libs(scaffolded_project, "mkdir depA depB")
    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "libs/depB already exists and was not fetched by scripts/get_libs.sh" in run.proc.stderr
    assert (hand_made / "notes.txt").read_text() == "mine\n"


@pytest.mark.parametrize("libs_gitignore", ["deleted", "yours"])
def test_no_record_explains_both_cases(scaffolded_project, run_build, libs_gitignore):
    write_get_libs(scaffolded_project, "mkdir depA && echo v1 > depA/version")
    run_build(scaffolded_project)
    (scaffolded_project / ".ngargparser" / "libs-stamp").unlink()
    gitignore = scaffolded_project / "libs" / ".gitignore"
    if libs_gitignore == "deleted":
        gitignore.unlink()
    else:  # a project-owned file without the marker is not a record of fetches
        gitignore.write_text("/depA\n")

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "If you made it yourself, rename it" in run.proc.stderr
    assert "delete libs/depA and build again" in run.proc.stderr
    assert (scaffolded_project / "libs" / "depA" / "version").read_text() == "v1\n"


@pytest.mark.parametrize("progress", [False, True])
def test_failed_fetch_leaves_libs_untouched(scaffolded_project, run_build, progress):
    write_get_libs(scaffolded_project, "mkdir depA && echo v1 > depA/version")
    run_build(scaffolded_project)
    before = stamp(scaffolded_project)

    write_get_libs(scaffolded_project, "mkdir depA && echo v2 > depA/version\necho 'clone failed' >&2\nexit 3")
    run = run_build(scaffolded_project, expect_ok=False, progress=progress)

    assert run.proc.returncode != 0
    assert "scripts/get_libs.sh failed (exit 3); libs/ was not changed." in run.proc.stderr
    assert (scaffolded_project / "libs" / "depA" / "version").read_text() == "v1\n"
    assert stamp(scaffolded_project) == before
    assert not list((scaffolded_project / ".ngargparser").glob("libs-fetch.*"))
    if progress:
        assert "clone failed" in run.proc.stderr, "progress mode shows the end of the deps log"


def test_unowned_entry_is_never_replaced(scaffolded_project, run_build):
    hand_made = scaffolded_project / "libs" / "depA"
    hand_made.mkdir(parents=True)
    (hand_made / "notes.txt").write_text("mine\n")
    write_get_libs(scaffolded_project, "mkdir depA")

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "libs/depA already exists and was not fetched by scripts/get_libs.sh" in run.proc.stderr
    assert (hand_made / "notes.txt").read_text() == "mine\n"


def test_project_owned_libs_gitignore_is_left_alone(scaffolded_project, run_build):
    subprocess.run(["git", "init", "-q", "."], cwd=scaffolded_project, check=True, timeout=60)
    libs = scaffolded_project / "libs"
    libs.mkdir()
    (libs / ".gitignore").write_text("/depB\n")
    write_get_libs(scaffolded_project, "mkdir depA depB")

    run = run_build(scaffolded_project)

    assert (libs / ".gitignore").read_text() == "/depB\n"
    assert "libs/depA was fetched by scripts/get_libs.sh but git does not ignore it" in run.proc.stdout
    assert "libs/depB was fetched" not in run.proc.stdout


def test_template_get_libs_sh_fetches_nothing(scaffolded_project, run_build):
    run = run_build(scaffolded_project)

    assert not (scaffolded_project / "libs").exists()
    assert stamp(scaffolded_project)[2:] == []
    assert "Getting libs with scripts/get_libs.sh" in run.proc.stdout


def test_get_libs_sh_gets_its_env_and_an_empty_cwd(scaffolded_project, run_build):
    write_get_libs(
        scaffolded_project,
        '[ -z "$(ls -A)" ] || { echo "cwd is not empty" >&2; exit 1; }\n'
        'printf "%s\\n" "$PWD" "$PROJECT_ROOT" "$APP_NAME" "$TOOL_NAME" "$TOOL_VERSION" "$SRC_DIR" "$LIBS_DIR" > env.txt',
    )

    run_build(scaffolded_project)

    libs = scaffolded_project / "libs"
    cwd, root, app, tool, version, src_dir, libs_dir = (libs / "env.txt").read_text().splitlines()
    assert cwd.startswith(str(scaffolded_project / ".ngargparser" / "libs-fetch."))
    assert root == str(scaffolded_project)
    assert (app, tool, version) == ("demo", "ng_demo", "tver")
    assert src_dir == str(scaffolded_project / "scripts")
    assert libs_dir == str(libs)


def test_fetched_entries_ship_as_left(scaffolded_project, run_build):
    # .distignore and the hidden-file rule apply to project files only; a
    # dependency's hidden data and shell scripts are part of the dependency.
    write_get_libs(
        scaffolded_project,
        "mkdir -p dep/data dep/bin dep/pkg/__pycache__ dep/.git\n"
        "echo thr > dep/data/.thr\n"
        "echo 'echo hi' > dep/bin/run_stage_1.sh\n"
        "touch dep/pkg/__pycache__/m.pyc dep/.git/HEAD",
    )
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("/libs/*\n")

    members = run_build(scaffolded_project).members

    assert "libs/dep/data/.thr" in members
    assert "libs/dep/bin/run_stage_1.sh" in members
    assert not any("__pycache__" in name or "/.git" in name for name in members)


@pytest.mark.parametrize("name", [".gitignore"])
def test_reserved_top_level_name_is_rejected(scaffolded_project, run_build, name):
    write_get_libs(scaffolded_project, f"touch {name}")

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert f"may not create '{name}'" in run.proc.stderr
    assert not (scaffolded_project / "libs").exists()


def test_interrupted_move_is_redone(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir depA && echo v1 > depA/version")
    run_build(scaffolded_project)
    # What an interrupted run leaves: a pending stamp and a half-moved entry.
    stamp_file = scaffolded_project / ".ngargparser" / "libs-stamp"
    lines = stamp(scaffolded_project)
    stamp_file.write_text("\n".join([lines[0], "hash pending", *lines[2:]]) + "\n")
    shutil.rmtree(scaffolded_project / "libs" / "depA")
    (scaffolded_project / "libs" / "depA").mkdir()

    run_build(scaffolded_project)

    assert (scaffolded_project / "libs" / "depA" / "version").read_text() == "v1\n"
    assert stamp(scaffolded_project)[1] != "hash pending"


def test_read_only_fetched_dirs_are_handled(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir -p dep/sub && touch dep/sub/f && chmod a-w dep/sub")
    run_build(scaffolded_project)

    write_get_libs(scaffolded_project, "mkdir -p dep/sub && touch dep/sub/g && chmod a-w dep/sub")
    run_build(scaffolded_project)

    assert sorted(os.listdir(scaffolded_project / "libs" / "dep" / "sub")) == ["__init__.py", "g"]
