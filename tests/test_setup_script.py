"""setup_<dep>_env.sh: one version behind `current`, run_<dep> errors, PATH, and its temporaries.

The script read <dep>_path/.env through the link three times (APP_ROOT,
APP_VENV, APP_ENTRY): a redeploy that moved `current` in between paired one
version's virtualenv with another's script. run_<dep> on a shell entry without
its exec bit said only "Permission denied", where dependency_command says how
to fix it. The tests at the end hold behaviour that no test checked before: a
PATH that is empty or unset gets no empty entry, and no `_ng_` variable is left
in the caller's shell.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_configure_env import deploy_version, make_tool_dir, make_venv, run_configure
from test_dependency_command import (
    SHELL_PEER,
    caller_and_peer,  # noqa: F401 (a fixture)
    configure,
    outside_venvs,
    probe_entry,
    publish_entry,
)

from ngargparser import cli

# The in_tmp_dir fixture hides every program from shutil.which.
WHICH = shutil.which
SHELLS = [shell for shell in ("sh", "bash", "dash") if WHICH(shell)]

pytestmark = pytest.mark.skipif(os.name != "posix" or WHICH("bash") is None, reason="needs bash and symlinks")


def run_in(shell, project, script, env=None):
    """Run shell code `script` with `shell` in `project`: (exit code, stdout, stderr)."""
    proc = subprocess.run(
        [WHICH(shell), "-c", script], cwd=project, env=env, capture_output=True, text=True, timeout=60
    )
    return proc.returncode, proc.stdout, proc.stderr


# One version behind `current` --------------------------------------------------

# A sed that moves `current` to the other version each time it runs, before it
# reads: the setup script reads each value of the dependency's .env with sed.
FLIPPING_SED = """\
#!/bin/sh
case $({readlink} {link}) in
    1.0) {ln} -sfn 1.1 {link} ;;
    *) {ln} -sfn 1.0 {link} ;;
esac
exec {sed} "$@"
"""


@pytest.mark.parametrize("shell", SHELLS)
def test_every_value_comes_from_one_version_while_current_moves(scaffolded_project, tmp_path, shell):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")
    deploy_version(apps, "1.1", "run_callee.py")  # `current` names 1.1
    current = apps / "callee" / "current"
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={current}")
    shim = tmp_path / "flipping-bin"
    shim.mkdir()
    sed = shim / "sed"
    sed.write_text(FLIPPING_SED.format(readlink=WHICH("readlink"), ln=WHICH("ln"), sed=WHICH("sed"), link=current))
    sed.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}

    rc, out, err = run_in(
        shell, scaffolded_project, 'set -eu; . ./setup_callee_env.sh; echo "$CALLEE_VENV|$CALLEE_ENTRY"', env
    )

    assert rc == 0, err
    venv, entry = out.strip().split("|")
    version = Path(venv).parent
    assert version.parent == apps / "callee", venv
    assert (venv, entry) == (str(version / ".venv"), str(version / "src" / "run_callee.py"))


# run_<dep> names a missing or non-executable entry as dependency_command does --


def run_peer(demo, shell):
    return run_in(shell, demo, "set -u; . ./setup_peer_env.sh; run_peer predict", outside_venvs())


@pytest.mark.parametrize("shell", SHELLS)
def test_run_dep_says_what_dependency_command_says_about_an_entry_it_cannot_run(caller_and_peer, shell):  # noqa: F811
    demo, peer, _venv, _lib = caller_and_peer

    # Not executable: 126, as the shell gives.
    script = publish_entry(peer, "bin/peer.sh", SHELL_PEER, mode=0o644)
    configure(demo)  # what demo saw, the fallback while peer is not there
    error = probe_entry(demo)["error"]
    assert error == (
        f"peer's entry {script} is not a Python script and not executable: chmod +x it, "
        "or point peer_entry in paths.py at its start-up script"
    )
    assert run_peer(demo, shell) == (126, "", error + "\n")
    # Not there (a redeploy in progress): 127, as for a command that is not found.
    script.unlink()
    error = probe_entry(demo)["error"]
    assert error == (
        f"peer cannot run right now: {script} does not exist (a redeploy in progress?); try again. "
        "If peer moved, run ./configure --peer-path=<new folder>"
    )
    assert run_peer(demo, shell) == (127, "", error + "\n")
    # The whole folder is not there.
    peer.rename(f"{peer}.away")
    error = probe_entry(demo)["error"]
    assert error == (
        f"peer is not deployed at {peer} right now (a redeploy in progress?); try again. "
        "If peer moved, run ./configure --peer-path=<new folder>"
    )
    assert run_peer(demo, shell) == (127, "", error + "\n")
    # Control: back in place and executable, it runs.
    Path(f"{peer}.away").rename(peer)
    script.write_text(SHELL_PEER)
    script.chmod(0o755)
    assert run_peer(demo, shell) == (0, "shell peer ran with: predict\n", "")


# A PATH that is empty or unset gets no empty entry -----------------------------


def project_with_a_venv(project, tmp_path, branch):
    """Declare callee with a virtualenv the setup script puts on PATH; its expected PATH entry.

    "published": callee's own .env names it (read when sourced; that .env is
    gone here, so the value ./configure saw is used, and no sed runs).
    "fixed": --callee-venv sets it in paths.py.
    """
    cli.add_deps_to_paths("paths.py", ["callee"])
    if branch == "published":
        apps = tmp_path / "apps"
        venv = deploy_version(apps, "1.0", "run_callee.py")
        run_configure(project, f"--callee-path={apps / 'callee' / 'current'}")
        (apps / "callee" / "1.0" / ".env").rename(apps / "callee" / "1.0" / ".env.away")
        assert "_ng_venv=" in (project / "setup_callee_env.sh").read_text()
    else:
        venv = make_venv(tmp_path / "fixed-venv")
        run_configure(
            project, f"--callee-path={make_tool_dir(tmp_path, 'callee', with_venv=False)}", f"--callee-venv={venv}"
        )
        assert "_ng_venv=" not in (project / "setup_callee_env.sh").read_text()
    return str(venv / "bin")


@pytest.mark.parametrize("branch", ["published", "fixed"])
@pytest.mark.parametrize("shell", SHELLS)
def test_an_empty_or_unset_path_gets_no_empty_entry(scaffolded_project, tmp_path, shell, branch):
    project_with_a_venv(scaffolded_project, tmp_path, branch)

    # An empty entry in PATH is the current folder; an unset PATH stops a `set -u` caller.
    # Sourcing leaves PATH as it was, empty and unset too: run_callee puts the
    # virtualenv's bin/ first, for the tool it runs.
    for start, expected in (("PATH=''", "[]"), ("unset PATH", "[<unset>]"), ("PATH=/a:/b", "[/a:/b]")):
        rc, out, err = run_in(
            shell, scaffolded_project, f'set -u; {start}; . ./setup_callee_env.sh; echo "[${{PATH-<unset>}}]"'
        )
        assert (rc, out) == (0, f"{expected}\n"), (start, err)


# No `_ng_` variable is left in the caller's shell ---------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_sourcing_leaves_no_temporary_variable(scaffolded_project, tmp_path, shell):
    apps = tmp_path / "apps"
    deploy_version(apps, "1.0", "run_callee.py")  # read when sourced: every temporary is set
    cli.add_deps_to_paths("paths.py", ["callee", "fixed"])
    run_configure(
        scaffolded_project,
        f"--callee-path={apps / 'callee' / 'current'}",
        f"--fixed-path={make_tool_dir(tmp_path, 'fixed')}",
        f"--fixed-entry={tmp_path / 'fixed' / 'run.py'}",
    )

    for script in ("setup_callee_env.sh", "setup_fixed_env.sh"):
        rc, out, err = run_in(shell, scaffolded_project, f"set -eu; . ./{script}; set")
        assert rc == 0, err
        # The function run_<dep> calls to find the dependency again stays, as run_<dep> does.
        left = [line for line in out.splitlines() if line.startswith("_ng_") and not line.endswith("_resolve () ")]
        assert left == [], script
        # Control: what the script exports is there.
        assert any(line.startswith(("CALLEE_ENTRY=", "FIXED_ENTRY=")) for line in out.splitlines()), script
