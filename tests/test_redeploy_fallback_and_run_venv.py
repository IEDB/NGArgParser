"""A redeploy never runs an older version folder, and run_<dep> runs in its own virtualenv.

1. While a dependency's .env is missing (the deploy orchestrator extracts the
   new version, and its install.sh writes .env last), dependency_command and
   setup_<dep>_env.sh fell back to the values ./configure saw. Saved through a
   `current` link, those name the version folder it pointed to then: with
   `current -> 3.0` and the caller configured at 2.0, a same-version redeploy
   of 3.0 ran 2.0's script, which was still there, without a word.
2. run_<dep> ran the dependency with the caller's VIRTUAL_ENV and PATH: after
   `. setup_depa_env.sh; . setup_gdep_env.sh`, run_depa ran inside gdep's
   virtualenv, so a `python` depa starts was gdep's.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest
from test_configure_env import make_venv, run_configure
from test_redeploy_messages import clean_env, deploy, from_shell, not_configured, not_deployed

from ngargparser import cli

# The in_tmp_dir fixture hides every program from shutil.which.
WHICH = shutil.which
SHELLS = [shell for shell in ("sh", "bash", "dash") if WHICH(shell)]

pytestmark = pytest.mark.skipif(os.name != "posix" or WHICH("bash") is None, reason="needs bash and symlinks")

# Runs dependency_command for callee once, then once per version given on the command line:
# before each of those, `current` moves to that version, and its .env moves away.
PROBE = """\
import json, os, sys
import core.set_pythonpath
from core.app_env import dependency_command
apps, steps = sys.argv[1], sys.argv[2:]
seen = []
for step in [None, *steps]:
    if step:
        current = os.path.join(apps, "callee", "current")
        os.unlink(current)
        os.symlink(step, current)
        os.rename(os.path.join(apps, "callee", step, ".env"), os.path.join(apps, "callee", step, ".env.away"))
    try:
        argv, _env = dependency_command("callee", "predict")
    except RuntimeError as e:
        seen.append({"error": str(e)})
    else:
        seen.append({"argv": argv[1:]})
print(json.dumps(seen))
"""


def deploy_callee(apps, version):
    """callee <version>, `current` pointing at it, whose entry prints its version."""
    folder = deploy(apps, version)
    (folder / "src" / "run_callee.py").write_text(f"import sys\nprint('callee {version} ran:', *sys.argv[1:])\n")
    return folder


def from_python(project, apps, *steps):
    (project / "src" / "probe_fallback.py").write_text(PROBE)
    proc = subprocess.run(
        [sys.executable, str(project / "src" / "probe_fallback.py"), str(apps), *steps],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def setup_lines(message):
    """What sourcing the setup script and running run_callee print on stderr for `message`."""
    return [f"setup_callee_env.sh: {message}", message]


@pytest.mark.parametrize("shell", SHELLS)
def test_a_same_version_redeploy_never_runs_the_version_configured_before(scaffolded_project, tmp_path, shell):
    apps = tmp_path / "apps"
    current = apps / "callee" / "current"
    old = deploy_callee(apps, "2.0")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={current}")  # its values name 2.0
    assert str(old / "src" / "run_callee.py") in (scaffolded_project / ".env").read_text()
    new = deploy_callee(apps, "3.0")
    # Control: deployed, 3.0 runs from both.
    assert from_python(scaffolded_project, apps) == [{"argv": [str(new / "src" / "run_callee.py"), "predict"]}]
    assert from_shell(scaffolded_project, shell) == (0, "callee 3.0 ran: predict\n", [])

    # 3.0 is redeployed: extracted again, and its install.sh has not written .env yet. 2.0 is still there.
    (new / ".env").rename(new / ".env.away")
    assert from_python(scaffolded_project, apps) == [{"error": not_configured(current)}]
    assert from_shell(scaffolded_project, shell) == (127, "", setup_lines(not_configured(current)))
    # The version folder itself is gone for a moment: `current` points at nothing.
    (new / ".env.away").rename(new / ".env")
    new.rename(apps / "callee" / "3.0.away")
    assert from_python(scaffolded_project, apps) == [{"error": not_deployed(current)}]
    assert from_shell(scaffolded_project, shell) == (127, "", setup_lines(not_deployed(current)))

    # Values that lie in the folder `current` points at do not stand in for its .env either:
    # a tool folder without one is being installed.
    (apps / "callee" / "3.0.away").rename(new)
    run_configure(scaffolded_project)  # its values name 3.0 now
    (new / ".env").rename(new / ".env.away")
    assert from_python(scaffolded_project, apps) == [{"error": not_configured(current)}]
    assert from_shell(scaffolded_project, shell) == (127, "", setup_lines(not_configured(current)))


def test_dependency_command_never_runs_the_version_loaded_at_start_up_after_a_redeploy(scaffolded_project, tmp_path):
    apps = tmp_path / "apps"
    first = deploy_callee(apps, "3.0")
    cli.add_deps_to_paths("paths.py", ["callee"])
    run_configure(scaffolded_project, f"--callee-path={apps / 'callee' / 'current'}")
    deploy_callee(apps, "4.0")  # current points at the last one deployed
    (apps / "callee" / "current").unlink()
    (apps / "callee" / "current").symlink_to("3.0")

    # Started with 3.0 deployed; 4.0 is then extracted, before its install.sh writes its .env.
    seen = from_python(scaffolded_project, apps, "4.0")

    assert seen == [
        {"argv": [str(first / "src" / "run_callee.py"), "predict"]},
        {"error": not_configured(apps / "callee" / "current")},
    ]
    # A same-version redeploy may remove ./configure first: a version folder that
    # still has src/ and .venv/ is being installed too, so the values loaded at
    # start-up do not stand in. (A dependency whose entry paths.py sets is the
    # control: test_clone_install_args_and_venv.py.)
    (apps / "callee" / "4.0" / ".env.away").rename(apps / "callee" / "4.0" / ".env")
    (apps / "callee" / "4.0" / "configure").unlink()
    (apps / "callee" / "current").unlink()
    (apps / "callee" / "current").symlink_to("4.0")
    run_configure(scaffolded_project)
    seen = from_python(scaffolded_project, apps, "4.0")
    assert seen == [
        {"argv": [str(apps / "callee" / "4.0" / "src" / "run_callee.py"), "predict"]},
        {"error": not_configured(apps / "callee" / "current")},
    ]


# --- 2. run_<dep> and the virtualenv of the setup script sourced last ----------------------------

REPORT = """\
import json, os, shutil, sys
print(json.dumps({
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
    "python_on_path": shutil.which("python"),
    "pythonhome": os.environ.get("PYTHONHOME"),
    "ran_with": os.environ.get("RAN_WITH"),
}))
"""


def make_dep(tmp_path, name):
    """Dependency `name`, deployed and configured, with a virtualenv whose python says it ran."""
    folder = tmp_path / name
    venv = make_venv(folder / ".venv")
    python = venv / "bin" / "python"
    if python.is_symlink():  # never write through a symlink into a real interpreter
        python.unlink()
    python.write_text(f'#!/bin/sh\nRAN_WITH={name} exec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    (folder / "src").mkdir()
    (folder / "src" / f"run_{name}.py").write_text(REPORT)
    (folder / "configure").write_text("#!/bin/sh\n")
    (folder / ".env").write_text(f"APP_ROOT={folder}\nAPP_NAME={name}\nAPP_VENV={venv}\nAPP_ENTRY=src/run_{name}.py\n")
    return folder


@pytest.mark.parametrize("shell", SHELLS)
def test_run_dep_runs_in_its_own_virtualenv_whichever_setup_script_was_sourced_last(
    scaffolded_project, tmp_path, shell
):
    depa, gdep = make_dep(tmp_path, "depa"), make_dep(tmp_path, "gdep")
    cli.add_deps_to_paths("paths.py", ["depa", "gdep"])
    run_configure(scaffolded_project, f"--depa-path={depa}", f"--gdep-path={gdep}")
    home = sys.base_prefix  # a PYTHONHOME that still lets Python start

    proc = subprocess.run(
        [
            WHICH(shell),
            "-c",
            # A PYTHONHOME in the caller's shell: run_<dep> unsets it for the dependency only.
            '. ./setup_depa_env.sh; . ./setup_gdep_env.sh; set -u; export PYTHONHOME="$TEST_HOME"; '
            'run_depa; run_gdep; echo "${VIRTUAL_ENV-<unset>}|$PYTHONHOME"',
        ],
        cwd=scaffolded_project,
        env=clean_env(TEST_HOME=home),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    from_depa, from_gdep, after = proc.stdout.strip().splitlines()[-3:]
    for name, folder, seen in (("depa", depa, from_depa), ("gdep", gdep, from_gdep)):
        assert json.loads(seen) == {
            "virtual_env": str(folder / ".venv"),
            "python_on_path": str(folder / ".venv" / "bin" / "python"),
            "pythonhome": None,
            "ran_with": name,
        }
    # Only the dependency's environment: sourcing activates no virtualenv in the caller's shell.
    assert after == f"<unset>|{home}"
