{TOOL_NAME} developer note
==========================
This note is for whoever builds {TOOL_NAME}. `README` is the end-user README:
it ships in the tarball, and `make build` replaces `TOOL_VERSION` in it with
the version. Fill in its `<...>` placeholders before the first release.

## Files you edit

| File | What goes in it |
|---|---|
| `src/run_{TOOL_EXEC_NAME}.py` | Your prediction code, in the `predict` branch of `main()`. The branch is `pass` until you fill it in. |
| `src/{TOOL_NAME_CAP}ArgumentParser.py` | The tool description and the `predict` options. |
| `src/preprocess.py` | `run()`: split one input into jobs and write `job_descriptions.json` into the `-o` folder. |
| `src/postprocess.py` | `run()`: merge the job results into one output. |
| `src/validators.py` | Your own argument checks, used as `type=` in the parser. |
| `paths.py` | The external tools {TOOL_NAME} runs. `cli deps add <tool>` creates it, and `./configure` reads it. |
| `scripts/get_libs.sh` | Code and data that `make build` fetches into `libs/`. |
| `.distignore` | Files to leave out of the tarball. |

Leave `src/core/`, `configure`, `scripts/core/build.sh`, and `Makefile` as
they are: `cli sync` overwrites them.

For a finished tool to copy from, run `cli generate example` outside this
project. It writes the sample tool `aa-counter/`, whose `preprocess.py` writes
`job_descriptions.json`.

## Commands to run

Run these from the project folder:

<!-- run -->
```bash
uv sync
uv run python src/run_{TOOL_EXEC_NAME}.py predict -h
echo '{}' > ../{TOOL_NAME}-input.json
uv run python src/run_{TOOL_EXEC_NAME}.py predict -j ../{TOOL_NAME}-input.json
make build
```

`predict -j` exits 0 and prints nothing until the `predict` branch has code.
In that branch, `write_results(result, args.output_prefix, args.output_format)`
from `core.result_writer` writes the result: TSV by default, and to the screen
when there is no `-o`. The comment in the branch shows the shape of `result`.

`make build` writes `build/IEDB_NG_<NAME>-local.tar.gz`, with the project
name in capitals, and `TOOL_VERSION=1.0 make build` sets the version. Every
file in the project folder ships in the tarball, README.md included, except
hidden files and folders, `build/`, `.git/`, and what `.distignore` excludes.
`README` and `deploy/install.sh` always ship. Keep test inputs and outputs
outside the project folder.

## More detail

The ngargparser README covers dependencies, builds, and `cli sync`:
https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser
