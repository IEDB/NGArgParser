"""The untracked notice at every depth, without false names, and the non-root
CI job on more than one Python.

Output written below the top level (src/big_output.tsv) shipped with no
notice, since the notice named top-level paths only. In a git repo whose
get_libs.sh fetches, the notice named libs/ on every build, although git
ignores all it holds, and excluding libs/ in .distignore, as the notice
advised, left it, since the fetched entries ship whatever the rules say. A
tool folder git does not track inside a parent repo was named as '.'. The
non-root CI job, the one place the permission tests run, ran on 3.12 only.
"""

import itertools
import os
import shutil
import subprocess

import pytest
from test_build_get_libs import write_get_libs
from test_build_repo_ci import ci  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or os.name != "posix",
    reason="build.sh tests require bash and git",
)

HEADER = (
    "Notice: these ship in the tarball, but git does not track them (output written into the tool folder?): "
    "commit them, delete them, or exclude them in .distignore:\n"
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)


def untracked_notice(stdout):
    """The lines the untracked notice lists, or None when there is no notice."""
    if HEADER not in stdout:
        return None
    return list(itertools.takewhile(lambda line: line.startswith("  "), stdout.split(HEADER, 1)[1].splitlines()))


def git_tracked(project):
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)


def test_untracked_output_below_the_top_level_is_named(scaffolded_project, run_build):
    git_tracked(scaffolded_project)
    (scaffolded_project / "src" / "big_output.tsv").write_text("a\n")
    (scaffolded_project / "src" / "runs" / "r1").mkdir(parents=True)
    (scaffolded_project / "src" / "runs" / "r1" / "res.json").write_text("{}\n")

    run = run_build(scaffolded_project, progress=True)

    assert untracked_notice(run.proc.stdout) == ["  src/big_output.tsv", "  src/runs"]
    assert "src/big_output.tsv" in run.members  # what ships does not change

    # Following the notice's advice clears it.
    (scaffolded_project / ".distignore").write_text(
        (scaffolded_project / ".distignore").read_text() + "\nsrc/big_output.tsv\nsrc/runs/\n"
    )
    subprocess.run(["git", "add", ".distignore"], cwd=scaffolded_project, check=True)
    run = run_build(scaffolded_project, progress=True)
    assert "git does not track them" not in run.proc.stdout
    assert "src/big_output.tsv" not in run.members


def test_a_long_untracked_list_is_cut_after_ten(scaffolded_project, run_build):
    git_tracked(scaffolded_project)
    for i in range(1, 13):
        (scaffolded_project / f"res{i:02}.tsv").write_text("a\n")

    run = run_build(scaffolded_project, progress=True)

    listed = [f"  res{i:02}.tsv" for i in range(1, 11)]
    assert untracked_notice(run.proc.stdout) == listed + ["  ... and 2 more"]


def test_fetched_libs_get_no_untracked_notice(scaffolded_project, run_build):
    git_tracked(scaffolded_project)
    write_get_libs(scaffolded_project, "mkdir -p depA/pkg && echo 'x = 1' > depA/pkg/mod.py")
    subprocess.run(["git", "add", "-A"], cwd=scaffolded_project, check=True)

    run = run_build(scaffolded_project, progress=True)

    assert "libs/depA/pkg/mod.py" in run.members
    assert "git does not track them" not in run.proc.stdout


def test_a_hand_made_entry_beside_fetched_libs_is_named_and_the_advice_clears_it(scaffolded_project, run_build):
    git_tracked(scaffolded_project)
    write_get_libs(scaffolded_project, "mkdir -p depA && echo 'x = 1' > depA/mod.py")
    subprocess.run(["git", "add", "-A"], cwd=scaffolded_project, check=True)
    (scaffolded_project / "libs" / "mine").mkdir(parents=True)
    (scaffolded_project / "libs" / "mine" / "a.py").write_text("a = 1\n")
    (scaffolded_project / "libs" / "mine" / "b.py").write_text("b = 1\n")

    run = run_build(scaffolded_project, progress=True)

    # Not libs (the build always makes it) nor libs/depA (fetched).
    assert untracked_notice(run.proc.stdout) == ["  libs/mine"]

    (scaffolded_project / ".distignore").write_text((scaffolded_project / ".distignore").read_text() + "\nlibs/mine/\n")
    subprocess.run(["git", "add", ".distignore"], cwd=scaffolded_project, check=True)
    run = run_build(scaffolded_project, progress=True)
    assert "git does not track them" not in run.proc.stdout
    assert "libs/depA/mod.py" in run.members and "libs/mine/a.py" not in run.members


def test_a_tool_folder_untracked_in_a_parent_repo_is_not_named_as_dot(scaffolded_project, run_build):
    subprocess.run(["git", "init", "-q"], cwd=scaffolded_project.parent, check=True)

    run = run_build(scaffolded_project, progress=True)

    assert untracked_notice(run.proc.stdout) is None


# --- the non-root CI job ------------------------------------------------------------------


def test_the_non_root_job_runs_on_the_oldest_and_the_newest_python(ci):  # noqa: F811
    job = ci["pytest-non-root"]

    assert job["image"] == "python:$PYVER-slim"
    versions = [v for entry in job["parallel"]["matrix"] for v in entry["PYVER"]]
    assert {"3.9", "3.14"} <= set(versions), versions
    supported = {v for entry in ci["pytest"]["parallel"]["matrix"] for v in entry["PYVER"]}
    assert set(versions) <= supported, (versions, supported)
