# Parser cookbook

Recipes for the two files you edit most: the parser, `src/<Name>ArgumentParser.py`, and the `predict` branch of `src/run_<name>.py`. Each recipe gives the code, then the error you are most likely to hit.

The examples use a tool named `my-app`. The [reference](reference.md) lists every rule and message, and the [README](../README.md) walks you through a whole tool.

## Contents

1. [Read the generated parser](#read-the-generated-parser)
2. [Add an option](#add-an-option)
3. [Check a value with a validator](#check-a-value-with-a-validator)
4. [Change or remove a framework option](#change-or-remove-a-framework-option)
5. [Group options in the help](#group-options-in-the-help)
6. [Write the results](#write-the-results)
7. [Test the parser](#test-the-parser)
8. [Test the predict branch](#test-the-predict-branch)
9. [Call a dependency](#call-a-dependency)
10. [Add a subcommand](#add-a-subcommand)

## Read the generated parser

`cli generate` writes `src/<Name>ArgumentParser.py`, whose notes at the top of `__init__` link this cookbook. This is the file with its comments removed, the description and help text filled in, and an `epilog=` added:

<!-- example: class -->
```python
import textwrap
import argparse
import validators
from core.NGArgumentParser import NGArgumentParser


class MyAppArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()

        self.description = textwrap.dedent(
            '''
            Report the length of each peptide.
            '''
        )

        self.parser_predict = self.add_predict_subparser(
            help='Report peptide lengths.',
            description='Report the length of each peptide in the input file.',
            epilog='See the README for the input format.',
        )

        self.set_subcommand_order(['predict', 'preprocess', 'postprocess'])

        inputs = self.predict_input_group.add_mutually_exclusive_group(required=True)
        inputs.add_argument("--input-tsv", "-t",
                            dest="input_tsv",
                            type=validators.validate_file,
                            help="Input file in TSV format.",
                            )
        inputs.add_argument("--input-json", "-j",
                            dest="input_json",
                            type=validators.validate_file,
                            help="Input file in JSON format.",
                            )
```

| Line | What it does |
|---|---|
| `super().__init__()` | Creates `preprocess` and `postprocess` with their built-in arguments |
| `self.description` | Text at the top of `-h` |
| `self.add_predict_subparser(...)` | Creates `predict` with `--output-prefix/-o` and `--output-format/-f` under "output options", and returns its parser |
| `self.set_subcommand_order(...)` | The order of the subcommands in `-h` |
| `self.predict_input_group` | The "input options" section of `predict -h` |
| `add_mutually_exclusive_group(required=True)` | Makes `-t` and `-j` exclusive and requires one of them: giving both, or neither, exits with code 2 |

`add_predict_subparser` passes other `add_parser` keywords, such as `epilog=`, on to argparse, and refuses `aliases=` and `conflict_handler=` ([Parser setup errors](reference.md#parser-setup-errors)). When you paste over this file, keep the input block: the predict logic reads `args.input_json` and `args.input_tsv`.

The error you are most likely to hit is using `self.parser_predict` or `self.predict_input_group` before `add_predict_subparser()` runs. Every command, `--help` included, stops with exit code 2:

```
src/MyAppArgumentParser.py:29: self.predict_input_group is not ready yet: call self.add_predict_subparser(...) first
```

## Add an option

Add options at the end of `__init__`, 8 spaces in, below the generated `--input-json` option. `self.parser_predict.add_argument` takes what argparse's `add_argument` takes, plus `group=`, the help section:

```python
class MyAppArgumentParser(NGArgumentParser):
    def __init__(self):
        # ... the generated lines, down to the --input-json option ...

        self.parser_predict.add_argument(
            "--min-length",
            dest="min_length",
            type=int,
            default=1,
            help="Skip peptides shorter than this.",
            group="prediction options",
        )
```

The predict branch reads it as `args.min_length`. A user who misspells it gets the closest option, and exit code 2: `unrecognized arguments: --min-lenght 3; did you mean --min-length?`.

A misspelled keyword or a value argparse refuses stops the tool with one line that names your file and line, such as `src/MyAppArgumentParser.py:68: predict argument --min-length: add_argument() got an unexpected keyword argument 'hepl'; did you mean help=?`.

The error you are most likely to hit is reusing a framework option. `predict` owns `--output-prefix/-o` and `--output-format/-f`, and the names `output_prefix` and `output_format` they are stored under. A flag or `dest` of yours that reuses one stops every command with exit code 2:

```
src/MyAppArgumentParser.py:68: predict argument --organism/-o: -o belongs to the framework (--output-prefix); pick another flag, change the framework option with self.parser_predict.update_arguments("--output-prefix", ...), or replace it with your own: call self.parser_predict.remove_argument("--output-prefix") first
```

Pick another flag, or [change or replace the framework option](#change-or-remove-a-framework-option). The [clash rules](reference.md#framework-option-clashes) cover `preprocess` and `postprocess` too.

## Check a value with a validator

A validator takes the command-line text and returns the value to use. For a bad value, it raises `argparse.ArgumentTypeError`, and the tool prints the usage, then your message on one line, and exits 2. Put yours in `src/validators.py`:

<!-- example: validators -->
```python
# src/validators.py
import argparse


def validate_peptide_length(value):
    try:
        length = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("peptide length must be a number")
    if not 1 <= length <= 50:
        raise argparse.ArgumentTypeError("peptide length must be 1 to 50")
    return length
```

Then pass `type=validators.validate_peptide_length` to `add_argument`; the generated parser already imports `validators`. `src/validators.py` imports `core.set_pythonpath` first, so a validator can import code from `libs/`. It also imports the built-in validators:

<!-- example: imports -->
```python
from core.core_validators import (
    validate_file,                      # an existing file, not a folder; returns a Path
    validate_directory,                 # an existing folder; returns a Path
    validate_directory_given_filename,  # a file path (not a folder) in an existing folder; returns a Path
)
```

The parser uses `validators.validate_file`, so keep that import. `validate_directory_given_filename` checks that the folder a file path names exists, and returns the path. It refuses a missing folder with `'nope/x.txt': folder 'nope' does not exist.` and a file in the folder's place with `'f.txt/x.txt': 'f.txt' is not a folder.`

The error you are most likely to hit is a `ValueError` your validator does not catch, such as `int("abc")` without the `try` above. For an option `--peptide-length/-n` that uses it, the tool still prints the usage and exits 2, but with Python's text instead of yours:

```
run_my_app.py predict: error: argument --peptide-length/-n: 'abc' is not valid: invalid literal for int() with base 10: 'abc'
```

Catch the error and raise `argparse.ArgumentTypeError` with your own message, as above. Any other exception gets one line too, such as `argument --allele: the check validate_allele failed on 'X': RuntimeError: boom` ([Validator errors](reference.md#validator-errors)). The sample app's `src/validators.py` has another worked example, `validate_amino_acid`.

## Change or remove a framework option

`update_arguments` on a command's parser changes an option in place: its help, default, choices, required, metavar or help section (`group=`). Pass all its names, or any one of them:

<!-- example: self -->
```python
# make this tool default to json instead of the framework-wide tsv:
self.parser_predict.update_arguments(
    "--output-format", "-f",
    default="json",
    help="Output format (default: json).",
)
```

The option keeps its place in its help section. `group=` moves it to the end of another section, created on first use:

<!-- example: self -->
```python
# list -f with the inputs in predict -h:
self.parser_predict.update_arguments("--output-format", group="input options")
```

`update_arguments` cannot rename an option, and a built-in option keeps its `dest`, because the framework reads `args.output_format`. To replace an option, remove it, then add your own under the same names:

<!-- example: self -->
```python
self.remove_argument("--output-format", "predict")
self.parser_predict.add_argument(
    "--output-format", "-f",
    dest="output_format",
    choices=["tsv", "json"],
    default="json",
    help="Output format (choices: tsv, json; default: json).",
    group="output options",
)
```

`self.parser_predict.remove_argument("--output-format")` removes it the same way. Keep the choices to `tsv` and `json`, which `write_results` writes, and pass `group="output options"`, or the option leaves that section of `predict -h`.

`remove_argument` refuses the four options the framework reads itself: `--params-dir` and `--inputs-dir` of `preprocess`, and `--job-desc-file` and `--input-results-dir` of `postprocess`. Change one with `update_arguments`, or hide it from `-h` with `help=argparse.SUPPRESS`.

The error you are most likely to hit is new choices that leave out the current default. Pass `default=` with them:

```
src/MyAppArgumentParser.py:68: update_arguments can't set choices of --output-format/-f: default 'tsv' is not among the new choices json: pass default= too
```

A misspelled option gets a did-you-mean, and `required=True` on one of the exclusive `-t`/`-j` options is refused: make the group required instead. [Update and remove errors](reference.md#update-and-remove-errors) lists every message.

## Group options in the help

`group="<label>"` on `self.parser_<command>.add_argument` puts the option in a help section of that title, created on first use. A label that names an existing section, such as `"output options"` on `predict`, adds the option there, and so does one of that command's groups, such as `group=self.predict_input_group`. To list an option with the inputs, you can also add it to the input group itself:

<!-- example: self -->
```python
self.predict_input_group.add_argument(
    "--alleles", "-a",
    dest="alleles",
    help="Comma-separated allele names.",
)
```

`--alleles` shows under "input options", but it is not one of the exclusive `-t`/`-j` inputs; to make it one, add it to the generated `inputs` group. The framework's groups and their titles are in the reference's [Argument groups](reference.md#argument-groups). After `self.predict_input_group.title = "inputs"`, `group="input options"` makes a new section, since no section has that title.

A label that differs from a section's title only in case, surrounding spaces or a trailing `s`, such as `group="output option"`, stops the tool with a did-you-mean, so a typo never makes a second section: `predict argument --zeta: group="output option" would add a section next to 'output options'; did you mean 'output options'?`

The error you are most likely to hit is passing a group of another command, such as a `preprocess` group on a `predict` option. A section belongs to one command's help:

```
src/MyAppArgumentParser.py:68: predict argument --gamma: group= takes a section of predict (a title, or one of its groups), not a group of preprocess: use group="optional parameters", or call that group's own add_argument
```

A mutually exclusive group is no section either, so pass its options to the group's own `add_argument`. Only the top-level parser's `self.add_argument` takes no `group=` at all.

## Write the results

Your predict branch, and your `postprocess.py`, call `core.result_writer.write_results` with the *result envelope*: a dict with `results`, a list of tables, and optional `warnings` and `errors`:

```python
from core.result_writer import write_results

result = {
    "warnings": [], "errors": [],
    "results": [
        {"type": "peptide_table",
         "table_columns": ["peptide", "score"],
         "table_data": [["ADMGHLKY", 0.5]]},
    ],
}
write_results(result, args.output_prefix, args.output_format)
```

| Form | Without `-o` | With `-o <prefix>` |
|---|---|---|
| TSV, one table | The table on stdout | `<prefix>.tsv` |
| TSV, several tables | Each table on stdout after a `--- <type> ---` banner | One `<prefix>.<type>.tsv` per table; a repeated type is numbered (`<prefix>.<type>-2.tsv`) |
| JSON | The whole envelope on stdout | `<prefix>.json` with the whole envelope |

For TSV, warnings and errors go to stderr, so stdout stays a clean data stream. JSON keeps the whole envelope, and also writes entries that are not tables, such as plot data. When it writes files, `write_results` creates missing folders and prints `Wrote <path>` on stderr. `predict` defaults to `tsv`, and `postprocess` to `json`.

`write_results` checks the envelope before it writes anything, and raises `ValueError` for a misspelled key, a wrong type or a row of the wrong length ([Envelope checks](reference.md#envelope-checks)). The error you are most likely to hit is the arguments in the wrong order, with a format where the prefix goes:

```
ValueError: write_results: output_prefix is 'tsv', a format name: pass it as output_format, e.g. write_results(result, args.output_prefix, 'tsv')
```

Your users get the same guard on the command line, where `-o` takes only a prefix. A format name such as `tsv`, a value that ends in `.tsv` or `.json`, a folder or an empty value stops the command with exit code 2 and a hint ([Built-in predict arguments](reference.md#built-in-predict-arguments)).

## Test the parser

`parse_args` takes a list, as argparse's does, so a test passes the command line it wants. `cli generate` writes two such tests in `tests/test_parser.py`. pytest puts `src/` on the import path, so a test imports the parser module directly. This one checks the `--min-length` default from [Add an option](#add-an-option):

```python
from MyAppArgumentParser import MyAppArgumentParser


def test_min_length_defaults_to_1(tmp_path):
    peptides = tmp_path / "input.json"
    peptides.write_text('{"peptide": ["ADMGHLKY"]}')
    args = MyAppArgumentParser().parse_args(["predict", "-j", str(peptides)])
    assert args.min_length == 1
```

Run the tests with `uv sync`, then `uv run pytest`. A file argument checks that the file exists, so give it a real one. A missing one exits with code 2 and `argument --input-json/-j: 'missing.json' does not exist.` If your parser overrides `parse_args`, accept and pass on the list: `def parse_args(self, args=None, namespace=None):`, then `args = super().parse_args(args, namespace)`.

`[tool.pytest.ini_options]` in `pyproject.toml` sets `pythonpath = ["src"]` for those imports. Its `testpaths = ["tests"]` keeps the tests that fetched code carries in `libs/` out of the run.

Importing the parser reads `.env`, which a fresh clone lacks: git leaves it out. So `cli generate` also writes `tests/conftest.py`, which runs `./configure` once when `.env` is missing, and `uv sync && uv run pytest` passes in a fresh clone.

The error you are most likely to hit is a dependency path that `paths.py` leaves as `None`. Then `./configure` fails, and pytest stops with exit code 1 and its message:

```
Exit: This tool has no .env, and ./configure, which writes it, failed:
* Detected 1 dependency tools: pepx
✗ pepx_path is None in paths.py. Set it with './configure --pepx-path=<path>', or run 'cli deps remove pepx' to drop this dependency.
  Nothing was changed.
```

Set the path with `./configure --pepx-path=<path>`, then run the tests again.

## Test the predict branch

The entry script's `main()` reads the command line from `sys.argv`, so a test sets `sys.argv`, calls `main()`, and reads what `predict` printed from pytest's `capsys`. This test checks the README's [Quickstart](../README.md#quickstart) `predict` branch; save it as `tests/test_predict.py`:

```python
import run_my_app


def test_predict_prints_one_row_per_peptide(tmp_path, monkeypatch, capsys):
    peptides = tmp_path / "input.json"
    peptides.write_text('{"peptide": ["ADMGHLKY"]}')
    monkeypatch.setattr("sys.argv", ["run_my_app.py", "predict", "-j", str(peptides)])
    run_my_app.main()
    assert capsys.readouterr().out == "peptide\tlength\nADMGHLKY\t8\n"
```

Run it with `uv run pytest`. The `pythonpath = ["src"]` setting lets the test import the entry script, and `tests/conftest.py` runs `./configure` first in a fresh clone, as for the parser tests. `monkeypatch` puts `sys.argv` back after the test.

With `-o <prefix>`, `write_results` writes the file instead, and prints `Wrote <prefix>.tsv` on stderr: read it from `capsys.readouterr().err`, and the file from `tmp_path`. A `sys.exit("message")` in the branch raises `SystemExit` whose `code` is the message, not 1, though the process exits 1. So test it with `pytest.raises(SystemExit)` and compare `code` with the message.

The error you are most likely to hit is leaving out the program name, `sys.argv[0]`. argparse takes the first item as the program name, so `["predict", "-j", path]` gives `-j` before any subcommand, and the test fails with `SystemExit: 2`:

```
predict: error: unrecognized arguments: -j; -j is an option of predict, preprocess and postprocess; put it after the subcommand
```

Put the script name first, as above.

## Call a dependency

`core.app_env.dependency_command(name, *args)` returns the command that runs a dependency and the environment for it. This `predict` branch passes its input to `pepx`, declared with `cli deps add pepx`, and writes pepx's result as its own:

```python
    if args.subcommand == 'predict':
        import json
        import subprocess
        import tempfile
        from core.app_env import dependency_command
        from core.result_writer import write_results

        flag, path = ("-j", args.input_json) if args.input_json else ("-t", args.input_tsv)
        with tempfile.TemporaryDirectory() as tmp:
            argv, env = dependency_command("pepx", "predict", flag, str(path), "-o", f"{tmp}/pepx", "-f", "json")
            child = subprocess.run(argv, env=env, capture_output=True, text=True)
            if child.returncode != 0:
                # The last line of the child's stderr is its error message.
                last = child.stderr.strip().splitlines()[-1:] or ["(no message)"]
                sys.exit(f"pepx failed (exit code {child.returncode}): {last[0]}")
            with open(f"{tmp}/pepx.json") as f:
                result = json.load(f)
        write_results(result, args.output_prefix, args.output_format)
```

The entry script imports `sys` already. When pepx fails, your tool stops with exit code 1 and pepx's last error line, such as `pepx failed (exit code 1): KeyError: 'peptide'`.

`argv` runs pepx's entry script with its virtualenv's `python`, and `env` activates that virtualenv for the child. The reference's [Run a dependency from Python](reference.md#run-a-dependency-from-python) covers a dependency without a virtualenv, an entry script that is not a Python script, and the warnings. To call pepx from a shell, see [Run a dependency from a shell](reference.md#run-a-dependency-from-a-shell).

The error you are most likely to hit is calling a dependency whose path is not set yet, as after `cli deps add`. `dependency_command` raises `DependencyError`, and the entry script prints its message and exits 1:

```
run ./configure --pepx-path=<path>: paths.py declares pepx, but pepx_path is None there
```

Run `./configure --pepx-path=<path>`. The same reference section lists the other messages, such as a misspelled name, an entry script that is not executable, and a dependency in the middle of a redeploy.

## Add a subcommand

`self.add_subcommand(name, help=...)` adds a subcommand of your own, such as `list-alleles`, and returns its parser. Its `add_argument` takes `group=`, as `self.parser_predict.add_argument` does. Add the lines at the end of `__init__`, 8 spaces in:

<!-- example: self -->
```python
self.parser_list_alleles = self.add_subcommand(
    'list-alleles',
    help='List the alleles the tool predicts for.',
)
self.parser_list_alleles.add_argument(
    "--species",
    dest="species",
    default="human",
    help="Species to list (default: human).",
)
```

`run_my_app.py -h` lists `list-alleles` after the framework's three subcommands. Then give it a branch in `main()` of `src/run_my_app.py`, above the `else:` line, where the generated comment marks the place:

```python
    elif args.subcommand == 'list-alleles':
        alleles = {"human": ["HLA-A*02:01", "HLA-B*07:02"], "mouse": ["H2-Kb"]}
        print("\n".join(alleles.get(args.species, [])))
```

`run_my_app.py list-alleles --species mouse` prints `H2-Kb`. A name the framework uses, such as `predict`, stops every command with exit code 2: `src/MyAppArgumentParser.py:68: self.add_subcommand('predict'): predict is a subcommand of the framework; self.add_predict_subparser(...) adds it, or pick another name`.

The error you are most likely to hit is a subcommand without a branch. The parser accepts the command line, and `main()` stops with exit code 1:

```
list-alleles has no branch in src/run_my_app.py: add one in main()
```
