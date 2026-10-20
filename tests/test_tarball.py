"""What the build's preflight refuses, and what never ships.

An unreadable .distignore passed the preflight: sed printed "can't read" three
times, the build failed late, with no ERROR line, and the previous build was
gone. It now stops the preflight, as an unreadable build.conf does.

A temporary file a stopped cli command left next to a file (cli sync's
README.md.ngargparser-restore, after a kill -9 during its undo) shipped in the
next tarball. Those names never ship now, and a notice names them.
"""

import os
import shutil

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


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="root reads any file")
def test_unreadable_distignore_stops_the_preflight(scaffolded_project, run_build):
    first = run_build(scaffolded_project)
    distignore = scaffolded_project / ".distignore"
    distignore.chmod(0o000)
    try:
        run = run_build(scaffolded_project, expect_ok=False, progress=True)
    finally:
        distignore.chmod(0o644)

    assert run.proc.returncode == 1
    assert run.proc.stderr.endswith(
        "ERROR: .distignore cannot be read (Permission denied): run 'chmod a+r .distignore' "
        "(or ask its owner to), then build again.\n"
    )
    assert "can't read" not in run.proc.stderr
    assert "Build failed" not in run.proc.stderr
    assert first.staged.is_dir() and first.tarball.is_file(), "a preflight stop removes nothing"
    # Control: readable again, it builds.
    run_build(scaffolded_project)


LEFTOVERS = [
    ".pyproject.toml.ab3_x9z1.tmp",
    "README.md.ngargparser-restore",
    "deploy/.install.sh.k2j4h6g8.ngargparser-sync",
    "src/core/NGArgumentParser.py.ngargparser-restore",
]
# Names that only look like them, and ship as any other file does.
LOOKALIKES = ["notes.tmp", ".short.ab3.tmp", "README.md.ngargparser-restore.txt", "src/.x.abcdefghi.tmp"]


def test_cli_temporary_files_never_ship(scaffolded_project, run_build):
    for name in LEFTOVERS + LOOKALIKES:
        (scaffolded_project / name).write_text("left over\n")
    (scaffolded_project / ".ngargparser").mkdir(exist_ok=True)
    (scaffolded_project / ".ngargparser" / "state.txt").write_text("build state\n")
    # Not even a rule that asks for them brings them back.
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\n!*.ngargparser-restore\n!.*.tmp\n!.*.ngargparser-sync\n!.ngargparser/\n!.short.ab3.tmp\n")

    run = run_build(scaffolded_project)

    for name in LEFTOVERS:
        assert name not in run.members
        assert not (run.staged / name).exists()
    for name in LOOKALIKES:
        assert name in run.members, name
    assert not [name for name in run.members if name == ".ngargparser" or name.startswith(".ngargparser/")]
    assert (
        "Notice: a cli command that was stopped (such as cli sync) left these temporary files in the tool "
        "folder; they do not ship. Check them, then delete them:\n" + "".join(f"  {name}\n" for name in LEFTOVERS)
    ) in run.proc.stdout
    # They are not .distignore's doing.
    excluded = run.proc.stdout.split("Excluded by .distignore")[1].split("Notice")[0]
    assert "ngargparser-restore" not in excluded
    # The build leaves them in the tool folder: a .ngargparser-restore file may
    # hold the only copy of a file as it was before the sync.
    assert all((scaffolded_project / name).is_file() for name in LEFTOVERS)


def test_fetched_libs_are_not_searched_for_cli_temporary_files(scaffolded_project, run_build):
    (scaffolded_project / "scripts" / "get_libs.sh").write_text(
        "mkdir mydep && echo 'VALUE = 1' > mydep/core.py && echo x > mydep/data.ngargparser-restore\n"
    )

    run = run_build(scaffolded_project)

    assert "libs/mydep/data.ngargparser-restore" in run.members, "fetched entries ship as fetched"
    assert "left these temporary files" not in run.proc.stdout
