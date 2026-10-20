"""`cli deps`: names given with commas, names the tool cannot take, a paths.py written in
other forms, and a paths.py with Windows line endings.

* `cli deps add pepx,netmhcpan` added one dependency named "pepx,netmhcpan"; the
  menu did the same with "pepx, netmhcpan". Both split at commas and whitespace now.
* `cli deps add uv` gave the tool's process UV_PYTHON (from <DEP>_PYTHON), which picks
  the Python every uv command in it uses; `ünï` silently became `n`.
* `pepx_path: str = "/opt/pepx"` is a dependency to ./configure, but `cli deps list`
  did not show it, and `cli deps add pepx` added a second pepx_path = None below it.
* A CRLF paths.py came back with LF line endings.

The menu tests drive `cli deps` (no names) with a fake terminal and scripted answers to
input(), as in test_deps_names_hints_dupes.py.
"""

import io
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

BOM = "﻿"


def deps(action, *names):
    return cli.deps_command(Namespace(deps_action=action, names=list(names)))


class _Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def answers(monkeypatch):
    """Make stdin a terminal and feed `input()` these replies in order."""

    def _answers(*replies):
        monkeypatch.setattr(sys, "stdin", _Terminal())
        queue = iter(replies)
        monkeypatch.setattr("builtins.input", lambda *_: next(queue))

    return _answers


def declared(project):
    return sorted(cli._paths_dependencies((project / "paths.py").read_text(encoding="utf-8-sig")))


# --- 6a. Names given with commas or spaces --------------------------------------------------


@pytest.mark.parametrize("given", [["pepx,netmhcpan"], ["pepx, netmhcpan"], ["pepx,", ",netmhcpan"]])
def test_add_splits_names_at_commas_and_spaces(scaffolded_project, strip_ansi, capsys, given):
    assert deps("add", *given) == 0

    out = strip_ansi("".join(capsys.readouterr()))
    assert "✓ Added 2 dependency stub(s) to 'paths.py': pepx, netmhcpan\n" in out
    assert "   Set their paths: ./configure --pepx-path=/path/to/pepx --netmhcpan-path=/path/to/netmhcpan\n" in out
    assert declared(scaffolded_project) == ["netmhcpan", "pepx"]

    # A name with nothing but commas and spaces is still refused, as before.
    assert deps("add", ", ") == 1
    assert "✗ Not added: ', ' has no letters or digits" in strip_ansi("".join(capsys.readouterr()))


def test_the_menu_splits_an_answer_with_commas_and_spaces(scaffolded_project, answers, strip_ansi, capsys):
    answers("pepx, netmhcpan", "mhci", "")

    assert deps(None) == 0

    assert "with 3 dependencies" in strip_ansi(capsys.readouterr().out)
    assert declared(scaffolded_project) == ["mhci", "netmhcpan", "pepx"]


# --- 6b. Names whose settings a program reads, and names that are not ASCII ----------------


@pytest.mark.parametrize(
    "name, prefix, reader, example",
    [
        ("uv", "UV_", "uv", "UV_PYTHON, for one, picks the Python that uv uses"),
        ("uv-tools", "UV_", "uv", "UV_PYTHON, for one, picks the Python that uv uses"),
        ("python", "PYTHON", "Python", "PYTHONPATH, for one, changes where it finds modules"),
        ("Python3", "PYTHON", "Python", "PYTHONPATH, for one, changes where it finds modules"),
    ],
)
def test_add_refuses_a_name_whose_settings_a_program_reads(
    scaffolded_project, strip_ansi, capsys, name, prefix, reader, example
):
    assert deps("add", name, "uvicorn") == 1

    out = strip_ansi("".join(capsys.readouterr()))
    var = cli.normalize_name(name)
    keys = [f"{var.upper()}{suffix}" for suffix in ("_PATH", "_VENV", "_MODULE", "_LIB_PATH", "_ENTRY", "_PYTHON")]
    assert (
        f"✗ Not added: '{name}' becomes '{var}', and the tool would set {', '.join(keys[:-1])} and {keys[-1]} for "
        f"it, in .env and in its own environment. {reader} takes its settings from variables that start with "
        f"{prefix} ({example}), so these could change how {reader} runs in the tool. Pick another name.\n"
    ) in out
    assert declared(scaffolded_project) == ["uvicorn"]  # control: a name that only starts with uv is fine


def test_the_menu_refuses_uv_too(scaffolded_project, answers, strip_ansi, capsys):
    answers("uv", "")

    assert deps(None) == 1

    assert "'uv' becomes 'uv', and the tool would set UV_PATH" in strip_ansi("".join(capsys.readouterr()))
    assert declared(scaffolded_project) == []


@pytest.mark.parametrize("name, becomes", [("ünï", "n"), ("pépx", "p_px"), ("ü", None)])
def test_add_refuses_a_name_that_is_not_ascii(scaffolded_project, strip_ansi, capsys, name, becomes):
    assert deps("add", name, "pepx") == 1

    out = strip_ansi("".join(capsys.readouterr()))
    turned = f", and cli deps would turn it into '{becomes}'" if becomes else ""
    assert (
        f"✗ Not added: '{name}' has characters that are not plain ASCII{turned}. Use letters a-z, digits, "
        "'-' and '_' (e.g. 'netmhcpan').\n"
    ) in out
    assert declared(scaffolded_project) == ["pepx"]


# --- 6c. A paths.py written in other forms ---------------------------------------------------

OTHER_FORMS = """\
pepx_path: str = "/opt/pepx"
pepx_venv: str = "/opt/pepx/.venv"
if True:
    mhci_path = "/opt/mhci"
netmhcpan_path = other_path = None


def helper():
    local_path = "only a local name"
    return local_path
"""


def test_an_annotated_path_is_listed_kept_and_removed(scaffolded_project, strip_ansi, capsys):
    paths = scaffolded_project / "paths.py"
    paths.write_text(OTHER_FORMS)

    assert deps("list") == 0
    out = strip_ansi(capsys.readouterr().out)
    assert '  ● pepx                           pepx_path = "/opt/pepx"\n' in out

    assert deps("add", "pepx") == 0
    assert "⚠  Skipped (already declared): pepx" in strip_ansi(capsys.readouterr().out)
    assert paths.read_text() == OTHER_FORMS  # no second pepx_path = None below it

    assert deps("remove", "pepx") == 0
    content = paths.read_text()
    assert "pepx" not in content and "mhci_path" in content


def test_add_refuses_a_setting_set_in_a_form_cli_deps_does_not_read(scaffolded_project, strip_ansi, capsys):
    paths = scaffolded_project / "paths.py"
    paths.write_text(OTHER_FORMS)

    assert deps("add", "mhci", "netmhcpan", "local", "tcell") == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert (
        "✗ Not added: 'paths.py' already sets mhci_path on line 4, in a form cli deps does not read (inside an if, "
        "or a chained or unpacked assignment), and the mhci_path=None that cli deps adds below it would replace "
        "that value. Write it as a plain mhci_path = ... line at the top level of 'paths.py' (then cli deps lists "
        "it), or edit the file by hand.\n"
    ) in out
    assert "already sets netmhcpan_path on line 5" in out
    assert "already sets local_path" not in out  # a function's own name is not paths.py's
    assert sorted(cli._paths_dependencies(paths.read_text())) == ["local", "pepx", "tcell"]


def test_the_menu_refuses_a_setting_set_in_another_form_too(scaffolded_project, answers, strip_ansi, capsys):
    paths = scaffolded_project / "paths.py"
    paths.write_text(OTHER_FORMS)
    answers("1", "mhci", "")

    assert deps(None) == 1

    assert "already sets mhci_path on line 4" in strip_ansi(capsys.readouterr().err)
    assert paths.read_text() == OTHER_FORMS


# --- 6d. Line endings -------------------------------------------------------------------------


def test_a_crlf_paths_file_keeps_its_line_endings_and_byte_order_mark(scaffolded_project, capsys):
    paths = scaffolded_project / "paths.py"
    # Control: a file with LF line endings gets no CR.
    paths.write_bytes(b'pepx_path = "/opt/pepx"\n')
    assert deps("add", "mhci") == 0
    assert b"\r" not in paths.read_bytes() and b"mhci_path=None\n" in paths.read_bytes()

    paths.write_bytes((BOM + '# my tools\r\npepx_path = "/opt/pepx"\r\n').encode("utf-8"))

    assert deps("add", "mhci") == 0
    raw = paths.read_bytes()
    assert raw.startswith(BOM.encode("utf-8")) and b"mhci_path=None\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")

    assert deps("remove", "pepx") == 0
    raw, backup = paths.read_bytes(), (scaffolded_project / "paths.py.bak").read_bytes()
    assert raw.startswith(BOM.encode("utf-8")) and b"pepx" not in raw
    assert raw.count(b"\n") == raw.count(b"\r\n") and backup.count(b"\n") == backup.count(b"\r\n")
    assert declared(scaffolded_project) == ["mhci"]


def test_a_quoted_name_with_a_space_stays_one_dependency(scaffolded_project, strip_ansi, capsys):
    # Only commas split: "T Cell" is one deliberate name (title "T Cell", variable t_cell).
    assert deps("add", "T Cell") == 0

    assert declared(scaffolded_project) == ["t_cell"]
