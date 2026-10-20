"""deploy/install.sh and the uv settings in the environment it runs in.

UV_LOCKED, UV_FROZEN, UV_PROJECT and UV_WORKING_DIR set in the deploy's
environment reached `uv sync --frozen --no-dev`: UV_LOCKED with --frozen is an
error, UV_FROZEN picks the lock mode, and the other two point uv at another
folder. make build unsets them for its uv calls; install.sh now does too, and
keeps the index, cache, Python and offline settings a host may need.
"""

import os

from test_install_sh import STUB_UV, deployed, run_install  # noqa: F401 (deployed is a fixture)

# The stub uv of test_install_sh.py, which also records the UV_ variables each call sees.
RECORDING_UV = STUB_UV.replace(
    'echo "$*" >> "$UV_STUB_LOG"\n',
    'echo "$*" >> "$UV_STUB_LOG"\nenv | grep "^UV_" | grep -v "^UV_STUB_" | sort | sed "s/^/  /" >> "$UV_STUB_LOG"\n',
)

OVERRIDES = {"UV_LOCKED": "1", "UV_FROZEN": "1", "UV_PROJECT": "/elsewhere", "UV_WORKING_DIR": "/elsewhere"}
KEPT = {
    "UV_INDEX_URL": "https://index.example/simple",
    "UV_CACHE_DIR": "/cache",
    "UV_PYTHON": "3.11",
    "UV_OFFLINE": "1",
}


def test_install_sh_runs_uv_sync_without_the_lock_and_project_overrides(deployed, tmp_path):  # noqa: F811
    stub_bin = tmp_path / "recording-uv"
    stub_bin.mkdir()
    (stub_bin / "uv").write_text(RECORDING_UV)
    (stub_bin / "uv").chmod(0o755)

    proc, calls = run_install(
        deployed, tmp_path, path=f"{stub_bin}{os.pathsep}{os.environ['PATH']}", **OVERRIDES, **KEPT
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    commands, seen = [], {}
    for line in calls:  # each command, then the UV_ variables it saw, indented
        if line.startswith("  "):
            key, _, value = line.strip().partition("=")
            seen[commands[-1]][key] = value
        else:
            commands.append(line)
            seen[line] = {}
    assert commands == ["--version", "sync --frozen --no-dev"]
    for command in commands:
        assert {key: seen[command].get(key) for key in OVERRIDES} == dict.fromkeys(OVERRIDES), command
        assert {key: seen[command].get(key) for key in KEPT} == KEPT, command
    assert "APP_NAME=demo" in (deployed / ".env").read_text().splitlines()
