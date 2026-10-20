"""deploy/install.sh: the uv settings it unsets, a uv in ~/.local/bin, and the venv a command added to it gets.

UV_PROJECT_ENVIRONMENT set by a host made uv build the virtualenv elsewhere, and
`source .venv/bin/activate` then failed; the settings that change how uv
resolves reached it too. make build unsets the same names, and the two lists
are kept the same. A uv an earlier deploy installed in ~/.local/bin was
downloaded again when that folder was not on the deploy shell's PATH. No test
held that a command added to install.sh after its activate line runs with the
tool's virtualenv.
"""

import os
import re
import shutil

import pytest
from test_install_sh import (  # noqa: F401 (deployed is a fixture)
    STUB_CURL,
    STUB_UV,
    deployed,
    path_without_uv,
    run_install,
)
from test_install_uv_settings import RECORDING_UV

from ngargparser import cli

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="install.sh tests require bash (and git for the build)",
)

# The names the finding listed, beyond the four install.sh unset before.
NEW_NAMES = {"UV_PROJECT_ENVIRONMENT", "UV_RESOLUTION", "UV_PRERELEASE", "UV_EXCLUDE_NEWER", "UV_NO_SOURCES"}


def unset_names(lines):
    return [name for line in lines if line.strip().startswith("unset UV_") for name in line.split()[1:]]


def test_install_sh_unsets_the_uv_settings_the_build_unsets():
    install = (cli.TEMPLATE_DIR / "deploy" / "install.sh").read_text().splitlines()
    build = (cli.TEMPLATE_DIR / "build.sh").read_text()
    body = re.search(r"^unset_uv_overrides\(\) \{\n(.*?)^\}", build, flags=re.MULTILINE | re.DOTALL)
    assert body, "build.sh has no unset_uv_overrides function"

    in_install = unset_names(line for line in install if not line.startswith((" ", "\t")))
    in_build = unset_names(body.group(1).splitlines())

    assert in_install == in_build
    assert NEW_NAMES <= set(in_install)


def test_uv_settings_that_move_the_venv_or_change_resolution_never_reach_uv(deployed, tmp_path):  # noqa: F811
    stub_bin = tmp_path / "recording-uv"
    stub_bin.mkdir()
    (stub_bin / "uv").write_text(RECORDING_UV)
    (stub_bin / "uv").chmod(0o755)
    settings = {
        "UV_PROJECT_ENVIRONMENT": str(tmp_path / "elsewhere"),
        "UV_RESOLUTION": "lowest",
        "UV_PRERELEASE": "allow",
        "UV_FORK_STRATEGY": "fewest",
        "UV_EXCLUDE_NEWER": "2020-01-01T00:00:00Z",
        "UV_NO_SOURCES": "1",
    }

    proc, calls = run_install(deployed, tmp_path, path=f"{stub_bin}{os.pathsep}{os.environ['PATH']}", **settings)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [line for line in calls if not line.startswith("  ")] == ["--version", "sync --frozen --no-dev"]
    assert [line.strip() for line in calls if line.startswith("  ") and line.strip().split("=")[0] in settings] == []
    assert f"APP_VENV={deployed / '.venv'}" in (deployed / ".env").read_text().splitlines()


def install_with_uv_in_local_bin(version_dir, tmp_path, mode):
    """Run install.sh where PATH has no uv and ~/.local/bin is not on it, but holds a stub uv with `mode`.

    Returns (proc, uv calls, curl calls).
    """
    path = path_without_uv()
    if path is None:
        pytest.skip("cannot hide uv here")
    home, curl_bin = tmp_path / "home", tmp_path / "curl-bin"
    (home / ".local" / "bin").mkdir(parents=True)
    curl_bin.mkdir()
    uv = home / ".local" / "bin" / "uv"
    uv.write_text(STUB_UV)
    uv.chmod(mode)
    (curl_bin / "curl").write_text(STUB_CURL)
    (curl_bin / "curl").chmod(0o755)
    uv_source = tmp_path / "uv-to-install"
    uv_source.write_text(STUB_UV)
    curl_log = tmp_path / "curl-calls.log"

    proc, calls = run_install(
        version_dir,
        tmp_path,
        path=f"{curl_bin}{os.pathsep}{path}",
        HOME=str(home),
        CURL_STUB_LOG=str(curl_log),
        UV_STUB_SOURCE=str(uv_source),
    )
    curls = curl_log.read_text().splitlines() if curl_log.exists() else []
    return proc, calls, curls


def test_a_uv_an_earlier_deploy_installed_is_used_without_downloading_it_again(deployed, tmp_path):  # noqa: F811
    proc, calls, curls = install_with_uv_in_local_bin(deployed, tmp_path, 0o755)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert curls == []
    assert "uv not found" not in proc.stdout
    assert calls == ["--version", "sync --frozen --no-dev"]


def test_a_uv_in_local_bin_that_cannot_run_is_installed_again(deployed, tmp_path):  # noqa: F811
    # Control: a uv there that is not executable is not used; the installer runs, as before.
    proc, calls, curls = install_with_uv_in_local_bin(deployed, tmp_path, 0o644)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(curls) == 1
    assert "[install.sh] uv not found; installing via the official installer" in proc.stdout
    assert calls == ["--version", "sync --frozen --no-dev"]


# A stub uv whose bin/activate also puts the virtualenv's bin/ first on PATH, as a real one does.
ACTIVATING_UV = STUB_UV.replace(
    "> .venv/bin/activate\n",
    "> .venv/bin/activate\n        printf 'PATH=\"$VIRTUAL_ENV/bin:$PATH\"\\nexport PATH\\n' >> .venv/bin/activate\n",
)


def test_a_command_added_to_install_sh_gets_the_tools_python(deployed, tmp_path):  # noqa: F811
    assert ACTIVATING_UV != STUB_UV
    stub_bin = tmp_path / "activating-uv"
    stub_bin.mkdir()
    (stub_bin / "uv").write_text(ACTIVATING_UV)
    (stub_bin / "uv").chmod(0o755)
    seen = tmp_path / "seen.txt"
    install = deployed / "deploy" / "install.sh"
    install.write_text(install.read_text() + f'command -v python > "{seen}"\necho "$VIRTUAL_ENV" >> "{seen}"\n')

    proc, _calls = run_install(deployed, tmp_path, path=f"{stub_bin}{os.pathsep}{os.environ['PATH']}")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert seen.read_text().splitlines() == [str(deployed / ".venv" / "bin" / "python"), str(deployed / ".venv")]
