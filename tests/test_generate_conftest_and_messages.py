"""`cli generate`: the starter tests, a fresh clone's `uv run pytest`, the message for a
tool generated inside another, and a substitution that cannot be written.

* tests/test_parser.py wrote {"peptides": [...]}, while the README's Quickstart writes
  {"peptide": [...]}.
* A fresh clone has no .env (it is in .gitignore), so `uv run pytest` stopped with
  "This tool has not been configured yet". A new tool's tests/conftest.py now runs
  ./configure first when .env is missing, and stops with ./configure's own message
  when that fails.
* Both forms of the "generated inside a tool" message end with the command to run.
* replace_text_in_place printed "An error occurred" and went on, leaving a file half
  substituted (cut short on a full disk) in a tool generate called a success.
"""

import errno
import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PACKAGE_ROOT = os.path.dirname(os.path.dirname(cli.__file__))


def run_pytest(project):
    """`pytest` in the tool, with the test interpreter, as `uv run pytest` runs it there."""
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ROOT", "APP_NAME", "PYTEST_ADDOPTS")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"],
        cwd=project,
        env={**env, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=120,
    )


# --- 7a. The starter test's input ----------------------------------------------------------


def test_the_starter_test_writes_the_quickstarts_input(scaffolded_project):
    test = (scaffolded_project / "tests" / "test_parser.py").read_text()

    assert """peptides.write_text('{"peptide": ["ADMGHLKY"]}')""" in test
    assert '"peptides"' not in test


# --- 7c. A fresh clone's tests -----------------------------------------------------------------


def test_pytest_in_a_fresh_clone_configures_the_tool_first(scaffolded_project, strip_ansi):
    project = scaffolded_project
    assert (project / "tests" / "conftest.py").is_file()
    (project / ".env").unlink()  # a fresh clone: .env is in .gitignore

    proc = run_pytest(project)

    output = strip_ansi(proc.stdout + proc.stderr)
    assert proc.returncode == 0, output
    assert "2 passed" in output and "not been configured" not in output
    assert f"APP_ROOT={project}\n" in (project / ".env").read_text()

    # The next run finds .env, and leaves it as it is.
    written = (project / ".env").read_text()
    assert run_pytest(project).returncode == 0
    assert (project / ".env").read_text() == written


def test_pytest_in_a_fresh_clone_whose_dependency_path_is_unset_says_what_to_do(scaffolded_project, strip_ansi):
    project = scaffolded_project
    cli.add_deps_to_paths(str(project / "paths.py"), ["pepx"])
    (project / ".env").unlink()

    proc = run_pytest(project)

    output = strip_ansi(proc.stdout + proc.stderr)
    assert proc.returncode == 1, output
    assert "Exit: This tool has no .env, and ./configure, which writes it, failed:\n" in output
    assert (
        "✗ pepx_path is None in paths.py. Set it with './configure --pepx-path=<path>', or run "
        "'cli deps remove pepx' to drop this dependency."
    ) in output
    assert "Traceback" not in output and "INTERNALERROR" not in output
    assert not (project / ".env").exists()


# --- 7d. Generating inside a tool --------------------------------------------------------------


def test_generate_inside_a_tool_ends_with_the_command_to_run(in_tmp_dir, strip_ansi, capsys, monkeypatch):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    capsys.readouterr()

    for folder, where in (
        (in_tmp_dir / "demo", "This folder is"),
        (in_tmp_dir / "demo" / "src", "This folder is inside"),
    ):
        monkeypatch.chdir(folder)
        assert cli.startapp_command(Namespace(project_name="inner")) == 1
        assert strip_ansi(capsys.readouterr().err) == (
            f"✗ {where} the ngargparser tool {in_tmp_dir / 'demo'} (it has a [tool.ngargparser] table in "
            "pyproject.toml). A tool generated here would ship in that tool's tarball, so nothing was created. "
            f"Run cli generate in a folder outside it: cd {in_tmp_dir} && cli generate inner\n"
        )


# --- replace_text_in_place: a write that fails --------------------------------------------------


@pytest.mark.skipif(sys.platform != "linux", reason="needs RLIMIT_FSIZE as Linux applies it")
def test_a_substitution_cut_short_by_a_full_disk_raises_and_leaves_the_file_whole(tmp_path):
    target = tmp_path / "template.txt"
    target.write_text("NAME " * 1000)  # 5000 bytes; 13000 once substituted
    code = (
        "import resource, sys\n"
        "from ngargparser import cli\n"
        "resource.setrlimit(resource.RLIMIT_FSIZE, (6000, 6000))\n"
        f"cli.replace_text_in_place({str(target)!r}, 'NAME', 'a-longer-name')\n"
    )
    env = {**os.environ, "PYTHONPATH": PACKAGE_ROOT, "PYTHONDONTWRITEBYTECODE": "1"}

    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)

    assert done.returncode == 1
    assert f"OSError: [Errno 27] File too large: {str(target)!r}" in done.stderr
    assert "An error occurred" not in done.stdout
    assert target.read_text() == "NAME " * 1000  # was cut to 6000 bytes, half substituted
    assert [p.name for p in tmp_path.iterdir()] == ["template.txt"]  # no temporary file left


def test_generate_stops_and_cleans_up_when_a_substitution_cannot_be_written(
    in_tmp_dir, strip_ansi, capsys, monkeypatch
):
    real_replace = os.replace

    def full_disk_for_the_entry_script(source, destination):
        if str(destination).endswith("run_demo.py"):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", full_disk_for_the_entry_script)

    assert cli.startapp_command(Namespace(project_name="demo")) == 1

    out = strip_ansi("".join(capsys.readouterr()))
    assert "✗ Error: [Errno 28] No space left on device: 'demo/src/run_demo.py'\n" in out
    assert "  └ Cleaned up the partial tool 'demo'.\n" in out
    assert "is ready" not in out and "An error occurred" not in out
    assert not (in_tmp_dir / "demo").exists()
