"""A nested .git never ships, a file named build is never deleted, and a
script a killed build left running is stopped by the next build.

A '!' rule in .distignore ('!/src/**', or '*' then '!*/') brought a nested
.git back, and with it a clone's .git/config, token included: such a .git is
now left out after the rules, with a notice. make clean deleted a regular
file named build, and make build failed on it after the old tarball was gone:
both now stop, and nothing is deleted. After kill -9, get_libs.sh or hooks.sh
(in a session of its own, from setsid) went on writing into the next build's
copy, or made the next build fail with rm's "Directory not empty": its process
group is now recorded in .ngargparser/script-group, and the next build stops
it first.
"""

import os
import shutil
import signal
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or os.name != "posix",
    reason="build.sh tests require bash, git and POSIX processes",
)

TOKEN = "glpat-SECRET"
GIT_NOTICE = (
    "Notice: .distignore would ship these .git paths, which never ship "
    "(a clone's .git/config can hold a token); the build left them out:"
)


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


def build_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "NGARGPARSER_SKIP_STARTUP_CHECK")}
    return {**env, "TOOL_VERSION": "tver", **extra}


# --- a nested .git ---------------------------------------------------------------------


def clone_with_token(project_dir):
    vend = project_dir / "src" / "vend"
    subprocess.run(["git", "init", "-q", str(vend)], check=True)
    subprocess.run(
        ["git", "-C", str(vend), "remote", "add", "origin", f"https://oauth2:{TOKEN}@example.org/x.git"], check=True
    )
    (vend / "vend.py").write_text("X = 1\n")
    # A submodule's pointer file, a .git that is a file.
    (project_dir / "src" / "sub").mkdir()
    (project_dir / "src" / "sub" / ".git").write_text("gitdir: ../../.git/modules/sub\n")


WHITELIST = "*\n!*/\n!/src/**\n!/pyproject.toml\n!/uv.lock\n!/configure\n"


@pytest.mark.parametrize(
    "rules",
    [WHITELIST, None],
    ids=["whitelist", "default-rules-and-a-negation"],
)
def test_a_nested_git_never_ships_whatever_the_rules_say(scaffolded_project, run_build, rules):
    clone_with_token(scaffolded_project)
    distignore = scaffolded_project / ".distignore"
    if rules is None:
        distignore.write_text(distignore.read_text() + "!src/vend/**\n!src/sub/**\n")
    else:
        distignore.write_text(rules)

    run = run_build(scaffolded_project, progress=True)

    shipped = [name for name in run.members if ".git" in name.split("/")]
    assert shipped == [], shipped
    assert "src/vend/vend.py" in run.members, "the rest of the clone still ships"
    with open(run.tarball, "rb") as f:
        assert TOKEN.encode() not in f.read()
    assert not (run.staged / "src" / "vend" / ".git").exists()
    lines = run.proc.stdout.splitlines()
    at = lines.index(GIT_NOTICE)
    assert lines[at + 1 : at + 3] == ["  src/sub/.git", "  src/vend/.git"]


def test_a_nested_git_the_rules_exclude_gets_no_notice(scaffolded_project, run_build):
    # Control: the built-in '.*' rule keeps it out, as before, without a notice.
    clone_with_token(scaffolded_project)

    run = run_build(scaffolded_project, progress=True)

    assert not [name for name in run.members if ".git" in name.split("/")]
    assert "src/vend/vend.py" in run.members
    assert ".git paths" not in run.proc.stdout


# --- a file named build ----------------------------------------------------------------

BUILD_NOT_A_FOLDER = (
    "ERROR: build is not a folder: make build writes its output into build/, and make clean deletes build/. "
    "Move it away (mv build build.old), then run the command again. Nothing was changed.\n"
)


@pytest.mark.parametrize("kind", ["file", "broken-link"])
def test_clean_and_build_keep_a_build_that_is_not_a_folder(scaffolded_project, kind):
    build = scaffolded_project / "build"
    if kind == "file":
        build.write_text("#!/bin/sh\necho my own build script\n")
    else:
        build.symlink_to("no-such-folder")

    for args in (["--clean"], ["--progress"], []):
        proc = subprocess.run(
            ["bash", "scripts/core/build.sh", *args],
            cwd=scaffolded_project,
            env=build_env(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 1, (args, proc.stdout, proc.stderr)
        assert BUILD_NOT_A_FOLDER in proc.stderr, (args, proc.stderr)
        assert os.path.lexists(build), args
        if kind == "file":
            assert build.read_text() == "#!/bin/sh\necho my own build script\n"
        else:
            assert os.readlink(build) == "no-such-folder"


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def test_make_clean_keeps_a_file_named_build(scaffolded_project):
    build = scaffolded_project / "build"
    build.write_text("mine\n")

    proc = subprocess.run(["make", "clean"], cwd=scaffolded_project, env=build_env(), capture_output=True, text=True)

    assert proc.returncode != 0
    assert BUILD_NOT_A_FOLDER in proc.stderr
    assert build.read_text() == "mine\n"

    # Control: a build folder is still removed.
    build.unlink()
    (build / "old").mkdir(parents=True)
    proc = subprocess.run(["make", "clean"], cwd=scaffolded_project, env=build_env(), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert not build.exists()


# --- a script a killed build left running ----------------------------------------------

LEFT_RUNNING = (
    "Notice: a build that was stopped with kill -9 (or by the out-of-memory killer) left scripts/get_libs.sh "
    "or scripts/hooks.sh running; this build stopped it first."
)

# Runs on (writing a file every 0.05 s, for at most 60 s) only while the
# marker is there, which it removes: the next build's run of it ends at once.
ENDLESS = """
if [ -e "{mark}" ]; then
    rm -f "{mark}"
    echo $$ > "{pid}"
    for i in $(seq 1200); do
        {write}
        sleep 0.05
    done
fi
"""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie is not running.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(") ", 1)[1].split()[0] != "Z"
    except OSError:
        return True


@pytest.mark.skipif(shutil.which("setsid") is None, reason="the script runs in a session of its own with setsid")
@pytest.mark.parametrize("script", ["hooks.sh", "get_libs.sh"])
def test_the_next_build_stops_a_script_a_killed_build_left(scaffolded_project, run_build, tmp_path, script):
    mark = tmp_path / "mark"
    pid_file = tmp_path / "script.pid"
    if script == "hooks.sh":
        # Writes into the build copy, where the next build's copy will be.
        write = 'mkdir -p "$BUILD_DIR/libs" 2>/dev/null; touch "$BUILD_DIR/libs/orphan-$i" 2>/dev/null'
    else:
        # Writes into its working folder, .ngargparser/libs-fetch.*, which the
        # next build removes.
        write = "mkdir -p data 2>/dev/null; touch data/orphan-$i 2>/dev/null"
    (scaffolded_project / "scripts" / script).write_text(ENDLESS.format(mark=mark, pid=pid_file, write=write))
    mark.touch()
    build = subprocess.Popen(
        ["bash", "scripts/core/build.sh"],
        cwd=scaffolded_project,
        env=build_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    orphan = None
    try:
        deadline = time.monotonic() + 60
        while not (pid_file.exists() and pid_file.read_text().strip()):
            assert build.poll() is None, "the build ended before the script ran"
            assert time.monotonic() < deadline, "the script did not start"
            time.sleep(0.05)
        orphan = int(pid_file.read_text())
        os.kill(build.pid, signal.SIGKILL)
        build.wait(timeout=30)
        time.sleep(0.3)
        assert alive(orphan), "kill -9 of the build left the script running"

        run = run_build(scaffolded_project, expect_ok=False)

        assert not alive(orphan), "the script a killed build left still runs"
        assert run.proc.returncode == 0, run.proc.stdout + run.proc.stderr
        assert LEFT_RUNNING in run.proc.stdout
        assert not [name for name in run.members if "orphan-" in name]
        assert not (scaffolded_project / ".ngargparser" / "script-group").exists()
    finally:
        if build.poll() is None:
            build.kill()
        if orphan is not None:
            try:
                os.killpg(orphan, signal.SIGKILL)
            except OSError:
                pass


@pytest.mark.skipif(not os.path.exists("/proc/self/stat"), reason="needs /proc to tell processes apart")
def test_a_recorded_group_another_process_now_has_is_left_alone(scaffolded_project, run_build):
    # The number of the recorded group is now the group of a process that
    # started later (here, a sleep this test starts): the next build leaves it.
    other = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        state = scaffolded_project / ".ngargparser"
        state.mkdir(exist_ok=True)
        with open("/proc/sys/kernel/random/boot_id") as f:
            boot = f.read().strip()
        (state / "script-group").write_text(f"{other.pid}\n1\n{boot}\n")

        run = run_build(scaffolded_project)

        assert other.poll() is None, "the build stopped a process it did not start"
        assert LEFT_RUNNING not in run.proc.stdout
        assert not (state / "script-group").exists()
    finally:
        other.kill()
        other.wait()


def test_a_finished_build_leaves_no_group_record(scaffolded_project, run_build):
    (scaffolded_project / "scripts" / "hooks.sh").write_text("echo hook\n")

    run_build(scaffolded_project)

    assert not (scaffolded_project / ".ngargparser" / "script-group").exists()
