"""The code `cli generate` writes must run as its comments say.

- run_<name>.py imported preprocess, postprocess and validators before
  core.set_pythonpath, so a libs/ module imported in one of them failed with
  ModuleNotFoundError. validators.py itself imported it after `import argparse`.
- An unedited predict printed nothing and exited 0, which looks like success.
- The predict example read no input, while predict takes -j or -t.
- The example in the src/validators.py header, pasted as-is, redeclared the
  framework's --inputs-dir.
- pyproject.toml had no pytest settings, so a test importing the tool's parser
  failed with ModuleNotFoundError.
- An unedited preprocess or postprocess printed nothing and exited 0, and
  preprocess wrote no job_descriptions.json.
- predict had no help text, so the top-level -h showed a blank next to it.
- run_<name>.py called load_dotenv() itself, after the framework had loaded the
  tool's .env. In a build copy, which has no .env, that call found the source
  tool's .env two folders up, so the copy ran with values it does not ship.
- src/validators.py re-exported validate_preprocess_dir, which creates and prints
  the preprocess folders while the command line is read.
- src/validators.py imported get_dependencies_from_paths and
  create_directory_structure_for_dependencies under "DO NOT MODIFY", though a
  tool's validators never use them (the framework calls them from core).
- The generated parser linked Python's argparse documentation, but not the
  parser cookbook, which has the recipes for that file.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from ngargparser import cli


@pytest.fixture
def demo(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    root = in_tmp_dir / "demo"
    (root / "in.json").write_text("{}\n")
    (root / "in.tsv").write_text("peptide\nAAK\n")
    return root


@pytest.fixture
def example(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="example"))
    return in_tmp_dir / "aa-counter"


def run(project, *args):
    return subprocess.run([sys.executable, *args], cwd=project, capture_output=True, text=True, timeout=60)


def add_libs_module(project):
    (project / "libs").mkdir()
    (project / "libs" / "mylib.py").write_text("VALUE = 1\n")


def insert_line(path, after, line):
    text = path.read_text()
    assert after in text
    path.write_text(text.replace(after, after + line, 1))


# An unedited predict says so and exits 1.


@pytest.mark.parametrize("input_args", [("-j", "in.json"), ("-t", "in.tsv")], ids=["json", "tsv"])
def test_unedited_predict_says_it_is_not_implemented(demo, input_args):
    result = run(demo, "src/run_demo.py", "predict", *input_args)

    assert result.returncode == 1
    assert result.stderr == "predict is not implemented yet: edit src/run_demo.py\n"
    assert result.stdout == ""


@pytest.mark.parametrize(
    "argv, message",
    [
        (["preprocess", "-j", "in.json", "-o", "out"], "preprocess is not implemented yet: edit src/preprocess.py\n"),
        (["postprocess", "-j", "in.json"], "postprocess is not implemented yet: edit src/postprocess.py\n"),
    ],
    ids=["preprocess", "postprocess"],
)
def test_unedited_preprocess_and_postprocess_say_they_are_not_implemented(demo, argv, message):
    result = run(demo, "src/run_demo.py", *argv)

    assert result.returncode == 1
    assert result.stderr == message


def test_unedited_preprocess_still_creates_its_folders_first(demo):
    # Control: the framework creates the layout before run() starts.
    run(demo, "src/run_demo.py", "preprocess", "-j", "in.json", "-o", "out")

    assert (demo / "out" / "predict-inputs" / "params").is_dir()


def test_top_level_help_describes_every_subcommand(demo):
    result = run(demo, "src/run_demo.py", "-h")

    assert result.returncode == 0, result.stderr
    listed = {}
    for line in result.stdout.split("subcommands:", 1)[1].splitlines():
        name, _, text = line.strip().partition(" ")
        if name in ("predict", "preprocess", "postprocess"):
            listed[name] = text.strip()
    assert listed == {
        "predict": "Run one prediction.",
        "preprocess": "Preprocess jobs.",
        "postprocess": "Postprocess jobs.",
    }


@pytest.mark.parametrize("subcommand", [[], ["predict"], ["preprocess"], ["postprocess"]], ids=str)
def test_unedited_tool_help_screens_still_exit_0(demo, subcommand):
    # Control: the build's startup check runs each of these.
    result = run(demo, "src/run_demo.py", *subcommand, "--help")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: ")


def test_predict_example_reads_either_input(demo):
    # predict takes -j or -t; an example reading only args.input_json fails with TypeError on -t.
    text = (demo / "src" / "run_demo.py").read_text()
    branch = text.split("if args.subcommand == 'predict':", 1)[1].split("elif args.subcommand == 'preprocess':", 1)[0]

    assert "args.input_json or args.input_tsv" in branch


# A libs/ module can be imported anywhere in the tool's own files.


@pytest.mark.parametrize(
    "path, after",
    [("src/validators.py", "import argparse\n"), ("src/preprocess.py", ""), ("src/postprocess.py", "")],
    ids=["validators", "preprocess", "postprocess"],
)
def test_libs_module_imports_from_the_tools_files(demo, path, after):
    add_libs_module(demo)
    insert_line(demo / path, after, "import mylib\n")

    result = run(demo, "src/run_demo.py", "--help")

    assert result.returncode == 0, result.stderr


def test_libs_module_imports_from_validators_when_a_test_imports_the_parser(demo):
    # A test imports the parser without the run script, so validators.py sets the path itself.
    add_libs_module(demo)
    insert_line(demo / "src" / "validators.py", "import argparse\n", "import mylib\n")

    result = run(
        demo, "-c", "import sys; sys.path.insert(0, 'src'); import DemoArgumentParser as m; m.DemoArgumentParser()"
    )

    assert result.returncode == 0, result.stderr


def test_example_app_libs_module_imports_from_preprocess(example):
    add_libs_module(example)
    insert_line(example / "src" / "preprocess.py", "import json\n", "import mylib\n")

    result = run(example, "src/run_aa_counter.py", "--help")

    assert result.returncode == 0, result.stderr


# The src/validators.py header example works when pasted into the parser.


def test_validators_header_example_works_when_pasted(demo):
    header = (demo / "src" / "validators.py").read_text().split("# ====", 1)[0]
    example = header.split("# ex) ", 1)[1]
    code = "\n".join("        " + line.lstrip("#").strip() for line in example.splitlines())
    (demo / "src" / "DemoArgumentParser.py").write_text(
        (demo / "src" / "DemoArgumentParser.py").read_text() + code + "\n"
    )

    result = run(demo, "src/run_demo.py", "predict", "--help")

    assert result.returncode == 0, result.stderr
    option = code.split('"', 2)[1]
    assert option in result.stdout


# pytest finds the tool's modules in src/.


def test_pytest_imports_the_tools_parser(demo):
    (demo / "tests").mkdir(exist_ok=True)  # cli generate writes tests/test_parser.py
    (demo / "tests" / "test_parser.py").write_text(
        "from DemoArgumentParser import DemoArgumentParser\n"
        "\n"
        "\n"
        "def test_predict_parses():\n"
        '    args = DemoArgumentParser().parse_args(["predict", "-j", "in.json", "-f", "json"])\n'
        '    assert args.output_format == "json"\n'
    )

    result = run(demo, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


# The run script reads only the tool's own .env, which the framework loads.


def print_env_in_predict(project, key):
    script = project / "src" / "run_demo.py"
    text = script.read_text()
    start = text.index('        sys.exit(f"predict is not implemented yet')
    end = text.index("\n", start)
    script.write_text(text[:start] + f'        print("{key}=" + str(os.environ.get("{key}")))' + text[end:])


def test_predict_sees_the_tools_env(demo):
    # Control: the framework loads .env before the run script reads it.
    with open(demo / ".env", "a") as f:
        f.write("DEMO_SETTING=from-env\n")
    print_env_in_predict(demo, "DEMO_SETTING")

    result = run(demo, "src/run_demo.py", "predict", "-j", "in.json")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "DEMO_SETTING=from-env\n"


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="build.sh needs bash and git")
def test_build_copy_does_not_read_the_source_env(demo, run_build, fake_venv):
    with open(demo / ".env", "a") as f:
        f.write("DEMO_SETTING=from-env\n")
    print_env_in_predict(demo, "DEMO_SETTING")
    fake_venv(demo)
    staged = run_build(demo).staged
    assert not (staged / ".env").exists()

    # APP_ROOT set to the copy, as the build's startup check runs it.
    result = subprocess.run(
        [sys.executable, "src/run_demo.py", "predict", "-j", str(demo / "in.json")],
        cwd=staged,
        env={**os.environ, "APP_ROOT": str(staged)},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "DEMO_SETTING=None\n"


# validate_preprocess_dir is legacy: kept in core, no longer re-exported.


def test_validators_does_not_reexport_validate_preprocess_dir(demo):
    result = run(
        demo,
        "-c",
        "import sys; sys.path.insert(0, 'src'); import validators; print(hasattr(validators, 'validate_preprocess_dir'))",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "False\n"


def test_older_validators_that_imports_validate_preprocess_dir_still_loads(demo):
    # Control: a tool generated earlier keeps its own src/validators.py, which imports it by name.
    insert_line(
        demo / "src" / "validators.py",
        "import argparse\n",
        "from core.core_validators import validate_preprocess_dir\n",
    )

    result = run(demo, "src/run_demo.py", "--help")

    assert result.returncode == 0, result.stderr


# src/validators.py imports only the built-in validators, the ones the cookbook
# lists; the dependency helpers stay in core.core_validators.

BUILT_IN_VALIDATORS = ["validate_directory", "validate_directory_given_filename", "validate_file"]


def validators_from_core(project):
    """The functions src/validators.py takes from core.core_validators, sorted."""
    result = run(
        project,
        "-c",
        "import inspect, json, sys; sys.path.insert(0, 'src'); import validators, core.core_validators as cv; "
        "print(json.dumps(sorted(n for n, f in vars(validators).items() "
        "if inspect.isfunction(f) and getattr(cv, n, None) is f)))",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("tool", ["demo", "example"])
def test_validators_imports_only_the_built_in_validators(request, tool):
    assert validators_from_core(request.getfixturevalue(tool)) == BUILT_IN_VALIDATORS


def test_validators_imports_what_the_cookbook_lists(demo):
    cookbook = (Path(__file__).resolve().parents[1] / "docs" / "parser-cookbook.md").read_text()
    block = cookbook.split("<!-- example: imports -->", 1)[1].split("```python\n", 1)[1].split("```", 1)[0]

    assert sorted(re.findall(r"^\s+(\w+),", block, re.MULTILINE)) == validators_from_core(demo)


def test_older_validators_that_imports_the_dependency_helpers_still_loads(demo):
    # Control: a tool generated earlier keeps its own src/validators.py, which
    # imports them from core by name; preprocess still creates its folders.
    insert_line(
        demo / "src" / "validators.py",
        "import argparse\n",
        "from core.core_validators import get_dependencies_from_paths, create_directory_structure_for_dependencies\n",
    )

    assert run(demo, "src/run_demo.py", "--help").returncode == 0
    result = run(demo, "src/run_demo.py", "preprocess", "-j", "in.json", "-o", "out")
    assert result.returncode == 1  # the unedited run() says it is not implemented
    assert (demo / "out" / "predict-inputs" / "params").is_dir(), result.stderr


# The generated parser points at the cookbook, next to the argparse link.

COOKBOOK_URL = (
    "https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser/-/blob/master/"
    "docs/parser-cookbook.md"
)


def test_generated_parser_links_the_cookbook(demo):
    parser = (demo / "src" / "DemoArgumentParser.py").read_text()
    readme_root = (Path(cli.__file__).resolve().parent / "templates" / "README.md").read_text()

    assert COOKBOOK_URL in parser
    assert parser.index("https://docs.python.org/3/library/argparse.html") < parser.index(COOKBOOK_URL)
    # The same repository URL as the generated README.md gives.
    assert COOKBOOK_URL.split("/-/blob/", 1)[0] in readme_root
    # The page it names exists in this repository.
    assert (Path(__file__).resolve().parents[1] / "docs" / "parser-cookbook.md").is_file()
