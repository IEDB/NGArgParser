"""Two real tools, deployed the way nxg-tools-deployments deploys them.

callee and caller are generated, built with the real build, extracted to
apps/<name>/<version>/ and installed with deploy/install.sh, with the stub uv
from test_install_sh.py. caller declares callee in paths.py and is wired to
apps/callee/current. Its predict branch sources setup_callee_env.sh and runs
"$CALLEE_VENV/bin/python" "$CALLEE_ENTRY", as an IEDB tool calls another one.
Then callee is redeployed as a new version whose entry script has another name,
next to a second src/run_*.py, and caller runs again without a new ./configure.
The other order is tested too: caller deployed and configured first, callee after.
"""

import os
import shutil
import subprocess
import sys
import tarfile
from argparse import Namespace

import pytest
from test_install_sh import run_install

from ngargparser import cli

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs symlinks, bash, and git for the build",
)

CALLEE_PREDICT = """\
        print("callee {version} ran " + os.path.abspath(__file__))
        sys.exit(0)
"""

CALLER_PREDICT = """\
        # Call the dependency through its setup script, as an IEDB tool does.
        import subprocess
        call = 'source ./setup_callee_env.sh && exec "$CALLEE_VENV/bin/python" "$CALLEE_ENTRY" predict -j "$1"'
        sys.exit(subprocess.call(["bash", "-c", call, "bash", str(args.input_json)], cwd=os.environ["APP_ROOT"]))
"""

# The same call from the caller's Python, with the values src/core/app_env.py
# loaded at start-up: CALLEE_VENV and CALLEE_ENTRY come from APP_KEYS_FROM_DEPS.
CALLER_PREDICT_FROM_PYTHON = """\
        # Call the dependency with the values this tool's .env names, as read at start-up.
        import subprocess
        python = os.path.join(os.environ["CALLEE_VENV"], "bin", "python")
        sys.exit(subprocess.call([python, os.environ["CALLEE_ENTRY"], "predict", "-j", str(args.input_json)]))
"""

# Run from a shell that has none of the values a test run may carry.
OUTER_ENV = {
    k: v for k, v in os.environ.items() if not k.startswith(("APP_", "CALLEE_", "CALLER_")) and k != "VIRTUAL_ENV"
}


def generate(parent, name, monkeypatch):
    parent.mkdir(exist_ok=True)
    monkeypatch.chdir(parent)
    assert not cli.startapp_command(Namespace(project_name=name))
    return parent / name


def set_predict(script, code):
    """Replace the generated predict branch's "not implemented" line of `script` with `code`."""
    lines = script.read_text().splitlines(keepends=True)
    at = [i for i, line in enumerate(lines) if "predict is not implemented yet" in line]
    assert len(at) == 1, f"{script.name}: the generated predict branch changed"
    lines[at[0] : at[0] + 1] = [code]
    script.write_text("".join(lines))


def set_entry(project, entry):
    """Set [tool.ngargparser] entry in pyproject.toml, adding it when the tool has none."""
    pyproject = project / "pyproject.toml"
    lines = [line for line in pyproject.read_text().splitlines(keepends=True) if not line.startswith("entry =")]
    at = lines.index("[tool.ngargparser]\n") + 1
    lines[at:at] = [f'entry = "{entry}"\n']
    pyproject.write_text("".join(lines))


def build(project, run_build):
    run = run_build(project, expect_ok=False)
    assert run.proc.returncode == 0, run.proc.stdout + run.proc.stderr
    (tarball,) = (project / "build").glob("IEDB_NG_*-tver.tar.gz")
    return tarball


def deploy(tarball, version_dir, tmp_path):
    """Extract `tarball` into `version_dir` and run its deploy/install.sh there."""
    unpacked = tmp_path / f"unpacked-{version_dir.parent.name}-{version_dir.name}"
    with tarfile.open(tarball) as tf:
        tf.extractall(unpacked, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    (top,) = unpacked.iterdir()
    version_dir.parent.mkdir(parents=True, exist_ok=True)
    top.rename(version_dir)
    proc, _calls = run_install(version_dir, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return version_dir


def flip(apps, name, version):
    current = apps / name / "current"
    if current.is_symlink():
        current.unlink()
    current.symlink_to(version)
    return current


def test_a_caller_follows_a_redeployed_callee_without_reconfiguring(
    in_tmp_dir, monkeypatch, run_build, fake_venv, tmp_path
):
    sources = in_tmp_dir / "sources"
    callee = generate(sources, "callee", monkeypatch)
    set_predict(callee / "src" / "run_callee.py", CALLEE_PREDICT.format(version="v1"))
    fake_venv(callee)  # so the build's startup check runs the help screens
    caller = generate(sources, "caller", monkeypatch)
    monkeypatch.chdir(caller)
    cli.add_deps_to_paths("paths.py", ["callee"])
    # The path on the developer's machine; each host sets its own with a flag.
    dev_path = subprocess.run(
        [sys.executable, "src/core/configure.py", "--callee-path=/opt/dev/callee"],
        cwd=caller,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert dev_path.returncode == 0, dev_path.stdout + dev_path.stderr
    set_predict(caller / "src" / "run_caller.py", CALLER_PREDICT)
    fake_venv(caller)

    apps = tmp_path / "apps"
    deploy(build(callee, run_build), apps / "callee" / "1.0.0", tmp_path)
    caller_dir = deploy(build(caller, run_build), apps / "caller" / "1.0.0", tmp_path)
    wired = subprocess.run(
        ["./configure", f"--callee-path={apps / 'callee' / 'current'}"],
        cwd=caller_dir,
        env=OUTER_ENV,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert wired.returncode == 0, wired.stdout + wired.stderr
    flip(apps, "callee", "1.0.0")
    caller_current = flip(apps, "caller", "1.0.0")
    job = tmp_path / "job.json"
    job.write_text("{}\n")

    def run_caller():
        python = caller_current / ".venv" / "bin" / "python"
        return subprocess.run(
            [str(python), str(caller_current / "src" / "run_caller.py"), "predict", "-j", str(job)],
            env=OUTER_ENV,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def run_from_a_shell():
        call = f'source ./setup_callee_env.sh && "$CALLEE_VENV/bin/python" "$CALLEE_ENTRY" predict -j "{job}"'
        return subprocess.run(
            ["bash", "-c", call], cwd=caller_current, env=OUTER_ENV, capture_output=True, text=True, timeout=120
        )

    # The script in the version folder that callee's virtualenv is in, not under `current`.
    v1 = f"callee v1 ran {apps / 'callee' / '1.0.0' / 'src' / 'run_callee.py'}"
    for proc in (run_caller(), run_from_a_shell()):
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert proc.stdout.strip().splitlines()[-1] == v1

    # callee 2.0.0: the entry script is renamed and a second run_*.py sits next to
    # it, so only [tool.ngargparser] entry in pyproject.toml says which one to run.
    (callee / "src" / "run_callee.py").rename(callee / "src" / "run_callee_v2.py")
    script = callee / "src" / "run_callee_v2.py"
    script.write_text(script.read_text().replace('"callee v1 ran "', '"callee v2 ran "'))
    (callee / "src" / "run_helper.py").write_text("print('helper')\n")
    set_entry(callee, "src/run_callee_v2.py")
    deploy(build(callee, run_build), apps / "callee" / "2.0.0", tmp_path)
    flip(apps, "callee", "2.0.0")

    v2 = f"callee v2 ran {apps / 'callee' / '2.0.0' / 'src' / 'run_callee_v2.py'}"
    for proc in (run_caller(), run_from_a_shell()):
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert proc.stdout.strip().splitlines()[-1] == v2


def test_a_caller_deployed_first_finds_its_callee_from_python_once_it_arrives(
    in_tmp_dir, monkeypatch, run_build, fake_venv, tmp_path
):
    # The caller's Python reads CALLEE_VENV and CALLEE_ENTRY from the callee's .env
    # at start-up, because ./configure listed them in APP_KEYS_FROM_DEPS while the
    # callee was not there yet. Without them listed: KeyError: 'CALLEE_VENV'.
    sources = in_tmp_dir / "sources"
    callee = generate(sources, "callee", monkeypatch)
    set_predict(callee / "src" / "run_callee.py", CALLEE_PREDICT.format(version="v1"))
    fake_venv(callee)
    caller = generate(sources, "caller", monkeypatch)
    monkeypatch.chdir(caller)
    cli.add_deps_to_paths("paths.py", ["callee"])
    dev_path = subprocess.run(
        [sys.executable, "src/core/configure.py", "--callee-path=/opt/dev/callee"],
        cwd=caller,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert dev_path.returncode == 0, dev_path.stdout + dev_path.stderr
    set_predict(caller / "src" / "run_caller.py", CALLER_PREDICT_FROM_PYTHON)
    fake_venv(caller)

    apps = tmp_path / "apps"
    caller_dir = deploy(build(caller, run_build), apps / "caller" / "1.0.0", tmp_path)
    callee_current = apps / "callee" / "current"
    wired = subprocess.run(
        ["./configure", f"--callee-path={callee_current}"],
        cwd=caller_dir,
        env=OUTER_ENV,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert wired.returncode == 0, wired.stdout + wired.stderr
    assert not callee_current.exists()  # not deployed yet
    caller_current = flip(apps, "caller", "1.0.0")

    deploy(build(callee, run_build), apps / "callee" / "1.0.0", tmp_path)
    flip(apps, "callee", "1.0.0")
    job = tmp_path / "job.json"
    job.write_text("{}\n")
    proc = subprocess.run(
        [
            str(caller_current / ".venv" / "bin" / "python"),
            str(caller_current / "src" / "run_caller.py"),
            "predict",
            "-j",
            str(job),
        ],
        env=OUTER_ENV,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (
        proc.stdout.strip().splitlines()[-1] == f"callee v1 ran {apps / 'callee' / '1.0.0' / 'src' / 'run_callee.py'}"
    )


# The caller runs callee through dependency_command, and from a shell through the
# setup script, with one call for any kind of entry. callee 2.0.0 starts through a
# shell script, published as APP_ENTRY: dependency_command ran it with python
# (SyntaxError), and so did "$CALLEE_PYTHON" "$CALLEE_ENTRY".

CALLER_PREDICT_DEPENDENCY_COMMAND = """\
        # Call the dependency from Python, with the command dependency_command builds.
        import subprocess
        from core.app_env import dependency_command
        argv, env = dependency_command("callee", "predict", "-j", str(args.input_json))
        sys.exit(subprocess.call(argv, env=env))
"""

CALLEE_SHELL_ENTRY = """\
#!/bin/sh
# callee 2.0.0 starts through this script.
here=$(cd "$(dirname "$0")/.." && pwd -P)
echo "callee shell entry: APP_NAME=${APP_NAME-<unset>}"
exec "$here/.venv/bin/python" "$here/src/run_callee.py" "$@"
"""


def test_a_caller_runs_a_redeployed_callee_through_dependency_command_and_the_setup_script(
    in_tmp_dir, monkeypatch, run_build, fake_venv, tmp_path
):
    sources = in_tmp_dir / "sources"
    callee = generate(sources, "callee", monkeypatch)
    set_predict(callee / "src" / "run_callee.py", CALLEE_PREDICT.format(version="v1"))
    fake_venv(callee)
    caller = generate(sources, "caller", monkeypatch)
    monkeypatch.chdir(caller)
    cli.add_deps_to_paths("paths.py", ["callee"])
    dev_path = subprocess.run(
        [sys.executable, "src/core/configure.py", "--callee-path=/opt/dev/callee"],
        cwd=caller,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert dev_path.returncode == 0, dev_path.stdout + dev_path.stderr
    set_predict(caller / "src" / "run_caller.py", CALLER_PREDICT_DEPENDENCY_COMMAND)
    fake_venv(caller)

    apps = tmp_path / "apps"
    deploy(build(callee, run_build), apps / "callee" / "1.0.0", tmp_path)
    caller_dir = deploy(build(caller, run_build), apps / "caller" / "1.0.0", tmp_path)
    wired = subprocess.run(
        ["./configure", f"--callee-path={apps / 'callee' / 'current'}"],
        cwd=caller_dir,
        env=OUTER_ENV,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert wired.returncode == 0, wired.stdout + wired.stderr
    flip(apps, "callee", "1.0.0")
    caller_current = flip(apps, "caller", "1.0.0")
    job = tmp_path / "job.json"
    job.write_text("{}\n")

    def run_caller():
        python = caller_current / ".venv" / "bin" / "python"
        return subprocess.run(
            [str(python), str(caller_current / "src" / "run_caller.py"), "predict", "-j", str(job)],
            env=OUTER_ENV,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def run_from_a_shell(call):
        return subprocess.run(
            ["sh", "-c", f'set -eu; . ./setup_callee_env.sh; {call} predict -j "{job}"'],
            cwd=caller_current,
            env=OUTER_ENV,
            capture_output=True,
            text=True,
            timeout=120,
        )

    one_call = '${CALLEE_PYTHON:+"$CALLEE_PYTHON"} "$CALLEE_ENTRY"'
    v1 = f"callee v1 ran {apps / 'callee' / '1.0.0' / 'src' / 'run_callee.py'}"
    for proc in (run_caller(), run_from_a_shell('"$CALLEE_PYTHON" "$CALLEE_ENTRY"'), run_from_a_shell(one_call)):
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert proc.stdout.strip().splitlines()[-1] == v1

    # callee 2.0.0 publishes a shell script as its entry (APP_ENTRY in its paths.py).
    script = callee / "src" / "run_callee.py"
    script.write_text(script.read_text().replace('"callee v1 ran "', '"callee v2 ran "'))
    (callee / "bin").mkdir()
    (callee / "bin" / "callee.sh").write_text(CALLEE_SHELL_ENTRY)
    (callee / "bin" / "callee.sh").chmod(0o755)
    (callee / "paths.py").write_text('APP_ENTRY = "bin/callee.sh"\n')
    deploy(build(callee, run_build), apps / "callee" / "2.0.0", tmp_path)
    flip(apps, "callee", "2.0.0")

    v2 = f"callee v2 ran {apps / 'callee' / '2.0.0' / 'src' / 'run_callee.py'}"
    from_python = run_caller()
    assert from_python.returncode == 0, from_python.stdout + from_python.stderr
    # Not the caller's APP_NAME: dependency_command leaves the caller's identity out.
    assert from_python.stdout.strip().splitlines()[-2:] == ["callee shell entry: APP_NAME=<unset>", v2]
    from_a_shell = run_from_a_shell(one_call)
    assert from_a_shell.returncode == 0, from_a_shell.stdout + from_a_shell.stderr
    assert from_a_shell.stdout.strip().splitlines()[-1] == v2
