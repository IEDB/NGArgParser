# ngargparser

> Framework for building IEDB-style command-line scientific tools — standardized argument parsing, dependency wiring, and reproducible tarball builds.

[![ngargparser](https://img.shields.io/badge/ngargparser-0.4.1-blue.svg)](https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser)

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
uv tool install 'git+ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.4.1'

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
- **`make build`** fetched dependencies (none yet), copied the project into `build/`, checked that the tool starts, and packed the tarball. Output ships to HPC. Keep editing and running in `my-app/` itself; `build/` is a read-only copy that the next build replaces.

When you need an external tool dep:

```bash
cli deps add mhci-predictor pepx                    # writes stub blocks to paths.py
./configure --mhci-predictor-path=/opt/tools/mhci   # fill in paths (or edit paths.py by hand)
./configure                                          # generate per-tool setup_<name>_env.sh + .env
```

`./configure` runs with the project's `.venv` when it has one, so it works right after `uv sync` without activating anything. See [Configuring tool paths](#configuring-tool-paths-configure) for the full set of flags.

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
├── libs/                       # vendored code: yours, plus deps fetched by scripts/get_libs.sh (git-ignored)
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
│   ├── core/                   # framework-owned (sync overwrites), except build.conf
│   │   ├── build.sh            # build pipeline
│   │   └── build.conf          # build knobs (yours; sync never overwrites it)
│   ├── get_libs.sh             # all outside code, fetched into libs/ (yours)
│   └── hooks.sh                # optional build hook that patches the build copy (yours; not generated)
└── deploy/
    └── install.sh              # imperative deploy hook (yours, runs on target host)
```

**Ownership rule:** anything inside a `core/` subdirectory is framework-owned — `cli sync` overwrites those files when you upgrade. The one exception is `scripts/core/build.conf`, which is yours. Everything else is yours; sync never touches it.

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

<!-- example: class -->
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
            "--threshold",
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
- **Testing your parser** — `parse_args` takes a list, the same as argparse's, so a test doesn't touch the real command line: `args = MyAppArgumentParser().parse_args(["predict", "-j", "in.json"])`. With no list it reads the command line as usual. If your parser overrides `parse_args`, accept and pass on the list: `def parse_args(self, args=None, namespace=None):` then `args = super().parse_args(args, namespace)`.

The examples in this README are run against a freshly generated tool by `tests/test_readme_examples.py`, so they work when pasted. The full parser API is in [Reference](#reference) at the bottom.

## Dependencies

ngargparser separates three kinds of dependencies and uses different tools for each:

| Kind | Manage with | Manifest |
|---|---|---|
| Python packages (numpy, pandas, …) | `uv` | `pyproject.toml` + `uv.lock` |
| External binaries / HPC modules / `LD_LIBRARY_PATH` | `cli deps` + `./configure` | `paths.py` |
| Code and data the tool loads from `libs/` (git repos, executables, data, tools you download by hand) | `scripts/get_libs.sh`, run by `make build` | `scripts/get_libs.sh` |

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

Run from the project root. It reads `paths.py` and regenerates two things: the project's `.env`, and one `setup_<name>_env.sh` per declared dependency. The launcher uses `.venv/bin/python` when the project has one, so the venv doesn't need to be active; without python-dotenv available it says to run `uv sync` first. `APP_NAME` is read from the project's own `.env` file, so a value passed down by a calling tool is ignored.

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

Three sources, tried in order. The first hit wins:

| Order | Source | Notes |
|---|---|---|
| 1 | `<name>_venv` in `paths.py` | An explicit setting always wins; nothing else is consulted |
| 2 | `APP_VENV` in `{tool_path}/.env` | What the dependency publishes about itself |
| 3 | A conventional virtualenv directory inside `{tool_path}` | `.venv`, `venv`, `env`, `.virtualenv`, in that order |

Nothing found is not an error, but it is now said out loud, because the tool will run under whatever Python happens to be active.

So a tool that bundles its own `.venv` (anything built by `uv sync`, which is every ngargparser project) needs no venv configuration at all:

```
* mhci: using virtualenv found at /opt/tools/mhci/.venv
```

Four things worth knowing:

- **Every project publishes its own virtualenv.** When exactly one conventional virtualenv sits in the project root, `./configure` writes `APP_VENV` into that project's `.env`, alongside `APP_ROOT` and `APP_NAME`. That is what step 2 reads, so a tool whose virtualenv lives somewhere unusual only has to be configured once, in its own project, rather than again in every project that depends on it.
- **A dependency's `paths.py` is never read.** It is a Python module, loaded by executing it, so reading another project's copy would run its code inside your `./configure`. `.env` is plain key/value text and is parsed, never executed.
- **Convention, not discovery.** Only those four directory names are tried, and only when the candidate holds both `pyvenv.cfg` and `bin/activate`. Anything else, `pyenv virtualenv 3.11.3 myenv`, a conda env, or a `myenv/` inside the tool's own directory, must be set with `--<name>-venv=`. When two candidates match, neither is adopted: the run says which it found and asks you to pick, rather than binding the tool to whichever the filesystem listed first.
- **Steps 2 and 3 are never written to `paths.py`.** Because they're re-derived on every run, the same `paths.py` works unchanged on your laptop, on dev, and on SDSC, each resolving locally. Persisting one would freeze a single host's absolute path — and would go stale the moment `<name>_path` moved.

#### Warnings and rewrites

A configured directory that doesn't exist warns but never blocks — `.env` and the shell scripts are still written, so a path that's valid on the deploy target but absent on the machine running `./configure` is fine:

```
⚠  pepx_path = '/opt/tools/pepx' does not exist. 'pepx' may fail until this is corrected.
```

A dependency that ends up with no virtualenv at all warns for the same reason, since the alternative is finding out at prediction time:

```
⚠  pepx has no virtualenv. It will run under whatever Python is active. Set one with './configure --pepx-venv=<path>' if it needs its own.
```

`.env` is rewritten only when its content actually changes; an otherwise identical run reports `* .env file unchanged`.

At run time each tool reads its own `.env` (found next to its `src/`, not by searching parent folders). `APP_ROOT` and `APP_NAME` always come from that file, so a tool started by another tool (phbr starting tc1) imports its own `libs/`, not the caller's. Other keys keep the usual rule: a value already set in the environment wins.

**Calling another tool: `APP_ENTRY`.** Every tool publishes its start-up script in its own `.env` as `APP_ENTRY` (`cli generate` writes `APP_ENTRY=src/run_<name>.py`; `./configure` keeps it, or finds the single `src/run_*.py`). With several `run_*.py` scripts, set `APP_ENTRY=src/<script>` in `.env` yourself. A tool that depends on it reads that value, and its `./configure` exports the full path as `<NAME>_ENTRY`, next to `<NAME>_PATH`, in `.env` and in `setup_<name>_env.sh`. Call the dependency through it instead of hard-coding the script's file name, which can change between its versions:

```bash
source setup_tcell_class_i_env.sh
python3 "$TCELL_CLASS_I_ENTRY" predict -j job.json -o out
```

A dependency that publishes no `APP_ENTRY` (an older tool, or not an ngargparser tool) gets no `<NAME>_ENTRY`.


### Vendored dependencies (`scripts/get_libs.sh`)

Code and data your tool imports or reads from `libs/` (git repositories such as allele-validator, predictor executables, percentile data) are downloaded by `scripts/get_libs.sh`. This is separate from `cli deps`, which records where external tools live on a host.

```bash
# scripts/get_libs.sh runs in an empty temp dir; each top-level entry it leaves lands in libs/
git clone -b v0.5.4 --single-branch --depth 1 \
    ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/allele-validator.git
rm -rf allele-validator/.git
```

Third-party tools you download by hand (for example, licensed software behind a download form) go in get_libs.sh too. Each developer points an environment variable at their unpacked copy, and get_libs.sh copies it into the temp dir. CI has no copy, so the template's Example 3 skips the tool when `CI` is set and stops the build otherwise. A local build must stop rather than skip: a skipped fetch is remembered as done, so setting the variable afterwards would be ignored until get_libs.sh changes. To switch to a different copy later, delete its folder in `libs/` and build again.

`make build` runs it first, and only when needed:

| Situation | What happens |
|---|---|
| First build, or `scripts/get_libs.sh` changed | get_libs.sh runs; on success each entry replaces `libs/<entry>` |
| get_libs.sh unchanged, and everything it fetched is still in `libs/` | Skipped |
| get_libs.sh fails | `libs/` stays as it was and the build stops |
| You want a fresh download anyway | Delete the fetched folders in `libs/` (listed in `libs/.gitignore`), then `make build` |

After a fetch:

- Fetched directories get `__init__.py` files, so a run from the project folder imports them the same way the build does.
- `libs/.gitignore` lists the fetched entries so they stay out of git (the file ignores itself). If `libs/.gitignore` is your own file, the build leaves it alone and warns about any fetched entry git would still pick up.
- The build refuses to overwrite a `libs/<name>` that get_libs.sh did not fetch, so code you commit in `libs/` is safe.
- `.ngargparser/libs-stamp` records which `libs/` folders the build owns. If it is lost (a fresh clone with `libs/` copied in, or `.ngargparser/` deleted), the build reads the same list from `libs/.gitignore`, whose first line marks it as the build's. If both are gone, delete the fetched folders in `libs/` and build again.

Fetched entries ship as get_libs.sh left them: `.distignore` and the hidden-file rule apply to your project files, not to dependencies. The build only strips `.git`, `.github`, `.gitlab` and `__pycache__` from them, so delete anything else you do not want shipped inside get_libs.sh. Never write a token into get_libs.sh; read it from a CI/CD variable such as `TOOLS_DEPLOY_TOKEN`, as the template shows.

`make clean` removes `build/` only. Fetched dependencies stay in `libs/`, so the next build does not download them again.

## Building

```bash
make build          # produce build/IEDB_NG_<TOOL>-<VERSION>.tar.gz
make build-verbose  # same, with full build output (no progress bar)
make clean          # remove build/ (fetched dependencies in libs/ stay)
```

Building requires `git` on the machine running `make build` (the exclusion rules are evaluated with `git check-ignore`, and `scripts/get_libs.sh` is fingerprinted with `git hash-object`).

### Developing: edit and run in the project folder

Edit, run and debug in the project folder. Once `make build` has fetched `libs/`, the project folder has everything the build has, so no build is needed between fixes:

```bash
uv run python src/run_my_app.py predict -j input.json -o out
```

`build/` is output: every `make build` replaces it. Its files are read-only, so an editor refuses to save a fix there instead of the fix silently vanishing on the next build. Directories stay writable, so `make clean`, the next build, and new files written by `./configure` keep working. Rewriting an existing file there does not (for example `./configure --x-path=…`, which edits `paths.py`); do that in the project folder.

Pipeline (under the hood, `scripts/core/build.sh`):

1. Set up `build/<TOOL>-<VERSION>/` and remove the previous tarball
2. Fetch dependencies with `scripts/get_libs.sh` into `libs/`, unless nothing changed (see [Vendored dependencies](#vendored-dependencies-scriptsget_libssh))
3. Evaluate `.distignore` (exact `.gitignore` semantics, see below)
4. Copy the project into the build dir, skipping excluded paths. Symlinks are copied as links, never followed
5. Run `scripts/hooks.sh` against the copy, if the project has one
6. Check symlinks: each must be relative and resolve inside the build, or the build fails and lists them
7. Check that the tool starts: run the entry script's `--help`, `predict --help`, `preprocess --help` and `postprocess --help` from the build dir with the project's `.venv`, and `APP_ROOT` set to the build. A crash fails the build and shows the error line. With no `.venv`, or more than one `src/run_*.py`, the check is skipped with a notice
8. Tar it up (symlinks stored as links), then make the build copy read-only

Removed layouts stop the build with a message that names the fix:

- `scripts/do-not-distribute.txt`, a root `do-not-distribute.txt`, or `scripts/.distignore`, without a root `.distignore`
- `scripts/build_hooks.sh` or `scripts/dependencies.sh` without `scripts/hooks.sh`
- a `libs/` that is a symlink

See [Migrating an existing tool](#migrating-an-existing-tool-to-scriptsget_libssh).

### `.distignore` — tarball exclusions

Lists paths that must not ship in the tarball (or the staged `build/` tree). Lives at the project root, next to `.gitignore`. The file has **exact `.gitignore` semantics** — you can paste `.gitignore` content verbatim: wildcards, dir-only `name/`, `!` negation, `/` anchoring, and `**` all match at every depth of the project tree.

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

The rules reach every depth of your project, including code you keep in `libs/`. A symlink is copied as a link, so rules never follow it into its target. Dependencies fetched by `scripts/get_libs.sh` are not filtered (see [Vendored dependencies](#vendored-dependencies-scriptsget_libssh)).

### `scripts/hooks.sh` — the build hook

This file is optional, and most tools do not need it: `cli generate` does not create it, and the build skips the step when it is missing. To add one, copy ngargparser's `templates/hooks.sh` to `scripts/hooks.sh`. Files to leave out of the tarball belong in `.distignore`, and outside code belongs in `scripts/get_libs.sh`.

This is your imperative escape hatch. It runs after the project is copied into the build dir, before the checks and the tarball. It works on that copy, so nothing it changes reaches the project. Working dir: `build/<TOOL>/libs/`. Available env vars: `BUILD_DIR`, `PROJECT_ROOT`, `APP_NAME`, `TOOL_NAME`, `TOOL_VERSION`, `TOOL_DIR`, `SRC_DIR`.

Use it for: patching copied files, code generation, signing, validation, or anything else `bash` can do to the copy. Downloads and local copies of third-party tools belong in `scripts/get_libs.sh`, which fetches once into the project instead of on every build. Symlinks a hook creates must be relative and stay inside the build.

### `scripts/core/build.conf` — declarative knobs

```bash
# Tarball filename prefix.
TARBALL_PREFIX="IEDB_"
```

`build.conf` sits in `scripts/core/` next to `build.sh`, but it is yours: sync never touches it. Older projects keep it in `scripts/`; `cli sync` moves it, and the build stops while `scripts/build.conf` is still there. `EXCLUDE_FROM_BUILD_SYMLINK` is gone, because the build copies everything; a leftover setting prints a notice and has no effect.

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

The three user-owned `*.sh` files are easy to confuse. They run at different times in different places:

| | `scripts/get_libs.sh` | `scripts/hooks.sh` | `deploy/install.sh` |
|---|---|---|---|
| When | Start of `make build`, only when get_libs.sh changed | Every `make build`, after copying (only if the file exists) | Deploy time (target server) |
| Invoked by | `scripts/core/build.sh` | `scripts/core/build.sh` | nxg-tools-deployments orchestrator |
| Purpose | Bring all outside code INTO the project's `libs/` (git repos, archives, data, tools you download by hand) | Patch the build copy (codegen, edits, extra files) | Set up the extracted tarball ON the server (uv sync, configure, edit paths.py) |

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

  Only release tags (`vX.Y.Z`) count as the latest. Pre-release tags such as `v0.4.2-test` or
  `v1.0.0-rc1` are skipped by `cli upgrade`, `cli sync`, the update notice and `cli --version`, so a
  test tag never reaches other developers by default. Install one on purpose with
  `cli upgrade --ref v0.4.2-test` or `cli sync --ref v0.4.2-test`.

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
  interactive terminal, never in CI/pipelines). Silence it with `NGARGPARSER_NO_UPDATE_CHECK=1`; set it
  to `0` or `false`, or unset it, to turn the notice back on.

  **`cli --version` and `cli --help` always check.** Each prints its usual output, then looks up the
  latest release tag on GitLab on every run, with no daily limit and no cache. If the installed version
  is behind, it prints the notice each time, with a link to the release notes. If the installed version
  is ahead of the latest tag, it says that instead. If GitLab can't be reached, it says so in one line.
  The lookup is skipped in CI/pipelines and when `NGARGPARSER_NO_UPDATE_CHECK=1` is set. Subcommand
  help (`cli sync --help`) and bare `cli` keep the once-a-day notice.

  **Install from a local checkout (contributors).** `cli upgrade --ref .` installs ngargparser from your
  current working tree instead of a release tag — handy while developing the framework itself. Pass `.`
  (run from the repo) or a path to a checkout; `--check` dry-runs it:

  ```bash
  cli upgrade --ref .                 # install the working tree (run from the ngargparser repo)
  cli upgrade --ref /path/to/repo     # same, from anywhere
  cli upgrade --ref . --check         # show what it would install, install nothing
  ```

- **`cli sync`** — refreshes the **framework-owned files inside a project** to match the installed
  version (and self-upgrades first, unless `--no-upgrade`). `cli sync --dry-run` installs nothing, so
  its preview uses the installed version's files; it prints which ngargparser a real run would install
  first, and warns when that would replace your local checkout (use `--no-upgrade` to sync from the
  checkout). Run it from inside an existing project:

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

It never overwrites `validators.py`, `<App>ArgumentParser.py`, `paths.py`, `hooks.sh`, `get_libs.sh`, `build.conf`, or anything else in user-owned space. It also migrates legacy filenames from older framework versions (e.g., `scripts/build.sh` → `scripts/core/build.sh`, `scripts/dependencies.sh` → `scripts/hooks.sh`, `scripts/build.conf` → `scripts/core/build.conf`) on first run.

### Sync safety checks

`cli sync` does not leave a broken project behind. Each run ends in one of four ways:

| Outcome | When | Exit code |
|---|---|---|
| Synced and verified | The project's help screens worked before the sync and still work after | 0 |
| Synced, could not verify | The project did not start even before the sync, has no `.venv` interpreter, or has several `src/run_*.py` | 0 |
| Stopped, nothing changed | The project has code this framework version is known to break | 1 |
| Rolled back | A help screen that worked before fails after, or sync hit an error. Every file is restored | 1 |

How it works:

- **Known clashes.** Before writing anything, sync reads your project's `src/*ArgumentParser.py` for
  code a framework release is known to break, and stops with the file, the line numbers and the fix.
  `cli sync --dry-run` reports the same findings. The list holds one entry today: declaring
  `--output-prefix` or `--output-format` on `predict`, or reusing `-o` or `-f` there, which the base
  class has owned since 0.3.0. A break that is not on the list is left to the test drive.
- **Test drive.** When a framework file is about to change, sync runs your entry script with
  `--help`, `predict --help`, `preprocess --help` and `postprocess --help`, before and after, using
  the project's own `.venv` interpreter. This executes your project's code. It runs only when
  `src/` holds exactly one `run_*.py`; with several, sync cannot tell which is the entry script and
  runs none of them.
- **Undo.** If a screen that worked before fails after, or sync is interrupted or hits an error,
  sync puts back every file it touched, including moved, created and edited ones and files reached
  through a symlink, and prints the error your project raised.

`cli sync --no-verify` skips the known-clash check and the test drive, and says so. Undo on error
still applies. Setting `NGARGPARSER_SYNC_NO_VERIFY=1` has the same effect.

The test drive proves the project still starts. It cannot prove that a full `predict`, `preprocess`
or `postprocess` run still gives the right result, so run your own tests after a sync.

### What sync can't do

For breaking changes — file layouts shifting, schema changes, removed APIs — sync keeps your core files current but won't transform user-edited content. The right move there: read the release notes, scaffold a fresh project with the new framework, and port your application code over.

### Migrating an existing tool to `scripts/get_libs.sh`

The copy-based build (see [Building](#building)) needs these steps once per existing tool:

1. Run `cli sync` to get the new `scripts/core/build.sh`. Sync also renames `scripts/dependencies.sh` or `scripts/build_hooks.sh` to `scripts/hooks.sh`, and moves `scripts/build.conf` to `scripts/core/build.conf`.
2. Copy `scripts/get_libs.sh` from a fresh `cli generate` (or from ngargparser's `templates/get_libs.sh`). Move every clone and download from `scripts/hooks.sh` into it. get_libs.sh runs in its own temp dir, so drop `$BUILD_DIR` from those lines. Copies of tools you downloaded by hand move too (see the template's Example 3). Keep patching steps in hooks.sh; if none are left, delete hooks.sh.
3. If the tool has `scripts/do-not-distribute.txt` and no root `.distignore`, run `git mv scripts/do-not-distribute.txt .distignore`. The syntax is the same.
4. Delete `EXCLUDE_FROM_BUILD_SYMLINK` from `scripts/core/build.conf`.
5. Delete hand-fetched copies in `libs/` that get_libs.sh now fetches; the build refuses to overwrite entries it did not fetch. If `libs/` itself is a symlink, replace it with a real directory.
6. Keep `scripts/get_libs.sh` out of the tarball. The default `.distignore` does this with `*.sh`; a `.distignore` that lists scripts by name needs `/scripts/get_libs.sh`.
7. Remove, or list in `.distignore`, any folder whose symlinks are absolute or point outside the project. The build now fails on them instead of shipping what they point at.
8. Run `uv sync` and `make build`, then compare the tarball contents with the last release: `diff <(tar -tzf old.tar.gz | sort) <(tar -tzf build/<new>.tar.gz | sort)`.
9. Keep `TOOLS_DEPLOY_TOKEN` in CI. A CI checkout starts with an empty `libs/`, so every CI build fetches.

---

## Reference

### CLI cheatsheet

```
cli --version                 # installed version; checks GitLab for a newer release on every run
cli --help                    # usage; same release check on every run

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

`preprocess` reads and checks the whole command line before it creates the folder layout under
`-o`, so a command that fails leaves nothing behind and the order of `-o`, `--params-dir` and
`--inputs-dir` doesn't matter. A `-o` that is an existing file, or a folder that can't be created,
stops with an error. A `--params-dir`/`--inputs-dir` outside `-o` must already exist.

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

<!-- example: self -->
```python
self.parser_preprocess.help = 'Custom preprocess help'
self.parser_preprocess.description = 'Detailed preprocess instructions'
```

#### Argument grouping

<!-- example: self -->
```python
self.parser_predict.add_argument(
    "--threshold",
    help="Score threshold",
    group="prediction options",   # args sharing this label cluster in --help
)
```

#### Updating inherited arguments

Some arguments are provided by the base class — notably `predict`'s `--output-prefix/-o`
and `--output-format/-f`. To tweak one for **your** tool (change its default, help text,
choices, or group), call `update_arguments(...)` — it edits the inherited argument **in
place**:

<!-- example: self -->
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

`update_arguments` changes an option's properties (help, default, choices, required, metavar, dest)
and its help section (`group=`); it can't rename one, so pass the option's existing names or just
its first. `remove_argument("--output-format", "predict")` removes the option under all its names,
so `-f` is free again too. Both raise a clear error for a misspelled option ("did you mean
--output-format?"), a misspelled subcommand, a rename, or an unknown keyword, instead of silently
doing nothing.

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

<!-- example: imports -->
```python
from core.core_validators import (
    validate_file,                     # file exists + readable
    validate_directory,                # directory exists or can be created
    validate_directory_given_filename, # parent dir of a path
    validate_preprocess_dir,           # special preprocessing dir setup
)
```

#### Custom validators

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
        raise argparse.ArgumentTypeError("peptide length must be 1–50")
    return length
```

<!-- example: self -->
```python
# src/MyAppArgumentParser.py
self.parser_predict.add_argument(
    "--peptide-length",
    type=validators.validate_peptide_length,   # the template imports validators
)
```

### File ownership

| Path | Ownership |
|---|---|
| `src/core/*` | Framework — `cli sync` overwrites |
| `scripts/core/*` | Framework — `cli sync` overwrites (except `build.conf`) |
| `Makefile` | Framework — `cli sync` overwrites |
| `pyproject.toml` `[tool.ngargparser] scaffold_version` | Framework — sync stamps |
| `pyproject.toml` (everything else) | You |
| `uv.lock` | You (regenerated by `uv` commands) |
| `paths.py` | You (`cli deps` writes stubs; `./configure --<name>-…` or a hand edit fills in values) |
| `scripts/hooks.sh` | You (optional; `cli generate` does not create it) |
| `scripts/get_libs.sh` | You (created by `cli generate`; sync never creates or overwrites it) |
| `scripts/core/build.conf` | You (the one user-owned file in `scripts/core/`; sync never overwrites it) |
| `.distignore` | You (`.gitignore` syntax) |
| `libs/` | You, except entries fetched by `scripts/get_libs.sh` |
| `libs/.gitignore` | Framework while its first line is the build's marker; yours otherwise (the build then leaves it alone) |
| `deploy/install.sh` | You — sync creates from template if missing, never overwrites |
| `src/run_*.py`, `src/<App>ArgumentParser.py`, `src/preprocess.py`, `src/postprocess.py`, `src/validators.py` | You |
