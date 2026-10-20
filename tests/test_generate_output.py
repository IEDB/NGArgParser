"""`cli generate`: the help text, the version line, the starter .gitlab-ci.yml, the check for a
folder inside a tool, the next steps it prints, and the entry key in pyproject.toml."""

import os
import re
import shutil
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

CI_TEMPLATE = "# CI for {TOOL_NAME}\ntest:\n  script:\n    - echo {TOOL_NAME}\n"


def run_cli(*args):
    env = {**os.environ, "CI": "1", "NGARGPARSER_NO_UPDATE_CHECK": "1", "NGARGPARSER_NO_SELF_UPGRADE": "1"}
    return subprocess.run([sys.executable, "-m", "ngargparser.cli", *args], capture_output=True, text=True, env=env)


@pytest.fixture
def templates(tmp_path_factory, monkeypatch):
    """A copy of the templates folder that generate reads from, with or without gitlab-ci.yml."""
    copy = tmp_path_factory.mktemp("templates") / "templates"
    shutil.copytree(cli.TEMPLATE_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(cli, "TEMPLATE_DIR", copy)

    def _with_ci(text):
        ci = copy / "gitlab-ci.yml"
        if text is None:
            ci.unlink(missing_ok=True)
        else:
            ci.write_text(text)
        return copy

    return _with_ci


def test_generate_help_mentions_the_sample_app():
    result = run_cli("generate", "-h")
    assert result.returncode == 0
    assert "'example'" in result.stdout and "aa-counter" in result.stdout


def test_generate_prints_the_ngargparser_version_it_used(in_tmp_dir, strip_ansi, capsys):
    cli.startapp_command(Namespace(project_name="demo"))
    assert f"Generated with ngargparser {cli.__version__}." in strip_ansi(capsys.readouterr().out)


def test_a_failed_generate_prints_no_version_line(in_tmp_dir, strip_ansi, capsys):
    # Control: the line comes with a tool that was made, not with a refusal.
    (in_tmp_dir / "demo").mkdir()
    assert cli.startapp_command(Namespace(project_name="demo")) == 1
    assert "Generated with ngargparser" not in strip_ansi(capsys.readouterr().out)


@pytest.mark.parametrize("name, folder", [("my-tool", "my-tool"), ("example", "aa-counter")])
def test_generate_copies_the_ci_template_with_the_tool_name(in_tmp_dir, templates, name, folder):
    templates(CI_TEMPLATE)

    assert not cli.startapp_command(Namespace(project_name=name))

    ci = in_tmp_dir / folder / ".gitlab-ci.yml"
    assert ci.read_text() == CI_TEMPLATE.replace("{TOOL_NAME}", folder)


def test_generate_without_a_ci_template_still_works(in_tmp_dir, templates):
    # Control: an ngargparser that ships no gitlab-ci.yml.
    templates(None)

    assert not cli.startapp_command(Namespace(project_name="my-tool"))

    assert (in_tmp_dir / "my-tool" / "src" / "run_my_tool.py").is_file()
    assert not (in_tmp_dir / "my-tool" / ".gitlab-ci.yml").exists()


def test_sync_never_creates_or_changes_the_ci_file(in_tmp_dir, templates, sync_args, monkeypatch):
    # Control: .gitlab-ci.yml is the tool's own once generated.
    templates(CI_TEMPLATE)
    assert not cli.startapp_command(Namespace(project_name="demo"))
    project = in_tmp_dir / "demo"
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)  # the test drive's interpreter
    ci = project / ".gitlab-ci.yml"
    ci.write_text("# edited by the team\n")
    core = project / "src" / "core" / "NGArgumentParser.py"
    core.write_text(core.read_text() + "# stale\n")

    assert cli.sync_command(sync_args()) == 0
    assert ci.read_text() == "# edited by the team\n"

    ci.unlink()
    core.write_text(core.read_text() + "# stale\n")
    assert cli.sync_command(sync_args()) == 0
    assert not ci.exists()


# --- Generating inside a tool, the next steps, the entry key --------------------------------


def test_generate_inside_a_tool_is_refused_and_creates_nothing(in_tmp_dir, strip_ansi, capsys, monkeypatch):
    assert not cli.startapp_command(Namespace(project_name="demo"))  # control: next to a tool is fine
    capsys.readouterr()

    for folder, where in (
        (in_tmp_dir / "demo", "This folder is"),
        (in_tmp_dir / "demo" / "src", "This folder is inside"),
    ):
        monkeypatch.chdir(folder)
        before = sorted(p.relative_to(in_tmp_dir) for p in in_tmp_dir.rglob("*"))

        for name in ("inner", "example"):
            assert cli.startapp_command(Namespace(project_name=name)) == 1
            out = strip_ansi("".join(capsys.readouterr()))
            assert f"✗ {where} the ngargparser tool {in_tmp_dir / 'demo'} (it has " in out
            assert "would ship in that tool's tarball, so nothing was created" in out
            assert "Generated with ngargparser" not in out
            assert sorted(p.relative_to(in_tmp_dir) for p in in_tmp_dir.rglob("*")) == before


def test_generate_looks_no_higher_than_the_home_folder(in_tmp_dir, monkeypatch):
    # A stray tool marker above the home folder does not block generating under it.
    (in_tmp_dir / "pyproject.toml").write_text('[tool.ngargparser]\napp_name = "stray"\n')
    home = in_tmp_dir / "home"
    (home / "work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home / "work")

    assert not cli.startapp_command(Namespace(project_name="demo"))
    assert (home / "work" / "demo" / "src" / "run_demo.py").is_file()

    monkeypatch.setenv("HOME", str(in_tmp_dir))  # control: the marker is now at the home folder itself
    assert cli.startapp_command(Namespace(project_name="other")) == 1
    assert not (home / "work" / "other").exists()


@pytest.mark.parametrize(
    "name, steps",
    [
        (
            "my-tool",
            [
                "cd my-tool",
                "uv sync",
                "# write the predict logic: replace the predict branch in src/run_my_tool.py",
                "uv run python src/run_my_tool.py predict -j src/example.json",
                "make build",
            ],
        ),
        (
            "example",
            [
                "cd aa-counter",
                "uv sync",
                "uv run python src/run_aa_counter.py predict -j src/example.json",
                "make build",
            ],
        ),
    ],
)
def test_generate_prints_the_quickstart_steps(in_tmp_dir, strip_ansi, capsys, name, steps):
    assert not cli.startapp_command(Namespace(project_name=name))

    out = strip_ansi(capsys.readouterr().out)
    lines = out.split("Next steps:", 1)[1].strip().splitlines()
    printed = [re.sub(r"\s{2,}#.*$", "", line.strip()) for line in lines]  # without the trailing comments
    assert printed == steps


@pytest.mark.parametrize("name, script", [("my-tool", "run_my_tool.py"), ("example", "run_aa_counter.py")])
def test_generate_names_the_entry_script_in_pyproject(in_tmp_dir, templates, name, script):
    # The template's {TOOL_EXEC_NAME} is the name in src/run_<name>.py.
    tmpl = templates(None) / "pyproject.toml.tmpl"
    tmpl.write_text(
        tmpl.read_text().split("[tool.ngargparser]")[0] + '[tool.ngargparser]\nentry = "src/run_{TOOL_EXEC_NAME}.py"\n'
    )

    assert not cli.startapp_command(Namespace(project_name=name))

    folder = in_tmp_dir / ("aa-counter" if name == "example" else name)
    assert f'entry = "src/{script}"' in (folder / "pyproject.toml").read_text()
    assert (folder / "src" / script).is_file()


# --- The name rules and the class name -------------------------------------------------------


def test_generate_refuses_a_name_that_ends_in_a_dash_or_underscore(in_tmp_dir, strip_ansi, capsys):
    for name, last in (("a-", "-"), ("my_tool_", "_")):
        assert cli.startapp_command(Namespace(project_name=name)) == 1
        out = strip_ansi(capsys.readouterr().err)
        assert (
            f"✗ Invalid tool name '{name}'. End it with a letter or digit: the name is also the Python package "
            f"name, which cannot end in '{last}' (e.g. 'aa-counter')." in out
        )
        assert not (in_tmp_dir / name).exists()

    # Control: a dash or underscore inside the name, and a digit at the end, are fine.
    for name in ("a-1", "my_tool"):
        assert not cli.startapp_command(Namespace(project_name=name))
        assert (in_tmp_dir / name / "src").is_dir()


def test_generate_keeps_the_capitals_of_the_name_in_the_class_name(in_tmp_dir):
    for name, script, parser in (
        ("MyTool", "run_MyTool.py", "MyToolArgumentParser"),
        ("my-tool", "run_my_tool.py", "MyToolArgumentParser"),
        ("my_app", "run_my_app.py", "MyAppArgumentParser"),
    ):
        assert not cli.startapp_command(Namespace(project_name=name))
        src = in_tmp_dir / name / "src"
        assert (src / f"{parser}.py").is_file()
        assert f"class {parser}(" in (src / f"{parser}.py").read_text()
        assert f"from {parser} import {parser}" in (src / script).read_text()
