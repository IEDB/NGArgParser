# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `docs/internals.md`: the build pipeline, its preflight stops, the build lock, the lock check, the
  startup check, the `libs/` stamp, the order of `./configure`'s writes, the sync's lock, safety
  checks, test drive and recovery, signals, and the version stamp and order. `docs/reference.md`
  keeps the API and the settings a tool uses: the parser API, the envelope checks,
  `dependency_command`, the `./configure` flags and `.env` keys, the build settings and the `cli`
  commands. The cookbook gains "Test the predict branch", a test that sets `sys.argv`, calls the
  entry script's `main()` and reads `capsys`.
- `make build` stops a tool whose package does not install from the build copy, as
  `deploy/install.sh` would fail on the deploy host: "ERROR: The tool does not install from the
  build copy, so deploy/install.sh would fail on the deploy host too ('uv sync --frozen --no-dev'
  exit 1):", then uv's lines and "Building the tool's package needs every file pyproject.toml
  names, such as its readme and its packages: check that .distignore and scripts/hooks.sh leave
  them in the build copy." This happens when `.distignore` leaves out a package folder, or the
  readme a backend such as hatchling needs.
- `make build` stops on a `uv.lock` that is TOML but that uv rejects, such as a version changed by
  hand without its wheels: "ERROR: uv.lock does not parse (uv rejects what it holds: see below),
  so 'uv lock' cannot read it either: ...", then up to 8 of uv's lines.
- `make build` stops while a `cli sync` did not finish in the tool folder, since the framework
  files may be half updated: "ERROR: a cli sync did not finish here
  (.ngargparser/sync-in-progress/<run>/): run cli sync to see what to do, then build again."
- `make build` stops a job that `scripts/get_libs.sh` or `scripts/hooks.sh` started with `&` and
  left running, TERM first and KILL after 5 seconds: "Notice: scripts/get_libs.sh left background
  jobs running; the build stopped them." Such a job went on writing into `libs/` or the build copy.
- `NGARGPARSER_SYNC_HELP_TIMEOUT` sets the seconds each help screen of `cli sync`'s test drive may
  take (default 60). The first screen that times out ends that run of the screens: "└ ⚠
  src/run_my_app.py --help timed out after 2s; the other 3 help screen(s) were not run. To allow
  more time, set NGARGPARSER_SYNC_HELP_TIMEOUT to the seconds each help screen may take." A value
  that is not a number above 0 is named once. Each run of the screens is announced: "Checking that
  the tool starts (4 help screens) before the update..." and "... after the update...".
- `cli sync` creates a missing `src/core/__init__.py`, empty, and never changes one that is there.
- `cli sync` stops with exit 1 and nothing changed when it cannot read a file it may change: "✗
  Cannot read src/core/result_writer.py (Permission denied): sync reads every file it may change,
  ... Make it readable, then run cli sync again:" and "chmod u+r src/core/result_writer.py" (or
  the `chmod a+r` its owner can run, for a file that is not yours).
- `cli sync` and `cli deps` in a folder under a build or deployed copy say what the folder is, and
  where the source is: "✗ This folder is inside <copy>, a build or deployed copy of a tool (it has
  .ngargparser-build), not its source. Nothing was changed. Run cli sync in the tool's source
  folder: cd <tool>, then build again."
- `cli deps add` refuses `node`, `gem`, `lua`, `git-exec`, `pip`, `virtualenv`, `pyspark` and
  `pyspark-driver`, whose settings would include `NODE_PATH`, `GEM_PATH`, `LUA_PATH`,
  `GIT_EXEC_PATH`, `PIP_PYTHON`, `VIRTUALENV_PYTHON`, `PYSPARK_PYTHON` or `PYSPARK_DRIVER_PYTHON`:
  "... Node.js reads NODE_PATH as its own setting, so it could change how Node.js runs in the tool.
  Pick another name."
- `cli deps remove` warns about a line it leaves that still reads a removed variable, which would
  stop `./configure` with a `NameError`: "⚠  'paths.py' line 17 still uses pepx_path, which this
  removal deleted: delete or fix that line, or ./configure will fail."
- A new tool's `.gitattributes` keeps `src/core/*.py` and `Makefile` at LF line endings too, so a
  checkout holds the framework's own bytes.
- The generated parser's notes at the top of `__init__` link the parser cookbook.
- `docs/parser-cookbook.md`: task recipes for the parser and the predict code (the generated
  parser, an option, a validator, changing or removing a framework option, help groups, writing
  results, testing the parser, calling a dependency), each with the error you are most likely to
  hit. The README's parser links point at it, and `docs/reference.md` keeps the message tables.
  `tests/test_readme_examples.py` runs its marked examples too.
- `make build` prints a notice, without stopping, while `README` holds a template placeholder or
  the `pyproject.toml` description still starts with TODO: "Notice: README still has template
  placeholders (<Describe ...>): fill them in before you deploy." and "Notice: the description in
  pyproject.toml still starts with TODO: fill it in before you deploy."
- `cli sync` keeps the files it may change in `.ngargparser/sync-in-progress/<UTC time>Z-<pid>/`,
  with a marker beside it, until it ends. After a sync that was killed (`kill -9`, a power cut) or
  whose undo did not finish, the next sync, dry runs included, stops with exit 1: "✗ A previous
  cli sync (pid N, started ...) did not finish, and may have left framework files half updated.
  The files from before it are in .ngargparser/sync-in-progress/<run>/. Copy them back (or run git
  checkout -- <the files that differ> if they are committed), then delete these files the sync
  created: <files>. Then delete that folder and run cli sync again. From the tool folder:", then the
  `cp -a`, `rm` and `rm -r` commands that do it. The marker lists each file the sync created
  before it writes it. It used to keep that copy in the system temp folder.
- `add_predict_subparser` passes the other keywords argparse's `add_parser` takes (`epilog=`,
  `usage=`, `prog=`, ...) on to it; Python used to say only "got an unexpected keyword argument".
  It refuses `aliases=` and `conflict_handler=` with a `ParserKeywordError` that says why, and a
  misspelled keyword gets a did-you-mean ("add_predict_subparser() got an unexpected keyword
  argument 'epilgo'; did you mean epilog=?").
- `core.app_env.dependency_command(name, *args)` returns `(argv, env)` for `subprocess`: the
  dependency's Python (`<dep>_python` from `paths.py`, else its virtualenv's, else the first
  `python3` on a `PATH` without any virtualenv's `bin/`, with a warning: "<name> publishes no
  virtualenv; running it with <python3>"), its entry script and `args`; an entry script that is not
  a Python script runs by itself (see the non-Python entry below). `env` is set up as activating
  that virtualenv does (`VIRTUAL_ENV`, its `bin/` first on `PATH`, no `PYTHONHOME`), with
  `<dep>_lib_path` in front of `LD_LIBRARY_PATH`, and without the calling tool's `APP_ROOT`,
  `APP_NAME`, `APP_VENV`, `APP_ENTRY` and `APP_KEYS_FROM_DEPS`. It loads no environment module: for
  a `<dep>_module` that `LOADEDMODULES` does not list it warns ("<name> needs the environment module
  <module>, which dependency_command cannot load: ..."). It raises `RuntimeError` for a name that is
  not a dependency ("<name> is not a dependency of this tool (declared: ...): check the name (did
  you mean ...?), or add it with cli deps add <name>"), for one `./configure` has not seen ("run
  ./configure: paths.py declares <name>, but .env has no values for it yet"), and for one that
  publishes no entry script ("<name> publishes no entry script: <path>/.env does not exist. ..." or
  "... (APP_ENTRY in <path>/.env, or [tool.ngargparser] entry): set <name>_entry in paths.py, then
  run ./configure").
- `cli generate` writes `tests/test_parser.py`, two parser tests, so `uv sync && uv run pytest`
  passes in a new tool; `tests/` does not ship. The starter test's input uses the Quickstart's
  `peptide` key (it said `peptides`). The generated `pyproject.toml` sets `testpaths = ["tests"]`,
  so the tests of code fetched into `libs/` stay out of `uv run pytest`.
- `cli generate` writes `.gitattributes`, which keeps `*.sh`, `configure` and `build.conf` at LF line
  endings (`text eol=lf`). It is the tool's own file; `cli sync` never touches it.
- An unrecognized option that belongs to the top-level command or to another subcommand gets a hint:
  "--verbose is an option of the top-level command; put it before 'predict'", "-f is an option of
  predict and postprocess, not preprocess". A misspelled option gets the closest option of its
  subcommand or of the top-level command, compared without the dashes: "unrecognized arguments:
  --min-lenght 3; did you mean --min-length?", "did you mean --verbose before 'predict'?". Options
  hidden from `-h` are not offered, and a word close to none gets no hint.
- `ParserConflictError` in `core.NGArgumentParser`, a `ParserSetupError` and an
  `argparse.ArgumentError`. A second `add_predict_subparser()` stops with one file:line line and
  exit 2 ("add_predict_subparser() was called a second time: predict already exists; ..."), as do
  two of the tool's own options that share a flag; both were argparse tracebacks.
- More preflight stops, before anything is removed: a `pyproject.toml` that does not parse
  ("pyproject.toml does not parse (a merge conflict marker at line N): fix it, then build again";
  for another parse error, uv decides when it is on the PATH); a `TARBALL_PREFIX` with characters
  other than letters, digits, `.`, `_`, `+` and `-` ("TARBALL_PREFIX '../x' is not allowed: ...");
  and Windows line endings in `scripts/core/build.conf`, `scripts/get_libs.sh`,
  `scripts/hooks.sh`, `deploy/install.sh` or `configure`, with the `sed` command that fixes them.
- A tool without `.distignore` builds with the default rules and says so: "Notice: no .distignore;
  using the default rules (see cli generate's .distignore)."
- ngargparser's CI pins uv (`UV_VERSION` 0.11.14), installs with `uv sync --locked`, and runs a
  `pytest-real-uv` job on `python:3.11-slim`: the tests named `real_uv`, with
  `NGARGPARSER_REAL_UV=1`. A skipped test fails that job ("ERROR: a real_uv test was skipped; this
  job must run them all.").
- One build runs at a time in a tool folder. A second `make build` stops at once, before anything is
  removed: "ERROR: Another build or a cli sync is running in this folder (pid N). Wait for it to
  finish." The lock is held with `flock` and ends with the build; without `flock` (macOS) it is a
  folder holding the build's pid, taken over once that process is gone. A folder lock that holds no
  pid stops the build with "... may be running in this folder (its pid is not known). Wait for it to
  finish, or remove .ngargparser/build.lock if none is running.", and one left by a build that is
  gone, which this build cannot remove, with "A build or a cli sync that is no longer running (pid
  N) left .ngargparser/build.lock, and this build could not remove it. ...". `cli sync` takes the
  same lock.
- More preflight stops, all before anything is removed: a `configure` without its exec bit
  ("configure is not executable: run 'chmod +x configure' and commit the mode (git
  update-index --chmod=+x configure)"); an `entry` that names a missing file or a folder, or, with
  no `entry`, an only `src/run_*.py` that `.distignore` excludes; a `uv.lock` that does not parse
  ("uv.lock does not parse (a merge conflict marker at line 3), so 'uv lock' cannot read it either:
  restore it (after a merge: git checkout --theirs uv.lock) or delete it, then run 'uv lock' and
  commit uv.lock"); and a `scripts/core/build.conf` that exits or runs a failing command
  ("scripts/core/build.conf exited (code N) while being read: it may only set variables."). An
  `exit 0` in `build.conf` used to end the build with exit 0 and no tarball.
- A fetched `libs/` entry edited by hand, with a file added, deleted or changed at any depth, is
  fetched again: "libs/<name> changed since it was fetched; fetching again". The stamp in
  `.ngargparser/libs-stamp` is format 3, with a fingerprint per entry: each path in it, and each
  file's size and modification time. A `chmod`, or a copy that keeps modification times (`cp -a`,
  `rsync -a`), is no change; an edit that keeps a file's size and modification time is not seen. A
  format 1 or 2 stamp is read and rewritten.
- `NGARGPARSER_BUILD_TRACE=1` prints the shell trace (`set -x`) of `make build` and
  `make build-verbose`.
- Parser setup mistakes raise `ParserSetupError` from `core.NGArgumentParser`, a `ValueError`
  whose message starts with the file and line of the call (`ParserNotReadyError` is also an
  `AttributeError`, `ParserKeywordError` also a `TypeError`). `update_arguments`,
  `remove_argument`, `set_subcommand_order` and an early `self.parser_predict` raise it. The
  generated entry script catches it, prints that one line and exits 2, as for a framework option
  clash. Tools generated earlier keep the traceback until their entry script catches
  `ParserSetupError`.
- A parser that skips `super().__init__()` stops with "src/MyAppArgumentParser.py:29:
  MyAppArgumentParser has not run NGArgumentParser.__init__: call super().__init__() at the start
  of MyAppArgumentParser.__init__" instead of "'MyAppArgumentParser' object has no attribute
  'subparser'".
- `cli deps add` prints the `./configure` flag that sets each new path ("Set its path:
  ./configure --<dep>-path=/path/to/<dep>"), then "Or edit 'paths.py' by hand."; it printed
  "Edit 'paths.py' to fill in the actual paths.".
- ngargparser's own CI runs shellcheck, from `shellcheck-py` pinned by `SHELLCHECK_PY_VERSION`
  (0.11.0.1), on `templates/build.sh`, `deploy/install.sh`, `get_libs.sh`, `hooks.sh` and
  `configure`, and ruff's F rules over the templates, which the main ruff check leaves out. mypy checks 7 files for Python 3.9 (`cli.py`, `core_validators.py`,
  `result_writer.py`, `app_env.py`, `NGArgumentParser.py`, `NGChildArgumentParser.py`,
  `templates/configure.py`) with `check_untyped_defs`, and the pre-commit mypy hook checks the same
  files.
- The cookbook's "Call a dependency" shows the Python call with `core.app_env.dependency_command`,
  passing `-j` or `-t` as the tool's own input was given, and stopping with the dependency's last
  error line when it fails ("pepx failed (exit code 1): KeyError: 'peptide'"). The README's earlier
  example passed `-j None` for a TSV input, which failed with `CalledProcessError`.
- `cli generate` writes a starter `.gitlab-ci.yml` on `python:3.11-slim`, with the uv that
  `UV_VERSION` pins (0.11.14). A `test` stage runs `uv sync --locked`, `./configure` and
  `uv run pytest`; then the `build` job sets `TOOL_VERSION` from `CI_COMMIT_REF_NAME`, runs
  `uv sync --locked` then `make build`, and keeps `build/*.tar.gz`. A comment says what to do when
  `./configure` stops on a dependency path left `None` in `paths.py`. The file is the tool's own; `cli sync` never creates or
  changes it. `cli generate` also prints `ℹ Generated with ngargparser X.`, then the next steps the
  README's Quickstart takes (`cd`, `uv sync`, where the predict logic goes, a `predict` run,
  `make build`), and `cli generate -h` names `example`.
- `make build` checks the lock first, before it changes anything. It compares the dependency lists
  in `pyproject.toml` (`dependencies`, `optional-dependencies`, `dependency-groups`) with `uv.lock`,
  which needs no network, no uv cache and no uv, then runs `uv lock --check --offline`. A stale lock
  stops the build with "uv.lock is out of date with pyproject.toml: run 'uv lock' and commit
  uv.lock" and one line per difference ("added to dependencies: numpy", "removed from ...",
  "changed in ... (uv.lock has ...)"). When uv cannot run but the lists match, the build prints
  "Lock check partly skipped: ..." and goes on; when neither check can tell, "Lock check skipped:
  ...".
- `make build` stops before it changes anything when the tool lacks a file a deploy needs (`README`,
  `deploy/install.sh`, `pyproject.toml`, an executable `configure`, `paths.py` when the tool has
  one, which `./configure` on the deploy host reads, and each framework file in `src/core/`:
  `NGArgumentParser.py`, `core_validators.py`, `result_writer.py`, `app_env.py`,
  `set_pythonpath.py`, `configure.py` and `__init__.py`), or when `.distignore` excludes
  `pyproject.toml`, `uv.lock`, `configure`, `paths.py` or one of those files: "The tool folder lacks
  files a deploy needs, so nothing was built:" or ".distignore excludes files a deploy needs, so
  nothing was built. Remove the rules that exclude them:", with the reason for each path. The build
  checks again after the copy and after `scripts/hooks.sh`, so a hook that deletes one fails the
  build too.
- Each build prints `Excluded by .distignore: <n> paths` and lists up to 20 of them;
  `make build-verbose` lists all.
- `[tool.ngargparser] entry` in `pyproject.toml` names a tool's entry script. `cli generate` writes
  `entry = "src/run_<name>.py"`. `./configure` publishes it as `APP_ENTRY`, and the build's startup
  check and `cli sync`'s test drive run it, so a tool with several `src/run_*.py` scripts is
  checked too. An `entry` that names a missing file fails the build. Without `entry`, several
  `src/run_*.py` skip the build's check with a hint locally and fail it in CI.
- `cli generate` refuses to run inside an ngargparser tool, or in a folder under one, because the
  new tool would ship in that tool's tarball: "✗ This folder is inside the ngargparser tool ...
  nothing was created. Run cli generate in a folder outside it: cd <parent> && cli generate <name>"
  (in the tool folder itself, "✗ This folder is the ngargparser tool ...").
- `cli sync` refuses to write any file that is a link, or sits in a linked folder, leading outside
  the tool (framework files, `.gitignore`, `pyproject.toml`, `README.md`, `deploy/install.sh`,
  `.ngargparser/`), and prints the commands that replace the link with a copy. It used to write the
  new framework code into the file the link pointed at.
- `setup_<dep>_env.sh` exports `<DEP>_VENV` whenever a virtualenv is known, and `<DEP>_PYTHON`
  always (`<dep>_python` from `paths.py`, else the virtualenv's `python`, else `python3`, and empty
  when no entry script is known or it is not a Python script; `load_app_env` sets it too), so a
  caller runs `${<DEP>_PYTHON:+"$<DEP>_PYTHON"} "${<DEP>_ENTRY:?}" predict ...`. After several
  setup scripts, the last virtualenv sourced comes first on `PATH`, so a bare `python3` ran the
  wrong one.
- `.env` starts with a comment line naming the keys `./configure` wrote. A key added to `.env` by
  hand is dropped on the next run, as before, and now named: "⚠  Removed FOO, BAR from .env:
  ./configure rewrites .env from paths.py. Put them in paths.py to keep them."
- Every build copy and tarball holds `.ngargparser-build` (`TOOL_NAME=` and `TOOL_VERSION=` lines).
  `cli sync` refuses a folder that has it, so a build copy or a deployed tool is never synced.
- `cli sync --allow-downgrade` (or `NGARGPARSER_SYNC_ALLOW_DOWNGRADE=1`). A tool last synced with
  a newer ngargparser than the installed one now stops the sync with exit 1 and nothing written
  ("This tool was last synced with ngargparser A, which is newer than the installed
  ngargparser B. ..."), and so does one last synced from a checkout whose files the installed one
  may lack (see the stamp entry under Changed); the flag syncs it anyway. Versions compare in PEP 440 order: `0.5` is
  `0.5.0`, and `0.4.2.dev1` < `0.4.2rc1` < `0.4.2` < `0.4.2.post1`.
- `NGARGPARSER_SKIP_STARTUP_CHECK=1` makes `make build` skip the startup check.
- `self.parser_<subcommand>.remove_argument(<option>)`, the same as
  `self.remove_argument(<option>, "<subcommand>")`. `cli sync`'s known-clash check accepts both.
- Tools can be deployed in either order. `setup_<dep>_env.sh` reads the dependency's `APP_ENTRY`
  and `APP_VENV` from `<dep>_path/.env` each time it is sourced, also when `./configure` found
  neither because the dependency was not deployed yet. The tool's Python does the same at start-up:
  `./configure` lists those keys in `APP_KEYS_FROM_DEPS` (for example
  `APP_KEYS_FROM_DEPS=CALLEE_VENV,CALLEE_ENTRY`), and `app_env.py` reads them again from the
  dependency's `.env`, with the tool's own values as the fallback. Both read a value the way
  python-dotenv does (a leading `export `, one pair of quotes). A `<dep>_venv` or `<dep>_entry`
  set in `paths.py` stays fixed, and `<dep>_entry` now wins over the dependency's `APP_ENTRY`.
  With no entry at all, `<DEP>_ENTRY` stays unset.
- A copied or moved tool stops with "This tool was configured in <X> but is running from <Y>.
  Run './configure' in <Y>, then re-run this command." instead of importing the old folder's
  `libs/`. When its `.venv` came along, a middle line says to recreate it first
  (`rm -r .venv && uv sync`).
- A generated `pyproject.toml` sets `[tool.pytest.ini_options] pythonpath = ["src"]`, so a test in
  `tests/` can `from MyAppArgumentParser import MyAppArgumentParser`. `uv sync` installs pytest
  from the `dev` dependency group, and `uv run pytest` runs the tests. Tools generated earlier need
  the block added by hand.
- Each tool publishes its start-up script as `APP_ENTRY` in its `.env`. `./configure` takes it
  from `[tool.ngargparser] entry` in `pyproject.toml`, which ships in the tarball; else from an
  `APP_ENTRY` already in `.env` (never one inherited from the environment), as older tools set it;
  else from the single `src/run_*.py`. It warns when `entry` names a missing file, naming what it
  publishes instead ("Publishing src/legacy_main.py instead." or "Publishing nothing."), and when
  it cannot choose ("No APP_ENTRY published: found N
  src/run_*.py scripts. Set 'entry = "src/<script>"' under [tool.ngargparser] ..."). For each
  dependency that publishes one, `./configure` exports `<NAME>_ENTRY` (the full path) next to
  `<NAME>_PATH` in `.env` and `setup_<name>_env.sh`, so a caller runs `"$<NAME>_ENTRY"` instead of
  hard-coding a script name such as `src/tcell_mhci.py`, which tc1's newer version renames.
- `cli sync --dry-run` says which ngargparser a real run would install first. The dry run skips that
  self-upgrade, so its preview uses the installed version's files; the new line makes the difference
  visible. When the installed ngargparser is a local checkout, it also warns that a real run replaces
  it, and names `--no-upgrade` as the way to sync from the checkout.
- `cli sync` no longer leaves a broken project behind. It now ends in one of four ways: synced
  and verified, synced but could not verify, stopped with nothing changed, or rolled back (the
  last two exit 1).
  - Known clashes: before writing anything, sync checks the project's `src/*ArgumentParser.py`
    for code a framework release is known to break and reports the file, line numbers and fix;
    the help screens decide whether it stops (below). First entry: a project that declares `--output-prefix` / `--output-format` on `predict`,
    or reuses `-o` / `-f` there, which the base class has owned since 0.3.0. `--dry-run` reports
    it too.
  - Test drive: when a framework file is about to change, sync runs the project's four help
    screens before and after, using the project's own `.venv` interpreter. This executes project
    code: the script `[tool.ngargparser] entry` names, else the only `src/run_*.py`.
  - The help screens decide a known clash, since the check reads the code and a finding can be
    false (a declaration under `if False:`). When every help screen passes before the sync, a real
    run goes on: with "ℹ Not a clash here: ..." when `scaffold_version` is not older than the
    clash's release or no framework file changes, else with "ℹ Possible known clash (ngargparser
    0.3.0 and later): <file>:<line> <finding>." and the test drive after the update decides. A
    screen that fails then rolls the sync back, naming the clash as the likely cause ("The likely
    cause is the known clash found before the sync:"). When a screen fails before the sync, or
    the screens cannot run, the stop says why ("The tool's help screens did not rule it out: ...
    fails before the sync (...)."). A dry run runs no tool code: it says "✗ Sync may stop: ..."
    and that the help screens decide, or "✗ Sync would stop: ..." when a real run cannot run
    them.
  - Undo: if a screen that worked before fails after, or sync is interrupted or errors out
    midway, every touched file is restored, including moved, created and edited ones and files
    reached through a symlink.
  - `cli sync --no-verify` (or `NGARGPARSER_SYNC_NO_VERIFY=1`) skips the known-clash check and
    the test drive, and prints that it did.
  - Measured on copies of the 8 current tools: the 5 built on 0.1.13 to 0.2.4, which the old
    sync left crashing on every command, are now stopped untouched; the 3 on 0.3.0 or newer
    sync and verify.

- `scripts/get_libs.sh`, a new user-owned hook for all outside code a tool needs at run time (git
  clones, archives, data, and third-party tools you download by hand; the template's Example 3
  shows the pattern for those). `make build` runs it at the start, in an empty temp dir, and moves
  each top-level entry it leaves into the project's `libs/`. The fetch is skipped while
  get_libs.sh and `TOOL_VERSION` are unchanged and its entries are still in `libs/` as it fetched
  them (state in `.ngargparser/libs-stamp`; to force a fetch, delete the fetched folders in
  `libs/`). If the stamp is lost, the build reads which `libs/` entries it owns from the managed
  `libs/.gitignore`, so it can still replace them. A failed fetch leaves `libs/` as it was and
  stops the build. Fetched directories get `__init__.py` files, and a managed `libs/.gitignore`
  keeps them out of git. `cli generate` creates the template; `cli sync` does not add it to
  existing projects.
- `make build` checks that the tool starts: it runs the entry script's `--help`, `predict --help`,
  `preprocess --help` and `postprocess --help` from the build dir, with `APP_ROOT` set to the
  build, before packing. It runs them in `.ngargparser/startup-venv`, synced from the build copy
  with `uv sync --frozen --no-dev --offline --reinstall-package <name>` as the deploy installs the
  tool, so a module only the `dev` group brings fails the build. Without uv or `uv.lock`, or when that sync fails, it uses the
  tool's `.venv` and prints "Notice: the startup check uses the tool's .venv, so packages only its
  dev group installs were not left out (...)". A crash fails the build and shows the last error
  line. Outside CI, no `.venv`, or more than one `src/run_*.py` and no `entry`, skips the check
  with a notice.
- `make build` checks every symlink in the build: each must be relative and resolve inside it.
  Absolute, dangling, looping and escaping links fail the build and are listed.
- `./configure --<dep>-entry=<path>` sets `<dep>_entry`, the entry script of a dependency that
  publishes none, such as one that is not an ngargparser tool; give it as an absolute path.
  `<dep>_python` in `paths.py` names the Python that runs a dependency, and wins everywhere: in
  `.env`, in `setup_<dep>_env.sh` and in `dependency_command`.
- A dependency whose entry script is not a Python script, such as a shell script or a program, runs
  by itself. Its `#!` line decides, and without one, a name that ends in `.py`. `dependency_command`
  returns `[<DEP>_ENTRY, *args]`, `<DEP>_PYTHON` is empty, and one shell form runs either kind:
  `${PEPX_PYTHON:+"$PEPX_PYTHON"} "${PEPX_ENTRY:?}" predict ...`. `./configure` prints no "has no
  virtualenv" warning for such a dependency, and `dependency_command` raises `RuntimeError` for one
  that is not executable ("<name>'s entry <path> is not a Python script and not executable: chmod +x
  it, or point <name>_entry in paths.py at its start-up script").
- `cli generate` writes `tests/conftest.py`, which runs `./configure` once when `.env` is missing,
  so `uv run pytest` passes in a fresh clone. When `./configure` fails, as for a dependency path
  left `None`, pytest exits 1 with "This tool has no .env, and ./configure, which writes it,
  failed:" and the message of `./configure`.
- `setup_<dep>_env.sh` warns on stderr when the dependency publishes no entry script yet:
  "setup_<dep>_env.sh: <dep> publishes no entry script yet (no APP_ENTRY in <path>/.env, or no .env
  there yet): deploy and configure <dep>, or set <dep>_entry in paths.py".
- `make build` notices, which do not stop it: "Notice: the tool has no uv.lock, so the deploy
  installs the newest versions pyproject.toml allows: run 'uv lock' and commit uv.lock.", "Notice:
  the build writes VERSION; the tool's own VERSION file is replaced in the tarball.", and "Notice:
  the startup check (the tool's help screens) created these in the build copy; the build removed
  them, so they do not ship:" with one line per path, for a file such as a log that a module writes
  when it is imported.
- More preflight stops: an empty `entry` ("entry = "" under [tool.ngargparser] in pyproject.toml is
  empty: name the entry script, or delete the line."; it read as no entry and picked a
  `src/run_*.py`), an unreadable `scripts/core/build.conf` ("scripts/core/build.conf cannot be read
  (Permission denied)."; it read as a build.conf that exited), a `TARBALL_PREFIX` that starts with
  `.` (a hidden tarball escapes the CI job's `build/*.tar.gz`), and a `scripts/core/build.sh` with
  Windows line endings, which `make` checks before it runs the file ("ERROR: scripts/core/build.sh
  has Windows line endings (CRLF): convert it with ..."; bash stopped with "$'\r': command not
  found").
- A `cli sync` marker from another host, as in a tool folder on a shared file system, names that
  host: "✗ A cli sync on the host <host> (pid N, started ...) is still running there, or did not
  finish and may have left framework files half updated. ... If it is not running on <host>, copy
  them back ...". Each marker records the host, and on Linux the boot and the start time of the
  sync's process.
- `make libs` (`build.sh --libs-only`) fetches `libs/` with `scripts/get_libs.sh`, as the first step
  of a build does, under the same lock and with the same record, so the next build does not fetch
  again. It ends with "libs/ is up to date with scripts/get_libs.sh." Run it after adding an entry,
  before running the tool from its folder. The starter CI's comment says tests that import fetched
  code need a `make libs` first.
- Each `setup_<dep>_env.sh` defines `run_<dep>`, such as `run_pepx predict -j in.json`, which runs
  the dependency as `${PEPX_PYTHON:+"$PEPX_PYTHON"} "$PEPX_ENTRY" "$@"` does and returns its exit
  code. With no entry known, it runs nothing, prints "run_pepx: pepx has no entry script
  (PEPX_ENTRY is empty): deploy and configure pepx, or set pepx_entry in paths.py and run
  ./configure, then source setup_pepx_env.sh again" on stderr and returns 127.
- `NGARGPARSER_STARTUP_CHECK_TIMEOUT` sets the seconds each help screen of the build's startup check
  may take (default 60). The first screen that times out ends the check: "src/run_my_app.py --help:
  timed out after 1s: set NGARGPARSER_STARTUP_CHECK_TIMEOUT to the seconds each help screen may
  take, or NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check". A value that is not a
  number of seconds above 0 stops the preflight with one ERROR line.
- `core.app_env.DependencyError`, a `RuntimeError`, is what `dependency_command` raises. A newly
  generated entry script, and the sample app's, print it as one line and exit 1; other errors keep
  their traceback. `cli sync` never changes an entry script, so a tool generated earlier can catch
  `RuntimeError` around the call, as the cookbook's "Call a dependency" shows.
- `dependency_command` names a dependency caught in a redeploy: "<dep> is not deployed at <path>
  right now (a redeploy in progress?); try again", or "<dep> cannot run right now: <file> does not
  exist (a redeploy in progress?); try again" when its entry script or Python is gone.
- `cli deps add`, `cli deps remove`, the `cli deps` menu and `./configure` with flags hold an flock
  on `.ngargparser/paths.lock` while they read, change and write `paths.py`, so two of them at once
  no longer lose one's change. Each waits 30 s, then stops with nothing changed: "✗ Another process
  (pid N) is changing paths.py: it still holds .ngargparser/paths.lock after 30s. ..." from `cli
  deps`, "✗ Another ./configure or cli deps is changing paths.py: .ngargparser/paths.lock was still
  locked after 30 s. ..." from `./configure`. A plain `./configure` takes no lock.
- `cli sync` keeps a file you edited while it ran: an undo moves your version to
  `.ngargparser/edited-during-sync-<UTC time>/<path>` before it puts back the file from before the
  sync, and says so: "⚠ <path> changed after the sync wrote it (an edit made while the sync ran);
  that version was moved to ...".
- `make build` never ships the temporary files a stopped `cli` command left (`*.ngargparser-restore`,
  `.<name>.<8 characters>.tmp`, `.<name>.<8 characters>.ngargparser-sync`), whatever `.distignore`
  says, and lists them: "Notice: a cli command that was stopped (such as cli sync) left these
  temporary files in the tool folder; they do not ship. Check them, then delete them:".
- Parser checks: a `group=` label that differs from a section title of the same subcommand only in
  case, surrounding spaces or one trailing "s" stops the tool ("predict argument --zeta:
  group="output option" would add a section next to 'output options'; did you mean 'output
  options'?"), and the "is already a group" hint names that title. `set_subcommand_order` refuses a
  name listed twice ("set_subcommand_order: subcommand 'predict' is listed twice; name each
  subcommand once."). `remove_argument` refuses the 4 options `parse_args` reads (`preprocess
  --params-dir` and `--inputs-dir`, `postprocess --job-desc-file` and `--input-results-dir`),
  pointing to `update_arguments` or `help=argparse.SUPPRESS`.
- `-o ""` is a usage error in `predict` and `postprocess`: "argument --output-prefix/-o: the value
  is empty; -o takes an output prefix such as 'result'".

### Changed
- The startup check syncs `.ngargparser/startup-venv` from the build copy, as the deploy syncs the
  extracted tarball, and reinstalls the tool's own package each build (`--reinstall-package`). A
  packaged tool's editable install then points at the build copy, so a module `.distignore` left
  out can no longer be imported from the tool folder. This costs about 0.5 s a build for a
  setuptools tool. What the sync writes into the build copy, such as an `.egg-info`, is removed.
- A fetched `libs/` entry edited by hand is moved to `.ngargparser/removed-libs/<name>-<time>`
  before the new fetch replaces it: "libs/<name> changed since it was fetched, so it was moved to
  .ngargparser/removed-libs/<name>-<YYYYMMDD-HHMMSS> before the new fetch replaced it." It was
  deleted.
- The default `.distignore` excludes `*.egg-info/`, the package metadata a build or an editable
  install writes.
- `cli sync` stops with exit 1 and nothing changed when it cannot tell which script to test drive:
  "✗ Sync cannot check that the tool still starts after the update: several src/run_*.py scripts
  were found (...), and pyproject.toml names none of them as the entry script. Set entry =
  "src/run_<name>.py" under [tool.ngargparser] in pyproject.toml, ..." (or "no src/run_*.py entry
  script was found, and pyproject.toml names no entry script"). It synced unchecked.
- A file `cli sync` creates gets the mode the umask gives a new file, with the framework's execute
  bits: under umask 077, `600` for `src/core/*.py` and `700` for `scripts/core/build.sh`. It got
  `644` or `755`.
- `cli deps remove` splits names at commas, as `cli deps add` does: `cli deps remove pepx,netmhcpan`
  removes two dependencies.
- A `type=` check of the tool's own, such as a validator in `src/validators.py`, stops the command
  with one usage line and exit 2, whatever it raises: "argument --peptide-length/-n: 'abc' is not
  valid: invalid literal for int() with base 10: 'abc'" for a `ValueError` or `TypeError`, and
  "argument --allele: the check validate_allele failed on 'X': RuntimeError: boom" for another
  exception. argparse said "invalid validate_peptide_length value: 'abc'", and any other exception
  was a traceback with exit 1. `int`, `float`, `complex` and `ArgumentTypeError` keep argparse's
  messages.
- `group=` also takes one of the subcommand's own argument groups, such as
  `group=self.predict_input_group` on `self.parser_predict.add_argument` or `update_arguments`.
- The framework's help texts are sentences in sentence case: `preprocess -h` reads "Split a JSON
  input file into smaller job units, if possible, and create job_descriptions.json, which lists the
  command of every job in the workflow.", `postprocess -h` "Aggregate the results of the individual
  prediction jobs into one result.", and options read like "Output prefix for the prediction result
  files.", "Folder of the result files to postprocess." and "Skip validation of the inputs (assume
  they are valid)."
- The sample app's `predict` refuses `-a` with `-j`, whose input names its own amino acid: "-a sets
  the amino acid for -t input; a -j input carries its own amino_acid" (exit 2). It ignored `-a`.
- `cli generate example` writes a filled-in end-user `README` for aa-counter, which `make build`
  ships without the placeholder notice; the walkthrough is `README.md`.
- Setup scripts activate a virtualenv by exporting `VIRTUAL_ENV`, putting its `bin/` first on `PATH`
  and unsetting `PYTHONHOME`, as `dependency_command` does. They no longer source `bin/activate`,
  whose `deactivate` put back the `PATH` saved by an earlier virtualenv and so dropped the `bin/` of
  a module just loaded, and define no `deactivate`. Several setup scripts stack their virtualenvs,
  the last one sourced first.
- `./configure` no longer loads the tool's `.env` before it runs `paths.py`, so a `paths.py` that
  reads `os.environ` sees only what the shell exports, never the value the last run wrote.
- `dependency_command` calls `load_app_env()` itself when nothing in the process has, so a helper
  or a test that imports only `core.app_env` works. It reads the dependency's `<DEP>_VENV` and
  `<DEP>_ENTRY` from its `.env` again at each call, so a dependency redeployed while the tool runs,
  with a renamed entry script, is called as it is now.
- A dependency without a virtualenv runs from `dependency_command` without the `bin/` of any
  virtualenv on `PATH`, also one a calling tool left there; only the tool's own was left out.
- `deploy/install.sh` installs uv 0.11.14, the version CI pins, when the host has no uv; it
  installed the latest release.
- Docs: the shell call form is `${<DEP>_PYTHON:+"$<DEP>_PYTHON"} "${<DEP>_ENTRY:?}" ...`, which
  stops with "<DEP>_ENTRY: parameter null or not set" (bash, exit 127) or "parameter not set or
  null" (dash, exit 2) when no entry script is known. The README, the cookbook and the reference
  name the two ways it differs from `dependency_command`: the tool's `APP_*` keys pass through the
  shell form, and a dependency without a virtualenv runs there with the caller's active `python3`.
  The cookbook's first `update_arguments` example no longer moves `-f` out of "output options";
  `group=` gets an example of its own.
- Setup scripts no longer run `module purge` before `module load <dep>_module`, so they add the
  dependency's module to what is loaded: the purge unloaded the caller's own modules, and the one
  a setup script sourced earlier had loaded. A shell without a `module` command (`sh`, `dash`, a
  host without modules) gets a warning on stderr and the script goes on: "setup_<dep>_env.sh:
  <dep> needs the environment module <module>, but this shell has no 'module' command; load it
  first (module load <module>), or source this script from a login shell." Generated setup
  scripts pass `shellcheck -s sh -S warning`.
- The `scaffold_version` that `cli generate` and `cli sync` stamp carries a local label when
  ngargparser runs from a checkout: `<version>+g<short commit>`, with `.dirty` for uncommitted
  changes under `ngargparser/`, or `<version>+local` for a copy installed from a local folder.
  `cli --version` is unchanged. A later sync from another install of the same number stops with
  exit 1, since its files may be older ("✗ This tool was last synced from a development checkout
  of ngargparser (0.4.1+g...); the installed ngargparser is the 0.4.1 release, whose framework
  files may be older. Nothing was changed. ..."), unless the installed checkout holds the stamp's
  commit; `--allow-downgrade` takes the installed files.
- `make build` reads `scripts/core/build.conf` in a subshell. Only `TOOL_NAME`, `TOOL_VERSION`
  and `TARBALL_PREFIX` (and `EXCLUDE_FROM_BUILD_SYMLINK`, for its notice) come back, and each
  other variable it sets gets a notice: "Notice: scripts/core/build.conf sets SRC_DIR, which the
  build does not read (only TOOL_NAME, TOOL_VERSION and TARBALL_PREFIX)." A `build.conf` that set
  one of the build's own variables changed what the build read.
- `make clean` runs `build.sh --clean`, which takes the build lock: while a build or a sync runs
  in the folder it removes nothing and stops with "Another build or a cli sync is running in this
  folder (pid N). ...". It used to delete the copy a running build was writing.
- A parser mistake that argparse's `add_argument` raises for, a misspelled keyword or a value it
  refuses, stops with one `file:line` line and exit 2 instead of a traceback, on the top-level
  parser, a subcommand's parser and every group: "src/MyAppArgumentParser.py:61: predict argument
  --beta: add_argument() got an unexpected keyword argument 'hepl'; did you mean help=?", "...:
  'int' is not callable". `required=True` on an option of a mutually exclusive group says to make
  the group required instead.
- The symlink check says why a link dangles in the build copy when its target was left out:
  "(points at src/data.txt, which .distignore (or the hidden-file rule) excludes)" or "(points at
  .venv/bin/python, which the build never ships)"; it said "(dangling or a loop)".
- `cli --help` and every subcommand's `--help` never contact GitLab; the passive notice after a
  help screen reads only its cache. `cli --version` waits up to 4 seconds for GitLab (it waited 5)
  and says when GitLab did not answer in time.
- The end-user `README` a new tool gets runs the tool with `.venv/bin/python src/run_<name>.py`,
  not `uv run python`: on a deployed copy, `uv run` installed the dev group the deploy leaves out,
  and in a shared, read-only install it failed ("Failed to install: pytest ... Permission
  denied"). The developer `README.md` says it no longer ships.
- README: the Quickstart's input uses the sample app's `peptide` key, so `src/example.json` from
  `cli generate example` works with the Quickstart code. "Call another tool" runs
  `${PEPX_PYTHON:+"$PEPX_PYTHON"} "${PEPX_ENTRY:?}"`, which the cookbook's "Call a dependency" shows
  too, and "Build in CI" describes the test stage.
- `cli sync` holds `.ngargparser/build.lock`, the lock `make build` takes, and refuses while a build
  or another sync runs: "✗ A build or another sync is running in this folder (pid N). Nothing was
  changed. Wait for it, then run cli sync again." A dry run takes no lock.
- A sync that would change framework files stops before it writes anything when the startup check
  cannot run for a reason the tool can fix: no `.venv` ("✗ Sync needs the tool's .venv to check
  that the tool still starts after the update. Run 'uv sync', then cli sync again (or cli sync
  --no-verify to update without the check). Nothing was changed."), or an `entry` the build would
  refuse ("✗ Sync cannot check that the tool still starts after the update: entry = ... Fix it,
  ..."). Both synced unchecked. A dry run reports the stop and exits 1.
- `cli sync` does not reinstall the release it runs: "ℹ Installed ngargparser 0.4.1 is the latest
  release (v0.4.1); not reinstalling it."
- `cli sync` writes the managed `.gitignore` section in the template's order when it edits the
  section, or when a managed `!` rule sits above a managed ignore rule.
- Every `cli` message and help line says "tool" where it said "project": "Tool 'my-app' is ready",
  "Created the tool 'my-app'.", "Invalid tool name", "Create a new tool", and "this tool is still
  on X" after `cli upgrade`. A tool that is not configured says "This tool has not been configured
  yet: '.env' is missing or incomplete." and "Run './configure' in the tool folder (<path>), then
  re-run this command."
- `cli generate` refuses the names of packages a tool needs, `pytest`, `python-dotenv`, `dotenv`
  and `ngargparser`, in any case ("✗ Invalid tool name 'pytest'. A new tool has pytest in its dev
  dependency group, and the tool's name is also its Python package name, so the two would clash.
  Pick another name (e.g. 'aa-counter')."). `uv lock` failed on such a tool.
- `scripts/get_libs.sh`: an entry the script no longer fetches is removed from `libs/` ("libs/<x>
  is no longer fetched by scripts/get_libs.sh; removing it."). One edited by hand, or one without a
  fingerprint, is moved to `.ngargparser/removed-libs/<x>-<YYYYMMDD-HHMMSS>` instead, and a second
  line says so.
- `make build` runs `bash ./build.sh`, so `scripts/core/build.sh` needs no exec bit.
- The startup check runs without `PYTHONPATH`, `PYTHONHOME` and `PYTHONSTARTUP`, and with
  `PYTHONNOUSERSITE=1`, so the shell's Python settings cannot change its result.
- `./configure` writes `.env`, the setup scripts, `paths.py.bak` and `paths.py` as new files next
  to their places, and moves them in only once all are written, `paths.py` last, so a write that
  fails changes nothing ("✗ Could not write '.env': Permission denied. Nothing else was changed.").
  The `* Set ... in paths.py` lines print last.
- The `./configure` launcher runs `configure.py` with `python3` when the tool has no `.venv`.
- Setup scripts are POSIX shell (`.`, not `source`), so `sh` and `dash` can source them, as
  `subprocess.run(..., shell=True)` does.
- `<DEP>_ENTRY` joins a relative `APP_ENTRY` to the `APP_ROOT` in the dependency's `.env`, else to
  the real folder behind `<dep>_path`, so through a `current` link the virtualenv and the entry
  script name the same version folder.
- `write_results` takes a tuple wherever it takes a list, and its messages say "list or tuple".
- **Breaking:** `libs/` and its package folders go at the end of the import path, after the tool's
  own `src/`, the standard library and the packages in `.venv`. A fetched folder no longer replaces
  a standard module (a fetched `tests/fixtures/csv/`, which the build gives an `__init__.py`, was
  imported as `csv`) or an installed package. A package vendored in `libs/` and also installed in
  `.venv` now comes from `.venv`; remove the installed one with `uv remove` to use the `libs/`
  copy.
- The generated test tools are in the `dev` dependency group (`dev = ["pytest"]`, was `test`):
  `uv sync` installs pytest, `uv run pytest` runs the tests, and the deploy's `--no-dev` leaves
  pytest out. A tool generated earlier keeps its `test` group until you rename it and run
  `uv lock`.
- `./configure` takes `APP_NAME` from `APP_NAME` in `paths.py`, then `[tool.ngargparser] app_name`,
  then the tool's own `.env` (older tools), then the folder name. `app_name` used to lose to the
  `.env`, so an edit to it changed nothing until `.env` was deleted.
- The managed `.gitignore` rule is `.ngargparser/*` plus `!.ngargparser/framework-hashes.json`, so
  the framework hash record is tracked and a fresh clone still names local edits on `cli sync`.
  Sync replaces an older `.ngargparser/` rule inside the managed section.
- `make build-verbose` prints each step and the output of `get_libs.sh` and `hooks.sh`, without
  the shell trace it used to print; `NGARGPARSER_BUILD_TRACE=1` adds the trace.
- `README`, `deploy/install.sh` and a top-level `.python-version` always ship. A `.distignore`
  rule that would exclude one prints "Notice: .distignore rule '<rule>' has no effect on <path>:
  the build always ships it." The other files in `deploy/` follow the rules; only `install.sh` is
  forced. A tool with a top-level `.python-version` now ships it, so uv on the deploy host reads
  it.
- A tool folder needs a `[tool.ngargparser]` table in `pyproject.toml` or
  `src/core/NGArgumentParser.py`; `scripts/core/build.sh` alone no longer makes one, for
  `cli sync`, `cli deps` and `cli generate`'s inside-a-tool check. From a folder under a tool,
  `cli sync` and `cli deps` say "This folder is inside the ngargparser tool <root>: run cli sync
  there: cd <root>".
- `cli upgrade` and `cli sync`'s self-upgrade keep an installed ngargparser that is newer than the
  latest release tag, such as a pre-release: "ℹ Installed ngargparser X is newer than the latest
  release Y; not changing it." Installing the tag was a downgrade. `cli upgrade --check` shows
  "(installed is newer)", and `--ref` and `--dev` still install what they name. `cli --version`
  and the update notice compare in PEP 440 order, so a pre-release ahead of the latest tag is
  reported as ahead.
- `preprocess -o` help reads "Output folder of the job: job_descriptions.json and the
  predict-inputs/ folder layout go here (created if missing)." (was "prediction result output
  directory.").
- The sample app imports its validators like the blank scaffold, from its own
  `src/validators.py`, which adds a check for `-a`: "argument --amino-acid/-a: '(' is not a valid
  regular expression: ..." (exit 2). A bad pattern used to crash the count with `re.error`.
- README split: internals and the API reference moved to `docs/reference.md`. The README keeps the
  developer path (install, Quickstart, a table of the cookbook's recipes, dependencies, calling
  another tool, build, deploy, upgrade, troubleshooting) at about 2,400 words, down from 12,300. The
  sync outcomes, the build check order and settings, the CI internals, the orchestrator's variables,
  the generated parser line by line and the rarer troubleshooting entries (SSH, git) moved to the
  reference, which gains a Troubleshooting section. The README and the generated READMEs say "tool"
  and "tool folder" throughout. Fixed on the way: the `write_results` text claimed a missing key
  raises (a missing `warnings` or `errors` is valid); "Test your parser" asserted the `tsv` default
  right after an example that changes it; the Quickstart quoted `cli generate`'s output as ending
  before its next steps; "Call another tool" said to set `APP_ENTRY` in `.env`, which `./configure`
  rewrites (set `entry` in `pyproject.toml`); the Quickstart predict code crashed on `-t` (it read
  only `-j`); the replace-an-argument example offered `csv`, which `write_results` rejects, and
  dropped the option out of "output options"; the Glossary gave `NGArgumentParser.add_argument` the
  `group=` keyword, which only the subparsers take. The reference lists each built-in argument's
  Python name, as `run()` receives it. `tests/test_readme_examples.py` runs the marked examples of
  both files.
- `write_results` checks the envelope before it writes anything and raises `ValueError` naming the
  table and the key, for example "write_results: results[0] (type 'peptide_table') has no
  'table_columns'; it has 'columns': rename it to 'table_columns'". It also checks that `results`,
  `warnings`, `errors`, `table_columns`, `table_data` and each row are lists or tuples (a set is
  refused: "table_data[0] must be a list or tuple, not set"), and that each row matches
  `table_columns`. A
  misspelled key used to give an empty file. `json` also writes result entries that are not
  tables; `tsv` writes only tables.
- A tool option that reuses a framework option's name or letter stops every command, and
  `make build`, with exit 2 and one line naming the file, the line and the framework option
  ("... -o belongs to the framework (--output-prefix); pick another flag, or change the framework
  option with self.parser_predict.update_arguments(...)"). It was argparse's traceback ending in
  "conflicting option string". Two of the tool's own options that share a flag stop the same way,
  with a `ParserConflictError`: "-x is already taken by --xa/-x; pick another flag". `make build`
  prints the line once per help screen of its startup check.
- An unedited `predict` prints "predict is not implemented yet: edit src/run_<name>.py" and exits
  1; it printed nothing and exited 0. Unedited `preprocess` and `postprocess` stubs do the same
  ("preprocess is not implemented yet: edit src/preprocess.py", and the same for postprocess). The
  help screens still work, and `predict`'s line in the top-level `-h` reads "Run one prediction.".
- `deploy/install.sh` runs `uv sync --frozen --no-dev` when `uv.lock` exists, so a deploy installs
  exactly the lock, without the `dev` group, and never rewrites it; `uv sync --no-dev` otherwise.
  Then it runs `sh ./configure`, so a `configure` that lost its exec bit still runs, and a failing
  `./configure` fails the deploy; it used to skip a `configure` without its exec bit. `cli sync`
  never overwrites `deploy/install.sh`, so edit an existing tool's copy by hand.
- In CI (`CI` set), `make build` fails when the tool has no `.venv/bin/python`: "The startup check
  cannot run: CI is set and the project has no .venv/bin/python. Run 'uv sync' before 'make build'
  in the CI job, or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check." It also
  fails when the build has several `src/run_*.py` and no `[tool.ngargparser] entry`. Outside CI
  the build still skips the check with a notice.
- The default `.distignore` of a new tool is anchored: `/Makefile`, `/scripts/`, `/README.md`
  (the developer note; `README` always ships), `/setup_*_env.sh` and `/tests/`, plus
  `__pycache__/` and `*.bak` at any depth. It used to exclude `build.sh`, `build.md`, `Makefile`
  and `*.sh` at every depth, so a shell script or `Makefile` deeper in the tool never shipped; now
  it does. `.distignore` is user-owned, so existing tools keep theirs: to adopt the new default,
  replace those four lines with the five anchored ones (the reference's "Adopt the default
  .distignore" shows how), then check the build's `Excluded by .distignore` list.
- `cli sync` gives each run its own backup folder, `.ngargparser/sync-backups/<UTC time>Z-<pid>/`,
  and a rollback deletes only that folder. Two syncs started in the same second shared one folder,
  so a rollback could delete the other run's backups. A run that overwrote nothing removes its
  empty folder.
- Parser API: every argparse group of a subcommand takes `group=`: the framework's own groups
  (`self.predict_input_group`, `self.preprocess_required_group`, `self.preprocess_optional_group`,
  `self.postprocess_required_input_group`, `self.postprocess_optional_group`), a group made with
  `add_argument_group` or `add_mutually_exclusive_group`, and a group made from one of those. A
  `group=` that names the section the option shows in is ignored; any other value stops with one
  line ("...: predict_input_group is already a group; drop group=, or use
  self.parser_predict.add_argument(..., group="my options")", or "'my options' is already a
  group; ...") instead of argparse's `TypeError`. `group=` on the top-level `self.add_argument`
  stops with one line too ("top-level argument --verbose: group= works only on a subcommand's
  options; use self.parser_predict.add_argument(..., group="misc") ..."), a `ParserKeywordError`. Using `self.parser_predict` or `self.predict_input_group` before
  `add_predict_subparser()` stops with "src/MyAppArgumentParser.py:29: self.predict_input_group is
  not ready yet: call self.add_predict_subparser(...) first" instead of "'NoneType' object has no
  attribute 'add_argument'".
- The clash check covers the names options are stored under. An option stored under a framework
  option's `dest` (`output_format`, `output_prefix`, `output_dir`, ...) stops with "...: dest
  output_format belongs to the framework (--output-format); pick another dest, ..."; it used to
  replace the value the framework reads. `dest="subcommand"` stops with "dest subcommand belongs to
  the framework (it records which subcommand ran); pick another dest". `update_arguments` refuses
  to change a framework option's `dest` ("... the framework reads args.output_format; dest cannot
  change. To replace the option, remove it with remove_argument(...), then add_argument() your
  own.") and to give your option a framework `dest`.
- `update_arguments()` with no option name raises `ValueError` listing the valid names; it returned
  `False` and changed nothing. `set_subcommand_order` with an unknown name raises `ValueError` with
  a did-you-mean, and checks every name before it reorders; it ignored the name. The rename error
  now reads "...: remove the option with remove_argument('--output-format', 'predict'), then
  add_argument() the new one."
- A new tool's `src/validators.py` imports only `validate_file`, `validate_directory` and
  `validate_directory_given_filename`. It no longer imports `validate_preprocess_dir`, which creates
  and prints the preprocess folders while argparse is still reading the command line, nor the
  dependency helpers `get_dependencies_from_paths` and
  `create_directory_structure_for_dependencies`. All three stay in `core_validators`, so an older
  tool that imports them still works.
- `make build` warns when `scripts/core/build.conf` sets `TOOL_VERSION` and the environment sets
  another: "Warning: scripts/core/build.conf sets TOOL_VERSION=..., which is ignored: the
  TOOL_VERSION environment variable (...) wins. Remove TOOL_VERSION from build.conf."
- ngargparser's own CI runs 11 fast test files on Python 3.12 on every push (`pytest-fast`). A tag
  pipeline runs the full suite on Python 3.9, 3.10, 3.11, 3.12, 3.13 and 3.14, once more as a
  non-root user, where the tests that need a permission error run, and the `real_uv` tests
  (`pytest-real-uv`). The full-suite jobs install `git` and `make`, so the build tests run instead
  of skipping.
- `scripts/get_libs.sh` runs again when `TOOL_VERSION` changes; the stamp's second line is now
  `hash <sha> version <TOOL_VERSION>`.
- A startup check failure shows the last 15 lines of stderr, such as a traceback, below the error
  line.
- `postprocess -o` takes a prefix in a folder that does not exist yet and creates the folder when
  the result is written, as `predict` does.
- Generated `run_<name>.py` and `validators.py` import `core.set_pythonpath` first, so every file in
  `src/` can import from `libs/`, also when a test imports the parser directly. `run_<name>.py` no
  longer calls `load_dotenv()`: `app_env.py` loads the tool's own `.env`, and the extra call
  searched parent folders, so a build copy under `build/` read the source tool's `.env`.
- `cli sync` reads and edits `[tool.ngargparser]` values in `'...'` as well as `"..."`. A table
  written as an inline table, a dotted key or a multi-line string gets "⚠ pyproject.toml writes
  [tool.ngargparser] in a form cli sync does not edit (...). Set scaffold_version = "X" in it by
  hand.", and the sync goes on.
- `cli sync` syncs a tool that has no `scripts/` folder (it creates `scripts/core/`), and a refusal
  for a missing `src/` names `src/`.
- `cli sync --dry-run` goes on past a known clash with the rest of its preview, then exits 1 like the
  real run.
- Ctrl-C during `cli sync` restores every file, prints "Cancelled; nothing changed." and exits 130,
  without a traceback. A `kill` (SIGTERM) or a closed terminal or dropped ssh session (SIGHUP) does
  the same: "✗ Sync interrupted (SIGTERM).", then the restore, then "Cancelled; nothing changed.",
  with exit 143 or 129.
- `cli deps` runs only in a tool's top folder and refuses anywhere else, as `cli sync` does.
  `cli deps remove` matches the variable name first, and a title only when exactly one block has
  it.
- `cli deps remove` deletes only the lines `cli deps` wrote (the title, the settings and the stub
  comments) and keeps any other line inside the block, naming it: "⚠  Kept N line(s) that cli
  deps did not write:". It deleted every line from the title to the last setting.
- The `./configure` launcher works in its own folder, so `../other-tool/configure` configures
  `other-tool`. Generated setup scripts start with a `# Generated by ./configure ...` line.
- The build's message for a legacy exclusion file no longer says ".distignore" has the same syntax:
  "... .distignore uses gitignore patterns, so a bare name also matches nested files and folders of
  that name; write /name to match only the top-level one, as <file> did."
- `cli generate <name>` writes two different READMEs. `README`, which ships in the tarball, is now
  the end-user README in the layout every IEDB tool uses (`<name> - version TOOL_VERSION`, with the
  name as typed, then Quick Install, Introduction, Release Notes, Prerequisites, Installation, Usage), with
  placeholders to fill in. It used to be a 403-line framework tutorial that each tool had to
  replace by hand. `README.md` is a 54-line developer note: the files you edit, where the predict
  logic goes, and commands that run as written. `cli generate example` writes the end-user README,
  filled in for aa-counter, as `README`, and its walkthrough as `README.md`, rewritten from a real
  run: its commands failed as written (wrong file name, `json` named as the default format, an
  option pair the tool rejects, folders that don't exist).
  `tests/test_generated_readmes.py` runs every command in the generated READMEs.
- README rewritten for a developer new to ngargparser: prerequisites, a Quickstart that goes from
  `cli generate` through writing predict logic to `make build`, the files you edit, the workflow
  with job descriptions defined, troubleshooting for the first errors, a glossary, and a table of
  contents; internals moved under Reference. About 40 statements the audit found wrong are
  corrected, among them a Quickstart `./configure` that exited 1, `python` without `uv run`,
  "auto-creates three subparsers", the ownership rule, the sync outcomes, and the build names.
  Every Python example marked `<!-- example: -->` still runs against a fresh tool. A second audit
  pass corrected 7 more statements and 4 contradictions, and dropped SDSC, which is not a deploy
  target.
- The `--help` contact block drops the retired IEDB Help Desk (https://help.iedb.org/) and lists
  only the IEDB Discussion Forum, in both the framework base class (`NGArgumentParser.py`) and the
  example-app scaffold template.
- `cli --version` now checks the latest release tag on GitLab on every run and reports any
  mismatch on stderr: the "a new ngargparser is available" notice, with a link to the release
  notes, when the installed version is behind, or a one-line note when it is ahead of the latest
  tag. This check uses no cache and no once-a-day limit, and it also runs when output is not a
  terminal. stdout is unchanged: exactly `cli X.Y.Z`. It waits up to 4 seconds; a remote that does
  not answer in time ("⚠ Couldn't check for a newer ngargparser (GitLab did not answer within
  4 s).") or cannot be reached is reported in one line and never changes the exit code. CI and
  `NGARGPARSER_NO_UPDATE_CHECK=1` skip the lookup. A help screen (`cli --help`, `cli sync --help`)
  never contacts GitLab: the notice after it reads only the cache. Bare `cli` and the passive
  notice on other `cli` commands are unchanged.
- **Breaking:** file arguments no longer use `argparse.FileType`, which Python 3.14 deprecated.
  `preprocess --input-json` and `postprocess --job-desc-file` in the framework base class, and
  the scaffold's predict `--input-tsv`/`--input-json`, now use `core_validators.validate_file`.
  They yield a `pathlib.Path`, not an open file; open it yourself after parsing. A missing file
  still exits 2 with an argparse error. `-` no longer means stdin.
- Downstream tools must update before their next `ngargparser sync`. A code reading on
  2026-10-02 (not executed) found:
  - Crash, because they call `.read()`, `.seek()` or `json.load()` on the argument: phbr,
    pepsysco, bcell-sequence-input.
  - Silently wrong path, because they test `hasattr(arg, "name")` and `Path.name` is only the
    basename: axelf (postprocess), conservancy and rate (preprocess).
  - Already path-safe: bcell-structure-input, population-coverage.

- **Breaking:** `make build` copies the project into `build/<TOOL>-<VERSION>/` instead of
  symlinking it, and packs with `tar -czf` instead of `tar -chzf`. Symlinks inside the project and
  its dependencies are kept as links, so a dependency's internal link (such as `data -> ../data`)
  is no longer stored twice, and `.git` inside a linked folder no longer ships. Developers edit and
  run in the project folder; `build/` is output, and its files are made read-only after packing so
  a fix typed there fails to save instead of vanishing on the next build. Hooks now patch the copy
  only, so nothing a hook writes reaches the project.
- **Breaking:** `scripts/hooks.sh` is for patching the build copy. Downloads in it still work but
  run on every build; move them to `scripts/get_libs.sh`.
- **Breaking:** old layouts stop the build with a message naming the fix, instead of being read or
  silently ignored: `scripts/do-not-distribute.txt`, a root `do-not-distribute.txt`, or
  `scripts/.distignore` without a root `.distignore`; `scripts/build_hooks.sh` or
  `scripts/dependencies.sh` without `scripts/hooks.sh`; `scripts/deps.sh` (the pre-release name
  of `scripts/get_libs.sh`); a symlinked `libs/`. `cli sync` still
  renames the old hook names. The README has a migration checklist for existing tools.
- `cli generate` no longer creates `scripts/hooks.sh`. The build skips the hook step when the file
  is missing, so most tools need only `scripts/get_libs.sh` and `.distignore`. To add a hook, copy
  `templates/hooks.sh`. The template no longer suggests copying local dependencies; those belong
  in get_libs.sh. Existing tools keep their hooks.sh.
- **Breaking:** `scripts/build.conf` moved to `scripts/core/build.conf`, next to `build.sh`. It
  stays user-owned: `cli sync` never overwrites it. `cli sync` moves an existing
  `scripts/build.conf`, and the build stops while `scripts/build.conf` is still there. In the
  tarball the file now ships at `scripts/core/build.conf`.
- A failed build now also removes the previous tarball, so an old one never looks fresh.
- `make build` works out the whole copy in one pass, then copies each folder's entries with one `cp`
  call, so a large folder with one excluded file copies fast; such a folder was walked one entry at
  a time, with a scan of the exclusion list for each folder.
- `scripts/get_libs.sh` and `scripts/hooks.sh` run in a process group of their own, so a job one of
  them starts with `&` stops with the build, after a Ctrl-C too; it ran on after the build. Where
  `setsid` exists (Linux), they also run in a session of their own: a hook can still read a terminal
  on its standard input, but can no longer open `/dev/tty`.
- In CI (`CI` set), a `uv sync --frozen --no-dev --offline` that cannot make the startup check's
  environment is tried once online, since a runner's uv cache is often cold. When both fail, the
  notice ends "..., and neither could 'uv sync --frozen --no-dev' (exit N):".
- `cli deps add` and the interactive menu split names at commas: `cli deps add pepx,netmhcpan`, and
  the answer `pepx, netmhcpan`, add two dependencies. A quoted name with a space ("T Cell") stays
  one. `cli deps` reads an annotated `<dep>_path: str = ...` as it reads a plain one, and `cli deps
  remove` also removes the dependency's `<dep>_entry` and `<dep>_python` lines.
- `cli deps add` refuses `uv`, names that start with `uv-`, `uv_` or `python` ("'uv' becomes 'uv',
  and the tool would set UV_PATH, ... uv takes its settings from variables that start with UV_
  (UV_PYTHON, for one, picks the Python that uv uses), so these could change how uv runs in the
  tool. Pick another name."), and names that are not plain ASCII ("'ünï' has characters that are not
  plain ASCII, and cli deps would turn it into 'n'. Use letters a-z, digits, '-' and '_' (e.g.
  'netmhcpan').").
- `libs/` folders go on the import path in sorted (code-point) order, the folders reached last
  first, so every host imports the same module when two folders hold one of the same name: of
  `libs/aaa/util.py` and `libs/zzz/util.py`, `import util` imports `libs/zzz/util.py`. The order
  followed the file system's listing, which differs between hosts.
- The warning for a dependency with two virtualenvs says which one to remove and what runs until
  then: "... Not guessing: pick one with './configure --pepx-venv=<path>', or remove venv there (uv
  sync uses .venv). Until then, pepx runs with the system Python."
- `postprocess -o` help reads "Output prefix for the merged result files (the results of every
  job, combined)." (was "prediction result output prefix.").
- `cli sync --dry-run` lines start "DRY RUN:", where they had a dash.
- ngargparser's own CI: `pytest-real-uv` fills uv's cache before its tests (a throwaway tool, locked
  and synced online, then locked again for `requires-python = ">=3.9"`); on a fresh runner the tests
  found an empty cache and skipped, which fails the job. The 3.11 `pytest` leg installs shellcheck
  and sets `NGARGPARSER_REQUIRE_SHELLCHECK=1`, so the tests that shellcheck the generated setup
  scripts fail there without it instead of skipping. The `dev` group pins mypy below 2, which cannot
  check for Python 3.9, and mypy skips pytest's own types, which use syntax 3.9 lacks.
- Docs: the README keeps the Quickstart and a table that points at the cookbook's recipes, in place
  of its copies of "Add an argument", "Write the results" and "Test your parser". "Call another
  tool" shows one form, the shell call the cookbook shows too, and the internals it held
  (`LD_LIBRARY_PATH`, values that follow a dependency's `.env`, sync rollback details) are in the
  reference. The cookbook's "Add an option" shows the option inside a `def __init__(self):` excerpt,
  8 spaces in, so a paste works, and its import list matches `src/validators.py`.
- **Breaking:** `postprocess`'s `output_prefix` is text, as in `predict`; it was a `Path`. A
  `postprocess.py` that calls a `Path` method on it needs `Path(output_prefix)`.
- The startup check falls back to the tool's `.venv` only when uv lacks the network or its cache.
  Any other uv error stops the build: "ERROR: 'uv sync --frozen --no-dev' fails in the build copy
  (exit N), so deploy/install.sh would fail on the deploy host too:", then uv's lines. After
  `uv.lock` changes, the startup environment syncs once with `--reinstall`, which checks every
  package's hash (the lock it last synced is in `.ngargparser/startup-venv.uv-lock`), so a hash
  edited by hand in `uv.lock` stops the build as it stops the deploy.
- An `entry` that is not a Python script (no `.py`, no Python `#!` line) runs by itself in the
  startup check and in `cli sync`'s test drive. In the startup check, the check's environment comes
  first on `PATH`, and one that is not executable fails with "src/run.sh is not a Python script and
  not executable: run 'chmod +x src/run.sh' and commit the mode (git update-index --chmod=+x
  src/run.sh)".
- The starter `.gitlab-ci.yml` maps every character the build refuses in `TOOL_VERSION` (from the
  branch name) to `-`, and drops a leading `-`: `feature/x#1@a` builds `feature-x-1-a`. Tags keep
  their names.
- `./configure` names every `<dep>_path` left `None` in one message, with one command that sets
  them all and repeats the flags given: "✗ alpha_path and beta_path are None in paths.py. Set them
  with './configure --pepx-path=../pepx --alpha-path=<path> --beta-path=<path>', or run 'cli deps
  remove <name>' to drop the ones this tool does not need." `cli deps add alpha beta` prints one
  command too, "./configure --alpha-path=/path/to/alpha --beta-path=/path/to/beta", and `cli deps
  list` shows each `<name>_path = ...` line.
- A framework option clash names the third way out: "...; pick another flag, change the framework
  option with self.parser_predict.update_arguments("--output-prefix", ...), or replace it with your
  own: call self.parser_predict.remove_argument("--output-prefix") first". The 4 options
  `remove_argument` refuses keep the two-way line.
- In a build or deployed copy (it has `.ngargparser-build` and `deploy/install.sh`), hints say
  `bash deploy/install.sh` instead of `uv sync`, the recreate-venv hints included ("rm -r .venv &&
  bash deploy/install.sh, which runs ./configure too").
- `deploy/install.sh` runs the pinned uv installer with `UV_INSTALL_DIR="$HOME/.local/bin"` and
  `UV_NO_MODIFY_PATH=1`, so uv lands in the folder the script puts on `PATH`, and the deploy
  account's shell start-up files stay as they are. Both end-user READMEs pin uv 0.11.14.
- `cli sync`'s help screens run without `PYTHONPATH`, `PYTHONHOME` and `PYTHONSTARTUP`, and with
  `PYTHONNOUSERSITE=1`, as the build's startup check does, each in a session of its own: a timeout
  kills its whole process group. `NGARGPARSER_SYNC_HELP_TIMEOUT` must be above 0 and at most 86400;
  another value gets one warning and the 60 s default.
- `cli sync` writes each pre-sync copy into `<run>.partial/` and renames it into
  `.ngargparser/sync-in-progress/<run>/` once whole, and the unfinished-sync message leaves an
  incomplete copy out of its commands. The undo's temporary file is
  `.ngargparser/sync-in-progress/<run>.restore`, not a file next to the tool's.
- `cli deps` error sentences end with a period before "Fix it, then run cli deps again; nothing was
  changed."
- Docs: the README keeps the main path, in under 2,000 words. Its shell-call detail, the bash and
  dash exit codes and four troubleshooting entries moved to the reference, which holds the one copy
  of the shell call, "Run a dependency from a shell", with `run_<dep>` first. The README and the
  cookbook show a 2-line `run_pepx` example and link there. The `:?` form's bash exit code is 127
  only without `set -e`; under `set -e` it is 1. The reference now says, as the code does, that
  `dependency_command` joins a relative `<dep>_entry` to `<DEP>_PATH`, as the setup script does.
- Docs: every README and `docs/` page was checked against a generated tool, and each statement that
  no longer held was fixed: among them the `-o` refusals, the `DependencyError` causes, what sourcing
  `setup_<dep>_env.sh` does (it activates nothing; `run_<dep>` does), the lock every `./configure`
  takes, and the build lock and sync messages. New: `add_subcommand` in the reference and the
  cookbook, `NGARGPARSER_UPGRADE_URL`, and the top-level `--version`. The guides now describe
  current behavior only; history stays in this changelog.

### Fixed
- With no entry script known for a dependency, `<DEP>_PYTHON` is empty too, in the setup script and
  at start-up, so the one-line call fails at once (exit 127, or the `:?` message) instead of
  running `python3 ""`, which runs the `__main__.py` of the current folder.
- A `.env` that is not UTF-8 text no longer stops `./configure` with a traceback. A dependency's is
  named and not used ("⚠  Could not read <path>/.env: it is not UTF-8 text. Its values are not
  used."), and the tool's own is written again ("⚠  Could not read .env: it is not UTF-8 text.
  ./configure writes it again from paths.py."). A tool whose own `.env` is not UTF-8 stops with
  "<path>/.env is not UTF-8 text: run ./configure again to rewrite it" (exit 1).
- `./configure` prints no "has no virtualenv" warning for a dependency whose `<dep>_python` is set
  in `paths.py`, since that Python runs it.
- `./configure` runs `paths.py` from its text on every run, without `__pycache__`. The import
  system reused a cached `paths.*.pyc` while the size and modification time matched, so a
  same-size edit within one second, such as two flags in a row, was not seen.
- `.distignore` matching is case-sensitive on every host: the build pins git's `core.ignorecase`
  to `false`, so a developer's global `ignorecase = true` no longer made `*.TXT` exclude `a.txt`.
- A chmod of a fetched `libs/` entry, or a copy of the tool folder that keeps modification times,
  no longer refetches it.
- `cli sync` hashes framework files without the CR before each LF, so a checkout with CRLF line
  endings (`core.autocrlf`) is not taken for a local edit.
- A `cli sync` marker that does not parse, because a kill cut it short, counts as a running sync
  only while the pid in its name runs `cli sync`; any other process, or none, means the sync ended
  and the next sync reports it.
- The sample app's `preprocess` deletes only the files an earlier run wrote, listed in
  `.aa-counter-preprocess-files` in each folder. It deleted every file in `--inputs-dir` and
  `--params-dir`, and a file of yours there became a job.
- `build/`, `.git/`, `.venv/` and `.ngargparser/` at the top of the tool folder never ship, and
  neither do `venv/`, `env/` or `.virtualenv/` when they hold a virtualenv (a `pyvenv.cfg`),
  whatever `.distignore` says, so a whitelist `.distignore` (`*`, then `!*/` and `!` rules) is
  safe. Only `build/` and `.git/` were always left out, so `!` rules could ship the tool's `.venv`,
  which the deploy host makes itself with `uv sync`, and the build's state.
- A `scripts/get_libs.sh` that clones into its working folder (`git clone <url> .`) stops the
  build: "scripts/get_libs.sh left a .git folder at the top of its working folder (git clone ...
  .): clone into a named folder instead, e.g. git clone <url> mylib." Its `.git` became `libs/.git`,
  a repository inside the tool's.
- `make build` and `cli sync` read `[tool.ngargparser]` with tomllib (or tomli) when one is
  installed, so `entry` in an inline table (`ngargparser = { entry = "..." }` under `[tool]`) or a
  dotted key names the entry script. The build read only a `[tool.ngargparser]` header table, and
  `cli sync` stopped, saying `entry` must be a quoted path.
- A sync write that fails names the file, and the read-only folder when that is the cause. When
  it was the run's first write, the run stops with nothing to undo: "✗ Could not write
  src/core/result_writer.py: the folder src/core is read-only (Permission denied). Nothing was
  changed." The undo of a failed sync puts back only the files the run wrote.
- `cli sync` handles a `scaffold_version` that is not a quoted string. A number
  (`scaffold_version = 0.5`) compares as the version it spells, and the stamp line says to set it
  by hand ("pyproject.toml sets scaffold_version under [tool.ngargparser] to a value that is not a
  quoted string (0.5), which cli sync does not edit. Set scaffold_version = "X" in it by hand.").
  Another type (`true`) gets "⚠ pyproject.toml sets scaffold_version = true under
  [tool.ngargparser], which is not a version string, so sync cannot tell which ngargparser last
  synced this tool; it goes on without that check."
- The known-clash check looks only at calls on the predict subparser: `self.parser_predict`,
  `self.predict_input_group`, names and attributes assigned from them or from
  `add_predict_subparser(...)`, and their groups. An `add_argument("-o")` on another object whose
  name holds "predict" (`self.predictor_cfg`) was reported as a clash, and `p.add_argument("-f")`
  after `p = self.parser_predict` was missed.
- `cli deps` writes `paths.py` and `paths.py.bak` through a temporary file renamed over each, so a
  failed write never leaves either cut short, and keeps a byte order mark at the start of
  `paths.py`. `cli deps remove <name>` with a name written like a dependency name never falls back
  to a block's title: "✗ Not removed: pepx is not declared in paths.py. The title Pepx is on the
  block of tcell_class_i; to remove that block, run: cli deps remove tcell_class_i". It removed
  that other dependency's block.
- `./configure --<dep>-...` works on a `paths.py` that starts with a UTF-8 byte order mark, and
  keeps the mark.
- `write_results` refuses a format name (`"tsv"`, `"json"`, any case) as `output_prefix` with
  `ValueError` ("write_results: output_prefix is 'tsv', a format name: pass it as output_format,
  e.g. write_results(result, args.output_prefix, 'tsv')"), and a prefix that is no text, path or
  `None` with `TypeError` ("... not Namespace: pass args.output_prefix"). They wrote `tsv.tsv` and
  `Namespace(output_prefix=None).tsv`. `predict -o tsv` and `postprocess -o json` are usage errors:
  "'tsv' is a format name; -o takes an output prefix such as 'result' (give the format with -f
  tsv, or name the files tsv.* with -o ./tsv)".
- `group=` takes a section title, or one of the same subcommand's argument groups, which counts as
  its title. A group of another subcommand stops with "group= takes a section of predict (a title,
  or one of its groups), not a group of preprocess: ...", and a mutually exclusive group with
  "group= takes a section title (a str), not a mutually exclusive group: ..." (each a
  `ParserKeywordError`); a group object made a help section titled "<argparse._ArgumentGroup
  object at 0x...>:".
- `update_arguments` refuses new `choices` without `default=` when they leave out the current
  default ("update_arguments can't set choices of --output-format/-f: default 'tsv' is not among
  the new choices json: pass default= too"), and `required=True` on an option of a mutually
  exclusive group ("--input-json is one of a group of mutually exclusive options (-t / -j): make
  the group required instead, or remove the other option"). The first left `-f` at a default it
  refused; the second made `-t` unusable.
- `cli sync` with GitLab unreachable no longer installs master: it skips the upgrade and syncs from the
  installed version. `cli upgrade` in that case names `--ref <tag>` and `--dev`.
- A failed `./configure` (a dependency path that is None) changes nothing: `.env`, the setup scripts and
  `paths.py` stay as they were.
- A framework file `cli sync` replaces keeps its own mode; only its execute bits change.
- A hash record that cannot be read, or is a folder, no longer fails the sync; one that cannot be
  written is named.
- A failed restore keeps the files from before the sync in `.ngargparser/failed-restore-<UTC time>/`,
  not in the system temp folder.
- A link out of the tool that points to nothing gets `rm <link>` as its fix.
- `make build`: "Build failed" after a signal starts on its own line; a dropped `libs/` entry gets one
  accurate line; the CRLF hint names `build.conf text eol=lf`; without `.distignore` the report says
  `Excluded by the default rules`; messages say "tool folder", not "project".
- `./configure` prints one warning, not two, for an `entry` that names a missing file with no fallback.
- A dependency with two virtualenvs follows its own `.env`, and a `<DEP>_VENV` inherited from a
  calling tool is no longer used.
- A tool started by another tool drops the inherited `APP_VENV`, `APP_ENTRY` and
  `APP_KEYS_FROM_DEPS`, and the keys of a declared dependency, when its own `.env` leaves them out.
- The copy of a dependency's virtualenv and entry script in the tool's `.env` is used only while the
  dependency has no `.env`. A dependency `.env` that publishes none leaves the key unset; the old
  copy described an earlier deploy.
- A copy started with `APP_ROOT` pointing at itself imports its own `libs/`.
- Under pytest, a tool that is not configured prints the message and exits 1, without pytest's
  INTERNALERROR traceback.
- `cli sync` replaces a framework file through a temporary file and a rename, so a running build
  keeps reading the old file.
- SIGHUP and SIGINT that reach `cli sync` together undo once. A signal the caller ignores (`nohup`'s
  SIGHUP, a background job's SIGINT) still does not stop the sync.
- `cli sync` names a `.ngargparser/framework-hashes.json` that does not parse ("⚠
  .ngargparser/framework-hashes.json does not parse; local edits to framework files cannot be
  detected this run (backups are still made).") instead of reading it as empty.
- `cli sync` restores the file modes of the framework files: `scripts/core/build.sh`, `configure`
  and `src/core/configure.py` executable, the other `src/core/*.py` not ("Fixed the file mode of
  scripts/core/build.sh (664 → 775)").
- A signal stops a running `scripts/get_libs.sh`, `scripts/hooks.sh` or startup check at once, and
  every process it started; the build used to wait for the script, or for the help screens' timeout.
  Without `ps` (`python:3.11-slim`), the build finds those processes in `/proc`; it used to stop
  only the script itself.
- `.git` and `__pycache__` folders inside fetched `libs/` entries get no `__init__.py`.
- In progress mode (`make build`), an error clears the progress bar first, so it prints on a line of
  its own.
- `update_arguments` refuses `choices` or a `default` for the framework's `-f` other than `tsv` and
  `json`: "update_arguments can't set choices of --output-format/-f to tsv, json, csv: write_results
  writes only tsv and json. To offer formats you write yourself, remove the option with
  remove_argument('--output-format', 'predict'), then add_argument() your own." The tool used to
  accept them and fail with "unsupported output format" only once a result was written.
- `write_results` in TSV raises `ValueError` for a cell or column name that holds a tab or line
  break ("write_results: results[0] (type 't'): table_data[3][1] holds a tab or line break, which
  tsv cannot hold; clean the value, or write -f json"); such a value shifted the row's fields or
  started a new row. `None` is an empty cell in TSV, where it was the text `None`. JSON is
  unchanged.
- A dependency value that neither the dependency's `.env` nor the tool's own `.env` sets is unset
  at start-up and in `setup_<dep>_env.sh`, even when a calling tool passed it down. The caller's
  value pointed at the caller's own dependency of that name.
- `setup_<dep>_env.sh` works in a shell with `set -u`, and prepends `<dep>_lib_path` to
  `LD_LIBRARY_PATH` without a trailing `:` when `LD_LIBRARY_PATH` is unset or empty; a trailing
  `:` made the loader search the current folder.
- `./configure --<dep>-...` with a read-only `paths.py` stops with "✗ Could not write 'paths.py':
  Permission denied. paths.py is unchanged." and keeps the earlier `paths.py.bak`. A read-only
  folder, or a write that fails partway, names the file and changes nothing: "✗ Could not write
  'paths.py': File too large. Nothing else was changed." Both used to stop with a traceback.
- `./configure` warns when the tool has two virtualenvs, of which it publishes neither: "⚠ Found 2
  virtualenvs in this tool (.venv, venv); publishing none as APP_VENV. Remove venv (uv sync uses
  .venv), or set APP_VENV = "<path>" in paths.py. Until then, tools that call this one run it with
  the system Python." It published none without a word.
- Running a tool with a Python that lacks python-dotenv prints "This tool needs its virtualenv: run
  it with 'uv run python src/<script> ...' (or activate .venv first). Run 'uv sync' if .venv is
  missing." instead of a traceback ending in "No module named 'dotenv'". Run from another folder,
  the line names the tool folder ("This tool needs its virtualenv: in <tool>, run it with ..."). A
  build or deployed copy, which has no uv project and may be read-only, says "run it with
  '.venv/bin/python src/<script> ...'. Run 'bash deploy/install.sh' if .venv is missing."
- `validate_directory_given_filename` says what is wrong: "'nope/x.txt': folder 'nope' does not
  exist." or "'f.txt/x.txt': 'f.txt' is not a folder." (was "'nope/x.txt' is not a valid
  directory.").
- `cli generate` refuses a name that does not end in a letter or digit ("✗ Invalid tool name
  'a-'. End it with a letter or digit: ..."), which `uv lock` rejects as a package name. It keeps
  capitals in the class name (`MyTool` gives `MyToolArgumentParser`, was
  `MytoolArgumentParser`), and writes the green badge `cli sync` writes, so the first sync of a
  new tool changes nothing.
- `cli sync` keeps CRLF line endings and a byte order mark in `pyproject.toml`, `README.md` and
  `.gitignore`; a line it adds gets the file's line ending. It rewrote every line ending as LF.
- `cli deps`: the interactive menu adds a name entered twice once ("⚠  foo was entered twice; added
  once."), where it wrote two blocks. `cli deps remove` removes every block of a name and counts
  them ("Removed 2 dependency block(s) from 'paths.py': Pepx (pepx, 2 blocks)"), where it left the
  second. A name whose settings would be a system variable (`ld-library` gives
  `LD_LIBRARY_PATH`; also `library`, `pkg-config` and others) is refused. Every form except
  `cli deps list` refuses a build or deployed copy.
- A second Ctrl-C, SIGTERM or SIGHUP while `cli sync` restores files is ignored, so the undo
  finishes. An undo that fails anyway prints "Restore did not finish (...). The files before the
  sync are in <snapshot>; backups of overwritten files are in <backups>.", then says to copy those
  files back by hand before the next sync, and not to run `cli sync --no-verify` on a
  half-restored tool.
- `TOOL_NAME` and `TOOL_VERSION` may hold only letters, digits, `.`, `_`, `+` and `-` (after `/`
  becomes `-`), may not be `.` or `..` alone, and may not start with `-`. Otherwise the build stops
  before it creates or removes anything. A `TOOL_NAME` such as `../../x` made a failed build delete
  a folder outside the tool.
- A `scripts/get_libs.sh` that writes into `$LIBS_DIR` directly fails the build with
  "scripts/get_libs.sh wrote into libs/ directly. Write into the current directory instead: ...".
  The build compares every path under `libs/`, at any depth, and lists up to 20 ("... and N
  more"): new ones are removed again, changed and deleted ones are listed. Such writes used to stay
  unrecorded, outside `libs/.gitignore`, and were never cleaned up; a write inside an existing
  entry was not seen at all.
- A UTF-8 byte order mark at the start of `.distignore` is ignored, as git ignores one; it became
  part of the first rule.
- With both `scripts/build.conf` and `scripts/core/build.conf`, the build says to merge them by hand
  instead of saying `cli sync` moves the file, which it cannot do then.
- `./configure` deletes only the setup scripts it generated. A hand-written `setup_*_env.sh` for a
  name not in `paths.py` is kept and named: "⚠  Kept 'setup_mine_env.sh': 'mine' is not in
  paths.py, but ./configure did not write this file. Delete it if nothing uses it."
- A `paths.py` that does not parse stops `./configure` with "✗ paths.py line N: <message>" and the
  offending line, instead of a traceback. One that raises while it runs (a missing environment
  variable, a failed import) stops with "✗ paths.py line N: <ExceptionType>: <message>" and the
  line, exit 1.
- A tool's own `src/` comes before `libs/` and its package folders on the import path, so a
  `preprocess.py` or `validators.py` that a fetched folder carries never replaces the tool's own.
  `libs/` now goes at the end of the import path (see Changed).
- `./configure` no longer publishes a `.venv` that was moved or copied with the tool, whose
  activate script points at the old folder: "⚠  <new>/.venv was made for <old>/.venv; recreate it:
  rm -r .venv && uv sync, then run ./configure again. Until then it is not published as
  APP_VENV." A dependency's bundled virtualenv made elsewhere warns: "⚠  <venv> was made for <old>,
  so activating it does not work. Recreate it in <dep path> (rm -r .venv && uv sync), or run
  "$<DEP>_VENV/bin/python" directly."
- `./configure` reads `app_name` and `entry` in `'...'` as well as `"..."`, with tomllib or tomli,
  and line by line when the file does not parse (a UTF-8 byte order mark included).
- `cli sync` syncs a `pyproject.toml` that starts with a UTF-8 byte order mark, and keeps the mark.
  A `pyproject.toml` that is not valid TOML stops it before anything changes: "✗ pyproject.toml is
  not valid TOML: ...; fix it, then run cli sync again. Nothing was changed."
- `cli deps add` refuses the name `app`, whose settings would be the tool's own `APP_VENV` and
  `APP_ENTRY` in `.env`. A `paths.py` that `cli deps add` or `remove` cannot write (a read-only
  folder) gives "✗ Could not write 'paths.py': ..." and exit 1.
- `cli sync` prints the backup path of each replaced local edit in its summary, once the sync
  stands. A rollback deletes those backups, and the line used to name one that was gone.
- A deployed tool keeps its name. A tarball ships no `.env`, so `./configure` took `APP_NAME` from
  the install folder: `1.0.0` under the orchestrator's `<name>/<version>/`, `ng_<name>-1.0.0` by
  hand. `APP_NAME` names the dependency layout `<out>/<APP_NAME>/`, so a deployed tool wrote to
  `out/1.0.0/`. `cli generate` now records the name as `[tool.ngargparser] app_name` in
  `pyproject.toml`, which ships. `./configure` takes `APP_NAME` from `paths.py` first, then from
  `app_name`, then from the tool's own `.env` (older tools), then from the folder name, so an edit
  to `app_name` takes effect on the next `./configure`. `cli sync` adds `app_name` to older tools
  from `APP_NAME` in their `.env` (else the folder name) and never changes an existing one.
- `preprocess` works once a tool declares a dependency. With a filled-in dependency, preprocess
  creates `<out>/<dep>/` and `<out>/<APP_NAME>/`, but the `--inputs-dir` and `--params-dir`
  defaults still pointed at `<out>/predict-inputs/`, which that layout never creates. The sample app
  then failed with `FileNotFoundError: 'out/predict-inputs/data'`. The defaults now point at
  `<out>/<APP_NAME>/predict-inputs/`, and the sample app writes its predict and postprocess outputs
  under `<out>/<APP_NAME>/` too. Without dependencies, every path is unchanged.
- Dependency wiring follows a redeployed dependency. `setup_<dep>_env.sh` copied the virtualenv
  and start-up script a dependency publishes (`APP_VENV`, `APP_ENTRY`) as fixed text when
  `./configure` ran. A deploy puts a new version behind the same path (a `current` link) and never
  re-runs a dependent's `./configure`, so the caller kept activating the old version's virtualenv
  and called a renamed entry script that no longer existed ("can't open file"). The script now
  reads both from `<dep>_path/.env` when it is sourced, with the values `./configure` saw as the
  fallback. An explicit `<dep>_venv`, or a virtualenv found inside `<dep>_path`, stays fixed.
- `cli sync` refuses a folder that is not an ngargparser tool. It accepted any folder with `src/`
  and `scripts/`, and in an unrelated project it created `src/core/` and replaced the project's
  `Makefile`, exiting 0. It now also needs a `[tool.ngargparser]` table in `pyproject.toml` or
  `src/core/NGArgumentParser.py` (every tool has one; `scripts/core/build.sh` alone is not enough),
  and otherwise exits 1 without writing.
- The `scaffold_version` stamp can no longer corrupt `pyproject.toml`. A `[` between
  `[tool.ngargparser]` and `scaffold_version` (a `# see [release notes]` comment) hid the stamp, so
  sync appended a second `[tool.ngargparser]` table and `uv sync`, and with it every deploy, failed
  with "duplicate key". The stamp is now found line by line, added inside an existing table that
  lacks it, and the result is checked (with `tomllib` where available) before it is written; a
  failed check rolls the sync back.
- Build: Ctrl-C, a kill or a dropped ssh session (SIGHUP, exit 129) during `make build` removes the
  half-built copy and says the build failed; the exit trap used to see status 0 and leave it. A
  failed `scripts/get_libs.sh` that printed nothing says `(get_libs.sh printed nothing)`. A
  `libs/` entry fetched by a `get_libs.sh` you deleted still ships, and each build now says so. A
  symlink error for a link to a folder says that its `.distignore` rule needs no trailing slash
  (`/shared`, not `/shared/`). A failing `scripts/hooks.sh` shows its
  exit code and the last 20 lines of `.ngargparser/hooks.log` (its output went to /dev/null). A
  `.distignore` rule that excludes the entry script fails the build instead of skipping the startup
  check. File names holding `"`, `\` or a tab are now matched by `.distignore` (`git check-ignore`
  quoted them in its output, so they always shipped).
- Sync: `cli sync --dry-run` lists every legacy move a real run would make and the files it could
  not move. Sync moves a legacy `scripts/do-not-distribute.txt`, `do-not-distribute.txt` or
  `scripts/.distignore` to `.distignore` (the build stops on them), and warns when `.distignore`
  already exists. `cli generate` and each sync record the framework files' hashes in
  `.ngargparser/framework-hashes.json`; sync warns before replacing a framework file you edited and
  names the backup.
- Machine setup: `cli deps add` refuses a name that starts with a digit (`2fold_path=None` is not
  valid Python). `./configure --<dep>-<field>` rewrites the whole assignment, multi-line and
  annotated ones included, adds a field `paths.py` leaves out after its `<dep>_path` line, keeps
  `paths.py.bak`, and stops with exit 1 instead of claiming success when the variable is chained or
  inside an `if` (it used to leave a broken file, or print "Set" and write nothing). A tool's own
  `.env` now wins for every key it defines, so a tool started by another tool no longer sees the
  caller's `APP_VENV`, `APP_ENTRY` or dependency paths; `PATH`, `PYTHONPATH`, `PYTHONHOME`,
  `LD_LIBRARY_PATH` and `HOME` keep the environment's value, so a stale `PATH=` line in an old
  `.env` cannot break the tool. The default `.distignore` excludes `*.bak`.
- Parser API: the generated input group is required, so `predict` with no `-j`/`-t` is a usage
  error instead of a `TypeError` in the predict code; `remove_argument` drops a mutually exclusive
  group it leaves empty. A passed `--params-dir`/`--inputs-dir` reaches `run()` as `str`, like the
  default. `write_results` writes path values in JSON as text, numbers a repeated table type
  (`<prefix>.<type>-2.tsv`) instead of overwriting the first, and `-o` that ends in `/` or names an
  existing folder is a usage error (it wrote a hidden `<folder>/.tsv`).
- The once-a-day update notice no longer offers a pre-release tag left in the shared cache. Every
  installed `cli` uses `~/.cache/ngargparser/update-check.json`, and an older one that does not skip
  pre-release tags stored `v0.4.2-test`; the notice trusted it for a day ("0.4.1 → v0.4.2-test").
  A cached value that is not a release tag now counts as stale: the notice looks the latest release
  up again, and prints nothing if GitLab cannot be reached.
- The interactive `cli deps` menu exits 1 when it changed nothing because of an error (a refused
  name, a `paths.py` that does not parse, a failed write, Ctrl-C or Ctrl-D at a prompt, which
  print "Cancelled."), like
  `cli deps add/remove/list`; it always exited 0. Without a terminal (a script or CI job) it stops at
  once with "needs a terminal" and exit 1, instead of printing "Error creating paths.py: EOF when
  reading a line" and exiting 0.
- The example app's `postprocess --input-results-dir ... -o <prefix>` writes `<prefix>.json`; it
  crashed with `TypeError` when `-p` was not given. With neither `-o` nor `-p` it stops with a usage
  error.
- `cli sync --no-backup` holds when sync upgrades ngargparser first. The restarted sync got only
  `s` and carried `--no-verify` alone, so it wrote backups anyway; `--no-backup` now travels as
  `NGARGPARSER_SYNC_NO_BACKUP=1`.
- `cli sync` warns about a legacy file it cannot move because the new name already exists: a
  `scripts/build_hooks.sh` next to `scripts/dependencies.sh` or an existing `scripts/hooks.sh`
  (the build never runs it), or a `scripts/build.conf` next to `scripts/core/build.conf` (the build
  stops while it exists, saying sync would move it). These used to stay without a word.
- The build forces only the top-level `README`, `deploy/install.sh` and `.python-version` into
  the tarball, so a nested `README` or `deploy/` folder, and the other files in `deploy/`, follow
  your `.distignore`. Earlier rules also re-included a nested `README` or `deploy/` folder that
  `.distignore` excluded, and every file in `deploy/`.
- `./configure` without a `.env` names a tool in a folder like `ng_my-app-local` `my-app`; the
  pattern rejected hyphens, so the name stayed `ng_my-app-local`.
- The first-run `cli deps` menu, which creates `paths.py`, refuses names ending in `_lib`, `_venv`,
  `_module` or `_path`, as `cli deps add` does; it used to write them.
- Stale comments: `deploy/install.sh` no longer calls `./configure` a shebangless wrapper,
  `set_pythonpath.py` no longer says the build skips dotfiles, the known-clash text says
  "conflicting option string(s)", and a tarball-name branch that could never run is gone.
- `.distignore` judges folders the way git does. The build asked git about each folder with a
  trailing slash in an empty scratch repo, so a `folder/*` rule also matched the folder itself and
  the whole folder was dropped: the README's own recipe (`!.streamlit/`, `.streamlit/*`,
  `!.streamlit/config.toml`) shipped nothing from `.streamlit/`, and `data/*` with `!data/keep.txt`
  dropped `keep.txt`. The project's folders now exist in the scratch repo and are asked about
  without the slash, so `folder/*` matches only what is inside. Dir-only rules (`__pycache__/`)
  match as before. No current tool's tarball changes (bcell-sequence-input built with both versions:
  identical; `libs/` is staged entry by entry, so `/libs/*` with `!/libs/__init__.py` already worked).
- Docs: the reference says the `cli deps` / `./configure` dependency rule covers plain `<name>_path
  = value` lines, the form `cli deps add` writes, and annotated ones; `./configure` also sees
  `if`-nested, chained and unpacked assignments that `cli deps` doesn't list or remove. The
  `build.conf` template no longer claims an empty `TARBALL_PREFIX` means no prefix: the build falls
  back to `IEDB_`, which the deploy orchestrator expects.
- A `preprocess` that fails because `--params-dir` or `--inputs-dir` names a missing folder no longer
  leaves the `-o` folders behind. The named folders were checked only after the layout was created,
  so `-o out --params-dir ../typo-dir` and `-o out --params-dir out/custom-params` left `out/`. They
  are now checked first: a named folder must exist or be one the layout creates.
- The example app (`cli generate example`) gets the same base class as a blank tool. It shipped its
  own old copy of `NGArgumentParser.py`, without this release's fixes (a failed `preprocess` left
  its folders behind), and its `preprocess` called `create_job_descriptions_file`, a method only that
  copy had, so after `cli sync` replaced the copy it failed with `AttributeError`. The example's
  `src/preprocess.py` now writes `job_descriptions.json` itself, with the same content, as tools do.
- README: postprocess `-p` is optional, not "(required)"; `validate_file` checks the file exists, not
  that it is readable; `validate_directory` requires an existing directory and never creates one.
- `cli sync` no longer replaces a local checkout with the latest release. With ngargparser installed
  from a working copy (an editable install), a plain sync installed the latest tag over it and then
  copied that release's older framework files into the project, which passed the help-screen check,
  so newer fixes were reverted without an error. A plain sync from a checkout now skips the
  self-upgrade, says so, and syncs from the checkout. `--ref <version>`, `--dev`,
  `NGARGPARSER_UPGRADE_URL` and `cli upgrade` still install what they name, and released installs
  upgrade as before.
- Help screens no longer show sample or placeholder text. A new tool's starter parser described
  `--input-tsv`/`--input-json` as "Perform counting given a TSV/JSON file.", the letter-counter
  sample's wording (phbr-legacy shipped it); they now read "Input file in TSV/JSON format.". The
  base class printed "Here are list of valid subcommands." and "additional help" on every tool's
  top-level `-h`, and its default description was "Text to display before the argument help (by
  default, no text)"; all three are gone. The README a new tool gets shows the real `predict --help`
  output. The example app keeps its counting text.
- `.gitignore` keeps `./configure`'s output out of git. `.env` and `setup_<name>_env.sh` hold the
  paths of the machine that ran `./configure`, but the managed section only listed `.ngargparser/`
  and `*.bak`, so `git add .` committed them (9 of 12 tools didn't ignore the setup scripts, 3 didn't
  ignore `.env`). New projects get both rules. `cli sync` adds them to an existing project, including
  one whose managed section already exists, which sync used to leave alone. A rule the file already
  has (`.env`, `/.env` or `**/.env`), or re-includes with `!`, is not added again.
- `cli deps` and `./configure` agree on what a dependency is: a `<name>_path` variable whose
  `<name>` doesn't end in `_lib`/`_venv`/`_module`. `cli deps` used to key on the `''' [ Title ] '''`
  line, so on hand-edited files `list` showed filled paths as `None` (phbr, axelf), found no tools at
  all (peptide-variant-comparison, bcell-structure-input), `remove tcell_class_i` found nothing, and
  `remove "T Cell Class I"` reported "Removed 1" while changing nothing. Titles are now labels;
  `remove` deletes the block's exact lines and reports only what is gone; `add` refuses names
  `./configure` can't see as a tool (`netmhc_lib`) and never overwrites a `paths.py` that doesn't
  parse; `cli deps` exits 1 when something asked for didn't happen. `./configure` copies only plain
  values from `paths.py` into `.env`, so `from pathlib import Path` no longer writes
  `PATH=<class 'pathlib.Path'>` (loaded with override, that line emptied the program search path).
- `preprocess` no longer creates folders while it is still reading the command line. The `-o`
  check built the folder layout as soon as argparse read it, so a command that then failed (a
  missing `-j`) left the folders behind; `--params-dir`/`--inputs-dir` under `-o` worked only when
  given after `-o`; and `-o` pointing at a file printed five "Error creating directory" lines,
  then "Successfully created", and exited 0. The layout is now created in `parse_args` once the
  command line is valid, a folder that can't be created stops with a usage error, and `-o` that is
  a file is rejected up front. The layout, the values tools read and the printed lines are as
  before. `create_directory_structure_for_dependencies` takes an optional `errors` list.
- `./configure` works without activating the venv. The launcher ran `configure.py` with whatever
  `python3` came first on `PATH`, usually one without python-dotenv, so the Quickstart's
  `./configure` failed with `No module named 'dotenv'`. It now uses the tool's `.venv/bin/python`
  when there is one, else `python3`, so `configure.py` needs no exec bit, and a missing
  python-dotenv prints "run `uv sync` first" instead of a traceback.
- `./configure` reads `APP_NAME` from the project's own `.env` file. An `APP_NAME` passed down by a
  calling tool used to win and was written into this project's `.env`.
- `parse_args` takes an argument list (`parse_args(["predict", "-j", "in.json"])`), as argparse's
  does, so a tool can test its own parser; with no list it reads the command line as before. A
  tool that overrides `parse_args` keeps working, and passes the list on once its override takes
  `args=None, namespace=None`.
- The README's examples work when pasted into a freshly generated tool. The `--threshold -t`
  examples clashed with the template's `--input-tsv -t`; the custom-validator example lacked
  `import argparse` and referred to its function without `validators.`; and the template
  `src/validators.py` lacked `import argparse` and a final newline, so a pasted function could
  land on its last comment line. `tests/test_readme_examples.py` now runs every marked example
  (`<!-- example: ... -->`) against a fresh scaffold.
- `remove_argument` and `update_arguments` now change what parses, not only what `--help` shows.
  A removed option used to stay accepted, and re-adding it (the README's recipe for replacing an
  option) failed with "conflicting option strings"; removal now frees every name of the option.
  `update_arguments` used to accept a rename that never parsed, and a misspelled option or keyword
  did nothing silently. Both helpers now raise a clear error for a misspelled option (with a "did
  you mean" suggestion), a misspelled subcommand, a rename, or an unknown keyword. Calls that pass
  an option's existing names, as current tools do, behave as before. `cli sync` no longer reports
  a re-declaration that follows `remove_argument` of the same option as a clash.
- A tool started by another tool now uses its own `.env` for `APP_ROOT` and `APP_NAME`. Child
  processes inherit the caller's values, and every core module loaded `.env` without overriding
  them, so tc1 started by phbr put phbr's `libs/` on its import path: a crash when a module was
  missing, or a silently different library version. The new `src/core/app_env.py` loads the tool's
  own `.env` by its exact path (no more searching parent folders). Without a `.env`, an inherited
  `APP_ROOT` that points at another tool is dropped, so the tool stops with "not configured"; one
  that points at the tool itself (the build's startup check) is kept.
- The latest release no longer resolves to a pre-release tag. `v0.4.2-test` used to beat both
  `v0.4.1` and `v0.4.2`, because tags were compared as text after the version numbers, so every
  test tag became what `cli sync` and `cli upgrade` installed and what the update notice and
  `cli --version` pointed to. Tags with a `-` suffix (`-test`, `-rc1`) are now skipped; install one
  on purpose with `--ref`.
- `NGARGPARSER_NO_UPDATE_CHECK=0` no longer silences the update notice. The variable is now read as
  an on/off value: unset, empty, `0`, `false`, `no` and `off` (any case) leave the notice on, and
  anything else turns it off. Before, any non-empty value turned it off, so setting it back to `0`
  after `1` kept the notice hidden. Applies to both the `cli --version` check and the passive notice.
- `cli sync | head` no longer leaves framework files half updated. When the reader quits while sync
  runs, sync restores every file, exits 1 and says on stderr "✗ cli sync stopped: its output could
  not be written (Broken pipe). Every file was restored to its state before the sync." The undo
  failed on the same error, and the record of the unfinished sync was deleted, so the next sync said
  nothing needed updating. Output that breaks once the sync stands does not undo it.
- A full disk during `cli sync` no longer cuts `README.md`, `.gitignore`, `pyproject.toml`,
  `deploy/install.sh` or the hash record short: each is written through a temporary file renamed
  over it, like the framework files. A write that fails undoes the sync and names the file ("✗ Could
  not write README.md: File too large." then "Every file was restored to its state before the
  sync."). `README.md` was cut short, and sync went on with "Skipped updating README badge".
- `cli generate` stops and removes the partial tool when it cannot write a file it fills in, such as
  on a full disk: "✗ Error: [Errno 28] No space left on device: '<name>/src/run_<name>.py'" and "└
  Cleaned up the partial tool '<name>'." It printed "✗ An error occurred: ..." and went on, with the
  file half substituted.
- A `cli sync` marker whose pid now belongs to another process (pid 1, say), or that predates a
  restart, counts as a sync that ended, so the next sync reports the unfinished sync; the pid alone
  made it look alive, and the half-updated tool went unreported.
- A dependency `.env` written for another folder, as when the dependency was copied or moved after
  its `./configure`, is no longer used by `./configure`, the setup script or `app_env.py` until
  `./configure` runs there: "⚠ <dep>'s .env was written for <old>: run ./configure in <path> first;
  its values are not used until then." The entry script then comes from the dependency's own folder
  ("* <dep>: using start-up script found at ..."), and `dependency_command` says "<dep> publishes no
  entry script: <path>/.env was written for <old>. ..." when that folder names none. The caller ran
  the original's virtualenv and entry script without a word.
- `cli deps add` refuses a stub whose `None` would replace a setting written in a form `cli deps`
  does not read, such as inside an `if`: "✗ Not added: 'paths.py' already sets foo_path on line 52,
  in a form cli deps does not read (inside an if, or a chained or unpacked assignment), and the
  foo_path=None that cli deps adds below it would replace that value. ...". The stub's line came
  after it and replaced the value.
- `./configure --<dep>-...` keeps CRLF line endings in `paths.py`, and `cli deps add`/`remove` keep
  each line's own ending, in a file that mixes CRLF and LF too; a line they add gets the ending
  most lines have. Both wrote LF. `./configure` writes `paths.py.bak` through a
  temporary file, so a full disk never cuts it short.
- Setup scripts read a value with a ` # comment` after it in a dependency's `.env` as python-dotenv
  does, without the comment, so the shell and Python get the same value; the comment was part of it.
- A build stopped during the preflight (Ctrl-C, a kill, a dropped ssh session) cleans up and
  releases the lock: the signal traps are set before the lock is taken, and the Python and uv the
  build runs no longer hold the lock, so one that outlives a killed build cannot stop the next one.
- `make build-verbose` says "ERROR: scripts/hooks.sh failed (exit N)." when the hook fails, as `make
  build` does; it stopped with only "Build failed".
- `update_arguments` keeps an option in its place in its help section: `update_arguments("-o",
  help=...)` moved `-o` below `-f` in `predict -h`. A `group=` that names another section still
  moves the option to the end of that one.
- The end-user `README` a new tool gets installs with `uv sync --frozen --no-dev`, as
  `deploy/install.sh` does, in all three places. Its plain `uv sync` installed pytest and the rest
  of the `dev` group, which the same README says a deploy leaves out.
- The pre-commit mypy hook checks all 7 files `[tool.mypy]` names. The config's global exclude left
  out `NGArgumentParser.py`, `NGChildArgumentParser.py` and `templates/configure.py`.
- A lock file only its owner may write (mode 444 in a shared folder) no longer stops `make build` or
  `cli sync`: both open it read-only, which still locks, and leave the old pid in it. A new lock
  file is made with mode 0666, whatever the umask, so the next user can write their pid there. One
  that cannot be opened says who can fix it: "ERROR: cannot open .ngargparser/build.lock (Permission
  denied): ask its owner to run chmod a+rw .ngargparser/build.lock", and from `cli sync` "✗ Cannot
  open ..." or "✗ Cannot create .ngargparser/build.lock (Permission denied): ask the owner of
  .ngargparser to run chmod a+rwx .ngargparser. Nothing was changed." A busy message names no pid
  when the file holds no running one ("✗ A build or another sync is running in this folder. Nothing
  was changed. ...").
- `cli sync` with stdout closed (`cli sync >&-`) still undoes a sync that fails its test drive,
  and says why in one line on stderr: "✗ cli sync rolled back: ... Its output could not be written
  (stdout is closed). Every file was restored to its state before the sync."
- A `.venv/bin/python` that is not executable stops `cli sync` before it writes anything: "✗ Sync
  needs the tool's .venv to check that the tool still starts after the update, and
  .venv/bin/python is not executable. Run 'uv sync', then cli sync again (or cli sync --no-verify
  to update without the check). Nothing was changed."
- A `README.md` that is a link gets no badge, and one line says so ("└ ⚠ Skipped the README.md
  badge: README.md is a link to <target>, and sync does not write the badge through a link."). It
  no longer stops the sync's link check.
- An unreadable `.distignore` stops the build in the preflight, so the previous build stays:
  "ERROR: .distignore cannot be read (Permission denied): run 'chmod a+r .distignore' (or ask its
  owner to), then build again."
- A `./configure` flag that rewrites a `paths.py` line keeps the ` # comment` at its end.
- A dependency `.env` that is not UTF-8 text, or cannot be read, is ignored alike by `./configure`,
  `app_env.py` and the setup script, each naming the cause: "⚠  Could not read <path>/.env: it is
  not UTF-8 text. Its values are not used: save it as UTF-8, or run ./configure in <path> to write
  it again." (or "Permission denied. Its values are not used: ask its owner to make it readable.").
- A `paths.py` that is not UTF-8 text stops every `cli deps` form with one line and exit 1: "✗
  paths.py is not UTF-8 text (byte 0xe9 at offset 1220): save it as UTF-8, then run cli deps again;
  nothing was changed."
- `cli --version` with stdout closed (`cli --version >&-`) prints nothing and exits 0.

### Removed
- **Breaking:** `EXCLUDE_FROM_BUILD_SYMLINK` in `scripts/build.conf`. Everything is copied; a
  leftover setting prints a notice and has no effect.
- **Breaking:** cloning git URLs listed in `requirements.txt` at build time. `requirements.txt`
  now ships as an ordinary file. No current tool used this; put such clones in `scripts/get_libs.sh`.
- Docs: the reference's "Migrate older tools" section (removed layouts, the move to
  `scripts/get_libs.sh`, the pre-0.2.4 upgrade), "Adopt the default `.distignore`", and the other
  notes for tools generated by older releases. Tools move to this release by being refactored onto a
  freshly generated tool.

## [0.4.1] — 2026-08-31

### Added
- `./configure` publishes `APP_VENV` in a project's own `.env` when exactly one conventional
  virtualenv sits in the project root, next to `APP_ROOT` and `APP_NAME`. A project that
  depends on that tool reads the key instead of guessing, so a tool whose virtualenv lives
  outside its directory is configured once, in its own project, rather than again in every
  project that uses it. The dependency's `paths.py` is never read: it is executable Python,
  while `.env` is parsed as plain key/value text.
- Bundled-virtualenv detection covers `.venv`, `venv`, `env` and `.virtualenv`, in that
  order, so a tool that names its virtualenv something other than `.venv` is still found.
  Convention, not discovery: no directory scan, and two matches adopt neither, naming both
  and pointing at `--<name>-venv=` rather than binding the tool to whichever the filesystem
  listed first.
- A dependency that resolves no virtualenv from any source now warns that it will run under
  whatever Python is active, instead of being silently mismatched until prediction time.

### Changed
- A candidate virtualenv is accepted only when it holds both `pyvenv.cfg` and `bin/activate`.
  Previously `bin/activate` alone was enough, which would accept any directory that happened
  to contain one.

## [0.4.0] — 2026-08-23

### Added
- `./configure` now takes a flag for every field of every declared dependency —
  `--<name>-path`, `--<name>-venv`, `--<name>-module`, `--<name>-lib-path` — so tool paths can be
  set from a script or a one-liner instead of hand-editing `paths.py`. Values persist into
  `paths.py`. `./configure -h` lists the available flags with each field's current value. Flags
  exist only for dependencies `cli deps add` has declared, so a typo'd name is rejected rather
  than silently accepted. This replaces the `sed` line deploy scripts currently use to patch
  `paths.py` on the target host.
- A tool that carries its own virtualenv at `{tool_path}/.venv` — anything built by `uv sync`,
  which is every ngargparser project — is now used automatically when the dependency's `_venv`
  field is empty. The value is re-derived on every run and deliberately **not** written into
  `paths.py`, so one `paths.py` works unchanged across dev laptop, dev server, and SDSC, each
  resolving its own local `.venv`. An explicit `_venv` always wins, so pyenv and conda
  environments (which live outside the tool directory, under their own names) are untouched.
  Detection checks for `bin/activate` rather than just the directory, so an empty leftover
  `.venv/` can't yield a broken setup script.
- `./configure` warns when a configured `_path`, `_venv`, or `_lib_path` directory doesn't
  exist. Advisory only: `.env` and the per-tool shell scripts are still written and the exit
  code is unchanged, so a path that is valid on the deploy target but absent on the machine
  running `./configure` still works.

### Fixed
- `predict` silently accepted `--input-tsv` and `--input-json` together. The scaffold's
  `if/elif` chain then used the TSV and dropped the JSON, exiting 0 with believable output
  computed from only one of the two inputs — in a batch pipeline nothing downstream could
  detect it. They are now a real mutually exclusive group, so argparse rejects the pair with
  exit 2 and the usage line advertises `(--input-tsv | --input-json)`. Whether an input is
  *required* remains the tool developer's decision; the group is not marked required.
- `predict`'s input arguments rendered *below* the output arguments in `--help`. The base
  class now creates the input group before adding the output arguments (argparse orders
  groups by creation, not by when they are populated) and exposes it as
  `predict_input_group` for child parsers to populate or retitle. An unpopulated group
  renders nothing, so tools that declare no inputs are unaffected.
- `postprocess`'s `--postprocessed-results-dir/-p` sat in a group titled "other required
  parameters" while being declared without `required=True`, so the help text asserted
  something argparse never enforced. The title was the error, not the flag: `axelf` accepts
  `-o` in its place, and `conservancy` and `rate` fall back to the working directory. It now
  lives in the existing "optional parameters" group alongside `-o`/`-f`. Parsing is unchanged.
- The `./configure` launcher silently discarded every argument. It was a plain line of text
  with no shebang and no `"$@"`, so the shell ran the inner script with no arguments at all —
  `./configure --help` and every other flag were dropped without a word. It is now a real
  `#!/bin/sh` script that forwards its arguments, and it is sync-managed, so `cli sync` repairs
  it in projects scaffolded before this fix.
- `.env` was truncated and rewritten on every `./configure` run even when the resulting content
  was byte-identical, and reported "updated" regardless — the label reflected only whether the
  file had existed beforehand, not whether anything changed. It is now written only when the
  content actually differs, and reports `unchanged` otherwise.
- The "path is None" error now names the exact `./configure --<name>-path=<path>` command to fix
  it, instead of saying only that the path should be set.
- A project missing `paths.py` (deleted, or scaffolded before this fix) crashed with an
  unhandled `FileNotFoundError` the moment `preprocess --output-dir` was parsed.
  `core_validators.py`'s `get_dependencies_from_paths` and
  `create_directory_structure_for_dependencies` both raised on a missing file instead of
  treating it as the zero-dependency case an *empty* `paths.py` already handled correctly.
  Both now treat "missing" the same as "empty" — genuinely unexpected read errors (e.g.
  `paths.py` turning out to be a directory) still propagate. Existing projects pick this up
  via `cli sync` / `cli upgrade`.

### Changed
- `cli generate` no longer writes an empty `paths.py` into new projects. It was only ever a
  placeholder for `cli deps add` to fill in later, and pre-creating it turned out to be
  unnecessary now that a missing `paths.py` is a fully supported state (see Fixed, above) —
  the rest of the framework (`./configure`, `cli deps add/remove/list`) already treated a
  missing file as valid. `cli deps add` still creates the file the first time you declare a
  dependency.
- **Removed the `postprocess_required_group` attribute** from the base class, since `-p` moved
  into the existing optional group (see Fixed, above). Nothing in ngargparser or the sibling
  IEDB tools referenced it, but it was a public attribute of a class tools subclass: a parser
  that called `self.postprocess_required_group.add_argument(...)` will raise `AttributeError`
  after `cli sync`. Use `self.postprocess_optional_group`, or add a group of your own.

## [0.3.5] — 2026-07-29

### Fixed
- An unconfigured project now says so instead of raising a `pathlib` `TypeError`.
  `src/core/set_pythonpath.py` ran `Path(os.getenv('APP_ROOT'))` unguarded, so a tree with no
  `.env` — every freshly extracted build tarball (`build.sh`'s `"$PROJECT_ROOT"/*` glob skips
  dotfiles) and every fresh clone (`.env` is gitignored) — died with
  `TypeError: expected str, bytes or os.PathLike object, not NoneType` several frames deep in
  `pathlib`. It now exits 1 with the project root and the `./configure` command to run. An empty
  `APP_ROOT=` is caught too; previously it yielded `Path('')` and a silently wrong `libs/` path.
  Existing projects pick this up via `cli sync` / `cli upgrade`.

## [0.3.4] — 2026-07-17

### Fixed
- An exclusion file (`.distignore` / legacy `do-not-distribute.txt`) whose **last line
  lacks a trailing newline** no longer silently drops that line's rule. `build.sh`
  composes the throwaway `.gitignore` by concatenating the user's file with forced
  contract rules; without a terminating newline the final user pattern fused with the
  appended `!README` (e.g. `**/*.json!README`), disabling both. `setup_exclusions` now
  terminates the user's chunk before appending the contract rules.

## [0.3.3] — 2026-07-17

### Fixed
- `cli upgrade --check` no longer reports `No semver tags on remote` when the remote is merely
  unreachable. A network/timeout failure while resolving the latest tag now prints
  `Couldn't reach the remote to check for the latest release` and exits non-zero (2), instead of
  silently resolving `latest` to `master`. A real upgrade aborts on such a failure rather than
  installing bleeding-edge `master` behind your back (`--dev` / `--ref master` still opt in
  explicitly).

## [0.3.2] — 2026-07-17

### Added
- **The tarball exclusion file now has exact `.gitignore` semantics.** `build.sh` evaluates
  it with `git check-ignore`, so `.gitignore` content can be pasted verbatim: wildcards, dir-only
  `name/`, `!` negation, `/` anchoring, and `**` all match at every depth of the project tree.
  Previously only exact top-level basenames matched — wildcard entries like `**.sh` and `build/*`
  were silently dead.
- **Renamed the exclusion file to `.distignore` at the project root** (next to `.gitignore`),
  replacing `scripts/do-not-distribute.txt`. New scaffolds write `.distignore`. The legacy name is
  still honored as an alias — existing projects keep working with no change, and `cli sync` performs
  no migration (the file is user-owned). If both exist, root `.distignore` wins.
- Built-in `.*` baseline: hidden files/dirs are excluded from the tarball by default (lowest
  precedence — re-include with a `!` rule, e.g. `!.env` or `!.streamlit/`). The project-root
  `.git/` and `build/` are always excluded; `README` and `deploy/install.sh` are always included
  (the deploy orchestrator's tarball contract — exclusion rules, including the default `*.sh`,
  cannot strip them).
- First behavioral test coverage of `build.sh` (`tests/test_build_exclusion.py`): runs the real
  build in a scaffolded project and inspects the staged tree and tarball contents.
- `git` is now a hard build prerequisite (clear preflight error if missing); CI pytest images
  install it.

### Fixed
- The build tooling no longer ships in tarballs: `scripts/core/build.sh`, `scripts/hooks.sh`, and
  `scripts/do-not-distribute.txt` previously leaked into every release because exclusions were
  only applied to top-level items, never inside `scripts/` (or any other directory reached
  through a whole-dir symlink). Nested `__pycache__/` dirs and stray dotfiles are filtered now
  too.

### Changed
- **Migration note for existing projects** (`do-not-distribute.txt` is user-owned and never
  synced; the new engine arrives via `cli sync` updating `scripts/core/build.sh`): old literal
  lists keep working, but patterns now match recursively — a legacy `**.sh` entry that never
  matched anything will now exclude every `.sh` file at any depth (except the always-included
  `deploy/install.sh`). Projects that ship runtime `.sh`/`Makefile` files under `libs/` or `src/`
  must add `!` re-include rules (e.g. `!libs/**/*.sh`).
- Directories emptied by exclusion no longer appear in the tarball; genuinely empty source
  directories still ship.

## [0.3.1] — 2026-07-03

### Changed
- `cli upgrade` now reliably reminds you to run `cli sync` after installing. Previously the reminder
  only appeared when you upgraded from *inside* a project whose framework files were behind — so
  upgrading the tool globally (the common case) gave no hint, and a project could silently keep
  running old framework files. Now: from inside a stale project it names both versions
  ("You upgraded the CLI to Y, but this project is still on X — run `cli sync` here") — including
  when the tool was already current but the project is behind — and from anywhere else it prints a
  generic reminder ("`cli upgrade` updates the CLI, not your projects"). Also covers local-checkout
  installs (`cli upgrade --ref .`).
- The `--help` contact block now points at the IEDB Discussion Forum
  (https://discuss.iedb.org) instead of the `help@iedb.org` email, in both the framework base class
  (`NGArgumentParser.py`, synced into projects as `src/core/NGArgumentParser.py`) and the
  example-app scaffold template.

## [0.3.0] — 2026-07-03

### Added
- Shared result serializer `core.result_writer.write_results` (synced into `src/core/result_writer.py`):
  renders the standard result envelope to **tsv** (default) or **json**, to stdout when no
  `-o` is given or to `<prefix>.<ext>` when it is. Multiple tables are separated on stdout by
  `--- <type> ---` banners and written one-file-per-table as `<prefix>.<type>.tsv`; a single
  table gets no banner and one `<prefix>.tsv`. json always preserves the full envelope
  (warnings/errors and per-table metadata). `cli generate` and `cli sync` now install/refresh it.

### Changed
- **Result output is now tsv-first and framework-owned.** `predict`'s `--output-prefix/-o` and
  `--output-format/-f` moved into the base class `add_predict_subparser()`, so they are inherited
  (and `cli sync`-able) rather than redefined per tool. `-f` is now a real choice `{tsv,json}`:
  `predict` defaults to **tsv**, `postprocess` stays **json** (its aggregated envelope carries
  metadata tsv can't represent). The `-f` metavar was dropped so usage renders `{tsv,json}`, and
  its help shows the default inline via `%(default)s`.
- Reference templates (`run_aa_counter.py`, `run_app.py`, `postprocess.py`) now serialize through
  `core.result_writer.write_results` instead of ad-hoc `json.dump`/`print`.

### Removed
- The pseudo `table` output format / terminal table rendering. `-f` accepts only `{tsv,json}`.

### Fixed
- `predict`'s `-f` help previously advertised `Default=tsv` while the code defaulted to `json`.
  The default is now genuinely tsv and help/behavior agree.

### Upgrading
- **Breaking for tools that define their own predict `-o`/`-f`.** After `cli sync`, remove the
  `--output-prefix`/`--output-format` arguments from your `*ArgumentParser.py` predict subparser
  (they are now inherited) — otherwise argparse raises a duplicate-option error. If your tool needs
  a different default, help text, or choices, keep the inherited argument and adjust it **in place**
  with `self.parser_predict.update_arguments("--output-format", "-f", default="json", ...)` rather
  than re-declaring it (see README → "Updating inherited arguments"). Replace any hand-rolled result
  serialization with `from core.result_writer import write_results`. Tools not built on ngargparser
  (optparse-based) must port their output layer manually.

## [0.2.4] — 2026-06-26

### Added
- `cli upgrade` (alias `u`): a standalone, discoverable command that self-updates the installed
  `ngargparser` tool to the latest release tag on GitLab. Works from any directory and never touches
  project files. `--check` reports installed-vs-latest without installing; `--ref <tag/branch/sha>` pins a
  specific version (and `--ref .` / `--ref <path>` installs from a local working-tree checkout, for
  contributors); `--dev` tracks `master`. uv-tool aware — picks `uv tool install`, `python -m pip`, or
  `uv pip install` per environment, so it works on uv-tool installs, pip venvs, and pip-less uv-managed
  venvs alike.
- Passive update notifier: any `cli` invocation — including `cli --version` and `cli --help` — occasionally
  prints a one-line "a newer ngargparser is available" notice on stderr. Throttled to one remote check per
  day (cached at `~/.cache/ngargparser/update-check.json`), shown only in an interactive terminal, and
  suppressed in CI, when piped, during `cli upgrade`, or when `NGARGPARSER_NO_UPDATE_CHECK=1`.
- After a successful `cli upgrade` run from inside a scaffolded project that's behind, a one-line nudge to
  run `cli sync` to update that project's framework files.

### Changed
- `cli sync` and `cli upgrade` share one upgrade path (`_resolve_upgrade_url` + `_run_self_upgrade`);
  `cli sync`'s self-upgrade behavior is unchanged.

### Fixed
- Build no longer breaks when `TOOL_VERSION` contains a slash (e.g. a `feature/foo` branch passed by CI);
  `scripts/core/build.sh` sanitizes `/` → `-` before building.

### Removed
- `cli config-paths` (and its `c` alias) — deprecated since 0.2.0; use `cli deps` (`add` / `remove` / `list`).

## [0.2.3] — 2026-06-25

### Fixed
- `cli sync` (`cli s`) self-upgrade no longer fails with "No module named pip" on `uv tool`
  installs. uv-tool environments have no pip; sync now detects the uv-managed env and upgrades
  via `uv tool install --force --reinstall`, falling back to `python -m pip` for pip/pipx
  installs. On failure it prints the manual `uv tool install` command and how to skip the
  upgrade (`cli s --no-upgrade`).

## [0.2.2] — 2026-05-21

### Added
- `cli sync` (`cli s`) now self-upgrades the installed `ngargparser` package and re-execs before syncing project templates; a single `cli s` invocation handles both halves of a version bump (no more manual `pip install --upgrade` step).
- `cli sync --ref <git-ref>` selects the upgrade source. Default is `latest`, resolved via `git ls-remote --tags` to the highest semver tag on the remote (falling back to `master` if no tags exist). Pass a branch / tag / sha (e.g., `--ref v0.2.0`) to override.
- `cli sync --dev`: shortcut for `--ref master`; pulls the bleeding-edge tip of `master` instead of the latest tagged release. Overrides `--ref` if both are given.
- `cli sync --no-upgrade` (and `NGARGPARSER_NO_SELF_UPGRADE=1`): skip the self-upgrade step for offline / CI runs. `NGARGPARSER_UPGRADE_URL` overrides the full upgrade URL.

## [0.2.1] — 2026-05-21

### Changed
- `src/core/configure.py` (template): missing `paths.py` is now a neutral info line (`ℹ`), not a red `✗`. It's the normal state for tools with no external IEDB-tool dependencies. The follow-up message no longer claims `paths.py` is "empty" when it's actually absent — one accurate line covers both cases.

### Fixed
- `src/core/configure.py` (template): a declared dependency with a `None` / empty `_path` is now a real error. The message points at the specific variable and suggests `cli deps remove <name>` to drop the dep, and `./configure` exits non-zero so CI and shell pipelines can detect a misconfigured tool. Previously it printed a red line but exited 0, letting misconfig propagate silently.

## [0.2.0] — 2026-05-12

### Added
- `deploy/install.sh` scaffold for the nxg-tools-deployments deploy contract; `cli generate` writes it for new projects and `cli sync` creates it once in legacy projects (never overwritten).
- `[tool.ngargparser] scaffold_version` stamp in scaffolded `pyproject.toml`; `cli sync` keeps it current and uses it to drive future version-aware migrations.
- Scriptable `cli deps add/remove/list` for managing external tool deps in `paths.py`.
- README "Prerequisites" section pointing at the `uv` installer; install-from-GitLab guidance via `uv tool install 'git+ssh://git@gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git'` (HTTPS form also documented).

### Changed
- **Workflow is now `uv`-first.** Framework install, scaffold venvs, and run commands all assume `uv` (`uv pip install`, `uv sync`, `uv run`). Framework `requires-python = ">=3.8"`; scaffolds pin `>=3.11,<3.12`.
- `cli generate` is now **non-interactive** — no prompts, fully scriptable.
- `cli config-paths` renamed to `cli deps`. (See *Deprecated*.)
- Build default flipped: `scripts/core/build.sh` now **copies** the source tree by default; symlinks are opt-in via `EXCLUDE_FROM_BUILD_SYMLINK`.
- `scripts/build.sh` moved to `scripts/core/build.sh` (mirrors the `src/core/` ownership boundary). `cli sync` migrates legacy projects.
- `scripts/dependencies.sh` renamed to `scripts/hooks.sh`. `cli sync` migrates legacy projects.
- `build.conf` knob `BUILD_SYMLINK_SRC_DIRS` renamed to `EXCLUDE_FROM_BUILD_SYMLINK` (inverted semantics — list items to *copy*, not symlink).
- Framework's own packaging migrated from `setup.py` + `setup.cfg` to `pyproject.toml`.
- README restructured around the first-time-reader path (Install → Quickstart → layout → workflow → deps → build → upgrade → reference).

### Deprecated
- `cli config-paths` — kept as an alias that prints a warning and forwards to `cli deps`. Will be removed in a future release.

### Removed
- `setup.py`, `setup.cfg` (replaced by framework `pyproject.toml`).
- `build.conf` knobs `BUILD_ENTRY_SCRIPT`, `BUILD_COPY_TOPLEVEL_FILES`, `APP_NAME_NORMALIZED`, and `TOOL_NAME` (the last now composes from `APP_NAME` directly).

### Upgrading from 0.1.x
Run `cli sync` from inside any existing project. It migrates the renamed files (`scripts/build.sh` → `scripts/core/build.sh`, `scripts/dependencies.sh` → `scripts/hooks.sh`), refreshes framework-owned files in `src/core/` and `scripts/core/`, stamps `scaffold_version`, and creates `deploy/install.sh` if missing. User-owned files (`paths.py`, `hooks.sh`, `build.conf`, `validators.py`, `<App>ArgumentParser.py`) are never touched. If you have scripts that call `cli config-paths`, update them to `cli deps` — the alias works but warns.
