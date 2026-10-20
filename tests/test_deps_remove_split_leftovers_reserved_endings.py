"""`cli deps`: remove with commas, lines left reading a removed variable, names other
programs read, and a paths.py whose lines end in different ways.

* `cli deps remove pepx,netmhcpan` looked for one dependency named "pepx,netmhcpan"
  (add splits at commas). Remove splits the same way now.
* A line outside a block that reads a removed setting (`aug_path += "aug"`) stayed, and
  ./configure then failed with a NameError. Remove now refuses, with its line number.
* `cli deps add node` gave the tool NODE_PATH, which Node.js reads; the same for a few
  other exact keys (GEM_PATH, PYSPARK_PYTHON, ...). These are refused now.
* A paths.py with CRLF and LF lines came back all CRLF. Each line keeps its own ending,
  and an added line gets the ending most lines have.
"""

import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli


def deps(action, *names):
    return cli.deps_command(Namespace(deps_action=action, names=list(names)))


def declared(project):
    return sorted(cli._paths_dependencies((project / "paths.py").read_text(encoding="utf-8-sig")))


# --- 7a. remove splits names at commas ---------------------------------------------------------


@pytest.mark.parametrize("given", [["pepx,netmhcpan"], ["pepx, netmhcpan"]])
def test_remove_splits_names_at_commas(scaffolded_project, strip_ansi, capsys, given):
    assert deps("add", "pepx", "netmhcpan", "keepme") == 0
    capsys.readouterr()

    assert deps("remove", *given) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "✓ Removed 2 dependency block(s) from 'paths.py': Pepx (pepx), Netmhcpan (netmhcpan)\n" in out
    assert "Not in 'paths.py'" not in out
    assert declared(scaffolded_project) == ["keepme"]


# --- 7b. A line left behind that reads a removed variable --------------------------------------


def test_remove_refuses_when_a_line_still_reads_a_removed_variable(scaffolded_project, strip_ansi, capsys):
    assert deps("add", "aug", "pepx") == 0
    paths = scaffolded_project / "paths.py"
    paths.write_text(paths.read_text() + '\naug_path += "aug"\naug_bin = aug_venv + "/bin"\nkeep = pepx_path\n')
    before = paths.read_text()
    capsys.readouterr()

    assert deps("remove", "aug") == 1

    err = strip_ansi(capsys.readouterr().err)
    lines = before.splitlines()
    first = lines.index('aug_path += "aug"') + 1
    second = lines.index('aug_bin = aug_venv + "/bin"') + 1
    assert err == (
        f"✗ Not removed: aug. In 'paths.py', line {first} uses aug_path, line {second} uses aug_venv, which the "
        "removal would delete, so ./configure would fail. Delete or fix those lines first. Nothing was changed.\n"
    )
    assert paths.read_text() == before  # pepx_path is still set: not a removed variable
    assert not (scaffolded_project / "paths.py.bak").exists()

    # Control: a variable the file still sets elsewhere (inside an if, which cli deps leaves) is not a refusal.
    paths.write_text("")
    assert deps("add", "aug") == 0
    paths.write_text(paths.read_text() + '\nif True:\n    aug_path = "/opt/aug"\nwhere = aug_path\n')
    capsys.readouterr()
    assert deps("remove", "aug") == 0
    out = strip_ansi(capsys.readouterr().out)
    assert "Removed 1 dependency block(s)" in out and "still uses" not in out
    run = subprocess.run([sys.executable, "paths.py"], cwd=scaffolded_project, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


# --- 7c. Names whose keys another program reads --------------------------------------------------


RESERVED = [
    ("node", "NODE_PATH", "Node.js"),
    ("gem", "GEM_PATH", "RubyGems"),
    ("lua", "LUA_PATH", "Lua"),
    ("git-exec", "GIT_EXEC_PATH", "git"),
    ("pip", "PIP_PYTHON", "pip"),
    ("virtualenv", "VIRTUALENV_PYTHON", "virtualenv"),
    ("pyspark", "PYSPARK_PYTHON", "PySpark"),
    ("pyspark-driver", "PYSPARK_DRIVER_PYTHON", "PySpark"),
]


def test_a_name_whose_key_another_program_reads_is_refused(scaffolded_project, strip_ansi, capsys):
    for name, key, reader in RESERVED:
        assert deps("add", name) == 1, name

        out = strip_ansi(capsys.readouterr().err)
        var = cli.normalize_name(name)
        keys = [f"{var}{suffix}".upper() for suffix in ("_path", "_venv", "_module", "_lib_path", "_entry")]
        assert (
            f"✗ Not added: '{name}' becomes '{var}', and the tool would set {', '.join(keys)} and "
            f"{var.upper()}_PYTHON for it, in .env and in its own environment. {reader} reads {key} as its own "
            f"setting, so it could change how {reader} runs in the tool. Pick another name.\n"
        ) in out
        assert not (scaffolded_project / "paths.py").exists()

    # Control: no key of these is one another program reads (GEM_HOME_PATH is not GEM_HOME).
    assert deps("add", "nodejs", "gem-home", "lua-c", "pyspark-tools") == 0
    assert declared(scaffolded_project) == ["gem_home", "lua_c", "nodejs", "pyspark_tools"]


# --- 7d. Mixed line endings --------------------------------------------------------------------

MIXED = "''' [ Pepx ] '''\r\npepx_path=None\r\n\r\n# my own note\npepx_venv=None\r\nkeep = 1\n"


def test_add_keeps_each_lines_ending_and_gives_new_lines_the_usual_one(scaffolded_project):
    paths = scaffolded_project / "paths.py"
    paths.write_bytes(MIXED.encode())

    assert deps("add", "netmhcpan") == 0

    raw = paths.read_bytes().decode()
    assert raw.startswith(MIXED.rstrip("\n"))  # every line it had, ending as it did
    assert "# my own note\n" in raw and "keep = 1" in raw and "# my own note\r\n" not in raw
    added = raw[raw.index("''' [ Netmhcpan ] '''") :]
    assert "\n" not in added.replace("\r\n", "")  # 4 of the 6 old lines end in CRLF: so does each added line


def test_remove_keeps_each_lines_ending(scaffolded_project):
    paths = scaffolded_project / "paths.py"
    paths.write_bytes(("keep = 1\n" + "other = 2\r\n").encode())
    assert deps("add", "pepx") == 0  # one LF line, one CRLF line: the first ending wins the tie for new lines
    assert "''' [ Pepx ] '''\n" in paths.read_bytes().decode()

    assert deps("remove", "pepx") == 0

    assert paths.read_bytes() == b"keep = 1\nother = 2\r\n"
