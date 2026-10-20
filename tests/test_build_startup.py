"""Behavioral tests for the startup check at the end of build.sh.

Before packing, the build runs the entry script's help screens from the build
dir with the project's .venv python and APP_ROOT pointing at the build. A tool
that cannot start fails the build and shows the end of its stderr. The entry
script is [tool.ngargparser] entry in pyproject.toml when set, else the only
src/run_*.py. A project without a .venv, or with several scripts and no entry,
skips the check with a notice, except that in CI (CI set) both fail the build.
NGARGPARSER_SKIP_STARTUP_CHECK=1 skips the check anywhere.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import time

import pytest
from test_build_lock import find_program, path_without_uv
from test_build_robustness import process_alive

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
    (scaffolded_project / "scripts" / "get_libs.sh").write_text("mkdir mydep && echo 'VALUE = 1' > mydep/core.py\n")
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


def test_no_venv_and_no_uv_lock_stops_the_build(scaffolded_project, run_build):
    # It used to skip the check with a notice, and ship a tool no one saw start.
    run = run_build(scaffolded_project, expect_ok=False, env={"NGARGPARSER_SKIP_STARTUP_CHECK": "0"})

    assert run.proc.returncode == 1
    assert (
        "ERROR: The startup check has no Python to run the tool with: the tool folder has no .venv/bin/python, "
        "and the tool has no uv.lock. Run 'uv sync', or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without "
        "the check."
    ) in run.proc.stderr
    assert not run.tarball.exists()


def set_entry(project_dir, line):
    """Put `line` (such as 'entry = "src/run_x.py"') under [tool.ngargparser] in
    pyproject.toml, replacing any entry line there; None removes it."""
    pyproject = project_dir / "pyproject.toml"
    lines = [x for x in pyproject.read_text().splitlines() if not re.match(r"\s*entry\s*=", x)]
    if line is not None:
        lines.insert(lines.index("[tool.ngargparser]") + 1, line)
    pyproject.write_text("\n".join(lines) + "\n")


ENTRY_HINT = 'Set entry = "src/run_<name>.py" under [tool.ngargparser] in pyproject.toml to name the entry script'


def test_several_entry_scripts_skip_with_a_notice(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    set_entry(scaffolded_project, None)
    shutil.copy(scaffolded_project / "src" / "run_demo.py", scaffolded_project / "src" / "run_other.py")

    run = run_build(scaffolded_project)

    assert (
        f"Startup check skipped: expected exactly one src/run_*.py in the build, found 2. {ENTRY_HINT}."
    ) in run.proc.stdout


def test_entry_in_pyproject_picks_the_script(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    src = scaffolded_project / "src"
    (src / "run_other.py").write_text('raise SystemExit("run_other ran")\n' + (src / "run_demo.py").read_text())
    set_entry(scaffolded_project, 'entry = "src/run_other.py"')

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert "src/run_other.py --help: run_other ran" in run.proc.stderr
    assert not run.tarball.exists()

    # Single quotes and a comment work too; CI no longer needs a single script.
    set_entry(scaffolded_project, "entry = 'src/run_demo.py'  # the entry script")
    run = run_build(scaffolded_project, env={"CI": "true"})
    assert "Startup check passed." in run.proc.stdout


def test_ci_with_several_scripts_and_no_entry_fails(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    set_entry(scaffolded_project, None)
    shutil.copy(scaffolded_project / "src" / "run_demo.py", scaffolded_project / "src" / "run_other.py")

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={"CI": "true"})

    assert run.proc.returncode != 0
    assert (
        "ERROR: The startup check cannot run: CI is set and the build has 2 src/run_*.py scripts. "
        f"{ENTRY_HINT}, or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check."
    ) in run.proc.stderr
    assert not run.tarball.exists()

    run = run_build(scaffolded_project, env={"CI": "true", "NGARGPARSER_SKIP_STARTUP_CHECK": "1"})
    assert "Startup check skipped: NGARGPARSER_SKIP_STARTUP_CHECK is set." in run.proc.stdout


@pytest.mark.parametrize("skip", [False, True], ids=["checked", "skip-flag"])
def test_entry_naming_a_missing_file_fails(scaffolded_project, run_build, skip):
    env = {"NGARGPARSER_SKIP_STARTUP_CHECK": "1"} if skip else {}
    # Control: only [tool.ngargparser] entry counts.
    set_entry(scaffolded_project, None)
    with open(scaffolded_project / "pyproject.toml", "a") as f:
        f.write('\n[tool.other]\nentry = "src/run_gone.py"\n')
    run_build(scaffolded_project, env=env)

    # No .venv: the file must exist even when the check itself would not run.
    set_entry(scaffolded_project, 'entry = "src/run_gone.py"')

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode != 0
    assert (
        'ERROR: entry = "src/run_gone.py" under [tool.ngargparser] in pyproject.toml names a file that does not exist.'
    ) in run.proc.stderr
    # A preflight stop: the build from the control run above stays.
    assert run.tarball.is_file() and run.staged.is_dir()
    assert "Build failed" not in run.proc.stderr


def test_entry_excluded_by_distignore_fails(scaffolded_project, run_build):
    shutil.copy(scaffolded_project / "src" / "run_demo.py", scaffolded_project / "src" / "run_other.py")
    set_entry(scaffolded_project, 'entry = "src/run_demo.py"')
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\n/src/run_demo.py\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert (
        "ERROR: src/run_demo.py is excluded by .distignore, so the tarball would have no entry script. "
        "Remove the rule that excludes it."
    ) in run.proc.stderr


# The entry is checked in the preflight, so a typo or a rule that drops it
# stops the build before the previous build copy and tarball are removed.


@pytest.mark.parametrize(
    "line, rule, message",
    [
        (
            'entry = "src/run_dmeo.py"',
            None,
            'entry = "src/run_dmeo.py" under [tool.ngargparser] in pyproject.toml names a file that does not exist.',
        ),
        ('entry = "src"', None, 'entry = "src" under [tool.ngargparser] in pyproject.toml names a folder, not a file.'),
        (
            'entry = "src/run_demo.py"',
            "run_*.py",
            "src/run_demo.py is excluded by .distignore, so the tarball would have no entry script.",
        ),
        (
            'entry = "src/app/run_app.py"',
            "app/",
            "src/app/run_app.py is excluded by .distignore, so the tarball would have no entry script.",
        ),
        (
            'entry = "../x/run.py"',
            None,
            'entry = "../x/run.py" under [tool.ngargparser] in pyproject.toml must be a path inside the tool folder, such as src/run_<name>.py.',
        ),
    ],
    ids=["typo", "folder", "excluded", "excluded-folder", "outside"],
)
def test_bad_entry_stops_before_anything_is_removed(scaffolded_project, run_build, line, rule, message):
    app = scaffolded_project / "src" / "app"
    app.mkdir()
    shutil.copy(scaffolded_project / "src" / "run_demo.py", app / "run_app.py")
    first = run_build(scaffolded_project)
    set_entry(scaffolded_project, line)
    if rule:
        with open(scaffolded_project / ".distignore", "a") as f:
            f.write(f"\n{rule}\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert f"ERROR: {message}" in run.proc.stderr
    assert first.tarball.is_file() and first.staged.is_dir(), "the previous build stays"
    assert "Build failed" not in run.proc.stderr


@pytest.mark.parametrize(
    "line, message",
    [
        ('entry = ["src/run_demo.py"]', 'must be a quoted path, such as entry = "src/run_<name>.py".'),
        ('entry = "../demo/src/run_demo.py"', "must be a path inside the tool folder, such as src/run_<name>.py."),
        ('entry = "/etc/hostname"', "must be a path inside the tool folder, such as src/run_<name>.py."),
    ],
)
def test_entry_that_is_not_a_path_in_the_tool_fails(scaffolded_project, run_build, line, message):
    set_entry(scaffolded_project, line)

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert message in run.proc.stderr


def test_ci_without_venv_fails_the_build(scaffolded_project, run_build, fake_venv):
    # Control: outside CI the build stops too, with the local hint.
    run = run_build(scaffolded_project, expect_ok=False, env={"NGARGPARSER_SKIP_STARTUP_CHECK": "0"})
    assert "ERROR: The startup check has no Python to run the tool with" in run.proc.stderr

    # CI=false is a local build too (read like the NGARGPARSER_* switches).
    run = run_build(scaffolded_project, expect_ok=False, env={"CI": "false"})
    assert "ERROR: The startup check has no Python to run the tool with" in run.proc.stderr

    run = run_build(scaffolded_project, expect_ok=False, env={"CI": "true"})

    assert run.proc.returncode != 0
    assert (
        "ERROR: The startup check cannot run: CI is set and the tool folder has no .venv/bin/python. "
        "Run 'uv sync' before 'make build' in the CI job, or set NGARGPARSER_SKIP_STARTUP_CHECK=1 "
        "to build without the check."
    ) in run.proc.stderr
    assert not run.tarball.exists()
    assert not run.staged.exists()

    run = run_build(scaffolded_project, env={"CI": "true", "NGARGPARSER_SKIP_STARTUP_CHECK": "1"})
    assert "Startup check skipped: NGARGPARSER_SKIP_STARTUP_CHECK is set." in run.proc.stdout

    fake_venv(scaffolded_project)
    run = run_build(scaffolded_project, env={"CI": "true"})
    assert "Startup check passed." in run.proc.stdout


def test_skip_flag_skips_the_check(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text('raise SystemExit("boom")\n' + run_script.read_text())

    run = run_build(scaffolded_project, progress=True, env={"NGARGPARSER_SKIP_STARTUP_CHECK": "1"})

    assert "Startup check skipped: NGARGPARSER_SKIP_STARTUP_CHECK is set." in run.proc.stdout
    assert "src/run_demo.py" in run.members

    # 0 switches it off again, as for the cli's NGARGPARSER_* switches.
    run = run_build(scaffolded_project, expect_ok=False, env={"NGARGPARSER_SKIP_STARTUP_CHECK": "0"})
    assert "src/run_demo.py --help: boom" in run.proc.stderr

    # The flag skips running the tool, not the check that the tarball has its entry script.
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\nsrc/run_*.py\n")
    run = run_build(scaffolded_project, expect_ok=False, env={"NGARGPARSER_SKIP_STARTUP_CHECK": "1"})
    assert "src/run_demo.py is excluded by .distignore" in run.proc.stderr


def test_failure_shows_the_end_of_stderr(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    (scaffolded_project / "src" / "broken_helper.py").write_text("def broken(:\n    pass\n")
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text("import broken_helper\n" + run_script.read_text())

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    err = run.proc.stderr
    assert "The tool does not start from the build dir:" in err
    assert "src/run_demo.py --help: SyntaxError: " in err
    # The traceback names the file, indented under the summary line.
    assert re.search(r'^      File ".*/build/ng_demo-tver/src/broken_helper\.py", line 1$', err, re.M)
    assert "\n    Traceback (most recent call last):\n" in err
    # The other three help screens fail the same way and do not repeat it:
    # one line each.
    for screen in ("predict", "preprocess", "postprocess"):
        assert re.search(rf"^src/run_demo\.py {screen} --help: SyntaxError: .* \(same error as above\)$", err, re.M), (
            err
        )
    assert err.count("(same error as above)") == 3
    assert err.count("Traceback (most recent call last)") == 1


def test_short_stderr_is_shown_once(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    run_script = scaffolded_project / "src" / "run_demo.py"
    original = run_script.read_text()
    run_script.write_text('import sys\nsys.stderr.write("first line\\n")\nraise SystemExit("boom")\n' + original)

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert "src/run_demo.py --help: boom\n    first line\n    boom\n" in run.proc.stderr

    # A one-line error is the summary line alone, not repeated below it.
    run_script.write_text('raise SystemExit("boom")\n' + original)
    run = run_build(scaffolded_project, expect_ok=False, progress=True)
    assert "src/run_demo.py --help: boom\n" in run.proc.stderr
    assert "    boom" not in run.proc.stderr


# The check runs the tool as a deploy host would: a PYTHONPATH (or
# PYTHONHOME, PYTHONSTARTUP) from the shell that runs the build does not
# reach it, so it cannot find a module the tarball lacks somewhere else.


def test_inherited_pythonpath_does_not_hide_a_missing_module(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    src = scaffolded_project / "src"
    assert "import validators" in (src / "run_demo.py").read_text()
    # Control: the tool starts with its own validators.py, PYTHONPATH or not.
    run = run_build(scaffolded_project, env={"PYTHONPATH": str(src)})
    assert "Startup check passed." in run.proc.stdout

    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\n/src/validators.py\n")
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={"PYTHONPATH": str(src)})

    assert run.proc.returncode != 0
    assert "src/run_demo.py --help: ModuleNotFoundError: No module named 'validators'" in run.proc.stderr
    assert not run.tarball.exists()


# The deploy installs the tool without its dev group (uv sync --frozen
# --no-dev), so the startup check runs in .ngargparser/startup-venv, synced the
# same way, instead of the tool's .venv, which `uv sync` fills with the dev
# group too: a module only the dev group brings (pygments, through pytest)
# passed the check and failed at deploy.


def dev_venv(project_dir, tmp_path):
    """A .venv/bin/python that also finds the module devonly, as a .venv
    synced with the dev group finds what only that group installs."""
    dev = tmp_path / "dev-packages"
    dev.mkdir()
    (dev / "devonly.py").write_text("X = 1\n")
    python = project_dir / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.unlink(missing_ok=True)  # never write through a link into a real interpreter
    python.write_text(f'#!/bin/sh\nPYTHONPATH="{dev}" exec "{sys.executable}" "$@"\n')
    python.chmod(0o755)


def fake_uv(tmp_path, code=0, output=""):
    """A stand-in uv first on PATH. `uv sync` logs its folder,
    UV_PROJECT_ENVIRONMENT and arguments, prints `output`, and with code 0
    makes that environment's bin/python (the test interpreter, without
    devonly), as uv sync --no-dev does. Every other command exits 0."""
    bin_dir = tmp_path / "fake-uv"
    bin_dir.mkdir()
    log = bin_dir / "sync.log"
    (bin_dir / "out.txt").write_text(output)
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        '[ "$1" = sync ] || exit 0\n'
        f'echo "$PWD|$UV_PROJECT_ENVIRONMENT|$*" >> "{log}"\n'
        f'cat "{bin_dir / "out.txt"}" >&2\n'
        f"[ {code} -eq 0 ] || exit {code}\n"
        'mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
        # Never through a link (a real uv's venv): that would overwrite the interpreter.
        'rm -f "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        f'printf \'#!/bin/sh\\nexec "%s" "$@"\\n\' "{sys.executable}" > "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        'chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
    )
    uv.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, log


def test_startup_check_leaves_out_the_dev_group(scaffolded_project, run_build, tmp_path):
    dev_venv(scaffolded_project, tmp_path)
    env, log = fake_uv(tmp_path)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    run_script = scaffolded_project / "src" / "run_demo.py"
    original = run_script.read_text()
    run_script.write_text("import devonly\n" + original)

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode != 0
    assert "src/run_demo.py --help: ModuleNotFoundError: No module named 'devonly'" in run.proc.stderr
    assert not run.tarball.exists()
    venv = scaffolded_project / ".ngargparser" / "startup-venv"
    # Synced from the build copy, as the deploy syncs the extracted tarball.
    assert log.read_text() == f"{run.staged}|{venv}|sync --frozen --no-dev --offline --reinstall-package demo\n"

    # Control: a tool that needs no dev-only module starts, from the build copy
    # with APP_ROOT set to it, in the same environment synced again (quick
    # when nothing changed). In CI too.
    run_script.write_text(APP_ROOT_GUARD + original)
    run = run_build(scaffolded_project, env={**env, "CI": "true"})
    assert "Startup check environment: .ngargparser/startup-venv (uv sync --frozen --no-dev)." in run.proc.stdout
    assert "Startup check passed." in run.proc.stdout
    assert log.read_text().count("sync --frozen --no-dev --offline") == 2
    assert not [name for name in run.members if name.startswith(".ngargparser/")]


# The same with the real uv, offline from its cache: pytest, in the dev group
# of every generated tool, brings pygments, which the deploy leaves out.
# Opt-in, as in test_install_real_uv.py: set NGARGPARSER_REAL_UV=1.


@pytest.mark.skipif(os.environ.get("NGARGPARSER_REAL_UV") != "1", reason="opt-in: set NGARGPARSER_REAL_UV=1")
def test_real_uv_startup_check_finds_a_dev_only_import(scaffolded_project, run_build):
    uv = find_program("uv", os.environ["PATH"])
    if uv is None:
        pytest.skip("needs uv")
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "CI")}
    env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")

    def run_uv(*args):
        return subprocess.run([uv, *args], cwd=scaffolded_project, env=env, capture_output=True, text=True, timeout=300)

    for args in (["lock"], ["sync", "--frozen"]):
        proc = run_uv(*args)
        if proc.returncode != 0:
            pytest.skip(f"uv {args[0]} cannot run offline here: {proc.stderr.strip().splitlines()[-1:]}")
    venv_python = scaffolded_project / ".venv" / "bin" / "python"
    if subprocess.run([str(venv_python), "-c", "import pygments"], capture_output=True).returncode != 0:
        pytest.skip("the dev group brings no pygments here")
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text("import pygments\n" + run_script.read_text())

    run = run_build(scaffolded_project, expect_ok=False, env={"UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never"})

    assert run.proc.returncode != 0
    assert "src/run_demo.py --help: ModuleNotFoundError: No module named 'pygments'" in run.proc.stderr
    assert "Startup check environment: .ngargparser/startup-venv (uv sync --frozen --no-dev)." in run.proc.stdout


FALLBACK = "Notice: the startup check uses the tool's .venv, so packages only its dev group installs were not left out"
UV_OFFLINE_ERROR = (
    "Using CPython 3.11.15\n"
    "error: Failed to fetch: `https://files.pythonhosted.org/packages/python_dotenv-1.2.4-py3-none-any.whl`\n"
    "  Caused by: Network connectivity is disabled, but the requested data wasn't found in the cache\n"
)


@pytest.mark.parametrize("case", ["no-uv", "no-uv-lock", "sync-fails"])
def test_startup_check_without_a_startup_venv_uses_the_venv_with_a_notice(
    scaffolded_project, run_build, tmp_path, case
):
    dev_venv(scaffolded_project, tmp_path)
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text("import devonly\n" + run_script.read_text())  # found in the .venv
    if case == "no-uv":
        path = path_without_uv()
        if path is None:
            pytest.skip("cannot hide uv here")
        env = {"PATH": path}
        (scaffolded_project / "uv.lock").write_text("version = 1\n")
        expected = f"{FALLBACK} (uv is not on the PATH).\n"
    elif case == "no-uv-lock":
        env, _ = fake_uv(tmp_path)
        expected = f"{FALLBACK} (the tool has no uv.lock).\n"
    else:
        env, log = fake_uv(tmp_path, code=2, output=UV_OFFLINE_ERROR)
        (scaffolded_project / "uv.lock").write_text("version = 1\n")
        uv_says = (
            "    error: Failed to fetch: `https://files.pythonhosted.org/packages/python_dotenv-1.2.4-py3-none-any.whl`\n"
            "      Caused by: Network connectivity is disabled, but the requested data wasn't found in the cache\n"
        )
        offline = "'uv sync --frozen --no-dev --offline' could not make .ngargparser/startup-venv (exit 2)"
        # Outside CI, only the offline sync is tried.
        run = run_build(scaffolded_project, env=env)
        assert f"{FALLBACK}: {offline}:\n{uv_says}" in run.proc.stdout
        assert "Startup check passed." in run.proc.stdout
        assert log.read_text().count("|sync ") == 1
        # In CI, once online too, then a stop naming both and uv's reason:
        # the .venv holds the dev group too, so it would pass `import devonly`.
        run = run_build(scaffolded_project, expect_ok=False, env={**env, "CI": "true"})
        assert run.proc.returncode == 1
        assert (
            f"ERROR: The startup check cannot run: CI is set and {offline}, and neither could "
            "'uv sync --frozen --no-dev' (exit 2). Give the CI job network access or uv's cache, or set "
            f"NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check. uv said:\n{uv_says}"
        ) in run.proc.stderr
        assert FALLBACK not in run.proc.stdout
        assert not run.tarball.exists()
        return

    # In CI too: the .venv is a way to run the check.
    run = run_build(scaffolded_project, env={**env, "CI": "true"})

    assert expected in run.proc.stdout
    assert "Startup check passed." in run.proc.stdout


# A kill of the build during the startup check waited for the help screen
# running then (60 s timeout each): they now run in the background, and stop
# with the build.


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX signals")
def test_kill_stops_the_startup_check_at_once(scaffolded_project, fake_venv, tmp_path):
    fake_venv(scaffolded_project)
    started = tmp_path / "help-started"
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text(
        "import os, time\n"
        'if "/build/" in __file__:\n'
        f"    open({str(started)!r}, 'w').write(str(os.getpid()))\n"
        "    time.sleep(60)\n" + run_script.read_text()
    )
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}

    proc = subprocess.Popen(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env={**env, "TOOL_VERSION": "tver"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not (started.exists() and started.read_text()):
            assert proc.poll() is None, "the build ended before the startup check ran"
            assert time.monotonic() < deadline, "the startup check did not start"
            time.sleep(0.05)
        sent = time.monotonic()
        proc.send_signal(signal.SIGTERM)  # the build alone, as `kill <pid>` does
        _, stderr = proc.communicate(timeout=20)
        took = time.monotonic() - sent
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    assert proc.returncode == 143, stderr
    assert took < 10, f"the build waited {took:.1f}s for the help screen"
    assert "Build failed" in stderr
    help_pid = int(started.read_text())
    deadline = time.monotonic() + 10
    while process_alive(help_pid):
        assert time.monotonic() < deadline, "the help screen still runs"
        time.sleep(0.05)


# entry may be written as an inline table: the build reads pyproject.toml as
# TOML where a Python that reads it is found, and it ignored that form.


def reads_toml(python):
    probe = "try:\n    import tomllib\nexcept ImportError:\n    import tomli\n"
    return subprocess.run([python, "-I", "-c", probe], capture_output=True, timeout=60).returncode == 0


@pytest.mark.parametrize("form", ["under-tool", "dotted-key"])
def test_entry_in_an_inline_table_picks_the_script(scaffolded_project, run_build, fake_venv, form):
    if not reads_toml(sys.executable):
        pytest.skip("the test interpreter reads no TOML (no tomllib or tomli)")
    fake_venv(scaffolded_project)  # the first Python the build tries for TOML
    src = scaffolded_project / "src"
    (src / "run_other.py").write_text('raise SystemExit("run_other ran")\n' + (src / "run_demo.py").read_text())
    pyproject = scaffolded_project / "pyproject.toml"
    text = re.sub(r"(?m)^\[tool\.ngargparser\]\n(?:[^\[\n].*\n|\n)*", "", pyproject.read_text())
    assert "[tool.ngargparser]" not in text and "entry" not in text
    if form == "under-tool":
        text += '\n[tool]\nngargparser = { app_name = "demo", entry = "src/run_other.py" }\n'
    else:
        text = 'tool.ngargparser = { app_name = "demo", entry = "src/run_other.py" }\n' + text
    pyproject.write_text(text)

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert "src/run_other.py --help: run_other ran" in run.proc.stderr
    assert "Startup check skipped" not in run.proc.stdout


# entry = "" was taken for no entry, and the build picked the only
# src/run_*.py. It now stops the preflight, whichever way pyproject.toml is
# read: as TOML, or line by line where no Python reads TOML.


@pytest.mark.parametrize("reader", ["toml", "line-by-line"])
@pytest.mark.parametrize("line", ['entry = ""', "entry = ''"])
def test_empty_entry_stops_the_build(scaffolded_project, run_build, tmp_path, reader, line):
    env = {}
    if reader == "line-by-line":
        from test_build_pyproject import stand_ins

        path, _ = stand_ins(tmp_path, "no-toml-reader", python="none", uv=("", 2))
        env = {"PATH": path}
    first = run_build(scaffolded_project, env=env)
    set_entry(scaffolded_project, line)

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode == 1
    assert (
        'ERROR: entry = "" under [tool.ngargparser] in pyproject.toml is empty: '
        "name the entry script, or delete the line.\n"
    ) in run.proc.stderr
    assert first.tarball.is_file() and first.staged.is_dir(), "a preflight stop removes nothing"
    # Control: with the line deleted, the only src/run_*.py is the entry again.
    set_entry(scaffolded_project, None)
    run_build(scaffolded_project, env=env)


# A file the help screens write while the module loads (a log opened at
# import) landed in the build copy, and shipped.


def test_files_the_help_screens_write_do_not_ship(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    (scaffolded_project / "logs").mkdir()
    (scaffolded_project / "logs" / "keep.txt").write_text("the tool's own\n")
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text(
        "import os as _os\n"
        "_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))\n"
        "if '/build/' in _root:\n"
        "    open(_os.path.join(_root, 'startup.log'), 'a').write('started\\n')\n"
        "    _os.makedirs(_os.path.join(_root, 'cache', 'deep'), exist_ok=True)\n"
        "    open(_os.path.join(_root, 'cache', 'deep', 'x.bin'), 'a').write('x')\n"
        "    open(_os.path.join(_root, 'logs', 'run.log'), 'a').write('x\\n')\n" + run_script.read_text()
    )

    run = run_build(scaffolded_project)

    assert (
        "Notice: the startup check (the tool's help screens) created these in the build copy; "
        "the build removed them, so they do not ship:\n  cache\n  logs/run.log\n  startup.log\n"
    ) in run.proc.stdout
    for name in ("startup.log", "cache", "cache/deep/x.bin", "logs/run.log"):
        assert name not in run.members
        assert not (run.staged / name).exists()
    assert "logs/keep.txt" in run.members, "what the tool ships stays"


# In CI, a runner's uv cache is often cold, so the offline sync of the
# startup environment failed, and the check fell back on the .venv with its
# dev group. CI now tries the sync once online first.


def sync_stand_in(tmp_path):
    """A stand-in uv: 'sync ... --offline' fails as uv does with a cold cache,
    'sync' without --offline makes the environment. Every other command exits 0."""
    bin_dir = tmp_path / "online-only-uv"
    bin_dir.mkdir()
    log = bin_dir / "sync.log"
    (bin_dir / "out.txt").write_text(UV_OFFLINE_ERROR)
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        '[ "$1" = sync ] || exit 0\n'
        f'echo "$*" >> "{log}"\n'
        'case " $* " in *" --offline "*)\n'
        f'    cat "{bin_dir / "out.txt"}" >&2\n'
        "    exit 2 ;;\n"
        "esac\n"
        'mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
        # Never through a link (a real uv's venv): that would overwrite the interpreter.
        'rm -f "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        f'printf \'#!/bin/sh\\nexec "%s" "$@"\\n\' "{sys.executable}" > "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        'chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
    )
    (bin_dir / "uv").chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, log


def test_ci_tries_the_startup_sync_online_once(scaffolded_project, run_build, tmp_path):
    dev_venv(scaffolded_project, tmp_path)
    env, log = sync_stand_in(tmp_path)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    # Control: outside CI, only offline, then the .venv.
    run = run_build(scaffolded_project, env=env)
    assert f"{FALLBACK}: 'uv sync --frozen --no-dev --offline' could not make" in run.proc.stdout
    assert log.read_text() == "sync --frozen --no-dev --offline --reinstall-package demo\n"
    log.unlink()

    run = run_build(scaffolded_project, env={**env, "CI": "true"})

    assert log.read_text() == (
        "sync --frozen --no-dev --offline --reinstall-package demo\nsync --frozen --no-dev --reinstall-package demo\n"
    )
    assert "Startup check environment: .ngargparser/startup-venv (uv sync --frozen --no-dev)." in run.proc.stdout
    assert FALLBACK not in run.proc.stdout
    assert "Startup check passed." in run.proc.stdout


# The startup venv was synced from the tool folder, so the editable install of
# a tool with a [build-system] put the tool folder's src/ on sys.path: a
# module .distignore left out of the tarball still imported, and the help
# screens passed, while the deploy's uv sync in the extracted copy failed
# ("package directory 'src/helperpkg' does not exist"). The venv is now
# synced from the build copy, and a tool that does not install from it fails
# the build with uv's lines.


def editable_uv(tmp_path, code=0, output="", name="editable-uv"):
    """A stand-in uv first on PATH. `uv sync` logs "$PWD|$*", prints `output`
    and, with code 0, makes $UV_PROJECT_ENVIRONMENT/bin/python, which puts
    the src/ of the folder the sync ran in on the tool's path, as the .pth of
    an editable install does. Every other command exits 0."""
    bin_dir = tmp_path / name
    bin_dir.mkdir()
    log = bin_dir / "sync.log"
    (bin_dir / "out.txt").write_text(output)
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        '[ "$1" = sync ] || exit 0\n'
        f'echo "$PWD|$*" >> "{log}"\n'
        f'cat "{bin_dir / "out.txt"}" >&2\n'
        f"[ {code} -eq 0 ] || exit {code}\n"
        'mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
        # Never through a link (a real uv's venv): that would overwrite the interpreter.
        'rm -f "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        f'printf \'#!/bin/sh\\nPYTHONPATH="%s/src" exec "%s" "$@"\\n\' "$PWD" "{sys.executable}" '
        '> "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        'chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
    )
    (bin_dir / "uv").chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, log


def with_helper_package(project_dir):
    """The entry script imports src/helperpkg, which .distignore leaves out."""
    (project_dir / "src" / "helperpkg").mkdir()
    (project_dir / "src" / "helperpkg" / "__init__.py").write_text("VALUE = 1\n")
    run_script = project_dir / "src" / "run_demo.py"
    run_script.write_text("import helperpkg  # noqa: F401\n" + run_script.read_text())
    with open(project_dir / ".distignore", "a") as f:
        f.write("\n/src/helperpkg/\n")


def test_startup_venv_is_synced_from_the_build_copy(scaffolded_project, run_build, fake_venv, tmp_path):
    fake_venv(scaffolded_project)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    env, log = editable_uv(tmp_path)
    # Control: shipped, the package imports from the build copy.
    run_script = scaffolded_project / "src" / "run_demo.py"
    original = run_script.read_text()
    (scaffolded_project / "src" / "helperpkg").mkdir()
    (scaffolded_project / "src" / "helperpkg" / "__init__.py").write_text("VALUE = 1\n")
    run_script.write_text("import helperpkg  # noqa: F401\n" + original)
    run = run_build(scaffolded_project, env=env)
    assert "Startup check passed." in run.proc.stdout

    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\n/src/helperpkg/\n")
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode != 0
    assert "src/run_demo.py --help: ModuleNotFoundError: No module named 'helperpkg'" in run.proc.stderr
    assert not run.tarball.exists()
    # In the build copy, with the tool reinstalled each build.
    assert log.read_text() == f"{run.staged}|sync --frozen --no-dev --offline --reinstall-package demo\n" * 2


# What uv 0.11 prints when the build backend cannot build the tool from the
# build copy (setuptools, packages = ["helperpkg"], src/helperpkg left out).
UV_BUILD_FAILED = (
    "Using CPython 3.11.15\n"
    "   Building demo @ file:///work/demo/build/ng_demo-tver\n"
    "  × Failed to build `demo @ file:///work/demo/build/ng_demo-tver`\n"
    "  ├─▶ The build backend returned an error\n"
    "  ╰─▶ Call to `setuptools.build_meta.build_editable` failed (exit status: 1)\n"
    "\n"
    "      [stdout]\n"
    "      running egg_info\n"
    "\n"
    "      [stderr]\n"
    "      error: package directory 'src/helperpkg' does not exist\n"
    "\n"
    "      hint: This usually indicates a problem with the package or the build\n"
    "      environment.\n"
)
# And when the build backend itself is not in its cache, offline.
UV_BACKEND_NOT_CACHED = (
    "Using CPython 3.11.15\n"
    "  × Failed to build `demo @ file:///work/demo/build/ng_demo-tver`\n"
    "  ├─▶ Failed to resolve requirements from `build-system.requires`\n"
    "  ├─▶ No solution found when resolving: `setuptools>=61`\n"
    "  ╰─▶ Because setuptools was not found in the cache and you require setuptools>=61, we can conclude that\n"
    "      your requirements are unsatisfiable.\n"
    "\n"
    "      hint: Packages were unavailable because the network was disabled. When the network is disabled,\n"
    "      registry packages may only be read from the cache.\n"
)
NO_INSTALL = (
    "ERROR: The tool does not install from the build copy, so deploy/install.sh would fail on the deploy host "
    "too ('uv sync --frozen --no-dev' exit 1):\n"
)


def test_a_tool_that_does_not_install_from_the_build_copy_fails(scaffolded_project, run_build, fake_venv, tmp_path):
    fake_venv(scaffolded_project)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    first = run_build(scaffolded_project, env=editable_uv(tmp_path, name="ok-uv")[0])
    # Control: a backend missing from uv's cache only means the check cannot
    # make its environment offline: the .venv, with a notice, as before.
    env, log = editable_uv(tmp_path, code=1, output=UV_BACKEND_NOT_CACHED)
    run = run_build(scaffolded_project, env=env)
    assert f"{FALLBACK}: 'uv sync --frozen --no-dev --offline' could not make" in run.proc.stdout
    assert "Startup check passed." in run.proc.stdout
    log.unlink()

    (tmp_path / "editable-uv" / "out.txt").write_text(UV_BUILD_FAILED)
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={**env, "CI": "true"})

    assert run.proc.returncode == 1
    assert (
        NO_INSTALL + "      × Failed to build `demo @ file:///work/demo/build/ng_demo-tver`\n"
        "      ├─▶ The build backend returned an error\n"
    ) in run.proc.stderr
    assert "          error: package directory 'src/helperpkg' does not exist\n" in run.proc.stderr
    assert "Using CPython" not in run.proc.stderr and "   Building demo" not in run.proc.stderr
    assert (
        "Building the tool's package needs every file pyproject.toml names, such as its readme and its "
        "packages: check that .distignore and scripts/hooks.sh leave them in the build copy.\n"
    ) in run.proc.stderr
    assert FALLBACK not in run.proc.stdout
    # The deploy would fail the same way online: no second try, even in CI.
    assert log.read_text().count("|sync ") == 1
    assert "Build failed" in run.proc.stderr
    assert not run.staged.exists() and not first.tarball.exists()


# What the sync writes into the build copy, such as the egg-info setuptools
# writes next to the package, is removed again, so it does not ship.


def test_what_the_startup_sync_writes_does_not_ship(scaffolded_project, run_build, fake_venv, tmp_path):
    fake_venv(scaffolded_project)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    env, _ = editable_uv(tmp_path)
    uv = tmp_path / "editable-uv" / "uv"
    uv.write_text(
        uv.read_text().replace(
            'chmod +x "$UV', 'mkdir -p src/demo.egg-info && touch src/demo.egg-info/PKG-INFO\nchmod +x "$UV'
        )
    )

    run = run_build(scaffolded_project, env=env)

    assert "Startup check passed." in run.proc.stdout
    # The sync writes nothing into the tool folder, which the next build copies.
    assert not (scaffolded_project / "src" / "demo.egg-info").exists()
    assert not any("egg-info" in name for name in run.members)
    assert not (run.staged / "src" / "demo.egg-info").exists()
    assert (
        "Removed src/demo.egg-info from the build copy: uv sync wrote it there for the startup check.\n"
    ) in run.proc.stdout
    # Not a file the help screens wrote.
    assert "the startup check (the tool's help screens) created these" not in run.proc.stdout


# The same with the real uv and its own build backend (uv_build), which needs
# no package from uv's cache: the module it builds is left out of the build.
# Opt-in, as in test_install_real_uv.py: set NGARGPARSER_REAL_UV=1.


@pytest.mark.skipif(os.environ.get("NGARGPARSER_REAL_UV") != "1", reason="opt-in: set NGARGPARSER_REAL_UV=1")
def test_real_uv_startup_check_installs_the_tool_from_the_build_copy(scaffolded_project, run_build):
    uv = find_program("uv", os.environ["PATH"])
    if uv is None:
        pytest.skip("needs uv")
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "CI")}
    env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    major, minor = subprocess.run([uv, "--version"], capture_output=True, text=True).stdout.split()[1].split(".")[:2]
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(
        f'[build-system]\nrequires = ["uv_build>={major}.{minor},<{major}.{int(minor) + 1}"]\n'
        'build-backend = "uv_build"\n\n'
        + pyproject.read_text()
        + '\n[tool.uv.build-backend]\nmodule-name = "helperpkg"\nmodule-root = "src"\n'
    )
    # uv_build reads the readme pyproject.toml names, which the default .distignore leaves out.
    distignore = scaffolded_project / ".distignore"
    distignore.write_text(distignore.read_text().replace("\n/README.md\n", "\n"))
    (scaffolded_project / "src" / "helperpkg").mkdir()
    (scaffolded_project / "src" / "helperpkg" / "__init__.py").write_text("VALUE = 1\n")
    run_script = scaffolded_project / "src" / "run_demo.py"
    run_script.write_text("import helperpkg  # noqa: F401\n" + run_script.read_text())

    for args in (["lock"], ["sync", "--frozen"]):
        proc = subprocess.run([uv, *args], cwd=scaffolded_project, env=env, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            pytest.skip(f"uv {args[0]} cannot run offline here: {proc.stderr.strip().splitlines()[-1:]}")
    build_env = {"UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never"}
    # Control: shipped, the tool installs from the build copy and starts.
    run = run_build(scaffolded_project, env=build_env)
    assert "Startup check environment: .ngargparser/startup-venv (uv sync --frozen --no-dev)." in run.proc.stdout
    pth = "".join(p.read_text() for p in (scaffolded_project / ".ngargparser" / "startup-venv").rglob("*.pth"))
    assert str(run.staged / "src") in pth, "the editable install points at the build copy"

    with open(distignore, "a") as f:
        f.write("\n/src/helperpkg/\n")
    run = run_build(scaffolded_project, expect_ok=False, env=build_env)

    assert run.proc.returncode == 1
    assert NO_INSTALL in run.proc.stderr
    assert "src/helperpkg/__init__.py" in run.proc.stderr
    assert not run.tarball.exists()
