"""Build messages that named the wrong cause.

A README that is a link to README.md (which the default .distignore excludes)
was reported as ".distignore excludes them ... README: the deploy publishes
it", though no rule names README. A link to a folder outside the tool folder
(ext -> ../outside_dir) was reported as "(dangling or a loop)". A startup
check timeout of 9999999999 seconds passed the preflight, then failed the
check with no reason shown: the preflight now takes at most 86400, as cli
sync does.
"""

import os
import shutil

import pytest

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


def test_a_readme_link_to_an_excluded_file_names_the_link(scaffolded_project, run_build):
    readme = scaffolded_project / "README"
    (scaffolded_project / "README.md").write_text(readme.read_text())
    readme.unlink()
    readme.symlink_to("README.md")
    assert "/README.md" in (scaffolded_project / ".distignore").read_text().splitlines()

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        "ERROR: README is a link to README.md, which .distignore (or the hidden-file rule) excludes, so the build "
        "copy has no README (the deploy publishes it): replace the link with the file it points at, or remove the "
        "rule that excludes README.md.\n"
    ) in run.proc.stderr
    assert "because .distignore excludes them" not in run.proc.stderr
    assert not run.tarball.exists()

    # The first fix it names works: the file in place of the link.
    readme.unlink()
    shutil.copy(scaffolded_project / "README.md", readme)
    run_build(scaffolded_project)


def test_a_readme_link_outside_the_tool_folder_says_so(scaffolded_project, run_build):
    shared = scaffolded_project.parent / "shared-README"
    readme = scaffolded_project / "README"
    shared.write_text(readme.read_text())
    readme.unlink()
    readme.symlink_to("../shared-README")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert (
        "ERROR: README is a link to ../shared-README, outside the tool folder, so the build copy has no README "
        "(the deploy publishes it): replace the link with the file it points at.\n"
    ) in run.proc.stderr
    assert "because .distignore excludes them" not in run.proc.stderr


def test_a_link_out_of_the_tool_folder_is_not_called_dangling(scaffolded_project, run_build):
    outside = scaffolded_project.parent / "outside_dir"
    outside.mkdir()
    (outside / "data.txt").write_text("x\n")
    (scaffolded_project / "ext").symlink_to("../outside_dir")
    (scaffolded_project / "notes.txt").symlink_to("../outside_dir/data.txt")
    (scaffolded_project / "gone").symlink_to("../nothing_there")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    err = run.proc.stderr
    assert "  ext -> ../outside_dir (points outside the tool folder)\n" in err
    assert "  notes.txt -> ../outside_dir/data.txt (points outside the tool folder)\n" in err
    # A link that dangles in the tool folder too still says so.
    assert "  gone -> ../nothing_there (dangling or a loop)\n" in err
    # The rule to keep the folder link out names it without a trailing slash.
    assert "ext is a link to a folder, which git treats as a file: a .distignore rule must name it" in err

    # Control: the rule it names keeps the links out, and the build passes.
    with open(scaffolded_project / ".distignore", "a") as f:
        f.write("\n/ext\n/notes.txt\n/gone\n")
    run = run_build(scaffolded_project)
    assert not {"ext", "notes.txt", "gone"} & set(run.members)


@pytest.mark.parametrize("value", ["9999999999", "86401", "86400.5", "99999999999999999999999"])
def test_a_timeout_over_a_day_stops_the_preflight(scaffolded_project, run_build, fake_venv, value):
    fake_venv(scaffolded_project)
    first = run_build(scaffolded_project)

    run = run_build(
        scaffolded_project, expect_ok=False, progress=True, env={"NGARGPARSER_STARTUP_CHECK_TIMEOUT": value}
    )

    assert run.proc.returncode == 1
    assert (
        f"ERROR: NGARGPARSER_STARTUP_CHECK_TIMEOUT={value} is not a number of seconds above 0 and at most 86400: "
        "set it to the seconds each help screen of the startup check may take, such as 120, or unset it for 60.\n"
    ) in run.proc.stderr
    assert run.proc.stderr.count("ERROR") == 1
    assert first.tarball.is_file() and first.staged.is_dir(), "a preflight stop removes nothing"


@pytest.mark.parametrize("value", ["86400", "86400.0", "0086400", "30.5"])
def test_a_timeout_up_to_a_day_is_taken(scaffolded_project, run_build, fake_venv, value):
    fake_venv(scaffolded_project)

    run = run_build(scaffolded_project, env={"NGARGPARSER_STARTUP_CHECK_TIMEOUT": value})

    assert "Startup check passed." in run.proc.stdout
    assert os.path.isfile(run.tarball)
