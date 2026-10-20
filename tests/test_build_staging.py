"""Behavioral tests for how build.sh copies a project into build/.

The project is copied, never linked: symlinks inside it are copied as links
(the tarball stores them as links too), every link must be relative and stay
inside the build, hooks work on the copy only, and the copied files end up
read-only.
"""

import os
import re
import shutil
import stat
import tarfile
from pathlib import Path

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


def write_hook(project_dir, body):
    (project_dir / "scripts" / "hooks.sh").write_text(f"#!/bin/bash\nset -e\n{body}\n")


def test_project_files_are_copies(scaffolded_project, run_build):
    run = run_build(scaffolded_project)

    for rel in (
        "configure",
        "pyproject.toml",
        "src/run_demo.py",
        "src/core/set_pythonpath.py",
        "deploy/install.sh",
    ):
        path = run.staged / rel
        assert path.is_file() and not path.is_symlink(), f"{rel} must be a copy"
    assert not any(info.issym() for info in run.members.values())


def test_relative_link_is_kept_as_a_link(scaffolded_project, run_build):
    data = scaffolded_project / "data"
    data.mkdir()
    (data / "real.txt").write_text("payload\n")
    os.symlink("real.txt", data / "alias.txt")

    run = run_build(scaffolded_project)

    assert os.readlink(run.staged / "data" / "alias.txt") == "real.txt"
    member = run.members["data/alias.txt"]
    assert member.issym() and member.linkname == "real.txt"


def test_dir_link_under_per_file_staging(scaffolded_project, run_build):
    # A hidden file forces data/ through the per-file path, which used to run a
    # plain `cp` on the directory link and fail with "omitting directory".
    data = scaffolded_project / "data"
    (data / "sub").mkdir(parents=True)
    (data / "sub" / "f.txt").write_text("f\n")
    os.symlink("sub", data / "subalias")
    (data / ".DS_Store").write_text("junk")

    run = run_build(scaffolded_project)

    assert run.members["data/subalias"].issym()
    assert "data/sub/f.txt" in run.members
    assert "data/.DS_Store" not in run.members


def test_dependency_data_is_stored_once(scaffolded_project, run_build):
    dep = scaffolded_project / "libs" / "dep"
    (dep / "data").mkdir(parents=True)
    (dep / "data" / "matrix.bin").write_bytes(b"\x01" * 4096)
    (dep / "bin").mkdir()
    os.symlink("../data", dep / "bin" / "data")

    run = run_build(scaffolded_project)

    assert run.members["libs/dep/bin/data"].issym()
    copies = [name for name in run.members if name.endswith("matrix.bin")]
    assert copies == ["libs/dep/data/matrix.bin"]


@pytest.mark.parametrize(
    "target, reason",
    [
        ("/etc/hostname", "absolute"),
        # build/<tool>/data/bad -> project root, which exists but is outside the build
        ("../../../outside.txt", "points outside the build"),
        ("missing.txt", "dangling or a loop"),
        ("loop", "dangling or a loop"),
    ],
)
def test_link_check_rejects_bad_links(scaffolded_project, run_build, target, reason):
    data = scaffolded_project / "data"
    data.mkdir()
    (scaffolded_project / "outside.txt").write_text("x\n")
    os.symlink(target, data / ("loop" if target == "loop" else "bad"))

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert reason in run.proc.stderr
    assert "data/" in run.proc.stderr
    assert not run.tarball.exists()
    assert not run.staged.exists(), "a failed build removes its build dir"


# git treats a link as a file, so a folder-only rule ('refdata/') never matches
# a link to a folder, and the link check names the rule that does.


def test_link_to_a_folder_says_how_to_exclude_it(scaffolded_project, run_build, tmp_path):
    # Control: a bad link to a file gets no folder hint.
    os.symlink("/etc/hostname", scaffolded_project / "hostlink")
    run = run_build(scaffolded_project, expect_ok=False, progress=True)
    assert "hostlink -> /etc/hostname (absolute)" in run.proc.stderr
    assert "a link to a folder" not in run.proc.stderr
    (scaffolded_project / "hostlink").unlink()
    outside = tmp_path / "shared-refdata"
    outside.mkdir()
    (outside / "matrix.txt").write_text("x\n")
    (scaffolded_project / "data").mkdir()
    os.symlink(outside, scaffolded_project / "data" / "refdata")
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\nrefdata/\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert f"  data/refdata -> {outside} (absolute)\n" in run.proc.stderr
    assert (
        "data/refdata is a link to a folder, which git treats as a file: a .distignore rule must name it "
        "without a trailing slash (/data/refdata, not /data/refdata/).\n"
    ) in run.proc.stderr

    # The rule the message names keeps the link out, and the build passes.
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("/data/refdata\n")
    run = run_build(scaffolded_project)
    assert "data/refdata" not in run.members


def test_link_check_covers_links_made_by_hooks(scaffolded_project, run_build):
    write_hook(scaffolded_project, 'ln -s /etc/hostname "$BUILD_DIR/hostlink"')

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "hostlink -> /etc/hostname (absolute)" in run.proc.stderr


def test_vcs_and_bytecode_are_stripped_from_libs(scaffolded_project, run_build):
    dep = scaffolded_project / "libs" / "dep"
    (dep / ".git").mkdir(parents=True)
    (dep / ".git" / "HEAD").write_text("ref\n")
    (dep / "pkg" / "__pycache__").mkdir(parents=True)
    (dep / "pkg" / "__pycache__" / "m.pyc").write_bytes(b"\x00")
    (dep / "pkg" / "m.py").write_text("x = 1\n")

    members = run_build(scaffolded_project).members

    assert "libs/dep/pkg/m.py" in members
    assert not any("/.git" in name or "__pycache__" in name for name in members)


def test_hooks_patch_the_copy_only(scaffolded_project, run_build):
    project_file = scaffolded_project / "src" / "preprocess.py"
    before = project_file.read_text()
    write_hook(scaffolded_project, 'echo "# patched" >> "$BUILD_DIR/src/preprocess.py"')

    run = run_build(scaffolded_project)

    with tarfile.open(run.tarball) as tf:
        shipped = tf.extractfile(f"{run.staged.name}/src/preprocess.py").read().decode()
    assert shipped.endswith("# patched\n")
    assert project_file.read_text() == before


def test_files_created_by_hooks_ship_as_packages(scaffolded_project, run_build):
    write_hook(scaffolded_project, 'mkdir vendored && echo "x = 1" > vendored/a.py')

    members = run_build(scaffolded_project).members

    assert "libs/vendored/a.py" in members
    assert "libs/vendored/__init__.py" in members


def test_requirements_txt_is_an_ordinary_file(scaffolded_project, run_build):
    content = "requests==2.31.0\ngit+https://invalid.invalid/x.git@v1#egg=x\n"
    (scaffolded_project / "requirements.txt").write_text(content)

    run = run_build(scaffolded_project)

    assert (run.staged / "requirements.txt").read_text() == content
    assert not (run.staged / "libs" / "x").exists(), "git URLs in requirements.txt are no longer cloned"


@pytest.mark.parametrize("legacy", ["build_hooks.sh", "dependencies.sh"])
def test_legacy_hook_name_fails_build(scaffolded_project, run_build, legacy):
    scripts = scaffolded_project / "scripts"
    marker = scaffolded_project.parent / "legacy-hook-ran"
    (scripts / legacy).write_text(f"touch {marker}\n")

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert f"scripts/{legacy} is no longer run" in run.proc.stderr
    assert not marker.exists()


def test_leftover_deps_sh_fails_build(scaffolded_project, run_build):
    marker = scaffolded_project.parent / "deps-sh-ran"
    (scaffolded_project / "scripts" / "deps.sh").write_text(f"touch {marker}\n")

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "scripts/deps.sh is no longer run. Rename it to scripts/get_libs.sh." in run.proc.stderr
    assert not marker.exists()


def test_build_conf_is_read_from_core(scaffolded_project, run_build):
    conf = scaffolded_project / "scripts" / "core" / "build.conf"
    conf.write_text(conf.read_text().replace('TARBALL_PREFIX="IEDB_"', 'TARBALL_PREFIX="LJI_"'))

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode == 0, run.proc.stderr
    assert (scaffolded_project / "build" / "LJI_NG_DEMO-tver.tar.gz").exists()
    assert not run.tarball.exists()


@pytest.mark.parametrize("keep_core_conf", [False, True])
def test_legacy_build_conf_fails_build(scaffolded_project, run_build, keep_core_conf):
    core_conf = scaffolded_project / "scripts" / "core" / "build.conf"
    marker = scaffolded_project.parent / "legacy-build-conf-read"
    if not keep_core_conf:
        core_conf.unlink()
    (scaffolded_project / "scripts" / "build.conf").write_text(f"touch {marker}\n")

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "scripts/build.conf is no longer read" in run.proc.stderr
    assert not marker.exists()


def test_symlinked_libs_fails_before_anything_is_removed(scaffolded_project, run_build, tmp_path):
    elsewhere = tmp_path / "elsewhere-libs"
    elsewhere.mkdir()
    os.symlink(elsewhere, scaffolded_project / "libs")
    previous = scaffolded_project / "build" / "keep-me.txt"
    previous.parent.mkdir()
    previous.write_text("from an earlier build\n")

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "libs/ is a symlink" in run.proc.stderr
    assert previous.exists()


def test_leftover_symlink_setting_is_reported(scaffolded_project, run_build):
    conf = scaffolded_project / "scripts" / "core" / "build.conf"
    conf.write_text(conf.read_text() + 'EXCLUDE_FROM_BUILD_SYMLINK="libs run_*.py"\n')

    run = run_build(scaffolded_project)

    assert "EXCLUDE_FROM_BUILD_SYMLINK in scripts/core/build.conf no longer has any effect" in run.proc.stdout


def test_build_output_is_read_only(scaffolded_project, run_build):
    run = run_build(scaffolded_project)

    for dirpath, dirnames, filenames in os.walk(run.staged):
        assert os.stat(dirpath).st_mode & stat.S_IWUSR, f"{dirpath} must stay writable"
        for name in filenames:
            mode = os.lstat(os.path.join(dirpath, name)).st_mode
            assert not mode & 0o222, f"{name} in {dirpath} must be read-only"
    # Only the build copy: the tarball and the project keep normal permissions.
    for name, info in run.members.items():
        if info.isfile():
            assert info.mode & 0o200, f"{name} must stay writable in the tarball"
    assert os.stat(scaffolded_project / "src" / "run_demo.py").st_mode & stat.S_IWUSR

    # A second build replaces the read-only first one.
    run_build(scaffolded_project)


def test_failed_build_removes_the_previous_tarball(scaffolded_project, run_build):
    first = run_build(scaffolded_project)
    os.symlink("/etc/hostname", scaffolded_project / "hostlink")

    second = run_build(scaffolded_project, expect_ok=False)

    assert second.proc.returncode != 0
    assert not first.tarball.exists(), "a failed build must not leave an older tarball that looks fresh"


@pytest.mark.parametrize("deps", ["none", "fetch", "skip"])
def test_progress_bar_reaches_exactly_100(scaffolded_project, run_build, deps):
    get_libs_sh = scaffolded_project / "scripts" / "get_libs.sh"
    if deps == "none":
        get_libs_sh.unlink()
    else:
        get_libs_sh.write_text("set -e\nmkdir depA && echo 'x = 1' > depA/mod.py\n")
    if deps == "skip":
        run_build(scaffolded_project, progress=True)

    run = run_build(scaffolded_project, progress=True)

    percents = [int(p) for p in re.findall(r"(\d+)%", run.proc.stdout)]
    assert max(percents) == 100
    assert percents.count(100) == 1
    expected = {"none": "No scripts/get_libs.sh", "fetch": "Getting libs", "skip": "Libs up to date"}
    assert expected[deps] in run.proc.stdout
    assert run.proc.stdout.rstrip().endswith(f"Build completed: build/{run.tarball.name}")


def test_build_marker_ships(scaffolded_project, run_build, fake_venv):
    # With the startup check on, so the marker is shown not to disturb it.
    fake_venv(scaffolded_project)
    # A hook that removes or replaces it cannot drop it from the tarball.
    write_hook(
        scaffolded_project, 'rm -f "$BUILD_DIR/.ngargparser-build"\nln -s VERSION "$BUILD_DIR/.ngargparser-build"'
    )

    run = run_build(scaffolded_project)

    marker = run.staged / ".ngargparser-build"
    assert marker.is_file() and not marker.is_symlink()
    assert marker.read_text() == "TOOL_NAME=ng_demo\nTOOL_VERSION=tver\n"
    assert run.members[".ngargparser-build"].isfile()
    assert "Startup check passed." in run.proc.stdout
    assert not (scaffolded_project / ".ngargparser-build").exists(), "only the build copy is marked"


# build.conf may only set variables: an exit in it, or a failing command,
# stops the build with a message instead of ending it silently.


@pytest.mark.parametrize(
    "line, code",
    [("exit 0", 0), ("exit 3", 3), ("false", 1)],
    ids=["exit-0", "exit-3", "failing-command"],
)
def test_build_conf_that_exits_fails(scaffolded_project, run_build, line, code):
    first = run_build(scaffolded_project)
    conf = scaffolded_project / "scripts" / "core" / "build.conf"
    conf.write_text(conf.read_text() + f"{line}\nTOOL_NAME=never_reached\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        f"ERROR: scripts/core/build.conf exited (code {code}) while being read: it may only set variables.\n"
    ) in run.proc.stderr
    assert first.tarball.is_file() and first.staged.is_dir(), "nothing was removed"


# make build-verbose shows each step and the scripts' output, not a trace of
# every command; NGARGPARSER_BUILD_TRACE=1 brings the trace back.


def test_verbose_build_has_no_command_trace(scaffolded_project, run_build):
    write_hook(scaffolded_project, 'echo "hook says hi"')

    run = run_build(scaffolded_project)

    out = run.proc.stdout.splitlines()
    for step in ("Setting up build directory", "Evaluating distignore rules", "Checking symlinks", "Creating tarball"):
        assert step in out
    assert "hook says hi" in out
    trace = [line for line in run.proc.stderr.splitlines() if line.startswith("+")]
    assert not trace, trace[:5]
    assert len(out) + len(run.proc.stderr.splitlines()) < 100

    run = run_build(scaffolded_project, env={"NGARGPARSER_BUILD_TRACE": "1"})
    assert any(line.startswith("+ ") for line in run.proc.stderr.splitlines())
    run = run_build(scaffolded_project, progress=True, env={"NGARGPARSER_BUILD_TRACE": "0"})
    assert not any(line.startswith("+") for line in run.proc.stderr.splitlines())


# build.conf is read in a subshell, and only TOOL_NAME, TOOL_VERSION and
# TARBALL_PREFIX come back, so a variable of the build's own cannot change the
# build: SRC_DIR="../src" made the fetch skip (the tarball shipped without
# what get_libs.sh fetches, exit 0), and PROJECT_ROOT=... wrote .ngargparser/
# somewhere else. Each other variable it sets gets a notice.


@pytest.mark.parametrize("variable", ["SRC_DIR", "PROJECT_ROOT"])
def test_build_conf_cannot_change_the_builds_own_variables(scaffolded_project, run_build, tmp_path, variable):
    elsewhere = tmp_path / "elsewhere"
    value = '"../src"' if variable == "SRC_DIR" else f'"{elsewhere}"'
    conf = scaffolded_project / "scripts" / "core" / "build.conf"
    original = conf.read_text()
    # Control: the settings build.conf may set get no notice.
    conf.write_text(original + 'TOOL_NAME="ng_other"\n')
    run = run_build(scaffolded_project, expect_ok=False, progress=True)
    assert (scaffolded_project / "build" / "IEDB_NG_OTHER-tver.tar.gz").is_file(), run.proc.stderr
    assert "build.conf sets" not in run.proc.stdout
    (scaffolded_project / "scripts" / "get_libs.sh").write_text("mkdir mydep && echo 'X = 1' > mydep/x.py\n")
    conf.write_text(original + f'\n{variable}={value}\nMY_SETTING=1\nTARBALL_PREFIX="LJI_"\n')

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 0, run.proc.stderr
    # TARBALL_PREFIX still comes from build.conf.
    tarball = scaffolded_project / "build" / "LJI_NG_DEMO-tver.tar.gz"
    with tarfile.open(tarball) as tf:
        names = tf.getnames()
    assert "ng_demo-tver/libs/mydep/x.py" in names, "the fetch ran and its result ships"
    assert (scaffolded_project / ".ngargparser" / "libs-stamp").is_file()
    assert not elsewhere.exists()
    notices = [line for line in run.proc.stdout.splitlines() if "scripts/core/build.conf sets" in line]
    assert [line[line.index("Notice:") :] for line in notices] == [
        f"Notice: scripts/core/build.conf sets {name}, which the build does not read "
        "(only TOOL_NAME, TOOL_VERSION and TARBALL_PREFIX)."
        for name in sorted(["MY_SETTING", variable])
    ]


# A link to a file the build leaves out dangles in the build copy, and the link
# check says why instead of "dangling or a loop".


@pytest.mark.parametrize(
    "target, rule, why",
    [
        ("../.secret", None, "points at .secret, which .distignore (or the hidden-file rule) excludes"),
        ("../notes.bak", None, "points at notes.bak, which .distignore (or the hidden-file rule) excludes"),
        (
            "../private/key.txt",
            "/private/",
            "points at private/key.txt, which .distignore (or the hidden-file rule) excludes",
        ),
        ("../.venv/pyvenv.cfg", None, "points at .venv/pyvenv.cfg, which the build never ships"),
    ],
    ids=["hidden", "default-rule", "folder-rule", "venv"],
)
def test_link_to_a_file_left_out_says_why(scaffolded_project, run_build, target, rule, why):
    (scaffolded_project / ".secret").write_text("x\n")
    (scaffolded_project / "notes.bak").write_text("x\n")  # *.bak is a default rule
    (scaffolded_project / "private").mkdir()
    (scaffolded_project / "private" / "key.txt").write_text("x\n")
    (scaffolded_project / ".venv").mkdir()
    (scaffolded_project / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    if rule:
        with open(scaffolded_project / ".distignore", "a") as f:
            f.write(f"\n{rule}\n")
    os.symlink(target, scaffolded_project / "src" / "link")
    # Control: a link that dangles in the tool folder too.
    os.symlink("../missing.txt", scaffolded_project / "src" / "gone")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert f"  src/link -> {target} ({why})\n" in run.proc.stderr
    assert "  src/gone -> ../missing.txt (dangling or a loop)\n" in run.proc.stderr
    assert not run.tarball.exists()


# The README and the pyproject.toml description cli generate writes hold
# placeholders, which would reach the tool's users: the build says so, and
# still builds.

README_NOTICE = "Notice: README still has template placeholders (<{} ...>): fill them in before you deploy."
TODO_NOTICE = "Notice: the description in pyproject.toml still starts with TODO: fill it in before you deploy."


def test_template_placeholders_get_a_notice(scaffolded_project, run_build):
    run = run_build(scaffolded_project, progress=True)

    assert README_NOTICE.format("Describe") in run.proc.stdout
    assert TODO_NOTICE in run.proc.stdout

    # Each placeholder counts: with the first filled in, the next is named.
    readme = scaffolded_project / "README"
    readme.write_text(re.sub(r"<Describe what [^>]*>", "Counts amino acids.", readme.read_text(), count=1))
    run = run_build(scaffolded_project, progress=True)
    assert README_NOTICE.format("Summarize") in run.proc.stdout

    # Control: a README and a description written out get no notice.
    readme.write_text("ng_demo - version TOOL_VERSION\n\nCounts amino acids. <b>Bold</b> <URL> text.\n")
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(
        re.sub(r'(?m)^description = ".*"$', 'description = "Counts amino acids."', pyproject.read_text())
    )
    run = run_build(scaffolded_project, progress=True)
    assert "template placeholders" not in run.proc.stdout
    assert "starts with TODO" not in run.proc.stdout


def test_every_readme_template_placeholder_is_found():
    templates = Path(__file__).resolve().parents[1] / "ngargparser" / "templates"
    readme = (templates / "README").read_text()
    verbs = set(re.findall(r"<([A-Z][a-z]+) [a-z]", readme))
    pattern = re.search(r"/<\(([A-Za-z|]+)\) \[", (templates / "build.sh").read_text())

    assert verbs, "the template README has placeholders"
    assert pattern, "build.sh looks for README placeholders"
    assert verbs <= set(pattern.group(1).split("|"))


# The build writes VERSION at the top of the build copy, which silently
# replaced a VERSION file of the tool's own.


def test_tool_version_file_gets_a_notice(scaffolded_project, run_build):
    notice = "Notice: the build writes VERSION; the tool's own VERSION file is replaced in the tarball."
    # Control: no VERSION of the tool's own, no notice.
    run = run_build(scaffolded_project, progress=True)
    assert notice not in run.proc.stdout
    (scaffolded_project / "VERSION").write_text("9.9.9\n")

    run = run_build(scaffolded_project, progress=True)

    assert notice in run.proc.stdout
    assert (run.staged / "VERSION").read_text().startswith("tver\n")
