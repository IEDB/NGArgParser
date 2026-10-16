"""Behavioral tests for the startup check at the end of build.sh.

Before packing, the build runs the entry script's help screens from the build
dir with the project's .venv python and APP_ROOT pointing at the build. A tool
that cannot start fails the build; a project without a .venv or with several
entry scripts skips the check with a notice.
"""

import shutil

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)

# Exits unless the script runs from the build copy with APP_ROOT set to it,
# which proves the check uses the build (not the project) and that the env var
# wins over the project's .env.
APP_ROOT_GUARD = """\
import os as _os, sys as _sys
_here = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _os.environ.get("APP_ROOT") != _here or "/build/" not in _here:
    _sys.exit("startup check did not run from the build copy")
"""


def test_tool_that_starts_passes(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text(APP_ROOT_GUARD + run_script.read_text())

    run = run_build(scaffolded_project)

    assert "Startup check passed." in run.proc.stdout
    assert not any("__pycache__" in name for name in run.members)


def test_tool_that_crashes_fails_the_build(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    run_script = scaffolded_project / "src" / "run_demo.py"
    # At the top: --help exits before anything after the parser runs.
    run_script.write_text('raise SystemExit("boom")\n' + run_script.read_text())

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "The tool does not start from the build dir:" in run.proc.stderr
    assert "src/run_demo.py --help: boom" in run.proc.stderr
    assert not run.tarball.exists()
    assert not run.staged.exists()


def test_missing_dependency_in_build_fails(scaffolded_project, run_build, fake_venv):
    # The import works in the project folder only if the build copied libs/.
    fake_venv(scaffolded_project)
    (scaffolded_project / "scripts" / "deps.sh").write_text("mkdir mydep && echo 'VALUE = 1' > mydep/core.py\n")
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text(
        run_script.read_text().replace("import core.set_pythonpath", "import core.set_pythonpath\nimport mydep.core", 1)
    )

    run_build(scaffolded_project)

    # Keep the fetched copy out of the build: the same script must now fail there.
    with open(scaffolded_project / "scripts" / "hooks.sh", "a") as f:
        f.write('\nrm -rf "$BUILD_DIR/libs/mydep"\n')
    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert "No module named 'mydep'" in run.proc.stderr


def test_no_venv_skips_with_a_notice(scaffolded_project, run_build):
    run = run_build(scaffolded_project)

    assert "Startup check skipped: the project has no .venv/bin/python" in run.proc.stdout


def test_several_entry_scripts_skip_with_a_notice(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    shutil.copy(scaffolded_project / "src" / "run_demo.py", scaffolded_project / "src" / "run_other.py")

    run = run_build(scaffolded_project)

    assert "Startup check skipped: expected exactly one src/run_*.py in the build, found 2." in run.proc.stdout
