"""`cli deps`: parallel runs that change paths.py, a paths.py that is not UTF-8, the
lines `cli deps list` prints, and the ./configure command `cli deps add` prints.
"""

import os
import re
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))


def deps(action, *names):
    return cli.deps_command(Namespace(deps_action=action, names=list(names)))


def declared(project):
    return sorted(cli._paths_dependencies((project / "paths.py").read_text()))


# --- 7. Parallel runs -----------------------------------------------------------------------------

# cli deps add, with a pause between reading paths.py and writing it: long enough that a
# second one started at the same time reads the file before the first one writes it.
SLOW_ADD = """\
import sys, time
from ngargparser import cli

real = cli._write_paths


def slow(path, content):
    time.sleep(1.5)
    real(path, content)


cli._write_paths = slow
sys.argv = ["cli", "deps", "add", sys.argv[1]]
sys.exit(cli.main())
"""


@pytest.mark.skipif(os.name != "posix", reason="flock")
def test_parallel_adds_keep_every_dependency(scaffolded_project):
    assert deps("add", "d0") == 0
    env = {**os.environ, "PYTHONPATH": PACKAGE_ROOT, "NGARGPARSER_NO_UPDATE_CHECK": "1"}
    runs = [
        subprocess.Popen(
            [sys.executable, "-c", SLOW_ADD, f"d{n}"],
            cwd=scaffolded_project,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for n in (1, 2, 3)
    ]
    outputs = [run.communicate(timeout=120)[0] for run in runs]

    assert [run.returncode for run in runs] == [0, 0, 0], outputs
    assert declared(scaffolded_project) == ["d0", "d1", "d2", "d3"]
    assert (scaffolded_project / ".ngargparser" / "paths.lock").is_file()
    # .ngargparser/ is ignored in git, the lock with it.
    assert ".ngargparser/*\n" in (scaffolded_project / ".gitignore").read_text()
    assert "!.ngargparser/paths.lock" not in (scaffolded_project / ".gitignore").read_text()


@pytest.mark.skipif(os.name != "posix", reason="flock")
def test_a_lock_held_too_long_stops_cli_deps_and_names_the_holder(scaffolded_project, strip_ansi, capsys, monkeypatch):
    import fcntl

    assert deps("add", "pepx") == 0
    capsys.readouterr()
    before = (scaffolded_project / "paths.py").read_bytes()
    monkeypatch.setattr(cli, "_PATHS_LOCK_WAIT", 0.3, raising=False)
    lock = scaffolded_project / ".ngargparser" / "paths.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # what another cli deps or ./configure holds while it writes
        os.write(fd, f"{os.getpid()}\n".encode())
        for action, name in (("add", "netmhcpan"), ("remove", "pepx")):
            assert deps(action, name) == 1
            out = strip_ansi(capsys.readouterr().err)
            assert out == (
                f"✗ Another process (pid {os.getpid()}) is changing paths.py: it still holds "
                ".ngargparser/paths.lock after 0.3s. Nothing was changed. Wait for it to finish (a cli deps or "
                "./configure), then run cli deps again.\n"
            )
            assert (scaffolded_project / "paths.py").read_bytes() == before
    finally:
        os.close(fd)

    # Control: once it is given back, the same command goes ahead.
    assert deps("add", "netmhcpan") == 0
    assert declared(scaffolded_project) == ["netmhcpan", "pepx"]


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="file modes")
def test_without_a_lock_file_cli_deps_stops_and_changes_nothing(scaffolded_project):
    # A .ngargparser/ that is not writable in a tool folder that is: no lock file can be made, and
    # going on without it would let a parallel run lose this change (see test_deps_and_colour.py).
    state = scaffolded_project / ".ngargparser"
    state.chmod(0o555)
    try:
        assert deps("add", "pepx") == 1
    finally:
        state.chmod(0o755)
    assert not (scaffolded_project / "paths.py").exists()
    assert not (state / "paths.lock").exists()


# --- 8. A paths.py that is not UTF-8 ---------------------------------------------------------------

LATIN_1 = b'# -*- coding: latin-1 -*-\n# caf\xe9\npepx_path = "/opt/pepx"\n'
NOT_UTF8 = (
    "✗ paths.py is not UTF-8 text (byte 0xe9 at offset 31): save it as UTF-8, then run cli deps again; "
    "nothing was changed.\n"
)


@pytest.mark.parametrize("command", [("list",), ("add", "netmhcpan"), ("remove", "pepx")])
def test_a_paths_py_that_is_not_utf8_is_said_in_one_line(scaffolded_project, strip_ansi, capsys, command):
    (scaffolded_project / "paths.py").write_bytes(LATIN_1)

    assert deps(*command) == 1

    assert strip_ansi(capsys.readouterr().err) == NOT_UTF8
    assert (scaffolded_project / "paths.py").read_bytes() == LATIN_1


def test_the_menu_says_a_paths_py_is_not_utf8_in_one_line(scaffolded_project, strip_ansi, capsys, monkeypatch):
    (scaffolded_project / "paths.py").write_bytes(LATIN_1)
    monkeypatch.setattr("builtins.input", lambda _prompt="": pytest.fail("the menu asked a question"))

    assert cli.setup_paths_file("paths.py") == 1

    assert strip_ansi("".join(capsys.readouterr())) == "\n" + NOT_UTF8
    assert (scaffolded_project / "paths.py").read_bytes() == LATIN_1


def test_sync_does_not_read_paths_py(scaffolded_project, sync_args, strip_ansi, capsys):
    # Control: cli sync never reads paths.py, so a Latin-1 one does not stop it.
    (scaffolded_project / "paths.py").write_bytes(LATIN_1)

    assert cli.sync_command(sync_args(verify=False)) == 0
    assert "Traceback" not in strip_ansi(capsys.readouterr().out)
    assert (scaffolded_project / "paths.py").read_bytes() == LATIN_1


@pytest.mark.parametrize("action", ["add", "remove"])
def test_a_paths_py_that_does_not_parse_is_said_in_whole_sentences(scaffolded_project, strip_ansi, capsys, action):
    broken = b"pepx_path = '/opt/pepx\n"  # an unterminated string
    (scaffolded_project / "paths.py").write_bytes(broken)

    assert deps(action, "pepx") == 1

    out = strip_ansi(capsys.readouterr().err)
    # The error from Python, as one sentence (its wording differs between Python versions), then what to do.
    assert re.fullmatch(
        r"✗ paths\.py doesn't parse \(line 1\): [^\n]*[^.?!]\. Fix it, then run cli deps again; nothing was "
        r"changed\.\n",
        out,
    ), out
    assert (scaffolded_project / "paths.py").read_bytes() == broken


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="file modes")
def test_a_paths_py_that_cannot_be_read_is_said_in_whole_sentences(scaffolded_project, strip_ansi, capsys):
    paths = scaffolded_project / "paths.py"  # a file of its own, not a link
    paths.write_text('pepx_path = "/opt/pepx"\n')
    paths.chmod(0o000)
    try:
        assert deps("add", "netmhcpan") == 1
    finally:
        paths.chmod(0o644)

    assert strip_ansi("".join(capsys.readouterr())) == (
        "✗ can't read 'paths.py': [Errno 13] Permission denied: 'paths.py'. Fix it, then run cli deps again; "
        "nothing was changed.\n"
    )


# --- 9. The lines cli deps list prints -------------------------------------------------------------


def test_list_names_each_path_variable(scaffolded_project, strip_ansi, capsys):
    assert deps("add", "alpha", "pepx") == 0
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text().replace("pepx_path=None", 'pepx_path="/opt/pepx"'))
    capsys.readouterr()

    assert deps("list") == 0

    assert strip_ansi(capsys.readouterr().out) == (
        "Dependencies declared in 'paths.py':\n"
        "  ○ Alpha (alpha)                  alpha_path = None\n"
        '  ● Pepx (pepx)                    pepx_path = "/opt/pepx"\n'
        "\n● = path filled in   ○ = stub (still None)\n"
    )


# --- 10. The ./configure command cli deps add prints ----------------------------------------------


def test_add_prints_one_configure_command_that_sets_every_new_path(scaffolded_project, strip_ansi, capsys, tmp_path):
    assert deps("add", "alpha", "beta") == 0

    out = strip_ansi(capsys.readouterr().out)
    command = "./configure --alpha-path=/path/to/alpha --beta-path=/path/to/beta"
    assert out == (
        "✓ Added 2 dependency stub(s) to 'paths.py': alpha, beta\n"
        f"   Set their paths: {command}\n"
        "   Or edit 'paths.py' by hand.\n"
    )
    # That one command sets both (each one alone stops: the other path is still None).
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
    flags = [flag.replace("/path/to", str(tmp_path)) for flag in command.split()[1:]]
    run = subprocess.run(
        [sys.executable, "src/core/configure.py", *flags], cwd=scaffolded_project, capture_output=True, text=True
    )
    assert run.returncode == 0, run.stdout + run.stderr
    values = cli._paths_dependencies((scaffolded_project / "paths.py").read_text())
    assert {name: values[name]["path"].strip("'\"") for name in values} == {
        "alpha": str(tmp_path / "alpha"),
        "beta": str(tmp_path / "beta"),
    }


def test_add_of_one_dependency_prints_its_command(scaffolded_project, strip_ansi, capsys):
    # Control: one new dependency, one flag, as before.
    assert deps("add", "pepx") == 0
    assert "   Set its path: ./configure --pepx-path=/path/to/pepx\n" in strip_ansi(capsys.readouterr().out)
