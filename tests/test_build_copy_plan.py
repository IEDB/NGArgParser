"""Behavioral tests for how build.sh plans the copy into build/.

A folder with an excluded file inside used to be copied file by file, five
processes per file: 3,000 data files and one old.bak took 16 s instead of
under 1 s. The build now works out what ships once and copies each folder's
entries with one cp, and keeps what the old copy kept: modes, symlinks as
links, timestamps, empty folders, and no folder emptied by exclusion.
"""

import os
import re
import shutil

import pytest
from test_build_lock import find_program

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or os.name != "posix",
    reason="build.sh tests require bash, git and POSIX modes",
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check.
    These tests expect a local build."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


def counting_cp(tmp_path):
    """PATH with a cp first that logs each call, then runs the real cp."""
    real = find_program("cp", os.environ["PATH"])
    assert real, "no cp on PATH"
    bin_dir = tmp_path / "counting-cp"
    bin_dir.mkdir()
    log = bin_dir / "calls.log"
    (bin_dir / "cp").write_text(f'#!/bin/sh\necho call >> "{log}"\nexec "{real}" "$@"\n')
    (bin_dir / "cp").chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}", log


def test_one_excluded_file_does_not_copy_the_folder_file_by_file(scaffolded_project, run_build, tmp_path):
    data = scaffolded_project / "data"
    (data / "sub" / "empty").mkdir(parents=True)
    for i in range(300):
        (data / f"f{i}.txt").write_text(f"{i}\n")
    # The default .distignore excludes *.bak, so data/ is walked entry by entry.
    (data / "old.bak").write_text("old\n")
    (data / "only-bak").mkdir()
    (data / "only-bak" / "y.bak").write_text("y\n")
    run_sh = data / "run.sh"
    run_sh.write_text("#!/bin/sh\n")
    run_sh.chmod(0o755)
    shared = data / "shared.txt"
    shared.write_text("shared\n")
    shared.chmod(0o666)
    os.symlink("f0.txt", data / "alias.txt")
    os.utime(data / "f1.txt", (1_000_000_000, 1_000_000_000))
    (data / "sub").chmod(0o750)
    path, log = counting_cp(tmp_path)

    run = run_build(scaffolded_project, env={"PATH": path})

    calls = log.read_text().count("call\n")
    assert calls <= 10, f"the copy ran cp {calls} times"
    members = run.members
    assert sum(1 for name in members if re.fullmatch(r"data/f\d+\.txt", name)) == 300
    assert "data/old.bak" not in members
    # A folder emptied by exclusion is dropped; an empty one ships.
    assert not any(name == "data/only-bak" or name.startswith("data/only-bak/") for name in members)
    assert members["data/sub/empty"].isdir()
    # Modes, links and times are kept, as cp -RPp keeps them.
    assert members["data/run.sh"].mode & 0o777 == 0o755
    assert members["data/shared.txt"].mode & 0o777 == 0o666
    assert members["data/sub"].mode & 0o777 == 0o750
    assert members["data/alias.txt"].issym() and members["data/alias.txt"].linkname == "f0.txt"
    assert members["data/f1.txt"].mtime == 1_000_000_000
    assert (run.staged / "data" / "f299.txt").read_text() == "299\n"
