import subprocess
import sys


def run_configure(project_dir):
    result = subprocess.run(
        [sys.executable, "src/core/configure.py"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_first_run_creates_minimal_env(scaffolded_project):
    (scaffolded_project / ".env").unlink()
    output = run_configure(scaffolded_project)
    assert "created" in output
    env = (scaffolded_project / ".env").read_text()
    assert "APP_ROOT=" in env
    assert "APP_NAME=demo" in env


def test_second_run_with_no_changes_is_a_noop(scaffolded_project):
    run_configure(scaffolded_project)
    before = (scaffolded_project / ".env").read_text()

    output = run_configure(scaffolded_project)

    assert "unchanged" in output
    after = (scaffolded_project / ".env").read_text()
    assert after == before


def test_drifted_env_is_still_corrected(scaffolded_project):
    run_configure(scaffolded_project)
    env_path = scaffolded_project / ".env"
    with open(env_path, "a") as f:
        f.write("SOME_STALE_GARBAGE=1\n")
    assert "SOME_STALE_GARBAGE" in env_path.read_text()

    output = run_configure(scaffolded_project)

    assert "updated" in output
    after = env_path.read_text()
    assert "SOME_STALE_GARBAGE" not in after
    assert "APP_ROOT=" in after
    assert "APP_NAME=demo" in after
