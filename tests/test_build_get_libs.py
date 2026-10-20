"""Behavioral tests for the dependency fetch step of build.sh (scripts/get_libs.sh).

get_libs.sh runs in an empty temp dir; its top-level entries replace libs/<entry>
in the project. The step is skipped while get_libs.sh is unchanged and everything
it fetched is still there, and a failed fetch leaves libs/ as it was.
"""

import os
import re
import shutil
import subprocess
import sys
import tarfile
import time

import pytest

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


MARKER = "# Managed by scripts/core/build.sh: entries fetched by scripts/get_libs.sh. Do not edit."


def write_get_libs(project_dir, body):
    (project_dir / "scripts" / "get_libs.sh").write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n")


def stamp(project_dir):
    return (project_dir / ".ngargparser" / "libs-stamp").read_text().splitlines()


def stamp_names(project_dir):
    """The fetched names in the stamp, without their fingerprints."""
    return [line.split(" ", 1)[1] for line in stamp(project_dir)[2:]]


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
    assert lines[0] == "ngargparser-libs-stamp 3"
    assert lines[1].startswith("hash ") and lines[1] != "hash pending"
    assert set(stamp_names(scaffolded_project)) == {"depA", "blob.bin"}
    assert all(re.fullmatch(r"\d+-\d+ \S+", line) for line in lines[2:]), lines
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
    assert stamp_names(scaffolded_project) == ["depA"]


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


def test_tool_version_change_fetches_again(scaffolded_project, run_build):
    runs = scaffolded_project.parent / "runs.log"
    write_get_libs(scaffolded_project, f'echo run >> {runs}\nmkdir dep && echo "$TOOL_VERSION" > dep/version')

    def build(version):
        run = run_build(scaffolded_project, expect_ok=False, env={"TOOL_VERSION": version})
        assert run.proc.returncode == 0, run.proc.stderr
        tarball = scaffolded_project / "build" / f"IEDB_NG_DEMO-{version}.tar.gz"
        with tarfile.open(tarball) as tf:
            return run, tf.extractfile(f"ng_demo-{version}/libs/dep/version").read().decode()

    assert build("1.0.0")[1] == "1.0.0\n"
    assert build("2.0.0")[1] == "2.0.0\n", "the 2.0.0 tarball must not ship what the 1.0.0 fetch got"
    assert stamp(scaffolded_project)[1].endswith(" version 2.0.0")
    run, shipped = build("2.0.0")
    assert shipped == "2.0.0\n"
    assert "Libs up to date" in run.proc.stdout, "the same version again is not fetched again"
    assert runs.read_text().count("run") == 2


@pytest.mark.parametrize("rc", [0, 3])
def test_write_into_libs_dir_fails_and_is_undone(scaffolded_project, run_build, rc):
    write_get_libs(scaffolded_project, "mkdir depA && echo v1 > depA/version")
    run_build(scaffolded_project)
    mine = scaffolded_project / "libs" / "mine"
    mine.mkdir()
    (mine / "code.py").write_text("x = 1\n")
    before_stamp = stamp(scaffolded_project)
    gitignore = (scaffolded_project / "libs" / ".gitignore").read_text()

    write_get_libs(
        scaffolded_project,
        'mkdir -p "$LIBS_DIR/direct" && echo x > "$LIBS_DIR/direct/f"\nmkdir depA && echo v2 > depA/version\n'
        f"exit {rc}",
    )
    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert (
        "ERROR: scripts/get_libs.sh wrote into libs/ directly. Write into the current directory instead: "
        "the build moves each top-level entry left there into libs/ and records it."
    ) in run.proc.stderr
    assert "  libs/direct: new, removed again" in run.proc.stderr
    assert ("scripts/get_libs.sh also failed (exit 3)." in run.proc.stderr) == (rc == 3)
    libs = scaffolded_project / "libs"
    assert sorted(os.listdir(libs)) == [".gitignore", "depA", "mine"], "libs/ is as it was"
    assert (libs / "depA" / "version").read_text() == "v1\n"
    assert (mine / "code.py").read_text() == "x = 1\n"
    assert (libs / ".gitignore").read_text() == gitignore
    assert stamp(scaffolded_project) == before_stamp
    assert not list((scaffolded_project / ".ngargparser").glob("libs-fetch.*"))
    assert not run.tarball.exists()


def test_changes_inside_libs_dir_are_reported(scaffolded_project, run_build):
    libs = scaffolded_project / "libs"
    libs.mkdir()
    (libs / "notes.txt").write_text("mine\n")
    (libs / "old").mkdir()
    # Dated in the future (clock skew, an unpacked archive): newer than the
    # fetch's start marker before the script even runs, so never blamed on it.
    future = libs / "future.txt"
    future.write_text("skewed\n")
    os.utime(future, (time.time() + 3600, time.time() + 3600))
    # Control: reading libs/ is fine.
    write_get_libs(scaffolded_project, 'cat "$LIBS_DIR/notes.txt" > copy.txt\nls "$LIBS_DIR" > listing.txt')
    run = run_build(scaffolded_project, progress=True)
    assert "wrote into libs/ directly" not in run.proc.stderr
    assert (libs / "copy.txt").read_text() == "mine\n"
    before_stamp = stamp(scaffolded_project)

    # The sleep keeps the change out of the clock tick in which the fetch started.
    write_get_libs(
        scaffolded_project, 'sleep 0.1\necho changed >> "$LIBS_DIR/notes.txt"\nrm -r "$LIBS_DIR/old"\nmkdir depA'
    )
    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert "scripts/get_libs.sh wrote into libs/ directly." in run.proc.stderr
    assert "  libs/notes.txt: changed, left as it is now" in run.proc.stderr
    assert "  libs/old: deleted, not restored" in run.proc.stderr
    assert "copy.txt" not in run.proc.stderr and "future.txt" not in run.proc.stderr
    assert sorted(os.listdir(libs)) == [".gitignore", "copy.txt", "future.txt", "listing.txt", "notes.txt"]
    assert stamp(scaffolded_project) == before_stamp


def test_libs_dir_made_by_the_script_is_removed(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, 'mkdir -p "$LIBS_DIR"\nmkdir depA')

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "  libs/: new, removed again" in run.proc.stderr
    assert not (scaffolded_project / "libs").exists()


def test_silent_failure_says_the_log_is_empty(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "exit 3")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert (
        "ERROR: scripts/get_libs.sh failed (exit 3); libs/ was not changed.\n(get_libs.sh printed nothing)\n"
    ) in run.proc.stderr
    assert "--- last lines of .ngargparser/get_libs.log ---" not in run.proc.stderr

    # The same when it also wrote into libs/ directly.
    write_get_libs(scaffolded_project, 'mkdir -p "$LIBS_DIR/direct"\nexit 3')
    run = run_build(scaffolded_project, expect_ok=False, progress=True)
    assert "scripts/get_libs.sh also failed (exit 3).\n(get_libs.sh printed nothing)\n" in run.proc.stderr

    # Control: output, even on stdout only, is still shown.
    write_get_libs(scaffolded_project, "echo 'fetching depA'\nexit 3")
    run = run_build(scaffolded_project, expect_ok=False, progress=True)
    assert "--- last lines of .ngargparser/get_libs.log ---\nfetching depA\n" in run.proc.stderr
    assert "printed nothing" not in run.proc.stderr


def test_deleted_get_libs_sh_leaves_a_notice(scaffolded_project, run_build):
    # Control: a tool that never had a get_libs.sh gets no notice.
    get_libs_sh = scaffolded_project / "scripts" / "get_libs.sh"
    get_libs_sh.unlink()
    run = run_build(scaffolded_project, progress=True)
    assert "no longer exists" not in run.proc.stdout
    write_get_libs(scaffolded_project, "mkdir depA && touch depA/a.py\necho x > blob.bin")
    run_build(scaffolded_project)
    (scaffolded_project / "libs" / "blob.bin").unlink()  # deleted by hand: nothing left to report
    (scaffolded_project / "scripts" / "get_libs.sh").unlink()

    run = run_build(scaffolded_project, progress=True)

    assert (
        "libs/depA was fetched by a get_libs.sh that no longer exists; it still ships. "
        "Delete it, or restore scripts/get_libs.sh.\n"
    ) in run.proc.stdout
    assert "libs/blob.bin" not in run.proc.stdout
    assert "libs/depA/a.py" in run.members
    assert (scaffolded_project / "libs" / "depA" / "a.py").is_file(), "nothing is deleted"
    assert "No scripts/get_libs.sh; nothing to fetch" in run.proc.stdout


# The before-and-after comparison of libs/ reaches every depth, so a write
# inside an entry is caught too, not only a new or changed top-level name.

WROTE_INTO_LIBS = (
    "ERROR: scripts/get_libs.sh wrote into libs/ directly. Write into the current directory instead: "
    "the build moves each top-level entry left there into libs/ and records it."
)


@pytest.fixture
def fetched_dep(scaffolded_project, run_build):
    """A project whose get_libs.sh fetched depA (depA/version, depA/sub/x),
    with a hand-made libs/mine next to it."""
    write_get_libs(scaffolded_project, "mkdir -p depA/sub && echo v1 > depA/version && echo x > depA/sub/x")
    run_build(scaffolded_project)
    mine = scaffolded_project / "libs" / "mine"
    mine.mkdir()
    (mine / "code.py").write_text("x = 1\n")
    return scaffolded_project


@pytest.mark.parametrize(
    "body, line",
    [
        ('echo data > "$LIBS_DIR/depA/data.txt"', "  libs/depA/data.txt: new, removed again"),
        (
            'mkdir -p "$LIBS_DIR/mine/gen/deep" && touch "$LIBS_DIR/mine/gen/deep/f"',
            "  libs/mine/gen: new, removed again",
        ),
        ('echo v2 >> "$LIBS_DIR/depA/version"', "  libs/depA/version: changed, left as it is now"),
        ('rm "$LIBS_DIR/depA/sub/x"', "  libs/depA/sub/x: deleted, not restored"),
        ('rm -r "$LIBS_DIR/depA/sub"', "  libs/depA/sub: deleted, not restored"),
        ('chmod +x "$LIBS_DIR/mine/code.py"', "  libs/mine/code.py: changed, left as it is now"),
    ],
    ids=["new-file", "new-folder", "changed", "deleted-file", "deleted-folder", "mode"],
)
def test_write_inside_a_libs_entry_fails(fetched_dep, run_build, body, line):
    before_stamp = stamp(fetched_dep)
    # The sleep keeps the change out of the clock tick in which the fetch started.
    write_get_libs(
        fetched_dep, f"sleep 0.1\n{body}\nmkdir -p depA/sub && echo v1 > depA/version && echo x > depA/sub/x"
    )

    run = run_build(fetched_dep, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert f"{WROTE_INTO_LIBS}\n{line}\n" in run.proc.stderr
    assert run.proc.stderr.count("\n  libs/") == 1, "one line per change, a new folder stands for its contents"
    assert stamp(fetched_dep) == before_stamp
    assert not (fetched_dep / "libs" / "depA" / "data.txt").exists(), "new paths are removed again"
    assert not (fetched_dep / "libs" / "mine" / "gen").exists()
    assert not run.tarball.exists()


def test_long_list_of_libs_changes_is_cut(fetched_dep, run_build):
    write_get_libs(fetched_dep, 'for i in $(seq 10 34); do touch "$LIBS_DIR/mine/f$i"; done\nmkdir depA')

    run = run_build(fetched_dep, expect_ok=False, progress=True)

    lines = [line for line in run.proc.stderr.splitlines() if line.startswith("  ")]
    assert lines[:2] == ["  libs/mine/f10: new, removed again", "  libs/mine/f11: new, removed again"]
    assert len(lines) == 21
    assert lines[-1] == "  ... and 5 more"
    assert sorted(os.listdir(fetched_dep / "libs" / "mine")) == ["code.py"]


def test_reading_libs_and_bytecode_are_not_changes(fetched_dep, run_build):
    # Control: reading libs/ is fine, and __pycache__ (which a Python run in
    # get_libs.sh may write) is not part of what ships.
    write_get_libs(
        fetched_dep,
        'cat "$LIBS_DIR/depA/version" > copy.txt\nmkdir -p "$LIBS_DIR/depA/__pycache__" && touch "$LIBS_DIR/depA/__pycache__/m.pyc"\n'
        "mkdir -p depA/sub && echo v1 > depA/version",
    )

    run = run_build(fetched_dep, progress=True)

    assert "wrote into libs/ directly" not in run.proc.stderr
    assert (fetched_dep / "libs" / "copy.txt").read_text() == "v1\n"


# A fetched entry edited by hand after the fetch is fetched again: the stamp
# keeps a fingerprint of each entry, and the build looks for files changed
# since the stamp was written.


@pytest.mark.parametrize(
    "edit",
    [
        lambda dep: (dep / "version").write_text("edited\n"),
        lambda dep: (dep / "extra.txt").write_text("new\n"),
        lambda dep: (dep / "sub" / "x").unlink(),
        lambda dep: (dep / "sub" / "x").rename(dep / "sub" / "y"),
    ],
    ids=["edited", "added", "deleted", "renamed"],
)
def test_entry_edited_by_hand_is_fetched_again(scaffolded_project, run_build, edit):
    runs = scaffolded_project.parent / "runs.log"
    write_get_libs(
        scaffolded_project,
        f"echo run >> {runs}\nmkdir -p depA/sub && echo 'x = 1' > depA/mod.py && echo v1 > depA/version && echo x > depA/sub/x",
    )
    run_build(scaffolded_project)
    time.sleep(0.1)  # out of the clock tick in which the stamp was written
    # Control: a run from the tool folder leaves bytecode in libs/, which is
    # not an edit.
    subprocess.run(
        [sys.executable, "-c", "import depA.mod"],
        cwd=scaffolded_project / "libs",
        check=True,
        timeout=60,
        env={k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"},
    )
    assert list((scaffolded_project / "libs").rglob("__pycache__"))
    run = run_build(scaffolded_project, progress=True)
    assert "Libs up to date" in run.proc.stdout
    assert runs.read_text().count("run") == 1

    edit(scaffolded_project / "libs" / "depA")
    run = run_build(scaffolded_project, progress=True)

    assert "libs/depA changed since it was fetched; fetching again" in run.proc.stdout
    assert runs.read_text().count("run") == 2
    dep = scaffolded_project / "libs" / "depA"
    assert (dep / "version").read_text() == "v1\n" and (dep / "sub" / "x").read_text() == "x\n"
    assert not (dep / "extra.txt").exists() and not (dep / "sub" / "y").exists()
    with tarfile.open(run.tarball) as tf:
        assert tf.extractfile("ng_demo-tver/libs/depA/version").read() == b"v1\n"


def test_stamp_from_before_the_fingerprints_is_upgraded(scaffolded_project, run_build):
    runs = scaffolded_project.parent / "runs.log"
    write_get_libs(scaffolded_project, f"echo run >> {runs}\nmkdir depA && echo v1 > depA/version")
    run_build(scaffolded_project)
    # The format 1 stamp an older build.sh wrote: names only.
    stamp_file = scaffolded_project / ".ngargparser" / "libs-stamp"
    lines = stamp(scaffolded_project)
    stamp_file.write_text("\n".join(["ngargparser-libs-stamp 1", lines[1], *stamp_names(scaffolded_project)]) + "\n")

    run = run_build(scaffolded_project, progress=True)

    assert "Libs up to date" in run.proc.stdout, "an upgrade does not fetch again"
    assert runs.read_text().count("run") == 1
    assert stamp(scaffolded_project)[0] == "ngargparser-libs-stamp 3"
    assert stamp_names(scaffolded_project) == ["depA"]
    # The upgraded stamp now catches a hand edit.
    time.sleep(0.1)
    (scaffolded_project / "libs" / "depA" / "version").write_text("edited\n")
    run = run_build(scaffolded_project, progress=True)
    assert "libs/depA changed since it was fetched; fetching again" in run.proc.stdout


# An entry get_libs.sh no longer fetches is removed, and the build says so.
# libs/ is out of git (libs/.gitignore lists it), so one edited since the
# fetch, or with no fingerprint to tell, is moved to .ngargparser/removed-libs/
# instead of deleted.

NO_LONGER = "libs/depB is no longer fetched by scripts/get_libs.sh; removing it.\n"


def removed_libs(project_dir):
    folder = project_dir / ".ngargparser" / "removed-libs"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_dropped_entry_is_named_when_it_is_removed(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir depA depB && echo b > depB/b.py")
    run_build(scaffolded_project)
    time.sleep(0.1)  # out of the clock tick in which the stamp was written
    # Bytecode from a run in the tool folder is not an edit.
    (scaffolded_project / "libs" / "depB" / "__pycache__").mkdir()
    (scaffolded_project / "libs" / "depB" / "__pycache__" / "b.pyc").write_bytes(b"x")

    write_get_libs(scaffolded_project, "mkdir depA")
    run = run_build(scaffolded_project, progress=True)

    assert NO_LONGER in run.proc.stdout and run.proc.stdout.count("is no longer fetched") == 1
    assert "moved to" not in run.proc.stdout
    assert not (scaffolded_project / "libs" / "depB").exists()
    assert removed_libs(scaffolded_project) == []
    # Control: an entry still fetched gets no notice.
    assert "libs/depA" not in run.proc.stdout


@pytest.mark.parametrize(
    "edit",
    [
        lambda dep: (dep / "b.py").write_text("edited\n"),
        lambda dep: (dep / "notes.txt").write_text("mine\n"),
        lambda dep: (dep / "sub" / "x").unlink(),
        lambda dep: ((dep / "b.py").write_text("edited\n"), dep.chmod(0o555)),
    ],
    ids=["edited", "added", "deleted", "read-only"],
)
def test_dropped_entry_with_hand_edits_is_moved_aside(scaffolded_project, run_build, edit):
    write_get_libs(scaffolded_project, "mkdir -p depA depB/sub && echo b > depB/b.py && echo x > depB/sub/x")
    run_build(scaffolded_project)
    time.sleep(0.1)  # out of the clock tick in which the stamp was written
    dep = scaffolded_project / "libs" / "depB"
    edit(dep)
    expected = sorted(str(p.relative_to(dep)) for p in dep.rglob("*"))

    write_get_libs(scaffolded_project, "mkdir depA")
    run = run_build(scaffolded_project, progress=True)

    kept = removed_libs(scaffolded_project)
    assert len(kept) == 1 and re.fullmatch(r"depB-\d{8}-\d{6}", kept[0].name), kept
    moved = (
        "libs/depB is no longer fetched by scripts/get_libs.sh. It changed since it was fetched, so it was moved "
        f"to .ngargparser/removed-libs/{kept[0].name} instead of deleted.\n"
    )
    # One message, the one that says what happened: it was moved, not removed.
    assert moved in run.proc.stdout and NO_LONGER not in run.proc.stdout
    assert run.proc.stdout.count("is no longer fetched") == 1
    assert sorted(str(p.relative_to(kept[0])) for p in kept[0].rglob("*")) == expected
    assert not dep.exists()
    assert "/depB" not in (scaffolded_project / "libs" / ".gitignore").read_text()
    assert not any(name.startswith("libs/depB") for name in run.members)


def test_dropped_entry_without_a_fingerprint_is_moved_aside(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir depA depB && echo b > depB/b.py")
    run_build(scaffolded_project)
    # The stamp is lost: the names come from libs/.gitignore, with no fingerprints.
    (scaffolded_project / ".ngargparser" / "libs-stamp").unlink()

    write_get_libs(scaffolded_project, "mkdir depA")
    run = run_build(scaffolded_project, progress=True)

    kept = removed_libs(scaffolded_project)
    assert [p.name[:5] for p in kept] == ["depB-"]
    moved = (
        f"libs/depB is no longer fetched by scripts/get_libs.sh. It was moved to .ngargparser/removed-libs/"
        f"{kept[0].name} instead of deleted: the build has no record to tell whether it changed since it was "
        "fetched.\n"
    )
    assert moved in run.proc.stdout and NO_LONGER not in run.proc.stdout
    assert run.proc.stdout.count("is no longer fetched") == 1
    assert (kept[0] / "b.py").read_text() == "b\n"
    assert not (scaffolded_project / "libs" / "depB").exists()


def test_git_and_bytecode_folders_in_a_fetched_entry_get_no_init_files(scaffolded_project, run_build):
    write_get_libs(
        scaffolded_project,
        "mkdir -p depG/pkg depG/__pycache__ && git init -q depG && echo 'x = 1' > depG/pkg/mod.py\n"
        "echo x > depG/__pycache__/mod.pyc",
    )

    run = run_build(scaffolded_project)

    dep = scaffolded_project / "libs" / "depG"
    assert (dep / ".git" / "HEAD").is_file(), "get_libs.sh kept its .git"
    # Control: the package folders get theirs.
    assert (dep / "__init__.py").is_file() and (dep / "pkg" / "__init__.py").is_file()
    assert not list((dep / ".git").rglob("__init__.py"))
    assert not (dep / "__pycache__" / "__init__.py").exists()
    assert "libs/depG/pkg/__init__.py" in run.members


# 'git clone <url> .' in get_libs.sh makes its working folder a repository,
# and moved into libs/, its .git made libs/ a repository inside the tool's
# own ("adding embedded git repository: libs"), with exit 0. The build now
# refuses a .git left at the top of that folder and leaves libs/ as it was.


def test_clone_into_the_working_folder_is_refused(scaffolded_project, run_build):
    # Control: a clone into a named folder ships, without its .git.
    write_get_libs(scaffolded_project, "mkdir mylib && git -C mylib init -q && echo 'X = 1' > mylib/x.py")
    first = run_build(scaffolded_project)
    assert "libs/mylib/x.py" in first.members
    assert not [name for name in first.members if "/.git" in name]
    before = sorted(str(p.relative_to(scaffolded_project)) for p in (scaffolded_project / "libs").rglob("*"))
    stamp_before = stamp(scaffolded_project)

    write_get_libs(scaffolded_project, "git init -q . && echo 'X = 1' > x.py")
    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        "ERROR: scripts/get_libs.sh left a .git folder at the top of its working folder (git clone ... .): "
        "clone into a named folder instead, e.g. git clone <url> mylib.\n"
    ) in run.proc.stderr
    assert not (scaffolded_project / "libs" / ".git").exists()
    assert not (scaffolded_project / "libs" / "x.py").exists()
    assert sorted(str(p.relative_to(scaffolded_project)) for p in (scaffolded_project / "libs").rglob("*")) == before
    assert stamp(scaffolded_project) == stamp_before
    assert not list((scaffolded_project / ".ngargparser").glob("libs-fetch.*"))
    assert not run.tarball.exists()


# A hand edit to an entry get_libs.sh still fetches was wiped by the refetch,
# with no copy kept. It is now moved to .ngargparser/removed-libs/ first, as
# a dropped entry is, also when the fetch runs because get_libs.sh changed.


@pytest.mark.parametrize("why", ["edit", "script-changed-too"])
def test_hand_edit_to_an_entry_still_fetched_is_kept(scaffolded_project, run_build, why):
    write_get_libs(scaffolded_project, "mkdir m2 && echo 'VALUE = 1' > m2/helper.py && echo v1 > m2/version")
    run_build(scaffolded_project)
    # Control: an entry fetched again unchanged is not kept.
    write_get_libs(scaffolded_project, "mkdir m2 && echo 'VALUE = 1' > m2/helper.py && echo v1 > m2/version\n# 2")
    run = run_build(scaffolded_project, progress=True)
    assert removed_libs(scaffolded_project) == []
    assert "moved to" not in run.proc.stdout

    helper = scaffolded_project / "libs" / "m2" / "helper.py"
    helper.write_text("VALUE = 1\n# my fix\n")
    (scaffolded_project / "libs" / "m2" / "notes.txt").write_text("mine\n")
    if why == "script-changed-too":
        write_get_libs(scaffolded_project, "mkdir m2 && echo 'VALUE = 1' > m2/helper.py && echo v1 > m2/version\n# 3")
    run = run_build(scaffolded_project, progress=True)

    kept = removed_libs(scaffolded_project)
    assert len(kept) == 1 and re.fullmatch(r"m2-\d{8}-\d{6}", kept[0].name), kept
    assert (
        f"libs/m2 changed since it was fetched, so it was moved to .ngargparser/removed-libs/{kept[0].name} "
        "before the new fetch replaced it.\n"
    ) in run.proc.stdout
    assert (kept[0] / "helper.py").read_text() == "VALUE = 1\n# my fix\n"
    assert (kept[0] / "notes.txt").read_text() == "mine\n"
    # libs/ holds the new fetch, which is what ships.
    assert helper.read_text() == "VALUE = 1\n"
    assert not (scaffolded_project / "libs" / "m2" / "notes.txt").exists()
    with tarfile.open(run.tarball) as tf:
        assert tf.extractfile("ng_demo-tver/libs/m2/helper.py").read() == b"VALUE = 1\n"
    assert not any(name.startswith(".ngargparser/") for name in run.members)


# Changes were found with the files' status change time, so a chmod, or a
# copy of the tool folder (cp -a keeps modification times, not status change
# times), counted as an edit: the entry was fetched again. The fingerprint now
# holds each file's size and modification time.


def test_mode_change_and_a_copy_are_not_edits(scaffolded_project, run_build, tmp_path):
    runs = tmp_path / "runs.log"
    write_get_libs(
        scaffolded_project,
        f"echo run >> {runs}\nmkdir -p depA/sub && echo 'x = 1' > depA/mod.py && echo x > depA/sub/x",
    )
    run_build(scaffolded_project)
    time.sleep(1.1)  # a later second, so a modification time set now would differ

    (scaffolded_project / "libs" / "depA" / "mod.py").chmod(0o755)
    (scaffolded_project / "libs" / "depA" / "sub").chmod(0o700)
    run = run_build(scaffolded_project, progress=True)

    assert "Libs up to date" in run.proc.stdout
    assert "changed since it was fetched" not in run.proc.stdout
    assert runs.read_text().count("run") == 1

    copy = tmp_path / "copy" / "demo"
    copy.parent.mkdir()
    subprocess.run(["cp", "-a", str(scaffolded_project), str(copy)], check=True, timeout=60)
    run = run_build(copy, progress=True)

    assert "Libs up to date" in run.proc.stdout
    assert runs.read_text().count("run") == 1

    # Controls: an edit is one, in the copy too, whether it changes a file's
    # size or, in a later second, only its modification time.
    (copy / "libs" / "depA" / "sub" / "x").write_text("edited\n")
    run = run_build(copy, progress=True)
    assert "libs/depA changed since it was fetched; fetching again" in run.proc.stdout
    assert runs.read_text().count("run") == 2
    time.sleep(1.1)
    (copy / "libs" / "depA" / "sub" / "x").write_text("y\n")
    run = run_build(copy, progress=True)
    assert "libs/depA changed since it was fetched; fetching again" in run.proc.stdout
    assert runs.read_text().count("run") == 3
    assert (copy / "libs" / "depA" / "sub" / "x").read_text() == "x\n"
