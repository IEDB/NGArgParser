"""./configure and `cli deps add` accept and refuse the same dependency names.

`cli deps add` refused ld_library, app, uv and other names whose keys the
system, the tool or another program reads, but ./configure took the same names
from a paths.py edited by hand: ld_library_path made the setup script export
LD_LIBRARY_PATH, and app_path replaced the tool's own APP_VENV and APP_ENTRY.
Both now read the rule from one module, dep_names.py.
"""

import importlib.util
import os
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli
from ngargparser.dep_names import invalid_dep_name

# Names as paths.py spells them (<name>_path), some cli deps add takes and some it refuses.
NAMES = [
    "pepx",
    "net_mhc",
    "tool2",
    "home",
    "app",
    "ld_library",
    "dyld_library",
    "uv",
    "python3",
    "node",
    "pyspark",
    "cuda",
    "x_path",
    "café",
]


def configure(project):
    return subprocess.run(
        [sys.executable, "src/core/configure.py"], cwd=project, capture_output=True, text=True, timeout=60
    )


def test_configure_accepts_and_refuses_the_names_cli_deps_add_does(scaffolded_project, tmp_path, capsys, strip_ansi):
    paths = scaffolded_project / "paths.py"
    env_before = (scaffolded_project / ".env").read_bytes()
    seen = {}
    for name in NAMES:
        if paths.exists():
            paths.unlink()
        capsys.readouterr()
        added = cli.deps_command(Namespace(deps_action="add", names=[name]))
        said = strip_ansi("".join(capsys.readouterr()))
        reason = invalid_dep_name(name)
        if reason:
            assert f"✗ Not added: {reason}" in said, name

        # The same name written into paths.py by hand, with its path set.
        paths.write_text(f"{name}_path = {str(tmp_path / 'deps' / name)!r}\n", encoding="utf-8")
        run = configure(scaffolded_project)
        script = scaffolded_project / f"setup_{name}_env.sh"
        if run.returncode == 0:
            assert script.is_file(), name
            script.unlink()
            (scaffolded_project / ".env").write_bytes(env_before)
        else:
            assert strip_ansi(run.stdout).splitlines() == [
                f"✗ paths.py declares {name}, a name cli deps refuses: {reason}",
                "  Nothing was changed.",
            ], name
            assert (scaffolded_project / ".env").read_bytes() == env_before, name
            assert not script.exists(), name
        seen[name] = (added == 0, run.returncode == 0)

    assert {name: by_cli for name, (by_cli, _by_configure) in seen.items()} == {
        name: by_configure for name, (_by_cli, by_configure) in seen.items()
    }
    # Both kinds are in the table.
    assert {by_cli for by_cli, _by_configure in seen.values()} == {True, False}


@pytest.mark.parametrize("where", ["templates", "a tool's src/core"])
def test_configure_reads_the_rule_from_the_dep_names_file_next_to_it(scaffolded_project, where):
    # ngargparser's own templates/configure.py has no dep_names.py beside it: it reads the package's.
    path = cli.TEMPLATE_DIR / "configure.py" if where == "templates" else scaffolded_project / "src/core/configure.py"
    spec = importlib.util.spec_from_file_location(f"configure_{where.split()[0]}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    expected = cli.NGPARSER_DIR / "dep_names.py" if where == "templates" else path.parent / "dep_names.py"
    assert os.path.samefile(module.dep_names.__file__, expected)
    assert module.detect_dependency_tools({"pepx_path": "/opt/pepx", "pepx_lib_path": "/opt/lib"}) == {"pepx": "pepx"}
    with pytest.raises(SystemExit):
        module.detect_dependency_tools({"app_path": "/opt/app"})
