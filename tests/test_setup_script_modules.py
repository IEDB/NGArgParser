"""setup_<dep>_env.sh and environment modules, and shellcheck on every kind of setup script.

Each setup script ran `module purge` before `module load <dep>_module`, so sourcing
a second one unloaded the first one's module and the caller's own: the first
dependency then ran without its module. In a shell with no `module` command
(dash, which does not see a function bash exported, or a host without modules) the
script printed "module: not found" and went on, or stopped a bash `set -e` caller
with exit code 127.

A fake `module` shell function stands in for Environment Modules or Lmod: it
records each call and keeps LOADEDMODULES as they do.
"""

import os
import shutil
import subprocess
import sys

import pytest
from test_configure_env import deploy_version, make_tool_dir, make_venv, run_configure

from ngargparser import cli

# The in_tmp_dir fixture hides every program from shutil.which.
WHICH = shutil.which

pytestmark = pytest.mark.skipif(os.name != "posix" or WHICH("bash") is None, reason="needs bash")

FAKE_MODULE = """\
module() {
    echo "$*" >> "$MODULE_LOG"
    case $1 in
        purge) LOADEDMODULES='' ;;
        load) LOADEDMODULES=${LOADEDMODULES:+$LOADEDMODULES:}$2 ;;
    esac
    export LOADEDMODULES
}
"""


def missing_module(name, module):
    return (
        f"setup_{name}_env.sh: {name} needs the environment module {module}, but this shell has no 'module' "
        f"command; load it first (module load {module}), or source this script from a login shell."
    )


def outer_env():
    """This environment without a `module` function, so only the fake one is there."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("BASH_FUNC_module", "LOADEDMODULES", "CALLEE_", "SHARED_")) and k != "BASH_ENV"
    }


@pytest.fixture
def two_with_modules(scaffolded_project, tmp_path):
    """A tool with two dependencies, callee (module callee/1.0) and shared (module shared/2.0).

    Each publishes an entry script, so sourcing its setup script prints nothing
    but what a module warns.
    """
    for name in ("callee", "shared"):
        (tmp_path / name).mkdir()
        (tmp_path / name / ".env").write_text(f"APP_NAME={name}\nAPP_ENTRY=src/run_{name}.py\n")
    cli.add_deps_to_paths("paths.py", ["callee", "shared"])
    run_configure(
        scaffolded_project,
        f"--callee-path={tmp_path / 'callee'}",
        "--callee-module=callee/1.0",
        f"--shared-path={tmp_path / 'shared'}",
        "--shared-module=shared/2.0",
    )
    return scaffolded_project


def source_with_modules(project, tmp_path, shell, scripts):
    """In `shell`, with the fake `module`, load gcc/9 and source `scripts`.

    Returns (the module calls, LOADEDMODULES at the end, stderr).
    """
    log = tmp_path / "module.log"
    log.write_text("")
    body = "; ".join(f". ./{script}" for script in scripts)
    proc = subprocess.run(
        [shell, "-c", f'{FAKE_MODULE}module load gcc/9; {body}; echo "$LOADEDMODULES"'],
        cwd=project,
        env={**outer_env(), "MODULE_LOG": str(log)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return log.read_text().splitlines(), proc.stdout.strip(), proc.stderr


@pytest.mark.parametrize("shell", [s for s in ("bash", "dash") if WHICH(s)])
def test_a_setup_script_loads_its_module_next_to_the_ones_loaded(two_with_modules, tmp_path, shell):
    calls, loaded, stderr = source_with_modules(two_with_modules, tmp_path, shell, ["setup_callee_env.sh"])

    assert calls == ["load gcc/9", "load callee/1.0"]
    assert loaded == "gcc/9:callee/1.0"
    assert stderr == ""


@pytest.mark.parametrize("shell", [s for s in ("bash", "dash") if WHICH(s)])
def test_two_setup_scripts_keep_both_modules(two_with_modules, tmp_path, shell):
    scripts = ["setup_callee_env.sh", "setup_shared_env.sh"]

    calls, loaded, stderr = source_with_modules(two_with_modules, tmp_path, shell, scripts)

    assert not [call for call in calls if call.split()[0] == "purge"]
    assert calls == ["load gcc/9", "load callee/1.0", "load shared/2.0"]
    assert loaded == "gcc/9:callee/1.0:shared/2.0"
    assert stderr == ""


@pytest.mark.skipif(WHICH("dash") is None, reason="needs dash")
def test_dash_does_not_see_a_bash_module_function_and_warns(two_with_modules, tmp_path):
    # bash exports `module` to its children (BASH_FUNC_module%%), but dash, which
    # subprocess.run(..., shell=True) runs on Debian and Ubuntu, never sees it.
    log = tmp_path / "module.log"
    log.write_text("")
    inner = '. ./setup_callee_env.sh; echo "rc=$?"; echo "$CALLEE_PATH"'
    proc = subprocess.run(
        ["bash", "-c", f"{FAKE_MODULE}export -f module; exec dash -c '{inner}'"],
        cwd=two_with_modules,
        env={**outer_env(), "MODULE_LOG": str(log)},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.splitlines() == [missing_module("callee", "callee/1.0")]
    assert proc.stdout.splitlines() == ["rc=0", str(tmp_path / "callee")]
    assert log.read_text() == ""


def test_bash_without_module_warns_and_goes_on_under_set_e(two_with_modules, tmp_path):
    proc = subprocess.run(
        ["bash", "-c", 'set -eu; . ./setup_callee_env.sh; . ./setup_shared_env.sh; echo "$CALLEE_PATH"'],
        cwd=two_with_modules,
        env=outer_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.splitlines() == [
        missing_module("callee", "callee/1.0"),
        missing_module("shared", "shared/2.0"),
    ]
    assert proc.stdout.splitlines() == [str(tmp_path / "callee")]


# The setup scripts were never shellchecked: `CDPATH= cd -P` was SC1007, a warning.

# The shellcheck-py the local fallback runs: the version CI installs
# (SHELLCHECK_PY_VERSION in .gitlab-ci.yml), so both check with one shellcheck.
SHELLCHECK_PY = "shellcheck-py==0.11.0.1"


def find_shellcheck(which=WHICH):
    """The shellcheck command: on PATH, else from the shellcheck-py package (SHELLCHECK_PY) through uvx, offline.

    Without either, the test is skipped, or fails when NGARGPARSER_REQUIRE_SHELLCHECK=1:
    a CI job that sets it must not pass without having run shellcheck.
    """
    found = which("shellcheck")
    if found:
        return [found]
    uvx = which("uvx")
    if uvx:
        command = [uvx, "--offline", "--from", SHELLCHECK_PY, "shellcheck"]
        try:
            probe = subprocess.run([*command, "--version"], capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            probe = None
        if probe is not None and probe.returncode == 0:
            return command
    reason = f"needs shellcheck (or uvx with {SHELLCHECK_PY} cached)"
    if os.environ.get("NGARGPARSER_REQUIRE_SHELLCHECK") == "1":
        pytest.fail(f"{reason}, and NGARGPARSER_REQUIRE_SHELLCHECK=1 is set")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def shellcheck():
    return find_shellcheck()


# The shellcheck test skipped wherever shellcheck was missing, a CI job too:
# the job passed without having checked anything.


def test_a_missing_shellcheck_fails_when_it_is_required(monkeypatch):
    nothing = lambda _name: None  # noqa: E731  # neither shellcheck nor uvx on PATH

    monkeypatch.setenv("NGARGPARSER_REQUIRE_SHELLCHECK", "1")
    with pytest.raises(pytest.fail.Exception, match="NGARGPARSER_REQUIRE_SHELLCHECK=1 is set"):
        find_shellcheck(nothing)
    # Control: without the variable, it skips, as before.
    monkeypatch.delenv("NGARGPARSER_REQUIRE_SHELLCHECK")
    with pytest.raises(pytest.skip.Exception):
        find_shellcheck(nothing)


def test_every_kind_of_setup_script_passes_shellcheck(scaffolded_project, tmp_path, shellcheck):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")  # publishes APP_VENV and APP_ENTRY, read when sourced
    two = make_tool_dir(tmp_path, name="two")  # two bundled virtualenvs: none is chosen, so it follows
    make_venv(two / "venv")
    variants = {
        "moduled": ["--moduled-module=python/3.11"],
        "libbed": ["--libbed-lib-path=/opt/lib"],
        "fixedentry": [],
        "fixedvenv": [f"--fixedvenv-venv={make_venv(tmp_path / 'fixed-env')}"],
        "bundled": [],
        "callee": [],
        "later": [],
        "two": [],
        "fixedpython": [],
    }
    paths_of = {
        "moduled": make_tool_dir(tmp_path, name="moduled", with_venv=False),
        "libbed": make_tool_dir(tmp_path, name="libbed", with_venv=False),
        "fixedentry": make_tool_dir(tmp_path, name="fixedentry", with_venv=False),
        "fixedvenv": make_tool_dir(tmp_path, name="fixedvenv", with_venv=False),
        "bundled": make_tool_dir(tmp_path, name="bundled"),  # one .venv inside: fixed
        "callee": apps / "callee" / "current",
        "later": tmp_path / "not-deployed-yet",  # nothing found: follows, empty for now
        "two": two,
        "fixedpython": make_tool_dir(tmp_path, name="fixedpython", with_venv=False),
    }
    cli.add_deps_to_paths("paths.py", list(variants))
    paths = scaffolded_project / "paths.py"
    paths.write_text(
        paths.read_text()
        + f"fixedentry_entry = '{tmp_path / 'fixedentry' / 'run.py'}'\n"
        + "fixedpython_python = '/usr/bin/python3'\n"
    )
    flags = [f"--{name}-path={paths_of[name]}" for name in variants]
    run_configure(scaffolded_project, *flags, *(flag for extra in variants.values() for flag in extra))
    scripts = {name: scaffolded_project / f"setup_{name}_env.sh" for name in variants}
    texts = {name: script.read_text() for name, script in scripts.items()}
    # Each variant is the one named.
    assert "module load python/3.11" in texts["moduled"]
    assert "LD_LIBRARY_PATH=/opt/lib" in texts["libbed"]
    assert "_ng_entry=" in texts["fixedentry"] and "APP_ENTRY" not in texts["fixedentry"]
    assert "APP_VENV" not in texts["fixedvenv"] and "APP_VENV" not in texts["bundled"]
    assert all("APP_VENV" in texts[name] for name in ("callee", "later", "two"))
    assert "export FIXEDPYTHON_PYTHON=/usr/bin/python3" in texts["fixedpython"]

    for name, script in scripts.items():
        for check in (["bash", "-n"], ["dash", "-n"]):
            if WHICH(check[0]):
                assert subprocess.run([*check, str(script)]).returncode == 0, (check, name)
        proc = subprocess.run(
            [*shellcheck, "-s", "sh", "-S", "warning", str(script)], capture_output=True, text=True, timeout=120
        )
        assert (proc.returncode, proc.stdout) == (0, ""), name


# A setup script sourced the dependency's bin/activate after `module load`. That
# activate first runs `deactivate nondestructive`, which puts back the PATH saved
# when the virtualenv active before it was activated: the module's bin/ was gone
# from PATH, while LOADEDMODULES still listed it. The script now activates the
# virtualenv as dependency_command does: VIRTUAL_ENV, its bin/ first on PATH, no
# PYTHONHOME, and no deactivate function.

# Like FAKE_MODULE, and `module load` puts the module's bin/ ($MODULE_BIN) first on PATH, as a real module does.
FAKE_MODULE_WITH_PATH = """\
module() {
    case $1 in
        load) PATH="$MODULE_BIN${PATH:+:$PATH}"; LOADEDMODULES=${LOADEDMODULES:+$LOADEDMODULES:}$2 ;;
    esac
    export PATH LOADEDMODULES
}
"""


def real_venv(path):
    """A virtualenv made by `python -m venv`, whose bin/activate defines deactivate and saves PATH."""
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(path)], check=True, timeout=120)
    return path


@pytest.fixture(scope="module")
def two_real_venvs(tmp_path_factory):
    """The virtualenv active when the setup script is sourced, and the dependency's own."""
    root = tmp_path_factory.mktemp("venvs")
    return real_venv(root / "caller-env"), real_venv(root / "pydep-env")


@pytest.mark.parametrize("shell", [s for s in ("bash", "dash") if WHICH(s)])
@pytest.mark.parametrize("venv_kind", ["published", "set-in-paths-py"])
def test_a_module_keeps_its_path_when_a_virtualenv_is_already_active(
    scaffolded_project, tmp_path, two_real_venvs, shell, venv_kind
):
    active, dep_venv = two_real_venvs
    dep = tmp_path / "pydep"
    dep.mkdir()
    published = f"APP_VENV={dep_venv}\n" if venv_kind == "published" else ""
    (dep / ".env").write_text(f"APP_ROOT={dep}\nAPP_NAME=pydep\n{published}APP_ENTRY=run.py\n")
    module_bin = tmp_path / "modtool-1.0" / "bin"
    module_bin.mkdir(parents=True)
    (module_bin / "modtool").write_text("#!/bin/sh\necho modtool\n")
    (module_bin / "modtool").chmod(0o755)
    cli.add_deps_to_paths("paths.py", ["pydep"])
    venv_flag = [f"--pydep-venv={dep_venv}"] if venv_kind == "set-in-paths-py" else []
    run_configure(scaffolded_project, f"--pydep-path={dep}", "--pydep-module=modtool/1.0", *venv_flag)

    def source(before):
        report = (
            'command -v modtool || echo "<no modtool>"; echo "$LOADEDMODULES"; echo "${VIRTUAL_ENV-<unset>}"; '
            'echo "${PATH%%:*}"; echo "${PYTHONHOME-<unset>}"; echo "$PYDEP_PYTHON"; '
            'case $(type deactivate 2>/dev/null) in *function*) echo "<deactivate>" ;; *) echo "<no deactivate>" ;; esac'
        )
        proc = subprocess.run(
            [
                shell,
                "-c",
                f"{FAKE_MODULE_WITH_PATH}{before} export PYTHONHOME=/opt/home; . ./setup_pydep_env.sh; {report}",
            ],
            cwd=scaffolded_project,
            env={**{k: v for k, v in outer_env().items() if k != "VIRTUAL_ENV"}, "MODULE_BIN": str(module_bin)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (proc.returncode, proc.stderr) == (0, ""), proc.stderr
        return proc.stdout.splitlines()

    after_activate = source(f'. "{active}/bin/activate";')
    fresh = source("")  # control: no virtualenv active before

    # The virtualenv is exported as PYDEP_VENV, not activated: VIRTUAL_ENV and
    # PYTHONHOME stay as the caller had them, and the module's bin/ comes first.
    for seen, caller_venv in ((after_activate, str(active)), (fresh, "<unset>")):
        assert seen[:6] == [
            str(module_bin / "modtool"),
            "modtool/1.0",
            caller_venv,
            str(module_bin),
            "/opt/home",
            str(dep_venv / "bin" / "python"),
        ]
    # The setup script defines no deactivate function; one is there only when the caller activated a virtualenv.
    assert (after_activate[6], fresh[6]) == ("<deactivate>", "<no deactivate>")
    assert "activate" not in (scaffolded_project / "setup_pydep_env.sh").read_text()
