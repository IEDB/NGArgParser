"""
Shared result serializer for ngargparser tools (framework-owned).

This module is installed into each project as ``src/core/result_writer.py`` and is
refreshed by ``cli sync``. Do not edit it in a project: edit it in the
ngargparser framework and re-sync.

Every tool produces the same result envelope::

    {
        "warnings": [...],        # optional
        "errors": [...],          # optional
        "results": [
            {
                "type": "<table_type>",   # or "result_type" (accepted as an alias)
                "table_columns": ["col1", "col2", ...],
                "table_data": [[v11, v12, ...], [v21, v22, ...], ...],
                ...                        # extra keys (unique_vals, field_ranges, ...)
                                           # are preserved by json and ignored by tsv
            },
            ...
        ],
    }

``write_results`` renders that envelope uniformly:

* ``output_format="tsv"`` (default): tab-separated columns/rows. When the
  envelope holds two or more tables, each is prefixed with a ``--- <type> ---``
  banner on stdout, and written to a separate ``<prefix>.<type>.tsv`` file when
  ``-o`` is given. A type that repeats is numbered from its second table on
  (``<type>-2``, ``<type>-3``, ...) in both. A single table gets no banner and a
  single ``<prefix>.tsv``.
* ``output_format="json"``: the full envelope is dumped verbatim, preserving all
  metadata (warnings/errors/unique_vals/field_ranges/...). Path values are
  written as text, and numpy values as the Python values they hold (a scalar
  as a number, an array as a list). NaN and +/-Infinity (a float or a numpy
  value, at any depth) are written as ``null``: JSON has no such values, and
  json would write ``NaN``/``Infinity``, which strict readers (JavaScript's
  JSON.parse, for one) refuse. The file is always valid JSON. Any other value
  json cannot write raises ValueError naming where it is
  (``results[0].table_data[0][1]``).

Destination:

* ``output_prefix=None``  -> written to stdout.
* ``output_prefix=<str>`` -> written to file(s) named from the prefix.

A prefix of ``"tsv"`` or ``"json"`` raises ValueError (a format passed as the
prefix), as do an empty prefix and a folder (one that ends in a separator, or
an existing folder), which ``-o`` refuses too. One that is no text or path,
such as the argparse Namespace, raises TypeError. Both come before anything is
written.

For tsv, any ``warnings``/``errors`` in the envelope are echoed to stderr so
stdout stays a clean, pipeable data stream (json keeps them in the payload).

The envelope is checked before anything is written: a wrong or missing key
raises ValueError naming the table and the key (a misspelled ``"columns"``
used to give an empty file). So does one table given without the envelope
(``table_columns``/``table_data`` but no ``results``): wrap it as
``{"results": [table]}``. json also accepts a result entry that is not a
table (no ``table_columns``/``table_data``, such as plot data); tsv can only
write tables.

In tsv, ``None`` is written as an empty cell, and a value holding a tab or a
line break raises ValueError naming the table, row and column (it would shift
the row's fields or start a new row).
"""

import argparse
import difflib
import json
import math
import os
import sys
from collections.abc import Iterable, Mapping, Sized
from collections.abc import Set as AbstractSet
from pathlib import Path

# Keys written by mistake for the envelope's own keys. A close spelling
# ("table_colums") is caught too, by difflib.
_NEAR_MISSES = {
    "results": ("result", "tables", "table"),
    "type": ("table_type", "kind", "name"),
    "table_columns": ("columns", "cols", "header", "headers"),
    "table_data": ("rows", "data", "table_rows"),
}


def _near_miss(mapping, key):
    """The key in ``mapping`` that was probably meant to be ``key``, or None."""
    keys = [k for k in mapping if isinstance(k, str) and k != key]
    for k in keys:
        if k.lower() in _NEAR_MISSES[key]:
            return k
    close = difflib.get_close_matches(key, keys, n=1, cutoff=0.8)
    return close[0] if close else None


def _missing(where, mapping, key):
    """ValueError for ``key`` missing from ``mapping``, suggesting a near miss."""
    found = _near_miss(mapping, key)
    hint = f"; it has {found!r}: rename it to {key!r}" if found else ""
    return ValueError(f"write_results: {where} has no {key!r}{hint}")


def _is_list(value):
    """A list, or a list-like such as a tuple: sized and iterable, not text, a dict
    or a set (a set has no order, so its cells would land in the wrong columns)."""
    return (
        isinstance(value, Sized)
        and isinstance(value, Iterable)
        and not isinstance(value, (str, bytes, bytearray, Mapping, AbstractSet))
    )


def _check_envelope(result, output_format):
    """Raise ValueError when ``result`` is not an envelope ``output_format`` can write.

    Every currently written shape stays valid: extra keys anywhere, a missing or
    empty ``results``, ``None`` for ``warnings``/``errors``, and for ``results``
    in json (tsv failed on it with TypeError).
    """
    if not isinstance(result, dict):
        raise ValueError(
            f"write_results: the result must be a dict (the envelope with 'results', "
            f"'warnings' and 'errors'), not {type(result).__name__}"
        )
    # One table given without the envelope wrote an empty file (tsv) or no table (json).
    if "results" not in result and ("table_columns" in result or "table_data" in result):
        raise ValueError(
            "write_results: the result is one table, not the envelope: wrap it as "
            '{"results": [table]}, e.g. write_results({"results": [table]}, args.output_prefix, args.output_format)'
        )
    for key in ("warnings", "errors"):
        if result.get(key) is not None and not _is_list(result[key]):
            raise ValueError(f"write_results: {key!r} must be a list or tuple, not {type(result[key]).__name__}")
    if result.get("results") is None and not ("results" in result and output_format == "tsv"):
        if "results" not in result and _near_miss(result, "results"):
            raise _missing("the result", result, "results")
        return
    if not _is_list(result["results"]):
        raise ValueError(
            f"write_results: 'results' must be a list or tuple of tables, not {type(result['results']).__name__}"
        )
    for i, table in enumerate(result["results"]):
        where = f"results[{i}]"
        if not isinstance(table, dict):
            raise ValueError(f"write_results: {where} must be a dict (one table), not {type(table).__name__}")
        table_type = table.get("type") or table.get("result_type")
        if not table_type:
            raise _missing(where, table, "type")
        where = f"results[{i}] (type {table_type!r})"
        # json writes any result; an entry is checked as a table when it looks like one.
        if output_format == "json" and not (
            "table_columns" in table or "table_data" in table or _near_miss(table, "table_columns")
        ):
            continue
        if "table_columns" not in table:
            if output_format == "tsv" and not _near_miss(table, "table_columns"):
                raise ValueError(
                    f"write_results: {where} has no 'table_columns'; tsv writes only tables (json writes any result)"
                )
            raise _missing(where, table, "table_columns")
        if "table_data" not in table:
            raise _missing(where, table, "table_data")
        columns, rows = table["table_columns"], table["table_data"]
        if not _is_list(columns):
            raise ValueError(
                f"write_results: {where}: 'table_columns' must be a list or tuple, not {type(columns).__name__}"
            )
        if not _is_list(rows):
            raise ValueError(
                f"write_results: {where}: 'table_data' must be a list or tuple of rows, not {type(rows).__name__}"
            )
        if output_format == "tsv":
            _check_tsv_cells(where, "table_columns", columns)
        for n, row in enumerate(rows):
            if not _is_list(row):
                raise ValueError(
                    f"write_results: {where}: table_data[{n}] must be a list or tuple, not {type(row).__name__}"
                )
            if len(row) != len(columns):
                raise ValueError(
                    f"write_results: {where}: table_data[{n}] has length {len(row)}, "
                    f"but table_columns has length {len(columns)}"
                )
            if output_format == "tsv":
                _check_tsv_cells(where, f"table_data[{n}]", row)


def _check_tsv_cells(where, name, cells):
    """Raise ValueError when a value of ``cells`` (``name``, in table ``where``)
    holds a tab or a line break: tsv would split it into two fields or rows."""
    for m, value in enumerate(cells):
        # A number's text never holds one; skip the str() of most cells.
        if isinstance(value, (int, float)) or value is None:
            continue
        text = str(value)
        if "\t" in text or "\n" in text or "\r" in text:
            raise ValueError(
                f"write_results: {where}: {name}[{m}] holds a tab or line break, which tsv "
                f"cannot hold; clean the value, or write -f json"
            )


def _tables(result):
    """Yield ``(table_type, columns, rows)`` for each table in the envelope."""
    for table in result.get("results", []):
        table_type = table.get("type") or table.get("result_type") or "table"
        columns = table.get("table_columns", [])
        rows = table.get("table_data", [])
        yield table_type, columns, rows


def _labels(tables):
    """Name each table by its type, numbering a repeat (x, x-2, x-3) so no two
    tables share a banner or a file."""
    labels = []
    for table_type, _, _ in tables:
        label, n = table_type, 1
        while label in labels:
            n += 1
            label = f"{table_type}-{n}"
        labels.append(label)
    return labels


def _json_default(value):
    """json.dumps fallback: write a Path (any os.PathLike) as its text, and a numpy
    value (anything with .tolist() or .item(), numpy is not imported) as the
    Python value it holds: np.float32(0.5) as 0.5, an array as a list."""
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    for method in ("tolist", "item"):
        convert = getattr(value, method, None)
        if callable(convert):
            try:
                return convert()
            except (TypeError, ValueError):
                continue
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _finite(value):
    """``value`` with each NaN and +/-Infinity in it (a float or a numpy value, at
    any depth) replaced by None, which json writes as null. A value json cannot
    write is left as it is, for json.dumps to refuse."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    try:
        converted = _json_default(value)
    except TypeError:
        return value
    return value if converted is value else _finite(converted)


def _dumps(result):
    """The json text of ``result``, with NaN and +/-Infinity written as null.
    allow_nan=False makes json raise for any NaN left, instead of writing it."""
    try:
        return json.dumps(result, indent=2, default=_json_default, allow_nan=False)
    except ValueError as e:
        if "Out of range float" not in str(e):
            raise
    # Only a result that holds one pays for the copy without them.
    return json.dumps(_finite(result), indent=2, default=_json_default, allow_nan=False)


class _UnwritableValue(ValueError, TypeError):
    """A value json cannot write, named by where it is: a ValueError, as for the
    other envelope errors, and a TypeError, as json.dumps raised."""


def _json_key_ok(key):
    """True for a dict key json.dumps writes."""
    return key is None or isinstance(key, (str, int, float, bool))


def _unwritable(value, path):
    """(path, problem) for the first value under ``value`` (at ``path``, as in
    results[0].table_data[0][1]) that json.dumps can't write, or None."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if not _json_key_ok(key):
                return path, f"has a key of type {type(key).__name__}, which json cannot write"
            if isinstance(key, str) and key.isidentifier():
                inner = f"{path}.{key}" if path else key
            else:
                inner = f"{path}[{key!r}]"
            found = _unwritable(item, inner)
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            found = _unwritable(item, f"{path}[{i}]")
            if found:
                return found
        return None
    try:
        converted = _json_default(value)
    except TypeError:
        return path, f"is of type {type(value).__name__}, which json cannot write"
    return None if converted is value else _unwritable(converted, path)


def _tsv_cell(value):
    """The text of one tsv cell: empty for None, else str(value)."""
    return "" if value is None else str(value)


def _render_tsv_block(columns, rows):
    """Render one table as a TSV string (header + rows), no trailing newline."""
    lines = ["\t".join(_tsv_cell(c) for c in columns)]
    lines += ["\t".join(_tsv_cell(v) for v in row) for row in rows]
    return "\n".join(lines)


def _emit_diagnostics(result):
    """Echo warnings/errors to stderr so stdout stays a clean data stream."""
    for warning in result.get("warnings", []) or []:
        print(f"warning: {warning}", file=sys.stderr)
    for error in result.get("errors", []) or []:
        print(f"error: {error}", file=sys.stderr)


def _write_text(path, text):
    """Write ``text`` to ``path``, creating parent directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _check_prefix(output_prefix):
    """Raise when ``output_prefix`` is no prefix: write_results(result, "tsv")
    wrote tsv.tsv into the current folder, and write_results(result, args) wrote
    "Namespace(output_prefix=None).tsv". So is a prefix -o refuses, with its
    wording: an empty one (it wrote the hidden file .tsv), and a folder, one that
    ends in a separator ("out/" wrote the hidden file out/.tsv) or exists ("out"
    wrote out.tsv next to the folder out). A path (pathlib.Path) is checked for
    the folder only: Path("tsv") names the files tsv.* on purpose."""
    if output_prefix is None:
        return
    if not isinstance(output_prefix, (str, os.PathLike)):
        hint = ": pass args.output_prefix" if isinstance(output_prefix, argparse.Namespace) else ""
        raise TypeError(
            f"write_results: output_prefix must be a path or None, not {type(output_prefix).__name__}{hint}"
        )
    text = os.fsdecode(output_prefix)
    if isinstance(output_prefix, str) and not text:
        raise ValueError(
            "write_results: output_prefix is empty; pass an output prefix such as 'result', or None to print to stdout"
        )
    if text.endswith(("/", os.sep)) or os.path.isdir(text):
        example = text.rstrip("/" + os.sep) + "/result"
        raise ValueError(
            f"write_results: output_prefix '{text}' is a folder; pass an output prefix such as '{example}'"
        )
    if not isinstance(output_prefix, str):
        return
    if output_prefix.lower() in ("tsv", "json"):
        raise ValueError(
            f"write_results: output_prefix is {output_prefix!r}, a format name: pass it as output_format, "
            f"e.g. write_results(result, args.output_prefix, {output_prefix.lower()!r})"
        )


def write_results(result, output_prefix=None, output_format="tsv"):
    """Serialize a standard result envelope to stdout or file(s).

    Args:
        result (dict): the result envelope (see module docstring).
        output_prefix (str | Path | None): output path prefix WITHOUT extension.
            When ``None``, results are printed to stdout.
        output_format (str): ``"tsv"`` (default) or ``"json"``.

    Returns:
        list[str]: the paths written (empty when printed to stdout).

    Raises:
        ValueError: ``output_format`` is not tsv or json, or ``result`` is not an
            envelope it can write (the message names the table and the key), or,
            in tsv, a value holds a tab or a line break (the message names the
            table, row and column). Also when ``output_prefix`` is the text
            ``"tsv"`` or ``"json"`` (in any case): a format given in its place;
            when it is empty; and when it is a folder: it ends in a separator
            (``"out/"``) or names an existing folder (``"out"``, or a Path), as
            ``-o`` refuses. Nothing is written then. In json, also for a value
            json cannot write, such as a set (the message names where it is; a
            TypeError too, as json.dumps raised); numpy values are written as
            the Python values they hold, and NaN and +/-Infinity as null.
        TypeError: ``output_prefix`` is neither text, a path nor None, such as
            the argparse Namespace itself. Nothing is written then.

    Note:
        TSV renders values with ``str()``, and ``None`` as an empty cell;
        pre-format numeric precision in ``table_data`` if you need fixed decimals. TSV is flat, so table-level
        metadata (warnings/errors/unique_vals/field_ranges) is preserved only by
        the json format.
    """
    _check_prefix(output_prefix)
    output_format = (output_format or "tsv").lower()
    if output_format not in ("tsv", "json"):
        raise ValueError(f"unsupported output format: {output_format!r} (expected 'tsv' or 'json')")
    _check_envelope(result, output_format)

    # ---- JSON: dump the full envelope verbatim (metadata preserved) ----
    if output_format == "json":
        try:
            payload = _dumps(result)
        except TypeError as e:
            # json named the type only ("Object of type set is not JSON serializable").
            found = _unwritable(result, "")
            if found is None:
                raise
            where, problem = found
            raise _UnwritableValue(
                f"write_results: {where} {problem}; convert it to text, a number, a list or a dict"
            ) from e
        if output_prefix is None:
            print(payload)
            return []
        path = f"{output_prefix}.json"
        _write_text(path, payload + "\n")
        print(f"Wrote {path}", file=sys.stderr)
        return [path]

    # ---- TSV: flat, so surface warnings/errors on stderr ----
    _emit_diagnostics(result)
    tables = list(_tables(result))

    if output_prefix is None:
        # stdout: banner-separate multiple tables; a single table gets no banner.
        blocks = []
        for label, (_, columns, rows) in zip(_labels(tables), tables):
            block = _render_tsv_block(columns, rows)
            if len(tables) > 1:
                block = f"--- {label} ---\n{block}"
            blocks.append(block)
        print("\n\n".join(blocks))
        return []

    # file(s): single table -> <prefix>.tsv; multiple -> <prefix>.<type>[-N].tsv
    written = []
    if len(tables) <= 1:
        columns, rows = (tables[0][1], tables[0][2]) if tables else ([], [])
        path = f"{output_prefix}.tsv"
        _write_text(path, _render_tsv_block(columns, rows) + "\n")
        written.append(path)
    else:
        for label, (_, columns, rows) in zip(_labels(tables), tables):
            path = f"{output_prefix}.{label}.tsv"
            _write_text(path, _render_tsv_block(columns, rows) + "\n")
            written.append(path)
    for path in written:
        print(f"Wrote {path}", file=sys.stderr)
    return written
