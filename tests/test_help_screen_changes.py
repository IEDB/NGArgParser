"""Help screens that change the files the build ships.

The build removed what the startup check's help screens made in the build
copy, but not what they changed: a `postprocess --help` that rewrote
$APP_ROOT/README ended in "Build completed", and the tarball's README read
"clobbered by help". The build now records each path in the build copy
before the help screens run, and fails naming each file they changed or
removed.
"""

import shutil

import pytest
from test_help_screens import in_the_build_copy

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)

MESSAGE = (
    "ERROR: The startup check (the tool's help screens) changed or removed files in the build copy, so they "
    "would ship that way. Stop the tool from changing its own files when it starts:\n"
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)
    monkeypatch.delenv("NGARGPARSER_STARTUP_CHECK_TIMEOUT", raising=False)


def on_postprocess_help(code):
    """`code` for the postprocess --help screen only, as Python lines."""
    return "if _sys.argv[1:] == ['postprocess', '--help']:\n" + "".join(f"    {line}\n" for line in code.splitlines())


def test_a_help_screen_that_rewrites_a_shipped_file_fails_the_build(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    in_the_build_copy(
        scaffolded_project,
        on_postprocess_help(
            "open(_os.path.join(_os.environ['APP_ROOT'], 'README'), 'w').write('clobbered by help\\n')"
        ),
    )

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert MESSAGE + "  README: changed\n" in run.proc.stderr
    assert "Build completed" not in run.proc.stdout
    assert not run.tarball.exists()


def test_a_change_that_keeps_size_and_time_is_found(scaffolded_project, run_build, fake_venv):
    # Same size, and the modification time set back: the status change time still moves.
    fake_venv(scaffolded_project)
    (scaffolded_project / "data.txt").write_text("aaaa\n")
    in_the_build_copy(
        scaffolded_project,
        on_postprocess_help(
            "_p = _os.path.join(_os.environ['APP_ROOT'], 'data.txt')\n"
            "_st = _os.stat(_p)\n"
            "open(_p, 'w').write('bbbb\\n')\n"
            "_os.utime(_p, ns=(_st.st_atime_ns, _st.st_mtime_ns))"
        ),
    )

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert MESSAGE + "  data.txt: changed\n" in run.proc.stderr


def test_removed_and_chmodded_files_are_named(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)
    (scaffolded_project / "data" / "sub").mkdir(parents=True)
    (scaffolded_project / "data" / "sub" / "a.txt").write_text("a\n")
    (scaffolded_project / "data" / "sub" / "b.txt").write_text("b\n")
    (scaffolded_project / "notes.txt").write_text("n\n")
    in_the_build_copy(
        scaffolded_project,
        on_postprocess_help(
            "import shutil as _sh\n"
            "_root = _os.environ['APP_ROOT']\n"
            "_sh.rmtree(_os.path.join(_root, 'data', 'sub'))\n"
            "_os.chmod(_os.path.join(_root, 'notes.txt'), 0o600)"
        ),
    )

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    # The top of a removed tree only.
    assert MESSAGE + "  data/sub: removed\n  notes.txt: changed\n" in run.proc.stderr
    assert "data/sub/a.txt" not in run.proc.stderr


def test_what_a_help_screen_only_reads_or_makes_still_builds(scaffolded_project, run_build, fake_venv):
    # Control: a help screen that reads the tool's files, or writes a new log
    # into the build copy (removed, with a notice), still builds.
    fake_venv(scaffolded_project)
    in_the_build_copy(
        scaffolded_project,
        "_root = _os.environ['APP_ROOT']\n"
        "open(_os.path.join(_root, 'README')).read()\n"
        "open(_os.path.join(_root, 'src', 'startup.log'), 'a').write('started\\n')",
    )

    run = run_build(scaffolded_project, progress=True)

    assert "changed or removed files" not in run.proc.stderr
    assert "  src/startup.log\n" in run.proc.stdout
    assert "src/startup.log" not in run.members
