"""preprocess reads and checks the whole command line before it creates any folder.

The -o check used to create the folder layout while argparse was still reading the
command line, and --params-dir/--inputs-dir required their folders to exist when read.
So a command that then failed left folders behind, the same flags worked or failed
depending on their order, and -o pointing at a file printed errors and exited 0.
"""

import json
import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PROBE = """\
import contextlib, io, json, sys
sys.path.insert(0, "src")
from DemoArgumentParser import DemoArgumentParser
out, err, code, ns = io.StringIO(), io.StringIO(), 0, None
try:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        ns = DemoArgumentParser().parse_args(json.loads(sys.argv[1]))
except SystemExit as e:
    code = e.code
args = {k: [type(v).__name__, str(v)] for k, v in vars(ns).items()} if ns else None
print(json.dumps({"code": code, "stdout": out.getvalue(), "stderr": err.getvalue(), "args": args}))
"""

LAYOUT = ["predict-inputs/data", "predict-inputs/params", "predict-outputs", "aggregate", "results"]


@pytest.fixture
def project(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    root = in_tmp_dir / "demo"
    (root / "in.json").write_text("{}\n")
    (root / "probe_parse.py").write_text(PROBE)
    return root


def preprocess(project, *argv):
    proc = subprocess.run(
        [sys.executable, "probe_parse.py", json.dumps(["preprocess", *argv])],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_failed_command_leaves_no_folders(project):
    out = preprocess(project, "-o", "out")  # -j is missing

    assert out["code"] == 2
    assert "--input-json" in out["stderr"]
    assert not (project / "out").exists()


@pytest.mark.parametrize("order", ["output-first", "params-first"])
def test_flag_order_does_not_matter(project, order):
    flags = {
        "output-first": ["-o", "out", "--params-dir", "out/predict-inputs/params"],
        "params-first": ["--params-dir", "out/predict-inputs/params", "-o", "out"],
    }[order]

    out = preprocess(project, "-j", "in.json", *flags)

    assert out["code"] == 0, out["stderr"]
    assert out["args"]["preprocess_parameters_dir"][1] == "out/predict-inputs/params"


def test_output_dir_that_is_a_file_is_rejected_before_anything_is_created(project):
    (project / "notadir.txt").write_text("hi\n")

    out = preprocess(project, "-j", "in.json", "-o", "notadir.txt")

    assert out["code"] == 2
    assert "'notadir.txt' exists and is not a directory." in out["stderr"]
    assert "Creating directory" not in out["stdout"]


def test_valid_run_creates_the_layout_and_sets_the_same_defaults(project):
    out = preprocess(project, "-j", "in.json", "-o", "out")

    assert out["code"] == 0, out["stderr"]
    for rel in LAYOUT:
        assert (project / "out" / rel).is_dir(), rel
    assert out["args"]["preprocess_inputs_dir"] == ["str", "out/predict-inputs/data"]
    assert out["args"]["preprocess_parameters_dir"] == ["str", "out/predict-inputs/params"]
    assert "Creating directory structure under: out" in out["stdout"]


def test_params_dir_outside_output_must_exist(project):
    out = preprocess(project, "-j", "in.json", "-o", "out", "--params-dir", "elsewhere/params")

    assert out["code"] == 2
    assert "argument --params-dir: 'elsewhere/params' does not exist." in out["stderr"]


def test_existing_params_dir_outside_output_is_accepted(project):
    (project / "elsewhere").mkdir()

    out = preprocess(project, "-j", "in.json", "-o", "out", "--params-dir", "elsewhere")

    assert out["code"] == 0, out["stderr"]
    assert out["args"]["preprocess_parameters_dir"] == ["str", "elsewhere"]


# A folder named with --params-dir/--inputs-dir used to reach run() as a Path,
# while the default for the same flag reached it as a str.

RUN_PROBE = """\
import json
def run(**kwargs):
    print(json.dumps({k: type(kwargs[k]).__name__ for k in ("preprocess_parameters_dir", "preprocess_inputs_dir")}))
"""


@pytest.mark.parametrize(
    "named",
    [
        [],  # control: both defaulted
        ["--params-dir", "elsewhere"],
        ["--inputs-dir", "elsewhere"],
        ["--params-dir", "out/predict-inputs/params", "--inputs-dir", "out/predict-inputs/data"],
    ],
)
def test_run_gets_str_folders_whether_named_or_defaulted(project, named):
    (project / "elsewhere").mkdir()
    (project / "src" / "preprocess.py").write_text(RUN_PROBE)

    proc = subprocess.run(
        [sys.executable, "src/run_demo.py", "preprocess", "-j", "in.json", "-o", "out", *named],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    types = json.loads(proc.stdout.strip().splitlines()[-1])
    assert types == {"preprocess_parameters_dir": "str", "preprocess_inputs_dir": "str"}


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a non-root POSIX user")
def test_folder_that_cannot_be_created_stops_the_command(project):
    locked = project / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        out = preprocess(project, "-j", "in.json", "-o", "locked/out")
    finally:
        locked.chmod(0o700)

    assert out["code"] == 2
    assert "could not create the folders under 'locked/out'" in out["stderr"]


def test_dependency_defaults_follow_the_dependency_layout(project):
    # With a dependency in paths.py the layout is as before (<out>/<dep>/ and
    # <out>/<APP_NAME>/), and the defaults point at the tool's own tree, which exists.
    # They used to point at out/predict-inputs/, which this layout never creates.
    cli.add_deps_to_paths(str(project / "paths.py"), ["tcell-class-i"])
    paths = project / "paths.py"
    paths.write_text(paths.read_text().replace("tcell_class_i_path=None", f"tcell_class_i_path='{project}'"))

    out = preprocess(project, "-j", "in.json", "-o", "out")

    assert out["code"] == 0, out["stderr"]
    # `cli deps add tcell-class-i` writes the header "Tcell Class I", so its folder is tcell_class_i.
    assert (project / "out" / "tcell_class_i" / "predict-inputs" / "data").is_dir()
    assert (project / "out" / "demo" / "results").is_dir()
    assert not (project / "out" / "predict-inputs").exists()
    assert out["args"]["preprocess_inputs_dir"] == ["str", "out/demo/predict-inputs/data"]
    assert out["args"]["preprocess_parameters_dir"] == ["str", "out/demo/predict-inputs/params"]
    assert (project / "out" / "demo" / "predict-inputs" / "data").is_dir()
    assert (project / "out" / "demo" / "predict-inputs" / "params").is_dir()


@pytest.mark.parametrize(
    "flag, value",
    [
        ("--params-dir", "../typo-dir"),  # outside -o, missing
        ("--inputs-dir", "../typo-dir"),
        ("--params-dir", "out/custom-params"),  # inside -o, but not a folder the layout creates
    ],
)
def test_missing_named_folder_leaves_no_folders(project, flag, value):
    out = preprocess(project, "-j", "in.json", "-o", "out", flag, value)

    assert out["code"] == 2
    assert f"argument {flag}: '{value}' does not exist." in out["stderr"]
    assert not (project / "out").exists()
    assert "Creating directory" not in out["stdout"]


def test_layout_folder_of_a_dependency_is_accepted(project):
    # The layout list must follow the dependency layout too, not only the default one.
    cli.add_deps_to_paths(str(project / "paths.py"), ["tcell-class-i"])
    paths = project / "paths.py"
    paths.write_text(paths.read_text().replace("tcell_class_i_path=None", f"tcell_class_i_path='{project}'"))

    out = preprocess(project, "-j", "in.json", "-o", "out", "--params-dir", "out/tcell_class_i/predict-inputs/params")

    assert out["code"] == 0, out["stderr"]
    assert out["args"]["preprocess_parameters_dir"][1] == "out/tcell_class_i/predict-inputs/params"


# preprocess -o was described as "prediction result output directory", while it is
# the folder of the whole job: preprocess puts its folder layout there, and the
# tool's preprocess.py writes job_descriptions.json there.


def test_output_dir_help_describes_the_job_folder(project):
    proc = subprocess.run(
        [sys.executable, "src/run_demo.py", "preprocess", "--help"], cwd=project, capture_output=True, text=True
    )

    assert proc.returncode == 0, proc.stderr
    help_text = " ".join(proc.stdout.split())
    # Python 3.13 lists the flags as "--output-dir, -o OUTPUT_DIR", older ones repeat the metavar.
    assert (
        "-o OUTPUT_DIR Output folder of the job: job_descriptions.json and the "
        "predict-inputs/ folder layout go here (created if missing)."
    ) in help_text
    assert "prediction result output directory" not in help_text
