"""File names with any bytes but NUL and a line break, in a UTF-8 locale.

awk, sed and grep ran in the C locale, but bash's own read used the caller's:
in a UTF-8 locale a byte that starts a character, such as the \\351 of a name
saved in Latin-1, took the byte after it along. At the end of a name that is
the line break, so `echo 1 > "$(printf 'r\\351sum\\351')"; make build` failed
with `cp: cannot stat '.../r'$'\\351''sum'$'\\351\\n''fpyproject.toml'`, and a
fetched entry named so failed the fetch. A fetched folder whose name ends in
a space failed too (ensure_init_files read without IFS=), and git did not
ignore it (libs/.gitignore held the pattern with its trailing space, which
git drops). A link to a file whose name starts with '-' failed the link
check (dirname and basename read the name as an option).
"""

import os
import shutil
import subprocess
import tarfile

import pytest
from test_build_lock import find_program

# A generated tool has no .venv and no uv.lock, so its startup check has no Python
# and stops the build: builds run here without it.
NO_PYTHON = {"NGARGPARSER_SKIP_STARTUP_CHECK": "1"}

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="build.sh tests require bash and git",
)

VERSION = "tver"
TOOL_DIR = f"ng_demo-{VERSION}"
TARBALL = f"IEDB_NG_DEMO-{VERSION}.tar.gz"


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch):
    """GitLab sets CI, and in CI a build without .venv fails its startup check."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NGARGPARSER_SKIP_STARTUP_CHECK", raising=False)


def utf8_locale():
    """A UTF-8 locale this machine has, or None."""
    if find_program("locale", os.environ["PATH"]) is None:
        return None
    names = subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.split()
    for name in ("C.UTF-8", "C.utf8", "en_US.UTF-8", "en_US.utf8"):
        if name in names:
            return name
    return None


@pytest.fixture
def utf8():
    """The environment of a caller in a UTF-8 locale."""
    locale = utf8_locale()
    if locale is None:
        pytest.skip("no UTF-8 locale here")
    return {"LC_ALL": locale, "LANG": locale}


def build(project_dir, env=None):
    """build.sh --progress, its output as bytes (names that are not UTF-8 stay as they are)."""
    return subprocess.run(
        ["bash", "scripts/core/build.sh", "--progress"],
        cwd=project_dir,
        env={**os.environ, **NO_PYTHON, "TOOL_VERSION": VERSION, **(env or {})},
        capture_output=True,
        timeout=300,
    )


def members(project_dir):
    """The tarball's paths below the tool folder, as bytes."""
    with tarfile.open(project_dir / "build" / TARBALL) as tf:
        return {os.fsencode(m.name)[len(TOOL_DIR) + 1 :] for m in tf.getmembers() if m.name != TOOL_DIR}


def output(proc):
    return (proc.stdout + proc.stderr).decode("utf-8", "replace")


def write_get_libs(project_dir, body):
    (project_dir / "scripts" / "get_libs.sh").write_bytes(b"#!/bin/bash\nset -euo pipefail\n" + body + b"\n")


def test_names_that_end_in_a_latin1_byte_ship(scaffolded_project, utf8):
    root = os.fsencode(scaffolded_project)
    names = [b"r\xe9sum\xe9", b"a\xff", b"trail ", b"-dash", b"d\xe9/f\xe9", b"x\xe9\xa9"]
    for name in names:
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"1\n")
    os.symlink(b"a\xff", os.path.join(root, b"lnk\xe9"))

    proc = build(scaffolded_project, env=utf8)

    assert proc.returncode == 0, output(proc)
    shipped = members(scaffolded_project)
    assert set(names) | {b"lnk\xe9", b"d\xe9"} <= shipped
    assert b"pyproject.toml" in shipped and b"README" in shipped


def test_links_to_a_name_that_starts_with_a_dash(scaffolded_project):
    (scaffolded_project / "-dash").write_text("1\n")
    (scaffolded_project / "-lnk").symlink_to("-dash")
    (scaffolded_project / "-lnk2").symlink_to("./-dash")

    proc = build(scaffolded_project)

    assert proc.returncode == 0, output(proc)
    assert b"invalid option" not in proc.stderr
    assert {b"-dash", b"-lnk", b"-lnk2"} <= members(scaffolded_project)

    # Control: a dangling one is still named, with its target.
    (scaffolded_project / "-dangle").symlink_to("-gone")
    proc = build(scaffolded_project)
    assert proc.returncode == 1
    assert b"  -dangle -> -gone (dangling or a loop)\n" in proc.stderr
    assert b"invalid option" not in proc.stderr


def test_fetched_entries_with_such_names_are_fetched_and_kept_out_of_git(scaffolded_project, utf8):
    write_get_libs(
        scaffolded_project,
        b"mkdir -p \"$(printf 'lib\\351')/pkg\" && echo y > \"$(printf 'lib\\351')/pkg/a.py\"\n"
        b'mkdir -p -- "-dlib/p" && echo z > "-dlib/p/b.py"\n'
        b"mkdir \"$(printf 'z\\377')\"\n"
        b"echo w > \"$(printf 'f\\351\\251')\"",
    )
    fetched = [b"lib\xe9", b"-dlib", b"z\xff", b"f\xe9\xa9"]

    proc = build(scaffolded_project, env=utf8)

    assert proc.returncode == 0, output(proc)
    shipped = members(scaffolded_project)
    assert {b"libs/lib\xe9/pkg/a.py", b"libs/lib\xe9/__init__.py", b"libs/-dlib/p/b.py", b"libs/z\xff"} <= shipped
    assert b"libs/f\xe9\xa9" in shipped
    stamp = (scaffolded_project / ".ngargparser" / "libs-stamp").read_bytes().splitlines()
    assert sorted(line.split(b" ", 1)[1] for line in stamp[2:]) == sorted(fetched)

    # Fetched once: the next build reads the names back from the stamp.
    proc = build(scaffolded_project, env=utf8)
    assert proc.returncode == 0, output(proc)
    assert b"Libs up to date" in proc.stdout

    subprocess.run(["git", "init", "-q", "."], cwd=scaffolded_project, check=True)
    for name in fetched:
        check = subprocess.run(["git", "check-ignore", "-q", b"libs/" + name], cwd=scaffolded_project)
        assert check.returncode == 0, name


def test_a_fetched_folder_whose_name_ends_in_a_space(scaffolded_project):
    write_get_libs(scaffolded_project, b'mkdir -p "mylib /sub" && echo x > "mylib /sub/m.py"')

    proc = build(scaffolded_project)

    assert proc.returncode == 0, output(proc)
    assert b"No such file or directory" not in proc.stderr
    libs = scaffolded_project / "libs"
    assert (libs / "mylib " / "__init__.py").is_file()
    assert (libs / "mylib " / "sub" / "__init__.py").is_file()
    assert not (libs / "mylib").exists()
    assert {b"libs/mylib /sub/m.py", b"libs/mylib /__init__.py"} <= members(scaffolded_project)
    # git keeps a trailing space only when it is escaped.
    assert "/mylib\\ " in (libs / ".gitignore").read_text().splitlines()
    subprocess.run(["git", "init", "-q", "."], cwd=scaffolded_project, check=True)
    assert subprocess.run(["git", "check-ignore", "-q", "libs/mylib "], cwd=scaffolded_project).returncode == 0

    # Without the record in .ngargparser/, the build reads the name back from libs/.gitignore.
    shutil.rmtree(scaffolded_project / ".ngargparser")
    (scaffolded_project / "scripts" / "get_libs.sh").write_text(
        (scaffolded_project / "scripts" / "get_libs.sh").read_text() + "# changed\n"
    )
    proc = build(scaffolded_project)
    assert proc.returncode == 0, output(proc)
    assert b"already exists and was not fetched" not in proc.stderr


def test_the_tool_and_its_scripts_keep_the_callers_locale(scaffolded_project, utf8):
    # The C locale applies to bash's read alone: get_libs.sh and hooks.sh get the caller's.
    seen = scaffolded_project.parent / "seen"
    script = f'printf "%s|%s\\n" "${{LC_ALL-unset}}" "$(locale charmap 2>/dev/null)" >> "{seen}"'
    write_get_libs(scaffolded_project, script.encode())
    (scaffolded_project / "scripts" / "hooks.sh").write_text(script + "\n")

    proc = build(scaffolded_project, env=utf8)

    assert proc.returncode == 0, output(proc)
    lines = seen.read_text().splitlines()
    assert len(lines) == 2
    assert all(line.startswith(utf8["LC_ALL"] + "|") for line in lines), lines
