"""The build's startup check: its timeout, the uv errors that stop it, and an
entry that is not a Python script.

Each help screen had a fixed 60 s timeout, and all four ran after the first
one timed out (4 minutes). NGARGPARSER_STARTUP_CHECK_TIMEOUT now sets the
seconds, a bad value stops the preflight, and the first timeout ends the check.

A uv error the deploy's 'uv sync' meets too (a .python-version the project
cannot use, a hash in uv.lock that does not match) only gave a notice, and the
tarball was built. Only offline or cache trouble falls back on the .venv now.
A kept startup venv hid a hash changed in uv.lock: after uv.lock changes, the
sync reinstalls from the cache, which checks the hashes.

An entry such as bin/run.sh ran with Python, and failed with a SyntaxError.
"""

import os
import shutil
import subprocess
import sys

import pytest
from test_build_lock import find_program
from test_build_startup import FALLBACK, fake_uv, set_entry

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)
    monkeypatch.delenv("NGARGPARSER_STARTUP_CHECK_TIMEOUT", raising=False)


TIMEOUT_HINT = (
    "set NGARGPARSER_STARTUP_CHECK_TIMEOUT to the seconds each help screen may take, "
    "or NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check"
)


def slow_help(project_dir, seconds):
    """Every help screen of the build copy's entry script waits `seconds` first."""
    run_script = project_dir / "src" / "run_demo.py"
    run_script.write_text(
        "import sys as _sys, time as _time\n"
        f'if "/build/" in __file__ and "--help" in _sys.argv:\n    _time.sleep({seconds})\n' + run_script.read_text()
    )


def test_timeout_comes_from_the_environment_and_the_first_one_ends_the_check(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    slow_help(scaffolded_project, 1.5)

    run = run_build(
        scaffolded_project, expect_ok=False, progress=True, env={"NGARGPARSER_STARTUP_CHECK_TIMEOUT": "0.5"}
    )

    assert run.proc.returncode != 0
    assert "ERROR: The tool does not start from the build dir:\n" in run.proc.stderr
    assert f"src/run_demo.py --help: timed out after 0.5s: {TIMEOUT_HINT}\n" in run.proc.stderr
    # The other three help screens did not run.
    assert run.proc.stderr.count("timed out") == 1
    assert "predict --help" not in run.proc.stderr
    assert not run.tarball.exists()

    # Control: with more seconds than the help screens take, the check passes.
    run = run_build(scaffolded_project, env={"NGARGPARSER_STARTUP_CHECK_TIMEOUT": " 10 "})
    assert "timed out" not in run.proc.stdout + run.proc.stderr


@pytest.mark.parametrize("value", ["abc", "0", "0.0", "-5", "1 2", "inf", "1e3"])
def test_bad_timeout_stops_the_preflight(scaffolded_project, run_build, fake_venv, value):
    fake_venv(scaffolded_project)
    first = run_build(scaffolded_project)

    run = run_build(
        scaffolded_project, expect_ok=False, progress=True, env={"NGARGPARSER_STARTUP_CHECK_TIMEOUT": value}
    )

    assert run.proc.returncode == 1
    assert (
        f"ERROR: NGARGPARSER_STARTUP_CHECK_TIMEOUT={value} is not a number of seconds above 0 and at most 86400: set it to the "
        "seconds each help screen of the startup check may take, such as 120, or unset it for 60.\n"
    ) in run.proc.stderr
    assert run.proc.stderr.count("ERROR") == 1
    assert "Build failed" not in run.proc.stderr
    assert first.tarball.is_file() and first.staged.is_dir(), "a preflight stop removes nothing"


# What uv 0.11.14 prints, as it prints it (wrapped, in a tree of causes).
UV_PYTHON_PIN = (
    "Using CPython 3.12.13\n"
    "error: The Python request from `.python-version` resolved to Python 3.12.13, which is incompatible with "
    "the project's Python requirement: `==3.11.*` (from `project.requires-python`)\n"
    "Use `uv python pin` to update the `.python-version` file to a compatible version\n"
)
UV_HASH_MISMATCH = (
    "  × Failed to download `python-dotenv==1.2.4`\n"
    "  ╰─▶ Hash mismatch for `python-dotenv==1.2.4`\n"
    "\n"
    "      Expected:\n"
    "        sha256:400269a8a5b3fd54ffa6f3d84b18abed50064717576b4ecf03dc4a55d8aa04fdc\n"
    "\n"
    "      Computed:\n"
    "        sha256:42269a8a5b3fd54ffa6f3d84b18abed50064717576b4ecf03dc4a55d8aa04fdc\n"
    "  help: `python-dotenv` (v1.2.4) was included because `demo` (v0.1.0) depends\n"
    "        on `python-dotenv`\n"
)
UV_NOT_CACHED_WRAPPED = (
    "Using CPython 3.11.15\n"
    "  × Failed to download `python-dotenv==1.2.4`\n"
    "  ╰─▶ Network connectivity is disabled, but the\n"
    "      requested data wasn't found in the cache for:\n"
    "      `https://files.pythonhosted.org/packages/python_dotenv-1.2.4-py3-none-any.whl`\n"
)
UV_DNS = (
    "Using CPython 3.11.15\n"
    "  × Failed to download `python-dotenv==1.2.4`\n"
    "  ├─▶ Request failed after 3 retries in 13.1s\n"
    "  ├─▶ Failed to fetch:\n"
    "  │   `https://files.pythonhosted.org/packages/python_dotenv-1.2.4-py3-none-any.whl`\n"
    "  ├─▶ error sending request for url\n"
    "  │   (https://files.pythonhosted.org/packages/python_dotenv-1.2.4-py3-none-any.whl)\n"
    "  ├─▶ client error (Connect)\n"
    "  ├─▶ dns error\n"
    "  ╰─▶ failed to lookup address information: Name or service not known\n"
)
UV_REFUSED = (
    "  × Failed to download `python-dotenv==1.2.4`\n  ├─▶ tcp connect error\n  ╰─▶ Connection refused (os error 111)\n"
)
UV_NO_PYTHON_OFFLINE = (
    "error: No interpreter found for Python ==3.11.* in managed installations or search path\n"
    "\n"
    "hint: A managed Python download is available for Python ==3.11.*, but uv is set to offline mode\n"
)
UV_CACHE = (
    "error: Failed to initialize cache at `/home/me/.cache/uv`\n"
    "  Caused by: failed to open file `/home/me/.cache/uv/CACHEDIR.TAG`: Permission denied (os error 13)\n"
)


@pytest.mark.parametrize(
    "output, code, first_line",
    [
        (UV_PYTHON_PIN, 2, "    error: The Python request from `.python-version` resolved to Python 3.12.13"),
        (UV_HASH_MISMATCH, 1, "      × Failed to download `python-dotenv==1.2.4`"),
    ],
    ids=["python-version", "hash-mismatch"],
)
@pytest.mark.parametrize("ci", [False, True], ids=["local", "ci"])
def test_uv_error_the_deploy_meets_too_stops_the_build(
    scaffolded_project, run_build, fake_venv, tmp_path, output, code, first_line, ci
):
    fake_venv(scaffolded_project)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    env, log = fake_uv(tmp_path, code=code, output=output)

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env={**env, **({"CI": "1"} if ci else {})})

    assert run.proc.returncode == 1
    assert (
        f"ERROR: 'uv sync --frozen --no-dev' fails in the build copy (exit {code}), so deploy/install.sh would "
        f"fail on the deploy host too:\n{first_line}"
    ) in run.proc.stderr
    assert "Using CPython" not in run.proc.stderr
    assert FALLBACK not in run.proc.stdout
    assert "Build completed" not in run.proc.stdout
    assert not run.tarball.exists()
    # Online, the deploy's uv would say the same: no second try in CI.
    assert log.read_text().count("|sync ") == 1


@pytest.mark.parametrize(
    "output",
    [UV_NOT_CACHED_WRAPPED, UV_DNS, UV_REFUSED, UV_NO_PYTHON_OFFLINE, UV_CACHE],
    ids=["not-cached-wrapped", "dns", "refused", "no-python-offline", "cache"],
)
def test_offline_or_cache_trouble_still_falls_back_on_the_venv(
    scaffolded_project, run_build, fake_venv, tmp_path, output
):
    fake_venv(scaffolded_project)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    env, log = fake_uv(tmp_path, code=2, output=output)

    run = run_build(scaffolded_project, env=env)

    assert f"{FALLBACK}: 'uv sync --frozen --no-dev --offline' could not make" in run.proc.stdout
    assert "Startup check passed." in run.proc.stdout
    assert "ERROR" not in run.proc.stderr
    assert log.read_text().count("|sync ") == 1

    # In CI, tried online once more, then a stop: the .venv holds the dev group too.
    log.unlink()
    run = run_build(scaffolded_project, expect_ok=False, env={**env, "CI": "1"})
    assert run.proc.returncode == 1
    assert "ERROR: The startup check cannot run: CI is set and 'uv sync --frozen --no-dev --offline'" in run.proc.stderr
    assert FALLBACK not in run.proc.stdout
    assert log.read_text().count("|sync ") == 2


def lock_checking_uv(tmp_path):
    """A stand-in uv: 'uv sync' logs its arguments, and with --reinstall reads
    uv.lock as real uv reads its cache: a lock holding BADHASH gets uv's hash
    mismatch (exit 1). Without --reinstall, it installs nothing new and checks
    nothing, as real uv does for a kept venv that holds every package. Every
    other command exits 0."""
    bin_dir = tmp_path / "lock-checking-uv"
    bin_dir.mkdir()
    log = bin_dir / "sync.log"
    (bin_dir / "mismatch.txt").write_text(UV_HASH_MISMATCH)
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        '[ "$1" = sync ] || exit 0\n'
        f'echo "$*" >> "{log}"\n'
        'case " $* " in *" --reinstall "*)\n'
        "    if grep -q BADHASH uv.lock; then\n"
        f'        cat "{bin_dir / "mismatch.txt"}" >&2\n'
        "        exit 1\n"
        "    fi ;;\n"
        "esac\n"
        'mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
        # Never through a link (a real uv's venv): that would overwrite the interpreter.
        'rm -f "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        f'printf \'#!/bin/sh\\nexec "%s" "$@"\\n\' "{sys.executable}" > "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        'chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
    )
    (bin_dir / "uv").chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, log


def test_hash_changed_in_uv_lock_after_a_good_build_stops_the_build(scaffolded_project, run_build, fake_venv, tmp_path):
    fake_venv(scaffolded_project)
    lock = scaffolded_project / "uv.lock"
    lock.write_text("version = 1\n# sha256:42269a8a\n")
    env, log = lock_checking_uv(tmp_path)
    # A first build makes the startup venv: nothing to reinstall.
    first = run_build(scaffolded_project, env=env)
    assert log.read_text() == "sync --frozen --no-dev --offline --reinstall-package demo\n"
    log.unlink()

    lock.write_text("version = 1\n# sha256:400269a8 BADHASH\n")
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode == 1
    assert "--reinstall --offline" in log.read_text()
    assert (
        "fails in the build copy (exit 1), so deploy/install.sh would fail on the deploy host too:" in run.proc.stderr
    )
    assert "      ╰─▶ Hash mismatch for `python-dotenv==1.2.4`\n" in run.proc.stderr
    assert not first.tarball.exists()

    # Control: uv.lock as it was synced last needs no reinstall, and builds.
    log.unlink()
    lock.write_text("version = 1\n# sha256:42269a8a\n")
    run_build(scaffolded_project, env=env)
    assert log.read_text() == "sync --frozen --no-dev --offline --reinstall-package demo\n"
    # A lock that changed for good reinstalls once, then is the one synced.
    log.unlink()
    lock.write_text("version = 1\n# sha256:5d562b63\n")
    run_build(scaffolded_project, env=env)
    run_build(scaffolded_project, env=env)
    assert log.read_text() == (
        "sync --frozen --no-dev --reinstall --offline --reinstall-package demo\n"
        "sync --frozen --no-dev --offline --reinstall-package demo\n"
    )


# The same with the real uv, offline from its cache. Opt-in, as in
# test_install_real_uv.py: set NGARGPARSER_REAL_UV=1.


@pytest.mark.skipif(os.environ.get("NGARGPARSER_REAL_UV") != "1", reason="opt-in: set NGARGPARSER_REAL_UV=1")
def test_real_uv_hash_changed_after_a_good_build_stops_the_build(scaffolded_project, run_build):
    uv = find_program("uv", os.environ["PATH"])
    if uv is None:
        pytest.skip("needs uv")
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "CI")}
    env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    for args in (["lock"], ["sync", "--frozen"]):
        proc = subprocess.run([uv, *args], cwd=scaffolded_project, env=env, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            pytest.skip(f"uv {args[0]} cannot run offline here: {proc.stderr.strip().splitlines()[-1:]}")
    build_env = {"UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never"}
    run = run_build(scaffolded_project, env=build_env)
    assert "Startup check environment: .ngargparser/startup-venv (uv sync --frozen --no-dev)." in run.proc.stdout
    lock = scaffolded_project / "uv.lock"
    text = lock.read_text()
    marker = "python_dotenv-"
    line = next(x for x in text.splitlines() if marker in x and '.whl"' in x and 'hash = "sha256:' in x)
    digest = line.split('hash = "sha256:')[1][:64]
    changed = ("0" if digest[0] != "0" else "1") + digest[1:]
    lock.write_text(text.replace(digest, changed))

    run = run_build(scaffolded_project, expect_ok=False, env=build_env)

    assert run.proc.returncode == 1
    assert "fails in the build copy" in run.proc.stderr
    assert "Hash mismatch for `python-dotenv==" in run.proc.stderr
    assert not run.tarball.exists()


# An entry that is not a Python script runs by itself, by the rule
# configure.py and app_env.py apply, and finds the check's environment as the
# build copy's .venv, as it finds the deploy's .venv.

WRAPPERS = {
    "relative-venv": '#!/bin/sh\nexec .venv/bin/python src/run_demo.py "$@"\n',
    "python-on-path": '#!/bin/sh\nexec python "$(dirname "$0")/../src/run_demo.py" "$@"\n',
}


@pytest.mark.parametrize("wrapper", sorted(WRAPPERS))
def test_entry_that_is_not_a_python_script_runs_by_itself(scaffolded_project, run_build, fake_venv, wrapper):
    fake_venv(scaffolded_project)
    (scaffolded_project / "bin").mkdir()
    entry = scaffolded_project / "bin" / "run.sh"
    entry.write_text(WRAPPERS[wrapper])
    entry.chmod(0o755)
    set_entry(scaffolded_project, 'entry = "bin/run.sh"')

    run = run_build(scaffolded_project, progress=True)

    assert "SyntaxError" not in run.proc.stderr
    assert "bin/run.sh" in run.members
    # The link to the check's environment is gone again, and does not ship.
    assert not any(name == ".venv" or name.startswith(".venv/") for name in run.members)
    assert not os.path.lexists(run.staged / ".venv")


def test_entry_rule_follows_the_first_line(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    src = scaffolded_project / "src"
    # Control: a #! line that names python runs with the check's Python, .py or not.
    (src / "run").write_text("#!/usr/bin/env python3\n" + (src / "run_demo.py").read_text())
    set_entry(scaffolded_project, 'entry = "src/run"')
    run_build(scaffolded_project)

    # Not a Python script, and not executable: said so, as app_env.py says it.
    (scaffolded_project / "bin").mkdir()
    (scaffolded_project / "bin" / "run.sh").write_text(WRAPPERS["relative-venv"])
    set_entry(scaffolded_project, 'entry = "bin/run.sh"')
    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert (
        "bin/run.sh is not a Python script and not executable: run 'chmod +x bin/run.sh' and commit the mode "
        "(git update-index --chmod=+x bin/run.sh)\n"
    ) in run.proc.stderr
    assert not run.tarball.exists()
