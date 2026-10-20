"""write_results in json must write numpy values, and name a value it can't write.

table_data=[["AAA", np.float32(0.5)]] gave a raw "TypeError: Object of type
float32 is not JSON serializable" in json (tsv wrote it). A value with .tolist()
or .item(), as numpy values have, is now written as the Python value it holds,
without importing numpy. Any other value json can't write raises ValueError
naming where it is, as the other envelope errors do (a TypeError too, as before).
"""

import json

import pytest

from ngargparser.result_writer import write_results


class Scalar:
    """A stand-in for a numpy scalar: not JSON, but .item() gives the value."""

    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value

    def __str__(self):
        return str(self.value)


class Array:
    """A stand-in for a numpy array: .tolist() gives the values, .item() fails for two or more."""

    def __init__(self, values):
        self.values = values

    def tolist(self):
        return list(self.values)

    def item(self):
        raise ValueError("can only convert an array of size 1 to a Python scalar")


def envelope(*row, **extra):
    return {
        "results": [
            {"type": "t", "table_columns": [f"c{i}" for i in range(len(row))], "table_data": [list(row)], **extra}
        ]
    }


def test_json_writes_a_value_with_item_as_its_python_value(tmp_path):
    written = write_results(envelope("AAA", Scalar(0.5), Scalar(3)), str(tmp_path / "res"), "json")

    assert written == [str(tmp_path / "res.json")]
    assert json.loads((tmp_path / "res.json").read_text())["results"][0]["table_data"] == [["AAA", 0.5, 3]]


def test_json_writes_a_value_with_tolist_as_a_list(capsys):
    write_results(envelope("AAA", Array([1, Scalar(2)]), unique_vals=Array(["x"])), output_format="json")

    printed = json.loads(capsys.readouterr().out)
    assert printed["results"][0]["table_data"] == [["AAA", [1, 2]]]
    assert printed["results"][0]["unique_vals"] == ["x"]


def test_tsv_writes_a_value_with_item_as_before(capsys):
    # Control: tsv wrote it with str() already.
    write_results(envelope("AAA", Scalar(0.5)))

    assert capsys.readouterr().out == "c0\tc1\nAAA\t0.5\n"


@pytest.mark.parametrize(
    "result, where, what",
    [
        (envelope("AAA", {1, 2}), "results[0].table_data[0][1]", "is of type set"),
        (envelope("AAA", 1, field_ranges={"len": object()}), "results[0].field_ranges.len", "is of type object"),
        (envelope("AAA", 1, extra={(1, 2): 3}), "results[0].extra", "has a key of type tuple"),
        ({"results": [], "warnings": ["ok", b"raw"]}, "warnings[1]", "is of type bytes"),
    ],
    ids=["cell", "metadata", "key", "warning"],
)
def test_json_names_where_a_value_it_cannot_write_is(tmp_path, result, where, what):
    with pytest.raises(ValueError) as caught:
        write_results(result, str(tmp_path / "res"), "json")

    assert str(caught.value) == (
        f"write_results: {where} {what}, which json cannot write; convert it to text, a number, a list or a dict"
    )
    assert isinstance(caught.value, TypeError)  # as json.dumps raised
    assert list(tmp_path.iterdir()) == []


def test_json_writes_numpy_values_as_python_values(capsys):
    # Skipped where numpy is not installed; run with uv run --isolated --with numpy.
    np = pytest.importorskip("numpy")

    write_results(envelope("AAA", np.float32(0.5), np.int64(3), unique_vals=np.array([[1, 2]])), output_format="json")

    printed = json.loads(capsys.readouterr().out)["results"][0]
    assert printed["table_data"] == [["AAA", 0.5, 3]]
    assert printed["unique_vals"] == [[1, 2]]
