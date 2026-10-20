"""`cli sync` and a folder it cannot write: refused before the first write, in one line.

With only the tool's top folder read-only, sync wrote src/core/result_writer.py (a
writable subfolder) first, then failed on Makefile and undid everything, in two lines
("Could not write Makefile ..." and "Every file was restored ..."). Every folder it will
write into, and every file it edits in place, is now checked first. A failure that shows
only while it writes (a full disk) is still undone (see test_sync_write_errors_and_unfinished.py).
"""

import hashlib
import os
import re
import stat

import pytest

from ngargparser import cli

needs_permissions = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply"
)


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    monkeypatch.setattr(cli, "_local_version_label", lambda: None)


def tree_state(root):
    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative:
            continue
        if path.is_dir():
            dirs.add(relative)
        else:
            files[relative] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    return files, dirs


def edit(project, path):
    target = project / path
    target.write_text(target.read_text() + "# a local edit that sync will overwrite\n")


@needs_permissions
@pytest.mark.parametrize("dry_run", [False, True])
def test_a_read_only_top_folder_is_refused_before_the_first_write(
    scaffolded_project, sync_args, strip_ansi, capsys, dry_run
):
    project = scaffolded_project
    edit(project, "src/core/result_writer.py")  # in a folder sync can write: the first file it writes
    edit(project, "Makefile")  # in the top folder, which it cannot
    before = tree_state(project)
    project.chmod(0o555)
    try:
        rc = cli.sync_command(sync_args(verify=False, dry_run=dry_run))
    finally:
        project.chmod(0o755)

    captured = capsys.readouterr()
    out = strip_ansi(captured.err)
    assert rc == 1
    assert out.endswith(
        "✗ Could not write Makefile: the tool folder is read-only (Permission denied). Nothing was changed.\n"
    )
    assert "Every file was restored" not in out and "Updated" not in captured.out
    assert tree_state(project) == before


@needs_permissions
def test_a_read_only_file_sync_edits_in_place_is_refused_before_the_first_write(
    scaffolded_project, sync_args, strip_ansi, capsys
):
    project = scaffolded_project
    edit(project, "src/core/result_writer.py")
    readme = project / "README.md"
    readme.write_text(re.sub(r"ngargparser-[^-/\s)]+-green", "ngargparser-0.0.1-red", readme.read_text()))
    readme.chmod(0o444)  # its folder is writable, the file is not
    before = tree_state(project)

    rc = cli.sync_command(sync_args(verify=False))

    out = strip_ansi(capsys.readouterr().err)
    assert rc == 1
    assert out.endswith("✗ Could not write README.md: Permission denied. Nothing was changed.\n")
    assert tree_state(project) == before


@needs_permissions
def test_a_folder_sync_makes_is_checked_in_the_folder_above_it(scaffolded_project, sync_args, strip_ansi, capsys):
    project = scaffolded_project
    (project / "deploy" / "install.sh").unlink()
    (project / "deploy").rmdir()  # sync makes deploy/ and writes install.sh there
    edit(project, "src/core/result_writer.py")
    before = tree_state(project)
    project.chmod(0o555)
    try:
        rc = cli.sync_command(sync_args(verify=False))
    finally:
        project.chmod(0o755)

    out = strip_ansi("".join(capsys.readouterr()))
    assert rc == 1
    assert out.endswith(
        "✗ Could not write deploy/install.sh: the tool folder is read-only (Permission denied). Nothing was changed.\n"
    )
    assert tree_state(project) == before
