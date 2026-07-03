"""
Shared result serializer for ngargparser tools (framework-owned).

This module is installed into each project as ``src/core/result_writer.py`` and is
refreshed by ``cli sync``. Do not edit it in a project — edit it in the
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
  ``-o`` is given. A single table gets no banner and a single ``<prefix>.tsv``.
* ``output_format="json"``: the full envelope is dumped verbatim, preserving all
  metadata (warnings/errors/unique_vals/field_ranges/...).

Destination:

* ``output_prefix=None``  -> written to stdout.
* ``output_prefix=<str>`` -> written to file(s) named from the prefix.

For tsv, any ``warnings``/``errors`` in the envelope are echoed to stderr so
stdout stays a clean, pipeable data stream (json keeps them in the payload).
"""
import json
import sys
from pathlib import Path


def _tables(result):
    """Yield ``(table_type, columns, rows)`` for each table in the envelope."""
    for table in result.get("results", []):
        table_type = table.get("type") or table.get("result_type") or "table"
        columns = table.get("table_columns", [])
        rows = table.get("table_data", [])
        yield table_type, columns, rows


def _render_tsv_block(columns, rows):
    """Render one table as a TSV string (header + rows), no trailing newline."""
    lines = ["\t".join(str(c) for c in columns)]
    lines += ["\t".join(str(v) for v in row) for row in rows]
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


def write_results(result, output_prefix=None, output_format="tsv"):
    """Serialize a standard result envelope to stdout or file(s).

    Args:
        result (dict): the result envelope (see module docstring).
        output_prefix (str | Path | None): output path prefix WITHOUT extension.
            When ``None``, results are printed to stdout.
        output_format (str): ``"tsv"`` (default) or ``"json"``.

    Returns:
        list[str]: the paths written (empty when printed to stdout).

    Note:
        TSV renders values with ``str()``; pre-format numeric precision in
        ``table_data`` if you need fixed decimals. TSV is flat, so table-level
        metadata (warnings/errors/unique_vals/field_ranges) is preserved only by
        the json format.
    """
    output_format = (output_format or "tsv").lower()
    if output_format not in ("tsv", "json"):
        raise ValueError(
            f"unsupported output format: {output_format!r} (expected 'tsv' or 'json')"
        )

    # ---- JSON: dump the full envelope verbatim (metadata preserved) ----
    if output_format == "json":
        payload = json.dumps(result, indent=2)
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
        for table_type, columns, rows in tables:
            block = _render_tsv_block(columns, rows)
            if len(tables) > 1:
                block = f"--- {table_type} ---\n{block}"
            blocks.append(block)
        print("\n\n".join(blocks))
        return []

    # file(s): single table -> <prefix>.tsv; multiple -> <prefix>.<type>.tsv
    written = []
    if len(tables) <= 1:
        columns, rows = (tables[0][1], tables[0][2]) if tables else ([], [])
        path = f"{output_prefix}.tsv"
        _write_text(path, _render_tsv_block(columns, rows) + "\n")
        written.append(path)
    else:
        for table_type, columns, rows in tables:
            path = f"{output_prefix}.{table_type}.tsv"
            _write_text(path, _render_tsv_block(columns, rows) + "\n")
            written.append(path)
    for path in written:
        print(f"Wrote {path}", file=sys.stderr)
    return written
