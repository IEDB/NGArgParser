# ngargparser

[![ngargparser](https://img.shields.io/badge/ngargparser-0.4.1-blue.svg)](https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser)

ngargparser is a framework for IEDB command-line tools that run on HPC (high-performance computing) clusters. It gives you a `cli` command that generates and updates tools, and a base class, `NGArgumentParser`, that gives every tool the same `preprocess`, `predict` and `postprocess` commands.

In this README, a *tool* is what `cli generate` creates, and the *tool folder* is its top folder. The [parser cookbook](docs/parser-cookbook.md) has recipes for the parser and the predict code. The [reference](docs/reference.md) lists the API, the settings, the messages and a [Glossary](docs/reference.md#glossary), and the [internals](docs/internals.md) show how the build, `./configure` and `cli sync` work.

## Prerequisites

| You need | For | Check with |
|---|---|---|
| [uv](https://github.com/astral-sh/uv) | Installing ngargparser, Python 3.11 and each tool's `.venv/` | `uv --version` |
| git | Installing from GitLab, and `make build` | `git --version` |
| make | `make build` and `make clean` | `make --version` |
| SSH access to gitlab.lji.org | The SSH install, and SSH clones in `scripts/get_libs.sh` | `ssh -T git@gitlab.lji.org` |

Install uv first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Install

Install the current release with `uv tool install`, which puts `cli` on your PATH:

```bash
# A release tag, over SSH (needs your SSH key on GitLab):
uv tool install 'git+ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.4.1'

# The same release over HTTPS (needs a GitLab access token):
uv tool install 'git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@v0.4.1'
```

`cli --version` prints `cli 0.4.1`, then checks GitLab for a newer release ([Update notice](docs/reference.md#update-notice)). An SSH install that fails with `Permission denied (publickey...)` needs your key on GitLab ([SSH install fails](docs/reference.md#ssh-install-fails)).

## Quickstart

These steps create a tool named `my-app`, give it a working `predict`, and build its tarball. Start in a folder outside any other tool.

1. Generate the tool and install its packages:

   ```bash
   cli generate my-app
   cd my-app
   uv sync
   ```

2. Write the predict logic. In `src/run_my_app.py`, the `predict` branch prints `predict is not implemented yet: edit src/run_my_app.py` and exits 1. Replace the whole branch, its `sys.exit(...)` line included, with this code:

   ```python
       if args.subcommand == 'predict':
           import json
           from core.result_writer import write_results

           if args.input_json:
               with open(args.input_json) as f:
                   peptides = json.load(f)["peptide"]
           else:
               with open(args.input_tsv) as f:
                   peptides = [line.split("\t")[0] for line in f.read().splitlines()[1:]]
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

   Exactly one of `args.input_json` (`-j`) and `args.input_tsv` (`-t`) is set. `write_results` prints the table as TSV, or writes a file for `-o` ([Write the results](docs/parser-cookbook.md#write-the-results)).

3. Create your inputs outside the tool folder, because `make build` ships the files in it, such as `src/example.json`, the sample input `cli generate` wrote. Then run `predict`, with the tool's `.venv`:

   ```bash
   mkdir -p ../my-app-runs
   echo '{"peptide": ["ADMGHLKY", "ELDDTLKY", "FMDHVLRYP"]}' > ../my-app-runs/input.json
   printf 'peptide\nADMGHLKY\nELDDTLKY\nFMDHVLRYP\n' > ../my-app-runs/input.tsv
   uv run python src/run_my_app.py predict -j ../my-app-runs/input.json
   ```

   Output:

   ```
   peptide	length
   ADMGHLKY	8
   ELDDTLKY	8
   FMDHVLRYP	9
   ```

   `-t ../my-app-runs/input.tsv` prints the same table. `-o ../my-app-runs/result` writes `../my-app-runs/result.tsv`, creating missing folders, and prints `Wrote ...` on stderr; `-f json` writes JSON. Since `-o` takes a prefix, a value that ends in `.tsv` or `.json`, names a folder or is empty stops with exit code 2 and a hint ([predict arguments](docs/reference.md#built-in-predict-arguments)).

4. Build the tarball:

   ```bash
   make build
   ```

   The build copies the tool into `build/ng_my-app-local/`, checks that it starts there with the packages a deploy installs, and packs `build/IEDB_NG_MY-APP-local.tar.gz`. Until you fill in the template text, it prints a `Notice:` line for `README` and one for the description in `pyproject.toml`.

### Try the sample app

`cli generate example` creates `aa-counter/`, a finished tool whose `README.md` walks through all three commands. Run it outside `my-app/`:

```bash
cd ..
cli generate example
cd aa-counter
uv sync
uv run python src/run_aa_counter.py predict -j src/example.json
```

## Files you edit

| File | What goes in it |
|---|---|
| `src/run_my_app.py` | The entry script, with your predict logic in its `predict` branch |
| `src/MyAppArgumentParser.py` | The tool description and the `predict` options |
| `src/preprocess.py`, `src/postprocess.py` | `run()`: split the input into job units; combine their results |
| `src/validators.py`, `tests/test_parser.py` | Your argument checks; tests of your parser |
| `pyproject.toml` | Python packages; `entry` under `[tool.ngargparser]` names the entry script |
| `README`, `README.md` | The end-user README, which ships in the tarball; notes for the tool's developers, which do not |
| `paths.py` | Where other tools live on each host; `cli deps add` creates it |
| `scripts/get_libs.sh` | Outside code that `make libs` and `make build` fetch into `libs/` |
| `scripts/hooks.sh` | Optional: steps the build runs on its copy of the tool ([Build hook](docs/reference.md#build-hook)) |
| `scripts/core/build.conf` | Build settings: `TOOL_NAME`, `TOOL_VERSION` and `TARBALL_PREFIX` ([Build settings](docs/reference.md#build-settings)); the one file in `scripts/core/` that `cli sync` never overwrites |
| `.distignore` | Files to keep out of the tarball |
| `deploy/install.sh` | The script a deploy runs on the host, where you can add `./configure --<dep>-path=...`; `cli sync` creates it only when it is missing |
| `.gitlab-ci.yml` | The CI jobs: the tests, then `make build` |

Leave `src/core/`, `scripts/core/build.sh`, `Makefile` and `configure` alone: `cli sync` overwrites them ([File ownership](docs/reference.md#file-ownership)).

## Workflow: preprocess, predict, postprocess

A tool splits a large job into *job units*, so a cluster can run `predict` on them in parallel:

```
input file ──► preprocess ──► job_descriptions.json, plus one parameter file per job unit
                                   │
                                   ▼
                    predict, once per job unit (in parallel)
                                   │
                                   ▼
                    postprocess ──► one combined result
```

`job_descriptions.json` lists one job per job unit, then the `postprocess` job ([fields](docs/reference.md#job-description-fields)). Before it calls your `run()`, `preprocess` creates `predict-inputs/`, `predict-outputs/`, `aggregate/` and `results/` under `-o` ([layout](docs/reference.md#preprocess-layout)).

`run()` gets every argument as a keyword, such as `output_dir` ([arguments](docs/reference.md#built-in-preprocess-arguments)). Until you replace its `sys.exit(...)` line, it prints `preprocess is not implemented yet: edit src/preprocess.py` and exits 1, as `postprocess` does.

## Add arguments and predict logic

The [parser cookbook](docs/parser-cookbook.md) has a recipe for each change, with the error you are most likely to hit:

| To | Recipe |
|---|---|
| See what each generated line does | [Read the generated parser](docs/parser-cookbook.md#read-the-generated-parser) |
| Add an option to `predict` | [Add an option](docs/parser-cookbook.md#add-an-option) |
| Check a value before predict runs | [Check a value with a validator](docs/parser-cookbook.md#check-a-value-with-a-validator) |
| Change the default of `-f`, or replace `-o` or `-f` | [Change or remove a framework option](docs/parser-cookbook.md#change-or-remove-a-framework-option) |
| List an option with the inputs, or in a section of its own | [Group options in the help](docs/parser-cookbook.md#group-options-in-the-help) |
| Write the result as TSV or JSON | [Write the results](docs/parser-cookbook.md#write-the-results) |
| Test the parser with `uv run pytest` | [Test the parser](docs/parser-cookbook.md#test-the-parser) |
| Test what `predict` prints | [Test the predict branch](docs/parser-cookbook.md#test-the-predict-branch) |
| Run another tool and keep its result | [Call a dependency](docs/parser-cookbook.md#call-a-dependency) |
| Add a subcommand of your own, such as `list-alleles` | [Add a subcommand](docs/parser-cookbook.md#add-a-subcommand) |

## Dependencies

### Python packages

```bash
uv add pandas      # add a package; updates pyproject.toml and uv.lock
uv remove pandas   # remove it
uv sync            # install the locked packages into .venv/
```

Commit `uv.lock`, so every deploy installs the same versions. When `uv.lock` no longer matches `pyproject.toml`, `make build` stops and tells you to run `uv lock` ([Lock check](docs/internals.md#lock-check)).

### Other tools: `cli deps` and `./configure`

A *dependency* is another tool, or any program, that your tool runs. Declare it, then set its path on each host:

```bash
cli deps add pepx                          # writes a stub block to paths.py
./configure --pepx-path=/opt/tools/pepx    # sets the path, then writes .env and setup_pepx_env.sh
```

The stub block holds four settings, all `None`; only `pepx_path` is required. A program that is not an ngargparser tool also needs `--pepx-entry=<path>`, its entry script, as an absolute path or one relative to `pepx_path`. `cli deps list` shows the dependencies, and `cli deps remove pepx` drops one. Keep your settings in `paths.py`: `./configure` rewrites `.env` from it each time ([flags and .env](docs/reference.md#configure-flags-and-env)).

### Code in `libs/`: `scripts/get_libs.sh`

`scripts/get_libs.sh` downloads the code and data your tool reads from `libs/`. To clone a GitLab repository, uncomment the `vendor` function in its Example 1, then call it:

```bash
# scripts/get_libs.sh runs in an empty temp dir; each top-level entry it leaves lands in libs/
vendor 'v0.5.4' "iedb/tools/tools-redesign/global-dependencies/allele-validator"
```

`vendor` clones with the `TOOLS_DEPLOY_TOKEN` CI/CD variable when it is set, and with your SSH key otherwise. The CI image has no SSH key, so a CI job without the token stops. Write into the current folder, never into `libs/`. Then run `make libs`, so the tool can import that code when you run it from its folder. A deploy installs only the packages in `pyproject.toml`, so add each package the fetched code imports with `uv add`. A build fetches the same way ([get_libs internals](docs/internals.md#get_libs-internals)).

## Call another tool

Once `./configure` knows a dependency's path, your tool can run it. From Python, `core.app_env.dependency_command("pepx", ...)` returns the command and its environment for `subprocess` ([Call a dependency](docs/parser-cookbook.md#call-a-dependency)). In a shell, source the setup script `./configure` wrote, then call its `run_<dep>` function:

```bash
. ./setup_pepx_env.sh
run_pepx predict -j job.json -o out
```

`run_pepx` runs pepx's entry script, with pepx's Python for a Python script, and returns pepx's exit code ([Run a dependency from a shell](docs/reference.md#run-a-dependency-from-a-shell)).

## Build and run

```bash
make build                      # build the tarball; shows a progress bar
make build-verbose              # the same, printing each step
make libs                       # fetch libs/ only, as a build does
make clean                      # remove build/, unless a build runs; libs/ stays
TOOL_VERSION=1.0.0 make build   # build version 1.0.0 instead of "local"
```

The build copy is `build/ng_<name>-<version>/`, and the tarball is `build/IEDB_NG_<NAME>-<version>.tar.gz`, with `<NAME>` in capitals. Run and debug in the tool folder, with `uv run python src/run_my_app.py ...`; the files in `build/` are read-only. [Build pipeline](docs/internals.md#build-pipeline) lists every step and check.

### What ships in the tarball

Every file in the tool folder ships, `libs/` included, except hidden files (`.env` among them), `build/`, `.venv/` and what `.distignore` excludes. The default `.distignore` keeps out the top-level `Makefile`, `README.md`, `scripts/` and `tests/`, among its 8 rules. `README` and `deploy/install.sh` always ship.

Each build lists what `.distignore` excluded ([rules](docs/reference.md#distignore-rules)), and stops when a file a deploy needs is missing or excluded ([Files a deploy needs](docs/reference.md#files-a-deploy-needs)).

### Build in CI

`cli generate` writes a `.gitlab-ci.yml`: its `test` job runs the tests, then its `build` job runs `make build` with `TOOL_VERSION` set to the branch or tag name. The orchestrator downloads the tarball from the `build` job, so keep its name ([Build in CI](docs/reference.md#build-in-ci)).

## Deploy

[nxg-tools-deployments](https://gitlab.lji.org/iedb/tools/tools-redesign/nxg-tools-deployments), the IEDB deploy orchestrator, takes the tarball from the CI job. On the target host, it extracts the tarball and runs `deploy/install.sh`, which runs `uv sync --frozen --no-dev` and `./configure`. Run a deployed copy with `.venv/bin/python src/run_my_app.py ...`: `uv run` would install the `dev` group the deploy left out, and fails in a read-only folder.

Set each host's dependency paths with `./configure --<dep>-path=...`, by hand or in `deploy/install.sh`. A tarball you extract by hand needs `bash deploy/install.sh` before anything under `src/` runs ([Deploy](docs/reference.md#deploy)).

## Upgrade

`cli upgrade` updates the installed ngargparser. `cli sync`, run in a tool folder, brings that tool's framework files up to it:

```bash
cli upgrade --check     # is a newer release available? installs nothing
cli upgrade             # install the latest release tag
cd path/to/your-tool
cli sync --dry-run      # show what would change; write nothing
cli sync
```

The [CLI cheatsheet](docs/reference.md#cli-cheatsheet) lists every option and alias, such as `cli sync --no-upgrade` and `cli upgrade --ref <version>`.

`cli sync` upgrades ngargparser first. It then copies each framework file that differs to `.ngargparser/sync-backups/<time>/`, overwrites it, and warns when you had edited it. When a help screen of your tool that worked before fails after, it restores every file. Other files you own, such as `README`, stay as they are ([cli sync](docs/reference.md#cli-sync)).

`cli upgrade` and `cli sync` look up and install releases over HTTPS. Without HTTPS access to GitLab, `cli upgrade` stops with `⚠ Couldn't reach the remote ...` and exit code 2, and `cli sync` syncs from the installed version. To upgrade over SSH, set `NGARGPARSER_UPGRADE_URL` to the SSH URL of a release tag for that one command:

```bash
NGARGPARSER_UPGRADE_URL='git+ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git@vX.Y.Z' cli upgrade
```

## Troubleshooting

The reference's [Troubleshooting](docs/reference.md#troubleshooting) has more, such as an invalid tool name or a framework option you reused.

### Not configured yet

A fresh clone of a tool stops with:

```
This tool has not been configured yet: '.env' is missing or incomplete.
Run './configure' in the tool folder (/path/to/tool), then re-run this command.
```

`.env` holds one machine's paths, so git leaves it out. Create it with `uv sync && ./configure`. `uv run pytest` runs `./configure` itself when `.env` is missing ([Test the parser](docs/parser-cookbook.md#test-the-parser)).

### Configured in another folder

```
This tool was configured in /home/me/my-app but is running from /home/me/my-app-copy.
Run './configure' in /home/me/my-app-copy, then re-run this command.
```

Run `./configure` in the new folder. When a middle line says the `.venv` was made for the old folder, run the command it names first.

### Tool needs its virtualenv

```
This tool needs its virtualenv: run it with 'uv run python src/run_my_app.py ...' (or activate .venv first). Run 'uv sync' if .venv is missing.
```

You ran the entry script with another Python, such as `/usr/bin/python3`. Use `uv run python`. A build or deployed copy names `.venv/bin/python` and `bash deploy/install.sh` instead.

### A dependency path is None

```
✗ pepx_path is None in paths.py. Set it with './configure --pepx-path=<path>', or run 'cli deps remove pepx' to drop this dependency.
  Nothing was changed.
```

Run one of the two commands the message names. With several such paths, one message names them all, with one combined `./configure` command.
