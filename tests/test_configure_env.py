import subprocess
import sys

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
