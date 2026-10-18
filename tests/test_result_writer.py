"""write_results must write every table it is given, and json must accept a Path.

Two tables of the same type both went to <prefix>.<type>.tsv, so the second
overwrote the first, and the stdout banners could not tell them apart. And json
failed with TypeError when the envelope held a pathlib.Path, such as a tool's
args.input_json.
"""

import json
from pathlib import Path

import pytest

from ngargparser.result_writer import write_results


def table(table_type, value):
    return {"type": table_type, "table_columns": ["a"], "table_data": [[value]]}


def written(tmp_path):
    return {p.name: p.read_text() for p in sorted(tmp_path.iterdir())}


# json: a Path is written as its text.


def test_json_writes_a_path_as_text(tmp_path, capsys):
    envelope = {"input": Path("in.json"), "results": [table("x", Path("a/b.tsv"))]}

    write_results(envelope, output_format="json")
    printed = json.loads(capsys.readouterr().out)
    write_results(envelope, str(tmp_path / "out"), "json")

    assert printed["input"] == "in.json"
    assert printed["results"][0]["table_data"] == [["a/b.tsv"]]
    assert json.loads((tmp_path / "out.json").read_text()) == printed


def test_json_still_rejects_other_objects():
    # Control: only paths are converted; anything else json can't write still fails.
    with pytest.raises(TypeError, match="Object of type object is not JSON serializable"):
        write_results({"results": [], "bad": object()}, output_format="json")


# tsv: a type that repeats is numbered, so no table is lost.


def test_repeated_type_writes_one_file_per_table(tmp_path, capsys):
    envelope = {"results": [table("x", 1), table("x", 2), table("y", 3), table("x", 4)]}

    paths = write_results(envelope, str(tmp_path / "out"))

    assert [Path(p).name for p in paths] == ["out.x.tsv", "out.x-2.tsv", "out.y.tsv", "out.x-3.tsv"]
    assert written(tmp_path) == {
        "out.x-2.tsv": "a\n2\n",
        "out.x-3.tsv": "a\n4\n",
        "out.x.tsv": "a\n1\n",
        "out.y.tsv": "a\n3\n",
    }
    assert capsys.readouterr().err.splitlines() == [f"Wrote {p}" for p in paths]


def test_repeated_type_gets_numbered_banners(capsys):
    write_results({"results": [table("x", 1), table("x", 2)]})

    assert capsys.readouterr().out == "--- x ---\na\n1\n\n--- x-2 ---\na\n2\n"


def test_numbered_name_that_is_already_a_type_is_not_reused(tmp_path):
    paths = write_results({"results": [table("x", 1), table("x-2", 2), table("x", 3)]}, str(tmp_path / "out"))

    assert [Path(p).name for p in paths] == ["out.x.tsv", "out.x-2.tsv", "out.x-3.tsv"]
    assert len(written(tmp_path)) == 3


def test_unique_types_are_unchanged(tmp_path, capsys):
    # Control: distinct types keep their plain names, on stdout and in files.
    envelope = {"results": [table("x", 1), table("y", 2)]}

    write_results(envelope)
    paths = write_results(envelope, str(tmp_path / "out"))

    assert capsys.readouterr().out == "--- x ---\na\n1\n\n--- y ---\na\n2\n"
    assert [Path(p).name for p in paths] == ["out.x.tsv", "out.y.tsv"]


def test_single_table_is_unchanged(tmp_path, capsys):
    # Control: one table gets no banner and a plain <prefix>.tsv.
    envelope = {"results": [table("x", 1)]}

    write_results(envelope)
    paths = write_results(envelope, str(tmp_path / "out"))

    assert capsys.readouterr().out == "a\n1\n"
    assert [Path(p).name for p in paths] == ["out.tsv"]
