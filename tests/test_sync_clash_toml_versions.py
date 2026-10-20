"""`cli sync`: a known clash the tool runs past, pyproject.toml that is not plain TOML,
version stamps in any PEP 440 form, and the entry script of a tool with several.

"Nothing changed" is checked against a hash of every file in the project, as in
test_sync_safety.py.
"""

import hashlib
import re
import stat
import sys

import pytest

from ngargparser import cli


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # These tests run from a git checkout, whose generate and sync stamp <version>+g<sha>
    # (see test_sync_checkout_stamps.py). Stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


CORE_FILE = "src/core/NGArgumentParser.py"
PARSER_FILE = "src/DemoArgumentParser.py"
VERSION = cli.__version__


def _has_toml_parser():
    try:
        import tomllib  # noqa: F401
    except ImportError:
        try:
            import tomli  # noqa: F401
        except ImportError:
            return False
    return True


needs_toml = pytest.mark.skipif(not _has_toml_parser(), reason="needs tomllib or tomli")


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


def make_stale(project):
    core = project / CORE_FILE
    pristine = core.read_text()
    core.write_text(pristine + "\n# a local edit that sync will overwrite\n")
    return pristine


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


def stamp(project, version):
    cli.write_scaffold_version(str(project / "pyproject.toml"), version)


# --- 4. A known clash the tool's own help screens prove false ---------------------------

# The removal sits in a helper method defined below __init__, so it comes later in the
# file than the redeclaration, but __init__ calls it first: the tool runs fine.
REMOVAL_IN_A_HELPER_BELOW = """
        self._free_output_format()
        self.parser_predict.add_argument("--output-format", "-f", dest="output_format", default="json")

    def _free_output_format(self):
        self.remove_argument("--output-format", "predict")
"""


def test_a_clash_the_help_screens_disprove_does_not_stop_a_synced_tool(
    runnable_project, sync_args, strip_ansi, capsys, monkeypatch
):
    parser = runnable_project / PARSER_FILE
    parser.write_text(parser.read_text() + REMOVAL_IN_A_HELPER_BELOW)
    assert [f["what"] for f in cli._clash_predict_output_options()] == ["declares --output-format on predict"]
    pristine = make_stale(runnable_project)

    # Stamped before 0.3.0, the stamp proves nothing, but every help screen passes before the
    # sync: it goes ahead, and the help screens after it decide that the finding is false.
    stamp(runnable_project, "0.2.2")
    assert cli.sync_command(sync_args()) == 0
    out = strip_ansi("".join(capsys.readouterr()))
    assert "Sync stopped" not in out
    assert f"ℹ Possible known clash (ngargparser 0.3.0 and later): {PARSER_FILE}:" in out
    assert f"ℹ Not a clash here: {PARSER_FILE}:" in out and "every help screen passes after the sync." in out
    assert "Verified: every help screen that worked before the sync still works (4 of 4)" in out
    assert (runnable_project / CORE_FILE).read_text() == pristine
    make_stale(runnable_project)

    # Control: stamped on this version, but a help screen fails before the sync; the finding stands.
    stamp(runnable_project, VERSION)
    real_check = cli._smoke_check

    def help_fails():
        result = real_check()
        result["results"]["src/run_demo.py --help"] = (False, "boom")
        return result

    monkeypatch.setattr(cli, "_smoke_check", help_fails)
    before = tree_state(runnable_project)
    assert cli.sync_command(sync_args()) == 1
    out = strip_ansi("".join(capsys.readouterr()))
    assert "Sync stopped" in out
    assert "The tool's help screens did not rule it out: src/run_demo.py --help fails before the sync (boom)." in out
    assert tree_state(runnable_project) == before
    monkeypatch.setattr(cli, "_smoke_check", real_check)

    # Stamped on this version and every help screen passes: the tool runs with the change.
    assert cli.sync_command(sync_args()) == 0
    out = strip_ansi("".join(capsys.readouterr()))
    assert "Sync stopped" not in out
    assert f"Not a clash here: {PARSER_FILE}:" in out and "declares --output-format on predict" in out
    assert "Verified: every help screen that worked before the sync still works (4 of 4)" in out
    assert (runnable_project / CORE_FILE).read_text() == pristine

    # A dry run runs no project code, so it keeps the finding, says a real run may go ahead, and
    # does not fail for it: the real run lets the help screens decide.
    make_stale(runnable_project)
    assert cli.sync_command(sync_args(dry_run=True)) == 0
    preview = strip_ansi("".join(capsys.readouterr()))
    assert "Sync may stop" in preview
    assert "A real run first runs the tool's help screens: if one fails, it stops here and changes nothing" in preview


# --- 5. pyproject.toml with a byte order mark, or not TOML at all ---------------------------


@needs_toml
def test_a_pyproject_with_a_byte_order_mark_is_synced_and_keeps_it(scaffolded_project, sync_args, strip_ansi, capsys):
    pyproject = scaffolded_project / "pyproject.toml"
    stamp(scaffolded_project, "0.3.0")
    pyproject.write_bytes(b"\xef\xbb\xbf" + pyproject.read_bytes())

    assert cli.sync_command(sync_args(dry_run=True)) == 0
    assert cli.sync_command(sync_args()) == 0

    out = strip_ansi(capsys.readouterr().out)
    assert f"scaffold_version: 0.3.0 → {VERSION}" in out and "Error" not in out
    raw = pyproject.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and not raw.startswith(b"\xef\xbb\xbf\xef\xbb\xbf")
    assert cli._project_scaffold_version() == VERSION


@needs_toml
def test_a_byte_order_mark_before_the_ngargparser_table_still_marks_a_tool(in_tmp_dir):
    (in_tmp_dir / "pyproject.toml").write_bytes(b'\xef\xbb\xbf[tool.ngargparser]\napp_name = "x"\n')
    assert cli._is_ngargparser_tool()


@needs_toml
def test_a_pyproject_that_is_not_toml_is_named_as_the_problem(scaffolded_project, sync_args, strip_ansi, capsys):
    stamp(scaffolded_project, "0.3.0")
    pyproject = scaffolded_project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "oops = = 1\n")
    make_stale(scaffolded_project)
    before = tree_state(scaffolded_project)

    for args in (sync_args(dry_run=True), sync_args()):
        assert cli.sync_command(args) == 1
        out = strip_ansi(capsys.readouterr().err)
        assert re.search(
            r"✗ pyproject\.toml is not valid TOML: .*\(at line \d+, column \d+\); fix it, then run cli sync again\.",
            out,
        ), out
        assert "version stamp" not in out and "Error during synchronization" not in out
        assert tree_state(scaffolded_project) == before


# --- 6. The downgrade guard reads every PEP 440 form ------------------------------------------

_major, _minor, _patch = (int(n) for n in re.match(r"(\d+)\.(\d+)\.(\d+)", VERSION).groups())


@pytest.mark.parametrize(
    "newer, not_newer",
    [
        (f"{_major}.{_minor + 1}", f"{_major}.{_minor}"),  # two release segments: 0.5 / 0.4
        (f"{_major}.{_minor}.{_patch + 1}.dev1", f"{VERSION}.dev1"),  # a dev release of the next / this version
        (f"{VERSION}.post1", f"{VERSION}rc1"),  # a post release is newer, a release candidate is not
        (f"{_major + 1}", f"{_major}.{_minor}.{_patch}.0"),  # one segment / four segments
    ],
)
def test_a_newer_stamp_in_any_pep440_form_is_refused(
    scaffolded_project, sync_args, strip_ansi, capsys, newer, not_newer
):
    stamp(scaffolded_project, not_newer)
    assert cli.sync_command(sync_args(dry_run=True)) == 0  # control: not newer, not refused
    assert "newer than the installed" not in strip_ansi(capsys.readouterr().err)

    stamp(scaffolded_project, newer)
    make_stale(scaffolded_project)
    before = tree_state(scaffolded_project)

    assert cli.sync_command(sync_args()) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert f"last synced with ngargparser {newer}, which is newer than the installed ngargparser {VERSION}" in out
    assert tree_state(scaffolded_project) == before


# --- 7. [tool.ngargparser] entry names the entry script --------------------------------------


def test_the_entry_key_picks_the_script_the_test_drive_runs(runnable_project, sync_args, strip_ansi, capsys):
    # With several run_*.py scripts, sync used to run none of them. The other one here
    # would leave a marker file behind if it ran.
    marker = runnable_project / "side-effect-ran"
    (runnable_project / "src/run_cleanup.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    pyproject = str(runnable_project / "pyproject.toml")

    cli.write_ngargparser_key(pyproject, "entry", "src/run_missing.py")
    pristine = make_stale(runnable_project)
    stale = (runnable_project / CORE_FILE).read_text()
    assert cli.sync_command(sync_args()) == 1  # the build refuses this entry too, so nothing could check the update
    out = strip_ansi("".join(capsys.readouterr()))
    assert (
        '✗ Sync cannot check that the tool still starts after the update: entry = "src/run_missing.py" under '
        "[tool.ngargparser] in pyproject.toml names a file that does not exist. Fix it, then run cli sync again "
        "(or cli sync --no-verify to update without the check). Nothing was changed."
    ) in out
    assert (runnable_project / CORE_FILE).read_text() == stale

    cli.write_ngargparser_key(pyproject, "entry", "src/run_demo.py")
    assert cli.sync_command(sync_args()) == 0
    out = strip_ansi("".join(capsys.readouterr()))
    assert "Verified: every help screen that worked before the sync still works (4 of 4)" in out
    assert (runnable_project / CORE_FILE).read_text() == pristine
    assert not marker.exists()
