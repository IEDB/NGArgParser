import argparse
from pathlib import Path

import pytest

from ngargparser import core_validators as cv


class TestGetDependenciesFromPaths:
    def test_missing_file_returns_empty_list(self, tmp_path):
        missing = tmp_path / "paths.py"
        assert cv.get_dependencies_from_paths(missing) == []

    def test_missing_file_matches_empty_file_behavior(self, tmp_path):
        empty = tmp_path / "empty_paths.py"
        empty.write_text("")
        missing = tmp_path / "missing_paths.py"
        assert cv.get_dependencies_from_paths(missing) == cv.get_dependencies_from_paths(empty) == []

    def test_other_os_errors_still_propagate(self, tmp_path):
        # A directory (not a missing path) triggers IsADirectoryError from
        # open(), which is NOT FileNotFoundError -- must not be swallowed.
        with pytest.raises(IsADirectoryError):
            cv.get_dependencies_from_paths(tmp_path)


class TestCreateDirectoryStructureForDependencies:
    def test_missing_paths_file_does_not_raise(self, tmp_path):
        output_dir = tmp_path / "out"
        missing_paths_file = tmp_path / "does_not_exist.py"
        cv.create_directory_structure_for_dependencies(output_dir, missing_paths_file)

    def test_missing_paths_file_creates_default_structure(self, tmp_path):
        output_dir = tmp_path / "out"
        result = cv.create_directory_structure_for_dependencies(output_dir, tmp_path / "does_not_exist.py")
        assert list(result.keys()) == ["default"]
        for sub in ("predict-inputs/data", "predict-inputs/params", "predict-outputs", "aggregate", "results"):
            assert (output_dir / sub).is_dir()

    def test_missing_paths_file_matches_empty_paths_file(self, tmp_path):
        empty_paths_file = tmp_path / "empty_paths.py"
        empty_paths_file.write_text("")

        out_missing = tmp_path / "out_missing"
        out_empty = tmp_path / "out_empty"

        result_missing = cv.create_directory_structure_for_dependencies(out_missing, tmp_path / "missing_paths.py")
        result_empty = cv.create_directory_structure_for_dependencies(out_empty, empty_paths_file)

        assert result_missing.keys() == result_empty.keys()
        rel_missing = sorted(Path(p).relative_to(out_missing) for p in result_missing["default"])
        rel_empty = sorted(Path(p).relative_to(out_empty) for p in result_empty["default"])
        assert rel_missing == rel_empty

    def test_other_os_errors_still_propagate(self, tmp_path):
        with pytest.raises(IsADirectoryError):
            cv.create_directory_structure_for_dependencies(tmp_path / "out", tmp_path)


# validate_directory_given_filename said "'nope/x.txt' is not a valid directory."
# for a file whose folder is missing, naming neither the folder nor the problem.


class TestValidateDirectoryGivenFilename:
    def test_missing_folder_is_named(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(argparse.ArgumentTypeError) as raised:
            cv.validate_directory_given_filename("nope/x.txt")
        assert str(raised.value) == "'nope/x.txt': folder 'nope' does not exist."

    def test_file_in_place_of_the_folder_is_named(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "f.txt").write_text("")
        with pytest.raises(argparse.ArgumentTypeError) as raised:
            cv.validate_directory_given_filename("f.txt/x.txt")
        assert str(raised.value) == "'f.txt/x.txt': 'f.txt' is not a folder."

    def test_existing_folder_still_passes(self, tmp_path, monkeypatch):
        # Control: a new file in an existing folder is fine, and comes back as a Path.
        monkeypatch.chdir(tmp_path)
        (tmp_path / "out").mkdir()
        assert cv.validate_directory_given_filename("out/x.txt") == Path("out/x.txt")
        assert cv.validate_directory_given_filename("x.txt") == Path("x.txt")


# With APP_ROOT unset (no .env, as in a test that imports core_validators), the
# layout check failed with "TypeError: expected str, bytes or os.PathLike object,
# not NoneType" from Path(APP_ROOT).


def test_layout_reads_the_projects_paths_py_when_app_root_is_unset(tmp_path, monkeypatch):
    project = tmp_path / "tool"
    project.mkdir()
    (project / "paths.py").write_text("''' [ PepX ] '''\npepx_path='/opt/pepx'\n")
    monkeypatch.setattr(cv, "APP_ROOT", None)
    monkeypatch.setattr(cv, "APP_NAME", "tool")
    monkeypatch.setattr(cv, "PROJECT_ROOT", project, raising=False)

    created = cv.create_directory_structure_for_dependencies(tmp_path / "out")

    assert list(created) == ["PepX", "tool"]
    assert (tmp_path / "out" / "pepx" / "predict-inputs" / "params").is_dir()


def test_layout_still_reads_app_root_when_it_is_set(tmp_path, monkeypatch):
    # Control: APP_ROOT, which ./configure writes, comes first.
    (tmp_path / "paths.py").write_text("''' [ PepX ] '''\npepx_path=None\n")
    monkeypatch.setattr(cv, "APP_ROOT", str(tmp_path))

    assert list(cv.create_directory_structure_for_dependencies(tmp_path / "out")) == ["default"]
