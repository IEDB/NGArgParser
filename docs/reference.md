# ngargparser reference

This page lists the API and the settings you use to write a tool: the parser API, the result checks, `./configure` and `.env`, the build and deploy settings, and the `cli` commands. Read the [README](../README.md) first, which walks you through a tool, and come here for the exact rules, defaults and messages.

The [parser cookbook](parser-cookbook.md) has recipes for the parser and the predict code. The [internals](internals.md) page describes how the build, `./configure` and `cli sync` work inside: the build pipeline, the locks, the `libs/` stamp, signals and the version stamp.

## Contents

1. [CLI cheatsheet](#cli-cheatsheet)
2. [NGArgumentParser API](#ngargumentparser-api)
3. [./configure flags and .env](#configure-flags-and-env)
4. [Runtime .env](#runtime-env)
5. [Build](#build)
6. [Deploy](#deploy)
7. [cli sync](#cli-sync)
8. [Tool folder tree](#tool-folder-tree)
9. [File ownership](#file-ownership)
10. [Glossary](#glossary)
11. [Migrate older tools](#migrate-older-tools)
12. [Troubleshooting](#troubleshooting)
13. [Contribute to ngargparser](#contribute-to-ngargparser)

## CLI cheatsheet

```
cli --version                 # installed version; checks GitLab for a newer release on every run (up to 4 s)
cli --help                    # usage; never contacts GitLab

cli generate <name>           # create a new tool (alias: g)
cli generate example          # create the sample app, aa-counter/

cli deps                      # interactive add and remove menu (alias: d)
cli deps add <dep> ...        # add stub blocks to paths.py (a comma separates names too)
cli deps remove <dep> ...     # remove blocks (alias: rm)
cli deps list                 # list declared dependencies and their status (alias: ls)

cli sync                      # bring a tool's framework files up to date (alias: s)
cli sync --dry-run            # show what would change; write and install nothing (-n)
cli sync --no-upgrade         # skip upgrading ngargparser first
cli sync --no-backup          # skip the backups in .ngargparser/sync-backups/
cli sync --no-verify          # skip the known-clash check and the startup check
cli sync --allow-downgrade    # sync a tool last synced with a newer ngargparser

cli upgrade                   # update ngargparser to the latest release tag (alias: u)
cli upgrade --check           # report installed and latest versions; install nothing
cli upgrade --ref <version>   # install a tag, branch, commit, or local checkout (also on cli sync)
cli upgrade --dev             # install the tip of master (also on cli sync)
```

`cli generate` ends with `Tool '<name>' is ready. Next steps:` and the commands to run next.

A *tool folder* is one with a `[tool.ngargparser]` table in `pyproject.toml` or a `src/core/NGArgumentParser.py`; a `scripts/core/build.sh` alone does not make one. `cli generate` refuses to run in a tool folder or in a folder under one. It looks up to your home folder (or the filesystem root), then exits 1 and creates nothing when it finds one.

`cli deps` and `cli sync` run only in a tool's top folder, and exit 1 anywhere else. In a folder under a tool, such as its `src/`, they name the tool:

```
✗ This folder is inside the ngargparser tool /home/me/my-app: run cli sync there: cd /home/me/my-app
```

Both also refuse a build or deployed copy (a folder with `.ngargparser-build`), and every folder under one, except `cli deps list`, which writes nothing. Under a build copy, the message names the source folder:

```
✗ This folder is inside /home/me/my-app/build/ng_my-app-local, a build or deployed copy of a tool (it has .ngargparser-build), not its source. Nothing was changed. Run cli sync in the tool's source folder: cd /home/me/my-app, then build again.
```

`cli upgrade` works from any folder and changes no tool files. Only *release tags* (`vX.Y.Z`) count as the latest: install a pre-release with `--ref v0.4.2-test`, or the tip of master with `--dev`.

`./configure` is a script in the tool folder, separate from `cli`:

```
./configure                   # regenerate .env and setup_<dep>_env.sh from paths.py
./configure -h                # list the flags for your declared dependencies
./configure --<dep>-path=...  # set a dependency's path (also -venv, -module, -lib-path, -entry)
```

Environment variables:

| Variable | Effect |
|---|---|
| `NGARGPARSER_NO_UPDATE_CHECK=1` | Turns the update notice and the `cli --version` release check off |
| `NGARGPARSER_SYNC_NO_VERIFY=1` | The same as `cli sync --no-verify` |
| `NGARGPARSER_SYNC_NO_BACKUP=1` | The same as `cli sync --no-backup` |
| `NGARGPARSER_SYNC_ALLOW_DOWNGRADE=1` | The same as `cli sync --allow-downgrade` |
| `NGARGPARSER_SKIP_STARTUP_CHECK=1` | `make build` skips the [startup check](internals.md#startup-check) |
| `NGARGPARSER_STARTUP_CHECK_TIMEOUT` | The seconds each help screen of `make build`'s startup check may take, default `60`; a value that is not a number above 0 stops the build |
| `NGARGPARSER_SYNC_HELP_TIMEOUT` | The seconds each help screen of `cli sync`'s [test drive](internals.md#test-drive) may take: above 0 and at most `86400`, default `60` |
| `NGARGPARSER_BUILD_TRACE=1` | `make build` and `make build-verbose` also print every shell command the build runs (`set -x`) |
| `TOOL_VERSION` | The version `make build` uses, default `local`; it wins over a `TOOL_VERSION` in [`build.conf`](#build-settings) |

Each switch is off when it is unset, empty, `0`, `false`, `no` or `off` (any case).

### Update notice

When you run `cli`, it sometimes prints a short notice that a newer version is available. It checks GitLab at most once a day, shows the notice only in an interactive terminal, and never in CI. Silence it with `NGARGPARSER_NO_UPDATE_CHECK=1`; set the variable to `0` or `false`, or unset it, to turn the notice back on.

`cli --version` checks on every run, with no daily limit and no cache. It prints `cli <version>`, then looks up the latest release tag, waiting up to 4 seconds for GitLab:

| Result | Extra output |
|---|---|
| Installed version is current | None: `cli --version` prints only `cli 0.4.1` |
| Installed version is behind | The update notice, with a link to the release notes |
| Installed version is ahead of the latest tag | `ℹ Installed ngargparser 0.4.2rc1 is ahead of the latest release tag v0.4.1.` |
| GitLab does not answer in 4 s | `⚠ Couldn't check for a newer ngargparser (GitLab did not answer within 4 s).` |
| GitLab cannot be reached | `⚠ Couldn't check for a newer ngargparser (GitLab unreachable: network/VPN, credentials, or git missing).` |

The lookup is skipped in CI and when `NGARGPARSER_NO_UPDATE_CHECK=1` is set. A help screen (`cli --help`, `cli sync --help`) never contacts GitLab: in a terminal, the notice after it reads only the cached result, however old. Bare `cli` and the other commands keep the once-a-day notice. `cli upgrade`, `cli sync`, the update notice and `cli --version` all skip pre-release tags such as `v0.4.2-test`. [Version order](internals.md#version-order) says how versions compare.

### Newer than the latest release

`cli upgrade` and `cli sync` never replace an installed ngargparser that is newer than the latest release tag, such as a pre-release or a build from a checkout. They keep it and say so:

```
ℹ Installed: 0.4.2rc1    Latest: v0.4.1  (installed is newer)
ℹ Installed ngargparser 0.4.2rc1 is newer than the latest release v0.4.1; not changing it.
```

The first line is `cli upgrade`'s, which `--check` prints alone. `cli sync` prints the second line, then syncs from the installed version, and `cli sync --dry-run` says that a real run would do so. `--ref <version>` and `--dev` still install what they name.

### Upgrade reminders

`cli upgrade` detects how ngargparser was installed (`uv tool install`, a pip venv, or a uv venv without pip) and runs the matching installer. After an install, it reminds you that tools need `cli sync`:

| Where you run it | What it prints |
|---|---|
| Inside a tool that is behind, after installing | `You upgraded the CLI to Y, but this tool is still on X.` then ``Run `cli sync` here to update this tool to Y.`` |
| Inside a tool that is behind, when ngargparser was already current | `The CLI is on Y, but this tool is still on X.` and the same second line |
| Anywhere else, after installing | `` `cli upgrade` updates the CLI, not your tools. `` then ``Run `cli sync` inside a tool to update it to Y.`` |

Inside a tool that is current, and outside a tool when nothing was installed, it prints no reminder.

## NGArgumentParser API

`NGArgumentParser.__init__` creates the `preprocess` and `postprocess` subcommands, each with built-in arguments. `predict` exists only after `add_predict_subparser()`, which the generated parser calls once; a second call is a [parser setup error](#parser-setup-errors). `predict`'s output arguments (`--output-prefix/-o`, `--output-format/-f`) are framework-owned; your subclass adds the input arguments.

The [parser cookbook](parser-cookbook.md) shows the common changes as recipes, starting with the generated parser line by line. This section lists the arguments, the rules and every message.

Until `add_predict_subparser()` runs, `self.parser_predict` and `self.predict_input_group` are placeholders. Using either stops the tool with exit code 2 and one line that names your file and line ([Parser setup errors](#parser-setup-errors)):

```
src/MyAppArgumentParser.py:29: self.predict_input_group is not ready yet: call self.add_predict_subparser(...) first
```

A parser whose `__init__` skips `super().__init__()` stops the same way at its first framework call:

```
src/MyAppArgumentParser.py:29: MyAppArgumentParser has not run NGArgumentParser.__init__: call super().__init__() at the start of MyAppArgumentParser.__init__
```

Each table below gives the flag, its Python name (`dest`), and the value the parsed arguments hold. The predict branch reads them as `args.<name>`. The entry script calls `preprocess.run(**vars(args))` and `postprocess.run(**vars(args))`, so `run()` gets the same names as keywords, plus `subcommand`.

### Built-in `preprocess` arguments

| Flag | Python name | Value |
|---|---|---|
| `--input-json/-j` | `input_json` | Required. An existing file, as a `Path` |
| `--output-dir/-o` | `output_dir` | Required. The job's output folder, as a `Path`: `job_descriptions.json` and the [layout](#preprocess-layout) go here |
| `--params-dir` | `preprocess_parameters_dir` | A folder, as text. Default `<output dir>/predict-inputs/params` |
| `--inputs-dir` | `preprocess_inputs_dir` | A folder, as text. Default `<output dir>/predict-inputs/data` |
| `--assume-valid` | `assume_valid_flag` | `True` when given (skip validation), else `False` |

When `paths.py` declares dependencies, the two defaults are `<output dir>/<APP_NAME>/predict-inputs/params` and `<output dir>/<APP_NAME>/predict-inputs/data`.

`preprocess` reads and checks the whole command line before it creates the folder layout under `-o`, so a command line that fails its checks leaves nothing behind, and the order of `-o`, `--params-dir` and `--inputs-dir` does not matter. Once the checks pass, the folders exist before your `run()` starts, and they stay if `run()` fails. A `-o` that is an existing file, or a folder that cannot be created, stops with an error. A `--params-dir` or `--inputs-dir` must be an existing folder or one of the folders `preprocess` creates under `-o`. Of the three commands, only `preprocess` has `--assume-valid`.

### Preprocess layout

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

When `paths.py` declares dependencies with a path set, `preprocess` creates the tree once per dependency (without `results/`) and once for the tool, in `<output dir>/<dependency>/` and `<output dir>/<APP_NAME>/`.

Unlike `cli deps` and `./configure`, `preprocess` finds dependencies by the `''' [ Title ] '''` line above each block. The folder name is that title in lowercase with spaces as `_`, except that `T Cell Class I` and `T Cell Class II` become `mhci` and `mhcii`.

A block counts when any of its `*_path` settings is filled in, so a `<dep>_lib_path` alone counts. A block without its own title counts toward the titled block above it. One before the first title is ignored, and a `paths.py` with no titled block gets the plain layout.

The sample app writes its predict and postprocess outputs under `<output dir>/<APP_NAME>/` as well; `job_descriptions.json` stays in `<output dir>/`. Its `preprocess` lists the files it writes into each folder in `.aa-counter-preprocess-files` there, and a rerun deletes only those, so a file of yours in `--params-dir` stays and never becomes a job.

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

### Built-in `predict` arguments

| Flag | Python name | Value |
|---|---|---|
| `--output-prefix/-o` | `output_prefix` | Text, or `None` when not given |
| `--output-format/-f` | `output_format` | `tsv` or `json`. Default `tsv` |
| `--input-tsv/-t` (generated parser) | `input_tsv` | An existing file, as a `Path`, or `None` |
| `--input-json/-j` (generated parser) | `input_json` | An existing file, as a `Path`, or `None` |

Your predict branch passes the first two to `core.result_writer.write_results` ([Write the results](parser-cookbook.md#write-the-results)): TSV to stdout when no `-o` is given, otherwise `<prefix>.<ext>`. `-o` takes a prefix, not a folder: a value that ends in `/` or names an existing folder stops with a usage error that suggests `<folder>/result`. A format name, in any case, stops the same way, since `-f` gives the format:

```
run_my_app.py predict: error: argument --output-prefix/-o: 'tsv' is a format name; -o takes an output prefix such as 'result' (give the format with -f tsv, or name the files tsv.* with -o ./tsv)
```

An empty value, `-o ""`, stops with `argument --output-prefix/-o: the value is empty; -o takes an output prefix such as 'result'`.

The generated parser adds `--input-tsv/-t` and `--input-json/-j`, both checked with `validate_file`, and requires exactly one of them.

An unedited `predict` branch prints `predict is not implemented yet: edit src/run_<name>.py` and exits 1. The unedited `run()` of `src/preprocess.py` and `src/postprocess.py` do the same, naming their own file. The help screens work either way.

### Built-in `postprocess` arguments

| Flag | Python name | Value |
|---|---|---|
| `--job-desc-file/-j` | `job_desc_file` | An existing file, as a `Path`, or `None`. Give this or `-i` |
| `--input-results-dir/-i` | `postprocess_input_dir` | An existing folder, as a `Path`, or `None`. Give this or `-j` |
| `--postprocessed-results-dir/-p` | `postprocess_result_dir` | Optional. An existing folder, as a `Path`, or `None` |
| `--output-prefix/-o` | `output_prefix` | The prefix of the merged result (the results of every job, combined), as text, or `None` |
| `--output-format/-f` | `output_format` | `tsv` or `json`. Default `json` |

`postprocess` takes exactly one of `-j` and `-i`. A `-p` folder that does not exist stops the command with `'<dir>' is not a valid directory.` `-o` must be a prefix, not a folder, a format name or empty, as for `predict`, and a missing folder in it is created when the result is written.

`output_prefix` is text in `postprocess` as in `predict`; it used to be a `Path`. A `postprocess.py` that calls a `Path` method on it, such as `output_prefix.with_suffix(...)`, needs `Path(output_prefix)` now.

### `SubparserWrapper`

Each subcommand's parser (`self.parser_preprocess`, `self.parser_predict`, `self.parser_postprocess`) is a `SubparserWrapper`. It exposes `.help` (the line in the top-level `-h`) and `.description` as plain attributes:

<!-- example: self -->
```python
self.parser_preprocess.help = 'Custom preprocess help'
self.parser_preprocess.description = 'Detailed preprocess instructions'
```

It also has `update_arguments` and `remove_argument`, and its `add_argument` takes `group=`. The generated parser's `predict` line in the top-level `-h` reads `Run one prediction.` until you set your own `help=`.

The framework's own help texts are sentences that start with a capital letter. `preprocess -h` opens with `Split a JSON input file into smaller job units, if possible, and create job_descriptions.json, which lists the command of every job in the workflow.`, and `postprocess -h` with `Aggregate the results of the individual prediction jobs into one result.` A built-in option reads like `--output-prefix OUTPUT_PREFIX, -o OUTPUT_PREFIX  Output prefix for the prediction result files.`; give it your own text with `update_arguments("--output-prefix", help=...)`.

### Argument groups

The cookbook's [Group options in the help](parser-cookbook.md#group-options-in-the-help) shows how `group=` works. Every argparse group of a subcommand takes `group=` too: the framework's groups below, a group you make with `self.parser_predict.add_argument_group(...)`, a mutually exclusive group, and a group made from any of these.

| Group | Section title |
|---|---|
| `self.predict_input_group` | input options |
| `self.preprocess_required_group` | required parameters |
| `self.preprocess_optional_group` | optional parameters |
| `self.postprocess_required_input_group` | input source (choose exactly one) |
| `self.postprocess_optional_group` | optional parameters |

`group=` takes a section title, a `str`, or one of the subcommand's own argument groups, such as `self.predict_input_group` or a group you made with `self.parser_predict.add_argument_group(...)`. A group counts as its current title. A label that names an existing section by that title adds the option there. A label that differs from a title of the same subcommand only in case, surrounding spaces or one trailing `s` is refused with a `ParserSetupError`, since it would add a second section next to that one: `predict argument --zeta: group="Output Option" would add a section next to 'output options'; did you mean 'output options'?` A `group=` on a group's own `add_argument` that names the section the option shows in is ignored. A mutually exclusive group shows its options in the group it was made from, so its `group=` names that group's title. Each other value stops the tool with exit code 2 and one line:

| Mistake | Message, after your file and line |
|---|---|
| Another title, on a group's own `add_argument` | `predict argument --alleles/-a: predict_input_group is already a group; drop group=, or use self.parser_predict.add_argument(..., group="my options")`; for a label close to an existing title, such as `"output option"`, the hint names that title, `group="output options"` |
| The same, on a group you made | `predict argument --beta: 'my options' is already a group; drop group=, or use self.parser_predict.add_argument(..., group="other")` |
| The same, on a mutually exclusive group | `predict argument --slow: the mutually exclusive group in 'input options' is already a group; drop group=, or use self.parser_predict.add_argument(..., group="speed")` |
| A group of another subcommand | `predict argument --gamma: group= takes a section of predict (a title, or one of its groups), not a group of preprocess: use group="optional parameters", or call that group's own add_argument` |
| A mutually exclusive group | `predict argument --epsilon: group= takes a section title (a str), not a mutually exclusive group: call that group's own add_argument` |
| `group=` on the top-level `self.add_argument` | `top-level argument --verbose: group= works only on a subcommand's options; use self.parser_predict.add_argument(..., group="misc") (or parser_preprocess, parser_postprocess), or drop group=` |

Each of these but the first three is a `ParserKeywordError`. `update_arguments(..., group=...)` takes the same values, and refuses a group of another subcommand with the same line.

### Update and remove errors

The cookbook's [Change or remove a framework option](parser-cookbook.md#change-or-remove-a-framework-option) shows `update_arguments` and `remove_argument`. `update_arguments` changes an option's help, default, choices, required, metavar and `dest`, and its help section (`group=`). The option keeps its place in its section, unless `group=` names another section: then it moves to the end of that one. It cannot rename one, so pass all its names, or any one of them. `self.parser_predict.remove_argument("--output-format")` is the same as `self.remove_argument("--output-format", "predict")`, and removes the option under all its names, so `-f` and the `output_format` dest are free again.

A built-in option keeps its `dest`, because the framework reads `args.<dest>` (for example `args.output_format`). Your own option may change its `dest`, but not to one a built-in option has. Do not re-declare a built-in option without removing it first: the tool stops with a [clash message](#framework-option-clashes).

The built-in `--output-format/-f` keeps to the formats `write_results` writes: `choices` or a `default` other than `tsv` and `json` (in any case) stops the tool. A replacement you add yourself must keep to them too, or `write_results` raises `unsupported output format: 'csv' (expected 'tsv' or 'json')`.

These methods raise a [parser setup error](#parser-setup-errors) instead of silently doing nothing. Each message starts with your file and line, left out here:

| Mistake | Message |
|---|---|
| Misspelled option, in `update_arguments` or `remove_argument` | `No option --output-formt in predict; did you mean --output-format?` |
| Misspelled subcommand, in `self.remove_argument(<option>, <subcommand>)` | `remove_argument: unknown subparser 'predcit'; use predict, preprocess or postprocess.` |
| Subcommand not added yet, in `self.remove_argument` | `remove_argument: the predict subparser hasn't been added yet.` |
| New option names (a rename), in `update_arguments` | `update_arguments can't rename --output-format/-f to --output-format/-F: remove the option with remove_argument('--output-format', 'predict'), then add_argument() the new one.` |
| Unknown keyword, in `update_arguments` (`ParserKeywordError`) | `update_arguments() got keyword(s) --output-format doesn't have: colour` |
| A group of another subcommand as `group=`, in `update_arguments` (`ParserKeywordError`) | `predict argument --output-format/-f: group= takes a section of predict (a title, or one of its groups), not a group of preprocess: use group="optional parameters", or call that group's own add_argument` |
| No option name, in `update_arguments` | `update_arguments() got no option name; pass one of predict's options first: --help, --input-json, ...` |
| `-f` choices or a default that `write_results` cannot write | `update_arguments can't set choices of --output-format/-f to tsv, json, csv: write_results writes only tsv and json. To offer formats you write yourself, remove the option with remove_argument('--output-format', 'predict'), then add_argument() your own.` |
| New `choices` without `default=`, which leave out the current default | `update_arguments can't set choices of --output-format/-f: default 'tsv' is not among the new choices json: pass default= too` |
| `required=True` on an option of a mutually exclusive group | `--input-json is one of a group of mutually exclusive options (-t / -j): make the group required instead, or remove the other option` |
| A new `dest` for a built-in option | `update_arguments can't change dest of --output-format/-f to fmt: the framework reads args.output_format; dest cannot change. To replace the option, remove it with remove_argument('--output-format', 'predict'), then add_argument() your own.` |
| A built-in `dest` for your option | `update_arguments can't set dest of --min to output_prefix: dest output_prefix belongs to the framework (--output-prefix); pick another dest.` |
| An option `parse_args` reads, in `remove_argument`: `preprocess`'s `--params-dir` and `--inputs-dir`, `postprocess`'s `--job-desc-file` and `--input-results-dir` | `remove_argument can't remove --params-dir from preprocess: the framework reads args.preprocess_parameters_dir; change the option with self.parser_preprocess.update_arguments("--params-dir", ...) instead, or hide it from -h with help=argparse.SUPPRESS` |
| Unknown name in `set_subcommand_order` | `set_subcommand_order: unknown subcommand 'predcit'; did you mean 'predict'?` |
| A name listed twice in `set_subcommand_order` | `set_subcommand_order: subcommand 'predict' is listed twice; name each subcommand once.` |
| `predict` in `set_subcommand_order` before `add_predict_subparser()` | `set_subcommand_order: the predict subparser hasn't been added yet; call self.add_predict_subparser(...) first.` |

`set_subcommand_order` checks every name before it reorders, so a typo changes nothing.

### Parser setup errors

A mistake in your parser code raises `ParserSetupError`, from `core.NGArgumentParser`, while the parser is built. Its message starts with the file and line of your call, as a [framework option clash](#framework-option-clashes) does:

| Class | Also a | Raised for |
|---|---|---|
| `ParserSetupError` | `ValueError` | Every mistake in [Update and remove errors](#update-and-remove-errors), and a value argparse's `add_argument` refuses (below) |
| `ParserNotReadyError` | `ParserSetupError`, `AttributeError` | `self.parser_predict` or `self.predict_input_group` before `add_predict_subparser()`, and a parser that skipped `super().__init__()` |
| `ParserKeywordError` | `ParserSetupError`, `TypeError` | An unknown keyword in `update_arguments`, `add_argument` or `add_predict_subparser`, a `group=` that is neither a title nor a group of that subcommand, and `group=` on the top-level `self.add_argument` ([Argument groups](#argument-groups)) |
| `ParserConflictError` | `ParserSetupError`, `argparse.ArgumentError` | A second `add_predict_subparser()`, and two of your own options that share a flag ([Framework option clashes](#framework-option-clashes)) |

A second `add_predict_subparser()` gives this line:

```
src/MyAppArgumentParser.py:61: add_predict_subparser() was called a second time: predict already exists; call it once, and add options to the self.parser_predict it returns
```

Every `add_argument`, on the top-level parser, a subcommand's parser or a group, turns the `TypeError` or `ValueError` argparse raises into one such line, instead of a traceback. A misspelled keyword gets the closest one:

| Mistake | Message, after your file and line |
|---|---|
| A misspelled keyword (a `ParserKeywordError`) | `predict argument --beta: add_argument() got an unexpected keyword argument 'hepl'; did you mean help=?` |
| A value argparse refuses, such as `type="int"` | `predict argument --beta: 'int' is not callable` |
| `required=True` on an option of a mutually exclusive group | `predict argument --input-csv/-c: an option of a mutually exclusive group can't be required=True: make the group required instead, with add_mutually_exclusive_group(required=True)` |

The top-level parser's lines start `top-level argument --verbose:`.

`add_predict_subparser` passes the other keywords argparse's `add_parser` takes, such as `epilog=`, `usage=` and `prog=`, on to it, and refuses two, each with a `ParserKeywordError`:

| Keyword | Message, after your file and line |
|---|---|
| `aliases=` | `add_predict_subparser() takes no aliases=: the run script runs predict only when args.subcommand is 'predict', and an alias would set args.subcommand to the alias` |
| `conflict_handler=` | `add_predict_subparser() takes no conflict_handler=: it would let an option of yours take a flag of a framework option; to replace one, remove it with self.parser_predict.remove_argument(...) first` |
| A misspelled one | `add_predict_subparser() got an unexpected keyword argument 'epilgo'; did you mean epilog=?` |

The generated entry script builds the parser inside `try`, prints the one line, and exits with code 2, so every command, `make build` and `cli sync`'s startup check stop there:

```python
from core.NGArgumentParser import ParserSetupError

try:
    parser = MyAppArgumentParser()
except ParserSetupError as e:
    print(e, file=sys.stderr)
    sys.exit(2)
```

A tool generated earlier shows a traceback ending in that line, and exits with code 1, until you add the import and the `try` to its entry script, which `cli sync` never changes. `except ValueError` still catches every one of them, and `except AttributeError`, `except TypeError` or `except argparse.ArgumentError` catches the class that is also one.

### Framework option clashes

The built-in options of all three subcommands belong to the framework, and so do the names they are stored under. An option of yours that reuses one of their flags, letters or `dest`s stops the tool with exit code 2 and one line. The line names your file and line, the framework option, and the way to change it:

```
src/MyAppArgumentParser.py:61: predict argument --organism/-o: -o belongs to the framework (--output-prefix); pick another flag, change the framework option with self.parser_predict.update_arguments("--output-prefix", ...), or replace it with your own: call self.parser_predict.remove_argument("--output-prefix") first
src/MyAppArgumentParser.py:62: predict argument --fmt: dest output_format belongs to the framework (--output-format); pick another dest, change the framework option with self.parser_predict.update_arguments("--output-format", ...), or replace it with your own: call self.parser_predict.remove_argument("--output-format") first
```

For the four options `remove_argument` refuses ([Update and remove errors](#update-and-remove-errors)), the line leaves out the `remove_argument` choice: `preprocess argument --inputs: dest preprocess_inputs_dir belongs to the framework (--inputs-dir); pick another dest, or change the framework option with self.parser_preprocess.update_arguments("--inputs-dir", ...)`.

`dest="subcommand"` stops the same way, because `args.subcommand` records which subcommand ran: `... dest subcommand belongs to the framework (it records which subcommand ran); pick another dest`. A removed built-in option leaves its flags and `dest` free.

Every command stops this way, including `--help`. So `make build` fails at its startup check, which prints the line once for each of the four help screens.

Two of your own options that share a flag stop the tool the same way, with a `ParserConflictError`:

```
src/MyAppArgumentParser.py:62: predict argument --xb/-x: -x is already taken by --xa/-x; pick another flag
```

### Unrecognized options

An option given to the wrong command, or misspelled, fails as in argparse, with exit code 2. The message says where the option belongs, or which one you meant. In these lines, the parser has a top-level `--verbose` and a `predict` option `--min-length`:

```
run_my_app.py: error: unrecognized arguments: --verbose; --verbose is an option of the top-level command; put it before 'predict'
run_my_app.py: error: unrecognized arguments: -f json; -f is an option of predict and postprocess, not preprocess
run_my_app.py: error: unrecognized arguments: --min-lenght 3; did you mean --min-length?
run_my_app.py: error: unrecognized arguments: --verbos; did you mean --verbose before 'predict'?
```

A misspelled option, `--min-lenght=3` included, is compared without its dashes with the options of its subcommand and of the top-level command. Options hidden from `-h` (`help=argparse.SUPPRESS`) are never offered. A word close to no option, such as a value or `--bogus`, gets no hint.

### Validator errors

A `type=` check of your own, such as a validator in `src/validators.py`, stops the command with exit code 2 and one usage line, whatever it raises. The error line names the option, after the usage. For an option `--peptide-length/-n`:

| The check raises | Error line, after `run_my_app.py predict: error: ` |
|---|---|
| `argparse.ArgumentTypeError("peptide length must be 1 to 50")` | `argument --peptide-length/-n: peptide length must be 1 to 50` |
| `ValueError` or `TypeError`, such as `int("abc")` | `argument --peptide-length/-n: 'abc' is not valid: invalid literal for int() with base 10: 'abc'` |
| Any other exception, such as `RuntimeError("boom")` in `validate_allele` | `argument --allele: the check validate_allele failed on 'X': RuntimeError: boom` |

An exception without a message gives its class name, such as `'abc' is not valid: TypeError`. Python's own `int`, `float` and `complex` keep argparse's message, `argument --count: invalid int value: 'abc'`, and so do the framework's own checks. A top-level option's check reports the same way, after `run_my_app.py: error: `.

### Envelope checks

`core.result_writer.write_results(result, output_prefix, output_format)` writes the *result envelope*; the cookbook's [Write the results](parser-cookbook.md#write-the-results) shows the call and the files it writes. Nothing calls it automatically: your predict branch calls it, and your `postprocess.py` calls it the same way (its template shows how).

It checks its arguments and the envelope before it writes anything, and raises `ValueError` naming the table and the key. Extra keys anywhere are allowed. `warnings` and `errors` are optional and may be `None`, and an envelope without `results` writes nothing: `write_results({}, None, "tsv")` prints an empty line.

| Problem | Message |
|---|---|
| The envelope is not a dict | `write_results: the result must be a dict (the envelope with 'results', 'warnings' and 'errors'), not list` |
| `warnings` or `errors` is not a list or tuple | `write_results: 'warnings' must be a list or tuple, not str` |
| A misspelled `results` | `write_results: the result has no 'results'; it has 'result': rename it to 'results'` |
| `results` is not a list or tuple (or is `None`, in TSV) | `write_results: 'results' must be a list or tuple of tables, not dict` |
| A table that is not a dict | `write_results: results[0] must be a dict (one table), not int` |
| A table without `type` (or `result_type`) | `write_results: results[0] has no 'type'` |
| A misspelled `table_columns` or `table_data` | `write_results: results[0] (type 'peptide_table') has no 'table_columns'; it has 'columns': rename it to 'table_columns'` |
| A table without `table_data` | `write_results: results[0] (type 't') has no 'table_data'` |
| `table_columns`, `table_data` or a row that is not a list or tuple, such as a set | `write_results: results[0] (type 't'): 'table_columns' must be a list or tuple, not str`, and for a row `table_data[0] must be a list or tuple, not set` |
| A row of the wrong length | `write_results: results[0] (type 't'): table_data[0] has length 1, but table_columns has length 2` |
| A result that is not a table, in TSV | `write_results: results[0] (type 'plot') has no 'table_columns'; tsv writes only tables (json writes any result)` |
| A cell or column name that holds a tab or line break, in TSV | `write_results: results[0] (type 't'): table_data[3][1] holds a tab or line break, which tsv cannot hold; clean the value, or write -f json` |
| A format other than `tsv` or `json` | `unsupported output format: 'csv' (expected 'tsv' or 'json')` |
| A format name as `output_prefix`, in any case: the arguments in the wrong order | `write_results: output_prefix is 'tsv', a format name: pass it as output_format, e.g. write_results(result, args.output_prefix, 'tsv')` |
| An `output_prefix` that is no text, path or `None` (a `TypeError`) | `write_results: output_prefix must be a path or None, not int`, and for the parsed arguments themselves `... not Namespace: pass args.output_prefix` |

JSON also writes a result entry that is not a table, such as plot data with no `table_columns` or `table_data`. TSV writes only tables. Every entry needs a `type`.

TSV writes each value with `str()`, and `None` as an empty cell. JSON writes `None` as `null`, and keeps tabs and line breaks inside a value.

## ./configure flags and .env

`./configure` runs from the tool folder. It reads `paths.py` and regenerates two things: the tool's `.env`, and one `setup_<dep>_env.sh` per declared dependency. Both hold this machine's paths, so the tool's `.gitignore` keeps them out of git and each machine runs `./configure` itself.

`./configure` runs `paths.py` as Python, compiled from its text on every run, so it writes no `__pycache__`. It does not load `.env` first: a `paths.py` that reads `os.environ`, such as `os.environ.get("PEPX_PATH", "/opt/pepx")`, sees only what your shell exports. [./configure internals](internals.md#configure-internals) describes the launcher and the order of the writes.

`./configure` takes `APP_NAME` from the first of these that gives one:

| Order | Source | Notes |
|---|---|---|
| 1 | `APP_NAME` in the tool's own `paths.py` | Like `APP_VENV` and `APP_ENTRY` there |
| 2 | `app_name` under `[tool.ngargparser]` in `pyproject.toml` | `cli generate` writes it. It ships in the tarball, and an edit takes effect on the next `./configure` |
| 3 | `APP_NAME` in the tool's own `.env` | For an older tool without `app_name`. Read from the file, so a value passed down by a calling tool is ignored |
| 4 | The folder name | `<name>` for a folder named `ng_<name>-local` (`ng_my-app-local` gives `my-app`) |

`app_name` and `entry` may be written in `"..."` or `'...'`. `./configure` reads `pyproject.toml` with tomllib (or tomli). When the file does not parse, for example because it starts with a UTF-8 byte order mark, it reads the `[tool.ngargparser]` table line by line.

A run prints what it did, and warns about anything that may fail later. With a flag, the `* Set ... in paths.py` lines come last, because `paths.py` is replaced last:

```
* .env file updated
* Detected 1 dependency tools: mhci_predictor
⚠  mhci_predictor_path = '/opt/tools/mhci' does not exist. 'mhci_predictor' may fail until this is corrected.
⚠  mhci_predictor has no virtualenv. It will run under whatever Python is active. Set one with './configure --mhci-predictor-venv=<path>' if it needs its own.
* Shell script for 'mhci_predictor' created at 'setup_mhci_predictor_env.sh'
* Set mhci_predictor_path = '/opt/tools/mhci' in paths.py
  └ Backup saved to 'paths.py.bak'.
```

A run that cannot write one of its files (a read-only tool folder, a full disk) changes nothing, and names the file:

```
✗ Could not write '.env': Permission denied. Nothing else was changed.
```

A read-only `.env` alone does not stop it: the new file replaces the old one, and keeps its mode. A tool `.env` that is not UTF-8 text is written again, with a warning: `⚠  Could not read .env: it is not UTF-8 text. ./configure writes it again from paths.py.`

`./configure -h` lists one flag per setting of every declared dependency, with the setting's current value:

```
usage: ./configure [-h] [--mhci-predictor-path MHCI_PREDICTOR_PATH]
                   [--mhci-predictor-venv MHCI_PREDICTOR_VENV]
                   [--mhci-predictor-module MHCI_PREDICTOR_MODULE]
                   [--mhci-predictor-lib-path MHCI_PREDICTOR_LIB_PATH]
                   [--mhci-predictor-entry MHCI_PREDICTOR_ENTRY]
  ...
  --mhci-predictor-path MHCI_PREDICTOR_PATH
                        Set mhci_predictor_path (currently: None).
```

A flag exists only for a dependency `cli deps add` has declared, so a misspelled or undeclared name is rejected instead of creating something. Flags write their values into `paths.py`. Each one replaces its variable's assignment, multi-line and annotated ones included, and keeps a ` # comment` at the end of its line. A variable `paths.py` leaves out is added right after its dependency's `<dep>_path` line. The previous file is kept as `paths.py.bak`. Both files are written as new files and moved into place last, so a full disk never cuts either one short. A UTF-8 byte order mark at the start of `paths.py` stays, and so do CRLF line endings. A variable assigned in a way `./configure` cannot rewrite safely (chained, or inside an `if`) stops it with exit code 1 and nothing written:

```bash
./configure --mhci-predictor-path=/opt/tools/mhci          # writes mhci_predictor_path into paths.py
./configure --mhci-predictor-venv=/home/me/.pyenv/versions/3.11.3/envs/myenv
./configure --mhci-predictor-module=python/3.11            # optional: environment module to load
./configure --mhci-predictor-lib-path=/opt/lib             # optional: prepended to LD_LIBRARY_PATH
./configure --mhci-predictor-entry=/opt/tools/mhci/run.sh  # optional: its entry script, when it publishes none
```

A run with flags holds `.ngargparser/paths.lock`, which `cli deps add` and `cli deps remove` hold too, so two commands never change `paths.py` at once. It waits up to 30 seconds for another holder, then stops with exit code 1 and nothing changed: `✗ Another ./configure or cli deps is changing paths.py: .ngargparser/paths.lock was still locked after 30 s. Nothing was changed; try again when it is done.` A plain `./configure` only reads `paths.py`, and takes no lock.

A `<dep>_path` still `None` stops `./configure` with exit code 1 and nothing written. One message names every such path, with one command that sets them all and repeats the flags you gave:

```
✗ alpha_path and beta_path are None in paths.py. Set them with './configure --pepx-path=../pepx --alpha-path=<path> --beta-path=<path>', or run 'cli deps remove <name>' to drop the ones this tool does not need.
  Nothing was changed, paths.py included: run ./configure again with every flag you gave.
```

A flag that cannot write `paths.py` stops with exit code 1, and leaves `paths.py`, `.env` and the setup scripts as they were:

| Cause | Message |
|---|---|
| `paths.py` is read-only | `✗ Could not write 'paths.py': Permission denied. paths.py is unchanged.` |
| The tool folder is read-only | `✗ Could not write '.env': Permission denied. Nothing else was changed.`, or the same for `paths.py.bak` when `.env` and the setup scripts are up to date |
| The write fails partway, past a file size limit (`File too large`) or on a full disk | `✗ Could not write 'paths.py': File too large. Nothing else was changed.` |

A `paths.py` that does not parse, or that raises when it runs, stops `./configure` with exit code 1, the line number and the offending line:

```
✗ paths.py line 18: '(' was never closed
    foo = (
✗ paths.py line 35: KeyError: 'NOPE_NOT_SET'
    x = os.environ["NOPE_NOT_SET"]
```

### Dependency rule

`cli deps` and `./configure` use one rule: a dependency is a top-level `<dep>_path` variable whose `<dep>` does not end in `_lib`, `_venv` or `_module` (those are its settings). `cli deps` reads two forms at the top of the file: `<dep>_path = value`, the form `cli deps add` writes, and an annotated `<dep>_path: str = value`. `./configure` runs `paths.py` as Python, so it also sees other forms (a line inside an `if`, a chained `a_path = b_path = ...`, an unpacked or a `+=` one) that `cli deps` does not list or remove; keep to the plain form.

`cli deps add` refuses a stub whose `None` would replace a setting written in such a form, and exits 1:

```
✗ Not added: 'paths.py' already sets foo_path on line 52, in a form cli deps does not read (inside an if, or a chained or unpacked assignment), and the foo_path=None that cli deps adds below it would replace that value. Write it as a plain foo_path = ... line at the top level of 'paths.py' (then cli deps lists it), or edit the file by hand.
```

The `''' [ Title ] '''` line above a block is only a label, so a hand-edited `paths.py` whose title does not match its variables (`T Cell Class I` above `tcell_class_i_path`), or one with no titles at all, still lists and removes correctly. `cli deps remove` matches the variable name first (`mhci-predictor` or `mhci_predictor`), and a title only when exactly one block has it; a title shared by several blocks removes nothing and asks for the variable name. A name written like a dependency name (`pepx`, `Pepx`, `mhci-predictor`) never falls back to a title, so it cannot remove another dependency's block by its label:

```
✗ Not removed: pepx is not declared in paths.py. The title Pepx is on the block of tcell_class_i; to remove that block, run: cli deps remove tcell_class_i
```

A title with spaces, such as `cli deps remove "T Cell Class I"`, still matches a title.

`cli deps add <name>` and the interactive menu refuse these names:

| Name | Why |
|---|---|
| Ending in `_lib`, `_venv`, `_module` or `_path` | `./configure` could not tell it from a setting |
| Starting with a digit | `<name>_path` would not be a valid Python name |
| `app` (any case) | Its settings would be `APP_VENV`, `APP_ENTRY` and so on in `.env`, which are the tool's own |
| One whose settings would be a system variable, such as `ld-library` (`LD_LIBRARY_PATH`), `library`, `dyld-library`, `ld-run`, `c-include`, `cplus-include` or `pkg-config` | Sourcing its setup script would replace your own value of that variable |
| `uv`, one that starts with `uv-` or `uv_`, and one that starts with `python`, such as `python3` | Its settings would start with `UV_` or `PYTHON`, which uv and Python read as their own settings: `UV_PYTHON`, for one, picks the Python that uv uses |
| `node`, `gem`, `lua`, `git-exec`, `pip`, `virtualenv`, `pyspark` and `pyspark-driver` | One of its settings would be a variable another program reads: `NODE_PATH`, `GEM_PATH`, `LUA_PATH`, `GIT_EXEC_PATH`, `PIP_PYTHON`, `VIRTUALENV_PYTHON`, `PYSPARK_PYTHON` or `PYSPARK_DRIVER_PYTHON` |
| One with characters that are not plain ASCII, such as `ünï` | `cli deps` would drop them: `ünï` would become `n` |

The refusal names the variable and the program, such as `✗ Not added: 'node' becomes 'node', and the tool would set NODE_PATH, NODE_VENV, NODE_MODULE, NODE_LIB_PATH, NODE_ENTRY and NODE_PYTHON for it, in .env and in its own environment. Node.js reads NODE_PATH as its own setting, so it could change how Node.js runs in the tool. Pick another name.`

`cli deps add` and `cli deps remove` split each name at commas, so `cli deps add pepx,netmhcpan` adds two dependencies, as `cli deps add pepx netmhcpan` does. The interactive menu reads the answer `pepx, netmhcpan` the same way. A quoted name with a space, such as `"T Cell"`, stays one name.

`cli deps add` prints the `./configure` command that sets the new paths, one command for several names:

```
✓ Added 2 dependency stub(s) to 'paths.py': alpha, beta
   Set their paths: ./configure --alpha-path=/path/to/alpha --beta-path=/path/to/beta
   Or edit 'paths.py' by hand.
```

`cli deps list` shows each block's title, its name and its `<name>_path = ...` line, with `●` for a path filled in and `○` for one still `None`.

The interactive menu adds a name entered twice once, and says so: `⚠  foo was entered twice; added once.`

`cli deps remove` deletes the block's title, its settings, `<dep>_entry` and `<dep>_python` included (a commented-out one too), and the comments `cli deps add` writes. A name with several blocks, as an older menu could write, loses every one, and the summary counts them: `Removed 2 dependency block(s) from 'paths.py': Pepx (pepx, 2 blocks)`. It keeps any other line inside a block, such as your own note or setting, and names it:

```
✓ Removed 1 dependency block(s) from 'paths.py': Pepx (pepx)
  └ Backup saved to 'paths.py.bak'.
⚠  Kept 2 line(s) that cli deps did not write:
     # my note about pepx
     PEPX_EXTRA = "keep me"
```

A line left in `paths.py` that still reads a removed variable, such as `x = pepx_path`, would stop `./configure` with a `NameError`, so `cli deps remove` names it: `⚠  'paths.py' line 17 still uses pepx_path, which this removal deleted: delete or fix that line, or ./configure will fail.`

Every form of `cli deps`, the interactive menu included, exits 1 when a name was refused or not found, or when `paths.py` does not parse, and never overwrites such a file: `✗ paths.py doesn't parse (line 35): <reason>. Fix it, then run cli deps again; nothing was changed.` A `paths.py` that is not UTF-8 text stops every form, `cli deps list` too, with one line such as `✗ paths.py is not UTF-8 text (byte 0xe9 at offset 1220): save it as UTF-8, then run cli deps again; nothing was changed.`

`cli deps add`, `cli deps remove` and the menu hold `.ngargparser/paths.lock` while they read, change and write `paths.py`, as `./configure` with flags does. After 30 seconds of waiting they stop: `✗ Another process (pid 4242) is changing paths.py: it still holds .ngargparser/paths.lock after 30s. Nothing was changed. Wait for it to finish (a cli deps or ./configure), then run cli deps again.`

`cli deps` writes `paths.py` and `paths.py.bak` through a temporary file renamed over each, so a failed write (a full disk) leaves both whole. A byte order mark at the start of `paths.py` stays, and each line keeps its own line ending, in a file that mixes CRLF and LF too; a line it adds gets the ending most lines have. A `paths.py` it cannot write (a read-only file or folder, such as a deployed copy) gives `✗ Could not write 'paths.py': Permission denied.` and exit 1, with the file unchanged. It also exits 1, before any question, outside a tool's top folder, and in a build or deployed copy, where only `cli deps list` runs.

The interactive menu needs a terminal: in a script or CI job it stops with exit code 1. Without `paths.py`, it asks for names first and creates the file. Its Cancel choice exits 0; Ctrl-C or Ctrl-D at a prompt prints `Cancelled.` and exits 1.

### What `./configure` writes to `.env`

`./configure` copies only plain values (text, numbers, paths) from `paths.py` into `.env`. Settings left at `None` are skipped, and an import such as `from pathlib import Path` does not appear there.

`.env` starts with a comment line that lists the keys `./configure` wrote:

```
# Written by ./configure from paths.py; keys added here by hand are removed on the next run. Keys: APP_ROOT,APP_NAME,APP_VENV,APP_ENTRY
```

The next run rewrites `.env` from `paths.py`, so a key you added by hand is dropped, and named:

```
⚠  Removed FOO, BAR from .env: ./configure rewrites .env from paths.py. Put them in paths.py to keep them.
```

`.env` is rewritten only when its content changes; an otherwise identical run reports `* .env file unchanged`.

### Virtualenv resolution

`./configure` finds each dependency's virtualenv from three sources, in order:

| Order | Source | Notes |
|---|---|---|
| 1 | `<dep>_venv` in `paths.py` | An explicit setting always wins; nothing else is consulted |
| 2 | `APP_VENV` in `<dep>_path/.env` | What the dependency publishes about itself; skipped when that `.env` was written for another folder |
| 3 | A conventional virtualenv folder inside `<dep>_path` | `.venv`, `venv`, `env` or `.virtualenv`; used only when exactly one of them exists |

When none of them gives a virtualenv, `./configure` warns, because the dependency then runs with `python3` ([Warnings for missing paths](#warnings-for-missing-paths)). A dependency that is an ngargparser tool needs no venv setting: once it has run its own `./configure`, its `.env` publishes its `.venv` (step 2):

```
* mhci_predictor: using virtualenv declared in its .env: /opt/tools/mhci/.venv
```

Any other dependency with exactly one conventional virtualenv folder is found by step 3:

```
* mhci_predictor: using virtualenv found at /opt/tools/mhci/.venv
```

A virtualenv found in step 3 that was made in another folder (the dependency was moved or copied) still runs its `python`, but activating it does not work:

```
⚠  /opt/tools/mhci/.venv was made for /old/mhci/.venv, so activating it does not work. Recreate it in /opt/tools/mhci (rm -r .venv && uv sync), or run "$MHCI_PREDICTOR_VENV/bin/python" directly.
```

Only those four folder names are tried, and only when the candidate holds both `pyvenv.cfg` and `bin/activate`. Anything else (`pyenv virtualenv 3.11.3 myenv`, a conda env, a `myenv/` inside the dependency's folder) must be set with `--<dep>-venv=`. When two candidates match, neither is adopted, and the run names both:

```
⚠  pepx has more than one virtualenv in '../pepx' (.venv, venv). Not guessing: pick one with './configure --pepx-venv=<path>', or remove venv there (uv sync uses .venv). Until then, pepx runs with the system Python.
```

Without `.venv` among them, the line says `remove all but one of them there`.

The dependency's virtualenv then follows its own `.env`, as when nothing was found: what the dependency publishes as `APP_VENV` when the tool runs, or none. A `PEPX_VENV` inherited from a calling tool is never used.

A dependency copied or moved after its own `./configure` keeps a `.env` whose `APP_ROOT` names the old folder, and whose `APP_VENV` and `APP_ENTRY` name that folder's files. `./configure` warns and uses none of its values, and neither do the setup script and `app_env.py`, until `./configure` runs in the dependency's folder:

```
⚠  pepx's .env was written for /home/me/pepx: run ./configure in /home/me/pepx-copy first; its values are not used until then.
```

The virtualenv then comes from step 3. The entry script comes from the dependency's folder: `entry` in its `pyproject.toml`, else its single `src/run_*.py`, and the run says `* pepx: using start-up script found at /home/me/pepx-copy/src/run_pepx.py`. A `.env` reached through a `current` link names the real folder behind it, which counts as the same folder.


### What a tool publishes

Every tool publishes two values in its own `.env` for the tools that depend on it: `APP_VENV`, its virtualenv, and `APP_ENTRY`, its entry script. Step 2 above reads `APP_VENV`; [Start-up script resolution](#start-up-script-resolution) reads `APP_ENTRY`.

`./configure` publishes `APP_VENV` when exactly one conventional virtualenv sits in the tool folder. For a tool whose virtualenv lives elsewhere, set `APP_VENV = "/path/to/venv"` in that tool's own `paths.py`. With two, it publishes none and warns:

```
⚠  Found 2 virtualenvs in this tool (.venv, venv); publishing none as APP_VENV. Remove venv (uv sync uses .venv), or set APP_VENV = "<path>" in paths.py. Until then, tools that call this one run it with the system Python.
```

A `.venv` that was made in another folder (the tool was moved or copied) is not published until you recreate it:

```
⚠  /home/me/my-app-copy/.venv was made for /home/me/my-app/.venv; recreate it: rm -r .venv && uv sync, then run ./configure again. Until then it is not published as APP_VENV.
```

`./configure` takes `APP_ENTRY` from the first of these that gives one:

| Order | Source | Notes |
|---|---|---|
| 1 | `entry` under `[tool.ngargparser]` in `pyproject.toml` | `cli generate` writes `entry = "src/run_<name>.py"`. It ships in the tarball, and an edit takes effect on the next `./configure`. One that names a missing file warns and is skipped |
| 2 | An `APP_ENTRY` already in the tool's `.env`, when it names a file | Older tools set it there by hand. Read from the file, never from the environment |
| 3 | The single `src/run_*.py` | The same rule as the build's startup check |

An `APP_ENTRY` set in the tool's own `paths.py` wins over all three, and is published even when it names a missing file:

```
⚠  APP_ENTRY in paths.py names 'src/nope.py', which does not exist. Publishing it anyway.
```

An `entry` that names a missing file warns and names what is published instead: the `APP_ENTRY` in `.env`, else the single `src/run_*.py`, else nothing. With nothing, the second line follows:

```
⚠  [tool.ngargparser] entry in pyproject.toml names 'src/run_gone.py', which does not exist. Publishing src/legacy_main.py instead.
⚠  [tool.ngargparser] entry in pyproject.toml names 'src/run_gone.py', which does not exist. Publishing nothing.
⚠  No APP_ENTRY published: found 2 src/run_*.py scripts. Set 'entry = "src/<script>"' under [tool.ngargparser] in pyproject.toml so tools that depend on this one can find it.
```

A tool generated before `entry` existed, with several `src/run_*.py` scripts, gets the second warning wherever its `.env` holds no `APP_ENTRY`, as in a fresh clone or a deployed tarball. Add the line that names its entry script:

```toml
[tool.ngargparser]
entry = "src/run_my_app.py"
```

### Start-up script resolution

`./configure` reads each dependency's `APP_ENTRY` from `<dep>_path/.env` and exports the full path as `<DEP>_ENTRY`. A relative `APP_ENTRY` is joined to the `APP_ROOT` in that `.env`, else to the real folder behind `<dep>_path`. Through a `current` link, `<DEP>_ENTRY` and `<DEP>_VENV` so name the same version folder. A `<dep>_entry` you set in `paths.py`, by hand or with `./configure --<dep>-entry=<path>`, wins over it, as `<dep>_venv` does for the virtualenv, and stays fixed. A relative one is joined to `<DEP>_PATH`, by the setup script and `dependency_command` alike. With no entry from either, `<DEP>_ENTRY` stays unset.

The dependency's Python runs the entry script only when it is a Python script. A first `#!` line decides: it names `python`, or it does not. Without one, a name that ends in `.py` is a Python script. Any other entry, such as a shell script or a program, runs by itself, so it must be executable. `setup_<dep>_env.sh`, `load_app_env()` and `dependency_command` all follow this rule, and a `<dep>_python` set in `paths.py` runs the entry script whatever it is.

### Setup scripts

Each generated `setup_<dep>_env.sh` starts with `#!/bin/bash`, then this line:

```
# Generated by ./configure from paths.py. Changes here are lost on the next ./configure.
```

When it is sourced, it does this, in order:

| Step | When |
|---|---|
| `module load <dep>_module`, next to the modules already loaded: no `module purge` | `<dep>_module` is set |
| Activate the dependency's virtualenv: export `VIRTUAL_ENV`, put its `bin/` first on `PATH` and unset `PYTHONHOME`; then export `<DEP>_VENV` | A virtualenv is known: set in `paths.py`, found by `./configure`, or published in the dependency's `.env` when the script is sourced |
| Prepend `<dep>_lib_path` to `LD_LIBRARY_PATH`, with a `:` only when `LD_LIBRARY_PATH` is already set and not empty | `<dep>_lib_path` is set |
| Export `<DEP>_PATH` | Always |
| Export `<DEP>_ENTRY` | The dependency publishes an `APP_ENTRY`, or `<dep>_entry` is set |
| Export `<DEP>_PYTHON`: `<dep>_python` from `paths.py`, else `<DEP>_VENV/bin/python`, else `python3`; empty when no entry script is known, or it is not a Python script | Always |
| Define the function `run_<dep>`, which runs the dependency ([Run a dependency from a shell](#run-a-dependency-from-a-shell)) | Always |

The script does not source the virtualenv's `bin/activate`, and defines no `deactivate`. `activate` first puts back the `PATH` saved by a virtualenv activated before it, which would drop the `bin/` of the module the script just loaded. So an environment module keeps its place on `PATH`, behind the virtualenv's `bin/`. Each script you source puts its own `bin/` first, so after several, the last one sourced comes first on `PATH` and `VIRTUAL_ENV` names it.

With no virtualenv known, the script activates nothing, unsets `<DEP>_VENV` and sets `<DEP>_PYTHON` to `python3`, the first one on the `PATH`.

With no entry known, it unsets `<DEP>_ENTRY`, leaves `<DEP>_PYTHON` empty, and says so on stderr, because the dependency is not deployed or configured yet:

```
setup_pepx_env.sh: pepx publishes no entry script yet (no APP_ENTRY in /opt/tools/pepx/.env, or no .env there yet): deploy and configure pepx, or set pepx_entry in paths.py
```

A dependency `.env` that cannot be read, or is not UTF-8 text, counts as missing, and a line on stderr says why: `setup_pepx_env.sh: /opt/tools/pepx/.env is not UTF-8 text, so its values are not used: save it as UTF-8, or run ./configure in /opt/tools/pepx to write it again`, or `... cannot be read (permission denied), so its values are not used: ask its owner to make it readable`.

A shell without a `module` command (`sh` or `dash`, which do not see a function bash exported, or a host without modules) skips the `module load`, and the script goes on after a warning on stderr:

```
setup_pepx_env.sh: pepx needs the environment module python/3.11, but this shell has no 'module' command; load it first (module load python/3.11), or source this script from a login shell.
```

The scripts are POSIX shell (`.`, not `source`), so `sh` and `dash` can source them too, as `subprocess.run(..., shell=True)` does: `. ./setup_<dep>_env.sh`. They work in a shell with `set -u`, and pass `shellcheck -s sh -S warning`.

When a dependency leaves `paths.py`, `./configure` deletes only setup scripts that carry the generated line. A hand-written `setup_*_env.sh` is kept and named:

```
⚠  Kept 'setup_mine_env.sh': 'mine' is not in paths.py, but ./configure did not write this file. Delete it if nothing uses it.
```

### Warnings for missing paths

A configured folder that does not exist warns but never blocks. `.env` and the shell scripts are still written, so a path that is valid on the deploy target but absent on the machine running `./configure` works:

```
⚠  pepx_path = '/opt/tools/pepx' does not exist. 'pepx' may fail until this is corrected.
```

A dependency left with no virtualenv warns for the same reason, since the alternative is finding out at prediction time:

```
⚠  pepx has no virtualenv. It will run under whatever Python is active. Set one with './configure --pepx-venv=<path>' if it needs its own.
```

A dependency whose entry script is not a Python script runs by itself, so it gets no such warning, and neither does one whose `<dep>_python` is set in `paths.py`, since that Python runs it. The warning holds in a shell: `"$PEPX_PYTHON"` is then `python3`, the active one. From Python, [`dependency_command`](#run-a-dependency-from-python) instead runs the first `python3` on a `PATH` without any virtualenv's `bin/`.

A dependency's `.env` that is not UTF-8 text, or cannot be opened, publishes nothing, as a missing one does, and is named once. The setup script and `app_env.py` ignore it too:

```
⚠  Could not read /opt/tools/pepx/.env: it is not UTF-8 text. Its values are not used: save it as UTF-8, or run ./configure in /opt/tools/pepx to write it again.
⚠  Could not read /opt/tools/pepx/.env: Permission denied. Its values are not used: ask its owner to make it readable.
```

## Runtime .env

At run time, `src/core/app_env.py` loads the `.env` next to the tool's `src/` folder, without searching parent folders. It runs when the entry script imports `core.set_pythonpath`, which then puts `APP_ROOT/libs/` and its package folders at the end of the import path, after the tool's own `src/` and its packages ([libs import order](internals.md#libs-import-order)).

A `.env` that is not UTF-8 text stops the tool, with exit code 1:

```
/path/to/my-app/.env is not UTF-8 text: run ./configure again to rewrite it
```

| Key | Where the running tool takes it from |
|---|---|
| Any key the tool's `.env` defines | That `.env`; exporting the variable does not override it, so run `./configure` to change it |
| `PATH`, `PYTHONPATH`, `PYTHONHOME`, `LD_LIBRARY_PATH`, `HOME` | The environment, when it has them; the `.env` value only fills a missing one |
| A key listed in `APP_KEYS_FROM_DEPS` | The dependency's own `<DEP>_PATH/.env`, read again at start-up, unless it was written for another folder; the tool's `.env` value is the fallback. With neither, the key is unset, even when the environment has it |
| `APP_VENV`, `APP_ENTRY`, `APP_KEYS_FROM_DEPS`, and a declared dependency's `<DEP>_PATH`, `_VENV`, `_ENTRY`, `_MODULE` and `_LIB_PATH`, when the `.env` does not define them | Nowhere: an inherited value describes the calling tool, so it is unset |
| A declared dependency's `<DEP>_PYTHON`, when the `.env` does not define it (`<dep>_python` in `paths.py` puts it there) | Worked out as the setup script exports it: empty when `<DEP>_ENTRY` is unset or not a Python script, else `<DEP>_VENV/bin/python`, else `python3`; an inherited one is replaced |
| Any other key the `.env` does not define | The environment |

So a tool started by another tool (the IEDB tool phbr starting tc1, for example) uses its own `APP_ROOT`, `libs/`, virtualenv and dependency paths, not the caller's.

`.env` records the absolute folder `./configure` ran in as `APP_ROOT`. A tool copied or moved after that stops instead of importing the old folder's `libs/`. When its `.venv` came along, a middle line says so:

```
This tool was configured in <X> but is running from <Y>.
<Y>/.venv was made for <X>/.venv; recreate it first: rm -r .venv && uv sync
Run './configure' in <Y>, then re-run this command.
```

In a build or deployed copy (a folder with `.ngargparser-build` and `deploy/install.sh`), this hint and the other `.venv` hints of `./configure` name `bash deploy/install.sh` in place of `uv sync`. The middle line then reads `<Y>/.venv was made for <X>/.venv; recreate it first: rm -r .venv && bash deploy/install.sh, which runs ./configure too`, and the last `Run that in <Y>, then re-run this command.`

An `APP_ROOT` in the environment that points at the tool itself lets it run, even when `.env` names another folder, as the build's startup check and `cli sync` set it. The tool then imports its own `libs/`. Without a `.env` (a build copy, or a tool not configured yet), an inherited `APP_ROOT` is kept only when it points at this tool. One that points elsewhere belongs to a calling tool, so it is dropped with `APP_NAME`, and the tool stops with "not configured".

A tool with no `.env`, or one without `APP_ROOT`, stops with:

```
This tool has not been configured yet: '.env' is missing or incomplete.
Run './configure' in the tool folder (/path/to/my-app), then re-run this command.
```

Nothing calls python-dotenv's `load_dotenv()`, which searches parent folders: so a build copy under `build/` never reads the tool folder's `.env`, and a deployed copy without one never reads another tool's. `./configure` does not load `.env` into its own environment either.

### Run a dependency from Python

`core.app_env.dependency_command(name, *args)` returns `(argv, env)` for `subprocess`, from the values `load_app_env()` put in `os.environ`. The entry script's import of `core.set_pythonpath` calls `load_app_env()`. When nothing in the process has called it, such as in a helper module or a test that imports only `core.app_env`, `dependency_command` calls it first. The cookbook's [Call a dependency](parser-cookbook.md#call-a-dependency) shows a `predict` branch that uses it.

At each call, it reads `<DEP>_VENV` and `<DEP>_ENTRY` again from the dependency's `.env`, when they [follow it](internals.md#values-that-follow-a-dependency). So a dependency redeployed while your tool runs, whose new version names another entry script, is called as it is now.

| Part | Value |
|---|---|
| `<DEP>` | `name` in capitals, with `-` as `_`: `mhci-predictor` gives `MHCI_PREDICTOR`, and `Pepx` gives `PEPX` |
| `argv`, for a Python entry script | The Python, then `<DEP>_ENTRY`, then `args`. The Python is `<dep>_python` when `paths.py` sets it, else `<DEP>_VENV/bin/python`, else the first `python3` on `env`'s `PATH` |
| `argv`, for any other entry script | `<DEP>_ENTRY`, then `args`: the script runs by itself ([Start-up script resolution](#start-up-script-resolution)). A `<dep>_python` set in `paths.py` still runs it |
| `env` with a virtualenv | A copy of `os.environ` set up as activating it does: `VIRTUAL_ENV` is `<DEP>_VENV`, its `bin/` comes first on `PATH`, and `PYTHONHOME` is removed. So a dependency that runs `python` or a console script by name gets its own |
| `env` without one | A copy of `os.environ` without `VIRTUAL_ENV`, and without the `bin/` of any virtualenv on `PATH`: your tool's, and one that a tool that started yours left there. So the dependency does not run inside another tool's virtualenv by accident |
| `env`, either way | `<DEP>_LIB_PATH`, when set, goes in front of `LD_LIBRARY_PATH`. Your tool's `APP_ROOT`, `APP_NAME`, `APP_VENV`, `APP_ENTRY` and `APP_KEYS_FROM_DEPS` are left out: they describe your tool, not the dependency |

`load_app_env()` sets `<DEP>_PYTHON` for each declared dependency by the same rule, as `setup_<dep>_env.sh` exports it, unless the tool's own `.env` sets one. It is empty when no entry script is known, and for an entry script that is not a Python script.

It prints a warning on stderr, and goes on, in two cases:

| Case | Warning |
|---|---|
| No virtualenv, for a Python entry script | `pepx publishes no virtualenv; running it with /usr/bin/python3` |
| `<dep>_module` is set, and `LOADEDMODULES` does not list it | `pepx needs the environment module python/3.11, which dependency_command cannot load: load it before starting this tool, or call the dependency through setup_pepx_env.sh` |

`module load` needs a shell, so the helper cannot load a module. `LOADEDMODULES` counts when it lists the module exactly, or a version below it such as `python/3.11/x`.

It raises `DependencyError`, from `core.app_env`, when it cannot run the dependency. `DependencyError` is a `RuntimeError`, so `except RuntimeError` catches it too:

| Cause | Message |
|---|---|
| `name` is not a declared dependency (a close one is suggested) | `pepxx is not a dependency of this tool (declared: pepx): check the name (did you mean pepx?), or add it with cli deps add pepxx` |
| `paths.py` declares it, but `./configure` has not run since | `run ./configure: paths.py declares pepx, but .env has no values for it yet` |
| Its path has no `.env`: it is not deployed or configured there | `pepx publishes no entry script: /opt/tools/pepx/.env does not exist. Deploy and configure pepx there, or set pepx_entry in paths.py, then run ./configure` |
| Its `.env` was written for another folder, and its folder names no entry script | `pepx publishes no entry script: /opt/tools/pepx/.env was written for /old/pepx. Run ./configure in /opt/tools/pepx, or set pepx_entry in paths.py, then run ./configure here` |
| Its `.env` publishes no `APP_ENTRY` | `pepx publishes no entry script (APP_ENTRY in /opt/tools/pepx/.env, or [tool.ngargparser] entry): set pepx_entry in paths.py, then run ./configure` |
| Its `.env` is not UTF-8 text, or cannot be read, and `paths.py` sets no `pepx_entry` | `pepx publishes no entry script: /opt/tools/pepx/.env is not UTF-8 text, so its values are not used. Save it as UTF-8, or run ./configure in /opt/tools/pepx to write it again; or set pepx_entry in paths.py, then run ./configure` |
| The entry script is not a Python script, and not executable | `shdep's entry /opt/shdep/run.sh is not a Python script and not executable: chmod +x it, or point shdep_entry in paths.py at its start-up script` |
| Its folder is gone, as during a redeploy | `pepx is not deployed at /opt/tools/pepx right now (a redeploy in progress?); try again` |
| Its entry script or its Python is gone | `pepx cannot run right now: /opt/tools/pepx/src/run_pepx.py does not exist (a redeploy in progress?); try again` |

A newly generated entry script catches `DependencyError` around `main()`, prints its message as one line and exits 1; any other error keeps its traceback:

```python
from core.app_env import DependencyError

if __name__=='__main__':
    try:
        main()
    except DependencyError as e:
        sys.exit(str(e))
```

`cli sync` never changes the entry script, so a tool generated earlier shows a traceback that ends in the message. Add these lines, or catch the error around the call ([Call a dependency](parser-cookbook.md#call-a-dependency)).

`<DEP>_PATH`, as `./configure` stores it, is the path you gave. A relative one, such as `../pepx`, is read against the current folder when the tool runs, so give an absolute path on a deploy host.

### Run a dependency from a shell

Each `setup_<dep>_env.sh` defines a function, `run_<dep>`, named after the dependency as `paths.py` spells it: `run_pepx`, `run_mhci_predictor`. Source the script, then call the function with the dependency's arguments:

```bash
. ./setup_pepx_env.sh
run_pepx predict -j job.json -o out
```

`run_pepx` runs this line with the values the script exported, and returns its exit code:

```bash
${PEPX_PYTHON:+"$PEPX_PYTHON"} "$PEPX_ENTRY" "$@"
```

`PEPX_PYTHON` is empty for an entry script that is not a Python script ([Start-up script resolution](#start-up-script-resolution)), so a shell script or a program runs by itself. A Python entry script runs with `PEPX_PYTHON`, the Python of the dependency's virtualenv, whichever virtualenv comes first on `PATH`. The function lives only in the shell that sourced the script: a child shell, such as `bash -c` or a job script, must source the script itself.

With no entry known, `run_pepx` runs nothing, prints one line on stderr and returns 127. A script with `set -e` stops there, with exit code 127:

```
run_pepx: pepx has no entry script (PEPX_ENTRY is empty): deploy and configure pepx, or set pepx_entry in paths.py and run ./configure, then source setup_pepx_env.sh again
```

You can also write the line out, with `"${PEPX_ENTRY:?}"` in place of `"$PEPX_ENTRY"` to stop when no entry is known. With no entry known, the three forms end like this:

| Form | bash | dash (`sh` on Debian and Ubuntu) |
|---|---|---|
| `run_pepx ...` | The line above; returns 127 | The same |
| `${PEPX_PYTHON:+"$PEPX_PYTHON"} "${PEPX_ENTRY:?}" ...` | `PEPX_ENTRY: parameter null or not set`; ends the script with exit code 127, or 1 under `set -e` | `PEPX_ENTRY: parameter not set or null`; ends the script with exit code 2 |
| `${PEPX_PYTHON:+"$PEPX_PYTHON"} "$PEPX_ENTRY" ...` | `: command not found`, exit code 127, and the script goes on unless `set -e` is on; under `set -u`, `PEPX_ENTRY: unbound variable`, exit code 127 | `: Permission denied`, exit code 127, and the script goes on unless `set -e` is on; under `set -u`, `PEPX_ENTRY: parameter not set`, exit code 2 |

The shell form runs the same entry script with the same Python as [`dependency_command`](#run-a-dependency-from-python), with two differences:

| What | Shell form | `dependency_command` |
|---|---|---|
| Your tool's `APP_ROOT`, `APP_NAME`, `APP_VENV`, `APP_ENTRY` and `APP_KEYS_FROM_DEPS`, when the shell exports them | Pass through to the dependency | Left out of `env` |
| A dependency without a virtualenv | Runs with the `python3` first on `PATH`: the caller's active one, such as your tool's `.venv` | Runs with the first `python3` on a `PATH` without any virtualenv's `bin/`, and warns |

## Build

`make build` turns the tool folder into a tarball. This section lists the settings and files you control. The internals page lists every [build step](internals.md#build-pipeline), and every message that [stops a build before it starts](internals.md#preflight-stops).

### Build settings

`scripts/core/build.conf` holds the build settings. It sits next to `build.sh`, but it is yours: `cli sync` never overwrites it. Older tools keep it in `scripts/`; [Removed layouts](#removed-layouts) covers the move. The build sources it as bash in a subshell, so it may only set variables: an `exit` or a failing command in it [stops the build](internals.md#preflight-stops). Only the settings below come back from that subshell, so `build.conf` cannot change a variable of the build itself. Each other variable it sets gets a notice:

```
Notice: scripts/core/build.conf sets SRC_DIR, which the build does not read (only TOOL_NAME, TOOL_VERSION and TARBALL_PREFIX).
```

| Setting | Default | Effect |
|---|---|---|
| `TARBALL_PREFIX` | `IEDB_` | Start of the tarball name: letters, digits, `.`, `_`, `+` and `-`, not starting with `-` or `.` (a hidden tarball would escape the CI job's `build/*.tar.gz`). Empty falls back to `IEDB_`, which the orchestrator expects |
| `TOOL_NAME` | `ng_<name>` | Name of the build copy (`build/<TOOL_NAME>-<version>/`); the tarball uses it in capitals |
| `TOOL_VERSION` | Unset | The version when the `TOOL_VERSION` environment variable is not set |

A `TOOL_VERSION` in the environment wins, so CI always builds the git ref, and the build warns:

```
Warning: scripts/core/build.conf sets TOOL_VERSION=9.9, which is ignored: the TOOL_VERSION environment variable (1.0) wins. Remove TOOL_VERSION from build.conf.
```

### Files a deploy needs

The deploy runs `deploy/install.sh`, which runs `uv sync` and `./configure`, so the tarball must hold these:

| Path | Why |
|---|---|
| `README` | The deploy publishes it |
| `deploy/install.sh` | The deploy runs it |
| `pyproject.toml` | `deploy/install.sh` runs `uv sync`, which reads it |
| `uv.lock`, when the tool has one | `deploy/install.sh` runs `uv sync --frozen`, which installs the versions it pins |
| `configure`, executable | `deploy/install.sh` runs it |
| `paths.py`, when the tool has one | `./configure` on the deploy host reads the dependencies from it |
| `src/core/NGArgumentParser.py`, `core_validators.py`, `result_writer.py`, `app_env.py`, `set_pythonpath.py`, `configure.py` | The framework code the tool runs on; `cli sync` restores a missing one |
| `src/core/__init__.py` | Without it, another package named `core` can take the place of `src/core/`; an empty file will do, and `cli sync` creates a missing one |

The build checks these three times: in the preflight, after the copy and after `scripts/hooks.sh`. The preflight stops the build, with nothing changed, when the tool folder lacks one or `.distignore` excludes one, and names the reason for each:

```
ERROR: The tool folder lacks files a deploy needs, so nothing was built:
  src/core/result_writer.py: framework code the tool runs on (cli sync restores it)
ERROR: .distignore excludes files a deploy needs, so nothing was built. Remove the rules that exclude them:
  uv.lock: deploy/install.sh runs uv sync --frozen, which installs the versions it pins
```

A `configure` without its exec bit stops the build with its own [message](internals.md#preflight-stops). A `scripts/hooks.sh` that deletes a file from the build copy fails the build with `scripts/hooks.sh removed files a deploy needs from the build copy:` and the same lines.

`README`, `deploy/install.sh` and a top-level `.python-version`, which uv on the deploy host reads, always ship. A `.distignore` rule that would exclude one has no effect, and the build says so:

```
Notice: .distignore rule '/README' has no effect on README: the build always ships it.
```

Only those paths are forced: a rule such as `/deploy/` still drops every other file in `deploy/`.

### `.distignore` rules

`.distignore` sits at the tool folder's top, next to `.gitignore`, and lists paths that must not ship in the tarball or the build copy. It has exact `.gitignore` semantics, so you can paste `.gitignore` content as is: wildcards, folder-only `name/`, `!` negation, `/` anchoring and `**` all work as in `.gitignore`. A pattern with no `/`, or only a trailing one, matches at every depth, and a pattern with a leading or middle `/` matches from the tool folder's top. A UTF-8 byte order mark at the start of the file is ignored, as git ignores one in `.gitignore`. Matching is case-sensitive on every host: the build pins git's `core.ignorecase` to `false`, so `*.TXT` never excludes `a.txt`, whatever your git config says.

`cli generate` writes this default, below its comment block, anchored so that a `Makefile` or a `.sh` file deeper in the tool ships. A tool without `.distignore` gets the same rules, and each build says so: `Notice: no .distignore; using the default rules (see cli generate's .distignore).`

```gitignore
# Build-time files at the top of the tool folder: make build runs them, a deploy does not.
/Makefile
/scripts/
# Notes for the tool's developers; its users get README, which always ships. A
# tool whose pyproject.toml has a [build-system] and readme = "README.md" may
# need it on the deploy host (hatchling and uv_build read it there; setuptools
# only warns): delete this rule then.
/README.md
# Written on each host by ./configure.
/setup_*_env.sh
# The tool's own tests.
/tests/
__pycache__/
# Package metadata a build or an editable install writes.
*.egg-info/
*.bak
```

A build backend that needs the readme to build the package, such as hatchling, stops the build at the [startup check](internals.md#startup-check) while `/README.md` is excluded. setuptools only warns, and builds.

Each build prints what `.distignore` excluded: the top of each excluded tree, without hidden names or fetched entries. `make build` lists the first 20, and `make build-verbose` lists all:

```
Excluded by .distignore: 4 paths
  Makefile
  README.md
  scripts/
  tests/
```

Without a `.distignore`, the build uses the default rules and the line reads `Excluded by the default rules: <n> paths`.

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

- `build/`, `.git/`, `.venv/` and `.ngargparser/` at the tool folder's top are always excluded, and so are `venv/`, `env/` and `.virtualenv/` when they hold a virtualenv (a `pyvenv.cfg`). No `!` rule brings them back, so a whitelist `.distignore` (`*`, then `!*/` and `!` rules for what ships) is safe. The deploy host makes its own `.venv` with `uv sync`.
- Temporary files that a stopped `cli` command left next to a file never ship: `<name>.ngargparser-restore`, `.<name>.<8 characters>.tmp` and `.<name>.<8 characters>.ngargparser-sync`. A notice lists them, since a `.ngargparser-restore` file may hold the only copy of a file from before a sync: `Notice: a cli command that was stopped (such as cli sync) left these temporary files in the tool folder; they do not ship. Check them, then delete them:`
- The top-level `README`, `deploy/install.sh` and `.python-version` always ship; a rule that would exclude one prints a notice instead ([Files a deploy needs](#files-a-deploy-needs)). Other files in `deploy/`, and a nested `README` or `deploy/` folder, follow your rules like any other file.

A rule that excludes `pyproject.toml`, `uv.lock`, `configure`, `paths.py` or a framework file in `src/core/` stops the build ([Files a deploy needs](#files-a-deploy-needs)), and so does a rule that excludes the entry script. A whitelist must re-include each of them, such as `!src/**`, `!pyproject.toml`, `!uv.lock` and `!configure`.

The rules reach every depth of the tool folder, including code you keep in `libs/`. A symlink is copied as a link, so rules never follow it into its target. Git treats a link to a folder as a file, so a rule for one must not end in `/`: write `/shared`, not `/shared/`. The build's symlink error says so when it finds such a link. `.distignore` does not apply to entries fetched by `scripts/get_libs.sh`.

A link whose target stays out of the build copy dangles there, and the symlink error names the target and why:

```
ERROR: Symlinks in the build must be relative and stay inside it. Fix these in the tool folder, scripts/get_libs.sh or scripts/hooks.sh, or keep the tool folder's links out with .distignore:
  src/link.txt -> data.txt (points at src/data.txt, which .distignore (or the hidden-file rule) excludes)
  src/py -> ../.venv/bin/python (points at .venv/bin/python, which the build never ships)
```

Any other broken link ends in `(dangling or a loop)`.

### Adopt the default `.distignore`

`cli sync` never changes `.distignore`, so a tool generated earlier keeps the old default:

```gitignore
__pycache__/
build.sh
build.md
Makefile
*.sh
*.bak
```

Those rules match at every depth: `*.sh` also drops `src/tools/run.sh`, and `Makefile` drops a nested `Makefile`. To adopt the new default, replace the `build.sh`, `build.md`, `Makefile` and `*.sh` lines with `/Makefile`, `/scripts/`, `/README.md`, `/setup_*_env.sh` and `/tests/`, or copy `.distignore` from a fresh `cli generate <name>`. Keep your own rules. Then run `make build` and check the `Excluded by .distignore` list: files that the old `*.sh` dropped deeper in the tool now ship.

### `scripts/get_libs.sh`

`make build` runs `scripts/get_libs.sh` with bash in an empty temp dir, then moves each top-level entry it left there into `libs/`. It runs the script again only when the script or `TOOL_VERSION` changes, or when a fetched entry is missing or was edited by hand ([get_libs internals](internals.md#get_libs-internals) has the rules).

`make libs` runs that step alone, under the same lock and with the same record, so the next build does not fetch again. Run it after you add an entry, before you run the tool from its folder: the code is then in `libs/` for the tool to import. It ends with `libs/ is up to date with scripts/get_libs.sh.`, after `Libs up to date (scripts/get_libs.sh, TOOL_VERSION and the fetched entries unchanged); skipping the fetch.` when nothing changed, and prints `No scripts/get_libs.sh; nothing to fetch.` without the script. `TOOL_VERSION=1.0 make libs` fetches for that version.

get_libs.sh sees these exported variables: `PROJECT_ROOT`, `APP_NAME`, `TOOL_NAME`, `TOOL_VERSION`, `SRC_DIR` and `LIBS_DIR`. Git prompts are off and stdin is empty, so a credential prompt fails the fetch instead of hanging the build. Never write a token into get_libs.sh: read it from a CI/CD variable such as `TOOLS_DEPLOY_TOKEN`.

get_libs.sh must write into its current folder, never into `libs/` itself. A file it adds, changes or deletes under `libs/` fails the build with this message, then one line per path, up to 20:

```
ERROR: scripts/get_libs.sh wrote into libs/ directly. Write into the current directory instead: the build moves each top-level entry left there into libs/ and records it.
  libs/mylib/deep.txt: new, removed again
```

A new path is removed again, so `libs/` is as it was. A changed or deleted one cannot be restored, so it is listed as `changed, left as it is now` or `deleted, not restored`.

Clone into a named folder, never into the current folder itself, which would make `libs/` a git repository inside the tool's. The build refuses that:

```
ERROR: scripts/get_libs.sh left a .git folder at the top of its working folder (git clone ... .): clone into a named folder instead, e.g. git clone <url> mylib.
```

A get_libs.sh saved with Windows line endings stops the build before it runs, with the command that fixes it:

```
ERROR: scripts/get_libs.sh has Windows line endings (CRLF): convert it with 'sed -i "s/\r$//" scripts/get_libs.sh' (and add '*.sh text eol=lf' to .gitattributes)
```

`cli generate` writes a `.gitattributes` with those rules, so a new tool keeps LF line endings in every checkout.

`.distignore` and the hidden-file rule apply only to your own files, not to fetched entries. In the build copy, the build removes `.git` and `__pycache__` folders at every depth of each `libs/` entry, and `.github` and `.gitlab` at the top of each entry. It also deletes `._*` files everywhere in the build copy. Delete anything else you do not want shipped inside get_libs.sh.

When you delete `scripts/get_libs.sh`, what it fetched still ships, and each build says so: `libs/<name> was fetched by a get_libs.sh that no longer exists; it still ships. Delete it, or restore scripts/get_libs.sh.`

`make clean` removes `build/` only. Fetched dependencies stay in `libs/`, so the next build does not download them again. While a build or a sync runs in the tool folder, `make clean` removes nothing and stops with the [lock message](internals.md#build-lock).

### Software you download by hand

Third-party software you download by hand (for example, licensed software behind a download form) goes in get_libs.sh too. Each developer points an environment variable at an unpacked copy, and get_libs.sh copies it into the temp dir.

Example 3 in the template shows the pattern: it copies `$MYTOOL_DIR` when that variable is set, builds without the tool in CI (where `CI` is set), and stops with an error otherwise. A local build must stop rather than skip, because a skipped fetch is remembered as done: setting the variable afterwards has no effect until get_libs.sh or `TOOL_VERSION` changes. To switch to a different copy later, delete its folder in `libs/` and build again.

### Build hook

The build hook, `scripts/hooks.sh`, is optional, and most tools do not need it: `cli generate` does not create it, and the build skips the step when it is missing. Files to keep out of the tarball belong in `.distignore`, and outside code belongs in `scripts/get_libs.sh`.

To add one, copy the template from the installed ngargparser. With a `uv tool install`, the templates sit inside uv's tool folder:

```bash
cp "$(uv tool dir)"/ngargparser/lib/python3*/site-packages/ngargparser/templates/hooks.sh scripts/hooks.sh
```

In a clone of the ngargparser repository, the same file is `ngargparser/templates/hooks.sh`.

The hook works on the build copy, so nothing it changes reaches the tool folder. Use it to patch copied files, generate code, sign, or validate. It runs with bash in `build/<TOOL_NAME>-<version>/libs/`, after the copy, the first symlink check and the `README` version stamp. It runs before `VERSION`, the second symlink check, the startup check and the tarball. Symlinks a hook creates must be relative and stay inside the build. A hook that fails stops the build, in both modes, with `ERROR: scripts/hooks.sh failed (exit 3).` In progress mode (`make build`), the hook's output goes to `.ngargparser/hooks.log`, and the log's last 20 lines follow that line. A hook saved with Windows line endings stops the preflight, as get_libs.sh does.

The hook runs in a process group of its own, so what it starts stops with the build. Where `setsid` exists, as on Linux, it also runs in a session of its own: it can read a terminal on its standard input, but cannot open `/dev/tty`, so a prompt that reads `/dev/tty` fails there.

### Hook variables

These variables are exported to the hook:

| Variable | Value |
|---|---|
| `BUILD_DIR` | The build copy, `<tool folder>/build/<TOOL_NAME>-<version>` |
| `PROJECT_ROOT` | The tool folder |
| `APP_NAME` | The tool folder's name |
| `TOOL_NAME` | `ng_<name>`, or the value in `build.conf` |
| `TOOL_VERSION` | The version, default `local` |
| `TOOL_DIR` | `<TOOL_NAME>-<version>` |
| `SRC_DIR` | The tool's `scripts/` folder (not `src/`) |

Downloads and local copies of third-party software belong in `scripts/get_libs.sh`, which fetches once into the tool folder instead of on every build.

### Which script runs when

The three `*.sh` files you own run at different times, in different places:

| | `scripts/get_libs.sh` | `scripts/hooks.sh` | `deploy/install.sh` |
|---|---|---|---|
| When | `make libs`, and early in `make build`, after the preflight checks: the first time, after a change to the script or to `TOOL_VERSION`, or when a fetched entry is missing or was edited | Every `make build`, after copying, if the file exists | Deploy time, on the target host |
| Run by | `scripts/core/build.sh` | `scripts/core/build.sh` | The nxg-tools-deployments orchestrator |
| Purpose | Bring all outside code into the tool's `libs/` (git repositories, archives, data, software you download by hand) | Patch the build copy (code generation, edits, extra files) | Set up the extracted tarball on the host (`uv sync --frozen --no-dev`, `./configure`, editing `paths.py`) |

### Build in CI

`cli generate` writes a starter `.gitlab-ci.yml` with two stages. Both jobs run in `python:3.11-slim`, install `make`, `git` and the uv that `UV_VERSION` pins (`0.11.14`), so a new uv release cannot change a result without a commit. Raise `UV_VERSION` with the uv you lock `uv.lock` with, and raise the version in `deploy/install.sh` with it: that script installs the same uv on a host that has none.

| Stage | Job | Runs |
|---|---|---|
| `test` | `test` | `uv sync --locked`, `./configure`, then `uv run pytest` |
| `build` | `build` | `uv sync --locked`, then `make build`, with `TOOL_VERSION` set to `$CI_COMMIT_REF_NAME`; keeps `build/*.tar.gz` for one month |

`build` runs only when `test` passes. `uv sync --locked` stops either job when `uv.lock` is out of date. Tag `v1.2.0` builds `build/IEDB_NG_<NAME>-v1.2.0.tar.gz`. In a branch name, each character the build refuses in a version (anything but letters, digits, `.`, `_`, `+` and `-`) becomes `-`, and a leading `-` is dropped: `feature/x#1@a` builds version `feature-x-1-a`.

`./configure` stops the test job when `paths.py` leaves a dependency's path as `None` ([A dependency path is None](../README.md#a-dependency-path-is-none)). Set the path in `paths.py`, or pass it in the job, such as `./configure --pepx-path=/opt/pepx`: a path that does not exist on the runner only warns. Tests that import code `scripts/get_libs.sh` fetches into `libs/` need a `make libs` first.

The orchestrator downloads the tarball from the job named `build`, so keep the job name and `TOOL_VERSION`. The file is yours: `cli sync` never creates or changes it. A tool generated earlier can copy it from a fresh `cli generate <name>`.

GitLab sets `CI`, and two startup check cases fail there instead of skipping: a tool with no `.venv`, and one with several `src/run_*.py` and no `entry` ([Startup check](internals.md#startup-check)). `NGARGPARSER_SKIP_STARTUP_CHECK=1` skips the check anywhere.

## Deploy

The orchestrator, nxg-tools-deployments, extracts the tarball on the target host, runs `deploy/install.sh` from the extracted folder, and publishes the top-level `README`. The build always ships both files.

```
developer             GitLab CI                     target server
─────────             ─────────                     ─────────────
git push  ────────►   make build
                      tarball as CI artifact  ────► nxg-tools-deployments fetches it
                                                    extracts the tarball
                                                    runs bash deploy/install.sh
                                                    flips the `current` symlink on success
```

The generated `deploy/install.sh` installs uv 0.11.14, the version the starter CI pins, when the host has no uv. It puts uv in `~/.local/bin` (`UV_INSTALL_DIR`), and leaves the deploy account's shell start-up files alone (`UV_NO_MODIFY_PATH=1`). Then it runs `uv sync --frozen --no-dev` when the tarball holds `uv.lock`, which installs exactly the locked versions and never rewrites the lock; without one, `uv sync --no-dev`. `--no-dev` leaves out the `dev` group (pytest and other tools for developers), which a plain `uv sync` installs. Then it activates `.venv` and runs `sh ./configure`, which writes the host's `.env`. `sh` runs `configure` even when an archive or a copy dropped its exec bit, and a failing `./configure` stops the script, and with it the deploy.

`paths.py` ships with the paths you filled in, so set each host's paths with `./configure --<dep>-path=...`, by hand or in `deploy/install.sh`.

`cli sync` never overwrites `deploy/install.sh`, so a tool generated earlier keeps its old commands, such as plain `uv sync` or `./configure` without `sh`, until you edit it by hand. The install folder is named after the version (`1.0.0`), so `./configure` takes the tool's name from `[tool.ngargparser] app_name` in `pyproject.toml`.

The orchestrator passes these variables to `deploy/install.sh`:

| Variable | Value |
|---|---|
| `IEDB_STANDALONE_NAME` | The tool's name |
| `IEDB_VERSION` | The version being deployed, such as `0.1.0` |
| `IEDB_VERSION_DIR` | The extracted tarball folder, also the working folder |
| `IEDB_STANDALONE_DIR` | The target's install root, where peer tools live |
| `IEDB_PREVIOUS_VERSION_DIR` | The previously deployed version's folder, or empty |

Tools that call other tools, such as axelf and phbr, use `IEDB_STANDALONE_DIR` to find those peer tools.

Do not hardcode `UV_PYTHON_INSTALL_DIR`, `UV_CACHE_DIR` or `UV_PYTHON_PREFERENCE` in install.sh. They come from the target's `install_shell_init` snippet, which the orchestrator sources first and which lets the cluster share one `/apps/uv/` cache. Hardcoding them breaks installs on developer laptops.

### Extract a tarball by hand

Outside the orchestrator, run `deploy/install.sh` in the extracted folder before you run anything under `src/`. Without that step, the entry script stops with "This tool has not been configured yet":

```bash
tar -xzf IEDB_NG_MY-APP-1.0.0.tar.gz
cd ng_my-app-1.0.0
bash deploy/install.sh
```

It runs `uv sync --frozen --no-dev` and `./configure`, as a deploy does; a plain `uv sync` would install the `dev` group too. Run the copy with `.venv/bin/python`. Another Python stops with:

```
This tool needs its virtualenv: run it with '.venv/bin/python src/run_my_app.py ...'. Run 'bash deploy/install.sh' if .venv is missing.
```

From another folder, the line names the tool folder: `This tool needs its virtualenv: in /opt/ng_my-app-1.0.0, run it with ...`.

## cli sync

`cli sync` upgrades ngargparser first, unless you pass `--no-upgrade` or `--dry-run`, or ngargparser is a local checkout. When the installed version is the latest release, it says so and syncs from it: `ℹ Installed ngargparser 0.4.1 is the latest release (v0.4.1); not reinstalling it.`

Before it changes a framework file, a real sync runs your tool's four help screens, and runs them again after the update: the *test drive*. When a screen that worked before fails after, sync puts back every file it wrote. A real sync also holds `.ngargparser/build.lock`, the lock `make build` takes, so a build and a sync never run at once in one tool folder. [Sync internals](internals.md#sync-internals) describes both.

### What `cli sync` changes

| What | How |
|---|---|
| `src/core/*.py` (except `__init__.py`), `scripts/core/build.sh`, the top-level `Makefile` and `configure` | Overwrites each one whose content differs, through a temporary file and a rename, so a build that has the old file open keeps reading it; creates `scripts/` when the tool has none |
| File modes of those files | `scripts/core/build.sh`, `configure` and `src/core/configure.py` get an execute bit wherever they have a read bit; the other `src/core/*.py` lose theirs. Each fix prints a line, such as `└ Fixed the file mode of scripts/core/build.sh (664 → 775)`. A file sync creates gets the mode your umask gives a new file, with those execute bits: under umask `077`, `src/core/app_env.py` gets `600` and `scripts/core/build.sh` `700`. A created `deploy/install.sh` gets no execute bit (`600` under `077`), since the deploy runs it with `bash` |
| `src/core/__init__.py` | Creates it, empty, when missing; never changes one that is there |
| `deploy/install.sh` | Creates it from the template when missing; never overwrites it |
| `.gitignore` | Creates it when missing; otherwise adds the missing rules to the `# --- ngargparser (managed) ---` section, and replaces an older `.ngargparser/` rule there with `.ngargparser/*` ([Hash record](internals.md#hash-record)). A section it edits, or one with a managed `!` rule above a managed ignore rule, is written in the template's order. A byte order mark and CRLF line endings stay |
| `pyproject.toml` | Sets `[tool.ngargparser] scaffold_version` to the installed version ([Version stamp](internals.md#version-stamp)), and adds `app_name` when missing. Values in `"..."` or `'...'` are edited in place, and a byte order mark and CRLF line endings stay; a line it adds gets the file's line ending. Another form gets a warning to set the value by hand, and the sync goes on. Sync never adds `entry` |
| `README.md` | Sets the ngargparser badge to the installed version and turns it green; a byte order mark and CRLF line endings stay. `cli generate` writes the same badge, so the first sync of a new tool changes nothing here. A `README.md` that is a link is left alone, with one line: `└ ⚠ Skipped the README.md badge: README.md is a link to README.dev.md, and sync does not write the badge through a link.` |
| Legacy names | Moves them to their current names ([Removed layouts](#removed-layouts)) |

Every file sync writes, the hash record included, goes through a temporary file renamed over it. So a write that fails, such as on a full disk, leaves the file whole, and the sync undoes itself ([Sync outcomes](#sync-outcomes)).

Sync never touches `.gitlab-ci.yml`, `.distignore`, `.gitattributes`, `README`, or any other file you own ([File ownership](#file-ownership)).

Before overwriting a framework file, each run copies it into its own folder, `.ngargparser/sync-backups/<UTC time>Z-<pid>/` (for example `20261018T101400.805606Z-1371158`); `--no-backup` skips this. A run that overwrote nothing removes its empty folder, and a rollback deletes only its own. When you edited a framework file since the last `cli generate` or sync, the summary names it and its backup.

`cli sync --dry-run` (`-n`) writes nothing and installs nothing, so its preview uses the installed version's files. It says first which ngargparser a real run would install, or that a real run keeps an installed version [newer than the latest release](#newer-than-the-latest-release). With ngargparser installed from a local checkout (an editable install), a plain `cli sync` skips the upgrade and syncs from the checkout.

When GitLab cannot be reached, a real sync skips the upgrade and syncs from the installed version: `⚠ Couldn't check for a newer ngargparser (GitLab unreachable: network/VPN, credentials, or git missing); syncing from the installed version <X>.` A reachable remote with no release tags installs master and says `⚠ No semver tags on remote; 'latest' resolves to master.`

### Sync outcomes

`cli sync` restores every file when the tool stops starting or the sync fails. Each run ends in one of these ways:

| Outcome | When | Exit code |
|---|---|---|
| Synced and verified | A framework file changed or a legacy file moved, and every help screen that worked before the sync still works | 0 |
| Synced without a startup check | No framework file needed changing (or only a file mode), or the checks are off (`--no-verify`) | 0 |
| Synced, could not verify | The tool did not start even before the sync | 0 |
| Stopped: known clash | The tool has a [known clash](internals.md#sync-safety-checks) with this framework version, and its help screens fail or cannot run before the sync. Nothing changed. A dry run reports it, finishes the preview, and exits 1 too | 1 |
| Stopped: startup check cannot run | Framework files would change, and the tool has no `.venv` (or its `.venv/bin/python` is not executable), an `entry` the build would refuse, or no `entry` and not exactly one `src/run_*.py`. Nothing changed. A dry run reports it and exits 1 too | 1 |
| Stopped: a file cannot be read | A file sync may change cannot be read; the message gives the `chmod` that fixes it. Nothing changed | 1 |
| Stopped: link out of the tool | A file sync would write is a link, or sits in a linked folder, that leads outside the tool. Nothing changed | 1 |
| Stopped: build or sync running | Another build or sync holds `.ngargparser/build.lock`. Nothing changed | 1 |
| Stopped: a write failed first | The first file this run wrote could not be written, such as one in a read-only folder. Nothing changed | 1 |
| Rolled back | A help screen that worked before fails after, sync hit an error, a write failed (a full disk), or its output could not be written (`cli sync \| head`); every file this run wrote is restored | 1 |
| Interrupted | Ctrl-C, a `kill` (SIGTERM), or a closed terminal or dropped ssh session (SIGHUP). Sync restores every file and prints `Cancelled; nothing changed.` | 130, 143, 129 |
| Not a tool folder | No `[tool.ngargparser]` table in `pyproject.toml` and no `src/core/NGArgumentParser.py`; or no `src/`, which the message names. In a folder under a tool, the message names the tool to `cd` to. Nothing is written | 1 |
| Build or deployed copy | The folder has `.ngargparser-build`. Nothing is written | 1 |
| Invalid `pyproject.toml` | `✗ pyproject.toml is not valid TOML: <reason>; fix it, then run cli sync again. Nothing was changed.` | 1 |
| Newer stamp | The tool was last synced with a newer ngargparser than the installed one, or from a checkout whose files the installed one may lack ([Version stamp](internals.md#version-stamp)). Nothing is written; upgrade first, or pass `--allow-downgrade` | 1 |
| Unfinished sync | An earlier sync was killed (`kill -9`, a power cut) or its undo did not finish. Nothing is written, and the message lists the commands that finish it by hand ([Unfinished sync recovery](internals.md#unfinished-sync-recovery)) | 1 |
| Upgrade failed | The installer failed before anything was synced; rerun with `cli s --no-upgrade` | The installer's exit code |

[Sync internals](internals.md#sync-internals) shows the messages of a sync that is rolled back, interrupted or left unfinished, and the checks that decide them.

## Tool folder tree

`cli generate my-app` creates this tree. Items marked *later* appear after the step named:

```
my-app/
├── .distignore              tarball exclusions (.gitignore syntax)
├── .env                     this machine's settings (APP_ROOT, APP_NAME, APP_ENTRY, ...); git-ignored
├── .gitattributes           LF line endings for *.sh, configure, build.conf, src/core/*.py and Makefile
├── .gitignore               base rules plus the "ngargparser (managed)" section
├── .gitlab-ci.yml           starter CI: a test job (pytest), then a build job (make build)
├── .ngargparser/            build and sync state; git-ignored except framework-hashes.json
├── .venv/                   later: uv sync
├── Makefile                 make build, make build-verbose, make clean
├── README                   end-user README; ships in the tarball
├── README.md                developer note with the ngargparser badge; does not ship
├── configure                launcher for src/core/configure.py
├── license-LJI.txt          cli generate <name> only
├── pyproject.toml           metadata, dependencies, [tool.ngargparser] (app_name, entry), pytest settings
├── uv.lock                  locked package versions (commit it)
├── paths.py                 later: cli deps add
├── setup_<dep>_env.sh       later: ./configure, one per dependency; git-ignored
├── libs/                    later: a make build whose get_libs.sh fetches something, or your own code
├── build/                   later: make build
├── deploy/
│   └── install.sh           run on the target host at deploy time
├── tests/
│   ├── conftest.py          runs ./configure before the tests when .env is missing
│   └── test_parser.py       two parser tests (uv run pytest); does not ship
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

`cli generate <name>` writes two READMEs. `README` is the end-user README that ships in the tarball, laid out like other IEDB tools: a first line `<name> - version TOOL_VERSION`, with the name as you typed it, then Quick Install, Introduction, Release Notes, Prerequisites, Installation and Usage. The build replaces `TOOL_VERSION` with the version. `README.md` is a short developer note that carries the ngargparser badge, which `cli sync` updates; the default `.distignore` keeps it out of the tarball. `cli generate example` writes an end-user `README` filled in for aa-counter, and its walkthrough of all three commands as `README.md`.

`cli generate example` creates `aa-counter/` with the same layout, except: no `license-LJI.txt`, no `scripts/get_libs.sh`, sample inputs in `src/example.json` and `src/example.tsv`, `src/run_aa_counter.py` with `src/AACounterArgumentParser.py`, and a `src/validators.py` that adds the check for `-a`.

A build copy, and every tarball, adds `VERSION` and `.ngargparser-build` at its top.

## File ownership

*Framework-owned* files belong to ngargparser: `cli sync` overwrites them, so do not edit them. Everything else is yours, with the exceptions in the right column.

| Path | Owner |
|---|---|
| `src/core/*.py` except `__init__.py` | Framework: `cli sync` overwrites each one whose content differs |
| `src/core/__init__.py` | Framework: `cli generate` creates it empty, and `cli sync` creates it, empty, when it is missing; neither changes one that is there |
| `scripts/core/build.sh` | Framework: `cli sync` overwrites |
| `scripts/core/build.conf` | You: the one file in `scripts/core/` that `cli sync` never overwrites (it moves a legacy `scripts/build.conf` here) |
| `Makefile` (top level) | Framework: `cli sync` overwrites |
| `configure` (top level) | Framework: `cli sync` overwrites |
| `pyproject.toml` `[tool.ngargparser] scaffold_version` | Framework: `cli sync` stamps it |
| `pyproject.toml` `[tool.ngargparser] app_name` | You: `cli generate` writes it, and `cli sync` adds it only when missing |
| `pyproject.toml` `[tool.ngargparser] entry` | You: `cli generate` writes it; `cli sync` never adds or changes it |
| `pyproject.toml` (everything else) | You |
| `uv.lock` | You (regenerated by `uv` commands) |
| `README` | You: the end-user README; the build writes the version into the build copy's `README` only |
| `README.md` | You, except the ngargparser badge, which `cli sync` updates |
| `license-LJI.txt` | You |
| `paths.py` | You (`cli deps` writes and removes blocks; `./configure --<dep>-...` or a hand edit fills in values) |
| `.env`, `setup_<dep>_env.sh` | Written for this machine: `.env` by `cli generate` and `./configure`, the setup scripts by `./configure`; both git-ignored. `./configure` rewrites `.env` from `paths.py` and drops keys added by hand. A setup script without the generated line is yours, and `./configure` never deletes it |
| `.gitattributes` | You (`cli generate` writes it, with LF line endings for the files bash runs and the framework files; `cli sync` never touches it) |
| `tests/` | You (`cli generate` writes `tests/test_parser.py` and `tests/conftest.py`; it does not ship) |
| `.gitignore` | You, except the section under `# --- ngargparser (managed) ---`. `cli sync` adds the rules missing from that section and replaces an older `.ngargparser/` line there; it never removes or rewrites another line |
| `.gitlab-ci.yml` | You (`cli generate` writes a starter; `cli sync` never creates or changes it) |
| `.distignore` | You (`.gitignore` syntax; `cli generate` writes the default, `cli sync` never changes it) |
| `scripts/get_libs.sh` | You (`cli generate <name>` creates it; `cli sync` never creates or overwrites it) |
| `scripts/hooks.sh` | You (optional; `cli generate` does not create it; `cli sync` renames legacy hook names to it) |
| `deploy/install.sh` | You: `cli sync` creates it from the template when missing, never overwrites it |
| `libs/` | You, except entries fetched by `scripts/get_libs.sh` |
| `libs/.gitignore` | Framework while its first line is the build's marker; yours otherwise (the build then leaves it alone) |
| `.ngargparser/` | Framework: build and sync state (`build.lock`, `paths.lock`, `libs-stamp`, `get_libs.log`, `hooks.log`, `startup-venv/`, `startup-venv.uv-lock`, `framework-hashes.json`, `sync-backups/`, `sync-in-progress/`, `edited-during-sync-*/`, `removed-libs/`); git-ignored, except `framework-hashes.json` ([Hash record](internals.md#hash-record)); never ships |
| `build/` | Framework: output of `make build`, replaced by the next build |
| `src/run_*.py`, `src/<Name>ArgumentParser.py`, `src/preprocess.py`, `src/postprocess.py`, `src/validators.py` | You |

## Glossary

| Term | Meaning |
|---|---|
| aa-counter | The sample app: a complete tool that `cli generate example` creates |
| `APP_ENTRY` | Key in a tool's `.env`: the tool's entry script, relative to the tool folder; `./configure` takes it from `[tool.ngargparser] entry` first |
| `APP_KEYS_FROM_DEPS` | Key in a tool's `.env`: the keys (such as `CALLEE_VENV`, `CALLEE_ENTRY`) the tool reads again from each dependency's `.env` at start-up |
| `APP_NAME` | Key in a tool's `.env`: the tool's name, from `APP_NAME` in `paths.py`, else `[tool.ngargparser] app_name`, else the tool's own `.env`, else the folder name |
| `app_name` | `[tool.ngargparser] app_name` in `pyproject.toml`: the tool's name for `./configure`, unless `paths.py` sets `APP_NAME`; it ships in the tarball |
| `APP_ROOT` | Key in a tool's `.env`: the absolute path of the tool folder; the tool imports `APP_ROOT/libs/` |
| `APP_VENV` | Key in a tool's `.env`: the tool's virtualenv, published for tools that depend on it |
| Build copy | `build/<TOOL_NAME>-<version>/`, the copy of the tool that `make build` packs; marked by `.ngargparser-build` |
| CI | Continuous integration: a tool's own GitLab CI pipeline, which typically runs `make build` and keeps the tarball as an artifact |
| Dependency | Another program your tool runs, often another IEDB tool, declared in `paths.py` with `cli deps add` |
| `<DEP>_ENTRY`, `<DEP>_PATH`, `<DEP>_VENV` | Variables `./configure` exports for a dependency: its entry script, its folder and its virtualenv |
| `<DEP>_PYTHON` | The Python that runs a dependency: `<dep>_python` from `paths.py`, else `<DEP>_VENV/bin/python`, else `python3`; empty when its entry script is not a Python script. The setup script exports it, and `load_app_env()` sets it |
| Editable install | An install that runs the code in a local checkout, so edits take effect without reinstalling |
| `entry` | `[tool.ngargparser] entry` in `pyproject.toml`: the entry script `./configure` publishes as `APP_ENTRY`, and the one the startup check and `cli sync` run |
| Entry script | `src/run_<name>.py`, the script you run; it builds the parser and dispatches each subcommand |
| `.env` | Plain key and value file in the tool folder with this machine's settings; written by `cli generate` and `./configure`, git-ignored |
| Environment module | A software package loaded with `module load` on an HPC host; set per dependency with `--<dep>-module` |
| Framework-owned | A file `cli sync` overwrites; see [File ownership](#file-ownership) |
| HPC | High-performance computing: the clusters IEDB tools run on |
| `install_shell_init` | The deploy target's shell snippet that the orchestrator sources before `deploy/install.sh`; it sets `UV_*` variables and modules |
| Job description | One entry in `job_descriptions.json`: a command, its id, its type, the jobs it depends on and its outputs |
| Job unit | One independent piece of the input, with its own parameter file; one `predict` run handles one job unit |
| Known clash | Tool code that a framework release is known to break; `cli sync` stops when it finds one |
| `.ngargparser/` | Folder for build and sync state; [File ownership](#file-ownership) lists what it holds |
| `.ngargparser-build` | File at the top of every build copy and tarball, with `TOOL_NAME=` and `TOOL_VERSION=` lines; `cli sync` refuses a folder that has it |
| Orchestrator | nxg-tools-deployments, which fetches a tarball, extracts it on the target host and runs `deploy/install.sh` |
| Parser setup error | `ParserSetupError`: a mistake in your parser code, found while the parser is built; the entry script prints its one line and exits 2 ([Parser setup errors](#parser-setup-errors)) |
| Release tag | A git tag of the form `vX.Y.Z` (the `v` optional, a `+build` suffix allowed); pre-release tags such as `v0.4.2-test` do not count |
| Result envelope | The dict `write_results` takes: a list of tables under `results`, and optional `warnings` and `errors` |
| `scaffold_version` | `[tool.ngargparser] scaffold_version` in `pyproject.toml`: the ngargparser version the tool was last generated or synced with, with a local label such as `+gff1ed80` from a checkout ([Version stamp](internals.md#version-stamp)) |
| `setup_<dep>_env.sh` | Shell script `./configure` writes per dependency; sourcing it loads the dependency's module, virtualenv and paths ([Setup scripts](#setup-scripts)) |
| Startup check | Running the entry script's four help screens; `make build` runs it on the build copy, and `cli sync` before and after a sync, as its test drive |
| Stub block | The four `None` settings `cli deps add` writes for a dependency |
| Subcommand | `preprocess`, `predict` or `postprocess` |
| Subparser | The parser of one subcommand, kept as `self.parser_<subcommand>` |
| `SubparserWrapper` | The class wrapping each subparser. It adds `.help`, `update_arguments` and `remove_argument`, and gives the subparser's `add_argument` the `group=` keyword. `NGArgumentParser`'s own `add_argument` takes no `group=` |
| Tool, tool folder | What `cli generate` creates, and its top folder |
| `TOOL_VERSION` | The version `make build` uses: the environment variable, else `build.conf`, else `local` |
| `TOOLS_DEPLOY_TOKEN` | GitLab CI/CD variable holding a token that get_libs.sh uses to clone in CI |

## Migrate older tools

Tools generated before the current build layout need a one-time migration.

### Removed layouts

The build stops with a message that names the fix when it finds a removed layout:

| Found | Build message |
|---|---|
| `scripts/do-not-distribute.txt`, a top-level `do-not-distribute.txt`, or `scripts/.distignore`, with no top-level `.distignore` | `<file> is no longer read. Move it to .distignore at the project root: git mv <file> .distignore. .distignore uses gitignore patterns, so a bare name also matches nested files and folders of that name; write /name to match only the top-level one, as <file> did.` |
| `scripts/build_hooks.sh` or `scripts/dependencies.sh`, with no `scripts/hooks.sh` | `scripts/<file> is no longer run. Rename it to scripts/hooks.sh (cli sync does this for you).` |
| `scripts/build.conf` | `scripts/build.conf is no longer read. Move it to scripts/core/build.conf (cli sync does this for you).` |
| `scripts/build.conf` and `scripts/core/build.conf` | `scripts/build.conf is no longer read, and scripts/core/build.conf already exists. Merge scripts/build.conf into scripts/core/build.conf by hand, then delete scripts/build.conf.` |
| `scripts/deps.sh` | `scripts/deps.sh is no longer run. Rename it to scripts/get_libs.sh.` |
| `libs` that is a symlink | `libs/ is a symlink (-> <target>). Replace it with a real directory: ...` |
| `libs` that is a file | `libs exists but is not a directory.` |

The old exclusion files matched exact top-level names only, so a bare name in `.distignore` can exclude more. Write `/name` for a top-level-only rule.

`cli sync` renames these legacy names on its first run: `scripts/build.sh` to `scripts/core/build.sh`, `scripts/dependencies.sh` or `scripts/build_hooks.sh` to `scripts/hooks.sh`, `scripts/build.conf` to `scripts/core/build.conf`, and the old exclusion files to `.distignore`. It renames only when the new name does not exist yet. When both `scripts/dependencies.sh` and `scripts/build_hooks.sh` exist, only `dependencies.sh` is renamed; `build_hooks.sh` stays and never runs. Sync warns about any legacy file it could not move, so merge that file into its new name and delete it.

`EXCLUDE_FROM_BUILD_SYMLINK` in `build.conf` has no effect any more, because the build copies everything; a leftover setting prints a notice.

### Move a tool to `scripts/get_libs.sh`

The build copies the tool into `build/` instead of linking to it. Each existing tool needs these steps once:

1. Run `cli sync` to get the new `scripts/core/build.sh`. Sync also renames `scripts/dependencies.sh` or `scripts/build_hooks.sh` to `scripts/hooks.sh`, and moves `scripts/build.conf` to `scripts/core/build.conf`.
2. Copy `scripts/get_libs.sh` from a fresh `cli generate <name>`, or from the templates folder ([Build hook](#build-hook) shows where it is). Move every clone and download from `scripts/hooks.sh` into it. get_libs.sh runs in its own temp dir, so drop `$BUILD_DIR` from those lines. Copies of software you downloaded by hand move too (template Example 3). Keep patching steps in hooks.sh; if none are left, delete hooks.sh.
3. A `scripts/do-not-distribute.txt` (or `do-not-distribute.txt`, `scripts/.distignore`) becomes `.distignore` in step 1, when the tool has no top-level `.distignore` yet. Add a leading `/` to each name that should match only at the top. If both exist, sync warns: merge the old file into `.distignore` and delete it.
4. Delete `EXCLUDE_FROM_BUILD_SYMLINK` from `scripts/core/build.conf`.
5. Delete hand-fetched copies in `libs/` that get_libs.sh now fetches; the build refuses to overwrite entries it did not fetch. If `libs/` itself is a symlink, replace it with a real folder.
6. Keep `scripts/get_libs.sh` out of the tarball. The default `.distignore` does this with `/scripts/`; an older one with `*.sh` ([Adopt the default .distignore](#adopt-the-default-distignore)).
7. Remove, or list in `.distignore`, any folder whose symlinks are absolute, point outside the tool folder, or point at a file the build does not copy (a hidden or excluded file). The build fails on them instead of shipping a broken link.
8. Run `uv sync` and `make build`, then compare the tarball contents with the last release. Each tarball's top folder carries its version, so strip it before comparing:

   ```bash
   diff <(tar -tzf old.tar.gz | sed 's|^[^/]*/||' | sort) <(tar -tzf build/<new>.tar.gz | sed 's|^[^/]*/||' | sort)
   ```

9. Keep `TOOLS_DEPLOY_TOKEN` in CI. A CI checkout starts with an empty `libs/`, so every CI build fetches.

A tool generated before this release also lacks these files or lines, which are yours, so `cli sync` adds none of them. Copy each from a fresh `cli generate <name>`:

| What | Where |
|---|---|
| The starter `.gitlab-ci.yml` | The tool folder |
| The `[tool.pytest.ini_options]` block (`pythonpath` and `testpaths`), and `entry` under `[tool.ngargparser]` | `pyproject.toml` |
| `tests/test_parser.py`, `tests/conftest.py` and `.gitattributes` | The tool folder |
| `dev = ["pytest"]` under `[dependency-groups]`, in place of an older `test` group, which a plain `uv sync` skips | `pyproject.toml`; run `uv lock` after the edit |
| The anchored default `.distignore` | The tool folder ([Adopt the default .distignore](#adopt-the-default-distignore)) |
| `uv sync --frozen --no-dev`, and `sh ./configure` | `deploy/install.sh` |
| The `ParserSetupError` catch around the parser | `src/run_<name>.py` ([Parser setup errors](#parser-setup-errors)) |

### Upgrade from before 0.2.4

`cli upgrade` did not exist before 0.2.4. To get it the first time, reinstall the package with whatever installed it. You do this once; after that, `cli upgrade` is enough:

```bash
# uv tool install:
uv tool install --force --reinstall 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.2.4'
# pip venv:
pip install --upgrade --force-reinstall 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.2.4'
# uv venv without pip:
uv pip install --reinstall 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.2.4'
```

## Troubleshooting

The README's [Troubleshooting](../README.md#troubleshooting) covers the messages a new tool meets first. These are the rest, each with its fix.

### SSH install fails

```
error: Git operation failed
...
git@gitlab.lji.org: Permission denied (publickey,gssapi-keyex,gssapi-with-mic,password).
```

GitLab did not accept an SSH key from this machine. Add your public key under your GitLab user settings (SSH Keys), then check with `ssh -T git@gitlab.lji.org`. Or use the HTTPS install with an access token.

### Invalid tool name

```
✗ Invalid tool name '1abc'. Use a letter followed by letters, digits, underscores, or hyphens (e.g. 'aa-counter').
✗ Invalid tool name 'a-'. End it with a letter or digit: the name is also the Python package name, which cannot end in '-' (e.g. 'aa-counter').
✗ Invalid tool name 'pytest'. A new tool has pytest in its dev dependency group, and the tool's name is also its Python package name, so the two would clash. Pick another name (e.g. 'aa-counter').
```

Start the name with a letter, end it with a letter or digit, and use only letters, digits, `_` and `-`. The name becomes a folder, a Python module (`run_<name>.py`), a class name and a Python package name. So it cannot be a package every tool needs: `pytest`, `python-dotenv`, `dotenv` or `ngargparser`. Case and the choice of `-`, `_` or `.` do not matter, so `Python_Dotenv` is refused too.

### Generated inside another tool

```
✗ This folder is inside the ngargparser tool /home/me/my-app (it has a [tool.ngargparser] table in pyproject.toml). A tool generated here would ship in that tool's tarball, so nothing was created. Run cli generate in a folder outside it: cd /home/me && cli generate other
```

Run the command the message ends with. In the tool folder itself, the message starts `✗ This folder is the ngargparser tool`.

### Folder already exists

```
✗ 'my-app' already exists; aborting.
```

`cli generate` writes nothing when the folder exists. Pick another name, or run it in another folder.

### Generate stops partway

```
✗ Error: [Errno 28] No space left on device: 'my-app/src/run_my_app.py'
  └ Cleaned up the partial tool 'my-app'.
```

`cli generate` could not write a file it fills in, here because the disk is full, so it removed the half-made tool folder. Free some space, or run it on another disk.

### A framework option is reused

A line such as `src/MyAppArgumentParser.py:61: predict argument --organism/-o: -o belongs to the framework (--output-prefix); ...` stops every command, `--help` included. Give your option another flag at the line named, or change the built-in option ([Framework option clashes](#framework-option-clashes), [Change or remove a framework option](parser-cookbook.md#change-or-remove-a-framework-option)).

### git missing at build time

```
ERROR: 'git' is required to build (distignore matching uses 'git check-ignore'). Install git and retry.
```

Install git on the build machine. The build uses it even when the tool is not a git repository.

### No module named dotenv

An older tool, run with a Python that is not its own, stops with `ModuleNotFoundError: No module named 'dotenv'`. Run it with `uv run python`, as for the README's [Tool needs its virtualenv](../README.md#tool-needs-its-virtualenv).

### pyproject.toml does not parse

```
ERROR: pyproject.toml does not parse (a merge conflict marker at line 26): fix it, then build again
```

Resolve the merge in `pyproject.toml`, or fix the line named, then run `uv lock` if the dependencies changed ([Preflight stops](internals.md#preflight-stops)).

### Windows line endings

```
ERROR: scripts/core/build.conf has Windows line endings (CRLF): convert it with 'sed -i "s/\r$//" scripts/core/build.conf' (and add 'build.conf text eol=lf' to .gitattributes)
```

Run the `sed` command the message names, and commit the file. A tool generated earlier lacks the `.gitattributes` that keeps these files LF; copy it from a fresh `cli generate <name>`.

### A build or a sync is running

```
ERROR: Another build or a cli sync is running in this folder (pid 4242). Wait for it to finish.
✗ A build or another sync is running in this folder (pid 4242). Nothing was changed. Wait for it, then run cli sync again.
```

The first line is `make build`'s, the second `cli sync`'s. Wait for the other run to end. Without `flock`, as on macOS, a lock left by a killed build can also stop the build ([Build lock](internals.md#build-lock)).

### Lock file cannot be opened

```
ERROR: cannot open .ngargparser/build.lock (Permission denied): ask its owner to run chmod a+rw .ngargparser/build.lock
✗ Cannot open .ngargparser/build.lock (Permission denied): ask its owner to run chmod a+rw .ngargparser/build.lock. Nothing was changed.
```

In a tool folder you share, another user made the lock file without read access for you. Ask that user to run the `chmod` the message names. A lock file made by this release is readable and writable for everyone.

### Sync needs the virtualenv

```
✗ Sync needs the tool's .venv to check that the tool still starts after the update. Run 'uv sync', then cli sync again (or cli sync --no-verify to update without the check). Nothing was changed.
```

Run `uv sync` in the tool folder, then `cli sync` again.

## Contribute to ngargparser

To work on ngargparser itself, clone it and let uv create a `.venv` with an *editable install* (one that runs the code in your clone):

```bash
git clone git@gitlab.lji.org:iedb/tools/tools-redesign/global-dependencies/ngargparser.git
cd ngargparser
uv sync               # .venv with ngargparser installed from the clone
uv run pytest         # tests
uv run ruff check .   # lint
```

`uv pip install -e .` installs the clone the same way into the active virtualenv (or the one named with `--python`); `pip install .` installs a copy that does not follow later edits.

To try unreleased changes without a clone, install the tip of master, or run `cli upgrade --dev`:

```bash
uv tool install 'git+ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git'
```

`cli upgrade --ref .` installs ngargparser from your working tree in place of a release tag. Pass `.` (from the repository) or a path to a checkout, written as a path (starting with `./`, `../`, `/` or `~`), so a folder named like a git ref (`latest`, `master`) is never mistaken for one; `--check` shows what it would install:

```bash
cli upgrade --ref .                 # install the working tree (run from the ngargparser repository)
cli upgrade --ref /path/to/repo     # the same, from anywhere
cli upgrade --ref . --check         # show what it would install, install nothing
```

The repository's GitLab CI installs the uv that `UV_VERSION` pins (`0.11.14`) in every job, so a new uv release cannot change a result without a commit. Each job installs the dev environment with `uv sync --locked`, which stops the job when `uv.lock` is out of date. The jobs:

| Job | What it checks |
|---|---|
| `ruff` | `ruff check .` and `ruff format --check .`, then `ruff check --select F --ignore F401 ngargparser/templates`, the error rules over the templates the first check leaves out |
| `shellcheck` | `shellcheck -S warning` on the scripts every tool runs: `templates/build.sh`, `deploy/install.sh`, `get_libs.sh`, `hooks.sh` and `configure`. shellcheck comes from PyPI's `shellcheck-py`, pinned by `SHELLCHECK_PY_VERSION` (`0.11.0.1`), so a new shellcheck or another Debian image cannot change the result |
| `mypy` | 7 files, for Python 3.9 with `check_untyped_defs` on: `cli.py`, `core_validators.py`, `result_writer.py`, `app_env.py`, `NGArgumentParser.py`, `NGChildArgumentParser.py` and `templates/configure.py`. The `dev` group pins mypy below 2, which cannot check for 3.9. pytest's own types are skipped (`follow_imports = "skip"`), since a newer pytest uses syntax 3.9 lacks |
| `pytest-fast` | 11 fast test files on Python 3.12, on every push: the `cli` entry point, the parser, the validators, `cli generate`, a generated tool running the README examples, the result writer and deps. The job installs `git`, which two of them run |
| `pytest` | Tag pipelines only (the `full-test` stage). The test suite on Python 3.9, 3.10, 3.11, 3.12, 3.13 and 3.14. The job installs `git` and `make`, which the build tests need: without them those tests skip. The 3.11 leg installs shellcheck and sets `NGARGPARSER_REQUIRE_SHELLCHECK=1`, so the tests that shellcheck the generated setup scripts fail there without it, instead of skipping |
| `pytest-non-root` | Tag pipelines only (the `full-test` stage). The test suite as an ordinary user, with `git` and `make`, because the tests that need a permission error skip as root |
| `pytest-real-uv` | The tests with `real_uv` in their names, with `NGARGPARSER_REAL_UV=1`, on `python:3.11-slim`: they run the real `uv lock` and `uv sync`, offline, against a real cache. The job fills the cache first: it generates a throwaway tool, locks and syncs it online, then locks it again for `requires-python = ">=3.9"`, so the packages older Pythons need are cached too. A skipped test fails the job (`ERROR: a real_uv test was skipped; this job must run them all.`) |

The mypy settings are `[tool.mypy]` in `pyproject.toml`, and the pre-commit mypy hook checks the same 7 files, the template payload included, which the lint, format and whitespace hooks leave out.

The Python blocks in the README, this file and the parser cookbook marked with an `<!-- example: ... -->` comment are pasted into a freshly generated tool and run by `tests/test_readme_examples.py`.
