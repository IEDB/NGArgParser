"""A build and a cli sync that find one stale lock at once.

Without flock, a build and a sync take the same folder lock,
.ngargparser/build.lock, and take over one a killed run left. Both now use
one protocol (the takeover folder .ngargparser/build.lock.takeover, and the
wait for a new lock's pid): exactly one of them goes on, and the other names
its pid. NGARGPARSER_LOCK_RACES sets how many races one run of the test has
(5 by default).
"""

import os
import random
import shutil
import subprocess
import time

import pytest
from test_build_one_at_a_time import BUSY
from test_lock import folder_lock_path, stale_lock

from ngargparser import cli

# A generated tool has no .venv and no uv.lock, so its startup check has no Python
# and stops the build: builds run here without it.
NO_PYTHON = {"NGARGPARSER_SKIP_STARTUP_CHECK": "1"}

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or os.name != "posix",
    reason="build.sh tests require bash, git and POSIX processes",
)

RACES = int(os.environ.get("NGARGPARSER_LOCK_RACES", "5"))


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


def test_a_build_and_a_sync_race_on_one_stale_lock(scaffolded_project, tmp_path):
    # The scaffold fixture hides flock from cli (shutil.which), so the sync takes the
    # folder lock; the build does too, on a PATH without flock.
    path = folder_lock_path(tmp_path)
    (scaffolded_project / "scripts" / "hooks.sh").write_text(
        'for i in $(seq 600); do [ -e "$RELEASE" ] && break; sleep 0.1; done\n'
    )
    outer = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")} | NO_PYTHON
    state = scaffolded_project / ".ngargparser"
    winners = []
    for race in range(RACES):
        stale_lock(scaffolded_project)
        released = tmp_path / f"release-{race}"
        build = subprocess.Popen(
            ["bash", "scripts/core/build.sh"],
            cwd=scaffolded_project,
            env={**outer, "PATH": path, "TOOL_VERSION": "tver", "RELEASE": str(released)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(random.uniform(0, 0.4))
            try:
                release = cli._take_build_lock()
            except cli._LockBusy as busy:
                # The build went on: the sync names its pid, and the build finishes.
                assert (busy.holder, getattr(busy, "owner_unknown", False)) == (str(build.pid), False), race
                released.touch()
                _out, err = build.communicate(timeout=120)
                assert build.returncode == 0, err
                winners.append("build")
            else:
                # The sync went on: the build stops and names the sync's pid. Were it to
                # go on too, its hook would wait for the release file.
                try:
                    released.touch()
                    _out, err = build.communicate(timeout=120)
                finally:
                    release()
                assert build.returncode == 1, err
                assert BUSY.format(os.getpid()) in err, err
                winners.append("sync")
        finally:
            released.touch()
            if build.poll() is None:
                build.kill()
                build.wait()
        assert not (state / "build.lock").exists(), race
        assert not (state / "build.lock.takeover").exists(), race
        assert not list(state.glob("build.lock.stale-*")), race
    print("winners:", " ".join(winners))
    log = os.environ.get("NGARGPARSER_LOCK_RACE_LOG")
    if log:
        with open(log, "a") as f:
            f.write(" ".join(winners) + "\n")
