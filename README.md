# ngargparser

[![ngargparser](https://img.shields.io/badge/ngargparser-0.4.1-blue.svg)](https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser)

ngargparser is a framework for IEDB command-line tools that run on HPC (high-performance computing) clusters. Installing it gives you a `cli` command that generates and updates tools, and a Python class, `NGArgumentParser`, that gives every tool the same `preprocess`, `predict` and `postprocess` commands.

In this README, a *tool* is a project you create with `cli generate`, and the *tool folder* is its top folder. The [Glossary](#glossary) defines the other terms.

## Contents

1. [Prerequisites](#prerequisites)
2. [Install](#install)
3. [Quickstart](#quickstart)
4. [Files you edit](#files-you-edit)
5. [Workflow: preprocess, predict, postprocess](#workflow-preprocess-predict-postprocess)
6. [Add arguments and predict logic](#add-arguments-and-predict-logic)
7. [Dependencies](#dependencies)
8. [Call another tool](#call-another-tool)
9. [Build and run](#build-and-run)
10. [Deploy](#deploy)
11. [Upgrade](#upgrade)
12. [Migrate older tools](#migrate-older-tools)
13. [Troubleshooting](#troubleshooting)
14. [Reference](#reference)

## Prerequisites

| You need | For | Check with |
|---|---|---|
| [uv](https://github.com/astral-sh/uv) | Installing ngargparser, creating each tool's `.venv/`, downloading Python | `uv --version` |
| git | Installing from GitLab; `make build` matches `.distignore` with `git check-ignore` | `git --version` |
| make | `make build` and `make clean` | `make --version` |
| SSH access to gitlab.lji.org | The SSH install below, and SSH clones in `scripts/get_libs.sh` | `ssh -T git@gitlab.lji.org` prints `Welcome to GitLab, @<you>!` |
| Python 3.11 | Generated tools set `requires-python = ">=3.11,<3.12"` in `pyproject.toml` | Nothing to check: uv downloads it when it is missing |

Install uv first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

You do not create a virtualenv yourself. `uv` creates `.venv/` in each tool folder, so skip the usual `python -m venv` step.

## Install

The current release is [![ngargparser](https://img.shields.io/badge/ngargparser-0.4.1-blue.svg)](https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser). Install it with `uv tool install`, which puts `cli` on your PATH in every folder:

```bash
# A release tag, over SSH (needs your SSH key on GitLab):
uv tool install 'git+ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.4.1'

# The same release over HTTPS (needs a GitLab access token, because the repository is private):
uv tool install 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.4.1'

# The tip of master, which can hold unreleased changes:
uv tool install 'git+ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git'
```

Check the install with `cli --version`. It prints `cli 0.4.1`, and nothing more when you have the latest release. A version behind the latest adds an update notice; a version ahead of it, or an unreachable GitLab, adds one line ([details](#update-notice)).

To update later, run `cli upgrade` ([Upgrade](#upgrade)). To work on ngargparser itself, see [Contribute to ngargparser](#contribute-to-ngargparser).

## Quickstart

These steps create a tool named `my-app`, give it a working `predict` command, and build its tarball. Start in the folder that should hold the tool.

1. Generate the tool and install its Python packages:

   ```bash
   cli generate my-app
   cd my-app
   uv sync
   ```

   `cli generate` asks no questions, so it also works in scripts. It creates `my-app/` and runs `uv lock`, so `uv.lock` already exists. `uv sync` installs the locked packages into `my-app/.venv/`. Run the remaining steps in `my-app/`.

2. Show the options `predict` already has:

   ```bash
   uv run python src/run_my_app.py predict -h
   ```

   `uv run` runs the entry script, `src/run_my_app.py`, with the tool's `.venv`. The generated `predict` reads an input file given with `--input-json/-j` or `--input-tsv/-t` (one of the two). `--output-prefix/-o` and `--output-format/-f` set where and how it writes results.

3. Write the predict logic. In `src/run_my_app.py`, the `predict` branch holds only comments and `pass`. Replace that whole branch with this code, which reads a list of peptides from the `-j` file and reports the length of each:

   ```python
       if args.subcommand == 'predict':
           import json
           from core.result_writer import write_results

           with open(args.input_json) as f:
               peptides = json.load(f)["peptides"]
           result = {
               "warnings": [],
               "errors": [],
               "results": [{
                   "type": "peptide_table",
                   "table_columns": ["peptide", "length"],
                   "table_data": [[p, len(p)] for p in peptides],
               }],
           }
           write_results(result, args.output_prefix, args.output_format)
   ```

   `write_results` prints the table as TSV, or writes files when `-o` is given. [Result output](#result-output) lists the formats.

4. Create an input file outside the tool folder, then run `predict` on it. `make build` ships every file in the tool folder that `.distignore` does not exclude, so test files belong elsewhere.

   ```bash
   mkdir -p ../my-app-runs
   echo '{"peptides": ["ADMGHLKY", "ELDDTLKY", "FMDHVLRYP"]}' > ../my-app-runs/input.json
   uv run python src/run_my_app.py predict -j ../my-app-runs/input.json
   ```

   Output:

   ```
   peptide	length
   ADMGHLKY	8
   ELDDTLKY	8
   FMDHVLRYP	9
   ```

   Add `-o ../my-app-runs/result` to write a file instead. `-o` is a prefix: the tool adds the extension and writes `../my-app-runs/result.tsv`. Add `-f json` for JSON.

5. Build the tarball:

   ```bash
   make build
   ```

   The build copies the tool into `build/ng_my-app-local/`, checks that the tool starts there, and packs `build/IEDB_NG_MY-APP-local.tar.gz`. Keep editing and running in `my-app/`, because the next build replaces `build/ng_my-app-local/`. [Build and run](#build-and-run) explains the names and how to set a version.

### Try the sample app

`cli generate example` creates `aa-counter/`, a complete tool that counts one amino acid in each peptide. Its `preprocess` and `postprocess` work too, so it shows the whole [workflow](#workflow-preprocess-predict-postprocess). From `my-app/`:

```bash
cd ..
cli generate example
cd aa-counter
uv sync
uv run python src/run_aa_counter.py predict -j src/example.json
```

`aa-counter/README` walks through all three commands. Next, [add your own arguments](#add-arguments-and-predict-logic) or [declare dependencies](#dependencies).

## Files you edit

| File | What goes in it |
|---|---|
| `src/run_my_app.py` | The entry script. Your predict logic goes in its `predict` branch |
| `src/MyAppArgumentParser.py` | The tool description and the `predict` arguments |
| `src/preprocess.py` | `run()`: split the input into job units and write `job_descriptions.json` |
| `src/postprocess.py` | `run()`: combine the results of the job units |
| `src/validators.py` | Your argument checks |
| `pyproject.toml` | Project metadata; `uv add` and `uv remove` edit its dependency list |
| `README` | The end-user README that ships in the tarball |
| `README.md` | A developer note; `cli sync` updates its version badge |
| `paths.py` | Where other tools live on each host; `cli deps add` creates it |
| `scripts/get_libs.sh` | Outside code the build fetches into `libs/` |
| `.distignore` | Files to keep out of the tarball |
| `deploy/install.sh` | Setup steps on the deploy host |

Leave `src/core/`, `scripts/core/build.sh`, `Makefile` and `configure` alone: `cli sync` overwrites them (all of `src/core/` except its empty `__init__.py`). [File ownership](#file-ownership) has the full rule, and [Project tree](#project-tree) shows every file.

## Workflow: preprocess, predict, postprocess

A tool runs a large job in three steps, so a cluster can run the middle step in parallel:

```
input file ──► preprocess ──► job_descriptions.json, plus one parameter file per job unit
                                   │
                                   ▼
                    predict, once per job unit (in parallel)
                                   │
                                   ▼
                    postprocess ──► one combined result
```

A *job unit* is one independent piece of the input, such as the peptides of one length in the sample app. It has its own parameter file, and one `predict` run handles one job unit.

The *job description* file, `job_descriptions.json`, lists every job: one `predict` job per job unit, then one `postprocess` job. A job runner uses it to start each job after the jobs it depends on.

| Command | What it does | Where you write it |
|---|---|---|
| `preprocess` | Checks the input, splits it into job units, writes `job_descriptions.json` | `run()` in `src/preprocess.py` |
| `predict` | Runs the analysis on one job unit | The `predict` branch of `src/run_my_app.py`; arguments in `src/MyAppArgumentParser.py` |
| `postprocess` | Combines the job units' results into one output | `run()` in `src/postprocess.py` |

In a new tool, `preprocess` and `postprocess` check their arguments, then call a `run()` that does nothing until you write it. You can run both already:

```bash
uv run python src/run_my_app.py preprocess -j ../my-app-runs/input.json -o ../my-app-runs/job
mkdir -p ../my-app-runs/final
uv run python src/run_my_app.py postprocess -i ../my-app-runs/job/predict-outputs -p ../my-app-runs/final
```

`preprocess` creates this layout under `-o` before it calls `run()`:

```
<output dir>/
├── predict-inputs/
│   ├── data/        non-JSON inputs, such as FASTA files (--inputs-dir)
│   └── params/      one JSON parameter file per job unit (--params-dir)
├── predict-outputs/
├── aggregate/
└── results/
```

When `paths.py` declares dependencies with a path set, `preprocess` creates the tree once per dependency (without `results/`) and once for the tool, in `<output dir>/<dependency>/` and `<output dir>/<APP_NAME>/`. Unlike `cli deps` and `./configure`, `preprocess` finds dependencies by the `''' [ Title ] '''` line above each block. The folder name is that title in lowercase with spaces as `_`, except that `T Cell Class I` and `T Cell Class II` become `mhci` and `mhcii`. A block counts when any of its `*_path` settings is filled in, so a `<dep>_lib_path` alone counts. A block without its own title counts toward the titled block above it; one before the first title is ignored, and a `paths.py` with no titled block gets the tree above.

`postprocess` takes exactly one input: `--job-desc-file/-j` or `--input-results-dir/-i`. The folder given to `-p` must already exist; otherwise the command stops with `'<dir>' is not a valid directory.`

### Job description fields

The framework does not write `job_descriptions.json`; your `preprocess.py` does. `JobDescriptionParams` in `core.NGArgumentParser` declares the fields of each entry:

| Field | Meaning |
|---|---|
| `shell_cmd` | The command that runs the job |
| `job_id` | The job's number |
| `job_type` | The kind of job; the sample app uses `prediction` and `postprocess` |
| `depends_on_job_ids` | Jobs that must finish first; the `postprocess` job lists every `predict` job |
| `expected_outputs` | Files the job writes |

The sample app writes the file to `<output dir>/job_descriptions.json`. Its first entry looks like this (paths shortened):

```json
{
    "shell_cmd": "/path/to/aa-counter/src/run_aa_counter.py predict -j ../aa-job/predict-inputs/params/0-nigd0vfg.json -o ../aa-job/predict-outputs/result.0 -f json",
    "job_id": 0,
    "job_type": "prediction",
    "depends_on_job_ids": [],
    "expected_outputs": ["../aa-job/predict-outputs/result.0.json"]
}
```

The built-in arguments of each command are listed under [NGArgumentParser API](#ngargumentparser-api).

## Add arguments and predict logic

The parser lives in `src/MyAppArgumentParser.py`. This is the file `cli generate` writes, with its comments removed and the description and help text filled in:

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
        )

        self.set_subcommand_order(['predict', 'preprocess', 'postprocess'])

        inputs = self.predict_input_group.add_mutually_exclusive_group()
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
| `self.add_predict_subparser(...)` | Creates `predict`, adds `--output-prefix/-o` and `--output-format/-f` under "output options", and returns the subparser, kept in `self.parser_predict`. It also takes `formatter_class`; `argparse.RawDescriptionHelpFormatter` keeps the line breaks of a multi-line description |
| `self.set_subcommand_order(...)` | The order of the subcommands in `-h` |
| `self.predict_input_group` | The "input options" section of `predict -h`, created by `add_predict_subparser()` |
| `add_mutually_exclusive_group()` | Makes `-t` and `-j` exclusive: giving both exits with code 2 |

When you paste over this file, keep the input block: the predict logic reads `args.input_json`.

### Add an argument

Add arguments at the end of `__init__`. `self.parser_predict.add_argument` takes everything argparse's `add_argument` takes, plus `group=`, which puts the argument in a help section of that name:

<!-- example: self -->
```python
self.parser_predict.add_argument(
    "--min-length",
    dest="min_length",
    type=int,
    default=1,
    help="Skip peptides shorter than this.",
    group="prediction options",   # arguments with the same label share a section in -h
)
```

To list an argument with the inputs instead, add it to the input group. An argparse group takes no `group=`:

<!-- example: self -->
```python
self.predict_input_group.add_argument(
    "--alleles", "-a",
    dest="alleles",
    help="Comma-separated allele names.",
)
```

`group=` works on `self.parser_predict`, `self.parser_preprocess` and `self.parser_postprocess`. The top-level parser (`self.add_argument`) and argparse groups such as `self.predict_input_group` raise `TypeError: ... got an unexpected keyword argument 'group'`.

`predict` already owns `--output-prefix/-o` and `--output-format/-f`, so give your options other names and letters. The predict branch then reads each value as `args.<dest>`, for example `args.min_length`.

### Change an inherited argument

To change the default, help text, choices or help section of a built-in argument, call `update_arguments` on that command's subparser. It edits the argument in place:

<!-- example: self -->
```python
# make this tool default to json instead of the framework-wide tsv:
self.parser_predict.update_arguments(
    "--output-format", "-f",
    default="json",
    help="Updated output format description",
    group="modified options",
)
```

The change applies to your tool only. A flag on the command line (`-f tsv`) still wins over the default you set. [Updating inherited arguments](#updating-inherited-arguments) covers replacing an argument and the errors both methods raise.

### Check values with a validator

A validator is a function that takes the text from the command line and returns the value to use. For a bad value, it raises `argparse.ArgumentTypeError`, and argparse prints the message and exits with code 2. Put yours in `src/validators.py`:

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

Then pass it as `type=`. The generated parser already imports `validators`:

<!-- example: self -->
```python
# src/MyAppArgumentParser.py
self.parser_predict.add_argument(
    "--peptide-length",
    type=validators.validate_peptide_length,
)
```

`src/validators.py` also re-exports the built-in validators, such as `validate_file`, which `-t` and `-j` use. [Built-in validators](#built-in-validators) lists them.

### Write the results

Nothing writes the results for you: the predict branch must call `core.result_writer.write_results`, as in [Quickstart](#quickstart) step 3. It takes the *result envelope* (a dict with `warnings`, `errors` and a list of tables), `args.output_prefix` and `args.output_format`. [Result output](#result-output) describes the envelope and every output form.

### Test your parser

`parse_args` takes a list, as argparse's does, so a test does not need the real command line:

```python
args = MyAppArgumentParser().parse_args(["predict", "-j", "../my-app-runs/input.json"])
```

`-j` checks that the file exists, so pass a real file. A missing file exits with code 2 and `argument --input-json/-j: 'in.json' is not a valid file.` With no list, `parse_args` reads the command line as usual. If your parser overrides `parse_args`, accept and pass on the list: `def parse_args(self, args=None, namespace=None):`, then `args = super().parse_args(args, namespace)`.

The Python blocks in this README marked with an `<!-- example: ... -->` comment are pasted into a freshly generated tool and run by `tests/test_readme_examples.py`.

## Dependencies

ngargparser manages three kinds of dependencies, each with its own command:

| Kind | Manage with | Recorded in |
|---|---|---|
| Python packages (numpy, pandas, ...) | `uv` | `pyproject.toml` and `uv.lock` |
| Other programs installed on each host (often other IEDB tools), with their virtualenvs, environment modules and `LD_LIBRARY_PATH` entries | `cli deps` and `./configure` | `paths.py` |
| Code and data the tool loads from `libs/`: git repositories, executables, data, software you download by hand | `scripts/get_libs.sh`, run by `make build` | `scripts/get_libs.sh` |

### Python packages

```bash
uv sync                                          # install the locked packages into .venv/
uv add pandas                                    # add a package; updates pyproject.toml and uv.lock
uv remove pandas                                 # remove it
uv run python src/run_my_app.py --help           # run with the tool's .venv
```

Commit `uv.lock`, so every deploy installs the same versions.

### Other programs: `cli deps` and `./configure`

A *dependency* here is another program that your tool runs, often another IEDB tool, installed somewhere on each host. `paths.py` records where. Declare it, then set its path:

```bash
cli deps add mhci-predictor                          # writes a stub block to paths.py
./configure --mhci-predictor-path=/opt/tools/mhci    # sets the path, then writes .env and setup_mhci_predictor_env.sh
```

`./configure` prints what it did, and warns about anything that may fail later:

```
* Set mhci_predictor_path = '/opt/tools/mhci' in paths.py
* .env file updated
* Detected 1 dependency tools: mhci_predictor
⚠  mhci_predictor_path = '/opt/tools/mhci' does not exist. 'mhci_predictor' may fail until this is corrected.
⚠  mhci_predictor has no virtualenv. It will run under whatever Python is active. Set one with './configure --mhci-predictor-venv=<path>' if it needs its own.
* Shell script for 'mhci_predictor' created at 'setup_mhci_predictor_env.sh'
```

`cli deps add` writes a *stub block* of four settings, all `None`: `<dep>_path`, `<dep>_venv`, `<dep>_module` and `<dep>_lib_path`. Only the path is required. Every declared dependency needs one: a path left at `None` stops `./configure` with exit code 1:

```
✗ pepx_path is None in paths.py. Set it with './configure --pepx-path=<path>', or run 'cli deps remove pepx' to drop this dependency.
```

| Command | What it does |
|---|---|
| `cli deps add <dep> [<dep> ...]` | Writes one stub block per name to `paths.py` |
| `cli deps remove <dep> [<dep> ...]` | Removes those blocks (alias `rm`); backs up `paths.py` to `paths.py.bak` first |
| `cli deps list` | Lists the declared dependencies; `●` means the path is set, `○` means it is still `None` (alias `ls`) |
| `cli deps` | Opens an interactive add and remove menu. Without `paths.py`, it asks for names first and creates the file. It needs a terminal: in a script or CI job it stops with exit code 1 |
| `./configure --<dep>-path=<path>` | Sets a setting in `paths.py` (also `--<dep>-venv`, `--<dep>-module`, `--<dep>-lib-path`), then regenerates `.env` and the setup scripts |
| `./configure` | Regenerates `.env` and one `setup_<dep>_env.sh` per dependency from `paths.py`, for example after a hand edit |

`cli deps remove` accepts the name you added (`mhci-predictor`), the title form (`Mhci Predictor`) or the variable form (`mhci_predictor`). `./configure` runs with the tool's `.venv` when it has one, so it works right after `uv sync` without activating anything. [./configure internals](#configure-internals) covers what counts as a dependency, how the virtualenv is found, and the warnings.

### Code in `libs/`: `scripts/get_libs.sh`

Code and data your tool imports or reads from `libs/` (git repositories such as allele-validator, predictor executables, percentile data) are downloaded by `scripts/get_libs.sh`. This differs from `cli deps`, which records where other tools live on a host. `cli generate <name>` creates the script; `cli generate example` does not.

```bash
# scripts/get_libs.sh runs in an empty temp dir; each top-level entry it leaves lands in libs/
git clone -b v0.5.4 --single-branch --depth 1 \
    ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/allele-validator.git
rm -rf allele-validator/.git
```

`make build` runs it first, and only when needed:

| Situation | What happens |
|---|---|
| First build, `scripts/get_libs.sh` changed, or an entry it fetched is missing from `libs/` | get_libs.sh runs; on success each entry replaces `libs/<entry>` |
| get_libs.sh unchanged, and everything it fetched is still in `libs/` | Skipped |
| get_libs.sh fails | `libs/` stays as it was and the build stops |
| You want a fresh download anyway | Delete the fetched folders in `libs/` (listed in `libs/.gitignore`), then `make build` |

Third-party software you download by hand (for example, licensed software behind a download form) goes in get_libs.sh too. Each developer points an environment variable at an unpacked copy, and get_libs.sh copies it into the temp dir. Example 3 in the template shows the pattern: it copies `$MYTOOL_DIR` when that variable is set, builds without the tool in CI (where `CI` is set), and stops with an error otherwise. A local build must stop rather than skip, because a skipped fetch is remembered as done: setting the variable afterwards has no effect until get_libs.sh changes. To switch to a different copy later, delete its folder in `libs/` and build again.

Never write a token into get_libs.sh. Read it from a CI/CD variable such as `TOOLS_DEPLOY_TOKEN`, as Example 1 in the template does. [get_libs internals](#get_libs-internals) covers what the build does with fetched entries.

## Call another tool

Every tool publishes its entry script in its own `.env` as `APP_ENTRY`. `cli generate` writes `APP_ENTRY=src/run_<name>.py`, and `./configure` keeps it or finds the single `src/run_*.py`. A tool with several `run_*.py` scripts must set `APP_ENTRY=src/<script>` in `.env` itself.

A tool that depends on it reads that value: its `./configure` exports the full path as `<DEP>_ENTRY`, next to `<DEP>_PATH`, in `.env` and in `setup_<dep>_env.sh`. Call the dependency through it instead of hard-coding the script's file name, which can change between versions:

```bash
source setup_tcell_class_i_env.sh
python3 "$TCELL_CLASS_I_ENTRY" predict -j job.json -o out
```

A dependency that publishes no `APP_ENTRY` (an older tool, or one not built with ngargparser) gets no `<DEP>_ENTRY`.

At run time each tool reads its own `.env`. `APP_ROOT` and `APP_NAME` always come from that file, so a tool started by another tool (the IEDB tool phbr starting tc1, for example) imports its own `libs/` instead of the caller's. Other keys keep python-dotenv's rule: a value already set in the environment wins.

## Build and run

```bash
make build                      # build the tarball; shows a progress bar
make build-verbose              # the same, with the full build output
make clean                      # remove build/; fetched dependencies in libs/ stay
TOOL_VERSION=1.0.0 make build   # build version 1.0.0 instead of "local"
```

`make build` names its output after the tool folder and the version:

| Item | Name | `my-app` at the default version |
|---|---|---|
| Version | The `TOOL_VERSION` environment variable, default `local`; a `/` becomes `-` | `local` |
| Build copy | `build/ng_<name>-<version>/` | `build/ng_my-app-local/` |
| Tarball | `build/IEDB_NG_<NAME>-<version>.tar.gz`, with `<NAME>` in capitals | `build/IEDB_NG_MY-APP-local.tar.gz` |

CI often passes the git tag or branch as `TOOL_VERSION`, so a branch such as `feature/foo` builds as `feature-foo`. A build replaces its own build copy and tarball; builds of other versions stay in `build/` until `make clean`. [`scripts/core/build.conf`](#build-settings-buildconf) can change both names.

The build needs `git` on the machine that runs it: it evaluates `.distignore` with `git check-ignore` and fingerprints `scripts/get_libs.sh` with `git hash-object`. [Build pipeline](#build-pipeline) lists every step.

### Run from the tool folder

Edit, run and debug in the tool folder. Once `make build` has fetched `libs/`, the tool folder has everything the tool needs to run, so you need no build between fixes:

```bash
uv run python src/run_my_app.py predict -j ../my-app-runs/input.json -o ../my-app-runs/result
```

Use `uv run python` or `.venv/bin/python`. A bare `python` usually lacks the tool's packages and fails with `ModuleNotFoundError: No module named 'dotenv'`.

`build/` is output. Its files are read-only, so an editor refuses to save a fix there, where the next build would erase it. Its folders stay writable, so `make clean`, the next build, and new files written by `./configure` keep working. Rewriting an existing file there fails, for example `./configure --<dep>-path=...`, which edits `paths.py`; do that in the tool folder.

### What ships in the tarball

The tarball holds one folder, `ng_<name>-<version>/`, with every file of the tool folder except:

- hidden files and folders (names starting with `.`), unless `.distignore` re-includes them
- anything `.distignore` excludes; the default file excludes `__pycache__/`, `build.sh`, `build.md`, `Makefile` and `*.sh`
- the tool folder's `build/` and `.git/`, always

`README` and `deploy/install.sh` always ship, whatever `.distignore` says, because the deploy orchestrator uses them ([Deploy](#deploy)). These rules cover only the top-level `README` and `deploy/`; a nested `README` or `deploy/` folder follows your rules like any other file. The build adds a `VERSION` file and writes the version into `README` (see [Build pipeline](#build-pipeline)). `README.md` ships too unless `.distignore` lists it. `.distignore` does not apply to entries fetched by `scripts/get_libs.sh`; the build only strips version-control folders and caches from them ([get_libs internals](#get_libs-internals)).

`.env` does not ship unless `.distignore` re-includes it, and it should not: its `APP_ROOT` is an absolute path on the build machine. An extracted tarball stays unconfigured until `./configure` writes a new `.env` there ([Deploy](#deploy)).

### `.distignore` rules

`.distignore` sits at the tool folder's top, next to `.gitignore`, and lists paths that must not ship in the tarball or the build copy. It has exact `.gitignore` semantics, so you can paste `.gitignore` content as is: wildcards, folder-only `name/`, `!` negation, `/` anchoring and `**` all work as in `.gitignore`: a pattern with no `/`, or only a trailing one, matches at every depth, and a pattern with a leading or middle `/` matches from the tool folder's top.

The build adds rules around your file:

- `.*` excludes hidden files and folders by default. It has the lowest precedence, so a `!` line in your file re-includes them:

  ```gitignore
  !.env              # ship a hidden file
  !.streamlit/       # ship a hidden folder and everything in it
  ```

  `.env` appears only as a syntax example: do not ship it. Its `APP_ROOT` is an absolute path on the build machine, so a shipped `.env` points every target host at a folder that does not exist there. `./configure` writes the right one at install time.

  To ship only one file from a hidden folder, re-include the folder first (gitignore cannot re-include a file whose parent folder is excluded), then exclude the rest again:

  ```gitignore
  !.streamlit/
  .streamlit/*
  !.streamlit/config.toml
  ```

- `.git/` and `build/` at the tool folder's top are always excluded; no `!` rule brings them back.
- `README` and `deploy/install.sh` are always included, so no rule (including the default `*.sh`) strips them. A nested `README` or `deploy/` folder follows your rules like any other file.

The rules reach every depth of the tool folder, including code you keep in `libs/`. A symlink is copied as a link, so rules never follow it into its target. `.distignore` does not apply to entries fetched by `scripts/get_libs.sh`.

### Build settings: `build.conf`

`scripts/core/build.conf` holds the build settings:

```bash
# Tarball filename prefix. Empty falls back to IEDB_, the prefix the deploy orchestrator expects.
TARBALL_PREFIX="IEDB_"
```

| Setting | Default | Effect |
|---|---|---|
| `TARBALL_PREFIX` | `IEDB_` | Start of the tarball name |
| `TOOL_NAME` | `ng_<name>` | Name of the build copy (`build/<TOOL_NAME>-<version>/`); the tarball uses it in capitals |

`build.conf` sits in `scripts/core/` next to `build.sh`, but it is yours: `cli sync` never overwrites it. Older tools keep it in `scripts/`. `cli sync` moves it when `scripts/core/build.conf` does not exist yet, and the build stops while `scripts/build.conf` is still there. If both exist, sync warns about it: merge them by hand and delete `scripts/build.conf`. `EXCLUDE_FROM_BUILD_SYMLINK` has no effect any more, because the build copies everything; a leftover setting prints a notice.

### Build hook: `scripts/hooks.sh`

The build hook is optional, and most tools do not need it: `cli generate` does not create it, and the build skips the step when it is missing. Files to keep out of the tarball belong in `.distignore`, and outside code belongs in `scripts/get_libs.sh`.

To add one, copy the template from the installed ngargparser. With a `uv tool install`, the templates sit inside uv's tool folder:

```bash
cp "$(uv tool dir)"/ngargparser/lib/python3*/site-packages/ngargparser/templates/hooks.sh scripts/hooks.sh
```

In a clone of the ngargparser repository, the same file is `ngargparser/templates/hooks.sh`.

The hook runs on the build copy after the copy, the first symlink check and the `README` version stamp, and before `VERSION`, the second symlink check, the startup check and the tarball ([Build pipeline](#build-pipeline)). It works on that copy, so nothing it changes reaches the tool folder. Use it to patch copied files, generate code, sign, or validate. Downloads and local copies of third-party software belong in `scripts/get_libs.sh`, which fetches once into the tool folder instead of on every build. Symlinks a hook creates must be relative and stay inside the build.

The hook runs in `build/<TOOL_NAME>-<version>/libs/`, with these variables exported:

| Variable | Value |
|---|---|
| `BUILD_DIR` | The build copy, `<tool folder>/build/<TOOL_NAME>-<version>` |
| `PROJECT_ROOT` | The tool folder |
| `APP_NAME` | The tool folder's name |
| `TOOL_NAME` | `ng_<name>`, or the value in `build.conf` |
| `TOOL_VERSION` | The version, default `local` |
| `TOOL_DIR` | `<TOOL_NAME>-<version>` |
| `SRC_DIR` | The tool's `scripts/` folder (not `src/`) |

### Which script runs when

The three `*.sh` files you own run at different times, in different places:

| | `scripts/get_libs.sh` | `scripts/hooks.sh` | `deploy/install.sh` |
|---|---|---|---|
| When | Early in `make build` (step 3, after the preflight checks): first build, after a change to the script, or when a fetched entry is missing | Every `make build`, after copying, if the file exists | Deploy time, on the target host |
| Run by | `scripts/core/build.sh` | `scripts/core/build.sh` | The nxg-tools-deployments orchestrator |
| Purpose | Bring all outside code into the tool's `libs/` (git repositories, archives, data, software you download by hand) | Patch the build copy (code generation, edits, extra files) | Set up the extracted tarball on the host (`uv sync`, `./configure`, editing `paths.py`) |

## Deploy

`make build` produces a tarball, and **nxg-tools-deployments**, the IEDB deploy orchestrator at `gitlab.lji.org/iedb/tools/tools-redesign/nxg-tools-deployments`, takes it from there:

```
developer             GitLab CI                     target server
─────────             ─────────                     ─────────────
git push  ────────►   make build
                      tarball as CI artifact  ────► nxg-tools-deployments fetches it
                                                    extracts the tarball
                                                    runs bash deploy/install.sh
                                                    flips the `current` symlink on success
```

The orchestrator downloads the CI artifact `build/<package_name>-<version>.tar.gz`, runs `deploy/install.sh` from the extracted folder when the tarball has one, and its publish step copies the top-level `README` out of the tarball. `cli generate` creates both files, and the build always ships them ([What ships](#what-ships-in-the-tarball)).

### `deploy/install.sh`

The orchestrator runs `deploy/install.sh` on the target host after it extracts the tarball, from the extracted folder. The generated script installs uv when it is missing, runs `uv sync` to create `.venv`, activates it, and runs `./configure` when present. The `./configure` step writes the host's `.env`, so an orchestrator deploy needs no extra step.

The orchestrator passes five variables: `IEDB_STANDALONE_NAME`, `IEDB_VERSION`, `IEDB_VERSION_DIR`, `IEDB_STANDALONE_DIR` and `IEDB_PREVIOUS_VERSION_DIR`. A generated install script finds everything from `${BASH_SOURCE[0]}`. Tools that call other tools use `IEDB_STANDALONE_DIR` to find and wire in those peer tools; axelf, phbr and peptide-variant-comparison do.

Do not hardcode `UV_PYTHON_INSTALL_DIR`, `UV_CACHE_DIR` or `UV_PYTHON_PREFERENCE` in install.sh. They come from the target's `install_shell_init` snippet, which the orchestrator sources before it runs install.sh, and they let the cluster share one `/apps/uv/` cache across users. Hardcoding them locks cluster paths into every release tarball and breaks installs on developer laptops.

### Extract a tarball by hand

Outside the orchestrator, configure the extracted folder yourself before you run anything under `src/`:

```bash
tar -xzf IEDB_NG_MY-APP-1.0.0.tar.gz
cd ng_my-app-1.0.0
uv sync && ./configure
```

Without that step the entry script exits with code 1 at import time. Its message starts with `This project has not been configured yet` and ends:

```
Run './configure' from the project root (/path/to/ng_my-app-1.0.0), then re-run this command.
```

## Upgrade

Two commands update two different things:

| You want to | Run | Where |
|---|---|---|
| Update the installed ngargparser (the `cli` command), for example before `cli generate` | `cli upgrade` | Any folder |
| Bring one tool's framework files up to the installed ngargparser | `cli sync` | Inside that tool folder |

They overlap in one place: `cli sync` upgrades ngargparser first (except with `--no-upgrade` or `--dry-run`, or for a plain `cli sync` from a local checkout), so getting a tool onto the latest framework takes one command. `cli upgrade` is the only way to update ngargparser without touching a tool, and `--check` exists only on `cli upgrade`. Both commands take `--ref <version>` and `--dev`.

### `cli upgrade`

`cli upgrade` (alias `u`) updates the installed ngargparser to the latest release tag on GitLab. It is the one command to update ngargparser (0.2.4 and later): it detects how ngargparser was installed (`uv tool install`, a pip venv, or a uv venv without pip) and runs the matching installer, so you never type a long `git+https://...` command. It works from any folder and changes no tool files.

```bash
cli upgrade --check        # is a newer release available? installs nothing
cli upgrade                # update to the latest release tag
```

Only *release tags* count as the latest: `vX.Y.Z`, where the `v` is optional and a `+build` suffix is allowed. `cli upgrade`, `cli sync`, the update notice and `cli --version` skip pre-release tags such as `v0.4.2-test` or `v1.0.0-rc1`, so a test tag never reaches other developers by default. Install one on purpose with `cli upgrade --ref v0.4.2-test` or `cli sync --ref v0.4.2-test`. `--dev` installs the tip of master.

After an install, `cli upgrade` reminds you that tools need `cli sync`:

| Where you run it | What it prints |
|---|---|
| Inside a tool that is behind, after installing | `You upgraded the CLI to Y, but this project is still on X.` then ``Run `cli sync` here to update this project to Y.`` |
| Inside a tool that is behind, when ngargparser was already current | `The CLI is on Y, but this project is still on X.` and the same second line |
| Anywhere else, after installing | `` `cli upgrade` updates the CLI, not your projects. `` then ``Run `cli sync` inside a project to update it to Y.`` |

Inside a tool that is current, and outside a tool when nothing was installed, it prints no reminder.

#### Upgrade from before 0.2.4

`cli upgrade` did not exist before 0.2.4. To get it the first time, reinstall the package with whatever installed it. You do this once; after that, `cli upgrade` is enough:

```bash
# uv tool install:
uv tool install --force --reinstall 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.2.4'
# pip venv:
pip install --upgrade --force-reinstall 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.2.4'
# uv venv without pip:
uv pip install --reinstall 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.2.4'
```

#### Update notice

When you run `cli`, it sometimes prints a short notice that a newer version is available. It checks GitLab at most once a day, shows the notice only in an interactive terminal, and never in CI. Silence it with `NGARGPARSER_NO_UPDATE_CHECK=1`; set the variable to `0` or `false`, or unset it, to turn the notice back on.

`cli --version` and `cli --help` check on every run, with no daily limit and no cache. Each prints its usual output, then looks up the latest release tag:

| Result | Extra output |
|---|---|
| Installed version is current | None: `cli --version` prints only `cli 0.4.1` |
| Installed version is behind | The update notice, with a link to the release notes |
| Installed version is ahead of the latest tag | A line that says so |
| GitLab cannot be reached | A line that says so |

The lookup is skipped in CI and when `NGARGPARSER_NO_UPDATE_CHECK=1` is set. Subcommand help (`cli sync --help`) and bare `cli` keep the once-a-day notice.

### `cli sync`

`cli sync` (alias `s`) refreshes a tool's framework-owned files to match the installed ngargparser. It upgrades ngargparser first, except with `--no-upgrade` or `--dry-run`, or for a plain `cli sync` from a local checkout (see below). Run it from inside the tool folder:

```bash
cd path/to/your-tool
cli sync
```

| What `cli sync` changes | How |
|---|---|
| `src/core/NGArgumentParser.py`, `core_validators.py`, `result_writer.py`, `app_env.py`, `set_pythonpath.py`, `configure.py` | Overwrites each one whose content differs |
| `scripts/core/build.sh`, the top-level `Makefile` and `configure` | Overwrites each one whose content differs |
| `deploy/install.sh` | Creates it from the template when missing; never overwrites it |
| `.gitignore` | Creates it when missing; otherwise adds only the missing rules to the `# --- ngargparser (managed) ---` section |
| `pyproject.toml` | Writes `[tool.ngargparser] scaffold_version` (adds it when missing) |
| `README.md` | Sets the ngargparser badge to the installed version and turns it green, to mark the tool as synced; adds the badge below the title when missing. `README` is never touched |
| `scripts/build.sh`, `scripts/dependencies.sh`, `scripts/build_hooks.sh`, `scripts/build.conf` | Moves these legacy names to `scripts/core/build.sh`, `scripts/hooks.sh` and `scripts/core/build.conf`, when the new name does not exist yet |

Before overwriting a framework file, sync copies it to `.ngargparser/sync-backups/<timestamp>/`. `--no-backup` skips the copies. It never overwrites `validators.py`, `<Name>ArgumentParser.py`, `run_<name>.py`, `preprocess.py`, `postprocess.py`, `paths.py`, `hooks.sh`, `get_libs.sh`, `build.conf`, `.distignore`, `README` or anything else you own. A tool that still has `requirements.txt` and no `pyproject.toml` gets a migration hint.

When ngargparser is an editable install of a local checkout, a plain `cli sync` skips the upgrade and syncs from the checkout: the latest release would replace your working copy and copy older framework files into the tool. `cli upgrade` installs a release on purpose, and `--ref <version>` or `--dev` still upgrades.

`cli sync --dry-run` (`-n`) writes nothing and installs nothing, so its preview uses the installed version's files. It says first which ngargparser a real run would install, and warns when a named version would replace your local checkout.

#### How a sync ends

`cli sync` restores every file when the tool stops starting or the sync fails. Each run ends in one of these ways:

| Outcome | When | Exit code |
|---|---|---|
| Synced and verified | A framework file changed or a legacy file moved, and every help screen that worked before the sync still works | 0 |
| Synced without a startup check | No framework file needed changing (the badge, stamp, `.gitignore` or `deploy/install.sh` may still change), or the checks are off (`--no-verify` or `NGARGPARSER_SYNC_NO_VERIFY=1`) | 0 |
| Synced, could not verify | The tool did not start even before the sync, has no `.venv` interpreter, or has no `src/run_*.py` or several | 0 |
| Stopped, nothing changed | The tool has a [known clash](#sync-safety-checks) with this framework version | 1 |
| Rolled back | A help screen that worked before fails after, or sync hit an error; every file is restored | 1 |
| Interrupted | You pressed Ctrl-C; sync restores every file, then stops with the interrupt | Nonzero |
| Not a tool folder | The current folder has no `src/` or no `scripts/` | 1 |
| Upgrade failed | The installer failed before anything was synced; rerun with `cli s --no-upgrade` | The installer's exit code |

[Sync safety checks](#sync-safety-checks) explains the checks behind these outcomes.

#### What sync cannot do

For breaking changes (file layouts shifting, schema changes, removed APIs), sync keeps your framework files current but does not transform content you edited. Read the release notes, generate a fresh tool with the new framework, and port your code over.

### Contribute to ngargparser

To work on ngargparser itself, clone it and let uv create a `.venv` with an *editable install* (one that runs the code in your clone):

```bash
git clone git@gitlab.lji.org:iedb/tools/tools-redesign/global-dependencies/ngargparser.git
cd ngargparser
uv sync               # .venv with ngargparser installed from the clone
uv run pytest         # tests
uv run ruff check .   # lint
```

`uv pip install -e .` installs the clone the same way into the active virtualenv (or the one named with `--python`); `pip install .` installs a copy that does not follow later edits.

`cli upgrade --ref .` installs ngargparser from your working tree in place of a release tag. Pass `.` (from the repository) or a path to a checkout; `--check` shows what it would install:

```bash
cli upgrade --ref .                 # install the working tree (run from the ngargparser repository)
cli upgrade --ref /path/to/repo     # the same, from anywhere
cli upgrade --ref . --check         # show what it would install, install nothing
```

## Migrate older tools

Tools generated before the current build layout need a one-time migration.

### Removed layouts

The build stops with a message that names the fix when it finds a removed layout:

| Found | Build message |
|---|---|
| `scripts/do-not-distribute.txt`, a top-level `do-not-distribute.txt`, or `scripts/.distignore`, with no top-level `.distignore` | `<file> is no longer read. Move it to .distignore at the project root (same syntax): git mv <file> .distignore` |
| `scripts/build_hooks.sh` or `scripts/dependencies.sh`, with no `scripts/hooks.sh` | `scripts/<file> is no longer run. Rename it to scripts/hooks.sh (cli sync does this for you).` |
| `scripts/build.conf` | `scripts/build.conf is no longer read. Move it to scripts/core/build.conf (cli sync does this for you).` |
| `scripts/deps.sh` | `scripts/deps.sh is no longer run. Rename it to scripts/get_libs.sh.` |
| `libs` that is a symlink | `libs/ is a symlink (-> <target>). Replace it with a real directory: ...` |
| `libs` that is a file | `libs exists but is not a directory.` |

`cli sync` renames these legacy names on its first run: `scripts/build.sh` to `scripts/core/build.sh`, `scripts/dependencies.sh` or `scripts/build_hooks.sh` to `scripts/hooks.sh`, and `scripts/build.conf` to `scripts/core/build.conf`. It renames only when the new name does not exist yet. When both `scripts/dependencies.sh` and `scripts/build_hooks.sh` exist, only `dependencies.sh` is renamed; `build_hooks.sh` stays and never runs. Sync warns about any legacy file it could not move, so merge that file into its new name and delete it.

### Move a tool to `scripts/get_libs.sh`

The build copies the tool into `build/` instead of linking to it. Each existing tool needs these steps once:

1. Run `cli sync` to get the new `scripts/core/build.sh`. Sync also renames `scripts/dependencies.sh` or `scripts/build_hooks.sh` to `scripts/hooks.sh`, and moves `scripts/build.conf` to `scripts/core/build.conf`.
2. Copy `scripts/get_libs.sh` from a fresh `cli generate <name>`, or from the templates folder ([Build hook](#build-hook-scriptshookssh) shows where it is). Move every clone and download from `scripts/hooks.sh` into it. get_libs.sh runs in its own temp dir, so drop `$BUILD_DIR` from those lines. Copies of software you downloaded by hand move too (template Example 3). Keep patching steps in hooks.sh; if none are left, delete hooks.sh.
3. If the tool has `scripts/do-not-distribute.txt` and no top-level `.distignore`, run `git mv scripts/do-not-distribute.txt .distignore`. The syntax is the same.
4. Delete `EXCLUDE_FROM_BUILD_SYMLINK` from `scripts/core/build.conf`.
5. Delete hand-fetched copies in `libs/` that get_libs.sh now fetches; the build refuses to overwrite entries it did not fetch. If `libs/` itself is a symlink, replace it with a real folder.
6. Keep `scripts/get_libs.sh` out of the tarball. The default `.distignore` does this with `*.sh`; a `.distignore` that lists scripts by name needs `/scripts/get_libs.sh`.
7. Remove, or list in `.distignore`, any folder whose symlinks are absolute, point outside the tool folder, or point at a file the build does not copy (a hidden or excluded file). The build fails on them instead of shipping a broken link.
8. Run `uv sync` and `make build`, then compare the tarball contents with the last release. Each tarball's top folder carries its version, so strip it before comparing:

   ```bash
   diff <(tar -tzf old.tar.gz | sed 's|^[^/]*/||' | sort) <(tar -tzf build/<new>.tar.gz | sed 's|^[^/]*/||' | sort)
   ```

9. Keep `TOOLS_DEPLOY_TOKEN` in CI. A CI checkout starts with an empty `libs/`, so every CI build fetches.

## Troubleshooting

The first errors newcomers hit, with the exact message and the fix.

### Invalid project name

```
✗ Invalid project name '1abc'. Use a letter followed by letters, digits, underscores, or hyphens (e.g. 'aa-counter').
```

The name becomes a folder, a Python module (`run_<name>.py`) and a class name. It must start with a letter and contain only letters, digits, `_` and `-`. A name with `/` stops with `Project name must not contain path separators`.

### Folder already exists

```
✗ 'my-app' already exists; aborting.
```

`cli generate` writes nothing when the folder exists. Pick another name, or run it from another folder.

### Not configured yet after cloning

A tool cloned from a colleague's repository stops with:

```
Run './configure' from the project root (/path/to/tool), then re-run this command.
```

The first line of the message starts with `This project has not been configured yet`. `.env` holds absolute paths for one machine, so `.gitignore` keeps it out of git and a clone has none. Create it from the tool folder:

```bash
uv sync && ./configure
```

The same applies to a tarball you extract by hand.

### No module named dotenv

```
ModuleNotFoundError: No module named 'dotenv'
```

You ran the entry script with a Python that is not the tool's. Run it with `uv run python src/run_<name>.py ...` or `.venv/bin/python src/run_<name>.py ...`, after `uv sync`.

### git missing at build time

```
ERROR: 'git' is required to build (distignore matching uses 'git check-ignore'). Install git and retry.
```

Install git on the build machine. The build uses it even when the tool is not a git repository.

### SSH authentication fails at install

```
error: Git operation failed
...
git@gitlab.lji.org: Permission denied (publickey,gssapi-keyex,gssapi-with-mic,password).
```

GitLab did not accept an SSH key from this machine. Add your public key under your GitLab user settings (SSH Keys), then check with `ssh -T git@gitlab.lji.org`, which prints `Welcome to GitLab, @<you>!`. Or use the HTTPS install form with an access token.

### A dependency path is None

```
✗ pepx_path is None in paths.py. Set it with './configure --pepx-path=<path>', or run 'cli deps remove pepx' to drop this dependency.
```

`./configure` exits with code 1 while any declared dependency has no path. Run the command the message names.

## Reference

### CLI cheatsheet

```
cli --version                 # installed version; checks GitLab for a newer release on every run
cli --help                    # usage; the same release check on every run

cli generate <name>           # create a new tool (alias: g)
cli generate example          # create the sample app, aa-counter/

cli deps                      # interactive add and remove menu (alias: d)
cli deps add <dep> ...        # add stub blocks to paths.py
cli deps remove <dep> ...     # remove blocks (alias: rm)
cli deps list                 # list declared dependencies and their status (alias: ls)

cli sync                      # bring a tool's framework files up to date (alias: s)
cli sync --dry-run            # show what would change; write and install nothing (-n)
cli sync --no-upgrade         # skip upgrading ngargparser first
cli sync --no-backup          # skip the backups in .ngargparser/sync-backups/
cli sync --no-verify          # skip the known-clash check and the startup check

cli upgrade                   # update ngargparser to the latest release tag (alias: u)
cli upgrade --check           # report installed and latest versions; install nothing
cli upgrade --ref <version>   # install a tag, branch, commit, or local checkout (also on cli sync)
cli upgrade --dev             # install the tip of master (also on cli sync)
```

Run from inside a tool folder. `./configure` is a script in the tool folder, separate from `cli`:

```
./configure                   # regenerate .env and setup_<dep>_env.sh from paths.py
./configure -h                # list the flags for your declared dependencies
./configure --<dep>-path=...  # set a dependency's path (also -venv, -module, -lib-path)
```

### NGArgumentParser API

`NGArgumentParser.__init__` creates the `preprocess` and `postprocess` subcommands, each with built-in arguments. `predict` exists only after `add_predict_subparser()`, which the generated parser calls once; a second call fails with `conflicting subparser: predict`. `predict`'s output arguments (`--output-prefix/-o`, `--output-format/-f`) are framework-owned; your subclass adds the input arguments.

#### Built-in `preprocess` arguments

```
--input-json / -j  JSON_FILE   (required)
--output-dir / -o  DIR         (required)
--params-dir       DIR         (default: $OUTPUT_DIR/predict-inputs/params)
--inputs-dir       DIR         (default: $OUTPUT_DIR/predict-inputs/data)
--assume-valid                 (skip validation)
```

`preprocess` reads and checks the whole command line before it creates the folder layout under `-o`, so a command line that fails its checks leaves nothing behind, and the order of `-o`, `--params-dir` and `--inputs-dir` does not matter. Once the checks pass, the folders exist before your `run()` starts, and they stay if `run()` fails. A `-o` that is an existing file, or a folder that cannot be created, stops with an error. A `--params-dir` or `--inputs-dir` must be an existing folder or one of the folders `preprocess` creates under `-o`. Of the three commands, only `preprocess` has `--assume-valid`.

#### Built-in `predict` arguments

```
--output-prefix / -o  STR
--output-format / -f  {tsv,json}   (default: tsv)
```

Your predict branch passes these to `core.result_writer.write_results` (see [Result output](#result-output)): TSV to stdout when no `-o` is given, otherwise `<prefix>.<ext>`. The generated parser adds `--input-tsv/-t` and `--input-json/-j`, both checked with `validate_file`.

#### Built-in `postprocess` arguments

```
--job-desc-file / -j     JSON   ─┐ pick exactly one
--input-results-dir / -i DIR    ─┘
--postprocessed-results-dir / -p DIR
--output-prefix / -o  STR
--output-format / -f  {tsv,json}    (default: json)
```

`-j` must be an existing file, and `-i` and `-p` existing folders. The folder part of `-o` must exist too.

#### `SubparserWrapper`

Each subcommand's parser (`self.parser_preprocess`, `self.parser_predict`, `self.parser_postprocess`) is a `SubparserWrapper`. It exposes `.help` (the line in the top-level `-h`) and `.description` as plain attributes:

<!-- example: self -->
```python
self.parser_preprocess.help = 'Custom preprocess help'
self.parser_preprocess.description = 'Detailed preprocess instructions'
```

#### Argument grouping

`group="<label>"` on `self.parser_<subcommand>.add_argument` puts the argument in a help section of that name, created on first use. Only these three subparsers accept it; see [Add an argument](#add-an-argument).

#### Updating inherited arguments

`update_arguments` changes an option's properties (help, default, choices, required, metavar, dest) and its help section (`group=`). It cannot rename one, so pass all its names, or any one of them. The example in [Change an inherited argument](#change-an-inherited-argument) makes `predict` default to json.

Do not re-declare an inherited argument with a new `add_argument("--output-format", ...)`. argparse raises `argument --output-format: conflicting option string: --output-format` (`conflicting option strings: --output-format, -f` when you repeat both names). To replace an argument, remove it first, then add your own:

<!-- example: self -->
```python
self.remove_argument("--output-format", "predict")
self.parser_predict.add_argument("--output-format", "-f", choices=["tsv", "json", "csv"], default="tsv")
```

`remove_argument("--output-format", "predict")` removes the option under all its names, so `-f` is free again too. Both methods raise an error instead of silently doing nothing:

| Mistake | `update_arguments` | `remove_argument` |
|---|---|---|
| Misspelled option | `ValueError: No option --output-formt in predict; did you mean --output-format?` | The same |
| Misspelled subcommand | Not applicable: you call it on `self.parser_<subcommand>` | `ValueError: remove_argument: unknown subparser 'predcit'; use predict, preprocess or postprocess.` |
| Subcommand not added yet | Not applicable | `ValueError: remove_argument: the predict subparser hasn't been added yet.` |
| New option names (a rename) | `ValueError: update_arguments can't rename --output-format/-f to --output-format/-F: ...` | Not applicable |
| Unknown keyword | `TypeError: update_arguments() got keyword(s) --output-format doesn't have: colour` | Not applicable |

#### Result output

Every tool emits the same *result envelope* and serializes it with the framework-owned helper `core.result_writer.write_results` (in `src/core/result_writer.py`). Nothing calls it automatically: your predict branch calls it, and your `postprocess.py` calls it the same way (its template shows how).

```python
from core.result_writer import write_results

result = {
    "warnings": [], "errors": [],
    "results": [
        {"type": "peptide_table",
         "table_columns": ["peptide", "score"],
         "table_data": [["ADMGHLKY", 0.5]]},
        # ... more tables ...
    ],
}
write_results(result, args.output_prefix, args.output_format)
```

| Form | Without `-o` | With `-o <prefix>` |
|---|---|---|
| TSV, one table | The table on stdout | `<prefix>.tsv` |
| TSV, several tables | Each table on stdout after a `--- <type> ---` banner | One `<prefix>.<type>.tsv` per table |
| JSON | The whole envelope on stdout | `<prefix>.json` with the whole envelope |

TSV is flat and pipeable; JSON keeps the whole envelope, including warnings, errors and any extra per-table keys. For TSV, warnings and errors go to stderr, so stdout stays a clean data stream. When it writes files, `write_results` creates missing parent folders and prints `Wrote <path>` on stderr.

`predict` defaults to `tsv`. `postprocess` defaults to `json`, because the combined envelope carries metadata that TSV cannot represent.

#### Built-in validators

<!-- example: imports -->
```python
from core.core_validators import (
    validate_file,                     # path is an existing file (its folder too); returns a Path
    validate_directory,                # path is an existing directory; returns a Path
    validate_directory_given_filename, # parent dir of a path
    validate_preprocess_dir,           # special preprocessing dir setup
)
```

#### Custom validators

A custom validator takes the command-line text, returns the converted value, and raises `argparse.ArgumentTypeError` for a bad value. Define it in `src/validators.py` and pass it as `type=`; [Check values with a validator](#check-values-with-a-validator) has a full example.

### ./configure internals

`./configure` runs from the tool folder. It reads `paths.py` and regenerates two things: the tool's `.env`, and one `setup_<dep>_env.sh` per declared dependency. Both hold this machine's paths, so the tool's `.gitignore` keeps them out of git and each machine runs `./configure` itself.

The `./configure` launcher uses `.venv/bin/python` when the tool has one, so the venv does not need to be active. Without python-dotenv available, it says to run `uv sync` first. `APP_NAME` is read from the tool's own `.env` file, so a value passed down by a calling tool is ignored. Without a `.env`, it is the folder name, or `<name>` for a folder named `ng_<name>-local` (`ng_my-app-local` gives `my-app`).

`./configure -h` lists one flag per setting of every declared dependency, with the setting's current value:

```
usage: ./configure [-h] [--mhci-predictor-path MHCI_PREDICTOR_PATH]
                   [--mhci-predictor-venv MHCI_PREDICTOR_VENV]
                   [--mhci-predictor-module MHCI_PREDICTOR_MODULE]
                   [--mhci-predictor-lib-path MHCI_PREDICTOR_LIB_PATH]
  ...
  --mhci-predictor-path MHCI_PREDICTOR_PATH
                        Set mhci_predictor_path (currently: None).
```

A flag exists only for a dependency `cli deps add` has declared, so a misspelled or undeclared name is rejected instead of creating something. Flags write their values into `paths.py`:

```bash
./configure --mhci-predictor-path=/opt/tools/mhci          # writes mhci_predictor_path into paths.py
./configure --mhci-predictor-venv=/home/me/.pyenv/versions/3.11.3/envs/myenv
./configure --mhci-predictor-module=python/3.11            # optional: environment module to load
./configure --mhci-predictor-lib-path=/opt/lib             # optional: prepended to LD_LIBRARY_PATH
```

#### Dependency rule

`cli deps` and `./configure` use one rule: a dependency is a top-level `<dep>_path` variable whose `<dep>` does not end in `_lib`, `_venv` or `_module` (those are its settings). The rule covers plain `<dep>_path = value` lines at the top of the file, the form `cli deps add` writes. `./configure` runs `paths.py` as Python, so it also sees other forms (a type annotation, a line inside an `if`, a chained `a_path = b_path = ...`) that `cli deps` does not list or remove; keep to the plain form.

The `''' [ Title ] '''` line above a block is only a label, so a hand-edited `paths.py` whose title does not match its variables (`T Cell Class I` above `tcell_class_i_path`), or one with no titles at all, still lists and removes correctly. `cli deps add <name>` and the interactive menu refuse a name ending in `_lib`, `_venv`, `_module` or `_path`, because `./configure` could not tell it from a setting. `remove` deletes the block's exact lines, refuses a block mixed with another tool's lines, and reports only what it really removed. Every form of `cli deps`, the interactive menu included, exits 1 when a name was refused or not found, or when `paths.py` does not parse, and never overwrites such a file. Cancelling the menu exits 0; Ctrl-C or Ctrl-D at a prompt exits 1.

`./configure` copies only plain values (text, numbers, paths) from `paths.py` into `.env`. Settings left at `None` are skipped, and an import such as `from pathlib import Path` does not appear there.

#### Virtualenv resolution

`./configure` finds each dependency's virtualenv from three sources, in order:

| Order | Source | Notes |
|---|---|---|
| 1 | `<dep>_venv` in `paths.py` | An explicit setting always wins; nothing else is consulted |
| 2 | `APP_VENV` in `<dep>_path/.env` | What the dependency publishes about itself |
| 3 | A conventional virtualenv folder inside `<dep>_path` | `.venv`, `venv`, `env` or `.virtualenv`; used only when exactly one of them exists |

When none of them gives a virtualenv, `./configure` warns, because the dependency then runs under whatever Python is active. A dependency that is an ngargparser tool needs no venv setting: once it has run its own `./configure`, its `.env` publishes its `.venv` (step 2):

```
* mhci_predictor: using virtualenv declared in its .env: /opt/tools/mhci/.venv
```

Any other dependency with exactly one conventional virtualenv folder is found by step 3:

```
* mhci_predictor: using virtualenv found at /opt/tools/mhci/.venv
```

Every tool publishes its own virtualenv. When exactly one conventional virtualenv sits in the tool folder, `./configure` writes `APP_VENV` into that tool's `.env`, next to `APP_ROOT` and `APP_NAME`. Step 2 reads that value. For a tool whose virtualenv lives elsewhere, set `APP_VENV = "/path/to/venv"` in that tool's own `paths.py`: its `./configure` then publishes it for every tool that depends on it. An `APP_VENV` added to `.env` by hand is erased by the next `./configure`.

A dependency's `paths.py` is never read. It is a Python module, loaded by running it, so reading another tool's copy would run its code inside your `./configure`. `.env` is plain key and value text, parsed and never run.

Only those four folder names are tried, and only when the candidate holds both `pyvenv.cfg` and `bin/activate`. Anything else (`pyenv virtualenv 3.11.3 myenv`, a conda env, a `myenv/` inside the dependency's folder) must be set with `--<dep>-venv=`. When two candidates match, neither is adopted: the run names both and asks you to pick one, instead of binding the dependency to whichever the filesystem listed first.

Steps 2 and 3 are never written to `paths.py`. They are worked out again on every run, so the same `paths.py` works on your laptop and on each deploy host, each resolving locally. Writing one down would freeze one host's absolute path, which goes stale as soon as `<dep>_path` moves.

#### Warnings and rewrites

A configured folder that does not exist warns but never blocks. `.env` and the shell scripts are still written, so a path that is valid on the deploy target but absent on the machine running `./configure` works:

```
⚠  pepx_path = '/opt/tools/pepx' does not exist. 'pepx' may fail until this is corrected.
```

A dependency left with no virtualenv warns for the same reason, since the alternative is finding out at prediction time:

```
⚠  pepx has no virtualenv. It will run under whatever Python is active. Set one with './configure --pepx-venv=<path>' if it needs its own.
```

`.env` is rewritten only when its content changes; an otherwise identical run reports `* .env file unchanged`.

#### Runtime `.env`

At run time, `src/core/app_env.py` loads the `.env` next to the tool's `src/` folder, without searching parent folders. `APP_ROOT` and `APP_NAME` always come from that file ([Call another tool](#call-another-tool)).

The generated `src/run_<name>.py` and `src/core/configure.py` also call python-dotenv's `load_dotenv()` with no path. That call searches upward from the script's folder and loads the first `.env` it finds, which can sit in a parent folder (for example, the tool folder's `.env` for a build copy under `build/`). Values already set in the environment still win.

### get_libs internals

After a fetch:

- Fetched folders get `__init__.py` files, so a run from the tool folder imports them the same way the build does.
- `libs/.gitignore` lists the fetched entries, so they stay out of git; the file ignores itself. If `libs/.gitignore` is your own file, the build leaves it alone. Inside a git work tree, it then warns about any fetched entry git would still pick up. `cli generate` does not run `git init`, so a new tool gets no warning until you make it a repository.
- The build refuses to overwrite a `libs/<name>` that get_libs.sh did not fetch, so code you commit in `libs/` is safe.
- `.ngargparser/libs-stamp` records the fingerprint of get_libs.sh and which `libs/` folders the build owns. If it is lost (a fresh clone with `libs/` copied in, or `.ngargparser/` deleted), the build reads the same list from `libs/.gitignore`, whose first line marks it as the build's. If both are gone, delete the fetched folders in `libs/` and build again.
- In progress mode (`make build`), the script's output goes to `.ngargparser/get_libs.log`; on failure the build prints its last 20 lines.

`.distignore` and the hidden-file rule apply only to your own files, not to fetched entries. In the build copy, the build removes `.git` and `__pycache__` folders at every depth of each `libs/` entry, and `.github` and `.gitlab` at the top of each entry. It also deletes `._*` files everywhere in the build copy. Delete anything else you do not want shipped inside get_libs.sh.

get_libs.sh sees these exported variables: `PROJECT_ROOT`, `APP_NAME`, `TOOL_NAME`, `TOOL_VERSION`, `SRC_DIR` and `LIBS_DIR` (read-only for the script; the build moves the results into it). Git prompts are off and stdin is empty, so a credential prompt fails the fetch instead of hanging the build.

`make clean` removes `build/` only. Fetched dependencies stay in `libs/`, so the next build does not download them again.

### Build pipeline

`scripts/core/build.sh` runs these steps:

1. Stop when `git` is missing or a removed layout is found ([Removed layouts](#removed-layouts)), before anything is removed.
2. Set up `build/<TOOL_NAME>-<version>/` and remove the previous tarball of the same name.
3. Fetch dependencies with `scripts/get_libs.sh` into `libs/`, unless nothing changed ([Code in libs/](#code-in-libs-scriptsget_libssh)).
4. Evaluate `.distignore` (exact `.gitignore` semantics).
5. Copy the tool into the build copy, skipping excluded paths. Symlinks are copied as links, never followed. Then strip `.git`, `__pycache__`, `.github` and `.gitlab` from `libs/` entries and add `__init__.py` files.
6. Check symlinks: each must be relative and resolve inside the build, or the build fails and lists them.
7. Write the version: replace every `TOOL_VERSION` in the build copy's `README` with the version.
8. Run `scripts/hooks.sh` against the copy, if the tool has one.
9. Write `VERSION` (the version, then the build date) at the top of the build copy, and delete `._*` files.
10. Check symlinks again, so links a hook created are checked too.
11. Check that the tool starts (the *startup check*): run the entry script's `--help`, `predict --help`, `preprocess --help` and `postprocess --help` from the build copy with the tool's `.venv`, and `APP_ROOT` set to the build copy. A crash fails the build and shows the error line. With no `.venv`, or with no `src/run_*.py` or several, the check is skipped with a notice.
12. Pack the tarball (symlinks stored as links), then make the build copy's files read-only.

A build that fails after step 1 removes its build copy, so no half-built copy or old tarball looks fresh. A step 1 stop comes before anything is removed, so the previous build copy and tarball stay.

### Sync safety checks

`cli sync` runs three checks:

| Check | What it does |
|---|---|
| Known clash | Before writing anything, sync reads your tool's `src/*ArgumentParser.py` for code a framework release is known to break, and stops with the file, the line numbers and the fix. `cli sync --dry-run` reports the same findings. The list holds one entry today: declaring `--output-prefix` or `--output-format` on `predict`, or reusing `-o` or `-f` there, which the base class has owned since 0.3.0. A break that is not on the list is left to the startup check |
| Startup check | When a framework file is about to change or a legacy file is about to move, sync runs your entry script with `--help`, `predict --help`, `preprocess --help` and `postprocess --help`, before and after, using the tool's own `.venv` interpreter. This runs your tool's code. It runs only when `src/` holds exactly one `run_*.py`; with several, sync cannot tell which is the entry script and runs none of them. `cli sync --help` calls this check the test drive |
| Undo | If a screen that worked before fails after, or sync is interrupted or hits an error, sync puts back every file it touched, including moved, created and edited ones and files reached through a symlink, and prints the error your tool raised |

`cli sync --no-verify` skips the known-clash check and the startup check, and says so. Undo on error still applies. Setting `NGARGPARSER_SYNC_NO_VERIFY=1` has the same effect.

The startup check proves the tool still starts. It cannot prove that a full `predict`, `preprocess` or `postprocess` run still gives the right result, so run your own tests after a sync.

### Project tree

`cli generate my-app` creates this tree. Items marked *later* appear after the step named:

```
my-app/
├── .distignore              tarball exclusions (.gitignore syntax)
├── .env                     this machine's settings (APP_ROOT, APP_NAME, APP_ENTRY, ...); git-ignored
├── .gitignore               base rules plus the "ngargparser (managed)" section
├── .ngargparser/            later: make build or cli sync; build and sync state, git-ignored
├── .venv/                   later: uv sync
├── Makefile                 make build, make build-verbose, make clean
├── README                   end-user README; ships in the tarball
├── README.md                developer note with the ngargparser badge
├── configure                launcher for src/core/configure.py
├── license-LJI.txt          cli generate <name> only
├── pyproject.toml           project metadata, dependencies, [tool.ngargparser] scaffold_version
├── uv.lock                  locked package versions (commit it)
├── paths.py                 later: cli deps add
├── setup_<dep>_env.sh       later: ./configure, one per dependency; git-ignored
├── libs/                    later: a make build whose get_libs.sh fetches something, or your own code
├── build/                   later: make build
├── deploy/
│   └── install.sh           run on the target host at deploy time
├── scripts/
│   ├── get_libs.sh          cli generate <name> only; fetches outside code into libs/
│   ├── hooks.sh             only if you add it
│   └── core/
│       ├── build.sh         the build pipeline
│       └── build.conf       build settings
└── src/
    ├── run_my_app.py        entry script (run_<name>.py, hyphens become underscores)
    ├── MyAppArgumentParser.py
    ├── preprocess.py
    ├── postprocess.py
    ├── validators.py
    └── core/
        ├── __init__.py
        ├── NGArgumentParser.py
        ├── app_env.py
        ├── configure.py
        ├── core_validators.py
        ├── result_writer.py
        └── set_pythonpath.py
```

`cli generate <name>` writes two READMEs. `README` is the end-user README that ships in the tarball, laid out like other IEDB tools: a first line `<Name> (<name>) - version TOOL_VERSION`, then Quick Install, Introduction, Release Notes, Prerequisites, Installation and Usage. The build replaces `TOOL_VERSION` with the version. `README.md` is a short developer note that carries the ngargparser badge, which `cli sync` updates. `cli generate example` puts the same walkthrough in both.

`cli generate example` creates `aa-counter/` with the same layout, except: no `license-LJI.txt`, no `scripts/get_libs.sh`, sample inputs in `src/example.json` and `src/example.tsv`, and `src/run_aa_counter.py` with `src/AACounterArgumentParser.py`.

### File ownership

*Framework-owned* files belong to ngargparser: `cli sync` overwrites them, so do not edit them. Everything else is yours, with the exceptions in the right column.

| Path | Owner |
|---|---|
| `src/core/*.py` except `__init__.py` | Framework: `cli sync` overwrites each one whose content differs |
| `src/core/__init__.py` | Framework: `cli generate` creates it empty; `cli sync` leaves it alone |
| `scripts/core/build.sh` | Framework: `cli sync` overwrites |
| `scripts/core/build.conf` | You: the one file in `scripts/core/` that `cli sync` never overwrites (it moves a legacy `scripts/build.conf` here) |
| `Makefile` (top level) | Framework: `cli sync` overwrites |
| `configure` (top level) | Framework: `cli sync` overwrites |
| `pyproject.toml` `[tool.ngargparser] scaffold_version` | Framework: `cli sync` stamps it |
| `pyproject.toml` (everything else) | You |
| `uv.lock` | You (regenerated by `uv` commands) |
| `README` | You: the end-user README; the build writes the version into the build copy's `README` only |
| `README.md` | You, except the ngargparser badge, which `cli sync` updates |
| `license-LJI.txt` | You |
| `paths.py` | You (`cli deps` writes and removes blocks; `./configure --<dep>-...` or a hand edit fills in values) |
| `.env`, `setup_<dep>_env.sh` | Written for this machine: `.env` by `cli generate` and `./configure`, the setup scripts by `./configure`; both git-ignored. An `APP_ENTRY` you set in `.env` is kept while it names a file |
| `.gitignore` | You, except the section under `# --- ngargparser (managed) ---`. `cli sync` only adds the rules missing from that section and never removes or rewrites a line |
| `.distignore` | You (`.gitignore` syntax) |
| `scripts/get_libs.sh` | You (`cli generate <name>` creates it; `cli sync` never creates or overwrites it) |
| `scripts/hooks.sh` | You (optional; `cli generate` does not create it; `cli sync` renames legacy hook names to it) |
| `deploy/install.sh` | You: `cli sync` creates it from the template when missing, never overwrites it |
| `libs/` | You, except entries fetched by `scripts/get_libs.sh` |
| `libs/.gitignore` | Framework while its first line is the build's marker; yours otherwise (the build then leaves it alone) |
| `.ngargparser/` | Framework: build and sync state (`libs-stamp`, `get_libs.log`, `sync-backups/`); git-ignored |
| `build/` | Framework: output of `make build`, replaced by the next build |
| `src/run_*.py`, `src/<Name>ArgumentParser.py`, `src/preprocess.py`, `src/postprocess.py`, `src/validators.py` | You |

### Glossary

| Term | Meaning |
|---|---|
| aa-counter | The sample app: a complete tool that `cli generate example` creates |
| `APP_ENTRY` | Key in a tool's `.env`: the tool's entry script, relative to the tool folder |
| `APP_NAME` | Key in a tool's `.env`: the tool's name, from `cli generate` or the folder name |
| `APP_ROOT` | Key in a tool's `.env`: the absolute path of the tool folder; the tool imports `APP_ROOT/libs/` |
| `APP_VENV` | Key in a tool's `.env`: the tool's virtualenv, published for tools that depend on it |
| Build copy | `build/<TOOL_NAME>-<version>/`, the copy of the tool that `make build` packs |
| CI | Continuous integration: a tool's own GitLab CI pipeline, which typically runs `make build` and keeps the tarball as an artifact |
| Dependency | Another program your tool runs, often another IEDB tool, declared in `paths.py` with `cli deps add` |
| `<DEP>_ENTRY`, `<DEP>_PATH` | Variables `./configure` exports for a dependency: its entry script and its folder |
| Editable install | An install that runs the code in a local checkout, so edits take effect without reinstalling |
| Entry script | `src/run_<name>.py`, the script you run; it builds the parser and dispatches each subcommand |
| `.env` | Plain key and value file in the tool folder with this machine's settings; written by `cli generate` and `./configure`, git-ignored |
| Environment module | A software package loaded with `module load` on an HPC host; set per dependency with `--<dep>-module` |
| Framework-owned | A file `cli sync` overwrites; see [File ownership](#file-ownership) |
| HPC | High-performance computing: the clusters IEDB tools run on |
| `install_shell_init` | The deploy target's shell snippet that the orchestrator sources before `deploy/install.sh`; it sets `UV_*` variables and modules |
| Job description | One entry in `job_descriptions.json`: a command, its id, its type, the jobs it depends on and its outputs |
| Job unit | One independent piece of the input, with its own parameter file; one `predict` run handles one job unit |
| Known clash | Tool code that a framework release is known to break; `cli sync` stops when it finds one |
| `.ngargparser/` | Folder for build and sync state: `libs-stamp`, `get_libs.log`, `sync-backups/` |
| Orchestrator | nxg-tools-deployments, which fetches a tarball, extracts it on the target host and runs `deploy/install.sh` |
| Release tag | A git tag of the form `vX.Y.Z` (the `v` optional, a `+build` suffix allowed); pre-release tags such as `v0.4.2-test` do not count |
| Result envelope | The dict `write_results` takes: `warnings`, `errors` and a list of tables under `results` |
| `scaffold_version` | `[tool.ngargparser] scaffold_version` in `pyproject.toml`: the ngargparser version the tool was last generated or synced with |
| `setup_<dep>_env.sh` | Shell script `./configure` writes per dependency. It runs `module purge` and loads the dependency's module (when one is set), activates its virtualenv (when one is set or found), adds its library path to `LD_LIBRARY_PATH` (when set), and exports `<DEP>_PATH`, plus `<DEP>_ENTRY` when the dependency publishes one |
| Startup check | Running the entry script's four help screens; `make build` runs it on the build copy, `cli sync` before and after a sync |
| Stub block | The four `None` settings `cli deps add` writes for a dependency |
| Subcommand | `preprocess`, `predict` or `postprocess` |
| Subparser | The parser of one subcommand, kept as `self.parser_<subcommand>` |
| `SubparserWrapper` | The class wrapping each subparser; adds `.help` and `update_arguments`. `NGArgumentParser` gives its `add_argument` the `group=` keyword |
| Tool, tool folder | A project you create with `cli generate`, and its top folder |
| `TOOL_VERSION` | The version `make build` uses: the environment variable, default `local` |
| `TOOLS_DEPLOY_TOKEN` | GitLab CI/CD variable holding a token that get_libs.sh uses to clone in CI |
