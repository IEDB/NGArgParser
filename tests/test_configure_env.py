import subprocess
import sys

import pytest

from ngargparser import cli


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


def run_launcher(project_dir, *args, expect_success=True):
    """Invoke the real `./configure` launcher (not the inner script
    directly) — this is the actual entry point users run."""
    result = subprocess.run(
        ["./configure", *args],
        cwd=project_dir,
        capture_output=True,
        text=True,
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
    (path / "bin" / "activate").write_text("# fake activate\n")
    if with_marker:
        (path / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.9.25\n")
    return path


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
    assert f"source {tool_dir}/.venv/bin/activate" in script


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
    assert f"source {explicit}/bin/activate" in script
    assert f"{tool_dir}/.venv/bin/activate" not in script


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


def test_unusable_venv_dir_is_not_adopted(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, venv_usable=False)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert result.returncode == 0
    assert "using virtualenv found at" not in result.stdout
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert "source " not in script


@pytest.mark.parametrize("venv_name", ["venv", "env", ".virtualenv"])
def test_conventional_venv_names_are_adopted(scaffolded_project, tmp_path, venv_name):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path, venv_name=venv_name)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert result.returncode == 0
    assert f"using virtualenv found at {tool_dir}/{venv_name}" in result.stdout
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert f"source {tool_dir}/{venv_name}/bin/activate" in script


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
    assert "source " not in script


def test_several_candidate_venvs_are_never_guessed(scaffolded_project, tmp_path, strip_ansi):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path)
    make_venv(tool_dir / "venv")

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")
    output = strip_ansi(result.stdout)

    assert result.returncode == 0
    assert "using virtualenv found at" not in output
    assert "has more than one virtualenv" in output
    assert ".venv, venv" in output
    assert "./configure --tcell-class-i-venv=<path>" in output
    # The generic warning would read as a contradiction right after this one.
    assert "has no virtualenv" not in output
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert "source " not in script


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
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert f"source {elsewhere}/bin/activate" in script


def test_declared_venv_beats_a_bundled_one(scaffolded_project, tmp_path):
    cli.add_deps_to_paths("paths.py", ["tcell-class-i"])
    tool_dir = make_tool_dir(tmp_path)
    elsewhere = make_venv(tmp_path / "pyenv-style-env")
    declare_app_venv(tool_dir, elsewhere)

    result = run_configure(scaffolded_project, f"--tcell-class-i-path={tool_dir}")

    assert result.returncode == 0
    assert "using virtualenv found at" not in result.stdout
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert f"source {elsewhere}/bin/activate" in script


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
    assert f"source {explicit}/bin/activate" in script
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
    script = (scaffolded_project / "setup_tcell_class_i_env.sh").read_text()
    assert f"source {stale}/bin/activate" in script


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


def test_project_with_two_venvs_publishes_nothing(scaffolded_project):
    make_venv(scaffolded_project / ".venv")
    make_venv(scaffolded_project / "venv")

    result = run_configure(scaffolded_project)

    assert result.returncode == 0
    assert "APP_VENV" not in (scaffolded_project / ".env").read_text()


def test_project_with_no_dependencies_gains_no_venv_warning(scaffolded_project, strip_ansi):
    result = run_configure(scaffolded_project)

    assert result.returncode == 0
    assert "has no virtualenv" not in strip_ansi(result.stdout)
