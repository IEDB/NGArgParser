"""Every pasteable Python example in README.md must work when pasted into a fresh tool.

A newcomer generates a tool and copies README examples into it. The examples drifted
from the template: one reused a short flag the template already owns (-t), and the
validator example failed on the template's missing final newline and missing
`import argparse`. Each example is marked in README.md with a comment on the line
before its fence, `<!-- example: <kind> -->`, and is run here the way it is pasted:

  class       a whole parser module, saved as src/ExampleArgumentParser.py
  self        statements for a parser's __init__, run with `self` set to the
              generated parser, in that parser module's own namespace
  validators  appended to the generated src/validators.py
  imports     run as-is in the project
"""

import ast
import json
import re
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from ngargparser import cli

README = Path(__file__).resolve().parent.parent / "README.md"
KINDS = ("class", "self", "validators", "imports")
MARKED = re.compile(r"^<!-- example: (\w+) -->\n```python\n(.*?)^```", re.M | re.S)


def readme_examples():
    text = README.read_text(encoding="utf-8")
    return [(m.group(1), text.count("\n", 0, m.start()) + 1, m.group(2)) for m in MARKED.finditer(text)]


EXAMPLES = readme_examples()


def defined_functions(code):
    return [n.name for n in ast.parse(code).body if isinstance(n, ast.FunctionDef)]


VALIDATOR_FUNCS = [name for kind, _, code in EXAMPLES if kind == "validators" for name in defined_functions(code)]

PROBES = {
    "class": """
import importlib, inspect, sys
sys.path.insert(0, "src")
from core.NGArgumentParser import NGArgumentParser
module = importlib.import_module("ExampleArgumentParser")
cls = next(c for _, c in inspect.getmembers(module, inspect.isclass)
           if issubclass(c, NGArgumentParser) and c is not NGArgumentParser)
try:
    cls().parse_args(["predict", "--help"])
except SystemExit as e:
    sys.exit(e.code)
""",
    "self": """
import importlib, sys
sys.path.insert(0, "src")
module = importlib.import_module("DemoArgumentParser")
parser = module.DemoArgumentParser()
namespace = dict(vars(module), self=parser)
exec(open("example.py").read(), namespace)
try:
    parser.parse_args(["predict", "--help"])
except SystemExit as e:
    sys.exit(e.code)
""",
    "validators": """
import argparse, json, sys
sys.path.insert(0, "src")
import validators
for name in json.loads(sys.argv[1]):
    try:
        getattr(validators, name)("not-a-valid-value")
    except argparse.ArgumentTypeError:
        continue
    sys.exit(f"{name} did not raise argparse.ArgumentTypeError for a bad value")
""",
    "imports": """
import sys
sys.path.insert(0, "src")
exec(open("example.py").read(), {})
""",
}


@pytest.fixture
def project(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    return in_tmp_dir / "demo"


def paste_into_validators(project, code):
    # A paste lands after whatever the file ends with, exactly as an editor appends it.
    with open(project / "src" / "validators.py", "a", encoding="utf-8") as f:
        f.write(code)


def run_probe(project, kind, *argv):
    (project / "probe_example.py").write_text(PROBES[kind])
    return subprocess.run(
        [sys.executable, "probe_example.py", *argv],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_readme_has_examples_of_every_kind():
    found = {kind for kind, _, _ in EXAMPLES}
    assert found == set(KINDS), f"README example markers: found {sorted(found)}, expected {sorted(KINDS)}"


@pytest.mark.parametrize("kind, line, code", EXAMPLES, ids=[f"README.md:{line}-{kind}" for kind, line, _ in EXAMPLES])
def test_readme_example_works_when_pasted(project, kind, line, code):
    argv = []
    if kind == "class":
        (project / "src" / "ExampleArgumentParser.py").write_text(code)
    elif kind == "validators":
        paste_into_validators(project, code)
        argv = [json.dumps(defined_functions(code))]
    else:
        (project / "example.py").write_text(code)
        if kind == "self":
            # A parser example that uses a README validator assumes it was pasted too.
            for v_kind, _, v_code in EXAMPLES:
                if v_kind == "validators" and any(name in code for name in defined_functions(v_code)):
                    paste_into_validators(project, v_code)

    proc = run_probe(project, kind, *argv)

    assert proc.returncode == 0, f"README.md:{line} ({kind}) failed when pasted:\n{proc.stderr[-1500:]}"
