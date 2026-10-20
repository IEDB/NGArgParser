"""Setup scripts, ./configure and deploy/install.sh.

1. While a same-version redeploy installs a dependency again where it was (its
   folder has ./configure or .ngargparser-build, but no .env yet), sourcing the
   setup script and run_<dep> ran the values ./configure saw, which may name a
   half-made .venv. Now both say "<dep> is being installed at <real folder>
   right now (a redeploy in progress?); try again", and run_<dep> returns 127.
2. A dependency that moved, or a mistyped <dep>_python, was only told to try
   again. A second sentence now names the fix.
3. Sourcing a setup script activated the dependency's virtualenv in the
   caller's shell: VIRTUAL_ENV, PATH and PYTHONHOME changed, so the caller's
   next `python` was the dependency's.
4. With no virtualenv, <DEP>_PYTHON was the bare python3, which the raw form
   "$<DEP>_PYTHON" "$<DEP>_ENTRY" found in the caller's virtualenv.
5. With no virtualenv, an executable entry whose #! line names a Python was run
   with python3, not with that Python.
6. Wrong-case names in paths.py (app_entry, pepx_VENV) were ignored, and left a
   second key of the same name in .env.
7. A hand-written setup_<dep>_env.sh of a current dependency was overwritten.
8. Two folders in libs/ providing one module name shadowed each other silently.
9. install.sh kept a uv older than the version it pins.
10. The paths.lock wait was silent for 30 s, and Ctrl-C ended it in a traceback.
"""

import os
import shutil
import signal
import subprocess
import sys
import time

import pytest
from test_configure_env import env_values, hold_paths_lock, make_tool_dir, make_venv, run_configure
from test_install_sh import STUB_CURL, STUB_UV, deployed, path_without_uv, run_install  # noqa: F401
from test_redeploy_messages import clean_env, deploy

from ngargparser import cli

# The in_tmp_dir fixture hides every program from shutil.which.
WHICH = shutil.which
SHELLS = [shell for shell in ("sh", "bash", "dash") if WHICH(shell)]

pytestmark = pytest.mark.skipif(os.name != "posix" or WHICH("bash") is None, reason="needs bash, symlinks, fcntl")


def in_shell(project, shell, body, env=None):
    """Run `body` in `shell` in `project`: (exit code, stdout, stderr lines)."""
    proc = subprocess.run(
        [WHICH(shell), "-c", body],
        cwd=project,
        env=env or clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr.splitlines()


def installing(version):
    return f"callee is being installed at {version} right now (a redeploy in progress?); try again"


# 1. A same-version redeploy -----------------------------------------------------


@pytest.mark.parametrize("marker", ["configure", ".ngargparser-build"])
@pytest.mark.parametrize("shell", SHELLS)
def test_a_dependency_being_installed_where_it_was_is_never_run_from_saved_values(
    scaffolded_project, tmp_path, shell, marker
):
    apps = tmp_path / "apps"
    version = deploy(apps, "1.0")
    if marker != "configure":
        (version / "configure").unlink()
        (version / marker).write_text("")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    run = "set -u; . ./setup_callee_env.sh; run_callee predict"
    # Control: installed, it runs.
    assert in_shell(scaffolded_project, shell, run) == (0, "callee ran: predict\n", [])

    # Extracted again in the same folder; install.sh has not written .env yet.
    (version / ".env").rename(version / ".env.away")
    assert in_shell(scaffolded_project, shell, run) == (
        127,
        "",
        [f"setup_callee_env.sh: {installing(version)}", installing(version)],
    )
    # Sourced while it was installed, run while it is installed again.
    (version / ".env.away").rename(version / ".env")
    rc, out, err = in_shell(
        scaffolded_project,
        shell,
        f"set -u; . ./setup_callee_env.sh; mv '{version}/.env' '{version}/.env.away'; run_callee predict",
    )
    assert (rc, out, err) == (127, "", [installing(version)])


@pytest.mark.parametrize("shell", SHELLS)
def test_a_dependency_whose_env_is_there_but_whose_venv_is_half_made_is_being_installed(
    scaffolded_project, tmp_path, shell
):
    # As dependency_command says (_being_installed): .env is there, the virtualenv
    # it publishes lies in the tool folder, and has no bin/python yet.
    apps = tmp_path / "apps"
    version = deploy(apps, "1.0")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    python = version / ".venv" / "bin" / "python"
    run = "set -u; . ./setup_callee_env.sh; run_callee predict"
    assert in_shell(scaffolded_project, shell, run) == (0, "callee ran: predict\n", [])  # control

    python.rename(version / "python.away")
    assert in_shell(scaffolded_project, shell, run) == (
        127,
        "",
        [f"setup_callee_env.sh: {installing(version)}", installing(version)],
    )
    # Sourced while it was whole, run while its bin/python is gone.
    (version / "python.away").rename(python)
    rc, out, err = in_shell(
        scaffolded_project, shell, f"set -u; . ./setup_callee_env.sh; mv '{python}' '{version}/python.away'; run_callee"
    )
    assert (rc, out, err) == (127, "", [installing(version)])
    # Control: not a tool folder (no ./configure), it is a Python that is not there.
    (version / "configure").unlink()
    rc, out, err = in_shell(scaffolded_project, shell, run)
    assert (rc, out) == (127, "")
    assert err == [
        f"callee cannot run right now: {python} does not exist (a redeploy in progress?); try again. "
        "If callee moved, run ./configure --callee-path=<new folder> or fix callee_python in paths.py"
    ]


# 2. A "try again" that never passes ---------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_a_moved_dependency_or_a_mistyped_python_is_told_the_fix(scaffolded_project, tmp_path, shell):
    tool = make_tool_dir(tmp_path, name="callee", with_venv=False)
    (tool / "src").mkdir()
    (tool / "src" / "run_callee.py").write_text("print('callee ran')\n")
    cli.add_deps_to_paths("paths.py", ["callee"])
    missing_python = tmp_path / "no-such" / "python3"
    run_configure(
        scaffolded_project,
        f"--callee-path={tool}",
        f"--callee-entry={tool / 'src' / 'run_callee.py'}",
        f"--callee-python={missing_python}",
    )
    run = "set -u; . ./setup_callee_env.sh; run_callee predict"

    assert in_shell(scaffolded_project, shell, run) == (
        127,
        "",
        [
            f"callee cannot run right now: {missing_python} does not exist (a redeploy in progress?); try again. "
            "If callee moved, run ./configure --callee-path=<new folder> or fix callee_python in paths.py"
        ],
    )
    tool.rename(tmp_path / "callee.moved")
    assert in_shell(scaffolded_project, shell, run) == (
        127,
        "",
        [
            f"callee is not deployed at {tool} right now (a redeploy in progress?); try again. "
            "If callee moved, run ./configure --callee-path=<new folder>"
        ],
    )


# 3. The caller's own Python ----------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_sourcing_leaves_the_callers_python_alone_and_run_dep_uses_its_own(scaffolded_project, tmp_path, shell):
    apps = tmp_path / "apps"
    version = deploy(apps, "1.0")
    (version / "src" / "run_callee.py").write_text("import os\nprint(os.environ.get('VIRTUAL_ENV'))\n")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    caller = make_venv(tmp_path / "caller-env")
    env = clean_env(VIRTUAL_ENV=str(caller), PYTHONHOME="/caller/home", PATH=f"{caller}/bin:{os.environ['PATH']}")

    rc, out, err = in_shell(
        scaffolded_project,
        shell,
        'before="$VIRTUAL_ENV|$PATH|$PYTHONHOME"; . ./setup_callee_env.sh; '
        'echo "$CALLEE_VENV"; [ "$VIRTUAL_ENV|$PATH|$PYTHONHOME" = "$before" ] && echo unchanged; run_callee',
        env=env,
    )

    assert (rc, err) == (0, [])
    assert out.splitlines() == [str(version / ".venv"), "unchanged", str(version / ".venv")]


# 4. The python3 the raw form runs -------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_a_dependency_without_a_venv_gets_python3_from_outside_every_venv(scaffolded_project, tmp_path, shell):
    tool = make_tool_dir(tmp_path, name="callee", with_venv=False)
    (tool / "run_callee.py").write_text("print('ran')\n")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={tool}", f"--callee-entry={tool / 'run_callee.py'}")
    caller = make_venv(tmp_path / "caller-env")
    (caller / "bin" / "python3").symlink_to(sys.executable)
    system = tmp_path / "system-bin"
    system.mkdir()
    (system / "python3").symlink_to(sys.executable)
    env = clean_env(VIRTUAL_ENV=str(caller), PATH=f"{caller}/bin:{system}:{os.environ['PATH']}")

    rc, out, err = in_shell(scaffolded_project, shell, '. ./setup_callee_env.sh; echo "$CALLEE_PYTHON"', env=env)

    assert (rc, out, err) == (0, f"{system}/python3\n", [])


# 5. An executable entry whose #! line names a Python ------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_an_executable_entry_without_a_venv_runs_with_the_python_its_first_line_names(
    scaffolded_project, tmp_path, shell
):
    tool = make_tool_dir(tmp_path, name="callee", with_venv=False)
    entry = tool / "run_callee"
    entry.write_text(f"#!{sys.executable}\nprint('ran with its own python')\n")
    entry.chmod(0o755)
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={tool}", f"--callee-entry={entry}")
    wrong = tmp_path / "wrong-bin"
    wrong.mkdir()
    (wrong / "python3").write_text("#!/bin/sh\necho wrong python3\n")
    (wrong / "python3").chmod(0o755)
    env = clean_env(PATH=f"{wrong}:{os.environ['PATH']}")

    rc, out, err = in_shell(
        scaffolded_project, shell, '. ./setup_callee_env.sh; echo "[$CALLEE_PYTHON]"; run_callee', env=env
    )

    assert (rc, out, err) == (0, "[]\nran with its own python\n", [])


# 6. Wrong-case names in paths.py ---------------------------------------------------


@pytest.mark.parametrize(
    "lines, message",
    [
        (['app_entry = "src/run_demo.py"'], "app_entry, which ./configure does not read: spell it APP_ENTRY"),
        (
            ['App_Name = "demo"', 'pepx_VENV = "/opt/pepx/.venv"'],
            "App_Name and pepx_VENV, which ./configure does not read: spell them APP_NAME and pepx_venv",
        ),
    ],
)
def test_a_wrong_case_name_in_paths_py_stops_the_run(scaffolded_project, tmp_path, strip_ansi, lines, message):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    run_configure(scaffolded_project, f"--pepx-path={make_tool_dir(tmp_path, 'pepx')}")
    env_before = (scaffolded_project / ".env").read_text()
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text() + "".join(f"{line}\n" for line in lines))

    result = run_configure(scaffolded_project, expect_success=False)

    assert result.returncode == 1
    assert strip_ansi(result.stdout).splitlines() == [f"✗ paths.py sets {message}. Nothing was changed."]
    assert (scaffolded_project / ".env").read_text() == env_before
    # Control: spelled right, it runs.
    paths.write_text(paths.read_text().replace("app_entry", "APP_ENTRY").replace("App_Name", "APP_NAME"))
    paths.write_text(paths.read_text().replace("pepx_VENV", "pepx_venv"))
    assert run_configure(scaffolded_project).returncode == 0


# 7. A hand-written setup script ------------------------------------------------------


def test_a_hand_written_setup_script_of_a_current_dependency_is_moved_aside(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    mine = "#!/bin/sh\nexport PEPX_PATH=/my/own/pepx\n"
    script = scaffolded_project / "setup_pepx_env.sh"
    script.write_text(mine)

    result = run_configure(scaffolded_project, f"--pepx-path={make_tool_dir(tmp_path, 'pepx')}")

    assert (scaffolded_project / "setup_pepx_env.sh.bak").read_text() == mine
    assert "Generated by ./configure" in script.read_text()
    assert (
        "./configure did not write 'setup_pepx_env.sh': moved it to 'setup_pepx_env.sh.bak', "
        "and wrote a new one from paths.py."
    ) in result.stdout
    # Control: the one ./configure wrote is written again without a backup.
    (scaffolded_project / "setup_pepx_env.sh.bak").unlink()
    result = run_configure(scaffolded_project, f"--pepx-path={make_tool_dir(tmp_path, 'pepx2')}")
    assert not (scaffolded_project / "setup_pepx_env.sh.bak").exists()
    assert "moved it to" not in result.stdout


# 8. Modules in libs/ that shadow each other --------------------------------------------

PROBE_UTIL = """\
import core.set_pythonpath
import util
print(util.__file__)
"""


def test_two_lib_folders_with_one_module_name_are_named_once(scaffolded_project, strip_ansi):
    for folder in ("aaa", "zzz"):
        lib = scaffolded_project / "libs" / folder
        lib.mkdir(parents=True, exist_ok=True)
        (lib / "__init__.py").write_text("")
        (lib / "util.py").write_text(f"WHO = {folder!r}\n")
    (scaffolded_project / "libs" / "aaa" / "only_here.py").write_text("")

    out = strip_ansi(run_configure(scaffolded_project).stdout)

    warnings = [line for line in out.splitlines() if "libs/" in line]
    assert warnings == [
        "⚠  Two folders in libs/ provide the module util: import util gets libs/zzz/util.py, "
        "never libs/aaa/util.py. Rename one if code needs the other."
    ]
    # What the warning says is what the tool imports.
    (scaffolded_project / "src" / "probe_util.py").write_text(PROBE_UTIL)
    proc = subprocess.run(
        [sys.executable, "src/probe_util.py"], cwd=scaffolded_project, env=clean_env(), capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == str(scaffolded_project / "libs" / "zzz" / "util.py")
    # Control: one of them renamed, no warning.
    (scaffolded_project / "libs" / "aaa" / "util.py").rename(scaffolded_project / "libs" / "aaa" / "util_a.py")
    assert "libs/" not in strip_ansi(run_configure(scaffolded_project).stdout)


# 9. A uv older than the pin -----------------------------------------------------------


@pytest.mark.parametrize("version, replaced", [("0.10.2", True), ("0.11.9", True), ("0.12.0", False)])
def test_install_sh_replaces_a_uv_older_than_its_pin(deployed, tmp_path, version, replaced):  # noqa: F811
    path = path_without_uv()
    if path is None:
        pytest.skip("cannot hide uv here")
    old_bin, curl_bin, home = tmp_path / "old-uv", tmp_path / "curl-bin", tmp_path / "home"
    for folder in (old_bin, curl_bin, home):
        folder.mkdir()
    (old_bin / "uv").write_text(STUB_UV.replace("0.11.14 (stub)", f"{version} (old)").replace('"$*"', '"old $*"'))
    (old_bin / "uv").chmod(0o755)
    (curl_bin / "curl").write_text(STUB_CURL)
    (curl_bin / "curl").chmod(0o755)
    (tmp_path / "uv-to-install").write_text(STUB_UV)
    curl_log = tmp_path / "curl-calls.log"

    proc, calls = run_install(
        deployed,
        tmp_path,
        path=f"{old_bin}{os.pathsep}{curl_bin}{os.pathsep}{path}",
        HOME=str(home),
        CURL_STUB_LOG=str(curl_log),
        UV_STUB_SOURCE=str(tmp_path / "uv-to-install"),
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    curls = curl_log.read_text().splitlines() if curl_log.exists() else []
    if replaced:
        assert curls == ["-LsSf https://astral.sh/uv/0.11.14/install.sh"]
        assert calls == ["old --version", "--version", "sync --frozen --no-dev"]
        assert f"[install.sh] uv {version} (old) is older than uv 0.11.14; installing uv 0.11.14" in proc.stdout
        assert "[install.sh] uv:   uv 0.11.14 (stub)" in proc.stdout
        assert sorted(str(p.relative_to(home)) for p in home.rglob("*") if p.is_file()) == [".local/bin/uv"]
    else:  # control: a newer uv is kept
        assert curls == []
        assert calls == ["old --version", "old sync --frozen --no-dev"]


# 10. Waiting for paths.lock -----------------------------------------------------------


@pytest.mark.parametrize("pid_known", [True, False])
def test_a_run_says_it_waits_for_the_paths_lock_and_stops_cleanly_on_ctrl_c(scaffolded_project, pid_known):
    held = hold_paths_lock(scaffolded_project)
    try:
        held.truncate(0)  # the pid an earlier run left there
        if pid_known:
            held.write(f"{os.getpid()}\n")
        held.flush()
        run = subprocess.Popen(
            [sys.executable, "src/core/configure.py"],
            cwd=scaffolded_project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "NO_COLOR": "1"},
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
        )
        time.sleep(3)
        run.send_signal(signal.SIGINT)
        out, err = run.communicate(timeout=60)
    finally:
        held.close()

    waiting = (
        f"Waiting for pid {os.getpid()} (cli deps or ./configure) to finish with paths.py..."
        if pid_known
        else "Waiting for another cli deps or ./configure to finish with paths.py..."
    )
    assert (run.returncode, out.splitlines(), err) == (
        130,
        [waiting, "✗ Stopped while waiting for paths.py. Nothing was changed."],
        "",
    )
    # Control: the lock free, the run says nothing about waiting, and writes its pid there.
    result = run_configure(scaffolded_project)
    assert "Waiting" not in result.stdout
    assert env_values(scaffolded_project)["APP_NAME"] == "demo"
    assert (scaffolded_project / ".ngargparser" / "paths.lock").read_text().strip().isdigit()
