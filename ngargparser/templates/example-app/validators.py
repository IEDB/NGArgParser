# Add any validation logic here. These files can be used along with argument parser.
# For example, all or most arguments should have a validation function defined here.
# Then, these functions can be called from add_argument() by setting the 'type'.
# ex) self.parser_predict.add_argument(
#           "--reference-dir",
#           dest="reference_dir",
#           type=validators.validate_directory)
# ====================================================================================
# A custom validator raises argparse.ArgumentTypeError for a bad value.
import core.set_pythonpath  # This automatically configures PYTHONPATH (first, so libs/ imports work)
import argparse
import json
import re

# The built-in validators, from core/core_validators.py. The parser uses
# validators.validate_file, so keep that one.
from core.core_validators import (
    validate_file,                      # an existing file, not a folder; returns a Path
    validate_directory,                 # an existing folder; returns a Path
    validate_directory_given_filename,  # a file path (not a folder) in an existing folder; returns a Path
)


# ADD ADDITIONAL VALIDATION LOGIC HERE
# ------------------------------------
# The 20 standard amino acids, by their one-letter codes.
AMINO_ACIDS = 'ACDEFGHIKLMNPQRSTVWY'


def _check_amino_acid(value, where):
    """Raise ArgumentTypeError unless `value` is one letter of AMINO_ACIDS (either case)."""
    if not isinstance(value, str) or len(value) != 1 or value.upper() not in AMINO_ACIDS:
        raise argparse.ArgumentTypeError(f"{where}{value!r} is not an amino acid; give one letter of {AMINO_ACIDS}.")


def validate_amino_acid(value):
    """-a: the amino acid to count, one letter of AMINO_ACIDS. countAA() searches
    each peptide with it as a regular expression, so a pattern that does not
    compile is refused first, instead of crashing the count with re.error; -a Z
    and -a LL, which counted nothing and pairs, are refused next. An empty value
    is let through: run_aa_counter.py asks for -a then."""
    try:
        re.compile(value)
    except re.error as e:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid regular expression: {e}")
    if value:
        _check_amino_acid(value, '')
    return value


def _validate_json(path_str, peptide_keys):
    """An existing JSON file holding an object with 'amino_acid' and one of
    `peptide_keys`, checked here so a typo'd key or malformed JSON gives one
    usage line instead of a traceback in the run."""
    path = validate_file(path_str)
    try:
        with open(path) as f:
            content = json.load(f)
    except ValueError as e:  # malformed JSON, or a file that is not text
        raise argparse.ArgumentTypeError(f"'{path_str}' is not valid JSON: {e}.")
    if not isinstance(content, dict):
        raise argparse.ArgumentTypeError(f"'{path_str}' must hold a JSON object, as src/example.json does.")
    keys = ', '.join(repr(key) for key in content) or 'none'
    if 'amino_acid' not in content:
        raise argparse.ArgumentTypeError(f"'{path_str}' has no 'amino_acid' key (its keys: {keys}).")
    if not any(key in content for key in peptide_keys):
        raise argparse.ArgumentTypeError(f"'{path_str}' has no 'peptide' key (its keys: {keys}).")
    _check_amino_acid(content['amino_acid'], f"'{path_str}': amino_acid ")
    peptides = content.get('peptide', [])
    if not isinstance(peptides, list) or not all(isinstance(peptide, str) for peptide in peptides):
        raise argparse.ArgumentTypeError(
            f"'{path_str}': 'peptide' must be a list of peptides, such as [\"ADMGHLKY\", \"ELDDTLKY\"].")
    return path


def validate_input_json(path_str):
    """predict -j: a JSON file such as src/example.json, with 'peptide' (a list
    of peptides) and 'amino_acid' (one letter). A job file preprocess wrote
    names a 'peptide_file_path' in place of 'peptide'."""
    return _validate_json(path_str, ('peptide', 'peptide_file_path'))


def validate_preprocess_json(path_str):
    """preprocess -j: a JSON file such as src/example.json, with 'peptide' and 'amino_acid'."""
    return _validate_json(path_str, ('peptide',))


def validate_input_tsv(path_str):
    """-t: a TSV file such as src/example.tsv: a header line with a 'peptide'
    column, then lines with as many tab-separated columns as the header. A file
    without that header (a bare list of peptides, or a JSON file) lost its first
    line or counted the wrong text, and a line with an extra or a missing column
    was read without a word."""
    path = validate_file(path_str)
    try:
        with open(path) as f:
            # Read as run_aa_counter.py's convert_tsv_to_json reads it.
            header = f.readline().strip().split('\t')
            rows = [line.strip().split('\t') for line in f.readlines()]
    except ValueError as e:  # a file that is not text
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a text file: {e}.")
    if 'peptide' not in header:
        found = ', '.join(repr(column) for column in header)
        raise argparse.ArgumentTypeError(f"'{path_str}' has no 'peptide' column in its header line (found: {found}).")
    for number, row in enumerate(rows, start=2):
        if row == ['']:
            raise argparse.ArgumentTypeError(f"'{path_str}' line {number} is empty.")
        if len(row) != len(header):
            raise argparse.ArgumentTypeError(
                f"'{path_str}' line {number} has {len(row)} columns, but its header has {len(header)}.")
    return path
