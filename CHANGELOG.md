# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Each tool publishes its start-up script as `APP_ENTRY` in its `.env`. `cli generate` writes
  `APP_ENTRY=src/run_<name>.py`; `./configure` keeps a value already in `.env` (never one inherited
  from the environment) or uses the single `src/run_*.py`, and warns when it can't choose. For each
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
    for code a framework release is known to break and stops with the file, line numbers and
    fix. First entry: a project that declares `--output-prefix` / `--output-format` on `predict`,
    or reuses `-o` / `-f` there, which the base class has owned since 0.3.0. `--dry-run` reports
    it too.
  - Test drive: when a framework file is about to change, sync runs the project's four help
    screens before and after, using the project's own `.venv` interpreter. This executes project
    code, and only when `src/` holds exactly one `run_*.py`.
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
  shows the pattern for those). `make build` runs it at the start, in an empty temp dir, and moves each
  top-level entry it leaves into the project's `libs/`. The fetch is skipped while get_libs.sh is
  unchanged and its entries are still in `libs/` (state in `.ngargparser/libs-stamp`; to force a
  fetch, delete the fetched folders in `libs/`). If the stamp is lost, the build reads which `libs/`
  entries it owns from the managed `libs/.gitignore`, so it can still replace them. A failed fetch leaves `libs/` as it was and stops the build. Fetched directories
  get `__init__.py` files, and a managed `libs/.gitignore` keeps them out of git. `cli generate`
  creates the template; `cli sync` does not add it to existing projects.
- `make build` checks that the tool starts: it runs the entry script's `--help`, `predict --help`,
  `preprocess --help` and `postprocess --help` from the build dir with the project's `.venv` and
  `APP_ROOT` set to the build, before packing. A crash fails the build and shows the last error
  line. No `.venv`, or more than one `src/run_*.py`, skips the check with a notice.
- `make build` checks every symlink in the build: each must be relative and resolve inside it.
  Absolute, dangling, looping and escaping links fail the build and are listed.

### Changed
- The `--help` contact block drops the retired IEDB Help Desk (https://help.iedb.org/) and lists
  only the IEDB Discussion Forum, in both the framework base class (`NGArgumentParser.py`) and the
  example-app scaffold template.
- `cli --version` and `cli --help` now check the latest release tag on GitLab on every run and
  report any mismatch on stderr: the "a new ngargparser is available" notice, with a link to the
  release notes, when the installed version is behind, or a one-line note when it is ahead of the
  latest tag. This check uses no cache and no once-a-day limit, and it also runs when output is not
  a terminal. stdout is unchanged: exactly `cli X.Y.Z`, or the same help text. An unreachable
  remote is reported in one line and never changes the exit code. CI and
  `NGARGPARSER_NO_UPDATE_CHECK=1` skip the lookup. Expect both commands to take about 2 seconds
  longer. Subcommand help, bare `cli`, and the passive notice on other `cli` commands are unchanged.
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

### Fixed
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

### Removed
- **Breaking:** `EXCLUDE_FROM_BUILD_SYMLINK` in `scripts/build.conf`. Everything is copied; a
  leftover setting prints a notice and has no effect.
- **Breaking:** cloning git URLs listed in `requirements.txt` at build time. `requirements.txt`
  now ships as an ordinary file. No current tool used this; put such clones in `scripts/get_libs.sh`.

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
