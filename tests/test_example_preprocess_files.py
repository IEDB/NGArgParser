"""The sample app's preprocess must delete only the files it wrote itself.

Its "Clean old files" step deleted every file in --inputs-dir and --params-dir,
so `preprocess ... --inputs-dir mydata` deleted mydata/notes.txt. preprocess now
lists the files it writes in each folder (.aa-counter-preprocess-files), and a
rerun deletes only the files listed there.
"""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from ngargparser import cli

LIST = ".aa-counter-preprocess-files"


@pytest.fixture
def example(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="example"))
    root = in_tmp_dir / "aa-counter"
    for folder, name, text in (
        ("mydata", "notes.txt", "important\n"),
        # Named like the files preprocess writes: <job>-<8 random characters>.
        ("mydata", "0-abcdefgh.txt", "mine too\n"),
        ("myparams", "keep.json", '{"mine": 1}\n'),
    ):
        (in_tmp_dir / folder).mkdir(exist_ok=True)
        (in_tmp_dir / folder / name).write_text(text)
    return root


def preprocess(project, *argv):
    proc = subprocess.run(
        [sys.executable, "src/run_aa_counter.py", "preprocess", "-j", "src/example.json", *argv],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads((project / argv[argv.index("-o") + 1] / "job_descriptions.json").read_text())


def job_inputs(project, jobs):
    """Each predict job's parameters, with its peptides in place of the data file path."""
    inputs = []
    for job in jobs[:-1]:
        params = json.loads((project / job["shell_cmd"].split(" -j ", 1)[1].split()[0]).read_text())
        params["peptide"] = Path(params.pop("peptide_file_path")).read_text().splitlines()
        inputs.append(params)
    return inputs


def files(folder):
    return {p.name: p.read_text() for p in folder.iterdir()}


NAMED = ["--inputs-dir", "../mydata", "--params-dir", "../myparams"]


def test_files_of_yours_in_the_named_folders_stay(example):
    jobs = preprocess(example, "-o", "../jobs", *NAMED)

    data, params = files(example / ".." / "mydata"), files(example / ".." / "myparams")
    assert data["notes.txt"] == "important\n"
    assert data["0-abcdefgh.txt"] == "mine too\n"
    assert params["keep.json"] == '{"mine": 1}\n'
    # The user's files are no jobs; the list names only what preprocess wrote.
    assert all("keep.json" not in job["shell_cmd"] for job in jobs)
    assert len(jobs) == 4  # 3 lengths, then the postprocess job
    assert sorted(data[LIST].split()) == sorted(set(data) - {"notes.txt", "0-abcdefgh.txt", LIST})
    assert sorted(params[LIST].split()) == sorted(set(params) - {"keep.json", LIST})


def test_a_rerun_removes_only_the_files_of_the_previous_run(example):
    first = preprocess(example, "-o", "../jobs", *NAMED)
    before = set(files(example / ".." / "mydata")) | set(files(example / ".." / "myparams"))
    first_inputs = job_inputs(example, first)

    second = preprocess(example, "-o", "../jobs", *NAMED)

    data, params = files(example / ".." / "mydata"), files(example / ".." / "myparams")
    kept = {"notes.txt", "0-abcdefgh.txt", "keep.json", LIST}
    assert (before & (set(data) | set(params))) == kept  # every file of the first run is gone
    assert (len(data), len(params)) == (3 + 3, 3 + 2)  # 3 jobs, the user's files and the list
    assert data["notes.txt"] == "important\n" and params["keep.json"] == '{"mine": 1}\n'
    # The same jobs as the first run.
    assert job_inputs(example, second) == first_inputs
    assert [j["job_type"] for j in second] == [j["job_type"] for j in first]


def test_a_file_of_yours_made_just_before_the_run_is_no_job(example):
    # The jobs came from the newest files in --params-dir, so a file made less
    # than 0.1 s before the run became a predict job.
    (example / ".." / "myparams" / "fresh.json").write_text("{}\n")

    jobs = preprocess(example, "-o", "../jobs", "--params-dir", "../myparams")

    assert (example / ".." / "myparams" / "fresh.json").read_text() == "{}\n"
    assert len(jobs) == 4
    assert all("fresh.json" not in job["shell_cmd"] and "keep.json" not in job["shell_cmd"] for job in jobs)


def test_the_default_folders_rerun_the_same_way(example):
    # The folders preprocess creates under -o follow the same rule.
    first = preprocess(example, "-o", "../jobs")
    first_inputs = job_inputs(example, first)
    data = example / ".." / "jobs" / "predict-inputs" / "data"
    (data / "mine.txt").write_text("mine\n")

    second = preprocess(example, "-o", "../jobs")

    assert job_inputs(example, second) == first_inputs
    assert (data / "mine.txt").read_text() == "mine\n"
    assert len(list(data.iterdir())) == 3 + 2
    for job in second[:-1]:
        script, *args = job["shell_cmd"].split()
        ran = subprocess.run([sys.executable, script, *args], cwd=example, capture_output=True, text=True)
        assert ran.returncode == 0, ran.stderr
