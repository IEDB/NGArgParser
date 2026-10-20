import ast
import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

# The in_tmp_dir fixture hides every program from shutil.which, so keep the real one.
_WHICH = shutil.which

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib


def run_configure(project_dir, *args, expect_success=True):
    result = subprocess.run(
        [sys.executable, "src/core/configure.py", *args],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if expect_success:
        assert result.returncode == 0, result.stderr
    return result


def test_first_run_creates_minimal_env(scaffolded_project):
    (scaffolded_project / ".env").unlink()
    output = run_configure(scaffolded_project).stdout
    assert "created" in output
    env = (scaffolded_project / ".env").read_text()
    assert "APP_ROOT=" in env
    assert "APP_NAME=demo" in env


def test_second_run_with_no_changes_is_a_noop(scaffolded_project):
    run_configure(scaffolded_project)
    before = (scaffolded_project / ".env").read_text()

    output = run_configure(scaffolded_project).stdout

    assert "unchanged" in output
    after = (scaffolded_project / ".env").read_text()
    assert after == before


def test_drifted_env_is_still_corrected(scaffolded_project):
    run_configure(scaffolded_project)
    env_path = scaffolded_project / ".env"
    with open(env_path, "a") as f:
        f.write("SOME_STALE_GARBAGE=1\n")
    assert "SOME_STALE_GARBAGE" in env_path.read_text()

    output = run_configure(scaffolded_project).stdout

    assert "updated" in output
    after = env_path.read_text()
    assert "SOME_STALE_GARBAGE" not in after
    assert "APP_ROOT=" in after
    assert "APP_NAME=demo" in after


def test_flag_sets_value_and_persists_to_paths_py(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])

    output = run_configure(scaffolded_project, "--tcell-class-i-path=/opt/tools/tc1").stdout
    assert "Set tcell_class_i_path = '/opt/tools/tc1' in paths.py" in output

    paths_content = (scaffolded_project / "paths.py").read_text()
    assert "tcell_class_i_path='/opt/tools/tc1'" in paths_content

    env = (scaffolded_project / ".env").read_text()
    assert "TCELL_CLASS_I_PATH=/opt/tools/tc1" in env

    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert "export TCELL_CLASS_I_PATH=/opt/tools/tc1" in script


def test_flag_rerun_with_same_value_is_unchanged(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    run_configure(scaffolded_project, "--tcell-class-i-path=/opt/tools/tc1")

    output = run_configure(scaffolded_project, "--tcell-class-i-path=/opt/tools/tc1").stdout

    assert "Set tcell_class_i_path" not in output
    assert "* .env file unchanged" in output


# A flag rewrites exactly its variable's assignment in paths.py. A one-line
# regex used to break a multi-line one, skip an annotated one, and print "Set"
# either way.


def test_flag_replaces_a_multi_line_assignment(scaffolded_project):
    paths = scaffolded_project / "paths.py"
    paths.write_text("multi_path = (\n    None\n)\nother_path = None\n")

    output = run_configure(scaffolded_project, "--multi-path=/opt/multi", "--other-path=/opt/other").stdout

    assert paths.read_text() == "multi_path='/opt/multi'\nother_path='/opt/other'\n"
    ast.parse(paths.read_text())
    assert "* Set multi_path = '/opt/multi' in paths.py" in output.splitlines()
    assert "MULTI_PATH=/opt/multi" in env_lines(scaffolded_project)


def test_flag_replaces_an_annotated_assignment(scaffolded_project):
    paths = scaffolded_project / "paths.py"
    paths.write_text("annot_path: str = None\n")

    output = run_configure(scaffolded_project, "--annot-path=/opt/annot").stdout

    assert paths.read_text() == "annot_path='/opt/annot'\n"
    assert "* Set annot_path = '/opt/annot' in paths.py" in output.splitlines()
    assert "ANNOT_PATH=/opt/annot" in env_lines(scaffolded_project)


@pytest.mark.parametrize(
    "content, flag, var, reason",
    [
        ("a_path = b_path = None\n", "--a-path=/opt/a", "a_path", "assigns it together with other names"),
        ("if True:\n    a_path = None\n", "--a-path=/opt/a", "a_path", "assigns it inside a block"),
    ],
    ids=["chained", "inside-if"],
)
def test_flag_refuses_an_assignment_it_cannot_rewrite(scaffolded_project, strip_ansi, content, flag, var, reason):
    paths = scaffolded_project / "paths.py"
    paths.write_text(content)
    env_before = (scaffolded_project / ".env").read_text()

    result = run_configure(scaffolded_project, flag, expect_success=False)
    output = strip_ansi(result.stdout)

    assert result.returncode == 1
    assert f"Can't set {var}: paths.py {reason}" in output
    assert "Edit paths.py by hand" in output
    assert not any(line.startswith("* Set ") for line in output.splitlines())
    assert paths.read_text() == content
    assert not (scaffolded_project / "paths.py.bak").exists()
    assert (scaffolded_project / ".env").read_text() == env_before


def test_flag_adds_a_field_paths_py_leaves_out(scaffolded_project, strip_ansi):
    # A hand-written block may hold only <tool>_path; the flag for another field
    # adds that field right after it instead of being refused.
    paths = scaffolded_project / "paths.py"
    paths.write_text("a_path = None\nother = 1\n")

    output = strip_ansi(run_configure(scaffolded_project, "--a-path=/opt/a", "--a-venv=/opt/venv").stdout)

    assert paths.read_text() == "a_path='/opt/a'\na_venv='/opt/venv'\nother = 1\n"
    assert "* Set a_venv = '/opt/venv' in paths.py" in output.splitlines()


def test_flag_backs_up_paths_py_before_rewriting_it(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    before = (scaffolded_project / "paths.py").read_text()

    output = run_configure(scaffolded_project, "--tcell-class-i-path=/opt/tools/tc1").stdout

    assert (scaffolded_project / "paths.py.bak").read_text() == before
    assert "Backup saved to 'paths.py.bak'" in output


# A paths.py ./configure cannot write (a deployed copy, a file made read-only)
# crashed with a PermissionError traceback and left a paths.py.bak behind.

needs_permission_errors = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="root ignores file permissions"
)


@needs_permission_errors
def test_a_read_only_paths_py_is_left_unchanged_with_its_old_backup(scaffolded_project, strip_ansi):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    paths, backup = scaffolded_project / "paths.py", scaffolded_project / "paths.py.bak"
    backup.write_text("# the backup of an earlier run\n")
    before = paths.read_text()
    paths.chmod(0o444)

    result = run_configure(scaffolded_project, "--tcell-class-i-path=/opt/tools/tc1", expect_success=False)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert strip_ansi(result.stdout).splitlines()[-1] == (
        "✗ Could not write 'paths.py': Permission denied. paths.py is unchanged."
    )
    assert paths.read_text() == before
    assert backup.read_text() == "# the backup of an earlier run\n"


@needs_permission_errors
def test_a_read_only_folder_leaves_paths_py_unchanged(scaffolded_project, strip_ansi):
    # paths.py is written last, after .env and the setup scripts, so .env is the
    # first file this run cannot write.
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    before = (scaffolded_project / "paths.py").read_text()
    env_before = (scaffolded_project / ".env").read_text()
    scaffolded_project.chmod(0o555)
    try:
        result = run_configure(scaffolded_project, "--tcell-class-i-path=/opt/tools/tc1", expect_success=False)
    finally:
        scaffolded_project.chmod(0o755)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert strip_ansi(result.stdout).splitlines()[-1] == (
        "✗ Could not write '.env': Permission denied. Nothing else was changed."
    )
    assert (scaffolded_project / "paths.py").read_text() == before
    assert (scaffolded_project / ".env").read_text() == env_before
    assert not (scaffolded_project / "paths.py.bak").exists()
    assert not (scaffolded_project / "setup_tcell_class_i_env.sh").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs RLIMIT_FSIZE and SIGXFSZ")
def test_a_paths_py_that_cannot_be_written_is_left_whole_and_nothing_else_changes(scaffolded_project, strip_ansi):
    # A full disk while paths.py is written. ./configure emptied paths.py and wrote
    # it again in place, after .env and the setup script had the new value, so a
    # file size limit just under the new paths.py left it cut short at the limit.
    # Now the new paths.py is written to a temporary file with .env and the setup
    # script, and renamed over the old one last.
    import resource
    import signal

    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    paths = scaffolded_project / "paths.py"
    limit = 32768
    # Just under the limit; the new value takes it past. .env and the setup script fit.
    paths.write_text(paths.read_text() + "# " + "x" * (limit - 100 - len(paths.read_bytes()) - 3) + "\n")
    paths.chmod(0o640)
    before = paths.read_bytes()
    env_before = (scaffolded_project / ".env").read_bytes()
    value = "/opt/tools/" + "t" * 200

    def limit_file_size():
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)  # fail the write, do not kill the process
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, resource.RLIM_INFINITY))

    result = subprocess.run(
        [sys.executable, "src/core/configure.py", f"--tcell-class-i-path={value}"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        preexec_fn=limit_file_size,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},  # a .pyc cut short breaks later imports
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert strip_ansi(result.stdout).splitlines()[-1] == (
        "✗ Could not write 'paths.py': File too large. Nothing else was changed."
    )
    assert paths.read_bytes() == before
    assert (scaffolded_project / ".env").read_bytes() == env_before
    assert not (scaffolded_project / "setup_tcell_class_i_env.sh").exists()
    assert not (scaffolded_project / "paths.py.bak").exists()
    assert not [p.name for p in scaffolded_project.iterdir() if p.name.endswith(".tmp")]

    # Without the limit, the same run writes all of them, and paths.py keeps its mode.
    output = strip_ansi(run_configure(scaffolded_project, f"--tcell-class-i-path={value}").stdout)
    assert f"* Set tcell_class_i_path = {value!r} in paths.py" in output.splitlines()
    assert f"tcell_class_i_path={value!r}" in paths.read_text().splitlines()
    assert paths.stat().st_mode & 0o777 == 0o640
    assert (scaffolded_project / "paths.py.bak").read_bytes() == before
    assert f"TCELL_CLASS_I_PATH={value}" in env_lines(scaffolded_project)


def test_control_one_line_assignment_changes_only_that_line(scaffolded_project):
    # The shape `cli deps add` writes: same "* Set" line and value as before.
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    before = (scaffolded_project / "paths.py").read_text()

    output = run_configure(scaffolded_project, "--tcell-class-i-path=/opt/tools/tc1").stdout

    assert "* Set tcell_class_i_path = '/opt/tools/tc1' in paths.py" in output.splitlines()
    after = (scaffolded_project / "paths.py").read_text()
    assert after == before.replace("tcell_class_i_path=None", "tcell_class_i_path='/opt/tools/tc1'")


def test_flag_for_undeclared_tool_is_rejected(scaffolded_project):
    result = run_configure(scaffolded_project, "--tcell-class-i-path=/opt/tools/tc1", expect_success=False)
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_help_lists_declared_tool_flags(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    output = run_configure(scaffolded_project, "-h").stdout
    assert "--tcell-class-i-path" in output
    assert "--tcell-class-i-venv" in output
    assert "--tcell-class-i-module" in output
    assert "--tcell-class-i-lib-path" in output


def test_help_with_no_dependencies_shows_hint_not_flags(scaffolded_project):
    output = run_configure(scaffolded_project, "-h").stdout
    assert "cli deps add" in output
    assert "--tcell-class-i" not in output
    assert "-path" not in output


def test_unfilled_path_error_suggests_the_configure_flag(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    result = run_configure(scaffolded_project, expect_success=False)
    assert result.returncode == 1
    assert "./configure --tcell-class-i-path=<path>" in result.stdout


def test_nonexistent_path_warns_but_does_not_block(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])

    result = run_configure(scaffolded_project, "--tcell-class-i-path=/does/not/exist")

    assert result.returncode == 0
    assert "tcell_class_i_path" in result.stdout
    assert "does not exist" in result.stdout

    env = (scaffolded_project / ".env").read_text()
    assert "TCELL_CLASS_I_PATH=/does/not/exist" in env
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert "export TCELL_CLASS_I_PATH=/does/not/exist" in script


def test_existing_path_does_not_warn(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={scaffolded_project}")

    assert result.returncode == 0
    assert "does not exist" not in result.stdout


def test_nonexistent_venv_is_warned_independently_of_path(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])

    result = run_configure(
        scaffolded_project,
        f"--tcell-class-i-path={scaffolded_project}",
        "--tcell-class-i-venv=/does/not/exist/venv",
    )

    assert result.returncode == 0
    assert "tcell_class_i_venv" in result.stdout
    assert "does not exist" in result.stdout


def launcher_env(folder):
    """os.environ with a python3 first on PATH that runs the test interpreter.

    Without a .venv the launcher runs configure.py with python3, and the one on
    PATH may have no python-dotenv (a plain `.venv/bin/python -m pytest`, outside
    `uv run`). The stub sits in `folder`/test-python3.
    """
    stub = folder / "test-python3"
    stub.mkdir(exist_ok=True)
    python3 = stub / "python3"
    if python3.is_symlink():  # never write through a symlink into a real interpreter
        python3.unlink()
    python3.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python3.chmod(0o755)
    return {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ.get('PATH', '')}"}


def run_launcher(project_dir, *args, expect_success=True):
    """Invoke the real `./configure` launcher (not the inner script
    directly) — this is the actual entry point users run."""
    result = subprocess.run(
        ["./configure", *args],
        cwd=project_dir,
        capture_output=True,
        text=True,
        env=launcher_env(project_dir.parent),
    )
    if expect_success:
        assert result.returncode == 0, result.stderr
    return result


def test_launcher_forwards_help_flag(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    output = run_launcher(scaffolded_project, "-h").stdout
    assert "--tcell-class-i-path" in output
    # The inner script's default flow (which would run if the flag were
    # dropped) prints "Detected N dependency tools" -- must not appear here.
    assert "Detected" not in output


def test_launcher_forwards_path_flag_and_persists_it(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])

    run_launcher(scaffolded_project, "--tcell-class-i-path=/opt/tools/tc1")

    paths_content = (scaffolded_project / "paths.py").read_text()
    assert "tcell_class_i_path='/opt/tools/tc1'" in paths_content


def test_launcher_with_no_args_still_works(scaffolded_project):
    output = run_launcher(scaffolded_project).stdout
    assert "Minimal .env file" in output


def make_venv(path, with_marker=True):
    """Create a fake virtualenv at `path`.

    `with_marker=False` leaves out pyvenv.cfg, which is what separates a real
    virtualenv from any directory that happens to hold an activate script.
    """
    (path / "bin").mkdir(parents=True, exist_ok=True)
    (path / "bin" / "activate").write_text(f'export VIRTUAL_ENV="{path}"\n')
    if with_marker:
        (path / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.9.25\n")
    return path


def sourced(project, script, *names):
    """Source `script` in bash from `project` and return the named variables as a dict."""
    echo = "; ".join(f'echo "{name}=${{{name}}}"' for name in names)
    proc = subprocess.run(
        ["bash", "-c", f"source ./{script}; {echo}"], cwd=project, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines())


UNSET = "<unset>"


def no_entry_warning(name, tool_path):
    """The line a setup script prints on stderr when `name` publishes no entry script."""
    return (
        f"setup_{name}_env.sh: {name} publishes no entry script yet (no APP_ENTRY in {tool_path / '.env'}, "
        "or no .env there yet): "
        f"deploy and configure {name}, or set {name}_entry in paths.py"
    )


def installing_warning(name, tool_path):
    """The line a setup script prints on stderr, in place of no_entry_warning, while `tool_path` is being installed."""
    return (
        f"setup_{name}_env.sh: {name} is being installed at {tool_path} right now (a redeploy in progress?); try again"
    )


def not_deployed_warning(name, tool_path):
    """The line a setup script prints on stderr, in place of no_entry_warning, when `tool_path` is not there."""
    return (
        f"setup_{name}_env.sh: {name} is not deployed at {tool_path} right now (a redeploy in progress?); try again. "
        f"If {name} moved, run ./configure --{name.replace('_', '-')}-path=<new folder>"
    )


def sourced_fresh(project, script, *names):
    """Like sourced(), from a shell where the named variables start unset; UNSET when the script leaves one so."""
    clear = f"unset {' '.join(names)}; "
    echo = "; ".join(f'echo "{name}=${{{name}-{UNSET}}}"' for name in names)
    proc = subprocess.run(
        ["bash", "-c", f"{clear}source ./{script}; {echo}"], cwd=project, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines())


def make_tool_dir(tmp_path, name="faketool", with_venv=True, venv_usable=True, venv_name=".venv"):
    """Build a fake peer-tool directory, optionally containing a virtualenv.

    `venv_usable=False` creates an empty venv directory (no bin/activate) --
    the case a plain isdir() check would wrongly accept.
    """
    tool_dir = tmp_path / name
    tool_dir.mkdir(exist_ok=True)
    if with_venv:
        if venv_usable:
            make_venv(tool_dir / venv_name)
        else:
            (tool_dir / venv_name).mkdir(exist_ok=True)
    return tool_dir


def test_bundled_venv_is_auto_detected(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert result.returncode == 0
    assert f"using virtualenv found at {tool_dir}/.venv" in result.stdout

    env = (scaffolded_project / ".env").read_text()
    assert f"TCELL_CLASS_I_VENV={tool_dir}/.venv" in env

    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert f"export TCELL_CLASS_I_VENV={tool_dir}/.venv" in script


def test_auto_detected_venv_is_not_written_to_paths_py(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path)

    run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    # The resolution is per-run and in-memory: paths.py stays portable.
    paths_content = (scaffolded_project / "paths.py").read_text()
    assert "tcell_class_i_venv=None" in paths_content


def test_explicit_venv_is_never_overridden(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path)
    explicit = make_tool_dir(tmp_path, name="pyenv-style-env") / ".venv"

    result = run_configure(
        scaffolded_project,
        f"--tcell-class-i-path={tool_dir}",
        f"--tcell-class-i-venv={explicit}",
    )

    assert result.returncode == 0
    assert "using virtualenv found at" not in result.stdout
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert f"export TCELL_CLASS_I_VENV={explicit}" in script
    assert f"{tool_dir}/.venv" not in script


def test_no_venv_anywhere_warns(scaffolded_project, tmp_path, strip_ansi):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, with_venv=False)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    # The pyenv/conda case: nothing to adopt, so say the tool inherits
    # whatever interpreter is active rather than leaving it to be discovered
    # when the prediction runs.
    assert result.returncode == 0
    assert "using virtualenv found at" not in result.stdout
    assert "does not exist" not in result.stdout
    assert "tcell_class_i has no virtualenv" in strip_ansi(result.stdout)
    assert "./configure --tcell-class-i-venv=<path>" in strip_ansi(result.stdout)


def test_a_dependency_whose_entry_is_not_python_gets_no_venv_warning(scaffolded_project, tmp_path, strip_ansi):
    # A shell-script or program entry runs by itself; no virtualenv is needed for it.
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, with_venv=False)
    (tool_dir / "run.sh").write_text("#!/bin/sh\necho hi\n")

    output = strip_ansi(
        run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}", "--tcell-class-i-entry=run.sh").stdout
    )

    assert "tcell_class_i has no virtualenv" not in output
    # Control: a Python entry still gets the warning.
    (tool_dir / "run.py").write_text("print('hi')\n")
    output = strip_ansi(run_configure(scaffolded_project, "--tcell-class-i-entry=run.py").stdout)
    assert "tcell_class_i has no virtualenv" in output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs RLIMIT_FSIZE and SIGXFSZ")
def test_a_full_disk_never_leaves_a_backup_of_paths_py_cut_short(scaffolded_project):
    # The backup is written to a temporary file and renamed, so a limit too small for
    # it leaves the old backup whole (or none), never a part of paths.py.
    import resource
    import signal

    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text() + "# padding\n" * 600)
    (scaffolded_project / "paths.py.bak").write_text("old backup\n")

    def limit_file_size():
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (4096, resource.RLIM_INFINITY))

    subprocess.run(
        [sys.executable, "src/core/configure.py", "--tcell-class-i-path=/opt/tools/tc1"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        preexec_fn=limit_file_size,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )

    backup = (scaffolded_project / "paths.py.bak").read_text()
    assert backup in ("old backup\n", paths.read_text())
    assert not (scaffolded_project / "paths.py.bak.tmp").exists()


def test_unusable_venv_dir_is_not_adopted(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, venv_usable=False)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert result.returncode == 0
    assert "using virtualenv found at" not in result.stdout
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert f"{tool_dir}/.venv" not in script
    assert sourced_fresh(scaffolded_project, "setup_tcell_class_i_env.sh", "TCELL_CLASS_I_VENV") == {
        "TCELL_CLASS_I_VENV": UNSET
    }


@pytest.mark.parametrize("venv_name", ["venv", "env", ".virtualenv"])
def test_conventional_venv_names_are_adopted(scaffolded_project, tmp_path, venv_name):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, venv_name=venv_name)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert result.returncode == 0
    assert f"using virtualenv found at {tool_dir}/{venv_name}" in result.stdout
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert f"export TCELL_CLASS_I_VENV={tool_dir}/{venv_name}" in script


def test_activate_without_pyvenv_cfg_is_not_adopted(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, with_venv=False)
    # A directory that merely happens to hold an activate script is not a
    # virtualenv, and binding a tool to it would pick the wrong interpreter.
    make_venv(tool_dir / ".venv", with_marker=False)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert result.returncode == 0
    assert "using virtualenv found at" not in result.stdout
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert f"{tool_dir}/.venv" not in script
    assert sourced_fresh(scaffolded_project, "setup_tcell_class_i_env.sh", "TCELL_CLASS_I_VENV") == {
        "TCELL_CLASS_I_VENV": UNSET
    }


def test_a_dependency_venv_made_elsewhere_warns(scaffolded_project, tmp_path, strip_ansi):
    # A dependency folder that was moved keeps a .venv whose activate script
    # points at the old folder: its python runs, activating it does not.
    tool_dir = make_tool_dir(tmp_path)
    (tool_dir / ".venv" / "bin" / "activate").write_text('export VIRTUAL_ENV="/old/place/.venv"\n')
    cli.add_deps_to_paths("paths.py", ["faketool"])
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text().replace("faketool_path=None", f"faketool_path='{tool_dir}'"))

    output = strip_ansi(run_configure(scaffolded_project).stdout)

    venv = tool_dir / ".venv"
    assert (
        f"⚠  {venv} was made for /old/place/.venv, so activating it does not work. Recreate it in "
        f'{tool_dir} (rm -r .venv && uv sync), or run "$FAKETOOL_VENV/bin/python" directly.'
    ) in output.splitlines()
    # Control: a venv made in place gets no warning.
    (tool_dir / ".venv" / "bin" / "activate").write_text(f'export VIRTUAL_ENV="{venv}"\n')
    assert "was made for" not in strip_ansi(run_configure(scaffolded_project).stdout)


def test_several_candidate_venvs_are_never_guessed(scaffolded_project, tmp_path, strip_ansi):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path)
    make_venv(tool_dir / "venv")

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")
    output = strip_ansi(result.stdout)

    assert result.returncode == 0
    assert "using virtualenv found at" not in output
    assert (
        f"⚠  tcell_class_i has more than one virtualenv in '{tool_dir}' (.venv, venv). Not guessing: pick one with "
        "'./configure --tcell-class-i-venv=<path>', or remove venv there (uv sync uses .venv). "
        "Until then, tcell_class_i runs with the system Python."
    ) in output.splitlines()
    # The generic warning would read as a contradiction right after this one.
    assert "has no virtualenv" not in output
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    # Neither candidate is written into the setup script.
    assert str(tool_dir / ".venv") not in script and str(tool_dir / "venv") not in script


def declare_app_venv(tool_dir, venv_path):
    """Write the .env a configured ngargparser tool publishes about itself."""
    (tool_dir / ".env").write_text(f"APP_ROOT={tool_dir}\nAPP_NAME=faketool\nAPP_VENV={venv_path}\n")


def test_venv_declared_in_the_tools_own_env_is_used(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, with_venv=False)
    elsewhere = make_venv(tmp_path / "pyenv-style-env")
    declare_app_venv(tool_dir, elsewhere)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert result.returncode == 0
    assert f"using virtualenv declared in its .env: {elsewhere}" in result.stdout
    env = (scaffolded_project / ".env").read_text()
    assert f"TCELL_CLASS_I_VENV={elsewhere}" in env
    assert sourced(scaffolded_project, "setup_tcell_class_i_env.sh", "TCELL_CLASS_I_VENV")["TCELL_CLASS_I_VENV"] == str(
        elsewhere
    )


def test_declared_venv_beats_a_bundled_one(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path)
    elsewhere = make_venv(tmp_path / "pyenv-style-env")
    declare_app_venv(tool_dir, elsewhere)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert result.returncode == 0
    assert "using virtualenv found at" not in result.stdout
    assert sourced(scaffolded_project, "setup_tcell_class_i_env.sh", "TCELL_CLASS_I_VENV")["TCELL_CLASS_I_VENV"] == str(
        elsewhere
    )


def test_explicit_setting_beats_a_declared_venv(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, with_venv=False)
    declared = make_venv(tmp_path / "declared-env")
    explicit = make_venv(tmp_path / "explicit-env")
    declare_app_venv(tool_dir, declared)

    result = run_configure(
        scaffolded_project,
        f"--tcell-class-i-path={tool_dir}",
        f"--tcell-class-i-venv={explicit}",
    )

    assert result.returncode == 0
    assert "declared in its .env" not in result.stdout
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert f"export TCELL_CLASS_I_VENV={explicit}" in script
    assert str(declared) not in script


def test_declared_venv_that_is_not_a_virtualenv_warns_but_is_used(scaffolded_project, tmp_path, strip_ansi):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, with_venv=False)
    # Exists, but no pyvenv.cfg. Trusted anyway: it may be correct on the
    # deploy target even when it looks wrong here.
    stale = make_venv(tmp_path / "stale-env", with_marker=False)
    declare_app_venv(tool_dir, stale)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")
    output = strip_ansi(result.stdout)

    assert result.returncode == 0
    assert "has no pyvenv.cfg. Using it anyway." in output
    assert sourced(scaffolded_project, "setup_tcell_class_i_env.sh", "TCELL_CLASS_I_VENV")["TCELL_CLASS_I_VENV"] == str(
        stale
    )


def test_tool_env_without_app_venv_falls_through_to_the_bundled_one(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path)
    # The pre-APP_VENV shape: an .env that declares nothing about itself.
    (tool_dir / ".env").write_text(f"APP_ROOT={tool_dir}\nAPP_NAME=faketool\n")

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert result.returncode == 0
    assert f"using virtualenv found at {tool_dir}/.venv" in result.stdout


def test_project_publishes_its_own_venv(scaffolded_project):
    make_venv(scaffolded_project / ".venv")

    result = run_configure(scaffolded_project)

    assert result.returncode == 0
    env = (scaffolded_project / ".env").read_text()
    assert f"APP_VENV={scaffolded_project}/.venv" in env
    # Still a dependency-free project; the extra key must not change that.
    assert "Minimal .env file" in result.stdout


def test_project_without_a_venv_publishes_nothing(scaffolded_project):
    result = run_configure(scaffolded_project)

    assert result.returncode == 0
    assert "APP_VENV" not in (scaffolded_project / ".env").read_text()


def test_project_with_two_venvs_publishes_nothing_and_says_so(scaffolded_project, strip_ansi):
    make_venv(scaffolded_project / ".venv")
    make_venv(scaffolded_project / "venv")

    result = run_configure(scaffolded_project)

    assert result.returncode == 0
    assert "APP_VENV" not in (scaffolded_project / ".env").read_text()
    # Which one to remove, and what a caller gets until then.
    assert (
        "⚠  Found 2 virtualenvs in this tool (.venv, venv); publishing none as APP_VENV. "
        'Remove venv (uv sync uses .venv), or set APP_VENV = "<path>" in paths.py. '
        "Until then, tools that call this one run it with the system Python."
    ) in strip_ansi(result.stdout).splitlines()


def test_project_with_no_dependencies_gains_no_venv_warning(scaffolded_project, strip_ansi):
    result = run_configure(scaffolded_project)

    assert result.returncode == 0
    assert "has no virtualenv" not in strip_ansi(result.stdout)


# APP_ENTRY: each tool publishes its start-up script; a tool that depends on it
# reads it and exports <TOOL>_ENTRY, instead of hard-coding the script's name.


def env_lines(project_dir):
    return (project_dir / ".env").read_text().splitlines()


def env_values(project_dir):
    """The project's .env as a dict; comment lines, such as the first one ./configure writes, are skipped."""
    return dict(line.split("=", 1) for line in env_lines(project_dir) if line and not line.startswith("#"))


def test_project_publishes_its_entry_script(scaffolded_project):
    (scaffolded_project / ".env").unlink()

    run_configure(scaffolded_project)

    assert "APP_ENTRY=src/run_demo.py" in env_lines(scaffolded_project)


def test_entry_script_survives_a_rerun(scaffolded_project):
    run_configure(scaffolded_project)
    output = run_configure(scaffolded_project).stdout

    assert "unchanged" in output
    assert "APP_ENTRY=src/run_demo.py" in env_lines(scaffolded_project)


def drop_pyproject_entry(project_dir):
    """Remove `entry` from [tool.ngargparser], as in a tool generated before it existed."""
    pyproject = project_dir / "pyproject.toml"
    lines = pyproject.read_text().splitlines(keepends=True)
    pyproject.write_text("".join(line for line in lines if not line.startswith("entry =")))


def test_several_run_scripts_publish_nothing_and_warn(scaffolded_project, strip_ansi):
    (scaffolded_project / ".env").unlink()
    drop_pyproject_entry(scaffolded_project)
    (scaffolded_project / "src" / "run_other.py").write_text("print('other')\n")

    output = strip_ansi(run_configure(scaffolded_project).stdout)

    assert (
        "⚠  No APP_ENTRY published: found 2 src/run_*.py scripts. Set 'entry = \"src/<script>\"' under "
        "[tool.ngargparser] in pyproject.toml so tools that depend on this one can find it."
    ) in output.splitlines()
    assert not any(line.startswith("APP_ENTRY=") for line in env_lines(scaffolded_project))


def test_entry_set_by_hand_in_env_is_kept(scaffolded_project):
    # A tool without [tool.ngargparser] entry (generated before it existed) keeps
    # an APP_ENTRY set by hand in .env.
    drop_pyproject_entry(scaffolded_project)
    (scaffolded_project / "src" / "run_other.py").write_text("print('other')\n")
    env_file = scaffolded_project / ".env"
    env_file.write_text(env_file.read_text().replace("APP_ENTRY=src/run_demo.py", "APP_ENTRY=src/run_other.py"))

    run_configure(scaffolded_project)

    assert "APP_ENTRY=src/run_other.py" in env_lines(scaffolded_project)


def test_an_edited_pyproject_entry_beats_the_one_left_in_env(scaffolded_project):
    # .env keeps the APP_ENTRY of the last run; changing entry in pyproject.toml
    # must take effect on the next ./configure, not only in a deployed copy.
    run_configure(scaffolded_project)
    (scaffolded_project / "src" / "run_other.py").write_text("print('other')\n")
    set_pyproject_entry(scaffolded_project, "src/run_other.py")

    run_configure(scaffolded_project)

    assert "APP_ENTRY=src/run_other.py" in env_lines(scaffolded_project)


def test_inherited_entry_is_ignored(scaffolded_project):
    # A tool that starts this one may pass its own APP_ENTRY down; only the file counts.
    result = subprocess.run(
        [sys.executable, "src/core/configure.py"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        env={**os.environ, "APP_ENTRY": "src/run_caller.py"},
    )

    assert result.returncode == 0, result.stderr
    assert "APP_ENTRY=src/run_demo.py" in env_lines(scaffolded_project)


def test_dependency_entry_is_exported(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, with_venv=False)
    (tool_dir / ".env").write_text(f"APP_ROOT={tool_dir}\nAPP_NAME=faketool\nAPP_ENTRY=src/run_faketool.py\n")

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    entry = tool_dir / "src" / "run_faketool.py"
    assert f"start-up script declared in its .env: {entry}" in result.stdout
    assert f"TCELL_CLASS_I_ENTRY={entry}" in env_lines(scaffolded_project)
    assert sourced(scaffolded_project, "setup_tcell_class_i_env.sh", "TCELL_CLASS_I_ENTRY") == {
        "TCELL_CLASS_I_ENTRY": str(entry)
    }
    assert "tcell_class_i_entry" not in (scaffolded_project / "paths.py").read_text()


def test_dependency_without_entry_gets_no_entry_line(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, with_venv=False)
    declare_app_venv(tool_dir, make_venv(tmp_path / "some-env"))  # a .env without APP_ENTRY

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert "start-up script" not in result.stdout
    assert not any(line.startswith("TCELL_CLASS_I_ENTRY=") for line in env_lines(scaffolded_project))
    seen = sourced_fresh(scaffolded_project, "setup_tcell_class_i_env.sh", "TCELL_CLASS_I_ENTRY")
    assert seen == {"TCELL_CLASS_I_ENTRY": UNSET}


# ./configure works without activating the venv: the launcher runs configure.py
# with the project's .venv/bin/python when there is one.


def fake_project_python(project_dir):
    """A .venv/bin/python that records it was used, then runs the test interpreter."""
    marker = project_dir / "used-venv-python"
    python = project_dir / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    if python.is_symlink():  # never write through a symlink into a real interpreter
        python.unlink()
    python.write_text(f'#!/bin/sh\necho used > "{marker}"\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    return marker


def test_launcher_uses_the_project_venv_without_activation(scaffolded_project):
    marker = fake_project_python(scaffolded_project)

    # No Python on PATH at all: only the project's .venv can run configure.py.
    result = subprocess.run(
        ["./configure"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": "/nonexistent"},
    )

    assert result.returncode == 0, result.stderr
    assert marker.exists()
    assert "Minimal .env file" in result.stdout


def test_launcher_without_a_venv_falls_back_to_the_shebang(scaffolded_project):
    # Control: no .venv, so the launcher runs configure.py directly, as before.
    marker = scaffolded_project / "used-venv-python"

    result = run_launcher(scaffolded_project)

    assert "Minimal .env file" in result.stdout
    assert not marker.exists()


def test_missing_dotenv_says_to_run_uv_sync(scaffolded_project, tmp_path):
    broken = tmp_path / "no-dotenv"
    broken.mkdir()
    (broken / "dotenv.py").write_text("raise ImportError('python-dotenv is not installed')\n")

    result = subprocess.run(
        [sys.executable, "src/core/configure.py"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(broken)},
    )

    assert result.returncode == 1
    assert "./configure needs python-dotenv. Run `uv sync` first" in result.stderr
    assert "Traceback" not in result.stderr


# APP_NAME comes from this project's own .env, never from a calling tool.


def test_inherited_app_name_is_ignored(scaffolded_project):
    result = subprocess.run(
        [sys.executable, "src/core/configure.py"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        env={**os.environ, "APP_NAME": "caller"},
    )

    assert result.returncode == 0, result.stderr
    assert "APP_NAME=demo" in env_lines(scaffolded_project)


def test_without_app_name_in_env_the_folder_name_is_used(scaffolded_project):
    # Control: the fallback still applies, and an inherited value doesn't replace it.
    (scaffolded_project / ".env").write_text(f"APP_ROOT={scaffolded_project}\n")

    result = subprocess.run(
        [sys.executable, "src/core/configure.py"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        env={**os.environ, "APP_NAME": "caller"},
    )

    assert result.returncode == 0, result.stderr
    assert "APP_NAME=demo" in env_lines(scaffolded_project)


def test_imported_names_in_paths_py_never_reach_env(scaffolded_project):
    # phbr's paths.py does `from pathlib import Path`; .env used to get PATH=<class 'pathlib.Path'>.
    (scaffolded_project / "paths.py").write_text(
        "from pathlib import Path\n"
        "APP_ROOT = Path(__file__).parent.absolute()\n\n"
        "''' [ Tcell Class I ] '''\n"
        f"tcell_class_i_path='{scaffolded_project}'\n"
    )

    run_configure(scaffolded_project)

    lines = env_lines(scaffolded_project)
    assert not any(line.startswith("PATH=") for line in lines)
    assert f"APP_ROOT={scaffolded_project}" in lines
    assert f"TCELL_CLASS_I_PATH={scaffolded_project}" in lines


@pytest.mark.parametrize("folder, app_name", [("ng_my-app-local", "my-app"), ("ng_myapp-local", "myapp")])
def test_app_name_from_a_build_folder_name(scaffolded_project, folder, app_name):
    # Without a .env or [tool.ngargparser] app_name, a folder named ng_<name>-local (a
    # build copy) names the tool <name>, hyphens included.
    renamed = scaffolded_project.parent / folder
    scaffolded_project.rename(renamed)
    (renamed / ".env").unlink()
    pyproject = renamed / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('app_name = "demo"\n', ""))

    run_configure(renamed)

    assert f"APP_NAME={app_name}" in (renamed / ".env").read_text().splitlines()


def test_a_deployed_tool_keeps_its_name(scaffolded_project):
    # A tarball ships pyproject.toml but no .env, and the orchestrator installs it to
    # <name>/<version>/. The name comes from [tool.ngargparser] app_name, not the folder.
    deployed = scaffolded_project.parent / "apps" / "demo" / "1.0.0"
    deployed.parent.mkdir(parents=True)
    scaffolded_project.rename(deployed)
    (deployed / ".env").unlink()

    run_configure(deployed)

    assert "APP_NAME=demo" in (deployed / ".env").read_text().splitlines()


def test_pyproject_app_name_wins_over_a_stale_one_in_env(scaffolded_project):
    # An edit to [tool.ngargparser] app_name used to stop at .env: the APP_NAME
    # already there won, as it did for entry before.
    (scaffolded_project / ".env").write_text("APP_NAME=from-env\n")

    run_configure(scaffolded_project)

    assert "APP_NAME=demo" in (scaffolded_project / ".env").read_text().splitlines()


def test_without_a_pyproject_app_name_the_one_in_env_is_kept(scaffolded_project):
    # Control: an older tool has no app_name, only the APP_NAME in its .env.
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('app_name = "demo"\n', ""))
    (scaffolded_project / ".env").write_text("APP_NAME=from-env\n")

    run_configure(scaffolded_project)

    assert "APP_NAME=from-env" in (scaffolded_project / ".env").read_text().splitlines()


def test_app_name_in_paths_py_wins_like_app_venv_and_app_entry(scaffolded_project):
    (scaffolded_project / "paths.py").write_text('APP_NAME = "custom"\n')
    assert "APP_NAME=demo" in env_lines(scaffolded_project)

    run_configure(scaffolded_project)

    assert "APP_NAME=custom" in env_lines(scaffolded_project)


# The orchestrator puts a new version of a dependency behind the same <dep>_path (a
# `current` link) and never re-runs the caller's ./configure. The setup script reads
# what the dependency publishes in its .env when it is sourced, so it follows.


def deploy_version(apps, version, entry):
    version_dir = apps / "callee" / version
    venv = make_venv(version_dir / ".venv")
    (version_dir / "src").mkdir(parents=True)
    (version_dir / "src" / entry).write_text("")
    (version_dir / ".env").write_text(
        f"APP_ROOT={version_dir}\nAPP_NAME=callee\nAPP_VENV={venv}\nAPP_ENTRY=src/{entry}\n"
    )
    current = apps / "callee" / "current"
    if current.is_symlink():
        current.unlink()
    current.symlink_to(version)
    return venv


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks and bash")
def test_setup_script_follows_a_redeployed_dependency(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")

    new_venv = deploy_version(apps, "1.1", "run_callee_v2.py")  # the entry script is renamed

    seen = sourced(scaffolded_project, "setup_callee_env.sh", "CALLEE_VENV", "CALLEE_ENTRY")
    assert seen["CALLEE_VENV"] == str(new_venv)
    # In the version folder the virtualenv is in, not under the `current` link.
    assert seen["CALLEE_ENTRY"] == str(apps / "callee" / "1.1" / "src" / "run_callee_v2.py")


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks and bash")
def test_setup_script_falls_back_to_what_configure_saw(scaffolded_project, tmp_path):
    # Control: with the dependency's .env not usable when the script is sourced
    # (written for another folder), the values ./configure resolved are used.
    # One that is gone is a redeploy in progress: none of them is used then.
    apps = tmp_path / "apps"
    venv = deploy_version(apps, "1.0", "run_callee.py")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    (apps / "callee" / "1.0" / ".env").write_text(f"APP_ROOT={tmp_path / 'elsewhere'}\nAPP_VENV=/elsewhere/.venv\n")

    seen = sourced(scaffolded_project, "setup_callee_env.sh", "CALLEE_VENV", "CALLEE_ENTRY")
    assert seen["CALLEE_VENV"] == str(venv)
    assert seen["CALLEE_ENTRY"] == str(apps / "callee" / "1.0" / "src" / "run_callee.py")


# A caller is often deployed before its dependency. Its setup script still reads
# the dependency's .env when sourced, so the dependency is found once it arrives.


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks and bash")
def test_setup_script_finds_a_dependency_deployed_after_configure(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")  # not deployed yet
    script = "setup_callee_env.sh"
    assert subprocess.run(["bash", "-n", script], cwd=scaffolded_project).returncode == 0

    before = sourced_fresh(scaffolded_project, script, "CALLEE_VENV", "CALLEE_ENTRY")
    venv = deploy_version(apps, "1.0", "run_callee.py")
    after = sourced_fresh(scaffolded_project, script, "CALLEE_VENV", "CALLEE_ENTRY")

    # Nothing to find yet: no virtualenv activated, and no entry (not the bare folder).
    assert before == {"CALLEE_VENV": UNSET, "CALLEE_ENTRY": UNSET}
    assert after == {
        "CALLEE_VENV": str(venv),
        "CALLEE_ENTRY": str(apps / "callee" / "1.0" / "src" / "run_callee.py"),
    }


# The caller's .env keeps copies of what a dependency published. app_env.py reads
# those keys again from the dependency's .env, so the caller's Python follows a
# redeploy too, not only the setup script.

PROBE_DEPS = """\
import json, os
import core.set_pythonpath
print(json.dumps({k: os.environ.get(k) for k in ("CALLEE_PATH", "CALLEE_VENV", "CALLEE_ENTRY")}))
"""


def probe_deps(project):
    (project / "src" / "probe_deps.py").write_text(PROBE_DEPS)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CALLEE_") and k not in ("APP_ROOT", "APP_NAME")}
    proc = subprocess.run(
        [sys.executable, str(project / "src" / "probe_deps.py")], env=env, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks")
def test_python_sees_a_redeployed_dependency_not_the_copy_in_env(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    stored = env_values(scaffolded_project)
    assert stored["CALLEE_ENTRY"] == str(apps / "callee" / "1.0" / "src" / "run_callee.py")

    new_venv = deploy_version(apps, "1.1", "run_callee_v2.py")  # the entry script is renamed
    seen = probe_deps(scaffolded_project)

    assert seen["CALLEE_ENTRY"] == str(apps / "callee" / "1.1" / "src" / "run_callee_v2.py")
    assert os.path.exists(seen["CALLEE_ENTRY"])
    assert seen["CALLEE_VENV"] == str(new_venv)

    # The dependency's .env gone (1.1 redeployed): the values ./configure stored name 1.0,
    # the folder `current` pointed to then, which is still there. Not used: they run 1.0.
    published = (apps / "callee" / "1.1" / ".env").read_text()
    (apps / "callee" / "1.1" / ".env").unlink()
    seen = probe_deps(scaffolded_project)
    assert (seen["CALLEE_ENTRY"], seen["CALLEE_VENV"]) == (None, None)
    # Control: stored while `current` pointed at 1.1, they lie in its folder, and are used.
    (apps / "callee" / "1.1" / ".env").write_text(published)
    run_configure(scaffolded_project)
    stored = env_values(scaffolded_project)
    (apps / "callee" / "1.1" / ".env").unlink()
    seen = probe_deps(scaffolded_project)
    assert seen["CALLEE_ENTRY"] == stored["CALLEE_ENTRY"] == str(apps / "callee" / "1.1" / "src" / "run_callee_v2.py")
    assert seen["CALLEE_VENV"] == stored["CALLEE_VENV"] == str(new_venv)


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks and bash")
def test_values_set_in_paths_py_stay_fixed(scaffolded_project, tmp_path):
    # <dep>_venv and <dep>_entry in paths.py win over what the dependency publishes,
    # in .env, in the setup script, and in the tool's Python.
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")
    fixed_venv = make_venv(tmp_path / "fixed-env")
    fixed_entry = tmp_path / "fixed" / "run_fixed.py"
    cli.add_deps_to_paths("paths.py", ["callee"])
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text() + f"callee_entry = '{fixed_entry}'\n")

    result = run_configure(
        scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}", f"--callee-venv={fixed_venv}"
    )
    deploy_version(apps, "1.1", "run_callee_v2.py")

    assert "start-up script declared in its .env" not in result.stdout
    stored = env_values(scaffolded_project)
    assert stored["CALLEE_ENTRY"] == str(fixed_entry)
    assert "CALLEE_ENTRY" not in stored.get("APP_KEYS_FROM_DEPS", "").split(",")
    assert "CALLEE_VENV" not in stored.get("APP_KEYS_FROM_DEPS", "").split(",")
    assert sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_VENV", "CALLEE_ENTRY") == {
        "CALLEE_VENV": str(fixed_venv),
        "CALLEE_ENTRY": str(fixed_entry),
    }
    seen = probe_deps(scaffolded_project)
    assert seen["CALLEE_ENTRY"] == str(fixed_entry)
    assert seen["CALLEE_VENV"] == str(fixed_venv)


# The launcher works on its own folder, wherever it is started from.


def test_launcher_configures_its_own_folder_from_anywhere(scaffolded_project, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (scaffolded_project / ".env").unlink()

    result = subprocess.run(
        [str(scaffolded_project / "configure")],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        timeout=60,
        env=launcher_env(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    assert f"APP_ROOT={scaffolded_project}" in env_lines(scaffolded_project)
    assert not (elsewhere / ".env").exists()


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks")
def test_launcher_from_another_tool_configures_the_launchers_tool(scaffolded_project, tmp_path):
    # cd tool1 && ../tool2/configure configures tool2, and so does a symlink to it.
    other = tmp_path / "other"
    other.mkdir()
    (other / "src" / "core").mkdir(parents=True)
    (other / "src" / "core" / "configure.py").write_text("raise SystemExit('configured the wrong tool')\n")
    (other / "cfg").symlink_to(os.path.relpath(scaffolded_project / "configure", other))
    (scaffolded_project / ".env").unlink()

    relative = subprocess.run(
        [os.path.join("..", "demo", "configure")],
        cwd=other,
        capture_output=True,
        text=True,
        timeout=60,
        env=launcher_env(tmp_path),
    )
    assert relative.returncode == 0, relative.stderr
    assert f"APP_ROOT={scaffolded_project}" in env_lines(scaffolded_project)

    (scaffolded_project / ".env").unlink()
    linked = subprocess.run(
        ["./cfg"], cwd=other, capture_output=True, text=True, timeout=60, env=launcher_env(tmp_path)
    )
    assert linked.returncode == 0, linked.stderr
    assert f"APP_ROOT={scaffolded_project}" in env_lines(scaffolded_project)
    assert not (other / ".env").exists()


# ./configure deletes only the setup scripts it wrote. A hand-written one is
# gitignored like the rest, so deleting it would lose it.


def test_a_hand_written_setup_script_survives_and_is_named(scaffolded_project, strip_ansi):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    run_configure(scaffolded_project, "--pepx-path=/opt/pepx")
    assert (scaffolded_project / "setup_pepx_env.sh").exists()
    mine = scaffolded_project / "setup_mine_env.sh"
    mine.write_text("#!/bin/bash\nexport MINE=1\n")
    (scaffolded_project / "paths.py").write_text("# no dependencies\n")

    output = strip_ansi(run_configure(scaffolded_project).stdout)

    assert not (scaffolded_project / "setup_pepx_env.sh").exists()
    assert "* Removed shell script for 'pepx' (no longer in paths.py)" in output.splitlines()
    assert mine.read_text() == "#!/bin/bash\nexport MINE=1\n"
    kept = [line for line in output.splitlines() if "setup_mine_env.sh" in line]
    assert kept == [
        "⚠  Kept 'setup_mine_env.sh': 'mine' is not in paths.py, but ./configure did not write this file. "
        "Delete it if nothing uses it."
    ]


# A paths.py that does not parse names the line instead of printing a traceback.


def test_a_paths_py_syntax_error_names_the_line(scaffolded_project, strip_ansi):
    (scaffolded_project / "paths.py").write_text("a_path = None\nb_path = '/opt/b\nc_path = None\n")
    env_before = (scaffolded_project / ".env").read_text()

    result = run_configure(scaffolded_project, expect_success=False)
    output = strip_ansi(result.stdout).splitlines()

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert output[0].startswith("✗ paths.py line 2: ")
    assert output[1] == "    b_path = '/opt/b"
    assert (scaffolded_project / ".env").read_text() == env_before


# [tool.ngargparser] entry in pyproject.toml ships in the tarball, unlike .env, so a
# deployed copy with several src/run_*.py scripts still publishes its start-up script.


def set_pyproject_entry(project_dir, value, quote='"'):
    drop_pyproject_entry(project_dir)
    pyproject = project_dir / "pyproject.toml"
    text = pyproject.read_text()
    pyproject.write_text(
        text.replace("[tool.ngargparser]\n", f"[tool.ngargparser]\nentry = {quote}{value}{quote}\n", 1)
    )


@pytest.mark.parametrize("name, script", [("demo", "run_demo.py"), ("my-app", "run_my_app.py")])
def test_a_generated_tool_names_its_entry_script_in_pyproject(in_tmp_dir, name, script):
    assert not cli.startapp_command(Namespace(project_name=name))
    project = in_tmp_dir / name
    table = tomllib.loads((project / "pyproject.toml").read_text())["tool"]["ngargparser"]

    assert table["entry"] == f"src/{script}"
    assert (project / table["entry"]).is_file()


def test_a_deployed_copy_with_several_scripts_publishes_the_pyproject_entry(scaffolded_project, strip_ansi):
    (scaffolded_project / ".env").unlink()  # a tarball ships no .env
    (scaffolded_project / "src" / "run_other.py").write_text("print('other')\n")
    set_pyproject_entry(scaffolded_project, "src/run_other.py")

    output = strip_ansi(run_configure(scaffolded_project).stdout)

    assert "APP_ENTRY=src/run_other.py" in env_lines(scaffolded_project)
    assert "No APP_ENTRY published" not in output
    # The pyproject entry also beats an APP_ENTRY left in .env.
    env_file = scaffolded_project / ".env"
    env_file.write_text(env_file.read_text().replace("APP_ENTRY=src/run_other.py", "APP_ENTRY=src/run_demo.py"))
    run_configure(scaffolded_project)
    assert "APP_ENTRY=src/run_other.py" in env_lines(scaffolded_project)


def test_app_name_on_the_first_line_after_a_byte_order_mark(scaffolded_project):
    # Without a TOML library the line reader decides; a BOM must not hide line 1.
    (scaffolded_project / ".env").unlink()
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_bytes(b'\xef\xbb\xbf[tool.ngargparser]\napp_name = "bom-tool"\n')
    renamed = scaffolded_project.parent / "1.0.0"
    scaffolded_project.rename(renamed)

    run_configure(renamed)

    assert "APP_NAME=bom-tool" in env_lines(renamed)


def test_a_pyproject_entry_naming_a_missing_file_warns(scaffolded_project, strip_ansi):
    (scaffolded_project / ".env").unlink()
    set_pyproject_entry(scaffolded_project, "src/run_gone.py")

    output = strip_ansi(run_configure(scaffolded_project).stdout).splitlines()

    assert (
        "⚠  [tool.ngargparser] entry in pyproject.toml names 'src/run_gone.py', which does not exist. "
        "Publishing src/run_demo.py instead."
    ) in output
    assert "APP_ENTRY=src/run_demo.py" in env_lines(scaffolded_project)

    # With several scripts there is nothing to fall back on.
    (scaffolded_project / ".env").unlink()
    (scaffolded_project / "src" / "run_other.py").write_text("print('other')\n")
    output = strip_ansi(run_configure(scaffolded_project).stdout).splitlines()
    assert (
        "⚠  [tool.ngargparser] entry in pyproject.toml names 'src/run_gone.py', which does not exist. "
        "Publishing nothing."
    ) in output
    # Only that warning: the one that says to set entry is for a tool that has none.
    assert not any("No APP_ENTRY published" in line for line in output)
    assert not any(line.startswith("APP_ENTRY=") for line in env_lines(scaffolded_project))


def test_an_app_entry_in_paths_py_naming_a_missing_file_warns(scaffolded_project, strip_ansi):
    (scaffolded_project / "paths.py").write_text("APP_ENTRY = 'src/run_gone.py'\n")

    output = strip_ansi(run_configure(scaffolded_project).stdout).splitlines()

    assert "⚠  APP_ENTRY in paths.py names 'src/run_gone.py', which does not exist. Publishing it anyway." in output
    assert "APP_ENTRY=src/run_gone.py" in env_lines(scaffolded_project)


# [tool.ngargparser] values may use either TOML string style; 'callee' used to give a
# deployed tool the name of its version folder.

WITHOUT_TOML_LIBRARY = (
    "import runpy, sys; sys.modules['tomllib'] = sys.modules['tomli'] = None; "
    "sys.argv = ['src/core/configure.py']; runpy.run_path('src/core/configure.py', run_name='__main__')"
)


@pytest.mark.parametrize("toml_library", [True, False], ids=["toml-library", "line-reader"])
def test_single_quoted_pyproject_values_are_read(scaffolded_project, toml_library):
    deployed = scaffolded_project.parent / "apps" / "callee" / "1.0.0"
    deployed.parent.mkdir(parents=True)
    scaffolded_project.rename(deployed)
    (deployed / ".env").unlink()
    (deployed / "src" / "run_other.py").write_text("print('other')\n")
    pyproject = deployed / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('app_name = "demo"', "app_name = 'callee'  # literal string"))
    set_pyproject_entry(deployed, "src/run_other.py", quote="'")

    command = (
        [sys.executable, "src/core/configure.py"] if toml_library else [sys.executable, "-c", WITHOUT_TOML_LIBRARY]
    )
    result = subprocess.run(command, cwd=deployed, capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr
    assert "APP_NAME=callee" in env_lines(deployed)
    assert "APP_ENTRY=src/run_other.py" in env_lines(deployed)


# A tool moved or copied with its .venv: the virtualenv still points at the old
# folder, so activating it gives another Python. ./configure no longer publishes it.


def test_a_moved_tool_does_not_publish_the_venv_it_brought(scaffolded_project, strip_ansi):
    old_venv = scaffolded_project / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(old_venv)], check=True, timeout=120)
    run_configure(scaffolded_project)
    assert f"APP_VENV={old_venv}" in env_lines(scaffolded_project)
    moved = scaffolded_project.parent / "demo-moved"
    scaffolded_project.rename(moved)
    new_venv = moved / ".venv"

    output = strip_ansi(run_configure(moved).stdout).splitlines()

    assert (
        f"⚠  {new_venv} was made for {old_venv}; recreate it: rm -r .venv && uv sync, then run ./configure again. "
        "Until then it is not published as APP_VENV."
    ) in output
    assert not any(line.startswith("APP_VENV=") for line in env_lines(moved))

    # Made again in its new folder, it is published.
    shutil.rmtree(new_venv)
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(new_venv)], check=True, timeout=120)
    output = run_configure(moved).stdout
    assert "was made for" not in output
    assert f"APP_VENV={new_venv}" in env_lines(moved)


def test_a_venv_whose_pyvenv_cfg_names_another_folder_is_not_published(scaffolded_project, strip_ansi):
    venv = scaffolded_project / ".venv"
    (venv / "bin").mkdir(parents=True)
    # This activate works out its folder when sourced, so only pyvenv.cfg names one.
    (venv / "bin" / "activate").write_text('VIRTUAL_ENV="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n')
    cfg = venv / "pyvenv.cfg"
    cfg.write_text('home = /usr/bin\ncommand = /usr/bin/python3 -m venv --prompt="demo" /old/place/.venv\n')

    output = strip_ansi(run_configure(scaffolded_project).stdout)

    assert f"⚠  {venv} was made for /old/place/.venv; recreate it: rm -r .venv && uv sync" in output
    assert not any(line.startswith("APP_VENV=") for line in env_lines(scaffolded_project))
    # The same virtualenv made in this folder is published.
    cfg.write_text(f'home = /usr/bin\ncommand = /usr/bin/python3 -m venv --prompt="demo" {venv}\n')
    output = run_configure(scaffolded_project).stdout
    assert "was made for" not in output
    assert f"APP_VENV={venv}" in env_lines(scaffolded_project)


# setup_<dep>_env.sh exports <DEP>_VENV, so a caller can run "$<DEP>_VENV/bin/python"
# whichever virtualenv was activated last.


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_setup_scripts_export_each_dependency_venv(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    published = deploy_version(apps, "1.0", "run_callee.py")  # APP_VENV in its .env
    fixed = make_venv(tmp_path / "fixed-env")  # --other-venv, in paths.py
    bundled = make_tool_dir(tmp_path, name="bundled")  # a .venv inside the tool
    cli.add_deps_to_paths("paths.py", ["callee", "other", "bundled"])
    run_configure(
        scaffolded_project,
        f"--callee-path={apps / 'callee' / 'current'}",
        f"--other-path={tmp_path}",
        f"--other-venv={fixed}",
        f"--bundled-path={bundled}",
    )

    names = ("CALLEE_VENV", "OTHER_VENV", "BUNDLED_VENV", "VIRTUAL_ENV")
    clear = f"unset {' '.join(names)}; "
    sources = "source ./setup_callee_env.sh; source ./setup_other_env.sh; source ./setup_bundled_env.sh; "
    echo = "; ".join(f'echo "{name}=${{{name}-{UNSET}}}"' for name in names)
    proc = subprocess.run(
        ["bash", "-c", clear + sources + echo], cwd=scaffolded_project, capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stderr
    assert dict(line.split("=", 1) for line in proc.stdout.splitlines()) == {
        "CALLEE_VENV": str(published),
        "OTHER_VENV": str(fixed),
        "BUNDLED_VENV": str(bundled / ".venv"),
        "VIRTUAL_ENV": UNSET,  # none is activated in the caller's shell: run_<dep> activates its own
    }


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks and bash")
def test_setup_script_exports_the_venv_of_a_dependency_deployed_after_configure(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")  # not deployed yet

    before = sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_VENV")
    venv = deploy_version(apps, "1.0", "run_callee.py")
    after = sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_VENV")

    assert before == {"CALLEE_VENV": UNSET}
    assert after == {"CALLEE_VENV": str(venv)}


# Caller and callee can both depend on a tool of one name, at different paths.
# The callee's setup script, finding no virtualenv or entry for its own, left the
# caller's SHARED_VENV and SHARED_ENTRY in place.


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_setup_script_unsets_values_it_cannot_find_instead_of_keeping_inherited_ones(scaffolded_project, tmp_path):
    shared_old = make_tool_dir(tmp_path, name="shared_old", with_venv=False)
    shared_new = make_venv(tmp_path / "shared_new" / ".venv").parent
    cli.add_deps_to_paths("paths.py", ["shared"])
    run_configure(scaffolded_project, f"--shared-path={shared_old}")
    inherited = (
        f"export SHARED_PATH={shared_new} SHARED_VENV={shared_new / '.venv'} "
        f"SHARED_ENTRY={shared_new / 'src' / 'run_shared.py'}; "
    )
    names = ("SHARED_PATH", "SHARED_VENV", "SHARED_ENTRY")
    echo = "; ".join(f'echo "{name}=${{{name}-{UNSET}}}"' for name in names)

    def source():
        proc = subprocess.run(
            ["bash", "-c", f"set -eu; {inherited}source ./setup_shared_env.sh; {echo}"],
            cwd=scaffolded_project,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        return dict(line.split("=", 1) for line in proc.stdout.splitlines())

    assert source() == {"SHARED_PATH": str(shared_old), "SHARED_VENV": UNSET, "SHARED_ENTRY": UNSET}
    # Control: once shared_old publishes its own values, they are exported.
    make_venv(shared_old / "env-of-its-own")
    (shared_old / ".env").write_text(f"APP_VENV={shared_old / 'env-of-its-own'}\nAPP_ENTRY=src/run_old.py\n")
    assert source() == {
        "SHARED_PATH": str(shared_old),
        "SHARED_VENV": str(shared_old / "env-of-its-own"),
        "SHARED_ENTRY": str(shared_old / "src" / "run_old.py"),
    }


# A caller that uses `set -u` sources the setup scripts too. `export
# LD_LIBRARY_PATH=<lib>:$LD_LIBRARY_PATH` stopped it with "LD_LIBRARY_PATH:
# unbound variable", and without set -u left a trailing ':', which makes the
# loader search the current folder.


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_lib_path_is_prepended_without_a_stray_colon(scaffolded_project, tmp_path):
    tool = make_tool_dir(tmp_path, name="libtool", with_venv=False)
    cli.add_deps_to_paths("paths.py", ["libtool"])
    run_configure(scaffolded_project, f"--libtool-path={tool}", "--libtool-lib-path=/opt/lib")

    def library_path(setup):
        proc = subprocess.run(
            ["bash", "-c", f'{setup}; source ./setup_libtool_env.sh; printf "%s" "$LD_LIBRARY_PATH"'],
            cwd=scaffolded_project,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    assert library_path("set -u; unset LD_LIBRARY_PATH") == "/opt/lib"
    assert library_path("unset LD_LIBRARY_PATH") == "/opt/lib"
    assert library_path("export LD_LIBRARY_PATH=") == "/opt/lib"
    assert library_path("set -u; export LD_LIBRARY_PATH=/usr/local/lib") == "/opt/lib:/usr/local/lib"


@pytest.mark.skipif(os.name != "posix" or shutil.which("bash") is None, reason="needs symlinks and bash")
def test_every_kind_of_setup_script_sources_under_set_u(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")  # publishes APP_VENV and APP_ENTRY
    fixed_venv = make_venv(tmp_path / "fixed-env")
    bundled = make_tool_dir(tmp_path, name="bundled")  # a .venv inside it, no .env
    plain = make_tool_dir(tmp_path, name="plain", with_venv=False)
    cli.add_deps_to_paths("paths.py", ["callee", "later", "fixed", "bundled", "plain"])
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text() + f"fixed_entry = '{tmp_path / 'fixed' / 'run_fixed.py'}'\n")
    run_configure(
        scaffolded_project,
        f"--callee-path={apps / 'callee' / 'current'}",
        f"--later-path={tmp_path / 'not-deployed-yet'}",
        f"--fixed-path={tmp_path / 'fixed'}",
        f"--fixed-venv={fixed_venv}",
        f"--bundled-path={bundled}",
        f"--plain-path={plain}",
        "--plain-lib-path=/opt/lib",
    )
    scripts = sorted(path.name for path in scaffolded_project.glob("setup_*_env.sh"))
    assert len(scripts) == 5

    for script in scripts:
        for check in ("bash", "-n"), ("dash", "-n"):
            if _WHICH(check[0]):
                assert subprocess.run([*check, script], cwd=scaffolded_project).returncode == 0, (check, script)
    # Each on its own, then all of them in one shell, as a caller sources several.
    # The ones with no entry script say so on stderr, and go on.
    clear = "unset LD_LIBRARY_PATH VIRTUAL_ENV CALLEE_VENV CALLEE_ENTRY LATER_VENV LATER_ENTRY"
    no_entry = {
        "setup_later_env.sh": not_deployed_warning("later", tmp_path / "not-deployed-yet"),
        # A .venv inside it and no .env: a tool being installed (a redeploy).
        "setup_bundled_env.sh": installing_warning("bundled", bundled),
        "setup_plain_env.sh": no_entry_warning("plain", plain),
    }
    for sources in [[script] for script in scripts] + [scripts]:
        body = "; ".join(f"source ./{script}" for script in sources)
        proc = subprocess.run(
            ["bash", "-c", f"set -euo pipefail; {clear}; {body}; echo sourced"],
            cwd=scaffolded_project,
            capture_output=True,
            text=True,
            timeout=30,
        )
        warned = [no_entry[script] for script in sources if script in no_entry]
        assert (proc.returncode, proc.stderr.splitlines(), proc.stdout) == (0, warned, "sourced\n"), sources


# The setup script and the tool's Python read a dependency's .env the same way.


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks and bash")
@pytest.mark.parametrize(
    "venv_line, entry_line",
    [
        ("APP_VENV='{venv}'", "APP_ENTRY='src/run_v2.py'"),
        ('export APP_VENV="{venv}"', "export APP_ENTRY=src/run_v2.py"),
        ("APP_VENV = {venv}", "APP_ENTRY = 'src/run_v2.py'"),
    ],
    ids=["single-quotes", "export", "spaces-around-equals"],
)
def test_setup_script_reads_a_dependency_env_as_python_does(scaffolded_project, tmp_path, venv_line, entry_line):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    venv = deploy_version(apps, "1.1", "run_v2.py")
    (apps / "callee" / "1.1" / ".env").write_text(f"APP_NAME=callee\n{venv_line.format(venv=venv)}\n{entry_line}\n")
    for shell in ("bash", "dash"):
        if shutil.which(shell):
            assert subprocess.run([shell, "-n", "setup_callee_env.sh"], cwd=scaffolded_project).returncode == 0

    shell = sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_ENTRY", "CALLEE_VENV", "VIRTUAL_ENV")
    python = probe_deps(scaffolded_project)

    # This .env has no APP_ROOT: the real folder behind the `current` link.
    entry = str(apps / "callee" / "1.1" / "src" / "run_v2.py")
    assert shell["CALLEE_ENTRY"] == python["CALLEE_ENTRY"] == entry
    assert shell["CALLEE_VENV"] == python["CALLEE_VENV"] == str(venv)
    assert shell["VIRTUAL_ENV"] == UNSET  # not activated in the caller's shell


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks and bash")
def test_setup_script_takes_the_last_entry_line_without_its_quotes(scaffolded_project, tmp_path):
    # Pins what the setup script already did: python-dotenv lets the last line for a
    # key win, and "double quotes" are not part of the value.
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    deploy_version(apps, "1.1", "run_v2.py")
    (apps / "callee" / "1.1" / ".env").write_text(
        'APP_NAME=callee\nAPP_ENTRY="src/run_old.py"\nAPP_ENTRY="src/run_v2.py"\n'
    )

    shell = subprocess.run(
        ["bash", "-c", 'unset CALLEE_ENTRY; source ./setup_callee_env.sh; printf "%s" "$CALLEE_ENTRY"'],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    python = probe_deps(scaffolded_project)

    entry = str(apps / "callee" / "1.1" / "src" / "run_v2.py")
    assert shell.returncode == 0, shell.stderr
    assert shell.stdout == python["CALLEE_ENTRY"] == entry


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("dependency_env", [None, "APP_NAME=callee\n"], ids=["no-env", "env-without-venv"])
def test_setup_script_without_a_venv_sources_nothing(scaffolded_project, tmp_path, dependency_env):
    # Pins what the setup script already did: with no virtualenv found now or at
    # source time, no `source "/bin/activate"` runs, so `set -e` callers survive.
    tool = make_tool_dir(tmp_path, name="callee", with_venv=False)
    if dependency_env is not None:
        (tool / ".env").write_text(dependency_env)
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={tool}")

    proc = subprocess.run(
        ["bash", "-c", 'set -e; unset VIRTUAL_ENV; source ./setup_callee_env.sh; echo "done ${VIRTUAL_ENV-none}"'],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert (proc.returncode, proc.stderr, proc.stdout) == (0, no_entry_warning("callee", tool) + "\n", "done none\n")


# .env is rebuilt from paths.py on every run. A key added to it by hand is named
# when it goes, instead of vanishing without a word.


def test_keys_added_to_env_by_hand_are_named_when_removed(scaffolded_project, strip_ansi):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text() + "kept_setting = 'k'\n")
    run_configure(scaffolded_project, "--pepx-path=/opt/pepx")
    env_file = scaffolded_project / ".env"
    env_file.write_text(env_file.read_text() + "FOO=1\nexport BAR='2'\n")
    # pepx leaves paths.py: its keys go too, and ./configure wrote those itself.
    paths.write_text("kept_setting = 'k'\n")

    output = strip_ansi(run_configure(scaffolded_project).stdout).splitlines()

    assert [line for line in output if "from .env" in line] == [
        "⚠  Removed FOO, BAR from .env: ./configure rewrites .env from paths.py. Put them in paths.py to keep them."
    ]
    values = env_values(scaffolded_project)
    assert "FOO" not in values and "PEPX_PATH" not in values
    assert values["KEPT_SETTING"] == "k"
    # Nothing more to say on the next run.
    assert "from .env" not in strip_ansi(run_configure(scaffolded_project).stdout)


def test_a_key_in_an_env_from_cli_generate_is_named_when_removed(scaffolded_project, strip_ansi):
    # The .env `cli generate` writes has no line naming ./configure's keys.
    env_file = scaffolded_project / ".env"
    env_file.write_text(env_file.read_text() + "MY_TOKEN_FILE=/home/me/token\n")

    output = strip_ansi(run_configure(scaffolded_project).stdout).splitlines()

    assert (
        "⚠  Removed MY_TOKEN_FILE from .env: ./configure rewrites .env from paths.py. Put it in paths.py to keep it."
    ) in output
    assert "APP_NAME=demo" in env_lines(scaffolded_project)


# An exception while paths.py runs names its line, as a syntax error does.


@pytest.mark.parametrize(
    "line, message",
    [
        ("import not_a_module_xyz", "ModuleNotFoundError: No module named 'not_a_module_xyz'"),
        ("b_path = undefined_name", "NameError: name 'undefined_name' is not defined"),
        ("b_path = 1 / 0", "ZeroDivisionError: division by zero"),
    ],
)
def test_an_error_while_paths_py_runs_names_the_line(scaffolded_project, strip_ansi, line, message):
    (scaffolded_project / "paths.py").write_text(f"a_path = None\n{line}\nc_path = None\n")
    env_before = (scaffolded_project / ".env").read_text()

    result = run_configure(scaffolded_project, expect_success=False)
    output = strip_ansi(result.stdout).splitlines()

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert output[:2] == [f"✗ paths.py line 2: {message}", f"    {line}"]
    assert (scaffolded_project / ".env").read_text() == env_before


# PROBE_KEYS prints the values the tool's Python has once
# core.set_pythonpath has loaded .env, for the keys named on its command line.

PROBE_KEYS = """\
import json, os, sys
import core.set_pythonpath
print(json.dumps({k: os.environ.get(k) for k in sys.argv[1:]}))
"""


def probe_keys(project, *keys, **inherited):
    """The tool's Python's values for `keys`, started with the `inherited` values in its environment."""
    (project / "src" / "probe_keys.py").write_text(PROBE_KEYS)
    base = {k: v for k, v in os.environ.items() if k not in keys and k not in ("APP_ROOT", "APP_NAME")}
    proc = subprocess.run(
        [sys.executable, str(project / "src" / "probe_keys.py"), *keys],
        env={**base, **inherited},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# A dependency with two bundled virtualenvs settles on neither. Its <DEP>_VENV was
# neither unset by the setup script nor listed in APP_KEYS_FROM_DEPS, so a value
# from the calling tool, for its own dependency of that name, was kept in both.


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_a_dependency_with_two_venvs_never_keeps_the_callers_venv(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path)
    make_venv(tool_dir / "venv")
    run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")
    callers = {"TCELL_CLASS_I_VENV": str(tmp_path / "callers-tc1" / ".venv")}

    def from_bash():
        proc = subprocess.run(
            ["bash", "-c", 'source ./setup_tcell_class_i_env.sh; echo "${TCELL_CLASS_I_VENV-<unset>}"'],
            cwd=scaffolded_project,
            env={**os.environ, **callers},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    assert "TCELL_CLASS_I_VENV" in env_values(scaffolded_project)["APP_KEYS_FROM_DEPS"].split(",")
    assert from_bash() == UNSET
    assert probe_keys(scaffolded_project, "TCELL_CLASS_I_VENV", **callers) == {"TCELL_CLASS_I_VENV": None}

    # Control: once the dependency publishes one in its own .env, both take it.
    declare_app_venv(tool_dir, tool_dir / ".venv")
    assert from_bash() == str(tool_dir / ".venv")
    assert probe_keys(scaffolded_project, "TCELL_CLASS_I_VENV", **callers) == {
        "TCELL_CLASS_I_VENV": str(tool_dir / ".venv")
    }


# A dependency whose path is None stops ./configure with exit code 1. It used to
# write .env and the other setup scripts first ("* .env file updated"), and with
# flags, paths.py too: a failed run now changes nothing.


def test_a_dependency_without_a_path_stops_configure_before_it_writes_anything(
    scaffolded_project, tmp_path, strip_ansi
):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i", "pepx"])
    pepx = make_tool_dir(tmp_path, "pepx")
    old_tool = make_tool_dir(tmp_path, "oldtool")
    # A setup script for a dependency paths.py no longer has, which a run that goes ahead removes.
    run_configure(scaffolded_project, f"--tcell-class-i-path={old_tool}", f"--pepx-path={pepx}")
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text().replace(f"tcell_class_i_path='{old_tool}'", "tcell_class_i_path=None"))
    (scaffolded_project / "setup_gone_env.sh").write_text(
        (scaffolded_project / "setup_pepx_env.sh").read_text().replace("pepx", "gone")
    )
    (scaffolded_project / "setup_pepx_env.sh").unlink()
    (scaffolded_project / "paths.py.bak").unlink()
    before = {p.name: p.read_bytes() for p in scaffolded_project.iterdir() if p.is_file()}

    for flags in ((), (f"--pepx-path={tmp_path}",)):
        result = run_configure(scaffolded_project, *flags, expect_success=False)
        output = strip_ansi(result.stdout).splitlines()

        assert result.returncode == 1
        # The command to run repeats the flags given, since none of them was written.
        assert (
            f"✗ tcell_class_i_path is None in paths.py. Set it with './configure --tcell-class-i-path=<path>{''.join(' ' + flag for flag in flags)}', "
            "or run 'cli deps remove tcell_class_i' to drop this dependency."
        ) in output
        assert "* Detected 2 dependency tools: tcell_class_i, pepx" in output
        assert not any(line.startswith(("* .env file", "* Shell script", "* Removed", "* Set ")) for line in output)
        assert output[-1] == (
            "  Nothing was changed, paths.py included: run ./configure again with every flag you gave."
            if flags
            else "  Nothing was changed."
        )
        assert {p.name: p.read_bytes() for p in scaffolded_project.iterdir() if p.is_file()} == before

    # Control: with the path set, the same run writes the files.
    run_configure(scaffolded_project, f"--tcell-class-i-path={old_tool}")
    assert f"tcell_class_i_path='{old_tool}'" in paths.read_text()
    assert (scaffolded_project / "setup_pepx_env.sh").is_file()
    assert not (scaffolded_project / "setup_gone_env.sh").exists()


# The venv was read as callee/1.0/.venv, the APP_VENV of the .env behind `current`,
# and the entry joined to callee/current/. Once a redeploy moved `current`, 1.0's
# Python ran a script that 1.1 had renamed: "can't open file".


@pytest.mark.skipif(os.name != "posix" or shutil.which("bash") is None, reason="needs symlinks and bash")
def test_venv_and_entry_come_from_one_version_when_current_moves(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    venv = deploy_version(apps, "1.0", "run_callee.py")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    one_version = {"CALLEE_VENV": str(venv), "CALLEE_ENTRY": str(apps / "callee" / "1.0" / "src" / "run_callee.py")}

    stored = env_values(scaffolded_project)  # what ./configure saw: the fallback
    shell = sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_VENV", "CALLEE_ENTRY")
    python = probe_deps(scaffolded_project)
    deploy_version(apps, "1.1", "run_callee_v2.py")  # `current` moves on; 1.1 has no run_callee.py

    for seen in (stored, shell, python):
        assert {key: seen[key] for key in one_version} == one_version
        assert os.path.isfile(seen["CALLEE_ENTRY"])


# `source` is bash only. subprocess.run(..., shell=True) runs /bin/sh, which is dash
# on Debian and Ubuntu: "source: not found", nothing activated, and the setup
# script still returned 0.


@pytest.mark.skipif(os.name != "posix" or shutil.which("dash") is None, reason="needs symlinks and dash")
def test_setup_scripts_activate_when_sourced_from_dash(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    published = deploy_version(apps, "1.0", "run_callee.py")  # in callee's .env, read when sourced
    bundled = make_tool_dir(tmp_path, name="bundled")  # found by ./configure inside the tool
    other = make_tool_dir(tmp_path, name="other", with_venv=False)
    explicit = tmp_path / "explicit-env"  # set in paths.py; a real virtualenv and its activate script
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(explicit)], check=True, timeout=120)
    cli.add_deps_to_paths("paths.py", ["callee", "bundled", "other"])
    run_configure(
        scaffolded_project,
        f"--callee-path={apps / 'callee' / 'current'}",
        f"--bundled-path={bundled}",
        f"--other-path={other}",
        f"--other-venv={explicit}",
    )

    for name, venv in (("callee", published), ("bundled", bundled / ".venv"), ("other", explicit)):
        script = f"setup_{name}_env.sh"
        assert subprocess.run(["dash", "-n", script], cwd=scaffolded_project).returncode == 0
        var = f"{name.upper()}_VENV"
        proc = subprocess.run(
            ["dash", "-c", f'unset VIRTUAL_ENV {var}; . ./{script}; echo "$VIRTUAL_ENV"; echo "${var}"'],
            cwd=scaffolded_project,
            capture_output=True,
            text=True,
            timeout=30,
        )
        folder = {"bundled": bundled, "other": other}.get(name)  # no .env, so no entry script
        # bundled has a .venv inside it: a tool being installed (a redeploy).
        warned = {"bundled": installing_warning, "other": no_entry_warning}[name](name, folder) + "\n" if folder else ""
        assert (proc.returncode, proc.stderr) == (0, warned), name
        # Exported, not activated: VIRTUAL_ENV stays as the caller had it (run_<dep> activates it).
        assert proc.stdout.splitlines() == ["", str(venv)], name


# A file ./configure could not write ended the run in a traceback. paths.py had
# been rewritten first, and .env next, so the three files no longer agreed.


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs RLIMIT_FSIZE and SIGXFSZ")
def test_a_setup_script_that_cannot_be_written_changes_nothing(scaffolded_project, strip_ansi):
    # A full disk: a file size limit with room for paths.py and .env, not for the setup script.
    import resource
    import signal

    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    env_file = scaffolded_project / ".env"
    env_file.chmod(0o640)
    names = ("paths.py", ".env")
    before = {name: (scaffolded_project / name).read_text() for name in names}
    limit = max(len(text.encode()) for text in before.values()) + 200

    def limit_file_size():
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)  # fail the write, do not kill the process
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, resource.RLIM_INFINITY))

    result = subprocess.run(
        [sys.executable, "src/core/configure.py", "--tcell-class-i-path=/opt/tools/tc1"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        preexec_fn=limit_file_size,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},  # a .pyc cut short breaks later imports
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert strip_ansi(result.stdout).splitlines()[-1] == (
        "✗ Could not write 'setup_tcell_class_i_env.sh': File too large. Nothing else was changed."
    )
    assert {name: (scaffolded_project / name).read_text() for name in names} == before
    assert not (scaffolded_project / "setup_tcell_class_i_env.sh").exists()
    assert not (scaffolded_project / "paths.py.bak").exists()
    assert not [p.name for p in scaffolded_project.iterdir() if p.name.endswith(".tmp")]

    # Without the limit, the same run writes all three, and .env keeps its mode.
    output = strip_ansi(run_configure(scaffolded_project, "--tcell-class-i-path=/opt/tools/tc1").stdout)
    assert "* Set tcell_class_i_path = '/opt/tools/tc1' in paths.py" in output.splitlines()
    assert "TCELL_CLASS_I_PATH=/opt/tools/tc1" in env_lines(scaffolded_project)
    assert env_file.stat().st_mode & 0o777 == 0o640
    assert (scaffolded_project / "setup_tcell_class_i_env.sh").stat().st_mode & 0o777 == 0o755


# A pyproject entry naming a missing file warned "Publishing src/run_demo.py instead"
# while an APP_ENTRY left in .env, checked first, was what got published.


def test_a_missing_pyproject_entry_names_the_entry_that_is_published(scaffolded_project, strip_ansi):
    set_pyproject_entry(scaffolded_project, "src/run_gone.py")
    (scaffolded_project / "src" / "legacy_main.py").write_text("print('legacy')\n")
    env_file = scaffolded_project / ".env"
    env_file.write_text(env_file.read_text().replace("APP_ENTRY=src/run_demo.py", "APP_ENTRY=src/legacy_main.py"))

    output = strip_ansi(run_configure(scaffolded_project).stdout).splitlines()

    assert (
        "⚠  [tool.ngargparser] entry in pyproject.toml names 'src/run_gone.py', which does not exist. "
        "Publishing src/legacy_main.py instead."
    ) in output
    assert "APP_ENTRY=src/legacy_main.py" in env_lines(scaffolded_project)


# Without a .venv the launcher ran configure.py by its shebang, which needs the exec
# bit: a copy that dropped it stopped with exit code 126 ("Permission denied").


def test_the_launcher_runs_configure_py_without_its_exec_bit(scaffolded_project):
    (scaffolded_project / "src" / "core" / "configure.py").chmod(0o644)

    result = run_launcher(scaffolded_project, expect_success=False)

    assert result.returncode == 0, result.stderr
    assert "Minimal .env file" in result.stdout


# The values ./configure saw were the fallback for a key the dependency's .env left
# out, not only for a missing .env: a 1.1 that published no virtualenv ran its
# script under the 1.0 virtualenv that ./configure had seen.


@pytest.mark.skipif(os.name != "posix" or shutil.which("bash") is None, reason="needs symlinks and bash")
def test_a_key_the_dependency_env_leaves_out_is_none_not_an_older_copy(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    deploy_version(apps, "1.1", "run_callee_v2.py")
    env_file = apps / "callee" / "1.1" / ".env"
    env_file.write_text("".join(line for line in env_file.read_text().splitlines(True) if "APP_VENV=" not in line))
    expected = {"CALLEE_VENV": None, "CALLEE_ENTRY": str(apps / "callee" / "1.1" / "src" / "run_callee_v2.py")}

    shell = sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_VENV", "CALLEE_ENTRY", "VIRTUAL_ENV")
    python = probe_deps(scaffolded_project)

    assert shell == {**expected, "CALLEE_VENV": UNSET, "VIRTUAL_ENV": UNSET}
    assert {key: python[key] for key in expected} == expected


# A flag stopped with "SyntaxError: invalid non-printable character U+FEFF" on a
# paths.py that starts with a UTF-8 byte order mark, which a plain ./configure reads.


def test_a_flag_rewrites_a_paths_py_that_starts_with_a_byte_order_mark(scaffolded_project, strip_ansi):
    paths = scaffolded_project / "paths.py"
    paths.write_bytes(b"\xef\xbb\xbfpepx_path = None\npepx_venv = None\n")

    result = run_configure(scaffolded_project, "--pepx-path=/opt/tools/pepx", expect_success=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "* Set pepx_path = '/opt/tools/pepx' in paths.py" in strip_ansi(result.stdout).splitlines()
    # The mark is kept, on the file and on its backup.
    assert paths.read_bytes() == b"\xef\xbb\xbfpepx_path='/opt/tools/pepx'\npepx_venv = None\n"
    assert (scaffolded_project / "paths.py.bak").read_bytes().startswith(b"\xef\xbb\xbfpepx_path = None\n")
    assert "PEPX_PATH=/opt/tools/pepx" in env_lines(scaffolded_project)
    # Control: the rewritten file still reads, and a second flag rewrites it again.
    run_configure(scaffolded_project, "--pepx-venv=/opt/venvs/pepx")
    assert paths.read_bytes() == b"\xef\xbb\xbfpepx_path='/opt/tools/pepx'\npepx_venv='/opt/venvs/pepx'\n"


# <DEP>_PYTHON names the Python a dependency runs with, the same in the setup
# script and in the tool's Python, and follows the dependency's .env like <DEP>_VENV.

PROBE_PYTHON = """\
import json, os
import core.set_pythonpath
print(json.dumps({k: os.environ.get(k) for k in ("CALLEE_VENV", "CALLEE_PYTHON")}))
"""


@pytest.mark.skipif(os.name != "posix" or shutil.which("bash") is None, reason="needs symlinks and bash")
def test_the_dependency_python_follows_its_env_in_the_shell_and_in_python(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    (scaffolded_project / "src" / "probe_python.py").write_text(PROBE_PYTHON)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CALLEE_") and k not in ("APP_ROOT", "APP_NAME")}

    def seen():
        shell = sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_PYTHON")
        proc = subprocess.run(
            [sys.executable, str(scaffolded_project / "src" / "probe_python.py")],
            env={**env, "CALLEE_PYTHON": "/callers/callee/bin/python"},  # a calling tool's value is replaced
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return shell["CALLEE_PYTHON"], json.loads(proc.stdout.strip().splitlines()[-1])["CALLEE_PYTHON"]

    venv = deploy_version(apps, "1.1", "run_callee_v2.py")  # redeployed: a new virtualenv
    assert seen() == (str(venv / "bin" / "python"),) * 2
    env_file = apps / "callee" / "1.1" / ".env"
    env_file.write_text("".join(line for line in env_file.read_text().splitlines(True) if "APP_VENV=" not in line))
    shell, python = seen()  # publishes none now
    assert python == "python3"
    # The shell writes it out: the first python3 on PATH outside any virtualenv.
    assert os.path.isabs(shell) and os.path.basename(shell) == "python3", shell


# A dependency's start-up script could only be set by editing paths.py; every
# other setting had a flag.


def test_entry_flag_sets_the_dependency_entry_in_paths_py(scaffolded_project, tmp_path, strip_ansi):
    tool = make_tool_dir(tmp_path, name="callee", with_venv=False)
    script = tool / "bin" / "run.sh"
    cli.add_deps_to_paths("paths.py", ["callee"])

    result = run_configure(scaffolded_project, f"--callee-path={tool}", f"--callee-entry={script}")

    lines = (scaffolded_project / "paths.py").read_text().splitlines()
    at = lines.index(f"callee_path={str(tool)!r}")
    assert lines[at + 1] == f"callee_entry={str(script)!r}"  # paths.py had no callee_entry: added after the path
    assert f"* Set callee_entry = {str(script)!r} in paths.py" in strip_ansi(result.stdout).splitlines()
    assert sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_ENTRY") == {"CALLEE_ENTRY": str(script)}
    assert "--callee-entry CALLEE_ENTRY" in run_configure(scaffolded_project, "-h").stdout


# Sourcing the setup script before the dependency was deployed left <DEP>_ENTRY
# unset without a word, so "$CALLEE_PYTHON" "$CALLEE_ENTRY" failed with
# "/usr/bin/python3: can't find '__main__' module in '<the current folder>/'".


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks and bash")
def test_a_setup_script_says_when_its_dependency_publishes_no_entry_yet(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    current = apps / "callee" / "current"
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={current}")  # not deployed yet
    call = 'set -eu; unset CALLEE_ENTRY; . ./setup_callee_env.sh; echo "goes on: ${CALLEE_ENTRY-<unset>}"'

    for shell in [shell for shell in ("bash", "dash") if _WHICH(shell)]:
        proc = subprocess.run([shell, "-c", call], cwd=scaffolded_project, capture_output=True, text=True, timeout=30)
        assert (proc.returncode, proc.stderr.splitlines(), proc.stdout) == (
            0,
            [not_deployed_warning("callee", current)],
            "goes on: <unset>\n",
        ), shell

    # Control: once the dependency is deployed, it publishes one, and nothing is printed.
    deploy_version(apps, "1.0", "run_callee.py")
    proc = subprocess.run(["bash", "-c", call], cwd=scaffolded_project, capture_output=True, text=True, timeout=30)
    assert (proc.returncode, proc.stderr, proc.stdout) == (
        0,
        "",
        f"goes on: {apps / 'callee' / '1.0' / 'src' / 'run_callee.py'}\n",
    )


# python-dotenv drops a " # comment" after an unquoted value, and anything after
# the closing quote of a quoted one. The setup script kept both, so the shell
# and the tool's Python ran two different scripts.


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks and bash")
@pytest.mark.parametrize(
    "venv_line, entry_line",
    [
        ("APP_VENV={venv}   # the virtualenv", "APP_ENTRY=src/run_v2.py # the start-up script"),
        ("APP_VENV='{venv}' # quoted", 'APP_ENTRY="src/run_v2.py"\t# quoted, then a tab'),
    ],
    ids=["unquoted", "quoted"],
)
def test_setup_script_drops_an_inline_comment_as_python_does(scaffolded_project, tmp_path, venv_line, entry_line):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    venv = deploy_version(apps, "1.1", "run_v2.py")
    env_file = apps / "callee" / "1.1" / ".env"
    env_file.write_text(f"APP_NAME=callee\n{venv_line.format(venv=venv)}\n{entry_line}\n")

    shell = sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_ENTRY", "CALLEE_VENV", "VIRTUAL_ENV")
    python = probe_deps(scaffolded_project)

    entry = str(apps / "callee" / "1.1" / "src" / "run_v2.py")
    assert shell["CALLEE_ENTRY"] == python["CALLEE_ENTRY"] == entry
    assert shell["CALLEE_VENV"] == python["CALLEE_VENV"] == str(venv)
    assert shell["VIRTUAL_ENV"] == UNSET  # not activated in the caller's shell
    # Control: a '#' with no space before it is part of the value, for both.
    env_file.write_text(f"APP_NAME=callee\nAPP_VENV={venv}\nAPP_ENTRY=src/run#v2.py\n")
    shell = sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_ENTRY")
    assert shell["CALLEE_ENTRY"] == probe_deps(scaffolded_project)["CALLEE_ENTRY"] == entry.replace("_v2", "#v2")


# A flag rewrote a paths.py saved with Windows line endings (CRLF) with LF ones,
# so `git diff` showed every line as changed.


def test_a_flag_keeps_the_line_endings_of_a_crlf_paths_py(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    paths = scaffolded_project / "paths.py"
    lf = paths.read_bytes().replace(b"pepx_venv=None\n", b"")  # the flag adds it back, after pepx_path
    assert b"pepx_path=None\n" in lf
    paths.write_bytes(lf.replace(b"\n", b"\r\n"))

    run_configure(scaffolded_project, "--pepx-path=/opt/pepx", "--pepx-venv=/opt/pepx/.venv")

    expected = lf.replace(b"pepx_path=None\n", b"pepx_path='/opt/pepx'\npepx_venv='/opt/pepx/.venv'\n")
    assert paths.read_bytes() == expected.replace(b"\n", b"\r\n")
    # Control: an LF paths.py stays LF.
    paths.write_bytes(lf)
    run_configure(scaffolded_project, "--pepx-path=/opt/pepx", "--pepx-venv=/opt/pepx/.venv")
    assert paths.read_bytes() == expected


# ./configure imported paths.py, which writes __pycache__/paths.*.pyc and reads it
# back while the size and modification time (in whole seconds) of paths.py match.
# Two flags of the same length within a second: the third run read the first
# value from the .pyc and wrote it back to .env. Now paths.py is compiled from
# its text on every run.


def test_a_same_size_edit_within_a_second_is_seen(scaffolded_project):
    cli.add_deps_to_paths("paths.py", ["xdep"])
    paths = scaffolded_project / "paths.py"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONDONTWRITEBYTECODE", "XDEP_PATH")}
    stamp = int(paths.stat().st_mtime)

    def configure(*args):
        proc = subprocess.run(
            [sys.executable, "src/core/configure.py", *args],
            cwd=scaffolded_project,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        os.utime(paths, (stamp, stamp))  # every write lands in the same second

    configure("--xdep-path=/opt/a1")
    configure("--xdep-path=/opt/b1")
    configure()

    assert "xdep_path='/opt/b1'" in paths.read_text().splitlines()
    assert "XDEP_PATH=/opt/b1" in env_lines(scaffolded_project)
    assert not (scaffolded_project / "__pycache__").exists()


# ./configure loaded the .env it had written into os.environ before it ran
# paths.py, so a paths.py that reads os.environ got back what the last run wrote:
# an edit to its default never reached .env.


def test_a_paths_py_that_reads_the_environment_sees_its_own_edit(scaffolded_project):
    paths = scaffolded_project / "paths.py"
    paths.write_text('import os\nxdep_path = os.environ.get("XDEP_PATH", "/opt/default1")\n')
    env = {k: v for k, v in os.environ.items() if k != "XDEP_PATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # no .pyc, so only the environment can hold the old value

    def configure(**extra):
        proc = subprocess.run(
            [sys.executable, "src/core/configure.py"],
            cwd=scaffolded_project,
            env={**env, **extra},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return [line for line in env_lines(scaffolded_project) if line.startswith("XDEP_PATH=")]

    assert configure() == ["XDEP_PATH=/opt/default1"]
    paths.write_text(paths.read_text().replace("/opt/default1", "/opt/default2"))
    assert configure() == ["XDEP_PATH=/opt/default2"]
    # Control: a value the calling shell exports still reaches paths.py.
    assert configure(XDEP_PATH="/from/the/shell") == ["XDEP_PATH=/from/the/shell"]


# Sourced before the dependency was deployed, the setup script unset <DEP>_ENTRY
# but left <DEP>_PYTHON=python3, so the documented one-line call ran
# `python3 "" predict ...`: the __main__.py of the current folder. With no entry
# known, <DEP>_PYTHON is empty, in the setup script and in the tool's Python.

MAIN_PY = 'print("__main__.py of the current folder ran")\n'


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks and bash")
@pytest.mark.parametrize("deployed", ["not-deployed", "venv-but-no-entry"])
def test_with_no_entry_known_the_one_line_call_runs_no_python(scaffolded_project, tmp_path, deployed):
    tool = tmp_path / "callee"
    venv = None
    if deployed == "venv-but-no-entry":
        venv = make_venv(tool / ".venv")
        (tool / ".env").write_text(f"APP_ROOT={tool}\nAPP_NAME=callee\nAPP_VENV={venv}\n")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={tool}")
    (scaffolded_project / "__main__.py").write_text(MAIN_PY)
    call = 'echo "CALLEE_PYTHON=[$CALLEE_PYTHON]"; ${CALLEE_PYTHON:+"$CALLEE_PYTHON"} "$CALLEE_ENTRY" predict'

    for shell in [shell for shell in ("bash", "dash") if _WHICH(shell)]:
        proc = subprocess.run(
            [shell, "-c", f"unset CALLEE_ENTRY CALLEE_PYTHON; . ./setup_callee_env.sh; {call}"],
            cwd=scaffolded_project,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.stdout.splitlines() == ["CALLEE_PYTHON=[]"], shell
        assert proc.returncode == 127, shell  # "": command not found
        warning = not_deployed_warning if deployed == "not-deployed" else no_entry_warning
        assert proc.stderr.splitlines()[0] == warning("callee", tool), shell

    (scaffolded_project / "src" / "probe_python.py").write_text(PROBE_PYTHON)
    proc = subprocess.run(
        [sys.executable, str(scaffolded_project / "src" / "probe_python.py")],
        env={k: v for k, v in os.environ.items() if not k.startswith(("CALLEE_", "APP_"))},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {
        "CALLEE_VENV": str(venv) if venv else None,
        "CALLEE_PYTHON": "",
    }


# A .env that is not UTF-8 text (saved in Latin-1, say) stopped ./configure with a
# UnicodeDecodeError traceback: the tool's own, or a dependency's.


def test_a_dependency_env_that_is_not_utf8_is_named_and_not_used(scaffolded_project, tmp_path, strip_ansi):
    tool = make_tool_dir(tmp_path, name="callee")
    (tool / ".env").write_bytes(b"APP_NAME=caf\xe9\nAPP_ENTRY=src/run_callee.py\n")
    cli.add_deps_to_paths("paths.py", ["callee"])

    result = run_configure(scaffolded_project, f"--callee-path={tool}", expect_success=False)

    assert (result.returncode, result.stderr) == (0, "")
    lines = strip_ansi(result.stdout).splitlines()
    unreadable = (
        f"⚠  Could not read {tool / '.env'}: it is not UTF-8 text. Its values are not used: "
        f"save it as UTF-8, or run ./configure in {tool} to write it again."
    )
    assert [line for line in lines if "Could not read" in line] == [unreadable]
    assert f"* callee: using virtualenv found at {tool / '.venv'}" in lines  # the .env published nothing
    assert not any(line.startswith("CALLEE_ENTRY=") for line in env_lines(scaffolded_project))


# The setup script still read the APP_ENTRY of that .env, which ./configure and
# app_env.py do not use: CALLEE_ENTRY=<tool>/src/run_callee.py.


@pytest.mark.skipif(_WHICH("iconv") is None, reason="the setup script tells with iconv")
def test_the_setup_script_does_not_use_a_dependency_env_that_is_not_utf8(scaffolded_project, tmp_path):
    tool = make_tool_dir(tmp_path, name="callee")
    (tool / ".env").write_bytes(f"APP_ROOT={tool}\nAPP_NAME=caf\xe9\nAPP_ENTRY=src/run_callee.py\n".encode("latin-1"))
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={tool}")
    not_utf8 = (
        f"setup_callee_env.sh: {tool / '.env'} is not UTF-8 text, so its values are not used: "
        f"save it as UTF-8, or run ./configure in {tool} to write it again"
    )

    for shell in [shell for shell in ("sh", "bash", "dash") if _WHICH(shell)]:
        proc = subprocess.run(
            [shell, "-c", f'unset CALLEE_ENTRY; set -eu; . ./setup_callee_env.sh; echo "${{CALLEE_ENTRY-{UNSET}}}"'],
            cwd=scaffolded_project,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (proc.returncode, proc.stdout) == (0, f"{UNSET}\n"), shell
        assert proc.stderr.splitlines() == [not_utf8, no_entry_warning("callee", tool)], shell

    # Control: the same .env saved as UTF-8 publishes its entry script.
    (tool / ".env").write_text(f"APP_ROOT={tool}\nAPP_NAME=café\nAPP_ENTRY=src/run_callee.py\n", encoding="utf-8")
    assert sourced_fresh(scaffolded_project, "setup_callee_env.sh", "CALLEE_ENTRY") == {
        "CALLEE_ENTRY": str(tool / "src" / "run_callee.py")
    }


def test_its_own_env_that_is_not_utf8_is_written_again(scaffolded_project, strip_ansi):
    env_file = scaffolded_project / ".env"
    env_file.write_bytes(b"APP_NAME=d\xe9mo\n")

    result = run_configure(scaffolded_project, expect_success=False)

    assert (result.returncode, result.stderr) == (0, "")
    lines = strip_ansi(result.stdout).splitlines()
    assert [line for line in lines if "Could not read" in line] == [
        "⚠  Could not read .env: it is not UTF-8 text. ./configure writes it again from paths.py."
    ]
    assert "* Minimal .env file updated (no external dependencies declared)." in lines
    assert "APP_NAME=demo" in env_file.read_text(encoding="utf-8").splitlines()


# A dependency with <dep>_python set in paths.py, and no virtualenv, was warned
# about: "has no virtualenv. It will run under whatever Python is active". The
# Python paths.py names runs it.


def test_a_dependency_with_its_python_set_gets_no_venv_warning(scaffolded_project, tmp_path, strip_ansi):
    tool = make_tool_dir(tmp_path, name="callee", with_venv=False)
    cli.add_deps_to_paths("paths.py", ["callee"])
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text() + "callee_python = '/usr/bin/python3'\n")

    output = strip_ansi(run_configure(scaffolded_project, f"--callee-path={tool}").stdout)

    assert "callee has no virtualenv" not in output
    # Control: without it, the warning is back.
    paths.write_text(paths.read_text().replace("callee_python = '/usr/bin/python3'\n", ""))
    assert "callee has no virtualenv" in strip_ansi(run_configure(scaffolded_project).stdout)


# Two dependencies with no path: each run named one of them, with a command that
# set only that one, and so failed on the other: './configure --beta-path=/opt/beta'
# said to run './configure --alpha-path=<path>', which said to set beta_path.
# One message now names every unset path, with one command that sets them all.


def unset_paths_error(result, strip_ansi):
    """The one ✗ line of a ./configure run that stopped on unset paths, and the command it gives."""
    import re

    (error,) = [line for line in strip_ansi(result.stdout).splitlines() if line.startswith("✗")]
    return error, re.search(r"Set (?:it|them) with '(.+?)', or run", error).group(1)


def test_every_unset_path_is_named_in_one_command_that_sets_them_all(scaffolded_project, tmp_path, strip_ansi):
    import shlex

    cli.add_deps_to_paths("paths.py", ["alpha", "beta"])
    alpha, beta = make_tool_dir(tmp_path, "alpha"), make_tool_dir(tmp_path, "beta")
    paths = scaffolded_project / "paths.py"
    before = paths.read_text()

    result = run_configure(scaffolded_project, expect_success=False)
    assert result.returncode == 1
    assert unset_paths_error(result, strip_ansi)[0] == (
        "✗ alpha_path and beta_path are None in paths.py. "
        "Set them with './configure --alpha-path=<path> --beta-path=<path>', "
        "or run 'cli deps remove <name>' to drop the ones this tool does not need."
    )

    # Given one of them, and another flag: the command keeps both, and asks only for the path still unset.
    result = run_configure(scaffolded_project, f"--beta-path={beta}", "--alpha-module=m/1", expect_success=False)
    assert result.returncode == 1
    error, command = unset_paths_error(result, strip_ansi)
    assert error == (
        f"✗ alpha_path is None in paths.py. Set it with './configure --alpha-path=<path> --alpha-module=m/1 "
        f"--beta-path={beta}', or run 'cli deps remove alpha' to drop this dependency."
    )
    assert paths.read_text() == before

    # That command, with <path> filled in, runs as it is.
    argv = shlex.split(command.replace("<path>", str(alpha)))
    assert argv[0] == "./configure"
    run_configure(scaffolded_project, *argv[1:])
    values = env_values(scaffolded_project)
    assert (values["ALPHA_PATH"], values["ALPHA_MODULE"], values["BETA_PATH"]) == (str(alpha), "m/1", str(beta))


# Parallel ./configure runs with flags each read paths.py, changed it and moved
# their copy in: the change moved in first was lost. A run with flags now holds
# .ngargparser/paths.lock, the lock `cli deps` takes too, from its first read of
# paths.py to its last write.


def test_parallel_flag_runs_keep_every_change(scaffolded_project, tmp_path):
    names = [f"dep{i}" for i in range(8)]
    cli.add_deps_to_paths("paths.py", names)
    run_configure(scaffolded_project, *(f"--{name}-path={make_tool_dir(tmp_path, name)}" for name in names))

    for round_ in range(2):
        runs = [
            subprocess.Popen(
                [sys.executable, "src/core/configure.py", f"--{name}-module=mod/{round_}"],
                cwd=scaffolded_project,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for name in names
        ]
        for run in runs:
            out, err = run.communicate(timeout=120)
            assert run.returncode == 0, out + err
        text = (scaffolded_project / "paths.py").read_text().splitlines()
        assert [name for name in names if f"{name}_module='mod/{round_}'" not in text] == [], round_
        values = env_values(scaffolded_project)
        assert {values.get(f"{name.upper()}_MODULE") for name in names} == {f"mod/{round_}"}


def hold_paths_lock(project):
    """Take .ngargparser/paths.lock in `project` as `cli deps` does; returns the open file, which holds it."""
    import fcntl

    (project / ".ngargparser").mkdir(exist_ok=True)
    held = open(project / ".ngargparser" / "paths.lock", "a")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)
    return held


@pytest.mark.skipif(os.name != "posix", reason="needs fcntl")
def test_a_flag_run_waits_for_the_paths_lock(scaffolded_project, tmp_path):
    import time

    cli.add_deps_to_paths("paths.py", ["pepx"])
    pepx = make_tool_dir(tmp_path, "pepx")
    held = hold_paths_lock(scaffolded_project)
    try:
        run = subprocess.Popen(
            [sys.executable, "src/core/configure.py", f"--pepx-path={pepx}"],
            cwd=scaffolded_project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(1.5)
        assert run.poll() is None, run.communicate()  # still waiting
        assert "pepx_path=None" in (scaffolded_project / "paths.py").read_text()
    finally:
        held.close()
    out, err = run.communicate(timeout=60)

    assert run.returncode == 0, out + err
    assert f"pepx_path='{pepx}'" in (scaffolded_project / "paths.py").read_text()
    # A run without flags waits too (tests/test_paths_lock.py): it writes .env from paths.py.


@pytest.mark.skipif(os.name != "posix", reason="needs fcntl")
def test_a_paths_lock_held_too_long_stops_the_run_with_one_line(scaffolded_project, capsys, strip_ansi):
    import importlib.util

    spec = importlib.util.spec_from_file_location("configure_under_test", scaffolded_project / "src/core/configure.py")
    configure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(configure)
    configure.PATHS_LOCK_WAIT = 0.3
    held = hold_paths_lock(scaffolded_project)
    try:
        with pytest.raises(SystemExit) as stopped:
            configure.lock_paths()
    finally:
        held.close()

    assert stopped.value.code == 1
    assert strip_ansi(capsys.readouterr().out).splitlines() == [
        "✗ Another ./configure or cli deps is changing paths.py: .ngargparser/paths.lock was still locked "
        "after 0.3 s. Nothing was changed; try again when it is done."
    ]
    # Control: free again, it is taken at once.
    configure.lock_paths()
    assert configure.paths_lock_fd is not None
    os.close(configure.paths_lock_fd)


@needs_permission_errors
def test_a_run_in_a_read_only_tool_folder_goes_on_without_the_lock(scaffolded_project, tmp_path):
    # A read-only deployed copy: neither the tool folder nor .ngargparser/ can be
    # written, so there is nothing to lock. A run that has nothing to change works.
    cli.add_deps_to_paths("paths.py", ["pepx"])
    pepx = make_tool_dir(tmp_path, "pepx")
    run_configure(scaffolded_project, f"--pepx-path={pepx}")
    state = scaffolded_project / ".ngargparser"
    (state / "paths.lock").unlink()
    state.chmod(0o555)
    scaffolded_project.chmod(0o555)
    try:
        result = run_configure(scaffolded_project)
    finally:
        scaffolded_project.chmod(0o755)
        state.chmod(0o755)

    assert result.stdout.count("✗") == 0
    assert "* .env file unchanged" in result.stdout.splitlines()
    assert not (state / "paths.lock").exists()


# A deployed copy has no uv project: "Run `uv sync` first" was the wrong step
# there. It has .ngargparser-build, which `make build` writes into every build
# copy, and deploy/install.sh makes its .venv.


def test_missing_dotenv_in_a_deployed_copy_says_to_run_install_sh(scaffolded_project, tmp_path):
    broken = tmp_path / "no-dotenv"
    broken.mkdir()
    (broken / "dotenv.py").write_text("raise ImportError('python-dotenv is not installed')\n")
    (scaffolded_project / ".ngargparser-build").write_text("TOOL_NAME=demo\nTOOL_VERSION=1.0.0\n")
    assert (scaffolded_project / "deploy" / "install.sh").is_file()

    result = subprocess.run(
        [sys.executable, str(scaffolded_project / "src" / "core" / "configure.py")],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(broken)},
    )

    assert result.returncode == 1
    assert result.stderr == (
        "./configure needs python-dotenv. Run `bash deploy/install.sh`; it installs it into .venv, then runs ./configure.\n"
    )
    # Control: a source folder, without the build stamp, still says uv sync.
    (scaffolded_project / ".ngargparser-build").unlink()
    result = subprocess.run(
        [sys.executable, "src/core/configure.py"],
        cwd=scaffolded_project,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(broken)},
    )
    assert result.stderr == "./configure needs python-dotenv. Run `uv sync` first; it installs it into .venv.\n"


# A flag rewrote `pepx_path = None  # where pepx lives` as `pepx_path='/opt/pepx'`:
# the comment was gone.


def test_a_flag_keeps_the_comment_at_the_end_of_the_line_it_rewrites(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["pepx"])
    paths = scaffolded_project / "paths.py"
    text = paths.read_text()
    # A non-ASCII value: ast says where a statement ends in UTF-8 bytes, not characters.
    text = text.replace("pepx_path=None", "pepx_path = '/opt/café'  # where pepx lives")
    text = text.replace(
        "pepx_venv=None", "pepx_venv = (\n    None  # inside: replaced with the value\n)\t# after a tab"
    )
    paths.write_text(text)
    pepx = make_tool_dir(tmp_path, "pepx", with_venv=False)

    run_configure(scaffolded_project, f"--pepx-path={pepx}", "--pepx-venv=/opt/pepx/.venv", "--pepx-module=m/1")

    lines = paths.read_text().splitlines()
    assert f"pepx_path='{pepx}'  # where pepx lives" in lines
    assert "pepx_venv='/opt/pepx/.venv'\t# after a tab" in lines
    assert "pepx_module='m/1'" in lines  # control: a line without a comment gets none
    assert "inside: replaced" not in paths.read_text()


# The other "recreate it" hints said uv sync in a deployed copy too, which has no
# uv project: deploy/install.sh makes its .venv there, and runs ./configure.


def mark_deployed(folder):
    """Make `folder` look like a build or deployed copy: the build stamp, and deploy/install.sh."""
    (folder / ".ngargparser-build").write_text("TOOL_NAME=demo\nTOOL_VERSION=1.0.0\n")
    (folder / "deploy").mkdir(exist_ok=True)
    if not (folder / "deploy" / "install.sh").exists():
        (folder / "deploy" / "install.sh").write_text("#!/bin/bash\n")


def test_a_deployed_copy_with_a_venv_made_elsewhere_says_to_run_install_sh(scaffolded_project, strip_ansi):
    venv = scaffolded_project / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "activate").write_text('VIRTUAL_ENV="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n')
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\ncommand = /usr/bin/python3 -m venv /old/place/.venv\n")
    mark_deployed(scaffolded_project)

    output = strip_ansi(run_configure(scaffolded_project).stdout).splitlines()

    assert (
        f"⚠  {venv} was made for /old/place/.venv; recreate it: rm -r .venv && bash deploy/install.sh, "
        "which runs ./configure again. Until then it is not published as APP_VENV."
    ) in output
    # Control: without the build stamp, a source folder, uv sync.
    (scaffolded_project / ".ngargparser-build").unlink()
    output = strip_ansi(run_configure(scaffolded_project).stdout)
    assert "recreate it: rm -r .venv && uv sync, then run ./configure again." in output


def test_a_deployed_dependency_with_a_venv_made_elsewhere_says_to_run_install_sh(
    scaffolded_project, tmp_path, strip_ansi
):
    tool_dir = make_tool_dir(tmp_path)
    (tool_dir / ".venv" / "bin" / "activate").write_text('export VIRTUAL_ENV="/old/place/.venv"\n')
    mark_deployed(tool_dir)
    cli.add_deps_to_paths("paths.py", ["faketool"])

    output = strip_ansi(run_configure(scaffolded_project, f"--faketool-path={tool_dir}").stdout)

    assert (
        f"⚠  {tool_dir / '.venv'} was made for /old/place/.venv, so activating it does not work. Recreate it in "
        f'{tool_dir} (rm -r .venv && bash deploy/install.sh), or run "$FAKETOOL_VENV/bin/python" directly.'
    ) in output.splitlines()


# A dependency .env this user cannot read was read three ways: ./configure did not
# use it, app_env.py read it as setting nothing (so not even the values
# ./configure saw were used), and the setup script printed sed's own error. All
# three now count it as missing, with one line that says why.


@needs_permission_errors
def test_a_dependency_env_that_cannot_be_read_counts_as_missing_everywhere(scaffolded_project, tmp_path, strip_ansi):
    tool = make_tool_dir(tmp_path, name="callee")
    env_file = tool / ".env"
    env_file.write_text(f"APP_ROOT={tool}\nAPP_NAME=callee\nAPP_ENTRY=src/run_callee.py\n")
    env_file.chmod(0)
    cli.add_deps_to_paths("paths.py", ["callee"])
    try:
        result = run_configure(scaffolded_project, f"--callee-path={tool}")
        shells = {
            shell: subprocess.run(
                [
                    shell,
                    "-c",
                    f'unset CALLEE_ENTRY; set -eu; . ./setup_callee_env.sh; echo "${{CALLEE_ENTRY-{UNSET}}}"',
                ],
                cwd=scaffolded_project,
                capture_output=True,
                text=True,
                timeout=30,
            )
            for shell in ("sh", "bash", "dash")
            if _WHICH(shell)
        }
    finally:
        env_file.chmod(0o644)

    lines = strip_ansi(result.stdout).splitlines()
    assert [line for line in lines if "Could not read" in line] == [
        f"⚠  Could not read {env_file}: Permission denied. Its values are not used: ask its owner to make it readable."
    ]
    assert not any(line.startswith("CALLEE_ENTRY=") for line in env_lines(scaffolded_project))
    for shell, proc in shells.items():
        assert (proc.returncode, proc.stdout) == (0, f"{UNSET}\n"), shell
        assert proc.stderr.splitlines() == [
            f"setup_callee_env.sh: {env_file} cannot be read (permission denied), so its values are not used: "
            "ask its owner to make it readable",
            no_entry_warning("callee", tool),
        ], shell
