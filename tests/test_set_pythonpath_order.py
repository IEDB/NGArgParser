"""A tool's own src/ modules win over modules of the same name in libs/.

core.set_pythonpath puts libs/ and every package folder in it on sys.path, so
code in libs/ can import its own modules by their plain name. The build adds an
__init__.py to every fetched folder, so a fetched tool's src/preprocess.py or a
library's examples/validators.py became importable as `preprocess` or
`validators`. With those folders ahead of src/ on sys.path, the tool's
`preprocess` subcommand ran the other tool's preprocess.run, and a library's
validators.py broke every help screen. Ahead of the standard library, a fetched
folder named csv or json replaced that module. These tests lay out libs/ the way
the build leaves it and run the tool.
"""

import json
import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

LIB_FILES = {
    # another tool fetched whole: its src/ holds the same module names
    "othertool/src/preprocess.py": 'def run(**kwargs):\n    print("othertool preprocess.run")\n',
    # deeper, and next to a module that only libs/ has
    "deep/a/b/postprocess.py": 'def run(**kwargs):\n    print("deep postprocess.run")\n',
    "deep/a/helper_mod.py": 'VALUE = "from libs"\n',
}
BROKEN_VALIDATORS = {
    "helperlib/examples/validators.py": 'raise ImportError("libs/helperlib/examples/validators.py was imported")\n',
}

PATH_PROBE = """\
import json, sys
import core.set_pythonpath
import helper_mod, postprocess, preprocess, validators
print(json.dumps({
    "sys_path": sys.path,
    "files": {m.__name__: m.__file__ for m in (preprocess, validators, postprocess)},
    "helper": helper_mod.VALUE,
}))
"""


@pytest.fixture
def tool(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    project = in_tmp_dir / "demo"
    (project / "src" / "preprocess.py").write_text('def run(**kwargs):\n    print("demo preprocess.run")\n')
    return project


def lay_out_libs(project, files):
    """Write `files` under libs/ with an __init__.py in every folder, as the build leaves them."""
    libs = project / "libs"
    for rel, text in files.items():
        path = libs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for folder in [p for p in libs.rglob("*") if p.is_dir()]:
        (folder / "__init__.py").touch()
    return libs


def sorted_walk(libs):
    """The package folders under `libs`, as a top-down walk that lists folders in sorted order reaches them."""
    walked = []
    for root, dirs, files in os.walk(libs):
        dirs.sort()
        if "__init__.py" in files and root != str(libs):
            walked.append(root)
    return walked


def run(project, *args):
    return subprocess.run(
        [sys.executable, *args],
        cwd=project,
        env={k: v for k, v in os.environ.items() if k not in ("APP_ROOT", "APP_NAME")},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_help_screens_start_when_libs_holds_a_validators_module(tool):
    lay_out_libs(tool, {**LIB_FILES, **BROKEN_VALIDATORS})

    for args in ([], ["predict"], ["preprocess"], ["postprocess"]):
        proc = run(tool, "src/run_demo.py", *args, "--help")
        assert proc.returncode == 0, (args, proc.stderr)
        assert "was imported" not in proc.stderr


def test_preprocess_runs_the_tools_own_preprocess(tool, tmp_path):
    lay_out_libs(tool, LIB_FILES)
    job = tmp_path / "job.json"
    job.write_text("{}\n")

    proc = run(tool, "src/run_demo.py", "preprocess", "-j", str(job), "-o", str(tmp_path / "out"))

    assert proc.returncode == 0, proc.stderr
    assert "demo preprocess.run" in proc.stdout
    assert "othertool" not in proc.stdout


def test_src_comes_first_and_libs_folders_come_last_in_their_order(tool):
    libs = lay_out_libs(tool, {**LIB_FILES, **BROKEN_VALIDATORS})
    (tool / "src" / "probe_path.py").write_text(PATH_PROBE)

    proc = run(tool, "src/probe_path.py")

    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    src = str(tool / "src")
    assert out["files"] == {
        name: os.path.join(src, f"{name}.py") for name in ("preprocess", "validators", "postprocess")
    }
    assert out["helper"] == "from libs"  # a module only libs/ has still imports
    # The libs/ folders come last, after the standard library and site-packages:
    # the folders a sorted, top-down walk reaches last first, libs/ itself last.
    expected = list(reversed(sorted_walk(libs))) + [str(libs)]
    path = out["sys_path"]
    assert path[0] == src
    assert path[-len(expected) :] == expected
    assert not [entry for entry in path[: -len(expected)] if entry.startswith(str(libs))]


# The build adds an __init__.py to every fetched folder, and each of those folders
# went on sys.path ahead of the standard library: a fetched tests/fixtures/csv/
# became `csv`, docs/_build/html/ became `html`, and `import xml.etree` failed.

SHADOWING_LIBS = {
    # a test fixture, built docs, a folder deep in a repo, and one at the top of libs/
    "somerepo/tests/fixtures/csv/__init__.py": 'raise ImportError("libs/somerepo/tests/fixtures/csv was imported")\n',
    "somerepo/docs/_build/html/__init__.py": 'raise ImportError("libs/somerepo/docs/_build/html was imported")\n',
    "deep/a/b/xml/__init__.py": "",
    "json/__init__.py": 'raise ImportError("libs/json was imported")\n',
    # an installed package: pytest is in the interpreter that runs these tests
    "somerepo/vendor/pytest/__init__.py": 'raise ImportError("libs/somerepo/vendor/pytest was imported")\n',
    # a real package in libs/, whose modules import each other by their plain name
    "otherrepo/pkg/mod_a.py": "from mod_b import VALUE\n",
    "otherrepo/pkg/mod_b.py": 'VALUE = "otherrepo ok"\n',
}

SHADOW_PROBE = """\
import importlib, json
import core.set_pythonpath
files = {name: importlib.import_module(name).__file__ for name in ("csv", "html", "xml.etree.ElementTree", "json", "pytest")}
import mod_a, otherrepo.pkg.mod_b
print(json.dumps({"files": files, "plain": mod_a.VALUE, "package": otherrepo.pkg.mod_b.__file__}))
"""


def test_libs_folders_never_shadow_the_standard_library_or_installed_packages(tool):
    libs = lay_out_libs(tool, SHADOWING_LIBS)
    (tool / "src" / "probe_shadow.py").write_text(SHADOW_PROBE)

    proc = run(tool, "src/probe_shadow.py")

    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert not [name for name, path in out["files"].items() if path.startswith(str(libs))], out["files"]
    assert out["plain"] == "otherrepo ok"
    assert out["package"] == str(libs / "otherrepo" / "pkg" / "mod_b.py")
    # The framework imports json, so a libs/json/ used to stop every help screen.
    for args in ([], ["predict"]):
        help_screen = run(tool, "src/run_demo.py", *args, "--help")
        assert help_screen.returncode == 0, (args, help_screen.stderr)


# os.walk lists folders in the order the file system returns them, which is not
# the same on every host (ext4 hashes names, tmpfs returns the newest first). Two
# libs/ folders with a module of the same name (aaa/util.py, zzz/util.py) gave
# `import util` either one.

UTIL_PROBE = """\
import json, sys
import core.set_pythonpath
import util
print(json.dumps({"util": util.WHO, "sys_path": sys.path}))
"""


def test_libs_folders_come_in_the_same_order_on_every_host(tool):
    # Made in an order that is neither sorted nor reversed, as a fetch might leave them.
    names = ["mmm", "zzz", "bbb", "aaa", "yyy", "ccc", "xxx", "nnn"]
    lay_out_libs(tool, {f"{name}/util.py": f'WHO = "{name}"\n' for name in names})
    libs = tool / "libs"
    (tool / "src" / "probe_util.py").write_text(UTIL_PROBE)

    proc = run(tool, "src/probe_util.py")

    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    # A sorted walk, then the folders it reaches last come first: zzz's util wins.
    expected = [str(libs / name) for name in sorted(names, reverse=True)] + [str(libs)]
    assert out["sys_path"][-len(expected) :] == expected
    assert out["util"] == "zzz"
