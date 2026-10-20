"""`cli sync`'s help screens and known-clash check: the tool's .venv for an entry that is not a
Python script, a help screen that leaves a program running in the background, the help
screens of a subcommand the tool adds, a dry run of a clash the help screens decide, and
the advice for a -o clash.

An entry such as src/run.sh that runs python3 found the shell's python3, so sync said
"did not start before the sync either" while make build passed; the shell's VIRTUAL_ENV
reached the help screens too. A program a help screen left in the background held its
stderr pipe, so a screen that took 0.1 s timed out. A dry run exited 1 for a clash that a
real run lets the help screens rule out (exit 0). A -o clash was told to remove
--output-format. The test drive ran only the framework subcommands' help, so a subcommand
the tool adds (self.subparser.add_parser) whose help broke after the sync went unseen.
"""

import os
import re
import sys
import time

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
PARSER_FILE = "src/DemoArgumentParser.py"
ENTRY = "src/run_demo.py"
ORDER_LINE = "        self.set_subcommand_order(['predict', 'preprocess', 'postprocess'])\n"


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def make_stale(project):
    core = project / CORE_FILE
    core.write_text(core.read_text() + "\n# a local edit that sync will overwrite\n")


def set_entry(project, entry):
    pyproject = project / "pyproject.toml"
    pyproject.write_text(re.sub(r'(?m)^entry = ".*"$', f'entry = "{entry}"', pyproject.read_text()))


def give_venv(project):
    """A .venv whose python and python3 run the test interpreter (wrappers, not links)."""
    folder = project / ".venv" / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("python", "python3"):
        if (folder / name).is_symlink():  # never write through a link: it can lead to a shared interpreter
            (folder / name).unlink()
        (folder / name).write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        (folder / name).chmod(0o755)


def add_to_parser(project, code):
    parser = project / PARSER_FILE
    text = parser.read_text()
    assert ORDER_LINE in text
    parser.write_text(text.replace(ORDER_LINE, ORDER_LINE + code))


# --- 3. The tool's .venv for the help screens -------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="runs a #!/bin/sh entry")
def test_an_entry_that_runs_python3_finds_the_tools_venv(
    scaffolded_project, sync_args, strip_ansi, capsys, monkeypatch, tmp_path
):
    project = scaffolded_project
    give_venv(project)
    script = project / "src" / "run.sh"
    script.write_text('#!/bin/sh\nexec python3 "$(dirname "$0")/run_demo.py" "$@"\n')
    script.chmod(0o755)
    set_entry(project, "src/run.sh")
    # The shell's python3 cannot start the tool: it is a program that fails.
    shell_bin = tmp_path / "shell-bin"
    shell_bin.mkdir()
    (shell_bin / "python3").write_text("#!/bin/sh\necho 'not the tool python' >&2\nexit 3\n")
    (shell_bin / "python3").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shell_bin}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "another-venv"))
    make_stale(project)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "✓ Verified: every help screen that worked before the sync still works (4 of 4)." in out
    assert "Could not verify" not in out


def test_the_help_screens_get_the_tools_venv_not_the_shells(scaffolded_project, monkeypatch, tmp_path):
    project = scaffolded_project
    give_venv(project)
    seen = tmp_path / "env.txt"
    (project / ENTRY).write_text(
        "import os\n"
        f"open({str(seen)!r}, 'a').write(repr((os.environ.get('VIRTUAL_ENV'), "
        "os.environ['PATH'].split(os.pathsep)[0])) + '\\n')\n"
    )
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "the-shells-venv"))

    results = cli._smoke_check()["results"]

    assert len(results) == 4 and all(passed for passed, _error in results.values()), results
    venv, first_on_path = eval(seen.read_text().splitlines()[0])  # noqa: S307 (a tuple the script above wrote)
    assert os.path.realpath(venv) == os.path.realpath(project / ".venv")
    assert os.path.realpath(first_on_path) == os.path.realpath(project / ".venv" / "bin")


def gone(pid):
    """True when process `pid` has ended (a zombie waiting to be reaped counts as ended)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="reads /proc")
def test_a_program_left_running_in_the_background_does_not_hold_the_help_screen_open(
    scaffolded_project, monkeypatch, tmp_path
):
    project = scaffolded_project
    give_venv(project)
    pid_file = tmp_path / "child.pid"
    entry = project / ENTRY
    # The program inherits the help screen's stderr and outlives it.
    entry.write_text(
        "import subprocess\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n" + entry.read_text()
    )
    # Each screen ends with its own program, well inside the timeout; held open by the
    # program it leaves, the first one would time out and end the run of them.
    monkeypatch.setenv("NGARGPARSER_SYNC_HELP_TIMEOUT", "10")
    child = None
    try:
        results = cli._smoke_check()["results"]
        assert len(results) == 4 and all(passed for passed, _error in results.values()), results
        child = int(pid_file.read_text())
        deadline = time.monotonic() + 10
        while not gone(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert gone(child), "the program the help screen left is still running"
    finally:
        if child and not gone(child):
            os.kill(child, 9)


# --- 10. The help screens of a subcommand the tool adds ------------------------------------------

FRAMEWORK = ["predict", "preprocess", "postprocess"]
USAGE_39 = "usage: run_x.py [-h] {predict,preprocess,postprocess,list-alleles} ...\n\noptional arguments:\n"
# Python 3.14 colours the usage when FORCE_COLOR is set, even in a file.
USAGE_314 = (
    "\x1b[1;34musage: \x1b[0m\x1b[1;35mrun_x.py\x1b[0m [\x1b[32m-h\x1b[0m] "
    "\x1b[32m{predict,preprocess,postprocess,list-alleles,la} ...\x1b[0m\n\npositional arguments:\n"
)


@pytest.mark.parametrize(
    "help_text, subcommands",
    [
        (USAGE_39, ["predict", "preprocess", "postprocess", "list-alleles"]),
        (USAGE_314, ["predict", "preprocess", "postprocess", "list-alleles", "la"]),
        # The framework's come first, whatever order the usage lists them in.
        ("usage: r [-h] {list-alleles,postprocess,predict} ...\n", [*FRAMEWORK, "list-alleles"]),
        # The first group that holds a framework subcommand; one after the usage does not count.
        ("usage: r [-f {a,b}] {predict,x} ...\n\n  {predict,y}\n", [*FRAMEWORK, "x"]),
        ("usage: r [-h]\n\nno subcommands {here}\n", FRAMEWORK),
        ("", FRAMEWORK),  # a top-level --help that failed
    ],
)
def test_the_subcommands_are_read_from_the_usage_line(help_text, subcommands):
    assert cli._help_screen_subcommands(help_text) == subcommands


LIST_ALLELES = (
    '        lister = self.subparser.add_parser("list-alleles", help="List the alleles.")\n'
    '        lister.add_argument("--species", help="Only these species.")\n'
)


def add_custom_subcommand(project):
    add_to_parser(project, LIST_ALLELES)
    # Its help needs a name the tool added to a framework file, which the sync replaces.
    core = project / CORE_FILE
    core.write_text(core.read_text() + "\nLOCAL_THING = 1\n")
    entry = project / ENTRY
    entry.write_text(
        'import sys\nif "list-alleles" in sys.argv:\n    from core.NGArgumentParser import LOCAL_THING  # noqa: F401\n'
        + entry.read_text()
    )


def test_a_subcommand_the_tool_adds_whose_help_breaks_rolls_the_sync_back(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    give_venv(project)
    add_custom_subcommand(project)
    before = {path: path.read_bytes() for path in (project / CORE_FILE, project / PARSER_FILE, project / ENTRY)}

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert "✗ Sync rolled back: these commands worked before the sync and fail after it.\n" in out
    assert f"\n  {ENTRY} list-alleles --help\n      ImportError: cannot import name 'LOCAL_THING'" in out
    for command in ("--help", "predict --help", "preprocess --help", "postprocess --help"):
        assert f"\n  {ENTRY} {command}\n" not in out  # the framework's still pass
    assert {path: path.read_bytes() for path in before} == before  # put back as it was


def test_a_subcommand_the_tool_adds_that_still_works_is_counted(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    give_venv(project)
    add_to_parser(project, LIST_ALLELES)
    make_stale(project)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "✓ Verified: every help screen that worked before the sync still works (5 of 5)." in out


# --- 5. A dry run of a clash the help screens decide -------------------------------------------------

NEVER_RUNS = '        if False:\n            self.parser_predict.add_argument("-o", dest="never")\n'


def test_a_dry_run_of_an_up_to_date_tool_with_a_clash_that_never_runs_exits_0(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    give_venv(project)
    add_to_parser(project, NEVER_RUNS)

    real = cli.sync_command(sync_args())
    real_out = strip_ansi(capsys.readouterr().out)
    preview = cli.sync_command(sync_args(dry_run=True))
    preview_out = strip_ansi(capsys.readouterr().out)

    assert real == 0 and "ℹ Not a clash here: " in real_out
    assert preview == 0, preview_out  # as the real run
    assert "⚠ Sync may stop: this tool has code the current framework breaks." in preview_out
    assert "⚠ DRY RUN: a dry run runs no tool code, so it cannot tell whether the known clash" in preview_out
    assert "✓ DRY RUN: tool is already up to date; nothing would change." in preview_out


# --- 6d. The advice names the option that clashes ---------------------------------------------------


@pytest.mark.parametrize(
    "declared, option, other",
    [("-o", "--output-prefix", "--output-format"), ("-f", "--output-format", "--output-prefix")],
)
def test_the_clash_advice_names_the_option_that_clashes(
    scaffolded_project, sync_args, strip_ansi, capsys, declared, option, other
):
    add_to_parser(scaffolded_project, f'        self.parser_predict.add_argument("{declared}", dest="mine")\n')

    assert cli.sync_command(sync_args(dry_run=True)) == 1  # no .venv: a real run stops at the clash

    out = strip_ansi(capsys.readouterr().err)
    assert f'  command stop with "{declared} belongs to the framework ({option})".\n' in out
    assert f'  self.parser_predict.remove_argument("{option}") before your add_argument(...).\n' in out
    assert other not in out.split("Declaring either again", 1)[1].split("Then run `cli sync` again.", 1)[0]
