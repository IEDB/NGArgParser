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

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)


def write_hook(project_dir, body):
    (project_dir / "scripts" / "hooks.sh").write_text(f"#!/bin/bash\nset -e\n{body}\n")


def test_project_files_are_copies(scaffolded_project, run_build):
    run = run_build(scaffolded_project)

    for rel in (
        "configure",
        "pyproject.toml",
        "src/run_demo.py",
        "src/core/set_pythonpath.py",
        "scripts/core/build.conf",
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
