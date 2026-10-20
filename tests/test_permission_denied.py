"""A path in a folder this user may not enter, on every Python.

Python 3.14's Path.is_file(), is_dir() and exists() return False where 3.13
and older raised PermissionError, so on 3.14 validate_file and
validate_directory said "does not exist" for a path in a folder this user may
not enter, and validate_directory_given_filename let an output path in one
through (older Pythons raised a raw PermissionError from it). Each validator
now reads the path with os.stat(), and every Python says "cannot be read
(permission denied)".
"""

import os
from argparse import ArgumentTypeError

import pytest

from ngargparser import core_validators as cv

as_root = pytest.mark.skipif(os.geteuid() == 0, reason="root enters any folder")


@pytest.fixture
def noenter(tmp_path):
    """A folder this user may read but not enter (mode 600), with a file and a folder in it."""
    (tmp_path / "noenter" / "sub").mkdir(parents=True)
    (tmp_path / "noenter" / "in.json").write_text("{}\n")
    (tmp_path / "noenter").chmod(0o600)
    yield tmp_path
    (tmp_path / "noenter").chmod(0o700)


@as_root
@pytest.mark.parametrize(
    "validator, name",
    [
        (cv.validate_file, "noenter/in.json"),
        (cv.validate_file, "noenter/sub/in.json"),
        (cv.validate_directory, "noenter/sub"),
        (cv.validate_directory_given_filename, "noenter/out.tsv"),
        (cv.validate_directory_given_filename, "noenter/sub/out.tsv"),
    ],
)
def test_a_path_in_a_folder_this_user_may_not_enter_cannot_be_read(noenter, validator, name):
    path = str(noenter / name)
    with pytest.raises(ArgumentTypeError) as refused:
        validator(path)

    assert str(refused.value) == f"'{path}' cannot be read (permission denied)."


@pytest.mark.parametrize(
    "validator, name, message",
    [
        (cv.validate_file, "nothing.json", "'{p}' does not exist."),
        (cv.validate_file, "in.json/x", "'{p}' does not exist."),
        (cv.validate_file, "dir", "'{p}' is a folder, not a file."),
        (cv.validate_file, "link", "'{p}' is a broken link: its target '{t}/gone' does not exist."),
        (cv.validate_directory, "nothing", "'{p}' does not exist."),
        (cv.validate_directory, "in.json", "'{p}' is a file, not a folder."),
        (cv.validate_directory, "link", "'{p}' is a broken link: its target '{t}/gone' does not exist."),
        (cv.validate_directory_given_filename, "dir", "'{p}' is a folder, not a file."),
        (cv.validate_directory_given_filename, "nothing/out.tsv", "'{p}': folder '{t}/nothing' does not exist."),
        (cv.validate_directory_given_filename, "in.json/out.tsv", "'{p}': '{t}/in.json' is not a folder."),
    ],
)
def test_the_other_refusals_are_unchanged(tmp_path, validator, name, message):
    # Control: a missing path, a broken link, and a file given as a folder (and
    # the reverse) keep their messages.
    (tmp_path / "in.json").write_text("{}\n")
    (tmp_path / "dir").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "gone")
    path = str(tmp_path / name)
    with pytest.raises(ArgumentTypeError) as refused:
        validator(path)

    assert str(refused.value) == message.format(p=path, t=tmp_path)


def test_readable_paths_still_pass(tmp_path):
    # Control: a readable file, a folder, and a new file in a folder pass as before.
    (tmp_path / "in.json").write_text("{}\n")

    assert cv.validate_file(str(tmp_path / "in.json")) == tmp_path / "in.json"
    assert cv.validate_directory(str(tmp_path)) == tmp_path
    assert cv.validate_directory_given_filename(str(tmp_path / "out.tsv")) == tmp_path / "out.tsv"
