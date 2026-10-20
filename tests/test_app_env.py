"""A tool started by another tool must use its own .env, not the caller's values.

phbr starts tc1 as a child process, and the child inherits phbr's environment.
Before core/app_env.py, every core module loaded .env without overriding it, so
tc1 imported phbr's libs/. Later only APP_ROOT and APP_NAME were overridden, so
tc1 still saw phbr's APP_VENV, APP_ENTRY and dependency paths. These tests
scaffold a caller ('demo') and a callee ('peer') and run a probe inside the
callee with the caller's values in its environment.
"""

import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PROBE = """\
import json, os, sys
import validators  # a tool's first import; it loads core.core_validators
import core.core_validators as cv
import core.NGArgumentParser as ng
import core.set_pythonpath as sp
print(json.dumps({
    "set_pythonpath": str(sp.APP_ROOT),
    "core_validators": cv.APP_ROOT,
    "app_name": cv.APP_NAME,
    "ngargparser": ng.APP_ROOT,
    "libs": sorted(p for p in sys.path if p.endswith(os.sep + "libs")),
    "env": {k: os.environ.get(k) for k in ("X_PATH", "APP_VENV", "APP_ENTRY", "FOO", "PATH")},
}))
"""


@pytest.fixture
def caller_and_callee(in_tmp_dir):
    for name in ("demo", "peer"):
        assert not cli.startapp_command(Namespace(project_name=name))
    peer = in_tmp_dir / "peer"
    (peer / "src" / "probe_env.py").write_text(PROBE)
    return in_tmp_dir / "demo", peer


PROBED_KEYS = ("APP_ROOT", "APP_NAME", "APP_VENV", "APP_ENTRY", "X_PATH", "FOO")


def run_probe(peer, **env):
    base = {k: v for k, v in os.environ.items() if k not in PROBED_KEYS}
    return subprocess.run(
        [sys.executable, str(peer / "src" / "probe_env.py")],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def seen(proc):
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_callee_uses_its_own_app_root_not_the_callers(caller_and_callee):
    demo, peer = caller_and_callee

    out = seen(run_probe(peer, APP_ROOT=str(demo), APP_NAME="demo"))

    assert out["set_pythonpath"] == out["core_validators"] == out["ngargparser"] == str(peer)
    assert out["app_name"] == "peer"
    assert out["libs"] == [str(peer / "libs")]


def test_control_nothing_inherited(caller_and_callee):
    _demo, peer = caller_and_callee

    out = seen(run_probe(peer))

    assert out["set_pythonpath"] == str(peer)
    assert out["app_name"] == "peer"


def test_unconfigured_callee_stops_instead_of_using_the_callers_libs(caller_and_callee):
    demo, peer = caller_and_callee
    (peer / ".env").unlink()
    (peer / ".git").mkdir()  # a clone: one without .git says it is being installed

    proc = run_probe(peer, APP_ROOT=str(demo), APP_NAME="demo")

    assert proc.returncode != 0
    assert "This tool has not been configured yet" in proc.stderr


def test_without_env_an_app_root_pointing_at_itself_is_kept(caller_and_callee):
    # The build's startup check runs the build copy (no .env) with APP_ROOT set to it.
    _demo, peer = caller_and_callee
    (peer / ".env").unlink()

    out = seen(run_probe(peer, APP_ROOT=str(peer), APP_NAME="peer"))

    assert out["set_pythonpath"] == str(peer)
    assert out["libs"] == [str(peer / "libs")]


def add_to_env_file(project, text):
    env_file = project / ".env"
    env_file.write_text(env_file.read_text() + text)


def test_callers_other_keys_are_replaced_by_the_callees_own_env(caller_and_callee):
    _demo, peer = caller_and_callee
    add_to_env_file(peer, "X_PATH=x-for-peer\nAPP_VENV=/venvs/peer\n")

    out = seen(run_probe(peer, X_PATH="x-for-demo", APP_VENV="/venvs/demo", APP_ENTRY="src/run_demo.py"))

    assert out["env"]["X_PATH"] == "x-for-peer"
    assert out["env"]["APP_VENV"] == "/venvs/peer"
    assert out["env"]["APP_ENTRY"] == "src/run_peer.py"


def test_control_a_key_the_callees_env_does_not_define_is_inherited(caller_and_callee):
    _demo, peer = caller_and_callee
    add_to_env_file(peer, "X_PATH=x-for-peer\n")

    out = seen(run_probe(peer, FOO="inherited"))

    assert out["env"]["FOO"] == "inherited"
    assert out["env"]["X_PATH"] == "x-for-peer"


def test_a_stale_path_in_the_env_file_does_not_replace_the_process_path(caller_and_callee):
    # phbr's .env still holds PATH=<class 'pathlib.Path'> from an old ./configure;
    # taking it would break every command the tool runs.
    _demo, peer = caller_and_callee
    add_to_env_file(peer, "PATH=<class 'pathlib.Path'>\n")

    out = seen(run_probe(peer))

    assert out["env"]["PATH"] == os.environ["PATH"]


# .env holds an absolute APP_ROOT. A copied tool used to import the original
# folder's libs/ without a word, and a moved one failed on its first import.


def configure(project):
    proc = subprocess.run(
        [sys.executable, "src/core/configure.py"], cwd=project, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr


def test_a_copied_tool_stops_and_says_to_run_configure(caller_and_callee):
    demo, peer = caller_and_callee
    copy = peer.parent / "peer-copy"
    shutil.copytree(peer, copy, symlinks=True)

    alone = run_probe(copy)
    started_by_a_tool = run_probe(copy, APP_ROOT=str(demo), APP_NAME="demo")

    for proc in (alone, started_by_a_tool):
        assert proc.returncode == 1
        assert proc.stderr.strip().splitlines() == [
            f"This tool was configured in {peer} but is running from {copy}.",
            f"Run './configure' in {copy}, then re-run this command.",
        ]
    # An APP_ROOT in the environment that points at the copy (the build's startup
    # check, `cli sync`) lets it start, as before.
    assert run_probe(copy, APP_ROOT=str(copy)).returncode == 0
    # The original still runs, and so does the copy once ./configure has run in it.
    assert seen(run_probe(peer))["libs"] == [str(peer / "libs")]
    configure(copy)
    assert seen(run_probe(copy))["libs"] == [str(copy / "libs")]


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks")
def test_a_moved_tool_stops_until_configured_and_a_current_link_still_matches(caller_and_callee):
    _demo, peer = caller_and_callee
    version_dir = peer.parent / "apps" / "peer" / "1.0.0"
    version_dir.parent.mkdir(parents=True)
    peer.rename(version_dir)

    proc = run_probe(version_dir)

    assert proc.returncode == 1
    assert f"This tool was configured in {peer} but is running from {version_dir}." in proc.stderr
    assert "ModuleNotFoundError" not in proc.stderr

    # ./configure in the version folder, then run it through a `current` link to it.
    configure(version_dir)
    current = version_dir.parent / "current"
    current.symlink_to("1.0.0")
    out = seen(run_probe(current))
    assert out["set_pythonpath"] == str(version_dir)
    assert out["libs"] == [str(version_dir / "libs")]


# When a callee's .env sets APP_ROOT but not APP_NAME, an inherited APP_NAME names
# the caller, so it is dropped rather than kept (see load_app_env's docstring).

NAME_PROBE = """\
import json, os
import core.set_pythonpath
print(json.dumps({"APP_ROOT": os.environ.get("APP_ROOT"), "APP_NAME": os.environ.get("APP_NAME")}))
"""


def test_an_inherited_app_name_is_dropped_when_the_env_sets_only_app_root(caller_and_callee):
    demo, peer = caller_and_callee
    (peer / "src" / "probe_name.py").write_text(NAME_PROBE)
    env_file = peer / ".env"
    env_file.write_text(
        "".join(line for line in env_file.read_text().splitlines(True) if not line.startswith("APP_NAME="))
    )
    base = {k: v for k, v in os.environ.items() if k not in PROBED_KEYS}

    proc = subprocess.run(
        [sys.executable, str(peer / "src" / "probe_name.py")],
        env={**base, "APP_ROOT": str(demo), "APP_NAME": "demo"},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert seen(proc) == {"APP_ROOT": str(peer), "APP_NAME": None}


# A copied or moved tool brings its .venv, which still points at the old folder.
# The "configured in another folder" message says to make it again first.


def test_a_copied_tool_with_its_venv_says_to_recreate_it(caller_and_callee):
    _demo, peer = caller_and_callee
    old_venv = peer / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(old_venv)], check=True, timeout=120)
    copy = peer.parent / "peer-copy"
    shutil.copytree(peer, copy, symlinks=True)

    proc = run_probe(copy)

    assert proc.returncode == 1
    assert proc.stderr.strip().splitlines() == [
        f"This tool was configured in {peer} but is running from {copy}.",
        f"{copy / '.venv'} was made for {old_venv}; recreate it first: rm -r .venv && uv sync",
        f"Run './configure' in {copy}, then re-run this command.",
    ]
    # A deployed copy (the build stamp, and deploy/install.sh) has no uv project: install.sh makes .venv again.
    (copy / ".ngargparser-build").write_text("TOOL_NAME=peer\nTOOL_VERSION=1.0.0\n")
    assert (copy / "deploy" / "install.sh").is_file()
    assert run_probe(copy).stderr.strip().splitlines() == [
        f"This tool was configured in {peer} but is running from {copy}.",
        f"{copy / '.venv'} was made for {old_venv}; recreate it first: rm -r .venv && bash deploy/install.sh, "
        "which runs ./configure too",
        f"Run that in {copy}, then re-run this command.",
    ]
    (copy / ".ngargparser-build").unlink()
    # Made again in the copy, the message is the one without a virtualenv.
    shutil.rmtree(copy / ".venv")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(copy / ".venv")], check=True, timeout=120)
    assert run_probe(copy).stderr.strip().splitlines() == [
        f"This tool was configured in {peer} but is running from {copy}.",
        f"Run './configure' in {copy}, then re-run this command.",
    ]


# Caller and callee can both depend on a tool of one name, each at its own path.
# The caller exports its dependency's virtualenv as SHARED_VENV, and the callee,
# whose own dependency publishes none, kept that value: old code under another
# install's Python.

SHARED_PROBE = """\
import json, os
import core.set_pythonpath
print(json.dumps({k: os.environ.get(k) for k in ("SHARED_PATH", "SHARED_VENV", "SHARED_ENTRY")}))
"""


def run_shared_probe(project, **env):
    base = {k: v for k, v in os.environ.items() if not k.startswith("SHARED_") and k not in PROBED_KEYS}
    return seen(
        subprocess.run(
            [sys.executable, str(project / "src" / "probe_shared.py")],
            env={**base, **env},
            capture_output=True,
            text=True,
            timeout=60,
        )
    )


def test_a_dependency_value_the_callee_cannot_find_is_not_taken_from_the_caller(caller_and_callee, tmp_path):
    _demo, peer = caller_and_callee
    shared_old, shared_new = tmp_path / "shared_old", tmp_path / "shared_new"
    for folder in (shared_old, shared_new / ".venv", shared_new / "src"):
        folder.mkdir(parents=True)
    cli.add_deps_to_paths(str(peer / "paths.py"), ["shared"])
    configured = subprocess.run(
        [sys.executable, "src/core/configure.py", f"--shared-path={shared_old}"],
        cwd=peer,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert configured.returncode == 0, configured.stdout + configured.stderr
    (peer / "src" / "probe_shared.py").write_text(SHARED_PROBE)
    callers_values = {
        "SHARED_PATH": str(shared_new),
        "SHARED_VENV": str(shared_new / ".venv"),
        "SHARED_ENTRY": str(shared_new / "src" / "run_shared.py"),
    }

    assert run_shared_probe(peer, **callers_values) == {
        "SHARED_PATH": str(shared_old),
        "SHARED_VENV": None,
        "SHARED_ENTRY": None,
    }

    # Control: once shared_old publishes its own values, the callee takes those.
    (shared_old / ".env").write_text(f"APP_VENV={shared_old / '.venv'}\nAPP_ENTRY=src/run_old.py\n")
    assert run_shared_probe(peer, **callers_values) == {
        "SHARED_PATH": str(shared_old),
        "SHARED_VENV": str(shared_old / ".venv"),
        "SHARED_ENTRY": str(shared_old / "src" / "run_old.py"),
    }


# A Python without the tool's packages (the system python3) used to end in a
# traceback: "ModuleNotFoundError: No module named 'dotenv'".


def test_a_python_without_dotenv_says_to_use_the_tools_virtualenv(caller_and_callee):
    _demo, peer = caller_and_callee
    base = {k: v for k, v in os.environ.items() if k not in PROBED_KEYS}

    # -S leaves out site-packages, where python-dotenv is installed.
    proc = subprocess.run(
        [sys.executable, "-S", "src/run_peer.py", "-h"], cwd=peer, env=base, capture_output=True, text=True, timeout=60
    )

    assert proc.returncode == 1
    assert proc.stderr.splitlines() == [
        "This tool needs its virtualenv: run it with 'uv run python src/run_peer.py ...' "
        "(or activate .venv first). Run 'uv sync' if .venv is missing."
    ]
    # Control: the same command with site-packages starts.
    started = subprocess.run(
        [sys.executable, "src/run_peer.py", "-h"], cwd=peer, env=base, capture_output=True, text=True, timeout=60
    )
    assert started.returncode == 0, started.stderr


# A callee whose .env leaves out a framework key kept the caller's value: APP_VENV
# and APP_ENTRY described the caller to the callee's own callers, and the caller's
# APP_KEYS_FROM_DEPS stayed in the environment. The settings of a dependency the
# callee declares came from the caller's dependency of the same name when the
# callee's .env left them out.

KEYS_PROBE = """\
import json, os, sys
import core.set_pythonpath
print(json.dumps({k: os.environ.get(k) for k in sys.argv[1:]}))
"""


def run_keys_probe(project, keys, **env):
    (project / "src" / "probe_keys.py").write_text(KEYS_PROBE)
    base = {k: v for k, v in os.environ.items() if k not in keys and k not in PROBED_KEYS}
    return seen(
        subprocess.run(
            [sys.executable, str(project / "src" / "probe_keys.py"), *keys],
            env={**base, **env},
            capture_output=True,
            text=True,
            timeout=60,
        )
    )


def test_a_callee_drops_the_callers_framework_keys_and_settings_its_env_leaves_out(caller_and_callee, tmp_path):
    demo, peer = caller_and_callee
    # peer publishes no entry script or virtualenv, follows nothing, and declares
    # one dependency, `shared`, with only its path set.
    env_file = peer / ".env"
    lines = [line for line in env_file.read_text().splitlines(True) if not line.startswith("APP_ENTRY=")]
    env_file.write_text("".join(lines) + f"SHARED_PATH={tmp_path / 'peers-shared'}\n")
    callers = {
        "APP_VENV": str(demo / ".venv"),
        "APP_ENTRY": "src/run_demo.py",
        "APP_KEYS_FROM_DEPS": "SHARED_VENV,SHARED_ENTRY",
        "SHARED_VENV": str(tmp_path / "demos-shared" / ".venv"),
        "SHARED_ENTRY": str(tmp_path / "demos-shared" / "src" / "run_shared.py"),
        "SHARED_MODULE": "python/3.9",
        "SHARED_LIB_PATH": str(tmp_path / "demos-shared" / "lib"),
        # Not a dependency of peer: may be the user's own, so it is kept.
        "ONLYDEMO_PATH": str(tmp_path / "onlydemo"),
        "ONLYDEMO_LIB_PATH": str(tmp_path / "onlydemo" / "lib"),
    }

    out = run_keys_probe(peer, [*callers, "SHARED_PATH"], **callers)

    assert out == {
        **dict.fromkeys(callers),
        "SHARED_PATH": str(tmp_path / "peers-shared"),
        "ONLYDEMO_PATH": callers["ONLYDEMO_PATH"],
        "ONLYDEMO_LIB_PATH": callers["ONLYDEMO_LIB_PATH"],
    }
    # Control: values peer's .env sets are its own.
    env_file.write_text(env_file.read_text() + "APP_VENV=/venvs/peer\nSHARED_LIB_PATH=/libs/peer\n")
    out = run_keys_probe(peer, ["APP_VENV", "SHARED_LIB_PATH"], **callers)
    assert out == {"APP_VENV": "/venvs/peer", "SHARED_LIB_PATH": "/libs/peer"}


# `cli sync` and the build's startup check start a tool with APP_ROOT set to the tool
# folder. In a copy, the .env still names the original folder as APP_ROOT, and that
# value replaced the one given: the copy imported the original folder's libs/.


def test_a_copied_tool_started_with_its_own_app_root_uses_its_own_libs(caller_and_callee):
    _demo, peer = caller_and_callee
    copy = peer.parent / "peer-copy"
    shutil.copytree(peer, copy, symlinks=True)
    (copy / "libs").mkdir(exist_ok=True)

    out = seen(run_probe(copy, APP_ROOT=str(copy)))

    assert out["set_pythonpath"] == out["core_validators"] == out["ngargparser"] == str(copy)
    assert out["libs"] == [str(copy / "libs")]
    # Control: the original, started the same way, keeps its own folder.
    assert seen(run_probe(peer, APP_ROOT=str(peer)))["libs"] == [str(peer / "libs")]


# The same message in a build or deployed copy said to run 'uv run python ...',
# which works on a uv project: a deployed copy has none to work on, and may be
# read-only. Run from another folder, it did not say which folder to run it in.


def test_a_python_without_dotenv_in_a_build_copy_or_another_folder_names_what_to_run(caller_and_callee, tmp_path):
    _demo, peer = caller_and_callee
    base = {k: v for k, v in os.environ.items() if k not in PROBED_KEYS}
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def message(cwd, script):
        proc = subprocess.run(
            [sys.executable, "-S", script, "-h"], cwd=cwd, env=base, capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 1
        return proc.stderr.splitlines()

    assert message(elsewhere, str(peer / "src" / "run_peer.py")) == [
        f"This tool needs its virtualenv: in {peer}, run it with 'uv run python src/run_peer.py ...' "
        "(or activate .venv first). Run 'uv sync' if .venv is missing."
    ]
    (peer / ".ngargparser-build").write_text("TOOL_NAME=peer\nTOOL_VERSION=1.0.0\n")
    assert message(peer, "src/run_peer.py") == [
        "This tool needs its virtualenv: run it with '.venv/bin/python src/run_peer.py ...'. "
        "Run 'bash deploy/install.sh' if .venv is missing."
    ]
    assert message(elsewhere, str(peer / "src" / "run_peer.py")) == [
        f"This tool needs its virtualenv: in {peer}, run it with '.venv/bin/python src/run_peer.py ...'. "
        "Run 'bash deploy/install.sh' if .venv is missing."
    ]


# A tool whose own .env is not UTF-8 text (saved in Latin-1, say) stopped with a
# UnicodeDecodeError traceback from python-dotenv.


def test_a_tool_whose_env_is_not_utf8_says_to_run_configure(caller_and_callee):
    _demo, peer = caller_and_callee
    (peer / ".env").write_bytes(f"APP_ROOT={peer}\nAPP_NAME=p\xe9er\n".encode("latin-1"))

    proc = run_probe(peer)

    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert proc.stderr.strip().splitlines() == [
        f"{peer / '.env'} is not UTF-8 text: run ./configure again to rewrite it"
    ]
