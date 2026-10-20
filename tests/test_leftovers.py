"""make from another folder, and what a build killed with kill -9 leaves.

`make -f other/Makefile build` said "scripts/core/build.sh not found. Run
'cli sync'", when the cause is the current folder: make now says to run it
in the tool folder. After kill -9, build/.IEDB_*.tar.gz.partial and the
build's temp folder (/tmp/ngbuild-exclude.*) stayed for good. The temp
folder is now .ngargparser/build-tmp.*, and the next build in the tool
folder removes what a killed build left there and in build/.
"""

import os
import shutil
import signal
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def test_make_from_another_folder_names_the_cause(scaffolded_project, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    makefile = os.path.relpath(scaffolded_project / "Makefile", elsewhere)

    proc = subprocess.run(["make", "-f", makefile, "build"], cwd=elsewhere, capture_output=True, text=True, timeout=60)

    assert proc.returncode != 0
    assert (
        f"ERROR: run make in the tool folder (where Makefile is): cd {os.path.dirname(makefile)} && make build\n"
    ) in proc.stderr
    assert "cli sync" not in proc.stdout + proc.stderr

    # Control: in the tool folder, a missing build.sh still points at cli sync.
    (scaffolded_project / "scripts" / "core" / "build.sh").unlink()
    proc = subprocess.run(["make", "build"], cwd=scaffolded_project, capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "scripts/core/build.sh not found. Run 'cli sync'" in proc.stdout


def test_the_temp_folder_is_in_ngargparser_and_goes_with_the_build(scaffolded_project, run_build, tmp_path):
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    seen = tmp_path / "seen"
    (scaffolded_project / "scripts" / "hooks.sh").write_text(f'ls "$PROJECT_ROOT/.ngargparser" > "{seen}"\n')

    run_build(scaffolded_project, env={"TMPDIR": str(tmpdir)})

    assert any(name.startswith("build-tmp.") for name in seen.read_text().split()), seen.read_text()
    assert not list((scaffolded_project / ".ngargparser").glob("build-tmp.*"))
    assert not list(tmpdir.iterdir())


def test_the_next_build_removes_what_a_killed_build_left(scaffolded_project, run_build, tmp_path):
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    started = tmp_path / "started"
    release = tmp_path / "release"
    (scaffolded_project / "scripts" / "hooks.sh").write_text(
        f'touch "{started}"\nfor i in $(seq 600); do [ -e "{release}" ] && break; sleep 0.1; done\n'
    )
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}
    build = subprocess.Popen(
        ["bash", "scripts/core/build.sh"],
        cwd=scaffolded_project,
        env={**env, "TOOL_VERSION": "tver", "TMPDIR": str(tmpdir)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            assert build.poll() is None, "the build ended before its hook ran"
            assert time.monotonic() < deadline, "the hook did not start"
            time.sleep(0.05)
        os.kill(build.pid, signal.SIGKILL)
        build.wait(timeout=30)
    finally:
        release.touch()
        if build.poll() is None:
            build.kill()
    # What tar was writing when a build was killed, in another version.
    partial = scaffolded_project / "build" / ".IEDB_NG_DEMO-old.tar.gz.partial"
    partial.write_bytes(b"x" * 1000)
    left = [p for p in (scaffolded_project / ".ngargparser").glob("build-tmp.*")] + list(tmpdir.iterdir())
    assert left, "the killed build left no temp folder"

    run = run_build(scaffolded_project, env={"TMPDIR": str(tmpdir)})

    assert not partial.exists()
    assert not list((scaffolded_project / ".ngargparser").glob("build-tmp.*"))
    assert not list(tmpdir.iterdir())
    assert run.tarball.is_file()


def test_a_temp_folder_in_tmpdir_is_removed_only_for_this_tool_folder(scaffolded_project, run_build, tmp_path):
    # Where .ngargparser/ cannot hold it, the temp folder is in TMPDIR, with an
    # owner file: the next build removes only this tool folder's.
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    mine = tmpdir / "ngbuild-exclude.AAAAAA"
    other = tmpdir / "ngbuild-exclude.BBBBBB"
    unmarked = tmpdir / "ngbuild-exclude.CCCCCC"
    for folder, owner in ((mine, scaffolded_project), (other, tmp_path / "other-tool")):
        folder.mkdir()
        (folder / "owner").write_text(f"{owner}\n")
    unmarked.mkdir()

    run_build(scaffolded_project, env={"TMPDIR": str(tmpdir)})

    assert not mine.exists()
    assert other.is_dir() and unmarked.is_dir()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes into a read-only folder")
def test_a_read_only_ngargparser_puts_the_temp_folder_in_tmpdir(scaffolded_project, run_build, tmp_path):
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    seen = tmp_path / "seen"
    run_build(scaffolded_project)  # makes .ngargparser/ with the lock file and the fetch record
    (scaffolded_project / "scripts" / "hooks.sh").write_text(f'cat "$TMPDIR"/ngbuild-exclude.*/owner > "{seen}"\n')
    state = scaffolded_project / ".ngargparser"
    state.chmod(0o555)
    try:
        run_build(scaffolded_project, env={"TMPDIR": str(tmpdir)})
    finally:
        state.chmod(0o755)

    assert seen.read_text() == f"{scaffolded_project}\n"
    assert not list(tmpdir.iterdir())
