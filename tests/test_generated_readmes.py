"""The READMEs that `cli generate` writes must hold for the tool it generates.

`cli generate <name>` writes two files: README, the end-user README that ships
in the tarball, and README.md, the developer note that carries the version
badge. `cli generate example` writes one walkthrough to both. In the Markdown
files (README.md, and the example's walkthrough), a shell block meant to be run
is marked with `<!-- run -->` on the line before its ```bash fence. The blank
tool's README is plain text like the real tools' READMEs, so its commands are
the lines indented 4 spaces under its "Usage" heading. These tests generate the
project, take those commands from the generated file, and run every command
line in order from the project folder:

  uv run python    replaced by the test interpreter, which has python-dotenv
  uv sync, make , ./configure, cd
                   lines skipped: the fixture already made the project, and
                   test_build_*.py and test_configure_env.py cover building
                   and configuring
  #, blank         lines skipped

Every other line runs through bash and must exit 0. A trailing backslash
continues a command on the next line.
"""

import re
import shlex
import shutil
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

MARKED = re.compile(r"^<!-- run -->\n```bash\n(.*?)^```", re.M | re.S)
SKIPPED = ("uv sync", "make ", "./configure", "cd ", "#")
BADGE = f"[![ngargparser](https://img.shields.io/badge/ngargparser-{cli.__version__}-blue.svg)]"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def generate(tmp_dir, name):
    assert not cli.startapp_command(Namespace(project_name=name))
    return tmp_dir / ("aa-counter" if name == "example" else name)


def command_lines(blocks):
    commands = []
    for block in blocks:
        for line in block.replace("\\\n", " ").splitlines():
            line = line.strip()
            if line and not line.startswith(SKIPPED):
                commands.append(line)
    return commands


def marked_commands(text):
    """The command lines of the `<!-- run -->` blocks in `text`, in order."""
    return command_lines(MARKED.findall(text))


def usage_commands(text):
    """The command lines indented 4 spaces under the plain-text "Usage" heading."""
    usage = text.split("\nUsage\n-----\n", 1)[1]
    return command_lines(["\n".join(line[4:] for line in usage.splitlines() if line.startswith("    "))])


@pytest.mark.parametrize(
    "project_name, filename",
    [("demo-tool", "README"), ("demo-tool", "README.md"), ("example", "README")],
)
def test_marked_commands_run_in_the_generated_project(in_tmp_dir, project_name, filename):
    project = generate(in_tmp_dir, project_name)
    text = (project / filename).read_text()
    plain = project_name != "example" and filename == "README"
    commands = usage_commands(text) if plain else marked_commands(text)
    assert commands, f"{filename} has no commands to run"

    python = shlex.quote(sys.executable)
    for command in commands:
        proc = subprocess.run(
            command.replace("uv run python", python),
            shell=True,
            executable="bash",
            cwd=project,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, f"{filename}: `{command}` exited {proc.returncode}\n{proc.stderr[-1500:]}"


def test_blank_tool_readme_is_the_end_user_readme(in_tmp_dir):
    readme = (generate(in_tmp_dir, "demo-tool") / "README").read_text()

    # build.sh replaces TOOL_VERSION in README when it packs the tarball.
    assert readme.splitlines()[0].endswith("- version TOOL_VERSION")
    assert "NGArgumentParser Framework" not in readme
    assert BADGE not in readme
    assert "{TOOL_" not in readme


def test_blank_tool_readme_md_is_the_developer_note(in_tmp_dir):
    project = generate(in_tmp_dir, "demo-tool")
    readme_md = (project / "README.md").read_text()

    assert BADGE in readme_md
    assert "{TOOL_" not in readme_md
    assert readme_md != (project / "README").read_text()


def test_example_writes_one_walkthrough_to_both_files(in_tmp_dir):
    project = generate(in_tmp_dir, "example")
    readme = (project / "README").read_text()
    readme_md = (project / "README.md").read_text()

    assert "NGArgumentParser Framework" not in readme
    assert "{TOOL_" not in readme
    assert BADGE not in readme
    # README.md is README with the badge below the title.
    badge_line = next(line for line in readme_md.splitlines() if line.startswith(BADGE))
    assert readme_md.replace(f"\n{badge_line}\n\n", "", 1) == readme
