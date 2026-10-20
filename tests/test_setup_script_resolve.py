"""setup_<dep>_env.sh.

1. A shell that sourced setup_<dep>_env.sh kept running the version it saw
   then: run_<dep> used the values saved at source time, and once a redeploy
   removed that version said "try again ... or fix <dep>_python", which never
   passed. run_<dep> now finds the dependency again at each call.
2. A `cd` or `pwd` function in the caller's shell (dotfiles, autoenv) wrote
   into the folders the script found: `$(cd -P -- ... && pwd -P)` captured its
   output too.
3. A git checkout of a tool without its .env was told it is being installed
   ("try again"), which never passes: it is not configured yet. A tool whose
   entry follows its .env and whose ./configure is already gone in a redeploy
   (only .venv/ or src/ left) was told it publishes no entry script.
4. The script's header did not say which form to use.
"""

import os
import shutil
import subprocess
import sys

import pytest
from test_configure_env import make_tool_dir, run_configure
from test_redeploy_messages import clean_env, deploy

from ngargparser import cli

WHICH = shutil.which
SHELLS = [shell for shell in ("sh", "bash", "dash") if WHICH(shell)]

pytestmark = pytest.mark.skipif(os.name != "posix" or WHICH("bash") is None, reason="needs bash and symlinks")


def in_shell(project, shell, body):
    """Run `body` in `shell` in `project`: (exit code, stdout lines, stderr lines)."""
    proc = subprocess.run(
        [WHICH(shell), "-c", body], cwd=project, env=clean_env(), capture_output=True, text=True, timeout=60
    )
    return proc.returncode, proc.stdout.splitlines(), proc.stderr.splitlines()


def installing(folder):
    return f"callee is being installed at {folder} right now (a redeploy in progress?); try again"


def unconfigured(folder):
    return f"callee at {folder} is not configured yet: run ./configure there"


def configured(project, folder, *extra):
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(project, f"--callee-path={folder}", *extra)


# 1. run_<dep> after a redeploy --------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_run_dep_runs_the_version_deployed_after_the_script_was_sourced(scaffolded_project, tmp_path, shell):
    apps = tmp_path / "apps"
    old = deploy(apps, "1.0")
    new = deploy(apps, "2.0")
    (new / "src" / "run_callee.py").write_text("import sys\nprint('callee 2.0 ran:', *sys.argv[1:])\n")
    current = apps / "callee" / "current"
    current.unlink()
    current.symlink_to("1.0")
    configured(scaffolded_project, current)
    redeploy = f"rm '{current}' && ln -s 2.0 '{current}' && mv '{old}' '{tmp_path}/gone'"

    rc, out, err = in_shell(
        scaffolded_project,
        shell,
        f'set -u; . ./setup_callee_env.sh; run_callee predict; {redeploy}; run_callee predict; echo "$CALLEE_ENTRY"',
    )

    assert (rc, err) == (0, [])
    # The raw form keeps what was found when the script was sourced.
    assert out == ["callee ran: predict", "callee 2.0 ran: predict", str(old / "src" / "run_callee.py")]


# 2. A cd or pwd function in the caller's shell ---------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_a_cd_or_pwd_function_in_the_callers_shell_changes_nothing(scaffolded_project, tmp_path, shell):
    apps = tmp_path / "apps"
    old = deploy(apps, "1.0")
    configured(scaffolded_project, apps / "callee" / "current")
    # Redeployed since: only the new version's .env names it, not what ./configure saw.
    new = deploy(apps, "2.0")
    (new / "src" / "run_callee.py").write_text("import sys\nprint('callee 2.0 ran:', *sys.argv[1:])\n")
    old.rename(tmp_path / "gone")
    functions = 'cd() { echo "cd says hi"; command cd "$@"; }; pwd() { echo "pwd says hi"; command pwd "$@"; }; '
    run = 'set -u; . ./setup_callee_env.sh; echo "$CALLEE_ENTRY"; run_callee predict'
    expected: "tuple[int, list[str], list[str]]" = (
        0,
        [str(new / "src" / "run_callee.py"), "callee 2.0 ran: predict"],
        [],
    )

    assert in_shell(scaffolded_project, shell, run) == expected  # control: no function
    assert in_shell(scaffolded_project, shell, functions + run) == expected


# 3. Not configured yet, or being installed ----------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_a_git_checkout_of_a_tool_without_its_env_is_not_configured_yet(scaffolded_project, tmp_path, shell):
    apps = tmp_path / "apps"
    version = deploy(apps, "1.0")
    configured(scaffolded_project, apps / "callee" / "current")
    run = "set -u; . ./setup_callee_env.sh; run_callee predict"
    (version / ".env").rename(version / ".env.away")
    # Control: a tool folder without .env and no .git is being installed.
    assert in_shell(scaffolded_project, shell, run) == (
        127,
        [],
        [f"setup_callee_env.sh: {installing(version)}", installing(version)],
    )

    (version / ".git").mkdir()
    assert in_shell(scaffolded_project, shell, run) == (
        127,
        [],
        [f"setup_callee_env.sh: {unconfigured(version)}", unconfigured(version)],
    )
    # Sourced while it was configured, run while it is not.
    (version / ".env.away").rename(version / ".env")
    rc, out, err = in_shell(
        scaffolded_project,
        shell,
        f"set -u; . ./setup_callee_env.sh; mv '{version}/.env' '{version}/.env.away'; run_callee",
    )
    assert (rc, out, err) == (127, [], [unconfigured(version)])


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("left", [".venv", "src", ".ngargparser", "deploy/install.sh"])
def test_a_tool_whose_configure_is_gone_in_a_redeploy_is_being_installed(scaffolded_project, tmp_path, shell, left):
    apps = tmp_path / "apps"
    version = deploy(apps, "1.0")
    configured(scaffolded_project, apps / "callee" / "current")  # its entry follows its .env
    # A redeploy removes the old files in any order: only `left` is still there.
    for name in os.listdir(version):
        os.rename(version / name, tmp_path / f"away-{name}")
    if left == "deploy/install.sh":
        (version / "deploy").mkdir()
        (version / left).write_text("#!/bin/sh\n")
    else:
        (version / left).mkdir()
    run = "set -u; . ./setup_callee_env.sh; run_callee predict"

    assert in_shell(scaffolded_project, shell, run) == (
        127,
        [],
        [f"setup_callee_env.sh: {installing(version)}", installing(version)],
    )
    # Control: a git checkout with only these is no tool, and is not told to try again.
    (version / ".git").mkdir()
    rc, out, err = in_shell(scaffolded_project, shell, run)
    assert (rc, out) == (127, [])
    assert not any(installing(version) in line or unconfigured(version) in line for line in err), err


@pytest.mark.parametrize("shell", SHELLS)
def test_a_folder_with_src_is_not_being_installed_when_its_entry_is_set_in_paths_py(
    scaffolded_project, tmp_path, shell
):
    # The entry does not follow the folder's .env: any folder with src/ or .venv/
    # may be no tool, and never has a .env.
    tool = make_tool_dir(tmp_path, name="callee")
    (tool / "src").mkdir()
    (tool / "src" / "run_callee.py").write_text("import sys\nprint('callee ran:', *sys.argv[1:])\n")
    python = tool / ".venv" / "bin" / "python"
    if python.is_symlink():  # never write through a symlink into a real interpreter
        python.unlink()
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    configured(scaffolded_project, tool, f"--callee-entry={tool / 'src' / 'run_callee.py'}")

    assert in_shell(scaffolded_project, shell, "set -u; . ./setup_callee_env.sh; run_callee predict") == (
        0,
        ["callee ran: predict"],
        [],
    )


# 4. The header says which form to use ------------------------------------------------


def test_the_header_recommends_run_dep(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    deploy(apps, "1.0")
    configured(scaffolded_project, apps / "callee" / "current")
    header = (scaffolded_project / "setup_callee_env.sh").read_text().split("\n_ng_callee_resolve() {")[0]

    assert "run callee with run_callee <arguments>" in header
    assert "runs it inside\n# callee's own virtualenv" in header
    assert "in the caller's VIRTUAL_ENV and PATH" in header
