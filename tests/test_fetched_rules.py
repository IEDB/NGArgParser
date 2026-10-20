""".distignore rules that match what scripts/get_libs.sh fetched.

Fetched entries ship as get_libs.sh left them: .distignore does not filter
them (the build's design, and what the .distignore template says). A rule
such as '*.bin' or '/libs/mylib/tests/' left libs/mylib/tests/big_test_data.bin
in the tarball without a word. The build now prints one notice per such rule.
"""

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


def write_get_libs(project_dir, body):
    (project_dir / "scripts" / "get_libs.sh").write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n")


def add_rules(project_dir, *rules):
    with open(project_dir / ".distignore", "a") as f:
        f.write("\n" + "\n".join(rules) + "\n")


FETCH = (
    "mkdir -p mylib/tests mylib/pkg/__pycache__\n"
    "echo b > mylib/tests/big_test_data.bin\n"
    "echo c > mylib/pkg/x.bin\n"
    "echo d > mylib/pkg/m.py\n"
    "touch mylib/.hidden mylib/pkg/__pycache__/m.pyc\n"
    "mkdir other && echo e > other/o.bin"
)


def notice(rule, path, more=0):
    extra = f" (and {more} more)" if more else ""
    return (
        f"Notice: .distignore rule '{rule}' has no effect on {path}{extra}: what scripts/get_libs.sh fetched "
        "ships as it left it. Delete what should not ship in scripts/get_libs.sh.\n"
    )


def test_a_rule_that_matches_a_fetched_entry_gets_a_notice(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, FETCH)
    (scaffolded_project / "data").mkdir()
    (scaffolded_project / "data" / "own.bin").write_text("x\n")
    # __pycache__/ matches in the fetched entry too, but the build strips those anyway.
    add_rules(scaffolded_project, "*.bin", "/libs/mylib/tests/", "__pycache__/")

    run = run_build(scaffolded_project, progress=True)

    out = run.proc.stdout
    # The top of an excluded subtree, then the rule's other matches as a count.
    assert notice("/libs/mylib/tests/", "libs/mylib/tests") in out
    assert notice("*.bin", "libs/mylib/pkg/x.bin", more=1) in out
    assert out.count("has no effect on libs/") == 2, out
    # The built-in '.*' is not the user's rule.
    assert ".hidden" not in out
    # The design is unchanged: fetched entries ship as fetched, the tool's own file does not.
    assert {"libs/mylib/tests/big_test_data.bin", "libs/mylib/pkg/x.bin", "libs/other/o.bin"} <= set(run.members)
    assert "data/own.bin" not in run.members


def test_a_rule_that_excludes_libs_names_each_fetched_entry(scaffolded_project, run_build):
    write_get_libs(scaffolded_project, "mkdir a b && touch a/x.py b/y.py")
    (scaffolded_project / "libs").mkdir()
    (scaffolded_project / "libs" / "own.py").write_text("x = 1\n")
    add_rules(scaffolded_project, "/libs/")

    run = run_build(scaffolded_project, progress=True)

    assert notice("/libs/", "libs/a", more=1) in run.proc.stdout
    assert {"libs/a/x.py", "libs/b/y.py"} <= set(run.members)
    assert "libs/own.py" not in run.members


def test_rules_that_match_no_fetched_entry_print_nothing(scaffolded_project, run_build):
    # Controls: a rule on the tool's own files, a negation, and no .distignore.
    write_get_libs(scaffolded_project, FETCH)
    add_rules(scaffolded_project, "/data/", "!libs/other/o.bin")

    run = run_build(scaffolded_project, progress=True)
    assert "has no effect on libs/" not in run.proc.stdout

    (scaffolded_project / ".distignore").unlink()
    run = run_build(scaffolded_project, progress=True)
    assert "has no effect on libs/" not in run.proc.stdout
