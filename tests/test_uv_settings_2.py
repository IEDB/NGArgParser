"""uv settings of the build machine, and a lock check that cannot run.

`UV_RESOLUTION=lowest make build` (or resolution = "lowest-direct" in a
uv.toml) stopped with "uv.lock is out of date with pyproject.toml: run 'uv
lock'", which hid uv's own reason ("Ignoring existing lockfile due to change
in resolution mode"), and following the advice locked the lowest versions.
`UV_NO_SOURCES=1 make build` blamed the deploy host. The build now leaves out
UV_RESOLUTION, UV_PRERELEASE, UV_FORK_STRATEGY, UV_EXCLUDE_NEWER,
UV_NO_SOURCES and UV_PROJECT_ENVIRONMENT for its uv commands (the startup
check sets its own environment), prints uv's reason under the error, and
names a setting in a uv.toml or a [tool.uv] table that changes how uv
resolves.

Without uv and without a Python that reads TOML (3.11 or later, or tomli),
as on many HPC hosts, the lock check was skipped without a word.
"""

import os
import shutil
import subprocess
import sys

import pytest
from test_build_lock import STALE_MESSAGE, find_program, path_without_uv
from test_build_startup import dev_venv

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)

# The settings of the build machine that uv must not get, with a value each.
LEFT_OUT = {
    "UV_RESOLUTION": "lowest",
    "UV_PRERELEASE": "allow",
    "UV_FORK_STRATEGY": "fewest",
    "UV_EXCLUDE_NEWER": "2020-01-01",
    "UV_NO_SOURCES": "1",
    "UV_PROJECT_ENVIRONMENT": "/nonexistent/env",
}
REASON = "Ignoring existing lockfile due to change in resolution mode: `highest` vs. `lowest-direct`"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """No CI, no uv settings from the shell that runs the tests, and no user or
    system uv.toml of this machine."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)
    for name in [*LEFT_OUT, "UV_CONFIG_FILE", "UV_NO_CONFIG"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-home"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg-dirs"))


def logging_uv(tmp_path, lock_output="", lock_code=0):
    """A stand-in uv that logs each call with the settings in LEFT_OUT it got.
    Its `lock --check` prints `lock_output` and exits `lock_code`; its `sync`
    makes the environment (the test interpreter) where UV_PROJECT_ENVIRONMENT says."""
    bin_dir = tmp_path / "logging-uv"
    bin_dir.mkdir(parents=True)
    log = bin_dir / "calls.log"
    (bin_dir / "lock-out.txt").write_text(lock_output)
    settings = " ".join(f"{name}=${name}" for name in LEFT_OUT)
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        f'echo "$* | {settings}" >> "{log}"\n'
        f'if [ "$1" = lock ]; then cat "{bin_dir / "lock-out.txt"}" >&2; exit {lock_code}; fi\n'
        '[ "$1" = sync ] || exit 0\n'
        'mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
        # Never through a link (a real uv's venv): that would overwrite the interpreter.
        'rm -f "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        f'printf \'#!/bin/sh\\nexec "%s" "$@"\\n\' "{sys.executable}" > "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        'chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
    )
    (bin_dir / "uv").chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, log


def calls(log):
    """The build's lock and sync calls (not its `uv run`, which finds a Python
    for the lock comparison when python3 cannot read TOML)."""
    return [line for line in log.read_text().splitlines() if line.startswith(("lock ", "sync "))]


@pytest.mark.parametrize("name", sorted(LEFT_OUT))
def test_resolution_settings_of_the_build_machine_do_not_reach_uv(scaffolded_project, run_build, tmp_path, name):
    dev_venv(scaffolded_project, tmp_path)
    env, log = logging_uv(tmp_path)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")

    run = run_build(scaffolded_project, env={**env, name: LEFT_OUT[name]})

    assert "Startup check environment: .ngargparser/startup-venv" in run.proc.stdout
    lines = calls(log)
    assert lines[0].startswith("lock --check --offline | "), lines
    assert lines[1].startswith("sync --frozen --no-dev --offline"), lines
    for line in lines:
        got = dict(item.split("=", 1) for item in line.split(" | ", 1)[1].split(" "))
        if name == "UV_PROJECT_ENVIRONMENT" and line.startswith("sync "):
            # The startup check's own environment, not the caller's.
            assert got[name] == str(scaffolded_project / ".ngargparser" / "startup-venv"), line
        else:
            assert got[name] == "", line


def stale_with_reason():
    return (
        "Using CPython 3.11.15\n"
        f"{REASON}\n"
        "Resolved 8 packages in 14ms\n"
        "The lockfile at `uv.lock` needs to be updated, but `--check` was provided. "
        "To update the lockfile, run `uv lock`.\n"
    )


def test_the_lock_error_shows_uvs_reason_and_names_a_setting_of_this_machine(
    scaffolded_project, run_build, tmp_path, monkeypatch
):
    env, _log = logging_uv(tmp_path, lock_output=stale_with_reason(), lock_code=1)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    user = tmp_path / "xdg-home" / "uv" / "uv.toml"
    user.parent.mkdir(parents=True)
    # [pip] settings apply to uv pip alone.
    user.write_text('# mine\nresolution = "lowest-direct"\n[pip]\nprerelease = "allow"\n')

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode == 1
    assert (
        f"{STALE_MESSAGE}\n"
        f"    {REASON}\n"
        f"uv reads settings that change how it resolves: resolution in {user}. One outside the tool folder "
        "applies on this machine only, not on the deploy host: if it is why uv.lock looks out of date, "
        "remove it there instead of running 'uv lock'.\n"
    ) in run.proc.stderr
    assert "prerelease" not in run.proc.stderr

    # A setting of the tool's own pyproject.toml ships: 'uv lock' is the fix.
    user.unlink()
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + '\n[tool.uv]\nprerelease = "allow"\n')
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)
    assert f"{REASON}\nuv reads settings that change how it resolves: prerelease in pyproject.toml.\n" in (
        run.proc.stderr
    )
    assert "this machine" not in run.proc.stderr

    # UV_NO_CONFIG: uv reads no configuration file, so none is named.
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={**env, "UV_NO_CONFIG": "1"})
    assert f"    {REASON}\n" in run.proc.stderr
    assert "uv reads settings" not in run.proc.stderr


def test_uv_config_files_are_found_as_uv_finds_them(scaffolded_project, run_build, tmp_path):
    env, _log = logging_uv(tmp_path, lock_output=stale_with_reason(), lock_code=1)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    # A uv.toml in a folder above the tool's applies, as the tool has no [tool.uv] table.
    above = scaffolded_project.parent / "uv.toml"
    above.write_text("exclude-newer = '2020-01-01'\nno-sources = true\n")
    system = tmp_path / "xdg-dirs" / "uv" / "uv.toml"
    system.parent.mkdir(parents=True)
    system.write_text("fork-strategy = 'fewest'\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert (
        f"uv reads settings that change how it resolves: exclude-newer in {above}, no-sources in {above}, "
        f"fork-strategy in {system}. One outside the tool folder"
    ) in run.proc.stderr

    # The tool's own [tool.uv] table stops the search: the uv.toml above is not read.
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "\n[tool.uv.sources]\n")
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)
    assert f"uv reads settings that change how it resolves: fork-strategy in {system}." in run.proc.stderr
    assert str(above) not in run.proc.stderr

    # UV_CONFIG_FILE is read alone, relative to the tool folder.
    (scaffolded_project / "machine.toml").write_text('resolution = "lowest"\n')
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={**env, "UV_CONFIG_FILE": "machine.toml"})
    assert (
        "uv reads settings that change how it resolves: resolution in machine.toml. One outside the tool folder"
    ) in run.proc.stderr
    assert str(system) not in run.proc.stderr


def test_a_stale_lock_without_settings_says_what_it_said(scaffolded_project, run_build, tmp_path):
    # Control: no reason from uv and no setting, so nothing more than before.
    env, _log = logging_uv(
        tmp_path,
        lock_output="Using CPython 3.11.15\nThe lockfile at `uv.lock` needs to be updated, but `--check` was provided.\n",
        lock_code=1,
    )
    (scaffolded_project / "uv.lock").write_text("version = 1\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.stderr.endswith(f"{STALE_MESSAGE}\n"), run.proc.stderr


def no_toml_python_path(tmp_path):
    """PATH without uv, whose python3 cannot read TOML (exits 1 when asked), or None."""
    path = path_without_uv()
    if path is None:
        return None
    shim = tmp_path / "no-toml-python"
    shim.mkdir()
    (shim / "python3").write_text("#!/bin/sh\nexit 1\n")
    (shim / "python3").chmod(0o755)
    return f"{shim}{os.pathsep}{path}"


def test_a_lock_check_that_cannot_run_says_so(scaffolded_project, run_build, tmp_path):
    path = no_toml_python_path(tmp_path)
    if path is None:
        pytest.skip("cannot hide uv here")
    (scaffolded_project / "uv.lock").write_text("version = 1\n")

    run = run_build(scaffolded_project, progress=True, env={"PATH": path})

    assert (
        "Lock check skipped: uv is not on the PATH, and no Python that reads TOML was found "
        "(.venv/bin/python or python3, at 3.11 or later or with tomli), so uv.lock was not checked "
        "against pyproject.toml.\n"
    ) in run.proc.stdout

    # Control: a Python that reads TOML runs the comparison, and that one is quiet.
    python_dir = os.path.dirname(sys.executable)
    probe = "try:\n    import tomllib\nexcept ImportError:\n    import tomli"
    if subprocess.run([sys.executable, "-I", "-c", probe]).returncode != 0:
        pytest.skip("the test interpreter reads no TOML")
    shim = tmp_path / "toml-python"
    shim.mkdir()
    # A script, not a link: a venv's Python finds its packages (tomli) through
    # the path it was started from.
    (shim / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (shim / "python3").chmod(0o755)
    run = run_build(
        scaffolded_project, progress=True, env={"PATH": f"{shim}{os.pathsep}{python_dir}{os.pathsep}{path}"}
    )
    assert "Lock check skipped" not in run.proc.stdout


# With the real uv, offline from its cache. Opt-in, as the other real_uv
# tests: set NGARGPARSER_REAL_UV=1 (the CI job pytest-real-uv does).


@pytest.mark.skipif(os.environ.get("NGARGPARSER_REAL_UV") != "1", reason="opt-in: set NGARGPARSER_REAL_UV=1")
@pytest.mark.parametrize("setting", ["UV_RESOLUTION=lowest", "UV_PRERELEASE=allow", "UV_NO_SOURCES=1"])
def test_real_uv_resolution_settings_do_not_fail_the_lock_check(scaffolded_project, run_build, setting):
    uv = find_program("uv", os.environ["PATH"])
    if uv is None:
        pytest.skip("needs uv")
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "CI")}
    env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    proc = subprocess.run([uv, "lock"], cwd=scaffolded_project, env=env, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        pytest.skip(f"uv lock cannot run offline here: {proc.stderr.strip().splitlines()[-1:]}")
    name, value = setting.split("=", 1)

    run = run_build(
        scaffolded_project, progress=True, env={"UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", name: value}
    )

    assert "uv.lock is out of date" not in run.proc.stderr
    assert "Lock check" not in run.proc.stdout
