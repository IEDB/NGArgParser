"""The startup check's help screens: what they start, what they remove, and
what their failure says.

A help screen that started a program in the background left it running: it
wrote into the build copy while the build packed it (tar: file changed as we
read it) or after the build made it read-only, and one that kept the help
screen's stderr open made a 0.1 s screen time out. Each help screen now runs
in a session of its own, its whole process group is killed when it ends, and
its stderr goes to a file. A help screen that deleted pyproject.toml from the
build copy still ended in "Build completed": the required files are checked
again after the startup check.

A dev-only import (pytest) failed with no hint, since `uv run` works in the
tool folder: the check now names the package to add. Later help screens
that fail the same way are one line each. A shell entry saved with Windows
line endings failed with "No such file or directory" for a file that exists.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest
from test_build_lock import find_program
from test_build_robustness import process_alive
from test_build_startup import dev_venv, fake_uv

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)
    monkeypatch.delenv("NGARGPARSER_STARTUP_CHECK_TIMEOUT", raising=False)


def in_the_build_copy(project_dir, code):
    """Each help screen of the build copy's entry script runs `code` first."""
    run_script = project_dir / "src" / "run_demo.py"
    body = "".join(f"    {line}\n" for line in code.splitlines())
    run_script.write_text(
        f'import os as _os, subprocess as _sp, sys as _sys\nif "/build/" in __file__:\n{body}' + run_script.read_text()
    )


def test_a_program_a_help_screen_starts_is_stopped(scaffolded_project, run_build, fake_venv, tmp_path):
    fake_venv(scaffolded_project)
    pids = tmp_path / "pids"
    # A second after the help screen, it writes into the build copy, as a log
    # thread or a forked server would.
    in_the_build_copy(
        scaffolded_project,
        "late = _os.path.join(_os.environ['APP_ROOT'], 'src', 'late.log')\n"
        "child = _sp.Popen(['sh', '-c', 'sleep 1; echo late > \"$0\"', late],"
        " stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)\n"
        f"open({str(pids)!r}, 'a').write('%d\\n' % child.pid)",
    )

    run = run_build(scaffolded_project, progress=True)
    time.sleep(2)

    assert not (run.staged / "src" / "late.log").exists(), "the program wrote into the build copy after the build"
    assert "src/late.log" not in run.members
    assert "file changed as we read it" not in run.proc.stderr
    started = [int(pid) for pid in pids.read_text().split()]
    assert len(started) == 4, "one per help screen"
    assert not any(process_alive(pid) for pid in started)


def test_a_program_holding_stderr_open_does_not_time_the_screen_out(scaffolded_project, run_build, fake_venv, tmp_path):
    fake_venv(scaffolded_project)
    pids = tmp_path / "pids"
    in_the_build_copy(
        scaffolded_project,
        f"child = _sp.Popen(['sleep', '30'])\nopen({str(pids)!r}, 'a').write('%d\\n' % child.pid)",
    )

    started = time.monotonic()
    run = run_build(scaffolded_project, env={"NGARGPARSER_STARTUP_CHECK_TIMEOUT": "10"})
    took = time.monotonic() - started

    assert "timed out" not in run.proc.stdout + run.proc.stderr
    assert "Startup check passed." in run.proc.stdout
    assert took < 30, f"the build took {took:.1f}s: a help screen waited for the program it started"
    assert not any(process_alive(int(pid)) for pid in pids.read_text().split())


def test_a_help_screen_that_removes_a_required_file_fails_the_build(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    in_the_build_copy(
        scaffolded_project,
        "_p = _os.path.join(_os.environ['APP_ROOT'], 'pyproject.toml')\nif _os.path.exists(_p):\n    _os.remove(_p)",
    )

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        "ERROR: The startup check (the tool's help screens) removed files a deploy needs from the build copy:\n"
        "  pyproject.toml: deploy/install.sh runs uv sync, which reads it\n"
    ) in run.proc.stderr
    assert "Build completed" not in run.proc.stdout
    assert not run.tarball.exists()


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX process groups")
def test_ctrl_c_stops_a_help_screen_in_its_own_session(scaffolded_project, fake_venv, tmp_path):
    # The help screen no longer gets the terminal's Ctrl-C itself: the build
    # stops it.
    fake_venv(scaffolded_project)
    started = tmp_path / "started"
    in_the_build_copy(
        scaffolded_project,
        f"open({str(started)!r}, 'w').write(str(_os.getpid()))\nimport time as _t\n_t.sleep(60)",
    )
    proc = subprocess.Popen(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=scaffolded_project,
        env={**os.environ, "TOOL_VERSION": "tver"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )
    try:
        deadline = time.monotonic() + 60
        while not (started.exists() and started.read_text()):
            assert proc.poll() is None, "the build ended before the startup check ran"
            assert time.monotonic() < deadline, "the startup check did not start"
            time.sleep(0.05)
        os.killpg(proc.pid, signal.SIGINT)
        _, stderr = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    assert proc.returncode == 130, stderr
    deadline = time.monotonic() + 10
    while process_alive(int(started.read_text())):
        assert time.monotonic() < deadline, "the help screen still runs"
        time.sleep(0.05)


def dev_package(tmp_path, module, package):
    """Make `module` part of an installed package named `package` in dev_venv's folder."""
    dev = tmp_path / "dev-packages"
    dev.mkdir(exist_ok=True)
    (dev / f"{module}.py").write_text("X = 1\n")
    info = dev / f"{package.replace('-', '_')}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {package}\nVersion: 1.0\n")
    (info / "RECORD").write_text(f"{module}.py,,\n{info.name}/METADATA,,\n{info.name}/RECORD,,\n")


def test_a_dev_only_import_names_the_package_to_add(scaffolded_project, run_build, tmp_path):
    dev_venv(scaffolded_project, tmp_path)
    dev_package(tmp_path, "devonly", "devonly-dist")
    env, _log = fake_uv(tmp_path)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    validators = scaffolded_project / "src" / "validators.py"
    original = validators.read_text()
    validators.write_text(original + "import devonly\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    err = run.proc.stderr
    assert "src/run_demo.py --help: ModuleNotFoundError: No module named 'devonly'\n    " in err
    assert err.count("Traceback (most recent call last)") == 1
    for screen in ("predict", "preprocess", "postprocess"):
        assert (
            f"src/run_demo.py {screen} --help: ModuleNotFoundError: No module named 'devonly' (same error as above)\n"
        ) in err
    assert (
        "devonly is only in the dev group (or not in [project] dependencies), which a deploy leaves out: "
        "uv add devonly-dist\n"
    ) in err
    assert err.count("uv add") == 1

    # Control: a module the tool's .venv lacks too gets no such line.
    validators.write_text(original + "import nosuchmodule\n")
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)
    assert "No module named 'nosuchmodule'" in run.proc.stderr
    assert "uv add" not in run.proc.stderr

    # Nor does a module of the tool itself, installed in the .venv as a package of that name.
    dev_package(tmp_path, "ownmodule", "demo")
    validators.write_text(original + "import ownmodule\n")
    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)
    assert "No module named 'ownmodule'" in run.proc.stderr
    assert "uv add" not in run.proc.stderr


def test_a_shell_entry_with_windows_line_endings_says_so(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    entry = scaffolded_project / "bin" / "run.sh"
    entry.parent.mkdir()
    entry.write_bytes(b'#!/bin/sh\r\nexec "$(dirname "$0")/../.venv/bin/python" src/run_demo.py "$@"\r\n')
    entry.chmod(0o755)
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('entry = "src/run_demo.py"', 'entry = "bin/run.sh"'))
    assert 'entry = "bin/run.sh"' in pyproject.read_text()

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    fix = "perl -pi -e 's/\\r$//' bin/run.sh" if sys.platform == "darwin" else 'sed -i "s/\\r$//" bin/run.sh'
    assert (
        "ERROR: The tool does not start from the build dir:\n"
        "bin/run.sh's #! line ends in a carriage return (Windows line endings), so it cannot run: "
        f"convert it with '{fix}' (and add 'bin/run.sh text eol=lf' to .gitattributes)\n"
    ) in run.proc.stderr
    assert "No such file or directory" not in run.proc.stderr

    # The command the message gives fixes the file, and the tool starts.
    subprocess.run(["sh", "-c", fix], cwd=scaffolded_project, check=True, timeout=60)
    run = run_build(scaffolded_project)
    assert "Startup check passed." in run.proc.stdout


# A subcommand the tool adds with self.subparser.add_parser was not checked:
# the check ran --help and the framework subcommands' --help only, so one
# whose help crashed still built. The subcommands are now read from the
# usage line of the top-level --help ({predict,preprocess,postprocess,...}).

BROKEN_HELP = """\
class BrokenHelp(argparse.HelpFormatter):
    # Crashes only when this subcommand's help is shown, on every Python.
    def format_help(self):
        raise KeyError("bogus")


"""


def add_subcommands(project_dir, *lines):
    """Add `lines` after set_subcommand_order in the tool's parser, and BrokenHelp before it."""
    parser = project_dir / "src" / "DemoArgumentParser.py"
    text = parser.read_text()
    anchor = "        self.set_subcommand_order(['predict', 'preprocess', 'postprocess'])\n"
    assert anchor in text and "import argparse\n" in text
    text = text.replace(anchor, anchor + "".join(f"        {line}\n" for line in lines))
    parser.write_text(text.replace("class DemoArgumentParser(", BROKEN_HELP + "class DemoArgumentParser(", 1))


def test_every_subcommand_the_usage_lists_is_checked(scaffolded_project, run_build, fake_venv, tmp_path):
    fake_venv(scaffolded_project)
    screens = tmp_path / "screens"
    in_the_build_copy(scaffolded_project, f"open({str(screens)!r}, 'a').write(' '.join(_sys.argv[1:]) + '\\n')")
    add_subcommands(
        scaffolded_project,
        "self.parser_list = self.subparser.add_parser('list-alleles', help='List the alleles.', aliases=['la'])",
        "self.parser_bad = self.subparser.add_parser('bad-help', help='Crashes.', formatter_class=BrokenHelp)",
    )

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert "\nsrc/run_demo.py bad-help --help: KeyError: 'bogus'\n" in run.proc.stderr
    assert run.proc.stderr.count("src/run_demo.py ") == 1, "the other screens pass"
    assert not run.tarball.exists()
    # The framework's first, then the others in the order the usage lists them.
    assert screens.read_text().splitlines() == [
        "--help",
        "predict --help",
        "preprocess --help",
        "postprocess --help",
        "list-alleles --help",
        "la --help",
        "bad-help --help",
    ]

    # Control: without the subcommand whose help crashes, the tool builds.
    parser = scaffolded_project / "src" / "DemoArgumentParser.py"
    parser.write_text("".join(line for line in parser.read_text().splitlines(True) if "parser_bad" not in line))
    screens.unlink()
    run = run_build(scaffolded_project)
    assert "Startup check passed." in run.proc.stdout
    assert screens.read_text().splitlines()[-2:] == ["list-alleles --help", "la --help"]


# The help screens of a Python entry ran with the PATH and VIRTUAL_ENV of the
# shell that ran the build (VIRTUAL_ENV=/elsewhere/.venv went through), where
# cli sync's test drive names the tool's .venv. Every entry now gets the
# check's environment: VIRTUAL_ENV names it (as .venv in the build copy), its
# bin/ is first on PATH, PYTHONHOME, PYTHONPATH and PYTHONSTARTUP are unset,
# PYTHONNOUSERSITE=1, and stdin is /dev/null.

ENVIRONMENT_LOG = (
    "import json as _json\n"
    "_null, _in = _os.stat(_os.devnull), _os.fstat(0)\n"
    "_venv = _os.environ.get('VIRTUAL_ENV', '')\n"
    "_seen = {'argv': _sys.argv[1:], 'VIRTUAL_ENV': _venv, 'real': _os.path.realpath(_venv),\n"
    "         'first_on_path': _os.environ['PATH'].split(_os.pathsep)[0],\n"
    "         'set': sorted(n for n in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP') if n in _os.environ),\n"
    "         'PYTHONNOUSERSITE': _os.environ.get('PYTHONNOUSERSITE'),\n"
    "         'stdin_is_dev_null': (_in.st_dev, _in.st_ino) == (_null.st_dev, _null.st_ino)}\n"
    "open(LOG, 'a').write(_json.dumps(_seen) + '\\n')"
)


@pytest.mark.parametrize("entry", ["python", "shell"])
def test_every_entry_runs_in_the_environment_of_the_check(scaffolded_project, run_build, tmp_path, entry):
    dev_venv(scaffolded_project, tmp_path)
    env, _log = fake_uv(tmp_path)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    seen = tmp_path / "seen.jsonl"
    in_the_build_copy(scaffolded_project, ENVIRONMENT_LOG.replace("LOG", repr(str(seen))))
    if entry == "shell":
        # Control: an entry that is not a Python script got this environment already.
        script = scaffolded_project / "bin" / "run.sh"
        script.parent.mkdir()
        script.write_text('#!/bin/sh\nexec python src/run_demo.py "$@"\n')
        script.chmod(0o755)
        pyproject = scaffolded_project / "pyproject.toml"
        pyproject.write_text(pyproject.read_text().replace('entry = "src/run_demo.py"', 'entry = "bin/run.sh"'))
    shell = {
        "VIRTUAL_ENV": "/elsewhere/.venv",
        "PATH": f"/elsewhere/.venv/bin{os.pathsep}{env['PATH']}",
        "PYTHONPATH": "/elsewhere/lib",
        "PYTHONSTARTUP": "/elsewhere/startup.py",
    }

    run = run_build(scaffolded_project, progress=True, env=shell)

    screens = [json.loads(line) for line in seen.read_text().splitlines()]
    assert [s["argv"][0] for s in screens] == ["--help", "predict", "preprocess", "postprocess"]
    startup_venv = scaffolded_project / ".ngargparser" / "startup-venv"
    for s in screens:
        assert s["VIRTUAL_ENV"] == str(run.staged / ".venv"), s
        assert s["real"] == os.path.realpath(startup_venv), s
        assert s["first_on_path"] == str(run.staged / ".venv" / "bin"), s
        assert s["set"] == [], s
        assert s["PYTHONNOUSERSITE"] == "1", s
        assert s["stdin_is_dev_null"], s
    assert not os.path.lexists(run.staged / ".venv"), "the link is for the help screens only"
    assert ".venv" not in {name.split("/")[0] for name in run.members}


# pkill -TERM -f build.sh stops build.sh and its subshells at once. The
# subshell that ran the startup check's driver died with them, so the build
# could no longer find the driver: it ran on, with the help screen and what
# that started, and held the build's output open (make build | cat did not
# return). The driver now stops when the shell that started it is gone.


def session_processes(sid):
    """(pid, command line) of each process in the session `sid` that is not a zombie, from /proc."""
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                fields = f.read().rsplit(b") ", 1)[1].split()
            with open(f"/proc/{name}/cmdline", "rb") as f:
                cmdline = f.read()
        except (OSError, IndexError):
            continue
        if fields[0] != b"Z" and int(fields[3]) == sid:
            found.append((int(name), cmdline))
    return found


@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="reads /proc")
def test_term_to_the_build_and_its_subshells_leaves_nothing_running(scaffolded_project, fake_venv, tmp_path):
    if find_program("make", os.environ["PATH"]) is None:
        pytest.skip("needs make")
    fake_venv(scaffolded_project)
    pids = tmp_path / "pids"
    in_the_build_copy(
        scaffolded_project,
        "child = _sp.Popen(['sleep', '300'])\n"
        f"open({str(pids)!r}, 'w').write('%d %d\\n' % (_os.getpid(), child.pid))\n"
        "import time as _t\n"
        "_t.sleep(300)",
    )
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}
    proc = subprocess.Popen(
        ["sh", "-c", "make build 2>&1 | cat"],
        cwd=scaffolded_project,
        env={**env, "TOOL_VERSION": "tver"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    started = []
    try:
        deadline = time.monotonic() + 60
        while not (pids.exists() and pids.read_text().endswith("\n")):
            assert proc.poll() is None, "the build ended before the startup check ran"
            assert time.monotonic() < deadline, "the startup check did not start"
            time.sleep(0.05)
        started = [int(pid) for pid in pids.read_text().split()]
        # As pkill -TERM -f build.sh: every process whose command line names build.sh, at once.
        targets = [pid for pid, cmdline in session_processes(proc.pid) if b"build.sh" in cmdline]
        assert len(targets) >= 2, "build.sh and the subshell that runs the driver"
        for pid in targets:
            os.kill(pid, signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)

        assert b"Build failed" in out
        deadline = time.monotonic() + 10
        while any(process_alive(pid) for pid in started) or session_processes(proc.pid):
            assert time.monotonic() < deadline, ("still running", started, session_processes(proc.pid))
            time.sleep(0.05)
    finally:
        for pid, _cmdline in session_processes(proc.pid):
            os.kill(pid, signal.SIGKILL)
        for pid in started:
            if process_alive(pid):
                os.kill(pid, signal.SIGKILL)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
