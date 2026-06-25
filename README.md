# ngargparser

> Framework for building IEDB-style command-line scientific tools — standardized argument parsing, dependency wiring, and reproducible tarball builds.

[![ngargparser](https://img.shields.io/badge/ngargparser-0.2.3-blue.svg)](https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser)

After `pip install`-ing the framework, you get a `cli` command and a Python class (`NGArgumentParser`) that together produce well-shaped scientific CLI apps:

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
uv tool install 'git+ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.2.3'

# Upgrade later
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
cli deps add mhci-predictor pepx     # writes stub blocks to paths.py
$EDITOR paths.py                      # fill in actual binary paths
./configure                           # generate per-tool setup_<name>_env.sh + .env
```

## Project layout

```
my-app/
├── Makefile                    # framework-owned (sync overwrites)
├── configure                   # framework-owned executable
├── pyproject.toml              # Python deps + project metadata + scaffold_version stamp
├── uv.lock                     # locked deps (commit this)
├── README                      # your app's user-facing README
├── license-LJI.txt
├── paths.py                    # declares external tool deps (yours)
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
│   ├── hooks.sh                # imperative build hook (yours)
│   └── do-not-distribute.txt   # exclusion list (yours)
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

`preprocess` and `postprocess` come with built-in arguments (input/output paths, validation flags) so you don't redefine them. `predict` is the customizable subparser — that's where you add tool-specific options.

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

        self.parser_predict.add_argument(
            "--output-prefix", "-o",
            dest="output_prefix",
            help="Prediction output prefix",
            metavar="OUTPUT_PREFIX",
            group="output options",   # same group label clusters args in --help
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
| External binaries / HPC modules / `LD_LIBRARY_PATH` | `cli deps` + manual `paths.py` edits | `paths.py` |

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

`cli deps add` writes a stub: `<name>_path = None`, `<name>_venv = None`, `<name>_module = None`, `<name>_lib_path = None`. You then edit `paths.py` to fill in actual paths and run `./configure` to regenerate `setup_<name>_env.sh` shell scripts and the project's `.env`.

`cli deps remove` accepts the original name (`mhci-predictor`), the display form (`Mhci Predictor`), or the var-name form (`mhci_predictor`).

`cli config-paths` is kept as a deprecated alias that prints a warning and forwards to interactive `cli deps`.

## Building

```bash
make build          # produce build/IEDB_NG_<TOOL>-<VERSION>.tar.gz
make build-verbose  # same, with full build output (no progress bar)
make clean          # remove build/
```

Pipeline (under the hood, `scripts/core/build.sh`):

1. Set up `build/<TOOL>-<VERSION>/`
2. Copy or symlink the source tree into the build dir (symlink by default; copy items listed in `EXCLUDE_FROM_BUILD_SYMLINK` — defaults to `libs run_*.py`)
3. Run `scripts/hooks.sh` (your imperative hook)
4. Tar it up

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

When the framework releases a bugfix or new feature, run `cli sync` from inside any existing project:

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

cli sync                      # pull latest framework files (alias: s)

cli config-paths              # [deprecated] alias for `cli deps` (alias: c)
```

### NGArgumentParser API

`NGArgumentParser` auto-creates three subparsers: `preprocess`, `predict`, `postprocess`. The first two come with built-in arguments; `predict` is yours to customize.

#### Built-in `preprocess` arguments

```
--input-json / -j  JSON_FILE   (required)
--output-dir / -o  DIR         (required)
--params-dir       DIR         (default: $OUTPUT_DIR/predict-inputs/params)
--inputs-dir       DIR         (default: $OUTPUT_DIR/predict-inputs/data)
--assume-valid                 (skip validation)
```

#### Built-in `postprocess` arguments

```
--job-desc-file / -j     JSON   ─┐ pick exactly one
--input-results-dir / -i DIR    ─┘
--postprocessed-results-dir / -p DIR    (required)
--output-prefix / -o  STR
--output-format / -f  FORMAT    (default: json)
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
    "--output-prefix", "-o",
    help="Output prefix",
    group="output options",   # args sharing this label cluster in --help
)
```

#### Updating inherited arguments

```python
self.parser_predict.update_arguments(
    "--output-format", "-f",
    default="tsv",
    help="Updated output format description",
    group="modified options",
)
```

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
| `paths.py` | You (`cli deps` writes stubs; you fill in values) |
| `scripts/hooks.sh` | You |
| `scripts/build.conf` | You |
| `scripts/do-not-distribute.txt` | You |
| `deploy/install.sh` | You — sync creates from template if missing, never overwrites |
| `src/run_*.py`, `src/<App>ArgumentParser.py`, `src/preprocess.py`, `src/postprocess.py`, `src/validators.py` | You |
