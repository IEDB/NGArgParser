"""`cli generate`: "tool" in what it prints, names it refuses because the tool needs a
package of that name, the end-user README title, and the two starter files a new
tool gets (tests/test_parser.py and .gitattributes)."""

import os
import re
import shutil
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli


def run_cli(*args, cwd=None):
    env = {**os.environ, "CI": "1", "NGARGPARSER_NO_UPDATE_CHECK": "1", "NGARGPARSER_NO_SELF_UPGRADE": "1"}
    return subprocess.run(
        [sys.executable, "-m", "ngargparser.cli", *args], capture_output=True, text=True, env=env, cwd=cwd
    )


# --- "tool", not "project" or "app" -------------------------------------------------------


def test_generate_says_tool_in_its_output_and_help(in_tmp_dir, strip_ansi, capsys):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    out = strip_ansi(capsys.readouterr().out)
    assert "Tool 'demo' is ready. Next steps:" in out
    assert "Created the tool 'demo'." in out
    assert "project" not in out.lower()

    top = run_cli("-h").stdout
    assert "generate (g)        Create a new tool" in top
    generate = run_cli("generate", "-h").stdout
    assert "generate [-h] tool_name" in generate
    assert "Name of the tool to create" in " ".join(generate.split())
    assert "project" not in generate.lower()
    assert "project" not in run_cli("sync", "-h").stdout.lower()


def test_name_errors_say_tool(in_tmp_dir, strip_ansi, capsys):
    for name in ("1abc", "a-", ""):
        assert cli.startapp_command(Namespace(project_name=name)) == 1
        out = strip_ansi(capsys.readouterr().err)
        assert "tool name" in out.lower() and "project" not in out.lower()


# The comments in the files a new tool gets said "project" where they mean the tool.


def test_comments_in_a_new_tools_files_say_tool(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    tool = in_tmp_dir / "demo"
    project_word = re.compile(r"(?<![A-Za-z_])project(?![A-Za-z_])", re.I)  # not pyproject, PROJECT_ROOT
    files = [
        tool / name
        for name in (
            ".gitlab-ci.yml",
            "configure",
            "Makefile",
            "scripts/get_libs.sh",
            ".distignore",
            "scripts/core/build.conf",
        )
    ] + [cli.TEMPLATE_DIR / "hooks.sh"]  # the starting point for scripts/hooks.sh

    found = []
    for path in files:
        assert path.is_file(), path
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#") and project_word.search(line):
                found.append(f"{path.name}:{number}: {line}")
    assert found == []


# --- Names a tool cannot take ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name, why",
    [
        ("pytest", "A new tool has pytest in its dev dependency group"),
        ("Pytest", "A new tool has pytest in its dev dependency group"),
        ("python-dotenv", "Every tool depends on python-dotenv"),
        ("python_dotenv", "Every tool depends on python-dotenv"),
        ("dotenv", "Every tool depends on python-dotenv, which installs the module dotenv"),
        ("ngargparser", "ngargparser is the framework's own package"),
    ],
)
def test_a_name_the_tool_needs_as_a_package_is_refused(in_tmp_dir, strip_ansi, capsys, name, why):
    assert cli.startapp_command(Namespace(project_name=name)) == 1

    out = strip_ansi(capsys.readouterr().err)
    assert (
        f"✗ Invalid tool name '{name}'. {why}, and the tool's name is also its Python package name, so the two "
        "would clash. Pick another name (e.g. 'aa-counter')."
    ) in out
    assert list(in_tmp_dir.iterdir()) == []  # nothing was created


def test_a_package_added_to_the_template_is_refused_too(in_tmp_dir, strip_ansi, capsys, monkeypatch, tmp_path_factory):
    # The names come from the generated pyproject.toml, not a fixed list.
    templates = tmp_path_factory.mktemp("templates") / "templates"
    shutil.copytree(cli.TEMPLATE_DIR, templates, ignore=shutil.ignore_patterns("__pycache__"))
    tmpl = templates / "pyproject.toml.tmpl"
    tmpl.write_text(tmpl.read_text().replace('    "python-dotenv",\n', '    "python-dotenv",\n    "numpy>=1.26",\n'))
    monkeypatch.setattr(cli, "TEMPLATE_DIR", templates)

    assert cli.startapp_command(Namespace(project_name="NumPy")) == 1
    assert "A new tool lists numpy in its dependencies" in strip_ansi("".join(capsys.readouterr()))
    # Control: a name that only contains one of them is fine.
    assert cli.startapp_command(Namespace(project_name="pytest-runner-tool")) is None
    assert (in_tmp_dir / "pytest-runner-tool" / "src" / "run_pytest_runner_tool.py").is_file()


# --- The end-user README title --------------------------------------------------------------


@pytest.mark.parametrize("name, cap", [("tcell_mhcii", "TcellMhcii"), ("netMHCpan-el", "NetMHCpanEl")])
def test_the_readme_title_shows_the_name_as_typed(in_tmp_dir, name, cap):
    assert not cli.startapp_command(Namespace(project_name=name))

    readme = (in_tmp_dir / name / "README").read_text()
    title = readme.splitlines()[0]
    assert title.startswith(f"{name} ") and cap not in title
    assert title == (cli.TEMPLATE_DIR / "README").read_text().splitlines()[0].replace("{TOOL_NAME_CAP}", name).replace(
        "{TOOL_NAME}", name
    )
    # The class-name form is still what the developer note names the parser file with.
    assert f"src/{cap}ArgumentParser.py" in (in_tmp_dir / name / "README.md").read_text()


# --- tests/test_parser.py -------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, folder, parser",
    [("demo", "demo", "DemoArgumentParser"), ("example", "aa-counter", "AACounterArgumentParser")],
)
def test_a_new_tool_has_a_parser_test_that_passes(in_tmp_dir, name, folder, parser):
    assert not cli.startapp_command(Namespace(project_name=name))
    tool = in_tmp_dir / folder
    test_file = tool / "tests" / "test_parser.py"

    assert f"from {parser} import {parser}\n" in test_file.read_text()
    # What `uv run pytest` runs there, on the test interpreter: pytest reads the tool's
    # [tool.pytest.ini_options], which puts src/ on the import path.
    env = {k: v for k, v in os.environ.items() if not k.startswith("APP_") and k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tool,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")
def test_the_tarball_leaves_the_tests_and_gitattributes_out(scaffolded_project, run_build, fake_venv):
    fake_venv(scaffolded_project)  # so the startup check runs

    build = run_build(scaffolded_project)

    assert (scaffolded_project / "tests" / "test_parser.py").is_file()
    assert not [m for m in build.members if m == "tests" or m.startswith("tests/") or m == ".gitattributes"]
    assert "src/run_demo.py" in build.members
    assert "Startup check skipped" not in build.proc.stdout + build.proc.stderr  # it ran, and passed


def test_sync_leaves_the_starter_files_alone(scaffolded_project, sync_args, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    test_file = scaffolded_project / "tests" / "test_parser.py"
    attributes = scaffolded_project / ".gitattributes"
    test_file.write_text("# my tests\n")
    attributes.write_text("# mine\n")
    core = scaffolded_project / "src" / "core" / "NGArgumentParser.py"
    core.write_text(core.read_text() + "# stale\n")

    assert cli.sync_command(sync_args()) == 0
    assert test_file.read_text() == "# my tests\n" and attributes.read_text() == "# mine\n"

    test_file.unlink()
    attributes.unlink()
    core.write_text(core.read_text() + "# stale\n")
    assert cli.sync_command(sync_args()) == 0
    assert not test_file.exists() and not attributes.exists()


# --- .gitattributes -------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("name, folder", [("demo", "demo"), ("example", "aa-counter")])
def test_a_new_tool_checks_out_its_shell_files_with_lf(in_tmp_dir, name, folder):
    assert not cli.startapp_command(Namespace(project_name=name))
    tool = in_tmp_dir / folder

    lines = (tool / ".gitattributes").read_text().splitlines()
    assert {"*.sh text eol=lf", "configure text eol=lf", "build.conf text eol=lf"} <= set(lines)
    subprocess.run(["git", "init", "-q"], cwd=tool, check=True)
    for path in ("scripts/core/build.sh", "configure", "scripts/core/build.conf", "deploy/install.sh"):
        eol = subprocess.run(["git", "check-attr", "eol", path], cwd=tool, capture_output=True, text=True).stdout
        assert eol.strip() == f"{path}: eol: lf"
    assert (
        subprocess.run(["git", "check-attr", "eol", "src/run_x.py"], cwd=tool, capture_output=True, text=True)
        .stdout.strip()
        .endswith("unspecified")
    )  # control: other files keep git's default
