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
        ".distignore",
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


def test_generated_project_has_gitignore(in_tmp_dir):
    cli.startapp_command(Namespace(project_name="demo"))
    gitignore = in_tmp_dir / "demo" / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text()
    assert ".ngargparser/" in content
    assert "*.bak" in content


def test_startapp_writes_paths_and_env(in_tmp_dir):
    rc = cli.startapp_command(Namespace(project_name="demo")) or 0
    assert rc == 0
    root = in_tmp_dir / "demo"
    assert (root / "paths.py").exists()
    env = (root / ".env").read_text()
    assert "APP_NAME=demo" in env
    assert "APP_ROOT=" in env


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
