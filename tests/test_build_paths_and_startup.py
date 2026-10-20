"""Names with a line break, unreadable folders, conda environments, untracked
output, and a startup check without a .venv.

A name with a line break read as two paths, so $'README\\ntests' shipped tests/
although .distignore excluded it: such a name now stops the build before
anything changes, unless the rules exclude it. A folder the build may not read
stopped every build, even when the rules excluded it, and a mode-000 folder
left in build/ stopped the next build and make clean. A conda environment
(conda-meta/) shipped whole. Output a run wrote into the tool folder shipped
without a word. Without a .venv the startup check was skipped and the tool
shipped unchecked; in CI, a startup venv uv could not make fell back on the
.venv, whose dev group hid a missing dependency.
"""

import os
import shutil
import subprocess

import pytest
from test_build_startup import UV_OFFLINE_ERROR, dev_venv, fake_uv

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or os.name != "posix",
    reason="build.sh tests require bash, git and POSIX file modes",
)

needs_modes = pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads any folder")

# Switch the check on again where the run_build fixture switches it off for a
# tool with no .venv.
CHECK = {"NGARGPARSER_SKIP_STARTUP_CHECK": "0"}


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


# --- 1. a name with a line break ---------------------------------------------------------


def test_a_name_with_a_line_break_that_would_ship_stops_the_build_before_anything_changes(
    scaffolded_project, run_build
):
    (scaffolded_project / "tests" / "secret.txt").write_text("not for users\n")
    first = run_build(scaffolded_project)
    assert "tests/secret.txt" not in first.members
    before = first.tarball.read_bytes()
    (scaffolded_project / "README\ntests").write_text("d\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        "ERROR: These names in the tool folder hold a line break, which the build cannot copy: $'README\\ntests'. "
        "Rename them, or exclude them in .distignore. Nothing was changed."
    ) in run.proc.stderr
    assert run.tarball.read_bytes() == before  # the old tarball stays
    assert (run.staged / "README").is_file()  # and so does the old build copy


def test_a_name_with_a_line_break_the_rules_exclude_never_ships(scaffolded_project, run_build):
    # *.bak (the default .distignore) excludes it; src/ is walked, not copied whole.
    (scaffolded_project / "src" / "notes\n.bak").write_text("old\n")
    (scaffolded_project / "tests" / "a\nb").write_text("in an excluded folder\n")

    run = run_build(scaffolded_project)

    assert not [name for name in run.members if "\n" in name]
    assert "src/run_demo.py" in run.members
    assert "tests" not in run.members


# --- 5. folders the build may not read --------------------------------------------------


@pytest.fixture
def restore_modes():
    """Folders to give their modes back, so pytest can remove them."""
    folders = []
    yield folders
    for folder in folders:
        if os.path.lexists(folder):
            os.chmod(folder, 0o755)


@needs_modes
def test_an_unreadable_folder_the_rules_exclude_is_skipped(scaffolded_project, run_build, restore_modes):
    private = scaffolded_project / "tests" / "private"
    private.mkdir()
    (private / "key").write_text("k\n")
    private.chmod(0)
    restore_modes.append(private)

    run = run_build(scaffolded_project)

    assert not [name for name in run.members if name.startswith("tests")]
    assert "src/run_demo.py" in run.members


@needs_modes
def test_an_unreadable_folder_that_would_ship_stops_the_build_before_the_old_tarball_goes(
    scaffolded_project, run_build, restore_modes
):
    first = run_build(scaffolded_project)
    before = first.tarball.read_bytes()
    data = scaffolded_project / "data"
    data.mkdir()
    (data / "table.tsv").write_text("a\tb\n")
    data.chmod(0)
    restore_modes.append(data)

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        "ERROR: The build may not read these folders, which would ship: data. Make them readable "
        "(chmod -R u+rX <folder>), or exclude them in .distignore. Nothing was changed."
    ) in run.proc.stderr
    assert run.tarball.read_bytes() == before


@needs_modes
def test_a_mode_000_folder_left_in_build_never_wedges_the_build_or_make_clean(
    scaffolded_project, run_build, restore_modes
):
    first = run_build(scaffolded_project)
    stuck = first.staged / "stuck"
    (stuck / "inner").mkdir(parents=True)
    (stuck / "inner" / "file").write_text("x\n")
    (stuck / "inner").chmod(0)
    stuck.chmod(0)
    restore_modes.extend([stuck, stuck / "inner"])  # the outer first: inner is reached through it

    again = run_build(scaffolded_project)
    assert not os.path.lexists(stuck)
    assert "src/run_demo.py" in again.members

    # make clean too, with one in build/ itself.
    other = scaffolded_project / "build" / "other"
    other.mkdir()
    other.chmod(0)
    restore_modes.append(other)
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    proc = subprocess.run(["make", "clean"], cwd=scaffolded_project, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (scaffolded_project / "build").exists()


# --- 4. a conda environment -------------------------------------------------------------


def test_a_conda_environment_never_ships(scaffolded_project, run_build):
    env = scaffolded_project / "condaenv"
    (env / "conda-meta").mkdir(parents=True)
    (env / "conda-meta" / "history").write_text("==> 2026-01-01 <==\n")
    (env / "lib").mkdir()
    (env / "lib" / "libbig.so").write_bytes(b"\0" * 64)
    # Control: a folder with no conda-meta/ follows the rules.
    (scaffolded_project / "data").mkdir()
    (scaffolded_project / "data" / "conda-meta.txt").write_text("not an environment\n")

    run = run_build(scaffolded_project)

    assert not [name for name in run.members if name.startswith("condaenv")]
    assert "data/conda-meta.txt" in run.members
    assert (
        "Notice: condaenv/ holds a conda environment (conda-meta/), which never ships, whatever .distignore "
        "says: the deploy host makes its own .venv with uv sync."
    ) in run.proc.stdout


# --- 6. output written into the tool folder ---------------------------------------------


def test_untracked_output_that_ships_is_named(scaffolded_project, run_build):
    subprocess.run(["git", "init", "-q"], cwd=scaffolded_project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=scaffolded_project, check=True)
    (scaffolded_project / "out").mkdir()
    (scaffolded_project / "out" / "res.json").write_text("{}\n")
    (scaffolded_project / "res.tsv").write_text("a\n")
    (scaffolded_project / "tests" / "scratch.txt").write_text("excluded: tests/ never ships\n")

    run = run_build(scaffolded_project, progress=True)

    assert (
        "Notice: these ship in the tarball, but git does not track them (output written into the tool folder?): "
        "commit them, delete them, or exclude them in .distignore:\n  out\n  res.tsv\n"
    ) in run.proc.stdout
    # What ships does not change.
    assert "out/res.json" in run.members and "res.tsv" in run.members

    # Control: once git tracks them, no notice.
    subprocess.run(["git", "add", "-A"], cwd=scaffolded_project, check=True)
    run = run_build(scaffolded_project, progress=True)
    assert "git does not track them" not in run.proc.stdout


def test_a_tool_folder_outside_git_gets_no_untracked_notice(scaffolded_project, run_build):
    (scaffolded_project / "res.tsv").write_text("a\n")

    run = run_build(scaffolded_project, progress=True)

    assert "git does not track them" not in run.proc.stdout


# --- 3. no .venv ------------------------------------------------------------------------


def test_without_a_venv_uv_makes_the_startup_venv_and_the_check_runs(scaffolded_project, run_build, tmp_path):
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    env, log = fake_uv(tmp_path)

    run = run_build(scaffolded_project, env={**env, **CHECK})

    assert "Startup check passed." in run.proc.stdout
    assert "Startup check skipped" not in run.proc.stdout
    assert "|sync --frozen --no-dev --offline" in log.read_text()


def test_without_a_venv_or_a_way_to_make_one_the_build_stops(scaffolded_project, run_build):
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=CHECK)

    assert run.proc.returncode == 1
    assert (
        "ERROR: The startup check has no Python to run the tool with: the tool folder has no .venv/bin/python, "
        "and the tool has no uv.lock. Run 'uv sync', or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without "
        "the check."
    ) in run.proc.stderr
    assert "Build completed" not in run.proc.stdout
    assert not run.tarball.exists()

    # The switch still builds without the check.
    run = run_build(scaffolded_project, env={"NGARGPARSER_SKIP_STARTUP_CHECK": "1"})
    assert "Startup check skipped: NGARGPARSER_SKIP_STARTUP_CHECK is set." in run.proc.stdout


def test_without_a_venv_a_startup_venv_uv_cannot_make_offline_stops_the_build(scaffolded_project, run_build, tmp_path):
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    env, _ = fake_uv(tmp_path, code=2, output=UV_OFFLINE_ERROR)

    run = run_build(scaffolded_project, expect_ok=False, env={**env, **CHECK})

    assert run.proc.returncode == 1
    assert (
        "ERROR: The startup check has no Python to run the tool with: the tool folder has no .venv/bin/python, "
        "and 'uv sync --frozen --no-dev --offline' could not make .ngargparser/startup-venv (exit 2). "
        "Run 'uv sync', or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check.\n"
        "    error: Failed to fetch:"
    ) in run.proc.stderr
    assert not run.tarball.exists()


# --- 2. CI, and a startup venv uv cannot make -------------------------------------------


def test_in_ci_a_startup_venv_uv_cannot_make_stops_the_build(scaffolded_project, run_build, tmp_path):
    dev_venv(scaffolded_project, tmp_path)
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text("import devonly\n" + run_script.read_text())  # only the dev group has it
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    env, log = fake_uv(tmp_path, code=2, output=UV_OFFLINE_ERROR)

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={**env, "CI": "true"})

    assert run.proc.returncode == 1
    assert "ERROR: The startup check cannot run: CI is set and 'uv sync --frozen --no-dev --offline'" in (
        run.proc.stderr
    )
    assert "Caused by: Network connectivity is disabled" in run.proc.stderr  # uv's reason
    assert "Startup check passed." not in run.proc.stdout
    assert not run.tarball.exists()
    assert log.read_text().count("|sync ") == 2  # offline, then online

    # Control: outside CI the .venv still runs the check, with the notice.
    run = run_build(scaffolded_project, env=env)
    assert "Notice: the startup check uses the tool's .venv" in run.proc.stdout
