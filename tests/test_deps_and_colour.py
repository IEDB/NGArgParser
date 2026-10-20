"""`cli deps` and the colour codes cli prints: dependency names whose keys other HPC and Python
tools read, a paths.py lock file that cannot be opened, and colour only on a terminal.

`cli deps add dyld-fallback-library cuda jupyter` was accepted, though their keys are
DYLD_FALLBACK_LIBRARY_PATH, CUDA_PATH and JUPYTER_PATH. A paths.lock that could not be
opened made cli deps go on without the lock, silently, and parallel runs lost changes.
`NO_COLOR=1 cli deps list | cat -v` showed ^[[92m.
"""

import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))
needs_permissions = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply"
)


def deps(action, *names):
    return cli.deps_command(Namespace(deps_action=action, names=list(names)))


def declared(project):
    return sorted(cli._paths_dependencies((project / "paths.py").read_text()))


def run_cli(project, *args, **env):
    """cli <args> in another process, its output to a pipe (not a terminal)."""
    code = "import sys\nfrom ngargparser.cli import main\nsys.argv = ['cli'] + sys.argv[1:]\nsys.exit(main())\n"
    full_env = {**os.environ, "PYTHONPATH": PACKAGE_ROOT, "NGARGPARSER_NO_UPDATE_CHECK": "1", **env}
    full_env = {k: v for k, v in full_env.items() if v is not None}
    return subprocess.run(
        [sys.executable, "-c", code, *args], cwd=project, env=full_env, capture_output=True, text=True, timeout=120
    )


# --- 8. Names whose keys another tool reads -------------------------------------------------------


@pytest.mark.parametrize(
    "name, variable, reader",
    [
        ("dyld-fallback-library", "DYLD_FALLBACK_LIBRARY_PATH", None),
        ("dyld-framework", "DYLD_FRAMEWORK_PATH", None),
        ("cuda", "CUDA_PATH", "CUDA"),
        ("jupyter", "JUPYTER_PATH", "Jupyter"),
        ("conda-envs", "CONDA_ENVS_PATH", "conda"),
        ("spack", "SPACK_PYTHON", "Spack"),
        ("cmake-prefix", "CMAKE_PREFIX_PATH", "CMake"),
        ("reticulate", "RETICULATE_PYTHON", "reticulate"),
    ],
)
def test_a_name_whose_key_another_tool_reads_is_refused(scaffolded_project, strip_ansi, capsys, name, variable, reader):
    assert deps("add", name, "pepx") == 1

    out = strip_ansi(capsys.readouterr().err)
    var = cli.normalize_name(name)
    if reader is None:  # the macOS loader: a variable the system reads
        assert f"{variable} is a variable the system or Python reads, and sourcing setup_{var}_env.sh" in out
    else:
        assert f"{reader} reads {variable} as its own setting, so it could change how {reader} runs in the tool." in out
    assert f"✗ Not added: '{name}' becomes '{var}'" in out
    assert declared(scaffolded_project) == ["pepx"]  # control: pepx is added


def test_names_whose_keys_no_tool_reads_are_still_added(scaffolded_project):
    # Control: JAVA_HOME, R_LIBS, MATLABPATH and MAMBA_ROOT_PREFIX are no key a dependency has.
    assert deps("add", "java", "r-libs", "matlab", "mamba", "dyld-fallback-library") == 1
    assert declared(scaffolded_project) == ["java", "mamba", "matlab", "r_libs"]


# --- 9. A paths.py lock file that cannot be opened ------------------------------------------------


@needs_permissions
def test_a_lock_file_that_cannot_be_opened_stops_cli_deps(scaffolded_project, strip_ansi, capsys):
    state = scaffolded_project / ".ngargparser"
    state.mkdir(exist_ok=True)
    lock = state / "paths.lock"
    lock.write_text("")
    lock.chmod(0o000)
    paths = scaffolded_project / "paths.py"
    before = paths.read_bytes() if paths.exists() else None
    try:
        assert deps("add", "pepx") == 1
        assert deps("remove", "pepx") == 1
    finally:
        lock.chmod(0o644)

    out = strip_ansi(capsys.readouterr().err)
    message = (
        "✗ Cannot open .ngargparser/paths.lock (Permission denied): ask its owner to run chmod a+rw "
        ".ngargparser/paths.lock. Nothing was changed.\n"
    )
    assert out == message * 2
    assert (paths.read_bytes() if paths.exists() else None) == before
    # Control: a new lock file, made under umask 077, which every user can open.
    lock.unlink()
    previous = os.umask(0o077)
    try:
        assert deps("add", "pepx") == 0
    finally:
        os.umask(previous)
    assert declared(scaffolded_project) == ["pepx"]
    assert lock.stat().st_mode & 0o777 == 0o666


@needs_permissions
def test_a_state_folder_where_the_lock_cannot_be_made_stops_cli_deps(scaffolded_project, strip_ansi, capsys):
    state = scaffolded_project / ".ngargparser"
    state.mkdir(exist_ok=True)
    state.chmod(0o555)
    try:
        assert deps("add", "pepx") == 1
    finally:
        state.chmod(0o755)

    assert strip_ansi(capsys.readouterr().err) == (
        "✗ Cannot create .ngargparser/paths.lock (Permission denied): .ngargparser is read-only for you; "
        "run chmod u+rwx .ngargparser. Nothing was changed.\n"
    )
    assert not (scaffolded_project / "paths.py").exists()

    # Control: a tool folder nobody may write (a deployed copy) goes on without the lock, and
    # writing paths.py is what fails there.
    state.chmod(0o555)
    scaffolded_project.chmod(0o555)
    try:
        assert deps("add", "pepx") == 1
    finally:
        scaffolded_project.chmod(0o755)
        state.chmod(0o755)
    out = strip_ansi("".join(capsys.readouterr()))
    assert "paths.lock" not in out
    assert "paths.py" in out and "Permission denied" in out


# --- 7. Colour only on a terminal, and never with NO_COLOR ------------------------------------------


@pytest.mark.parametrize("no_color", [None, "1"])
def test_output_to_a_pipe_has_no_colour_codes(scaffolded_project, no_color):
    assert deps("add", "pepx") == 0

    run = run_cli(scaffolded_project, "deps", "list", NO_COLOR=no_color)

    assert run.returncode == 0, run.stderr
    assert "pepx" in run.stdout
    assert "\033[" not in run.stdout


def test_a_terminal_gets_colour_unless_no_color_is_set(scaffolded_project, capsys, monkeypatch):
    assert deps("add", "pepx") == 0
    capsys.readouterr()
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    for no_color, coloured in ((None, True), ("", True), ("1", False), ("0", False)):
        if no_color is None:
            monkeypatch.delenv("NO_COLOR", raising=False)
        else:
            monkeypatch.setenv("NO_COLOR", no_color)

        assert deps("list") == 0

        out = capsys.readouterr().out
        assert "pepx" in out
        assert ("\033[92m" in out) is coloured, (no_color, out)
