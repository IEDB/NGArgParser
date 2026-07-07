from argparse import Namespace

from ngargparser import cli


def test_create_project_structure_builds_expected_tree(in_tmp_dir):
    rc = cli.create_project_structure("demo")
    assert not rc
    root = in_tmp_dir / "demo"

    expected = [
        "src/core/NGArgumentParser.py",
        "src/core/core_validators.py",
        "src/core/result_writer.py",
        "src/core/set_pythonpath.py",
        "src/core/configure.py",
        "src/core/__init__.py",
        "src/validators.py",
        "src/run_demo.py",
        "src/DemoArgumentParser.py",
        "src/preprocess.py",
        "src/postprocess.py",
        "scripts/core/build.sh",
        "scripts/build.conf",
        "scripts/hooks.sh",
        "Makefile",
        "configure",
        "deploy/install.sh",
        "pyproject.toml",
        "README.md",
    ]
    for rel in expected:
        assert (root / rel).exists(), f"missing scaffolded file: {rel}"


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


def test_startapp_writes_paths_and_env(in_tmp_dir):
    rc = cli.startapp_command(Namespace(project_name="demo")) or 0
    assert rc == 0
    root = in_tmp_dir / "demo"
    assert (root / "paths.py").exists()
    env = (root / ".env").read_text()
    assert "APP_NAME=demo" in env
    assert "APP_ROOT=" in env
