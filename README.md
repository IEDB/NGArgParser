# ngargparser

> Framework for building IEDB-style command-line scientific tools — standardized argument parsing, dependency wiring, and reproducible tarball builds.

[![ngargparser](https://img.shields.io/badge/ngargparser-0.4.0-blue.svg)](https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser)

After installing the framework, you get a `cli` command and a Python class (`NGArgumentParser`) that together produce well-shaped scientific CLI apps:

- A standardized `preprocess → predict → postprocess` workflow shared across every app
- A reproducible build that emits a self-contained tarball for HPC distribution
- Two-tier dependency management: `uv` for Python packages, `cli deps` + `paths.py` for external binaries / HPC modules / library paths
- A clean ownership boundary: framework files live in `core/` subdirectories and get sync-managed; everything else is yours

## Prerequisites

ngargparser uses [`uv`](https://github.com/astral-sh/uv) to manage Python versions and virtual environments. Install it first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

You don't need to create a Python venv yourself — `uv` creates a `.venv/` for each scaffolded app and downloads the right Python version on demand. Skip the usual `python -m venv` step.

## Install

### Straight from GitLab (framework users)

```bash
# Best option: install as an isolated uv tool, `cli` on PATH globally.
# Using SSH (recommended for IEDB devs with SSH keys configured):
uv tool install 'git+ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git'

# Using HTTPS (requires token-based auth for private repos):
uv tool install 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git'

# Pin to a tag for reproducibility (SSH form shown; HTTPS works the same way):
uv tool install 'git+ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.4.0'

# Upgrade later (either works)
cli upgrade                 # self-update to the latest release tag on GitLab
uv tool upgrade ngargparser
```

### From a local clone (framework contributors)

```bash
uv pip install -e .
pip install .
```

## Quickstart (5 minutes)

```bash
cli generate my-app                                  # scaffold
cd my-app
uv sync                                              # install Python deps
uv run python src/run_my_app.py predict --help       # see what your app does
make build                                           # build/IEDB_NG_MY-APP-local.tar.gz
```

What just happened:

- **`cli generate`** scaffolded a fresh project tree at `my-app/`. Non-interactive: no prompts, scriptable.
- **`uv sync`** read `pyproject.toml`, locked deps into `uv.lock`, installed them into `.venv/`.
- **`uv run python …`** ran the entry script through the project venv. Out of the box your app understands `preprocess`, `predict`, and `postprocess` subcommands.
- **`make build`** ran the build pipeline (copy + symlink, run hooks, tarball). Output ships to HPC.

When you need an external tool dep:

```bash
cli deps add mhci-predictor pepx                    # writes stub blocks to paths.py
./configure --mhci-predictor-path=/opt/tools/mhci   # fill in paths (or edit paths.py by hand)
./configure                                          # generate per-tool setup_<name>_env.sh + .env
```

See [Configuring tool paths](#configuring-tool-paths-configure) for the full set of flags.

## Project layout

```
my-app/
├── Makefile                    # framework-owned (sync overwrites)
├── configure                   # framework-owned executable
├── pyproject.toml              # Python deps + project metadata + scaffold_version stamp
├── uv.lock                     # locked deps (commit this)
├── README                      # your app's user-facing README
├── license-LJI.txt
├── .distignore                 # tarball exclusions — .gitignore syntax (yours)
├── paths.py                    # declares external tool deps (yours; created by `cli deps add`)
├── src/
│   ├── core/                   # framework-owned (sync overwrites) — DO NOT EDIT
│   │   ├── NGArgumentParser.py
│   │   ├── core_validators.py
│   │   ├── configure.py
│   │   └── set_pythonpath.py
│   ├── run_my_app.py           # entry script (yours)
│   ├── MyAppArgumentParser.py  # subclass of NGArgumentParser (yours)
│   ├── preprocess.py           # your input prep logic
│   ├── postprocess.py          # your aggregation logic
│   └── validators.py           # your custom validators
├── scripts/
│   ├── core/                   # framework-owned (sync overwrites)
│   │   └── build.sh            # build pipeline
│   ├── build.conf              # build knobs (yours)
│   └── hooks.sh                # imperative build hook (yours)
└── deploy/
    └── install.sh              # imperative deploy hook (yours, runs on target host)
```

**Ownership rule:** anything inside a `core/` subdirectory is framework-owned — `cli sync` overwrites those files when you upgrade. Everything else is yours; sync never touches it.

## The three-stage workflow

Every generated app supports three subcommands out of the box:

```bash
python src/run_my_app.py preprocess  -j input.json -o output-dir/
python src/run_my_app.py predict     -j input.json -o output-file
python src/run_my_app.py postprocess -j job_descriptions.json -p final-results/
```

| Stage | Role | Where you customize |
|---|---|---|
| `preprocess` | Validate inputs, split work into job units, emit `job_descriptions.json` | `src/preprocess.py` |
| `predict` | Run the core analysis on a single job unit | `src/MyAppArgumentParser.py` (args) + `src/run_my_app.py` (logic) |
| `postprocess` | Merge per-job results into a single output | `src/postprocess.py` |

All three subparsers come with built-in arguments so you don't redefine them: `preprocess`/`postprocess` bring input/output paths and validation flags, and `predict` brings the output arguments (`--output-prefix/-o`, `--output-format/-f`). `predict` is where you add your tool-specific **input** options.

## Customizing your app

The argument parser for your app lives in `src/MyAppArgumentParser.py` and subclasses `NGArgumentParser`:

```python
import textwrap
import argparse
from core.NGArgumentParser import NGArgumentParser

class MyAppArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()
        self.description = textwrap.dedent("""
            One-line description of your app.
        """)

        self.parser_predict = self.add_predict_subparser(
            help='Run a single prediction.',
            description='Multi-line help that survives formatting.',
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

        # --output-prefix/-o and --output-format/-f are already provided by the
        # base class — add your tool-specific *input* args here instead.
        self.parser_predict.add_argument(
            "--threshold", "-t",
            dest="threshold",
            help="Score threshold",
            metavar="THRESHOLD",
            group="prediction options",   # same group label clusters args in --help
        )
```

Common patterns:

- **Argument grouping** — pass `group="<label>"` to any `add_argument` call.
- **Custom validators** — define a function in `src/validators.py` that raises `argparse.ArgumentTypeError` on bad input, then pass it as `type=...` on `add_argument`.
- **Updating inherited arguments** — to change a default or help text on an argument from the parent class, call `parser.update_arguments(...)` instead of redefining.

The full parser API is in [Reference](#reference) at the bottom.

## Dependencies

ngargparser separates two kinds of dependencies and uses different tools for each:

| Kind | Manage with | Manifest |
|---|---|---|
| Python packages (numpy, pandas, …) | `uv` | `pyproject.toml` + `uv.lock` |
| External binaries / HPC modules / `LD_LIBRARY_PATH` | `cli deps` + `./configure` | `paths.py` |

### Python packages (`uv`)

```bash
uv sync                                          # install locked deps into .venv/
uv add pandas                                    # add a dep, update pyproject.toml + uv.lock
uv remove pandas                                 # remove
uv run python src/run_my_app.py --help           # run inside the project venv
```

Commit `uv.lock` so deploys are reproducible.

### External tool deps (`cli deps`)

```bash
cli deps add mhci-predictor pepx       # write stub blocks to paths.py
cli deps remove pepx                    # remove (alias: rm)
cli deps list                           # show declared deps + which paths are filled in (alias: ls)
cli deps                                # bare → interactive add/remove menu
```

`cli deps add` writes a stub: `<name>_path = None`, `<name>_venv = None`, `<name>_module = None`, `<name>_lib_path = None`. Fill those in with `./configure --<name>-path=…` (or by editing `paths.py` directly), then run `./configure` to regenerate `setup_<name>_env.sh` shell scripts and the project's `.env`.

`cli deps remove` accepts the original name (`mhci-predictor`), the display form (`Mhci Predictor`), or the var-name form (`mhci_predictor`).

### Configuring tool paths (`./configure`)

Run from the project root. It reads `paths.py` and regenerates two things: the project's `.env`, and one `setup_<name>_env.sh` per declared dependency.

`./configure -h` lists a flag for every field of every dependency you've declared, along with what each is currently set to:

```
usage: ./configure [-h] [--mhci-path MHCI_PATH] [--mhci-venv MHCI_VENV]
                   [--mhci-module MHCI_MODULE] [--mhci-lib-path MHCI_LIB_PATH]
                   ...
  --mhci-path MHCI_PATH
                        Set mhci_path (currently: None).
```

Those flags set values without hand-editing `paths.py`, and they **persist** into it:

```bash
./configure --mhci-path=/opt/tools/mhci          # writes mhci_path into paths.py
./configure --mhci-venv=/home/me/.pyenv/versions/3.11.3/envs/myenv
./configure --mhci-module=python/3.11            # optional: environment module
./configure --mhci-lib-path=/opt/lib             # optional: prepended to LD_LIBRARY_PATH
```

A flag only exists for a dependency `cli deps add` has already declared — a typo'd or undeclared name is rejected rather than silently creating something.

#### Virtualenv resolution

| `<name>_venv` in `paths.py` | What `./configure` uses |
|---|---|
| Set to a path | That path, always — auto-detection is skipped entirely |
| Empty, and `<name>_path`/`.venv` exists | That `.venv`, resolved fresh each run and **not** written into `paths.py` |
| Empty, no such `.venv` | No virtualenv — the generated script simply omits its `source` line |

So a tool that bundles its own `.venv` (anything built by `uv sync`, which is every ngargparser project) needs no venv configuration at all:

```
* mhci: using virtualenv found at /opt/tools/mhci/.venv
```

Two things worth knowing about that fallback:

- **It looks only at `{tool_path}/.venv`.** A virtualenv under any other name — `pyenv virtualenv 3.11.3 myenv`, a conda env, or even a `myenv/` sitting inside the tool's own directory — is not found, and must be set with `--<name>-venv=`. Nothing warns when this happens; the tool just runs against whatever Python is active.
- **It is never written to `paths.py`.** Because it's re-derived on every run, the same `paths.py` works unchanged on your laptop, on dev, and on SDSC, each resolving its own local `.venv`. Persisting it would freeze one host's absolute path — and would go stale the moment `<name>_path` moved.

#### Warnings and rewrites

A configured directory that doesn't exist warns but never blocks — `.env` and the shell scripts are still written, so a path that's valid on the deploy target but absent on the machine running `./configure` is fine:

```
⚠  pepx_path = '/opt/tools/pepx' does not exist. 'pepx' may fail until this is corrected.
```

`.env` is rewritten only when its content actually changes; an otherwise identical run reports `* .env file unchanged`.


## Building

```bash
make build          # produce build/IEDB_NG_<TOOL>-<VERSION>.tar.gz
make build-verbose  # same, with full build output (no progress bar)
make clean          # remove build/
```

Building requires `git` on the machine running `make build` (the exclusion rules are evaluated with `git check-ignore`, and git deps in `requirements.txt` are vendored via clone).

Pipeline (under the hood, `scripts/core/build.sh`):

1. Set up `build/<TOOL>-<VERSION>/`
2. Evaluate `.distignore` (exact `.gitignore` semantics, see below)
3. Copy or symlink the source tree into the build dir, skipping excluded paths (symlink by default; copy items listed in `EXCLUDE_FROM_BUILD_SYMLINK` — defaults to `libs run_*.py`)
4. Run `scripts/hooks.sh` (your imperative hook)
5. Tar it up

### `.distignore` — tarball exclusions

Lists paths that must not ship in the tarball (or the staged `build/` tree). Lives at the project root, next to `.gitignore`. The file has **exact `.gitignore` semantics** — you can paste `.gitignore` content verbatim: wildcards, dir-only `name/`, `!` negation, `/` anchoring, and `**` all match at every depth of the project tree.

> **Legacy alias:** projects scaffolded before the rename use `scripts/do-not-distribute.txt`. `build.sh` still honors that name, so those projects keep working untouched. If both exist, root `.distignore` wins. New scaffolds write `.distignore`.

Built-in rules layered around your file by `build.sh`:

- `.*` — hidden files and directories are excluded by default. This is the *lowest*-precedence rule, so a `!` line in your file re-includes them:

  ```gitignore
  !.env              # ship a hidden file
  !.streamlit/       # ship a hidden dir and everything in it
  ```

  `.env` is shown only as a syntax example — **don't actually ship it.** Its `APP_ROOT` is an
  absolute path on the build machine, so a shipped `.env` points every target host at a directory
  that doesn't exist there. `./configure` regenerates it correctly at install time; see
  [What ships in the tarball](#what-ships-in-the-tarball).

  To ship only one file from a hidden dir, re-include the dir first (gitignore cannot re-include a file whose parent dir is excluded), then re-exclude the rest:

  ```gitignore
  !.streamlit/
  .streamlit/*
  !.streamlit/config.toml
  ```

- `.git/` and `build/` (project root) are **always** excluded — no `!` rule can bring them back.
- `README` and `deploy/install.sh` are **always** included — the deploy orchestrator requires them at the tarball top level, so exclusion rules (including the default `*.sh`) cannot strip them.

One caveat: a directory that is itself a symlink is treated as a single file by the matcher, and `tar -h` dereferences it wholesale — exclusion rules do not reach inside symlinked directories.

### `scripts/hooks.sh` — the build hook

This is your imperative escape hatch. It runs after the source tree is in place but before the tarball is created. Working dir: `build/<TOOL>/libs/`. Available env vars: `BUILD_DIR`, `PROJECT_ROOT`, `APP_NAME`, `TOOL_NAME`, `TOOL_VERSION`, `TOOL_DIR`, `SRC_DIR`.

Use it for: vendoring git deps, downloading archives, copying local binaries, code generation, patching files, signing, validation — anything `bash` can do.

### `scripts/build.conf` — declarative knobs

```bash
# Items to COPY instead of symlink (glob-matched). Default: "libs run_*.py".
EXCLUDE_FROM_BUILD_SYMLINK="libs run_*.py"

# Tarball filename prefix.
TARBALL_PREFIX="IEDB_"
```

`build.conf` is user-owned — sync never touches it.

## Deploying

`make build` produces a tarball; **`nxg-tools-deployments`** (an internal IEDB orchestrator at `gitlab.lji.org/iedb/tools/tools-redesign/nxg-tools-deployments`) takes it from there. The full lifecycle:

```
developer                    GitLab CI                 target server
─────────                    ─────────                 ─────────────
git push  ──────────────►   make build  ──────►       (tarball lands here)
                            tarball as
                            CI artifact
                                │
                                │ nxg-tools-deployments fetches
                                ▼
                            (extract tarball)  ──►    bash deploy/install.sh
                            (flip `current` symlink on success)
```

### What ships in the tarball

`make build` produces `build/IEDB_NG_<TOOL>-<VERSION>.tar.gz`. The orchestrator's only contract: the tarball top-level must contain `deploy/install.sh` and a `README` file. Both are scaffolded by `cli generate` and pass through the build pipeline unchanged.

**`.env` never ships** — hidden files are excluded by default, and that's deliberate: `APP_ROOT` is machine-absolute (see [`.distignore`](#distignore--tarball-exclusions)). An extracted tarball is therefore *unconfigured* until `./configure` regenerates `.env` in place. `deploy/install.sh` does that for you, so an orchestrator-driven deploy needs no extra step. Extracting a tarball by hand does — run `uv sync && source .venv/bin/activate && ./configure` before invoking anything under `src/`. Skip it and the entry script exits 1 at import time:

```
This project has not been configured yet — '.env' is missing or incomplete.
Run './configure' from the project root (/path/to/ng_my-app-1.0.0), then re-run this command.
```

### `deploy/install.sh` — the deploy hook

Runs on the target host **after** the tarball is extracted. Working dir is the extracted tarball. The default scaffold installs uv if needed, runs `uv sync` to materialize the `.venv`, then runs `./configure` if present.

The orchestrator passes five env vars (`IEDB_STANDALONE_NAME`, `IEDB_VERSION`, `IEDB_VERSION_DIR`, `IEDB_STANDALONE_DIR`, `IEDB_PREVIOUS_VERSION_DIR`) — most install scripts ignore them and resolve everything from `${BASH_SOURCE[0]}`. The phbr install.sh is the exception: it uses `IEDB_STANDALONE_DIR` to validate and wire in peer tools (`tcell_mhci`, `tcell_mhcii`).

**Do not hardcode `UV_PYTHON_INSTALL_DIR` / `UV_CACHE_DIR` / `UV_PYTHON_PREFERENCE` in install.sh.** Those come from the target's `install_shell_init` snippet (sourced by the orchestrator before invoking install.sh) and let the cluster share a single `/apps/uv/` cache across users. Hardcoding them locks cluster paths into every release tarball and breaks dev-laptop installs.

### Build hook vs. deploy hook

The two `*.sh` user-owned files are easy to confuse. They run at different times in different places:

| | `scripts/hooks.sh` | `deploy/install.sh` |
|---|---|---|
| When | Build time (developer / CI) | Deploy time (target server) |
| Invoked by | `scripts/core/build.sh` | nxg-tools-deployments orchestrator |
| Purpose | Pull stuff INTO the tarball (vendor git deps, codegen, patch source) | Set up the extracted tarball ON the server (uv sync, configure, edit paths.py) |

## Upgrading the framework

Two commands, two different targets — **`cli upgrade` updates the tool; `cli sync` updates a project.**

| You want to… | Run | Where |
|---|---|---|
| Update the `cli` tool itself (to get the latest, or before `cli generate`-ing a new project) | `cli upgrade` | anywhere |
| Bring **one project's** framework files up to the installed tool's version | `cli sync` | inside that project |

They overlap in exactly one spot: `cli sync` **self-upgrades the tool first** (unless `--no-upgrade`), so the common "get my project on the latest framework" flow is a single command. That convenience is the *only* reason the two look similar — `cli upgrade` is still the only way to update the tool **without** being in (and rewriting) a project, and it's also what `--check` / `--ref <version>` hang off of.

After a successful `cli upgrade`, it reminds you to run `cli sync` — because `cli upgrade` updates the CLI, not your projects. Run from inside a project that's behind, it names both versions ("You upgraded the CLI to Y, but this project is still on X — run `cli sync` here to update this project to Y"), even when the tool itself was already current. Run from anywhere else, it prints a generic reminder to `cli sync` your projects.

- **`cli upgrade`** — self-updates the installed `ngargparser` tool (the `cli` itself) to the latest
  release tag on GitLab. **This is the one command to update the CLI** (0.2.4+) — it auto-detects how
  ngargparser was installed (uv tool, pip venv, or a pip-less uv venv) and runs the right installer, so
  you never type a long `git+https://…` command. Works from **any** directory and touches no project
  files. Add `--check` to see installed-vs-latest without installing.

  ```bash
  cli upgrade --check        # is a newer release available?
  cli upgrade                # update the cli to the latest tag (alias: u)
  ```

  > **Upgrading from a pre-0.2.4 version (one-time bootstrap).** `cli upgrade` didn't exist before
  > 0.2.4, so to get onto it the first time, reinstall the package directly with whatever installed it
  > (you only do this once — after that it's just `cli upgrade`):
  >
  > ```bash
  > # uv tool install:
  > uv tool install --force --reinstall 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.2.4'
  > # pip venv:
  > pip install --upgrade --force-reinstall 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.2.4'
  > # pip-less uv venv:
  > uv pip install --reinstall 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.2.4'
  > ```

  **Update notifier.** You don't have to remember to check — when you run `cli`, it occasionally prints a
  one-line "a newer version is available" notice (throttled to one remote check per day, shown only in an
  interactive terminal, never in CI/pipelines). Silence it with `NGARGPARSER_NO_UPDATE_CHECK=1`.

  **Install from a local checkout (contributors).** `cli upgrade --ref .` installs ngargparser from your
  current working tree instead of a release tag — handy while developing the framework itself. Pass `.`
  (run from the repo) or a path to a checkout; `--check` dry-runs it:

  ```bash
  cli upgrade --ref .                 # install the working tree (run from the ngargparser repo)
  cli upgrade --ref /path/to/repo     # same, from anywhere
  cli upgrade --ref . --check         # show what it would install, install nothing
  ```

- **`cli sync`** — refreshes the **framework-owned files inside a project** to match the installed
  version (and self-upgrades first, unless `--no-upgrade`). Run it from inside an existing project:

```bash
cd path/to/your-app
cli sync
```

`cli sync` only touches **framework-owned** files. It refreshes:

- `src/core/*.py`
- `scripts/core/build.sh`
- the root `Makefile`
- the `[tool.ngargparser] scaffold_version` stamp in `pyproject.toml`
- the README badge (flips to green to mark "synced")

It never overwrites `validators.py`, `<App>ArgumentParser.py`, `paths.py`, `hooks.sh`, `build.conf`, or anything else in user-owned space. It also migrates legacy filenames from older framework versions (e.g., `scripts/build.sh` → `scripts/core/build.sh`, `scripts/dependencies.sh` → `scripts/hooks.sh`) on first run.

### What sync can't do

For breaking changes — file layouts shifting, schema changes, removed APIs — sync keeps your core files current but won't transform user-edited content. The right move there: read the release notes, scaffold a fresh project with the new framework, and port your application code over.

---

## Reference

### CLI cheatsheet

```
cli --version
cli --help

cli generate <name>           # scaffold a new project (alias: g)
cli generate example          # scaffold the aa-counter example app

cli deps                      # interactive add/remove menu (alias: d)
cli deps add <name> ...       # add stub blocks to paths.py
cli deps remove <name> ...    # remove blocks (alias: rm)
cli deps list                 # show declared deps + status (alias: ls)

cli sync                      # pull latest framework files into a project (alias: s)

cli upgrade                   # self-update the cli to the latest release tag (alias: u)
cli upgrade --check           # report installed vs latest without installing
```

Run from inside a project (not a `cli` subcommand — it's a script in the project root):

```
./configure                   # regenerate .env + setup_<name>_env.sh from paths.py
./configure -h                # list the flags available for your declared deps
./configure --<name>-path=…   # set a dependency's path (also -venv, -module, -lib-path)
```

### NGArgumentParser API

`NGArgumentParser` auto-creates three subparsers: `preprocess`, `predict`, `postprocess`. All three come with built-in arguments. `predict`'s output arguments (`--output-prefix/-o`, `--output-format/-f`) are framework-owned; you add the tool-specific input arguments in your subclass.

#### Built-in `preprocess` arguments

```
--input-json / -j  JSON_FILE   (required)
--output-dir / -o  DIR         (required)
--params-dir       DIR         (default: $OUTPUT_DIR/predict-inputs/params)
--inputs-dir       DIR         (default: $OUTPUT_DIR/predict-inputs/data)
--assume-valid                 (skip validation)
```

#### Built-in `predict` arguments

```
--output-prefix / -o  STR
--output-format / -f  {tsv,json}   (default: tsv)
```

Predict output is serialized by `core.result_writer.write_results` (see **Result output** below): tsv to stdout when no `-o` is given, otherwise `<prefix>.<ext>`. These two arguments come from the base class — add your tool-specific input arguments in the subclass.

#### Built-in `postprocess` arguments

```
--job-desc-file / -j     JSON   ─┐ pick exactly one
--input-results-dir / -i DIR    ─┘
--postprocessed-results-dir / -p DIR    (required)
--output-prefix / -o  STR
--output-format / -f  {tsv,json}    (default: json)
```

#### `SubparserWrapper`

Every subparser exposes `.help` and `.description` as plain attributes:

```python
self.parser_preprocess.help = 'Custom preprocess help'
self.parser_preprocess.description = 'Detailed preprocess instructions'
```

#### Argument grouping

```python
self.parser_predict.add_argument(
    "--threshold", "-t",
    help="Score threshold",
    group="prediction options",   # args sharing this label cluster in --help
)
```

#### Updating inherited arguments

Some arguments are provided by the base class — notably `predict`'s `--output-prefix/-o`
and `--output-format/-f`. To tweak one for **your** tool (change its default, help text,
choices, or group), call `update_arguments(...)` — it edits the inherited argument **in
place**:

```python
# e.g. make this tool default to json instead of the framework-wide tsv:
self.parser_predict.update_arguments(
    "--output-format", "-f",
    default="json",
    help="Updated output format description",
    group="modified options",
)
```

The change is local to your tool and doesn't affect others. An explicit flag on the
command line (`-f tsv`) still overrides whatever default you set.

> ⚠️ Use `update_arguments` for this — do **not** re-declare an inherited argument with a
> fresh `add_argument("--output-format", ...)`. That raises `conflicting option strings`.
> (To replace one entirely, `remove_argument(...)` first, then `add_argument(...)`.)

#### Result output (`core.result_writer`)

Every tool emits the same result envelope and serializes it through the shared,
framework-owned helper `core.result_writer.write_results` (synced into `src/core/result_writer.py`):

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

- **Format** (`-f`): `tsv` (default) or `json`. tsv is flat and pipeable; json
  preserves the full envelope (warnings/errors and any extra per-table metadata).
- **Destination**: no `-o` → stdout; `-o <prefix>` → file(s).
- **Multiple tables**: on stdout each table is prefixed with a `--- <type> ---`
  banner; to file each is written as `<prefix>.<type>.tsv`. A single table gets no
  banner and a single `<prefix>.tsv`.
- **json** always writes the whole envelope verbatim (`<prefix>.json` or stdout).

`predict` defaults to `tsv`; `postprocess` defaults to `json` because the aggregated
envelope carries metadata that tsv can't represent. warnings/errors are echoed to
stderr for tsv so stdout stays a clean data stream.

#### Built-in validators

```python
from core.core_validators import (
    validate_file,                     # file exists + readable
    validate_directory,                # directory exists or can be created
    validate_directory_given_filename, # parent dir of a path
    validate_preprocess_dir,           # special preprocessing dir setup
)
```

#### Custom validators

```python
# src/validators.py
def validate_peptide_length(value):
    try:
        length = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("peptide length must be a number")
    if not 1 <= length <= 50:
        raise argparse.ArgumentTypeError("peptide length must be 1–50")
    return length

# src/MyAppArgumentParser.py
self.parser_predict.add_argument(
    "--peptide-length",
    type=validate_peptide_length,
)
```

### File ownership

| Path | Ownership |
|---|---|
| `src/core/*` | Framework — `cli sync` overwrites |
| `scripts/core/*` | Framework — `cli sync` overwrites |
| `Makefile` | Framework — `cli sync` overwrites |
| `pyproject.toml` `[tool.ngargparser] scaffold_version` | Framework — sync stamps |
| `pyproject.toml` (everything else) | You |
| `uv.lock` | You (regenerated by `uv` commands) |
| `paths.py` | You (`cli deps` writes stubs; `./configure --<name>-…` or a hand edit fills in values) |
| `scripts/hooks.sh` | You |
| `scripts/build.conf` | You |
| `.distignore` | You (`.gitignore` syntax; legacy `scripts/do-not-distribute.txt` still honored) |
| `deploy/install.sh` | You — sync creates from template if missing, never overwrites |
| `src/run_*.py`, `src/<App>ArgumentParser.py`, `src/preprocess.py`, `src/postprocess.py`, `src/validators.py` | You |
