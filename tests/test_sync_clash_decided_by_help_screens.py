"""`cli sync`: a known-clash finding that the tool's own help screens decide.

The known-clash check reads the code, so it can be wrong: a declaration under
`if False:` is a finding, and a tool with no stamp was stopped by it, with
--no-verify (which drops the test drive too) as the only way on. When every help
screen passes before the sync, a real run now goes ahead and lets the test drive
after the update decide: if they still pass, the finding was false and the sync
stands; if one fails, the sync rolls back and names the clash as the likely cause.

"Nothing changed" is checked against a hash of every file in the project, as in
test_sync_safety.py.
"""

import hashlib
import stat
import sys

import pytest

from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"
PARSER_FILE = "src/DemoArgumentParser.py"
ORDER_LINE = "        self.set_subcommand_order(['predict', 'preprocess', 'postprocess'])\n"
NEVER_RUNS = '        if False:\n            self.parser_predict.add_argument("-f", dest="never")\n'
FASTA = '        self.parser_predict.add_argument("-f", "--fasta", dest="fasta", help="a FASTA file")\n'


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # The tests run from a git checkout; stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    """A scaffolded tool whose test drive runs on the test interpreter, with no stamp: one that
    no sync has stamped, so the stamp shows nothing about its framework files."""
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(
        "".join(line for line in pyproject.read_text().splitlines(True) if "scaffold_version" not in line)
    )
    return scaffolded_project


def tree_state(root):
    """Every file (content hash + mode) and every directory under `root`."""
    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative:
            continue
        if path.is_dir():
            dirs.add(relative)
        else:
            files[relative] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    return files, dirs


def add_to_parser(project, code):
    """Add `code` to the parser's __init__, below its set_subcommand_order line; return its first line."""
    parser = project / PARSER_FILE
    text = parser.read_text()
    assert ORDER_LINE in text
    parser.write_text(text.replace(ORDER_LINE, ORDER_LINE + code))
    return text[: text.index(ORDER_LINE)].count("\n") + 2


def make_stale(project):
    core = project / CORE_FILE
    pristine = core.read_text()
    core.write_text(pristine + "\n# a local edit that sync will overwrite\n")
    return pristine


def test_a_finding_the_help_screens_disprove_lets_the_sync_go_ahead(runnable_project, sync_args, strip_ansi, capsys):
    line = add_to_parser(runnable_project, NEVER_RUNS) + 1
    assert [(f["first"], f["what"]) for f in cli._clash_predict_output_options()] == [(line, "declares -f on predict")]
    pristine = make_stale(runnable_project)

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "Sync stopped" not in out
    assert (
        f"ℹ Possible known clash (ngargparser 0.3.0 and later): {PARSER_FILE}:{line} declares -f on predict.\n"
        "  Every help screen passes before the sync, so sync goes ahead, runs them again after the update, and "
        "rolls back if one fails then.\n"
    ) in out
    assert (
        f"ℹ Not a clash here: {PARSER_FILE}:{line} declares -f on predict, and every help screen passes after the sync."
    ) in out
    assert "Verified: every help screen that worked before the sync still works (4 of 4)" in out
    assert (runnable_project / CORE_FILE).read_text() == pristine


def test_a_finding_in_a_tool_with_this_versions_framework_files_is_dropped(
    runnable_project, sync_args, strip_ansi, capsys
):
    # Nothing to change in the framework files: the tool already runs on this version's.
    line = add_to_parser(runnable_project, NEVER_RUNS) + 1

    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert "Sync stopped" not in out and "Possible known clash" not in out
    assert (
        f"ℹ Not a clash here: {PARSER_FILE}:{line} declares -f on predict, and every help screen passes on this "
        "tool's framework files, which are this version's already."
    ) in out


def test_a_clash_that_breaks_the_tool_after_the_update_is_rolled_back_and_named(
    runnable_project, sync_args, strip_ansi, capsys
):
    # Framework files from before the framework took -f for --output-format: the tool's own -f works.
    core = runnable_project / CORE_FILE
    framework_option = 'self.parser_predict.add_argument("--output-format", "-f",'
    assert framework_option in core.read_text()
    core.write_text(core.read_text().replace(framework_option, 'self.parser_predict.add_argument("--output-format",'))
    line = add_to_parser(runnable_project, FASTA)
    passed, error = cli._smoke_check()["results"]["src/run_demo.py predict --help"]
    assert passed, error  # it runs before the sync
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args())

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert "Sync stopped" not in out
    assert "✗ Sync rolled back: these commands worked before the sync and fail after it.\n" in out
    assert "-f belongs to the framework (--output-format)" in out  # the error the tool raised
    rollback = out[out.index("Sync rolled back") :]
    assert (
        "\n  The likely cause is the known clash found before the sync:\n\n"
        f"  {PARSER_FILE}:{line}   declares -f on predict\n\n"
        "  Since 0.3.0 the framework adds --output-prefix/-o and --output-format/-f to predict.\n"
    ) in rollback
    assert "  Every file was restored to its state before the sync.\n" in rollback
    assert tree_state(runnable_project) == before


def test_a_dry_run_says_a_real_run_lets_the_help_screens_decide(runnable_project, sync_args, strip_ansi, capsys):
    add_to_parser(runnable_project, NEVER_RUNS)
    make_stale(runnable_project)
    before = tree_state(runnable_project)

    rc = cli.sync_command(sync_args(dry_run=True))

    out = strip_ansi(capsys.readouterr().out)
    # It cannot run the help screens, so it cannot tell: it says so, and does not fail for it.
    assert rc == 0
    assert "⚠ Sync may stop: this tool has code the current framework breaks." in out
    assert "Sync would stop" not in out and "A real run stops here and changes nothing" not in out
    assert (
        "  The check reads the code, so a finding can be false, and a dry run runs no tool code to tell. A real "
        "run first runs the tool's help screens: if one fails, it stops here and changes nothing; if every one "
        "passes, it goes ahead, and rolls back if one fails after the update. To sync without these checks: "
        "cli sync --no-verify\n"
    ) in out
    assert (
        "⚠ DRY RUN: a dry run runs no tool code, so it cannot tell whether the known clash reported above is "
        "real. A real run first runs the help screens: if every one passes, it goes ahead; if one fails, it stops "
        "at the clash and changes nothing.\n"
    ) in out
    assert f"Would update {CORE_FILE}" in out
    assert tree_state(runnable_project) == before
