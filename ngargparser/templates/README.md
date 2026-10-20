{TOOL_NAME} developer note
==========================
This note is for whoever builds {TOOL_NAME}. `README` is the end-user README:
it ships in the tarball, and `make build` replaces `TOOL_VERSION` in it with
the version. Fill in its `<...>` placeholders before the first release.

## Files you edit

| File | What goes in it |
|---|---|
| `src/run_{TOOL_EXEC_NAME}.py` | Your prediction code, in the `predict` branch of `main()`. Replace the whole branch, its `sys.exit(...)` line included. |
| `src/{TOOL_NAME_CAP}ArgumentParser.py` | The tool description and the `predict` options. |
| `src/preprocess.py` | `run()`: split one input into jobs and write `job_descriptions.json` into the `-o` folder. |
| `src/postprocess.py` | `run()`: merge the job results into one output. |
| `src/validators.py` | Your own argument checks, used as `type=` in the parser. |
| `tests/test_parser.py` | Tests of the parser: `uv run pytest` runs them. In a fresh clone, `tests/conftest.py` runs `./configure` first. `tests/` does not ship. |
| `pyproject.toml` | Python dependencies (`uv add`), and `entry` under `[tool.ngargparser]`: the entry script that tools calling {TOOL_NAME} run. |
| `paths.py` | The external tools {TOOL_NAME} runs. `cli deps add <tool>` creates it, and `./configure` reads it. |
| `scripts/get_libs.sh` | Code and data that `make build` fetches into `libs/`. After you add an entry, run `make libs`, so a run from the tool folder can import it. |
| `.distignore` | Files to leave out of the tarball. |
| `.gitlab-ci.yml` | The GitLab CI jobs: `test` runs the tests, then `build` builds the tarball. `cli sync` never changes it. |

Leave `src/core/`, `configure`, `scripts/core/build.sh`, and `Makefile` as
they are: `cli sync` overwrites them.

For a finished tool to copy from, run `cli generate example` outside this
tool folder. It writes the sample app `aa-counter/`, whose `preprocess.py` writes
`job_descriptions.json`.

## Commands to run

Run these in the tool folder:

<!-- run -->
```bash
uv sync
uv run python src/run_{TOOL_EXEC_NAME}.py predict -h
echo '{}' > ../{TOOL_NAME}-input.json
uv run python src/run_{TOOL_EXEC_NAME}.py predict -j ../{TOOL_NAME}-input.json
make build
```

Until the `predict` branch has code, `predict -j` prints `predict is not
implemented yet: edit src/run_{TOOL_EXEC_NAME}.py` and exits 1.
In that branch, `write_results(result, args.output_prefix, args.output_format)`
from `core.result_writer` writes the result: TSV by default, and to the screen
when there is no `-o`. The comment in the branch shows the shape of `result`.

| Command | What it does |
|---|---|
| `make build` | Builds `build/IEDB_NG_<NAME>-local.tar.gz`, with the tool name in capitals; `TOOL_VERSION=1.0 make build` sets the version |
| `make build-verbose` | The same, printing each step |
| `make libs` | Fetches `libs/` with `scripts/get_libs.sh`, as a build does, and nothing else |
| `make clean` | Removes `build/`; `libs/` stays |

Every file in the tool folder ships in the tarball, except hidden files and
folders, `build/`, and what `.distignore` excludes. The default `.distignore`
keeps out the top-level `Makefile`, `scripts/`, `tests/` and this
`README.md`, and each build lists what it excluded. `README` and
`deploy/install.sh` always ship. Keep test inputs and outputs outside the
tool folder.

## More detail

The ngargparser README covers dependencies, builds, and `cli sync`:
https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser
