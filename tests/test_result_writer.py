"""write_results must write every table it is given, and json must accept a Path.

Two tables of the same type both went to <prefix>.<type>.tsv, so the second
overwrote the first, and the stdout banners could not tell them apart. And json
failed with TypeError when the envelope held a pathlib.Path, such as a tool's
args.input_json.
"""

import json
from argparse import Namespace
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
    # Control: only paths and numpy-like values are converted; anything else json
    # can't write still fails, now naming where it is (still a TypeError too).
    with pytest.raises(TypeError, match="write_results: bad is of type object, which json cannot write"):
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


# The envelope is checked before anything is written. A misspelled key used to
# write a one-newline file and exit 0, and "results" as a dict crashed with
# AttributeError deep inside the writer.

BAD_ENVELOPES = [
    (
        {"results": [{"type": "t", "columns": ["a"], "rows": [[1]]}]},
        "results[0] (type 't') has no 'table_columns'; it has 'columns': rename it to 'table_columns'",
    ),
    (
        {"results": [{"type": "t", "table_colums": ["a"], "table_data": [[1]]}]},
        "it has 'table_colums': rename it to 'table_columns'",
    ),
    (
        {"results": [{"type": "t", "table_columns": ["a"], "rows": [[1]]}]},
        "results[0] (type 't') has no 'table_data'; it has 'rows': rename it to 'table_data'",
    ),
    (
        {"results": {"type": "t", "table_columns": ["a"], "table_data": [[1]]}},
        "'results' must be a list or tuple of tables, not dict",
    ),
    ({"result": [table("t", 1)]}, "the result has no 'results'; it has 'result': rename it to 'results'"),
    ([table("t", 1)], "the result must be a dict"),
    ({"results": ["t"]}, "results[0] must be a dict (one table), not str"),
    ({"results": [{"table_columns": ["a"], "table_data": [[1]]}]}, "results[0] has no 'type'"),
    (
        {"results": [table("t", 1), {"type": "u", "table_columns": "a", "table_data": [[1]]}]},
        "results[1] (type 'u'): 'table_columns' must be a list or tuple, not str",
    ),
    (
        {"results": [{"type": "t", "table_columns": ["a"], "table_data": {"a": 1}}]},
        "'table_data' must be a list or tuple of rows, not dict",
    ),
    (
        {"results": [{"type": "t", "table_columns": ["a"], "table_data": ["x"]}]},
        "table_data[0] must be a list or tuple, not str",
    ),
    (
        {"results": [{"type": "t", "table_columns": ["a", "b"], "table_data": [[1, 2], [3]]}]},
        "results[0] (type 't'): table_data[1] has length 1, but table_columns has length 2",
    ),
    ({"warnings": "check the input", "results": [table("t", 1)]}, "'warnings' must be a list or tuple, not str"),
    ({"errors": {"code": 1}, "results": [table("t", 1)]}, "'errors' must be a list or tuple, not dict"),
    # A set has no order: its cells would land under the wrong columns in tsv,
    # and json cannot write it at all.
    (
        {"results": [{"type": "t", "table_columns": ["a", "b"], "table_data": [{1, 2}]}]},
        "table_data[0] must be a list or tuple, not set",
    ),
]


@pytest.mark.parametrize("output_format", ["tsv", "json"])
@pytest.mark.parametrize("envelope, message", BAD_ENVELOPES, ids=[m[:40] for _, m in BAD_ENVELOPES])
def test_bad_envelope_is_refused_before_anything_is_written(tmp_path, capsys, envelope, message, output_format):
    with pytest.raises(ValueError) as raised:
        write_results(envelope, str(tmp_path / "x"), output_format)

    assert str(raised.value).startswith("write_results: ")
    assert message in str(raised.value)
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr() == ("", "")


def test_bad_table_stops_before_the_warnings_are_printed(tmp_path, capsys):
    envelope = {"warnings": ["low coverage"], "results": [{"type": "t", "columns": ["a"], "table_data": [[1]]}]}

    with pytest.raises(ValueError, match="rename it to 'table_columns'"):
        write_results(envelope)

    assert capsys.readouterr() == ("", "")


def test_tsv_refuses_a_result_that_is_not_a_table(tmp_path):
    envelope = {"results": [table("t", 1), {"type": "plot", "series": [[1, 2]]}]}

    with pytest.raises(
        ValueError, match=r"results\[1\] \(type 'plot'\) has no 'table_columns'; tsv writes only tables"
    ):
        write_results(envelope, str(tmp_path / "x"))

    assert list(tmp_path.iterdir()) == []


# Control: every shape that wrote before still writes, byte for byte.

VALID_ENVELOPES = {
    "extra keys": {
        "warnings": ["w"],
        "errors": [],
        "input": "in.json",
        "results": [{**table("t", 1), "unique_vals": {"a": [1]}, "field_ranges": {}}],
    },
    "result_type alias": {"results": [{"result_type": "t", "table_columns": ["a"], "table_data": [[1]]}]},
    "empty results": {"warnings": [], "errors": [], "results": []},
    "no results": {"errors": ["no peptides in the input"]},
    "None lists": {"warnings": None, "errors": None, "results": []},
    "empty table": {"results": [{"type": "t", "table_columns": ["a", "b"], "table_data": []}]},
    "tuples": {"results": [{"type": "t", "table_columns": ("a", "b"), "table_data": [(1, 2), (3, 4)]}]},
    # Control: a tuple is valid wherever the messages say "a list or tuple".
    "tuples everywhere": {
        "warnings": ("w",),
        "errors": (),
        "results": ({"type": "t", "table_columns": ("a", "b"), "table_data": ((1, 2), (3, 4))},),
    },
}


@pytest.mark.parametrize("name", VALID_ENVELOPES)
def test_valid_envelopes_still_write(tmp_path, capsys, name):
    envelope = VALID_ENVELOPES[name]

    write_results(envelope, output_format="json")
    printed = capsys.readouterr().out
    write_results(envelope)
    tsv = capsys.readouterr().out

    assert printed == json.dumps(envelope, indent=2) + "\n"
    assert tsv.endswith("\n")


def test_json_still_writes_a_result_that_is_not_a_table(tmp_path, capsys):
    # population-coverage puts plot data and a nested matrix next to its tables.
    envelope = {
        "results": [
            table("summary", 1),
            {"type": "matrix", "populations": ["World"], "grids": [{"rows": [{"epitope": "e"}]}]},
            {"type": "distribution", "series": [{"distribution": [[0, 1.5, 100.0]]}]},
        ]
    }

    paths = write_results(envelope, str(tmp_path / "x"), "json")

    assert (tmp_path / "x.json").read_text() == json.dumps(envelope, indent=2) + "\n"
    assert paths == [str(tmp_path / "x.json")]


def test_none_results_still_writes_json_and_is_refused_by_tsv(capsys):
    # Control for json, which wrote "results": null; tsv failed with TypeError.
    write_results({"results": None}, output_format="json")
    assert capsys.readouterr().out == '{\n  "results": null\n}\n'

    with pytest.raises(ValueError, match="'results' must be a list or tuple of tables, not NoneType"):
        write_results({"results": None})


# tsv: a value holding a tab wrote two fields under one header, and a line break
# started a new row, with no error; None was written as the text "None".


def rows_table(cell):
    return {
        "results": [{"type": "t", "table_columns": ["a", "b"], "table_data": [[1, 2], [3, 4], [5, 6], ["x", cell]]}]
    }


@pytest.mark.parametrize("cell", ["x\ty", "x\ny", "x\ry", Path("a\tb")], ids=["tab", "newline", "return", "path"])
def test_tsv_refuses_a_value_with_a_tab_or_line_break(tmp_path, capsys, cell):
    with pytest.raises(ValueError) as raised:
        write_results(rows_table(cell), str(tmp_path / "out"))

    assert str(raised.value) == (
        "write_results: results[0] (type 't'): table_data[3][1] holds a tab or line break, which tsv cannot hold; "
        "clean the value, or write -f json"
    )
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr() == ("", "")


def test_tsv_refuses_a_column_name_with_a_tab(capsys):
    envelope = {"results": [table("t", 1), {"type": "u", "table_columns": ["a", "b\tc"], "table_data": []}]}

    with pytest.raises(ValueError, match=r"^write_results: results\[1\] \(type 'u'\): table_columns\[1\] holds a tab"):
        write_results(envelope)

    assert capsys.readouterr() == ("", "")


def test_tsv_writes_none_as_an_empty_cell(tmp_path, capsys):
    envelope = {"results": [{"type": "t", "table_columns": ["a", "b", "c"], "table_data": [[None, 1, None]]}]}

    write_results(envelope)
    write_results(envelope, str(tmp_path / "out"))

    assert capsys.readouterr().out == "a\tb\tc\n\t1\t\n"
    assert (tmp_path / "out.tsv").read_text() == "a\tb\tc\n\t1\t\n"


def test_json_keeps_tabs_and_none(capsys):
    # Control: json holds any text, and writes None as null.
    envelope = rows_table("x\ty")
    envelope["results"][0]["table_data"].append([None, "z\nw"])

    write_results(envelope, output_format="json")

    assert json.loads(capsys.readouterr().out) == envelope


def test_tsv_cells_without_tabs_are_unchanged(capsys):
    # Control: text with spaces, numbers, booleans and lists render with str() as before.
    write_results(
        {"results": [{"type": "t", "table_columns": ["a", "b"], "table_data": [["x y", 1.5], [True, [1, 2]]]}]}
    )

    assert capsys.readouterr().out == "a\tb\nx y\t1.5\nTrue\t[1, 2]\n"


# The prefix: write_results(result, "tsv") wrote tsv.tsv into the current folder
# (the tool folder, which ships), and write_results(result, args) wrote a file
# named "Namespace(output_prefix=None).tsv".


@pytest.mark.parametrize("output_format", ["tsv", "json"])
@pytest.mark.parametrize("prefix", ["tsv", "json", "TSV", "Json"])
def test_format_name_as_the_prefix_is_refused(tmp_path, monkeypatch, capsys, prefix, output_format):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError) as raised:
        write_results({"warnings": ["w"], "results": [table("x", 1)]}, prefix, output_format)

    assert str(raised.value) == (
        f"write_results: output_prefix is {prefix!r}, a format name: pass it as output_format, "
        f"e.g. write_results(result, args.output_prefix, {prefix.lower()!r})"
    )
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr() == ("", "")
    # Control: a prefix that only holds a format name still writes, in either form.
    assert write_results({"results": [table("x", 1)]}, "out/tsv", output_format) == [f"out/tsv.{output_format}"]
    assert write_results({"results": [table("x", 1)]}, Path("json"), output_format) == [f"json.{output_format}"]


@pytest.mark.parametrize(
    "prefix, message",
    [
        (Namespace(output_prefix=None), "not Namespace: pass args.output_prefix"),
        (5, "not int"),
        (b"out", "not bytes"),
        ({"results": []}, "not dict"),
    ],
    ids=["namespace", "int", "bytes", "dict"],
)
def test_prefix_that_is_no_path_is_refused(tmp_path, monkeypatch, capsys, prefix, message):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(TypeError) as raised:
        write_results({"results": [table("x", 1)]}, prefix)

    assert str(raised.value) == f"write_results: output_prefix must be a path or None, {message}"
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr() == ("", "")
    # Control: None, text and a Path, by position as the template calls it.
    write_results({"results": [table("x", 1)]}, None, "tsv")
    assert capsys.readouterr().out == "a\n1\n"
    assert write_results({"results": [table("x", 1)]}, "res", "json") == ["res.json"]
    assert write_results({"results": [table("x", 1)]}, tmp_path / "sub" / "res") == [str(tmp_path / "sub" / "res.tsv")]
