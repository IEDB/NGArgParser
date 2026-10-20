import re
import shutil
import stat
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli


def test_create_project_structure_builds_expected_tree(in_tmp_dir):
    rc = cli.create_project_structure("demo")
    assert not rc
    root = in_tmp_dir / "demo"

    expected = [
        "src/core/NGArgumentParser.py",
        "src/core/core_validators.py",
        "src/core/result_writer.py",
        "src/core/app_env.py",
        "src/core/set_pythonpath.py",
        "src/core/configure.py",
        "src/core/__init__.py",
        "src/validators.py",
        "src/run_demo.py",
        "src/DemoArgumentParser.py",
        "src/preprocess.py",
        "src/postprocess.py",
        "scripts/core/build.sh",
        "scripts/core/build.conf",
        "scripts/get_libs.sh",
        ".distignore",
        "Makefile",
        "configure",
        "deploy/install.sh",
        "pyproject.toml",
        "README.md",
    ]
    for rel in expected:
        assert (root / rel).exists(), f"missing scaffolded file: {rel}"
    # The build hook is optional: generate leaves it out.
    assert not (root / "scripts" / "hooks.sh").exists()


def test_deps_script_is_executable_bash(in_tmp_dir):
    cli.create_project_structure("demo")
    path = in_tmp_dir / "demo" / "scripts" / "get_libs.sh"
    assert stat.S_IMODE(path.stat().st_mode) == 0o755
    if shutil.which("bash"):
        subprocess.run(["bash", "-n", str(path)], check=True, timeout=30)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_hooks_template_is_valid_bash():
    # The README tells developers to copy this template when they need a hook.
    subprocess.run(["bash", "-n", f"{cli.TEMPLATE_DIR}/hooks.sh"], check=True, timeout=30)


def test_pyproject_records_the_tool_name(in_tmp_dir):
    # A deployed copy has no .env; ./configure reads the name from here instead of
    # the install folder (<name>/<version>/).
    cli.create_project_structure("demo")
    content = (in_tmp_dir / "demo" / "pyproject.toml").read_text()
    table = content.split("[tool.ngargparser]\n", 1)[1]
    assert 'app_name = "demo"\n' in table


def test_placeholders_are_substituted(in_tmp_dir):
    cli.create_project_structure("demo")
    root = in_tmp_dir / "demo"

    run_file = (root / "src" / "run_demo.py").read_text()
    assert "CHILDPARSER" not in run_file
    assert "DemoArgumentParser" in run_file

    parser_file = (root / "src" / "DemoArgumentParser.py").read_text()
    assert "class DemoArgumentParser" in parser_file

    configure = (root / "src" / "core" / "configure.py").read_text()
    assert "PROJECT_NAME" not in configure

    readme = (root / "README.md").read_text()
    assert "img.shields.io/badge/ngargparser-" in readme


def test_configure_launcher_forwards_arguments(in_tmp_dir):
    cli.create_project_structure("demo")
    root = in_tmp_dir / "demo"

    launcher = (root / "configure").read_text()
    assert launcher.startswith("#!/bin/sh")
    assert '"$@"' in launcher
    assert launcher != "./src/core/configure.py"  # the old, broken content


def test_generated_project_has_gitignore(in_tmp_dir):
    cli.startapp_command(Namespace(project_name="demo"))
    gitignore = in_tmp_dir / "demo" / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text()
    assert ".ngargparser/" in content
    assert "*.bak" in content


def test_generated_project_keeps_configure_output_out_of_git(in_tmp_dir):
    # ./configure writes .env and setup_<dep>_env.sh with this machine's paths.
    cli.startapp_command(Namespace(project_name="demo"))
    project = in_tmp_dir / "demo"
    (project / "setup_tc1_env.sh").write_text("export TC1_PATH=/here\n")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ".env" not in status
    assert "setup_tc1_env.sh" not in status
    assert "README.md" in status  # control: the project's own files still show


def test_startapp_writes_env_but_not_paths(in_tmp_dir):
    rc = cli.startapp_command(Namespace(project_name="demo")) or 0
    assert rc == 0
    root = in_tmp_dir / "demo"
    assert not (root / "paths.py").exists()
    env = (root / ".env").read_text()
    assert "APP_NAME=demo" in env
    assert "APP_ROOT=" in env
    # The start-up script a tool that depends on this one runs.
    assert "APP_ENTRY=src/run_demo.py" in env
    assert (root / "src" / "run_demo.py").is_file()


def test_generated_validators_are_ready_to_append_to(in_tmp_dir):
    # A developer pastes custom validators at the end of this file; they raise
    # argparse.ArgumentTypeError, so the file must import argparse and end with a newline.
    cli.create_project_structure("demo")
    text = (in_tmp_dir / "demo" / "src" / "validators.py").read_text()
    assert text.endswith("\n")
    assert "\nimport argparse\n" in text


class TestValidateProjectName:
    def test_accepts_reasonable_names(self):
        for name in ("demo", "aa-counter", "tool_v2"):
            assert cli.validate_project_name(name) is None

    def test_rejects_path_traversal(self):
        assert cli.validate_project_name("../escape") is not None

    def test_rejects_nested_path(self):
        assert cli.validate_project_name("a/b") is not None

    def test_rejects_absolute_path(self):
        assert cli.validate_project_name("/tmp/evil") is not None

    def test_rejects_leading_dash_and_dot(self):
        assert cli.validate_project_name("-x") is not None
        assert cli.validate_project_name("..") is not None

    def test_rejects_empty(self):
        assert cli.validate_project_name("") is not None
        assert cli.validate_project_name("   ") is not None


def test_existing_directory_is_refused(in_tmp_dir):
    (in_tmp_dir / "demo").mkdir()
    rc = cli.startapp_command(Namespace(project_name="demo"))
    assert rc == 1
    # The pre-existing dir must be untouched — no scaffolding written into it.
    assert not (in_tmp_dir / "demo" / "src").exists()
    assert not (in_tmp_dir / "demo" / "paths.py").exists()


def test_path_traversal_creates_nothing(in_tmp_dir):
    rc = cli.startapp_command(Namespace(project_name="../escape"))
    assert rc == 1
    assert not (in_tmp_dir.parent / "escape").exists()


def test_failed_scaffold_cleans_up_partial_dir(in_tmp_dir, monkeypatch):
    # Force a mid-scaffold failure and assert the partial project is removed
    # and no paths.py/.env are written.
    real_copy = cli.shutil.copy
    calls = {"n": 0}

    def flaky_copy(src, dst, *a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("simulated copy failure")
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(cli.shutil, "copy", flaky_copy)
    rc = cli.startapp_command(Namespace(project_name="demo"))
    assert rc == 1
    assert not (in_tmp_dir / "demo").exists()


def _help(project, script, *args):
    result = subprocess.run(
        [sys.executable, f"src/{script}", *args, "-h"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_blank_tool_predict_help_has_no_sample_app_text(in_tmp_dir):
    # The starter parser used to describe its inputs with the letter-counter
    # sample's "Perform counting given a TSV file."
    cli.startapp_command(Namespace(project_name="demo"))

    out = _help(in_tmp_dir / "demo", "run_demo.py", "predict")
    assert "counting" not in out.lower()
    assert "--input-tsv" in out and "--input-json" in out  # control: the inputs are still there


def test_blank_tool_top_level_help_has_no_placeholders(in_tmp_dir):
    cli.startapp_command(Namespace(project_name="demo"))

    out = _help(in_tmp_dir / "demo", "run_demo.py")
    assert "Here are list" not in out
    assert "additional help" not in out
    assert "Text to display before" not in out
    for sub in ("predict", "preprocess", "postprocess"):  # control
        assert sub in out


def test_example_app_keeps_its_own_counting_text(in_tmp_dir):
    # Control: the letter-counter sample really counts, so it keeps its wording.
    cli.startapp_command(Namespace(project_name="example"))

    out = _help(in_tmp_dir / "aa-counter", "run_aa_counter.py", "predict")
    assert "Perform counting given a TSV file." in out


def _run(project, script, *args):
    return subprocess.run([sys.executable, f"src/{script}", *args], cwd=project, capture_output=True, text=True)


# predict with no input used to reach the predict branch, where the Quickstart
# code crashed with TypeError on open(None).

TOOLS = [("demo", "demo", "run_demo.py"), ("example", "aa-counter", "run_aa_counter.py")]
ONE_INPUT = {
    "demo": [("-j", "in.json"), ("-t", "in.tsv")],
    "example": [("-j", "src/example.json"), ("-t", "src/example.tsv", "-a", "L")],
}


def _parsed(result):
    """The command line parsed: the command ran, or reached the unedited predict or postprocess stub."""
    if result.returncode == 1 and re.search(r"\b(predict|postprocess) is not implemented yet", result.stderr):
        return True
    return result.returncode == 0


@pytest.mark.parametrize("name, folder, script", TOOLS, ids=["blank", "example"])
def test_predict_without_input_is_a_usage_error(in_tmp_dir, name, folder, script):
    cli.startapp_command(Namespace(project_name=name))

    result = _run(in_tmp_dir / folder, script, "predict")
    assert result.returncode == 2
    assert "predict: error: one of the arguments --input-tsv/-t --input-json/-j is required" in result.stderr


@pytest.mark.parametrize("name, folder, script", TOOLS, ids=["blank", "example"])
def test_predict_with_one_input_still_runs(in_tmp_dir, name, folder, script):
    # Control: either input alone still runs.
    cli.startapp_command(Namespace(project_name=name))
    project = in_tmp_dir / folder
    (project / "in.json").write_text("{}\n")
    (project / "in.tsv").write_text("peptide\nAAK\n")

    for args in ONE_INPUT[name]:
        result = _run(project, script, "predict", *args)
        assert _parsed(result), f"{args}: {result.stderr}"


# -o is a prefix. 'out/' used to write the hidden file out/.tsv, and an existing
# folder 'out' wrote out.tsv beside it.


@pytest.fixture
def demo(in_tmp_dir):
    cli.startapp_command(Namespace(project_name="demo"))
    project = in_tmp_dir / "demo"
    (project / "in.json").write_text("{}\n")
    (project / "jd.json").write_text("[]\n")
    (project / "existing").mkdir()
    return project


INPUT = {"predict": "in.json", "postprocess": "jd.json"}


@pytest.mark.parametrize("subcommand", ["predict", "postprocess"])
@pytest.mark.parametrize("value, example", [("out/", "out/result"), ("existing", "existing/result")])
def test_output_prefix_that_is_a_folder_is_rejected(demo, subcommand, value, example):
    result = _run(demo, "run_demo.py", subcommand, "-j", INPUT[subcommand], "-o", value)

    assert result.returncode == 2
    assert (
        f"{subcommand}: error: argument --output-prefix/-o: '{value}' is a folder; "
        f"-o takes an output prefix such as '{example}'"
    ) in result.stderr


@pytest.mark.parametrize("subcommand", ["predict", "postprocess"])
@pytest.mark.parametrize("value", ["result", "existing/result", "out/result"])
def test_output_prefix_that_is_a_file_name_is_accepted(demo, subcommand, value):
    # Control: a prefix, even one inside a folder, still parses. Both predict and
    # postprocess create a missing folder (out/) when they write.
    result = _run(demo, "run_demo.py", subcommand, "-j", INPUT[subcommand], "-o", value)

    assert _parsed(result), result.stderr
