"""Behavioral tests for the build.sh do-not-distribute engine.

These run the real scripts/core/build.sh in a scaffolded project and inspect
both the staged build directory and the produced tarball. The exclusion file
(.distignore at the project root) has exact .gitignore semantics (evaluated via
`git check-ignore`), with a built-in `.*` baseline (hidden files excluded unless
re-included with `!`) and force-included deploy-contract files (README,
deploy/install.sh). The old exclusion file names fail the build.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh exclusion tests require bash and git",
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check.
    These tests expect a local build unless they set CI themselves."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


# The legacy do-not-distribute.txt template, from before .distignore.
LEGACY_LIST = """\
# list of files to remove from the distributed package
.env
.gitmodules
.gitignore
.gitlab-ci.yml
.git/*
.python-version
.git/
__pycache__/*
__pycache__
build.sh
build.md
build/*
build/
do-not-distribute.txt
Makefile
**.sh
"""


def quiet_uv(tmp_path):
    """The environment for a build with a stand-in uv first on PATH, which
    prints nothing and exits 0 for every command."""
    bin_dir = tmp_path / "quiet-uv"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "uv").chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def append_rules(project_dir, *lines):
    # New scaffolds carry the exclusion file at the project root as .distignore.
    dnd = project_dir / ".distignore"
    dnd.write_text(dnd.read_text() + "\n".join(lines) + "\n")


def test_default_template_exclusions(scaffolded_project, run_build):
    (scaffolded_project / "scripts" / "hooks.sh").write_text("true\n")
    (scaffolded_project / "setup_netmhcpan_env.sh").write_text("export X=1\n")  # ./configure writes these
    # cli generate may already write a tests/ folder.
    (scaffolded_project / "tests").mkdir(exist_ok=True)
    (scaffolded_project / "tests" / "test_demo.py").write_text("def test_x():\n    pass\n")

    members = run_build(scaffolded_project).members

    for absent in (
        "Makefile",
        "scripts/hooks.sh",
        "scripts/get_libs.sh",
        "scripts/core/build.sh",
        "scripts/core/build.conf",  # read by build.sh only
        "setup_netmhcpan_env.sh",
        "tests/test_demo.py",
        ".distignore",  # dotfile at root, auto-excluded by the '.*' baseline
        "README.md",  # the developer notes; users get README
    ):
        assert absent not in members, f"{absent} must not ship"
    assert not any(m == "scripts" or m.startswith("scripts/") for m in members)

    shell_scripts = {m for m in members if m.endswith(".sh")}
    assert shell_scripts == {"deploy/install.sh"}, f"only the deploy hook may ship as *.sh, got: {shell_scripts}"

    for present in (
        "deploy/install.sh",
        "configure",
        "VERSION",
        "README",
        "pyproject.toml",
        "src/core/NGArgumentParser.py",
    ):
        assert present in members, f"{present} missing from tarball"


def test_default_rules_keep_the_tools_own_build_names(scaffolded_project, run_build):
    # The default rules name the framework's files at the top only, so a tool's
    # own shell script, Makefile or tests folder deeper down ships.
    own = [
        "src/tools/run_netmhc.sh",
        "libs/mylib/Makefile",
        "deploy/post_install.sh",
        "src/tests/fixture.txt",
        "data/scripts/convert.py",
    ]
    for rel in own:
        path = scaffolded_project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")

    members = run_build(scaffolded_project).members

    for rel in own:
        assert rel in members, f"{rel} must ship"
    assert "Makefile" not in members and "scripts/core/build.sh" not in members  # control: the top-level ones do not


def test_hidden_files_excluded_by_default(scaffolded_project, run_build):
    (scaffolded_project / ".secret").write_text("token=abc\n")
    (scaffolded_project / "src" / ".hidden.cfg").write_text("x\n")
    data = scaffolded_project / "data"
    data.mkdir()
    (data / ".DS_Store").write_text("junk")
    (data / "keep.txt").write_text("keep\n")

    members = run_build(scaffolded_project).members

    for absent in (".secret", ".env", ".gitignore", "src/.hidden.cfg", "data/.DS_Store"):
        assert absent not in members, f"hidden path {absent} must not ship"
    assert "data/keep.txt" in members


def test_negation_reincludes_hidden(scaffolded_project, run_build):
    streamlit = scaffolded_project / ".streamlit"
    streamlit.mkdir()
    (streamlit / "config.toml").write_text("[server]\n")
    append_rules(scaffolded_project, "!.env", "!.streamlit/")

    members = run_build(scaffolded_project).members

    assert ".env" in members
    assert ".streamlit/config.toml" in members
    assert ".gitignore" not in members


def test_nested_exclusions(scaffolded_project, run_build):
    deep = scaffolded_project / "src" / "deep"
    (deep / "__pycache__").mkdir(parents=True)
    (deep / "mod.py").write_text("x = 1\n")
    (deep / "__pycache__" / "m.pyc").write_bytes(b"\x00")
    logs = scaffolded_project / "data" / "logs"
    logs.mkdir(parents=True)
    (logs / "app.log").write_text("log\n")
    (logs / "notes.txt").write_text("notes\n")
    append_rules(scaffolded_project, "*.log")

    members = run_build(scaffolded_project).members

    assert "src/deep/mod.py" in members
    assert "src/deep/__pycache__/m.pyc" not in members
    assert "data/logs/app.log" not in members
    assert "data/logs/notes.txt" in members


@pytest.mark.parametrize("legacy", ["scripts/do-not-distribute.txt", "do-not-distribute.txt", "scripts/.distignore"])
def test_legacy_exclusion_file_fails_build(scaffolded_project, run_build, legacy):
    # An old project: no root .distignore, only a file the build no longer reads.
    # Ignoring it would silently ship everything it used to exclude.
    (scaffolded_project / ".distignore").unlink()
    (scaffolded_project / legacy).write_text(LEGACY_LIST)

    run = run_build(scaffolded_project, expect_ok=False)

    assert run.proc.returncode != 0
    assert f"{legacy} is no longer read" in run.proc.stderr
    assert ".distignore" in run.proc.stderr
    assert not run.tarball.exists()


def test_legacy_exclusion_file_ignored_next_to_distignore(scaffolded_project, run_build):
    # Both files present: the root .distignore applies, the legacy file is ignored.
    (scaffolded_project / "alpha.txt").write_text("a\n")
    (scaffolded_project / "beta.txt").write_text("b\n")
    (scaffolded_project / ".distignore").write_text("alpha.txt\n")
    (scaffolded_project / "scripts" / "do-not-distribute.txt").write_text("beta.txt\n")

    members = run_build(scaffolded_project).members

    assert "alpha.txt" not in members, "root .distignore rule must apply"
    assert "beta.txt" in members, "legacy do-not-distribute.txt must be ignored when .distignore exists"


def test_contract_files_cannot_be_excluded(scaffolded_project, run_build):
    (scaffolded_project / "README").write_text("TOOL_VERSION\n")
    append_rules(scaffolded_project, "README", "deploy/", "deploy/install.sh")

    members = run_build(scaffolded_project).members

    assert "README" in members
    assert "deploy/install.sh" in members


def test_exclusion_file_without_trailing_newline(scaffolded_project, run_build):
    # Regression: an exclusion file whose LAST line has no trailing newline must
    # not fuse with the forced contract rules build.sh appends after it. Before the
    # fix, the composed pattern became "*.json!README" (a dead rule) and nothing was
    # excluded. Note: .write_text("*.json") deliberately writes NO trailing newline.
    (scaffolded_project / "top.json").write_text("{}\n")
    data = scaffolded_project / "data"
    data.mkdir()
    (data / "x.json").write_text("{}\n")
    (scaffolded_project / ".distignore").write_text("*.json")

    members = run_build(scaffolded_project).members

    assert "top.json" not in members, "top-level *.json must be excluded"
    assert "data/x.json" not in members, "nested *.json must be excluded"
    # The forced deploy-contract re-include must survive (not absorbed by the fusion).
    assert "deploy/install.sh" in members
    assert "README.md" in members


@pytest.mark.skipif(os.name != "posix", reason="these names are not valid on Windows")
def test_names_git_would_quote_are_excluded(scaffolded_project, run_build):
    # Regression: git check-ignore printed names holding '"', '\' or a tab in
    # C-quoted form, which never matched the real path, so they shipped.
    quoted = ['a"b.key', "back\\slash.key", "tab\tthere.key"]
    weird = scaffolded_project / "weird"
    weird.mkdir()
    for name in quoted + ["plain.key", "keep.txt"]:
        (weird / name).write_text("x\n")
    (weird / 'quo"te.d').mkdir()
    (weird / 'quo"te.d' / "inner.txt").write_text("x\n")
    append_rules(scaffolded_project, "*.key", "*.d/")

    members = run_build(scaffolded_project).members

    for name in quoted + ["plain.key", 'quo"te.d', 'quo"te.d/inner.txt']:
        assert f"weird/{name}" not in members, f"weird/{name} must not ship"
    assert "weird/keep.txt" in members


def test_git_dir_never_ships(scaffolded_project, run_build):
    subprocess.run(["git", "init", "-q", "."], cwd=scaffolded_project, check=True, timeout=60)
    append_rules(scaffolded_project, "!.git/")

    members = run_build(scaffolded_project).members

    assert not any(m == ".git" or m.startswith(".git/") for m in members)


def test_staged_dir_mirrors_tarball(scaffolded_project, run_build):
    (scaffolded_project / "data").mkdir()
    (scaffolded_project / "data" / "real.txt").write_text("x\n")
    os.symlink("real.txt", scaffolded_project / "data" / "alias.txt")

    run = run_build(scaffolded_project)

    staged_files, staged_links = set(), set()
    for dirpath, dirnames, filenames in os.walk(run.staged):
        rel = os.path.relpath(dirpath, run.staged)
        for name in filenames + dirnames:
            path = os.path.join(dirpath, name)
            relpath = name if rel == "." else f"{rel}/{name}"
            if os.path.islink(path):
                staged_links.add(relpath)
            elif os.path.isfile(path):
                staged_files.add(relpath)

    assert staged_files == {name for name, info in run.members.items() if info.isfile()}
    assert staged_links == {name for name, info in run.members.items() if info.issym()}
    assert staged_links == {"data/alias.txt"}


def test_empty_dir_semantics(scaffolded_project, run_build):
    (scaffolded_project / "outputs").mkdir()
    (scaffolded_project / "tools").mkdir()
    (scaffolded_project / "tools" / "scratch.tmp").write_text("x\n")
    append_rules(scaffolded_project, "*.tmp")

    members = run_build(scaffolded_project).members

    # A genuinely empty source dir ships ...
    assert "outputs" in members and members["outputs"].isdir()
    # ... but a dir emptied by exclusion (tools/, whose only content is a *.tmp
    # file a rule excludes) is dropped entirely.
    assert not any(m == "tools" or m.startswith("tools/") for m in members)


# 'folder/*' excludes what is inside the folder, not the folder itself, as in git,
# so a '!' rule can still bring one file back.


def test_one_file_from_a_hidden_folder(scaffolded_project, run_build):
    streamlit = scaffolded_project / ".streamlit"
    streamlit.mkdir()
    (streamlit / "config.toml").write_text("[server]\n")
    (streamlit / "secrets.toml").write_text("token = 'x'\n")
    append_rules(scaffolded_project, "!.streamlit/", ".streamlit/*", "!.streamlit/config.toml")

    members = run_build(scaffolded_project).members

    assert ".streamlit/config.toml" in members
    assert ".streamlit/secrets.toml" not in members


def test_one_file_from_a_folder(scaffolded_project, run_build):
    data = scaffolded_project / "data"
    data.mkdir()
    (data / "keep.txt").write_text("keep\n")
    (data / "other.txt").write_text("other\n")
    append_rules(scaffolded_project, "data/*", "!data/keep.txt")

    members = run_build(scaffolded_project).members

    assert "data/keep.txt" in members
    assert "data/other.txt" not in members


def test_folder_contents_rule_alone_ships_nothing_from_it(scaffolded_project, run_build):
    # Control: with nothing re-included, the emptied folder is dropped, as before.
    data = scaffolded_project / "data"
    data.mkdir()
    (data / "other.txt").write_text("other\n")
    append_rules(scaffolded_project, "data/*")

    members = run_build(scaffolded_project).members

    assert not any(name == "data" or name.startswith("data/") for name in members)


# The deploy contract re-includes the top-level README and deploy/ only; a nested
# README or deploy/ follows the user's rules like any other file.


def test_nested_readme_and_deploy_follow_user_rules(scaffolded_project, run_build):
    (scaffolded_project / "docs").mkdir()
    (scaffolded_project / "docs" / "README").write_text("internal notes\n")
    (scaffolded_project / "docs" / "guide.txt").write_text("guide\n")
    (scaffolded_project / "sub" / "deploy").mkdir(parents=True)
    (scaffolded_project / "sub" / "deploy" / "notes.txt").write_text("notes\n")
    append_rules(scaffolded_project, "docs/*", "sub/*")

    members = run_build(scaffolded_project).members

    assert "docs/README" not in members
    assert "sub/deploy/notes.txt" not in members
    assert "README" in members and "deploy/install.sh" in members  # control: top level always ships


@pytest.mark.parametrize("crlf", [False, True], ids=["lf", "crlf"])
def test_byte_order_mark_keeps_the_first_rule(scaffolded_project, run_build, crlf):
    # Some Windows editors start the file with a UTF-8 byte order mark; git
    # drops it from .gitignore, so the first rule must still apply.
    (scaffolded_project / "secret.txt").write_text("x\n")
    (scaffolded_project / "second.txt").write_text("x\n")
    (scaffolded_project / "kept.txt").write_text("x\n")
    newline = "\r\n" if crlf else "\n"
    (scaffolded_project / ".distignore").write_bytes(
        b"\xef\xbb\xbf" + newline.join(["secret.txt", "second.txt", ""]).encode()
    )

    members = run_build(scaffolded_project).members

    assert "secret.txt" not in members
    assert "second.txt" not in members
    assert "kept.txt" in members


# The build lists what .distignore kept out: the top of each excluded subtree,
# without hidden names (the built-in '.*' rule) or entries get_libs.sh fetched.


def excluded_report(output):
    """The lines under 'Excluded by .distignore: ...', header first."""
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if "Excluded by .distignore:" in line)
    report = [lines[start][lines[start].index("Excluded by .distignore:") :]]
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        report.append(line)
    return report


def test_build_lists_what_distignore_excluded(scaffolded_project, run_build):
    data = scaffolded_project / "data" / "raw"
    data.mkdir(parents=True)
    (data / "a.csv").write_text("x\n")
    (data / ".hidden").write_text("x\n")
    (scaffolded_project / "src" / "notes.bak").write_text("x\n")
    (scaffolded_project / ".secret").write_text("x\n")
    (scaffolded_project / "scripts" / "get_libs.sh").write_text("mkdir fetched && echo x > fetched/old.bak\n")
    append_rules(scaffolded_project, "/data/")
    # cli generate may write a tests/ folder, which the default rules exclude.
    tests = ["  tests/"] if (scaffolded_project / "tests").is_dir() else []

    run = run_build(scaffolded_project, progress=True)

    assert excluded_report(run.proc.stdout) == [
        f"Excluded by .distignore: {5 + len(tests)} paths",
        "  Makefile",
        "  README.md",
        "  data/",
        "  scripts/",
        "  src/notes.bak",
        *tests,
    ]
    assert "libs/fetched/old.bak" in run.members  # fetched entries are not filtered, so not listed


def test_long_exclusion_list_is_cut_in_progress_mode(scaffolded_project, run_build):
    for i in range(25):
        (scaffolded_project / f"old{i:02d}.bak").write_text("x\n")
    # Makefile, README.md and scripts/, and tests/ when cli generate wrote one.
    count = 28 + (scaffolded_project / "tests").is_dir()

    run = run_build(scaffolded_project, progress=True)

    report = excluded_report(run.proc.stdout)
    assert report[0] == f"Excluded by .distignore: {count} paths"
    assert len(report) == 22
    assert report[-1] == f"  ... and {count - 20} more (make build-verbose lists them all)"

    # make build-verbose lists every one.
    run = run_build(scaffolded_project)
    report = excluded_report(run.proc.stdout)
    assert report[0] == f"Excluded by .distignore: {count} paths"
    assert len(report) == count + 1
    assert "  old24.bak" in report and "  scripts/" in report


# A tarball must hold what a deploy uses: README (published), deploy/install.sh
# (run), and what install.sh runs: uv sync (pyproject.toml, uv.lock) and
# ./configure, plus each framework file in src/core/.

FRAMEWORK_WHY = "framework code the tool runs on (cli sync restores it)"
INIT_WHY = "without it, another package named core can take the place of src/core (an empty file will do)"
CORE_LINES = (
    "".join(
        f"  src/core/{name}: {FRAMEWORK_WHY}\n"
        for name in (
            "NGArgumentParser.py",
            "core_validators.py",
            "result_writer.py",
            "app_env.py",
            "set_pythonpath.py",
            "configure.py",
            "dep_names.py",
        )
    )
    + f"  src/core/__init__.py: {INIT_WHY}\n"
)


def test_missing_deploy_files_stop_before_anything_is_removed(scaffolded_project, run_build, tmp_path):
    first = run_build(scaffolded_project)
    moved = tmp_path / "moved"
    moved.mkdir()
    (scaffolded_project / "README").rename(moved / "README")
    (scaffolded_project / "deploy").rename(moved / "deploy")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert (
        "ERROR: The tool folder lacks files a deploy needs, so nothing was built:\n"
        "  README: the deploy publishes it\n"
        "  deploy/install.sh: the deploy runs it\n"
    ) in run.proc.stderr
    # A preflight stop: the previous build copy and tarball stay.
    assert first.tarball.is_file() and first.staged.is_dir()
    assert "Build failed" not in run.proc.stderr


@pytest.mark.parametrize(
    "rule, lines",
    [
        ("pyproject.toml", "  pyproject.toml: deploy/install.sh runs uv sync, which reads it\n"),
        (
            "/uv.lock",
            "  uv.lock: deploy/install.sh runs uv sync --frozen, which installs the versions it pins\n",
        ),
        ("configure", "  configure: deploy/install.sh runs it\n"),
        ("core/", CORE_LINES),
        ("configure.py", f"  src/core/configure.py: {FRAMEWORK_WHY}\n"),
        ("/src/core/result_writer.py", f"  src/core/result_writer.py: {FRAMEWORK_WHY}\n"),
        ("__init__.py", f"  src/core/__init__.py: {INIT_WHY}\n"),
    ],
    ids=["pyproject", "uv.lock", "configure", "core-folder", "configure.py", "result_writer", "init"],
)
def test_distignore_cannot_drop_what_install_sh_needs(scaffolded_project, run_build, tmp_path, rule, lines):
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    # The real uv rejects that uv.lock, which stops the build: a stand-in
    # accepts it, so the control build passes.
    env = quiet_uv(tmp_path)
    first = run_build(scaffolded_project, env=env)
    append_rules(scaffolded_project, rule)

    run = run_build(scaffolded_project, expect_ok=False, progress=True, env=env)

    assert run.proc.returncode != 0
    assert (
        f"ERROR: .distignore excludes files a deploy needs, so nothing was built. Remove the rules that exclude them:\n{lines}"
    ) in run.proc.stderr
    assert run.proc.stderr.count("\n  ") == lines.count("\n"), "only the excluded files are listed"
    # A preflight stop: the previous build copy and tarball stay.
    assert first.tarball.is_file() and first.staged.is_dir()
    assert "Build failed" not in run.proc.stderr


def test_missing_and_excluded_deploy_files_are_listed_together(scaffolded_project, run_build):
    (scaffolded_project / "README").unlink()
    append_rules(scaffolded_project, "/src/*", "!/src/run_*.py")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    err = run.proc.stderr
    assert (
        "ERROR: The tool folder lacks files a deploy needs, so nothing was built:\n  README: the deploy publishes it\n"
        in err
    )
    assert (
        f"ERROR: .distignore excludes files a deploy needs, so nothing was built. Remove the rules that exclude them:\n{CORE_LINES}"
    ) in err
    assert not (scaffolded_project / "build").exists()


def test_uv_lock_is_required_only_when_the_tool_has_one(scaffolded_project, run_build):
    append_rules(scaffolded_project, "uv.lock")
    # Control: with no uv.lock in the project there is nothing to require.
    run_build(scaffolded_project)
    (scaffolded_project / "uv.lock").write_text("version = 1\n")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert (
        "  uv.lock: deploy/install.sh runs uv sync --frozen, which installs the versions it pins\n" in run.proc.stderr
    )


def test_hook_that_removes_a_deploy_file_fails(scaffolded_project, run_build):
    (scaffolded_project / "scripts" / "hooks.sh").write_text('rm "$BUILD_DIR/configure"\n')

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert (
        "ERROR: scripts/hooks.sh removed files a deploy needs from the build copy:\n"
        "  configure: deploy/install.sh runs it\n"
    ) in run.proc.stderr
    assert not run.tarball.exists()


def test_missing_framework_file_stops_the_build(scaffolded_project, run_build, tmp_path):
    first = run_build(scaffolded_project)
    (scaffolded_project / "src" / "core" / "app_env.py").rename(tmp_path / "app_env.py")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert (
        "ERROR: The tool folder lacks files a deploy needs, so nothing was built:\n"
        f"  src/core/app_env.py: {FRAMEWORK_WHY}\n"
    ) in run.proc.stderr
    assert first.tarball.is_file() and first.staged.is_dir()


def test_hook_that_removes_a_framework_file_fails(scaffolded_project, run_build):
    (scaffolded_project / "scripts" / "hooks.sh").write_text('rm "$BUILD_DIR/src/core/configure.py"\n')

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert (
        "ERROR: scripts/hooks.sh removed files a deploy needs from the build copy:\n"
        f"  src/core/configure.py: {FRAMEWORK_WHY}\n"
    ) in run.proc.stderr
    assert not run.tarball.exists()


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX file modes")
def test_configure_without_its_exec_bit_stops_the_build(scaffolded_project, run_build):
    first = run_build(scaffolded_project)
    configure = scaffolded_project / "configure"
    configure.chmod(0o644)

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode != 0
    assert (
        "ERROR: configure is not executable: run 'chmod +x configure' and commit the mode "
        "(git update-index --chmod=+x configure)\n"
    ) in run.proc.stderr
    assert first.tarball.is_file() and first.staged.is_dir()
    assert "Build failed" not in run.proc.stderr


# README, deploy/install.sh and .python-version always ship. A rule that would
# exclude one of them gets a notice; everything else in deploy/ follows the rules.


def forced_notices(output):
    return [
        line[line.index("Notice: .distignore rule") :]
        for line in output.splitlines()
        if "Notice: .distignore rule" in line
    ]


def test_rule_that_excludes_a_forced_file_gets_a_notice(scaffolded_project, run_build):
    (scaffolded_project / ".python-version").write_text("3.11\n")
    append_rules(scaffolded_project, "README", "*.sh", ".python-version")

    run = run_build(scaffolded_project, progress=True)

    assert forced_notices(run.proc.stdout) == [
        "Notice: .distignore rule 'README' has no effect on README: the build always ships it.",
        "Notice: .distignore rule '*.sh' has no effect on deploy/install.sh: the build always ships it.",
        "Notice: .distignore rule '.python-version' has no effect on .python-version: the build always ships it.",
    ]
    for path in ("README", "deploy/install.sh", ".python-version"):
        assert path in run.members


def test_one_notice_per_rule(scaffolded_project, run_build):
    # A whitelist: everything at the top is excluded, then the tool's own files come back.
    append_rules(scaffolded_project, "/*", "!/src/", "!/pyproject.toml", "!/configure", "!/README.md")

    run = run_build(scaffolded_project, progress=True)

    assert forced_notices(run.proc.stdout) == [
        "Notice: .distignore rule '/*' has no effect on README and deploy/install.sh: the build always ships them."
    ]
    assert "README" in run.members and "deploy/install.sh" in run.members
    assert "license-LJI.txt" not in run.members  # control: the rule still applies elsewhere


def test_rule_for_the_deploy_folder_keeps_only_install_sh(scaffolded_project, run_build):
    deploy = scaffolded_project / "deploy"
    (deploy / "notes.txt").write_text("notes\n")
    (deploy / "helpers").mkdir()
    (deploy / "helpers" / "setup.sh").write_text("true\n")
    # Control: a rule for one file in deploy/ works, and needs no notice.
    append_rules(scaffolded_project, "/deploy/notes.txt")
    run = run_build(scaffolded_project, progress=True)
    assert "deploy/notes.txt" not in run.members and "deploy/helpers/setup.sh" in run.members
    assert not forced_notices(run.proc.stdout)

    append_rules(scaffolded_project, "/deploy/")
    run = run_build(scaffolded_project, progress=True)

    assert forced_notices(run.proc.stdout) == [
        "Notice: .distignore rule '/deploy/' has no effect on deploy/install.sh: the build always ships it."
    ]
    assert {m for m in run.members if m.startswith("deploy/")} == {"deploy/install.sh"}


def test_python_version_ships(scaffolded_project, run_build):
    (scaffolded_project / ".python-version").write_text("3.11\n")
    (scaffolded_project / "src" / ".python-version").write_text("3.12\n")

    run = run_build(scaffolded_project, progress=True)

    assert "Notice: .distignore rule" not in run.proc.stdout, "the built-in hidden-file rule is not the user's"
    assert ".python-version" in run.members
    assert (run.staged / ".python-version").read_text() == "3.11\n"
    assert "src/.python-version" not in run.members  # control: only the top-level one is forced


# A tool without .distignore gets the rules of the .distignore that cli
# generate writes, with a notice, instead of shipping everything.

TEMPLATES = Path(__file__).resolve().parents[1] / "ngargparser" / "templates"


def test_missing_distignore_uses_the_default_rules(scaffolded_project, run_build):
    (scaffolded_project / ".distignore").unlink()
    (scaffolded_project / "tests").mkdir(exist_ok=True)
    (scaffolded_project / "tests" / "test_demo.py").write_text("def test_x():\n    pass\n")
    (scaffolded_project / "src" / "notes.bak").write_text("x\n")
    (scaffolded_project / "setup_tool_env.sh").write_text("export X=1\n")

    run = run_build(scaffolded_project, progress=True)

    assert "Notice: no .distignore; using the default rules (see cli generate's .distignore).\n" in run.proc.stdout
    for absent in ("Makefile", "scripts/core/build.sh", "scripts/get_libs.sh", "tests/test_demo.py"):
        assert absent not in run.members, f"{absent} must not ship"
    assert "src/notes.bak" not in run.members and "setup_tool_env.sh" not in run.members
    assert not any(name.startswith("src/__pycache__") for name in run.members)
    assert "src/run_demo.py" in run.members and "configure" in run.members

    # Control: an empty .distignore is the tool's choice, with no rules and no notice.
    (scaffolded_project / ".distignore").write_text("")
    run = run_build(scaffolded_project, progress=True)
    assert "Notice: no .distignore" not in run.proc.stdout
    assert "Makefile" in run.members and "tests/test_demo.py" in run.members


# Without a .distignore the report named it anyway: "Excluded by .distignore".


def test_the_report_names_the_default_rules_without_a_distignore(scaffolded_project, run_build):
    (scaffolded_project / ".distignore").unlink()
    (scaffolded_project / "src" / "notes.bak").write_text("x\n")

    run = run_build(scaffolded_project, progress=True)

    header = next(line for line in run.proc.stdout.splitlines() if "Excluded by" in line)
    count = 4 + (scaffolded_project / "tests").is_dir()  # Makefile, README.md, scripts/, src/notes.bak and tests/
    assert header.endswith(f"Excluded by the default rules: {count} paths"), header
    assert "Excluded by .distignore" not in run.proc.stdout

    # Control: with a .distignore, the report names it.
    (scaffolded_project / ".distignore").write_text("*.bak\n")
    run = run_build(scaffolded_project, progress=True)
    assert "Excluded by .distignore: 1 path\n" in run.proc.stdout


def test_default_rules_are_the_templates():
    text = (TEMPLATES / "build.sh").read_text()
    block = re.search(r"^DEFAULT_DISTIGNORE='([^']*)'$", text, re.M)
    assert block, "build.sh holds DEFAULT_DISTIGNORE"
    template = [
        line
        for line in (TEMPLATES / "distignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert block.group(1).splitlines() == template


# The developer notes (README.md, with its "fill in the placeholders" text)
# stay out of the tarball by default; the users' README always ships.


@pytest.mark.parametrize("rules", ["template", "built-in"])
def test_developer_readme_does_not_ship_by_default(scaffolded_project, run_build, rules):
    if rules == "built-in":
        (scaffolded_project / ".distignore").unlink()
    (scaffolded_project / "docs").mkdir()
    (scaffolded_project / "docs" / "README.md").write_text("nested notes\n")

    run = run_build(scaffolded_project, progress=True)

    assert (scaffolded_project / "README.md").is_file()
    assert "README.md" not in run.members
    assert "README" in run.members
    assert "docs/README.md" in run.members, "only the top-level README.md is left out"


# The tool's virtualenv and the build's own state never ship, like build/ and
# .git/. A whitelist .distignore ('*', then '!*/' and the files to ship)
# re-included .venv/ (git keeps it out only through the .gitignore uv writes
# inside it), and the deploy's uv sync then refused it: "Project virtual
# environment directory ... is not a valid Python environment".

WHITELIST = ["*", "!*/", "!*.py", "!pyproject.toml", "!uv.lock", "!configure"]


def make_virtualenv(folder):
    """A folder laid out as a virtualenv, with a .py file in its site-packages."""
    site = folder / "lib" / "python3.11" / "site-packages"
    site.mkdir(parents=True)
    (site / "mod.py").write_text("X = 1\n")
    (folder / "bin").mkdir()
    (folder / "bin" / "activate").write_text("# activate\n")
    (folder / "pyvenv.cfg").write_text("home = /usr/bin\n")


def shipped_under(members, top):
    return sorted(m for m in members if m == top or m.startswith(top + "/"))


def test_whitelist_distignore_never_ships_the_venv_or_the_build_state(scaffolded_project, run_build):
    make_virtualenv(scaffolded_project / ".venv")
    removed = scaffolded_project / ".ngargparser" / "removed-libs" / "old-20261018"
    removed.mkdir(parents=True)
    (removed / "kept.py").write_text("X = 1\n")
    (scaffolded_project / ".distignore").write_text("\n".join(WHITELIST) + "\n")

    run = run_build(scaffolded_project, progress=True)

    assert shipped_under(run.members, ".venv") == []
    assert shipped_under(run.members, ".ngargparser") == []
    assert not (run.staged / ".venv").exists() and not (run.staged / ".ngargparser").exists()
    # Control: the whitelist ships what it names.
    assert "src/run_demo.py" in run.members and "pyproject.toml" in run.members

    # No '!' rule brings them back.
    append_rules(scaffolded_project, "!.venv/", "!.venv/**", "!.ngargparser/", "!.ngargparser/**")
    run = run_build(scaffolded_project, progress=True)
    assert shipped_under(run.members, ".venv") == []
    assert shipped_under(run.members, ".ngargparser") == []


@pytest.mark.parametrize("name", ["venv", "env", ".virtualenv"])
def test_other_virtualenv_folders_never_ship(scaffolded_project, run_build, name):
    # The other folder names ./configure looks for a virtualenv under.
    make_virtualenv(scaffolded_project / name)
    append_rules(scaffolded_project, f"!{name}/")

    run = run_build(scaffolded_project, progress=True)

    assert shipped_under(run.members, name) == []

    # Control: a folder of that name that holds no virtualenv follows the rules.
    (scaffolded_project / name / "pyvenv.cfg").unlink()
    run = run_build(scaffolded_project, progress=True)
    assert f"{name}/lib/python3.11/site-packages/mod.py" in run.members


# paths.py holds the dependencies ./configure reads on the deploy host. A rule
# that excluded it built a tarball whose ./configure found no paths.py.

PATHS_PY_LINE = "  paths.py: ./configure on the deploy host reads the dependencies from it\n"


def test_distignore_cannot_drop_paths_py(scaffolded_project, run_build):
    # Control: a tool without paths.py builds; with one, it ships.
    run_build(scaffolded_project)
    (scaffolded_project / "paths.py").write_text("# The tool's dependencies.\n")
    first = run_build(scaffolded_project)
    assert "paths.py" in first.members
    append_rules(scaffolded_project, "/paths.py")

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert (
        "ERROR: .distignore excludes files a deploy needs, so nothing was built. "
        "Remove the rules that exclude them:\n" + PATHS_PY_LINE
    ) in run.proc.stderr
    assert first.staged.is_dir() and first.tarball.is_file(), "a preflight stop removes nothing"


def test_hook_that_removes_paths_py_fails(scaffolded_project, run_build):
    (scaffolded_project / "paths.py").write_text("# The tool's dependencies.\n")
    (scaffolded_project / "scripts" / "hooks.sh").write_text('rm "$BUILD_DIR/paths.py"\n')

    run = run_build(scaffolded_project, expect_ok=False, progress=True)

    assert run.proc.returncode == 1
    assert "ERROR: scripts/hooks.sh removed files a deploy needs from the build copy:\n" + PATHS_PY_LINE in (
        run.proc.stderr
    )
    assert not run.tarball.exists()


# The build machine's git config changed what shipped: core.ignorecase=true in
# a developer's global config (git init also sets it on a case-insensitive
# file system) made '*.TXT' exclude a.txt too. The rules repo's git calls pin
# it to false, so a rule matches as it does in CI.


def test_global_git_config_cannot_change_what_ships(scaffolded_project, run_build, tmp_path):
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[core]\n\tignorecase = true\n")
    env = {"GIT_CONFIG_GLOBAL": str(gitconfig)}
    seen = subprocess.run(
        ["git", "config", "--global", "core.ignorecase"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )
    if seen.stdout.strip() != "true":
        pytest.skip("this git does not read GIT_CONFIG_GLOBAL (git 2.32 or later does)")
    (scaffolded_project / "src" / "a.txt").write_text("a\n")
    (scaffolded_project / "src" / "B.TXT").write_text("B\n")
    append_rules(scaffolded_project, "*.TXT")

    run = run_build(scaffolded_project, env=env)

    assert "src/a.txt" in run.members, "the global core.ignorecase changed what ships"
    assert "src/B.TXT" not in run.members
    assert "  src/B.TXT\n" in run.proc.stdout and "  src/a.txt\n" not in run.proc.stdout
    # Control: the same rules without that config.
    plain = run_build(scaffolded_project)
    assert sorted(plain.members) == sorted(run.members)


def test_package_metadata_does_not_ship_by_default(scaffolded_project, run_build):
    # An editable install or a package build writes src/<name>.egg-info/ in the tool folder.
    egg = scaffolded_project / "src" / "demo.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text("Name: demo\n")

    run = run_build(scaffolded_project)

    assert not [name for name in run.members if ".egg-info" in name]
