"""deploy/install.sh, the script the deploy orchestrator runs on the target host.

nxg-tools-deployments extracts the tarball into <install_dir>/<name>/<version>/
and runs `bash deploy/install.sh` there. These tests build a scaffolded tool,
extract its tarball the same way and run install.sh with a stub `uv` on PATH: it
records its arguments and creates a .venv whose python runs the test
interpreter (which has python-dotenv), so nothing is downloaded.
"""

import os
import shutil
import subprocess
import sys
import tarfile

import pytest
from conftest import BUILD_TOOL_DIR

from ngargparser import cli

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="install.sh tests require bash (and git for the build)",
)

# It prints the version install.sh pins (UV_PIN), so install.sh keeps it: an
# older one is replaced through the official installer.
STUB_UV = """\
#!/bin/sh
echo "$*" >> "$UV_STUB_LOG"
case "$1" in
    --version) echo "uv 0.11.14 (stub)" ;;
    sync)
        mkdir -p .venv/bin
        printf 'VIRTUAL_ENV="%s/.venv"\\nexport VIRTUAL_ENV\\n' "$PWD" > .venv/bin/activate
        printf 'home = /usr/bin\\ninclude-system-site-packages = false\\n' > .venv/pyvenv.cfg
        rm -f .venv/bin/python  # never write through a symlink into a real interpreter
        printf '#!/bin/sh\\nexec "%s" "$@"\\n' "$UV_STUB_PYTHON" > .venv/bin/python
        chmod +x .venv/bin/python
        ;;
esac
"""


def deploy_tarball(tarball, version_dir, tmp_path):
    """Extract `tarball` the way the orchestrator does, into `version_dir`."""
    unpacked = tmp_path / f"unpacked-{version_dir.name}"
    with tarfile.open(tarball) as tf:
        # The "data" filter where this Python has it (3.12 warns without a filter).
        tf.extractall(unpacked, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    version_dir.parent.mkdir(parents=True, exist_ok=True)
    (unpacked / BUILD_TOOL_DIR).rename(version_dir)
    return version_dir


def run_install(version_dir, tmp_path, *, path=None, cwd=None, script="deploy/install.sh", **extra_env):
    """Run `bash <script>` in `cwd` (default `version_dir`) with the stub uv. Returns (proc, uv calls).

    `path` replaces the PATH the stub uv is put in front of, and `extra_env` adds
    variables (HOME, for one).
    """
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir(exist_ok=True)
    uv = stub_bin / "uv"
    uv.write_text(STUB_UV)
    uv.chmod(0o755)
    log = tmp_path / f"uv-calls-{version_dir.name}.log"
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ROOT", "APP_NAME", "VIRTUAL_ENV")}
    env.update(
        PATH=path if path is not None else f"{stub_bin}{os.pathsep}{env.get('PATH', '')}",
        UV_STUB_LOG=str(log),
        UV_STUB_PYTHON=sys.executable,
        IEDB_STANDALONE_NAME="demo",
        IEDB_VERSION=version_dir.name,
        IEDB_VERSION_DIR=str(version_dir),
        IEDB_STANDALONE_DIR=str(version_dir.parent.parent),
        IEDB_PREVIOUS_VERSION_DIR="",
    )
    env.update(extra_env)
    proc = subprocess.run(
        ["bash", script], cwd=cwd or version_dir, env=env, capture_output=True, text=True, timeout=120
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def quiet_uv(tmp_path):
    """The environment for a build with a stand-in uv first on PATH, which prints
    nothing and exits 0: the stub uv.lock here is not one the real uv accepts, and
    the build's own lock checks have their own tests."""
    bin_dir = tmp_path / "quiet-uv"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "uv").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "uv").chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def test_install_sh_installs_the_lock_as_is_and_configures_the_tool(scaffolded_project, run_build, tmp_path):
    (scaffolded_project / "uv.lock").write_text("version = 1\n")  # the stub uv never reads it
    run = run_build(scaffolded_project, env=quiet_uv(tmp_path))
    assert "uv.lock" in run.members
    apps = tmp_path / "apps" / "demo"
    locked = deploy_tarball(run.tarball, apps / "1.0.0", tmp_path)
    unlocked = deploy_tarball(run.tarball, apps / "1.0.1", tmp_path)
    (unlocked / "uv.lock").unlink()

    proc, calls = run_install(locked, tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == ["--version", "sync --frozen --no-dev"]
    assert "[install.sh] running ./configure" in proc.stdout
    env_lines = (locked / ".env").read_text().splitlines()
    assert f"APP_ROOT={locked}" in env_lines
    assert "APP_NAME=demo" in env_lines
    assert f"APP_VENV={locked / '.venv'}" in env_lines  # made here, so published
    assert (locked / "uv.lock").read_text() == "version = 1\n"

    # Without a uv.lock there is nothing to freeze: `uv sync`, still without the dev group.
    proc, calls = run_install(unlocked, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == ["--version", "sync --no-dev"]
    assert "APP_NAME=demo" in (unlocked / ".env").read_text().splitlines()


def test_sync_leaves_a_customized_install_sh_byte_for_byte(scaffolded_project, sync_args):
    install = scaffolded_project / "deploy" / "install.sh"
    custom = install.read_bytes().replace(b'echo "[install.sh] done"', b'./my-extra-step\necho "[install.sh] done"')
    assert custom != install.read_bytes()
    install.write_bytes(custom)
    install.chmod(0o700)

    rc = cli.sync_command(sync_args())

    assert rc == 0
    assert install.read_bytes() == custom
    assert install.stat().st_mode & 0o777 == 0o700
    backups = scaffolded_project / ".ngargparser" / "sync-backups"
    assert not backups.exists() or not any(p.name == "install.sh" for p in backups.rglob("*"))


@pytest.fixture
def deployed(scaffolded_project, run_build, tmp_path):
    """The scaffolded tool, built with a uv.lock and extracted to apps/demo/1.0.0/."""
    (scaffolded_project / "uv.lock").write_text("version = 1\n")
    run = run_build(scaffolded_project, env=quiet_uv(tmp_path))
    return deploy_tarball(run.tarball, tmp_path / "apps" / "demo" / "1.0.0", tmp_path)


# install.sh ran ./configure only when it had its exec bit, so a configure that
# lost it was skipped without a word, and the deploy succeeded unconfigured.


def test_a_configure_without_its_exec_bit_still_runs(deployed, tmp_path):
    (deployed / "configure").chmod(0o644)

    proc, calls = run_install(deployed, tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[install.sh] running ./configure" in proc.stdout
    assert "APP_NAME=demo" in (deployed / ".env").read_text().splitlines()


def test_a_failing_configure_fails_the_install(deployed, tmp_path):
    (deployed / "configure").chmod(0o644)
    (deployed / "paths.py").write_text("broken = (\n")

    proc, _calls = run_install(deployed, tmp_path)

    assert proc.returncode == 1
    assert "paths.py line 1: " in proc.stdout  # the message itself depends on the Python version
    assert "[install.sh] done" not in proc.stdout
    assert not (deployed / ".env").exists()


# A host without uv: install.sh runs the official installer, then finds uv in
# ~/.local/bin. A stub curl prints an installer that puts the stub uv there. It
# installed the latest uv, while CI tests with the version it pins: the installer
# of that version is used now.


def ci_uv_version():
    """The uv version the generated .gitlab-ci.yml pins (UV_VERSION)."""
    import re

    text = (cli.TEMPLATE_DIR / "gitlab-ci.yml").read_text()
    (version,) = re.findall(r'^\s*UV_VERSION:\s*"([^"]+)"', text, flags=re.MULTILINE)
    return version


def test_install_sh_installs_the_uv_version_ci_pins():
    text = (cli.TEMPLATE_DIR / "deploy" / "install.sh").read_text()
    # The end-user READMEs' Quick Install ran the latest installer too: a blank tool's, and the example's.
    readmes = [
        (folder / name).read_text()
        for folder, name in ((cli.TEMPLATE_DIR, "README"), (cli.EXAMPLE_DIR, "README.enduser"))
    ]
    url = f"https://astral.sh/uv/{ci_uv_version()}/install.sh"

    assert f'    curl -LsSf {url} | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh' in text.splitlines()
    for readme in readmes:
        assert f"    curl -LsSf {url} | sh   # if uv is not installed yet" in readme.splitlines()
    for pinned in (text, *readmes):
        assert "https://astral.sh/uv/install.sh" not in pinned


# Prints a stand-in for uv's installer, which picks its install folder and edits
# the shell start-up files the way the real one (0.11.14) does: UV_INSTALL_DIR,
# else $XDG_BIN_HOME, else $XDG_DATA_HOME/../bin, else ~/.local/bin; and, unless
# UV_NO_MODIFY_PATH is set or that folder is on PATH already, ~/.profile and the like.
STUB_CURL = """\
#!/bin/sh
echo "$*" >> "$CURL_STUB_LOG"
cat <<'INSTALLER'
if [ -n "${UV_INSTALL_DIR:-}" ]; then
    dir=$UV_INSTALL_DIR
elif [ -n "${XDG_BIN_HOME:-}" ]; then
    dir=$XDG_BIN_HOME
elif [ -n "${XDG_DATA_HOME:-}" ]; then
    dir=$XDG_DATA_HOME/../bin
else
    dir=$HOME/.local/bin
fi
mkdir -p "$dir"
cp "$UV_STUB_SOURCE" "$dir/uv"
chmod +x "$dir/uv"
case :$PATH: in
    *:"$dir":*) UV_NO_MODIFY_PATH=1 ;;
esac
if [ -z "${UV_NO_MODIFY_PATH:-}" ] || [ "$UV_NO_MODIFY_PATH" = 0 ]; then
    for rc in .profile .bashrc .zshrc; do
        echo ". \\"$dir/env\\"" >> "$HOME/$rc"
    done
    mkdir -p "$HOME/.config/fish/conf.d"
    echo "source $dir/env.fish" > "$HOME/.config/fish/conf.d/uv.env.fish"
fi
INSTALLER
"""


def path_without_uv():
    """The PATH folders that hold no `uv`, or None when bash or sh would not be on it.

    Not shutil.which: the scaffolded_project fixture replaces it for the test.
    """
    folders = [
        p for p in os.environ.get("PATH", "").split(os.pathsep) if p and not os.path.exists(os.path.join(p, "uv"))
    ]
    found = {name for name in ("bash", "sh") for p in folders if os.access(os.path.join(p, name), os.X_OK)}
    return os.pathsep.join(folders) if found == {"bash", "sh"} else None


def install_without_uv(deployed, tmp_path, **extra_env):
    """Run install.sh where no uv is on PATH, with the stub curl and an empty HOME.

    Returns (proc, uv calls, curl calls, HOME).
    """
    path = path_without_uv()
    if path is None:
        pytest.skip("cannot hide uv here")
    curl_bin, home = tmp_path / "curl-bin", tmp_path / "home"
    curl_bin.mkdir()
    home.mkdir()
    curl = curl_bin / "curl"
    curl.write_text(STUB_CURL)
    curl.chmod(0o755)
    uv_source = tmp_path / "uv-to-install"
    uv_source.write_text(STUB_UV)
    curl_log = tmp_path / "curl-calls.log"

    proc, calls = run_install(
        deployed,
        tmp_path,
        path=f"{curl_bin}{os.pathsep}{path}",
        HOME=str(home),
        CURL_STUB_LOG=str(curl_log),
        UV_STUB_SOURCE=str(uv_source),
        **{key: value.format(home=home) for key, value in extra_env.items()},
    )
    curls = curl_log.read_text().splitlines() if curl_log.exists() else []
    return proc, calls, curls, home


def files_in(folder):
    """Every file under `folder`, as paths relative to it."""
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file())


def test_a_host_without_uv_gets_it_from_the_official_installer(deployed, tmp_path):
    proc, calls, curls, home = install_without_uv(deployed, tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[install.sh] uv not found; installing via the official installer" in proc.stdout
    assert curls == [f"-LsSf https://astral.sh/uv/{ci_uv_version()}/install.sh"]
    assert calls == ["--version", "sync --frozen --no-dev"]
    assert "APP_NAME=demo" in (deployed / ".env").read_text().splitlines()
    # uv, and nothing else: the deploy account's .profile, .bashrc, .zshrc and fish files are left alone.
    assert files_in(home) == [".local/bin/uv"]


# The installer puts uv in $XDG_BIN_HOME when that is set, but install.sh looked
# in ~/.local/bin: "uv: command not found", exit 127. It now tells the installer
# where to put it.


@pytest.mark.parametrize("variable, value", [("XDG_BIN_HOME", "{home}/xdgbin"), ("XDG_DATA_HOME", "{home}/xdgdata")])
def test_uv_installs_where_install_sh_looks_for_it_whatever_xdg_says(deployed, tmp_path, variable, value):
    proc, calls, _curls, home = install_without_uv(deployed, tmp_path, **{variable: value})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == ["--version", "sync --frozen --no-dev"]
    assert files_in(home) == [".local/bin/uv"]


def test_install_sh_works_from_another_folder(deployed, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    proc, calls = run_install(deployed, tmp_path, cwd=elsewhere, script=str(deployed / "deploy" / "install.sh"))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == ["--version", "sync --frozen --no-dev"]
    assert f"[install.sh] venv: {deployed / '.venv'}" in proc.stdout
    env_lines = (deployed / ".env").read_text().splitlines()
    assert f"APP_ROOT={deployed}" in env_lines
    assert f"APP_VENV={deployed / '.venv'}" in env_lines
    assert list(elsewhere.iterdir()) == []
