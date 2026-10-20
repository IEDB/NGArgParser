# ngargparser internals

This page describes how `make build`, `./configure` and `cli sync` work inside: the build pipeline and its checks, the build lock, the `libs/` stamp, the order of the writes, the sync's safety net, signals, and the version stamp. You need it to debug a build or a sync, not to write a tool.

The [README](../README.md) walks you through a tool. The [reference](reference.md) lists the API and the settings, and the [parser cookbook](parser-cookbook.md) has recipes.

## Contents

1. [Build pipeline](#build-pipeline)
2. [get_libs internals](#get_libs-internals)
3. [./configure internals](#configure-internals)
4. [Sync internals](#sync-internals)
5. [Signals](#signals)

## Build pipeline

`scripts/core/build.sh` runs these steps. `make build` runs it as `bash ./build.sh`, so the file needs no exec bit:

1. Preflight, before anything is created or removed: take the folder's build lock, read `build.conf` in a subshell ([Build settings](reference.md#build-settings)), and run the checks in [Preflight stops](#preflight-stops).
2. Set up `build/<TOOL_NAME>-<version>/`, remove the previous tarball of the same name, and write `.ngargparser-build` into the build copy.
3. Fetch dependencies with `scripts/get_libs.sh` into `libs/`, unless nothing changed ([get_libs internals](#get_libs-internals)).
4. Evaluate `.distignore` ([.distignore rules](reference.md#distignore-rules)) and print what it excludes.
5. Copy the tool into the build copy, skipping excluded paths. The build works out the whole copy in one pass: a folder with nothing excluded inside is copied whole, and the entries of each other folder go in one `cp` call, so a large folder with one excluded file copies fast. Symlinks are copied as links, never followed. Then strip `.git`, `__pycache__`, `.github` and `.gitlab` from `libs/` entries, add `__init__.py` files, and check that the files a deploy needs are there.
6. Check symlinks: each must be relative and resolve inside the build, or the build fails and lists them ([.distignore rules](reference.md#distignore-rules) shows the message).
7. Write the version: replace every `TOOL_VERSION` in the build copy's `README` with the version.
8. Run `scripts/hooks.sh` against the copy, if the tool has one ([Hook variables](reference.md#hook-variables)).
9. Write `VERSION` (the version, then the build date) at the top of the build copy, write `.ngargparser-build` again, and delete `._*` files. A `VERSION` file of your own is replaced, with a notice: `Notice: the build writes VERSION; the tool's own VERSION file is replaced in the tarball.`
10. Check symlinks again, so links a hook created are checked too, and check that the hook removed no file a deploy needs.
11. Run the [startup check](#startup-check), check that its help screens removed no file a deploy needs and changed no other file, then remove what they created in the build copy.
12. Pack the tarball (symlinks stored as links), then make the build copy's files read-only.

`make libs` runs step 3 alone, under the build lock ([scripts/get_libs.sh](reference.md#scriptsget_libssh)).

A build that fails after step 1 removes its build copy, so no half-built copy or old tarball looks fresh. A step 1 stop comes before anything is removed, so the previous build copy and tarball stay. [Signals](#signals) covers a build you stop.

In progress mode (`make build`), an error clears the progress bar line first, so the message starts on a line of its own. The bar is drawn only when stdout is a terminal and `NO_COLOR` is empty; otherwise progress mode prints each step as a line.

`TOOL_NAME` and `TOOL_VERSION` name the build copy, which a failed build removes, so each must be one plain folder name. After a `/` in `TOOL_VERSION` becomes `-`, each may hold only letters, digits, `.`, `_`, `+` and `-`, and may not be `.` or `..` alone or start with `-`:

```
ERROR: TOOL_NAME '../../x' is not allowed: use only letters, digits, '.', '_', '+' and '-' (not '.' or '..' alone, and not starting with '-'). It comes from scripts/core/build.conf, or is ng_<tool folder name> when build.conf does not set it.
```

`.ngargparser-build` holds `TOOL_NAME=` and `TOOL_VERSION=` lines. It ships in the tarball, so a deployed copy has it too, and `cli sync` refuses any folder that has it. The build writes it twice, so a hook cannot drop or change it.

`make build` needs `git` even when the tool is not a git repository.

### Preflight stops

The preflight stops the build, in this order, before anything is created or removed, so the previous build copy and tarball stay:

| Stop | Message |
|---|---|
| `scripts/core/build.sh` has Windows line endings, which `make` checks before it runs the file | `ERROR: scripts/core/build.sh has Windows line endings (CRLF): convert it with 'sed -i "s/\r$//" scripts/core/build.sh' (and add '*.sh text eol=lf' to .gitattributes)` |
| `git` is missing | `ERROR: 'git' is required to build (distignore matching uses 'git check-ignore'). Install git and retry.` |
| `.ngargparser` is a file | `ERROR: Cannot create .ngargparser/build.lock: .ngargparser is a file, not a folder. Move it away (mv .ngargparser .ngargparser.old), then run the command again. Nothing was changed.` |
| Another build, or a `cli sync`, runs in the same tool folder | `ERROR: Another build or a cli sync is running in this folder (pid 4242). Wait for it to finish.` ([Build lock](#build-lock) has the other forms) |
| `.ngargparser/build.lock` cannot be opened | `ERROR: Cannot open .ngargparser/build.lock (Permission denied): ask its owner to run chmod a+rw .ngargparser/build.lock. Nothing was changed.` |
| `scripts/core/build.conf` cannot be read | `ERROR: scripts/core/build.conf cannot be read (Permission denied).` |
| `scripts/core/build.conf` has Windows line endings | `ERROR: scripts/core/build.conf has Windows line endings (CRLF): convert it with 'sed -i "s/\r$//" scripts/core/build.conf' (and add 'build.conf text eol=lf' to .gitattributes)` |
| `scripts/core/build.conf` exits, or a command in it fails | `ERROR: scripts/core/build.conf exited (code 3) while being read: it may only set variables.` An `exit 0` stops the build too |
| `TOOL_NAME` or `TOOL_VERSION` is not allowed | The message above |
| `TARBALL_PREFIX` is not allowed | `ERROR: TARBALL_PREFIX '../x' is not allowed: use only letters, digits, '.', '_', '+' and '-' (not starting with '-' or '.'), or leave it empty for IEDB_. It comes from scripts/core/build.conf.` |
| The tarball name would be too long for a file name | `ERROR: TOOL_VERSION is 240 characters long, which makes the tarball's name 265 characters, and at most 246 fit (a file name may have 255 bytes, and the build first writes .<name>.partial): use a TOOL_VERSION of at most 221 characters. ...` |
| A `cli sync` did not finish here: its folder in `.ngargparser/sync-in-progress/` is still there, and the framework files may be half updated | `ERROR: a cli sync did not finish here (.ngargparser/sync-in-progress/20261018T192120.788048Z-4242/): run cli sync to see what to do, then build again.` ([Unfinished sync recovery](#unfinished-sync-recovery)) |
| `.distignore` cannot be read | `ERROR: .distignore cannot be read (Permission denied): run 'chmod a+r .distignore' (or ask its owner to), then build again.` |
| `scripts/get_libs.sh`, `scripts/hooks.sh`, `deploy/install.sh` or `configure` has Windows line endings | The message above, with `*.sh text eol=lf`, or `configure text eol=lf` for `configure`: `ERROR: configure has Windows line endings (CRLF): convert it with 'sed -i "s/\r$//" configure' (and add 'configure text eol=lf' to .gitattributes)` |
| `libs` is a symlink, or a file | `ERROR: libs/ is a symlink (-> ../shared-libs). Replace it with a real directory: the build copies libs/, and fetched dependencies are written into it.`, or `ERROR: libs exists but is not a directory.` |
| `build` is not a folder | `ERROR: build is not a folder: make build writes its output into build/, and make clean deletes build/. Move it away (mv build build.old), then run the command again. Nothing was changed.` |
| A [file a deploy needs](reference.md#files-a-deploy-needs) is missing, or `.distignore` excludes it | One line per file, with the reason |
| `configure` is not executable | `ERROR: configure is not executable: run 'chmod +x configure' and commit the mode (git update-index --chmod=+x configure)` |
| `pyproject.toml` does not parse | `ERROR: pyproject.toml does not parse (a merge conflict marker at line 3): fix it, then build again` |
| `entry` names a missing file or a folder, is not a quoted path, or points outside the tool | `ERROR: entry = "src/run_gone.py" under [tool.ngargparser] in pyproject.toml names a file that does not exist.` |
| `entry` is empty | `ERROR: entry = "" under [tool.ngargparser] in pyproject.toml is empty: name the entry script, or delete the line.` |
| `.distignore` excludes the entry script: the one `entry` names, or with no `entry`, the only `src/run_*.py` | `ERROR: src/run_my_app.py is excluded by .distignore, so the tarball would have no entry script. Remove the rule that excludes it.` |
| `uv.lock` does not parse: a merge conflict, a cut-off file or an empty one | `ERROR: uv.lock does not parse (a merge conflict marker at line 3), so 'uv lock' cannot read it either: restore it (after a merge: git checkout --theirs uv.lock) or delete it, then run 'uv lock' and commit uv.lock` |
| `uv.lock` is TOML, but uv rejects what it holds, such as a version changed by hand without its wheels | The same line with `(uv rejects what it holds: see below)`, then uv's first lines, indented ([Lock check](#lock-check)) |
| `uv.lock` is out of date | See [Lock check](#lock-check) |
| `NGARGPARSER_STARTUP_CHECK_TIMEOUT` is not a number of seconds above 0 and at most 86400 | `ERROR: NGARGPARSER_STARTUP_CHECK_TIMEOUT=abc is not a number of seconds above 0 and at most 86400: set it to the seconds each help screen of the startup check may take, such as 120, or unset it for 60.` |
| A name that would ship holds a line break, or a folder that would ship cannot be read | `ERROR: These names in the tool folder hold a line break, which the build cannot copy: $'src/bad\nname'. Rename them, or exclude them in .distignore. Nothing was changed.`, or `ERROR: The build may not read these folders, which would ship: data/private. Make them readable (chmod -R u+rX <folder>), or exclude them in .distignore. Nothing was changed.` |

The preflight also prints these notices, which do not stop the build. The first is for a tool without `uv.lock`, whose deploy then runs a plain `uv sync --no-dev`. Act on each before you deploy:

```
Notice: the tool has no uv.lock, so the deploy installs the newest versions pyproject.toml allows: run 'uv lock' and commit uv.lock.
Notice: README still has template placeholders (<Describe ...>): fill them in before you deploy.
Notice: the description in pyproject.toml still starts with TODO: fill it in before you deploy.
```

`pyproject.toml` is read with Python's tomllib, which gives the reason. A merge conflict marker stops the build at once. For another parse error, uv decides when it is on the PATH: the build stops only when `uv lock --check --offline` cannot parse the file either, so a file uv reads (such as an inline table split over lines) still builds. Without uv, tomllib decides alone. Without a Python that reads TOML, uv's message gives the reason, such as `(TOML parse error at line 4, column 5: string values must be quoted, expected literal string)`.

### Build lock

One build runs at a time in a tool folder, because builds share `libs/` and `build/`, and `cli sync` takes the same lock. The lock is `.ngargparser/build.lock`, held with `flock` where the system has it, and ends with the build, so it never goes stale. The build writes its pid into the file, for the message of a second build.

In a tool folder that several people share, a new lock file is made readable and writable for everyone (mode 0666), whatever the umask. A lock file that is only readable to you still locks, since `flock` needs no write access; your build then leaves the old pid in it. A lock file you cannot open at all stops the build with the message in the table above. Without the lock file, in a `.ngargparser/` you cannot write, the line reads `ERROR: Cannot create .ngargparser/build.lock (Permission denied): .ngargparser is read-only for you; run chmod u+rwx .ngargparser. Nothing was changed.`, or for another user's folder `ERROR: Cannot create .ngargparser/build.lock (<reason>): ask the owner of .ngargparser to run chmod g+rwx .ngargparser, which lets a group you share write there. Nothing was changed.`

Without `flock` (macOS), or on a file system where `flock` cannot lock (some network file systems), the lock is a folder holding the build's pid, which the next build takes over once that process is gone. A second build stops with one of these; the last two come from the folder lock only:

| Lock state | Message, after `ERROR: ` |
|---|---|
| Held by a running build or sync | `Another build or a cli sync is running in this folder (pid 4242). Wait for it to finish.` (`pid unknown` when the file holds no running pid) |
| A folder that holds no pid yet | `Another build or a cli sync may be running in this folder (its pid is not known). Wait for it to finish, or remove .ngargparser/build.lock if none is running.` |
| Left by a build that is gone, and the build cannot remove it | `A build or a cli sync that is no longer running (pid 4242) left .ngargparser/build.lock, and this build could not remove it. Remove .ngargparser/build.lock (or ask its owner to), then build again.` |

`make build-verbose` prints each step and the output of `get_libs.sh` and `hooks.sh`. Neither mode prints the shell trace unless `NGARGPARSER_BUILD_TRACE=1` is set.

### Lock check

The preflight compares the dependency lists in `pyproject.toml` (`dependencies`, `optional-dependencies` and `dependency-groups`) with what `uv.lock` records for the tool. The comparison reads both files, so it needs no network, no uv cache and no uv; it needs a Python that reads TOML. It then runs `uv lock --check --offline`, which also catches other changes, such as `requires-python`.

| Result | What the build does |
|---|---|
| `uv.lock` does not parse (a merge conflict marker, a cut-off or empty file) | Stops first with `ERROR: uv.lock does not parse (<reason>), so 'uv lock' cannot read it either: ...`; the reason names a conflict marker's line or the parse error, from Python or, without one, from uv |
| uv cannot read what a TOML `uv.lock` holds (`Failed to parse uv.lock`) | Stops with the same line, the reason `uv rejects what it holds: see below`, and up to 8 lines of uv's output |
| uv says the lock needs updating, or uv cannot run and a list differs | Stops with `ERROR: uv.lock is out of date with pyproject.toml: run 'uv lock' and commit uv.lock`, then one line per difference, uv's reason when it gives one (such as `Ignoring existing lockfile due to change in resolution mode`), and the uv settings that change how it resolves, when any are set |
| uv finds the lock current | Goes on quietly; when uv runs, its verdict wins |
| uv cannot run (no cached package, no matching Python, an older uv), and the lists match | Prints `Lock check partly skipped: the dependencies in pyproject.toml match uv.lock, but 'uv lock --check --offline' could not run (exit N), so other changes such as requires-python were not checked:` with up to 3 lines of uv's output, and goes on |
| uv cannot run, and the comparison cannot tell (an unknown lock layout, dynamic dependencies, no Python that reads TOML) | Prints `Lock check skipped: 'uv lock --check --offline' could not run (exit N):` with uv's output, and goes on |
| No `uv` on the PATH | The comparison decides alone: a difference stops the build, and anything else goes on quietly. Without a Python that reads TOML either, the build prints `Lock check skipped: uv is not on the PATH, and no Python that reads TOML was found (.venv/bin/python or python3, at 3.11 or later or with tomli), so uv.lock was not checked against pyproject.toml.` and goes on |
| No `uv.lock` | Skips the check, with the notice in [Preflight stops](#preflight-stops) |

The difference lines name the list and the requirement:

```
  added to dependencies: numpy
  removed from dependencies: python-dotenv
  changed in dependencies: python-dotenv>=1.0 (uv.lock has python-dotenv)
  added to dependency-groups.test: pytest-cov
```

A hand edit of `uv.lock` that uv rejects looks like this:

```
ERROR: uv.lock does not parse (uv rejects what it holds: see below), so 'uv lock' cannot read it either: restore it (after a merge: git checkout --theirs uv.lock) or delete it, then run 'uv lock' and commit uv.lock
    error: Failed to parse `uv.lock`
      Caused by: The entry for package `packaging` (26.2) has wheel `packaging-26.3-py3-none-any.whl` with inconsistent version (26.3), which indicates a malformed wheel. If this is intentional, set `UV_SKIP_WHEEL_FILENAME_CHECK=1`.
```

### Startup check

The startup check runs the entry script's `--help`, `predict --help`, `preprocess --help` and `postprocess --help` from the build copy, then `<name> --help` for each other subcommand or alias the top-level usage lists, with `APP_ROOT` set to the build copy. It runs them with the packages a deploy installs, in `.ngargparser/startup-venv`. The build syncs that environment from the build copy, as the deploy syncs the extracted tarball, offline from uv's cache: `uv sync --frozen --no-dev --offline --reinstall-package <name>`. It keeps the environment between builds. After `uv.lock` changes, the next build syncs once with `--reinstall`, which checks each package against its hash in `uv.lock`, so a hash edited by hand stops the build as it would stop the deploy. `.ngargparser/startup-venv.uv-lock` records the lock it last synced. So a module only the `dev` group brings, such as `pygments` through pytest, fails the build here, as it would fail on the deploy host:

```
ERROR: The tool does not start from the build dir:
src/run_my_app.py --help: ModuleNotFoundError: No module named 'pygments'
    ...
pygments is only in the dev group (or not in [project] dependencies), which a deploy leaves out: uv add Pygments
```

A tool with a `[build-system]` is installed into that environment from the build copy, so its editable install points at the build copy. `--reinstall-package` installs it again on every build, because the build copy is new each time and keeps the old modification times, so uv would not see a change. For a setuptools tool this costs about 0.5 s a build; a tool without a `[build-system]` installs no package of its own, and the flag does nothing.

A tool whose package does not build from the build copy, because `.distignore` or `scripts/hooks.sh` left out a package folder, or the readme a backend such as hatchling needs, fails the build, as it would fail `deploy/install.sh`:

```
ERROR: The tool does not install from the build copy, so deploy/install.sh would fail on the deploy host too ('uv sync --frozen --no-dev' exit 1):
      × Failed to build `my-app @
      │ file:///home/me/my-app/build/ng_my-app-local`
      ...
          error: package directory 'src/mypkg' does not exist
Building the tool's package needs every file pyproject.toml names, such as its readme and its packages: check that .distignore and scripts/hooks.sh leave them in the build copy.
```

uv's lines are cut to the first 6 and the last 20 when there are more than 30. What the sync writes into the build copy, such as a setuptools `src/my_app.egg-info/`, is removed again, so it does not ship; `make build-verbose` names it: `Removed src/my_app.egg-info from the build copy: uv sync wrote it there for the startup check.`

Any other uv error stops the build too, since `deploy/install.sh` would fail on it, such as a hash that does not match:

```
ERROR: 'uv sync --frozen --no-dev' fails in the build copy (exit 1), so deploy/install.sh would fail on the deploy host too:
      × Failed to download `python-dotenv==1.2.4`
      ╰─▶ Hash mismatch for `python-dotenv==1.2.4`
```

Only when uv lacks the network or its cache, such as a package missing from the cache offline, does the check run with the tool's `.venv`, and say why:

| Cause | Notice |
|---|---|
| No `uv` on the PATH | `Notice: the startup check uses the tool's .venv, so packages only its dev group installs were not left out (uv is not on the PATH).` |
| No `uv.lock` | The same, ending `(the tool has no uv.lock).` |
| The offline sync fails for want of the network or of uv's cache, outside CI | The same, ending `: 'uv sync --frozen --no-dev --offline' could not make .ngargparser/startup-venv (exit N):`, then the first 3 lines of uv's output |

In CI (`CI` set), a CI runner's uv cache is often cold, so the build tries the sync once online. When that fails too, it stops instead of using the `.venv`, whose dev group would hide a missing package: `ERROR: The startup check cannot run: CI is set and 'uv sync --frozen --no-dev --offline' could not make .ngargparser/startup-venv (exit 1), and neither could 'uv sync --frozen --no-dev' (exit 1). Give the CI job network access or uv's cache, or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check. uv said:`, then uv's first lines.

`make build-verbose` names the environment: `Startup check environment: .ngargparser/startup-venv (uv sync --frozen --no-dev).` The check removes `PYTHONPATH`, `PYTHONHOME` and `PYTHONSTARTUP` and sets `PYTHONNOUSERSITE=1`, so your shell's Python settings cannot change the result. A crash fails the build.

Each screen may take 60 seconds, or the seconds `NGARGPARSER_STARTUP_CHECK_TIMEOUT` sets. The first screen that takes longer fails the check, and the others do not run: `src/run_my_app.py --help: timed out after 1s: set NGARGPARSER_STARTUP_CHECK_TIMEOUT to the seconds each help screen may take, or NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check`.

An `entry` that is not a Python script (no `#!` line that names `python`, and no `.py` ending) runs by itself, with the check's environment first on `PATH`. One that is not executable fails the check: `src/run.sh is not a Python script and not executable: run 'chmod +x src/run.sh' and commit the mode (git update-index --chmod=+x src/run.sh)`. For each screen that fails, the build shows the last line of its error. When the error has more lines (a traceback), the build also shows its last 15 lines, indented, the first time; a later screen that ends on the same line gets `(same error as above)` after it instead.

The help screens run your tool's code in the build copy, and a module may write a file there when it is imported, such as a log. The build removes what the check created, so it does not ship, and names it:

```
Notice: the startup check (the tool's help screens) created these in the build copy; the build removed them, so they do not ship:
  src/startup.log
```

Help screens that change or remove a file in the build copy fail the build, since the file would ship that way:

```
ERROR: The startup check (the tool's help screens) changed or removed files in the build copy, so they would ship that way. Stop the tool from changing its own files when it starts:
  src/example.json: changed
```

The entry script is `entry` under `[tool.ngargparser]` in `pyproject.toml`, else the single `src/run_*.py`. The build reads `entry` as TOML when it finds a Python that reads TOML, so an inline table (`ngargparser = { entry = "src/run_my_app.py" }` under `[tool]`) counts too; otherwise it reads the `[tool.ngargparser]` table line by line:

| Situation | What the build does |
|---|---|
| `entry` names a file that is missing or excluded by `.distignore`, is not a quoted path, or points outside the tool folder | The [preflight](#preflight-stops) stops the build, naming the cause, even when the check is skipped |
| `entry` names a file a hook removed | Fails, naming the cause, even when the check is skipped |
| `NGARGPARSER_SKIP_STARTUP_CHECK` is set | Skips the check with a notice |
| No `entry`, and several `src/run_*.py`, outside CI | Skips the check with a notice to set `entry = "src/run_<name>.py"` |
| No `entry`, and several `src/run_*.py`, with `CI` set | Fails: `ERROR: The startup check cannot run: CI is set and the build has 2 src/run_*.py scripts. Set entry = "src/run_<name>.py" under [tool.ngargparser] in pyproject.toml to name the entry script, or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check.` |
| No `entry`, and no `src/run_*.py` in the build copy | Skips the check with a notice. A `.distignore` rule that excludes the only `src/run_*.py` stops the preflight instead |
| No `.venv/bin/python`, when the check falls back to it (above), outside CI | Fails: `ERROR: The startup check has no Python to run the tool with: the tool folder has no .venv/bin/python, and uv is not on the PATH. Run 'uv sync', or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check.` The reason after `and` is the cause from the table above |
| No `.venv/bin/python`, when the check falls back to it, with `CI` set | Fails: `ERROR: The startup check cannot run: CI is set and the tool folder has no .venv/bin/python. Run 'uv sync' before 'make build' in the CI job, or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check.` |

## get_libs internals

`.ngargparser/libs-stamp` records each run of `scripts/get_libs.sh` ([scripts/get_libs.sh](reference.md#scriptsget_libssh) lists the rules for the script):

| Line | Holds |
|---|---|
| 1 | The format marker, `ngargparser-libs-stamp 3` |
| 2 | `hash <git hash-object of scripts/get_libs.sh> version <TOOL_VERSION>` |
| 3 and later | One line per fetched entry: its fingerprint, then its name |

An entry's fingerprint is a checksum of each path in it with, for a file, its size and its modification time in whole seconds, and for a link, its size. An entry that is itself a link adds its target. It reads no file, so it stays fast on large `libs/`.

The fetch is skipped while line 2 matches and every fetched entry is in `libs/` with the fingerprint the stamp records. So the script runs again when it changes, when `TOOL_VERSION` changes (get_libs.sh may fetch by version), or when a fetched entry is missing. A fetched entry edited by hand, with a file added, deleted or changed at any depth, is fetched again, so what ships is what get_libs.sh fetched. The edited copy is kept in `.ngargparser/removed-libs/<name>-<YYYYMMDD-HHMMSS>` (with `-2`, `-3` added when that name is taken), since `libs/.gitignore` keeps it out of git:

```
libs/mylib changed since it was fetched; fetching again
libs/mylib changed since it was fetched, so it was moved to .ngargparser/removed-libs/mylib-20261018-213902 before the new fetch replaced it.
```

What counts as a change:

| Change | Fetched again |
|---|---|
| A file added, deleted, or edited to another size or modification time | Yes |
| A `chmod`, or a copy of the tool folder that keeps modification times (`cp -a`, `cp -p`, `rsync -a`, `tar`) | No |
| A copy that does not keep them, such as `cp -r` | Yes |
| An edit that keeps a file's size and its modification time, to the second | No: the build cannot see it |
| A change inside a `.git` or `__pycache__` folder | No, so running the tool from the tool folder fetches nothing |

A failed run leaves `libs/` as it was and stops the build.

An entry that get_libs.sh no longer fetches is removed from `libs/` on the next fetch:

```
libs/b is no longer fetched by scripts/get_libs.sh; removing it.
```

Such an entry, when it was edited by hand since the fetch, or the stamp has no fingerprint for it, is moved to `.ngargparser/removed-libs/<name>-<YYYYMMDD-HHMMSS>` instead of deleted. Each entry gets one line:

```
libs/a is no longer fetched by scripts/get_libs.sh. It changed since it was fetched, so it was moved to .ngargparser/removed-libs/a-20261018-113440 instead of deleted.
libs/a is no longer fetched by scripts/get_libs.sh. It was moved to .ngargparser/removed-libs/a-20261018-113448 instead of deleted: the build has no record to tell whether it changed since it was fetched.
```

After a fetch:

- Fetched folders get `__init__.py` files, so a run from the tool folder imports them the same way the build does. `.git` and `__pycache__` folders, at any depth, get none.
- `libs/.gitignore` lists the fetched entries, so they stay out of git; the file ignores itself. If `libs/.gitignore` is your own file, the build leaves it alone. Inside a git work tree, it then warns about any fetched entry git would still pick up. `cli generate` does not run `git init`, so a new tool gets no warning until you make it a repository.
- The build refuses to overwrite a `libs/<name>` that get_libs.sh did not fetch, so code you commit in `libs/` is safe.
- If `.ngargparser/libs-stamp` is lost (a fresh clone with `libs/` copied in, or `.ngargparser/` deleted), the build reads the list of fetched folders from `libs/.gitignore`, whose first line marks it as the build's. If both are gone, delete the fetched folders in `libs/` and build again.
- In progress mode (`make build`), the script's output goes to `.ngargparser/get_libs.log`; on failure the build prints its last 20 lines, or `(get_libs.sh printed nothing)`.

### libs import order

The entry script imports `core.set_pythonpath`, which loads `.env` and then puts `APP_ROOT/libs/` and its package folders at the end of the import path. The tool's own `src/`, the standard library and the packages in `.venv` all come first.

`set_pythonpath` walks `libs/` top-down in sorted (code-point) order, so every host gets the same order. Each folder with an `__init__.py` goes on the import path, and the folders reached last come first, with `libs/` itself last. So of `libs/aaa/util.py` and `libs/zzz/util.py`, `import util` imports `libs/zzz/util.py`, and `./configure` warns: `⚠  Two folders in libs/ provide the module util: import util gets libs/zzz/util.py, never libs/aaa/util.py. Rename one if code needs the other.`

A `libs/` folder never replaces a module of the same name. A fetched `somerepo/tests/fixtures/csv/`, which the build gives an `__init__.py`, is not imported as `csv`, and a fetched `preprocess.py` never replaces the tool's own. A package that sits in `libs/` and is also installed in `.venv` comes from `.venv`; to use the `libs/` copy, remove the installed one with `uv remove`.

## ./configure internals

The `./configure` launcher changes to its own folder first, following a symlink to the real launcher, so `../other-tool/configure` configures `other-tool`. It runs `src/core/configure.py` with `.venv/bin/python` when the tool has one, so the venv does not need to be active. Without a `.venv`, it runs the file with `python3`, so `configure.py` needs no exec bit. A Python without python-dotenv stops with ``./configure needs python-dotenv. Run `uv sync` first; it installs it into .venv.``, or in a build or deployed copy ``./configure needs python-dotenv. Run `bash deploy/install.sh`; it installs it into .venv, then runs ./configure.``

`./configure` writes each file it changes as a new file next to its place first: `.env`, each setup script, then `paths.py.bak` and `paths.py` when a flag changes `paths.py`. Only when all of them are written does it move them into place, `paths.py` last. So a run that cannot write one of them changes nothing, and names the file ([messages](reference.md#configure-flags-and-env)). `paths.py` is replaced, never emptied and written again, and the new file keeps the old one's mode.

Every run, with flags or without, holds `.ngargparser/paths.lock` from before it reads `paths.py` until it ends, and `cli deps add` and `remove` hold the same lock. Each command then reads the file the one before it wrote. So two runs at once cannot both read the old file and lose the change moved in first, and a run without flags cannot write `.env` and the setup scripts from a `paths.py` that a flag run has since changed.

`cli deps add` and `cli deps remove` write `paths.py.bak`, then `paths.py`, each through a temporary file renamed over it, so neither is ever cut short.

`./configure` runs `paths.py` from its bytes with `compile()` on every run, never through the import system. An import would write `__pycache__/paths.*.pyc` and read it back while the file's size and modification time match, so it would miss a same-size edit within one second, such as two flags in a row. A UTF-8 byte order mark, a coding line and CRLF line endings work as they do in an import.

It does not load the tool's `.env` into its environment first. So a `paths.py` that reads `os.environ` sees what the shell exports, never the value the last run wrote into `.env`.

A dependency's `paths.py` is never read. It is a Python module, loaded by running it, so reading another tool's copy would run its code inside your `./configure`. `.env` is plain key and value text, parsed and never run.

Steps 2 and 3 of [Virtualenv resolution](reference.md#virtualenv-resolution) are never written to `paths.py`. They are worked out again on every run, so the same `paths.py` works on your laptop and on each deploy host, each resolving locally.

### Values that follow a dependency

A dependency's virtualenv and start-up script follow its own `.env`, so a dependency deployed after `./configure` ran, or redeployed behind the same path (a `current` link), is picked up without running `./configure` again:

| Setting | Follows the dependency's `.env` when | Stays fixed when |
|---|---|---|
| Virtualenv (`APP_VENV`) | It came from step 2, step 3 found two candidates, or nothing was found at all (the dependency may not be deployed yet) | `<dep>_venv` is set in `paths.py`, or step 3 found one |
| Start-up script (`APP_ENTRY`) | Always | `<dep>_entry` is set in `paths.py` |

Three readers follow these values:

- `setup_<dep>_env.sh` reads them from `<dep>_path/.env` each time it is sourced. The values `./configure` saw, empty when it found none, are the fallback.
- The tool's own Python reads them again at start-up. `./configure` lists them in the `.env` key `APP_KEYS_FROM_DEPS`, for example `APP_KEYS_FROM_DEPS=CALLEE_VENV,CALLEE_ENTRY`, and `src/core/app_env.py` reads each listed key from the dependency's `.env`. The values in the tool's own `.env` are the fallback.
- `dependency_command` reads `<DEP>_VENV` and `<DEP>_ENTRY` from the dependency's `.env` again at each call, by the same rules ([Run a dependency from Python](reference.md#run-a-dependency-from-python)).

The fallback counts only while the dependency has no `.env` (it is not deployed yet), has one that cannot be read or is not UTF-8 text, or has one written for another folder. A fallback that names an existing folder outside the dependency's own, such as the version folder a `current` link pointed to when `./configure` ran, would run that older version, so then none of the dependency's fallbacks is used. The setup script also uses none of them when the dependency's folder is there without a `.env` but holds a tool's files or a git checkout; it prints that the dependency `is being installed at <folder> right now (a redeploy in progress?); try again`, or `is not configured yet: run ./configure there`. A dependency `.env` that sets no `APP_VENV`, or an empty one, leaves `<DEP>_VENV` unset, because the stored copy describes an earlier deploy.

All three read a value the way python-dotenv does: a leading `export `, spaces around `=`, one pair of quotes and a ` # comment` after an unquoted value are not part of it, and the last line for a key wins. So tools can be deployed in either order.

A value that neither the dependency's `.env` nor the tool's own `.env` sets is unset, in Python and in the setup script, even when the environment has it. Such a value comes from a calling tool, whose own dependency of the same name may be another install.

## Sync internals

### Sync lock

A real sync holds `.ngargparser/build.lock`, the lock `make build` takes, so a build and a sync never run at once in one tool folder. A dry run takes no lock. A sync that finds the lock held changes nothing, and leaves out `(pid 4242)` when the file holds no running pid:

```
✗ A build or another sync is running in this folder (pid 4242). Nothing was changed. Wait for it, then run cli sync again.
```

Like the build, sync opens a lock file it may not write read-only, which still locks, and makes a new one readable and writable for everyone ([Build lock](#build-lock)). A lock file it cannot open, or cannot create, stops it:

```
✗ Cannot open .ngargparser/build.lock (Permission denied): ask its owner to run chmod a+rw .ngargparser/build.lock. Nothing was changed.
✗ Cannot create .ngargparser/build.lock (Permission denied): .ngargparser is read-only for you; run chmod u+rwx .ngargparser. Nothing was changed.
```

For a `.ngargparser/` another user owns, the second line ends `ask the owner of .ngargparser to run chmod g+rwx .ngargparser, which lets a group you share write there. Nothing was changed.`

### Hash record

`cli generate` and each sync record the framework files' hashes in `.ngargparser/framework-hashes.json`, which is how sync tells your edits from an older release. The managed `.gitignore` section ignores the rest of `.ngargparser/` but keeps this file in git, so a fresh clone still warns about local edits:

```gitignore
.ngargparser/*
!.ngargparser/framework-hashes.json
```

A hash leaves out each CR before an LF. So a checkout with CRLF line endings, as git's `core.autocrlf` makes on Windows, hashes as the LF file in git, and sync does not take it for a local edit. A new tool's `.gitattributes` keeps `src/core/*.py` and `Makefile` at LF anyway (`text eol=lf`), so a checkout holds the bytes sync compares with its own copy.

A record that does not parse, such as one left with merge conflict markers, cannot tell your edits from an older release. Sync says so, goes on, and writes a new record at the end:

```
  └ ⚠ .ngargparser/framework-hashes.json does not parse; local edits to framework files cannot be detected this run (backups are still made).
```

A dry run ends the line with `(a real run still makes backups)`, and `--no-backup` with `(backups are off for this run)`.

A record that cannot be read, or that is a folder, gets the same warning with `cannot be read`, and the sync goes on. When the new record cannot be written either, sync prints `⚠ Could not update .ngargparser/framework-hashes.json (<reason>). The framework files are synced, but local edits to them cannot be detected reliably until the record can be written: make it a writable file, or delete it.` and still exits 0.

### Sync safety checks

`cli sync` runs three checks:

| Check | What it does |
|---|---|
| Known clash | Before writing anything, sync reads your tool's `src/*ArgumentParser.py` for code a framework release is known to break (the list in `KNOWN_CLASHES` in `cli.py`), and reports the file, the line numbers and the fix. A finding read from code can be false, so the help screens decide. When every screen passes before the sync, sync goes ahead, and names the clash if a screen fails after the update. When one fails or they cannot run, it stops before it writes anything. A break that is not on the list is left to the startup check |
| Startup check | When a framework file is about to change or `src/core/__init__.py` is missing, sync runs your tool's help screens before and after, with the tool's own `.venv` interpreter: the [test drive](#test-drive). This runs your tool's code |
| Undo | If a screen that worked before fails after, or sync is interrupted or hits an error, sync puts back every file it wrote, including created and edited ones and files reached through a symlink, and prints the error your tool raised. A rollback deletes its own backup folder, so the local-edit lines naming it are printed only in the summary of a sync that stands |

A dry run runs no tool code. When a real run could run the help screens, the dry run reports `⚠ Sync may stop: this tool has code the current framework breaks.`, says that the help screens decide, finishes the preview and exits 0. Otherwise it reports `✗ Sync would stop: ...`, says that a real run stops there, and exits 1. Its other lines start `DRY RUN:`, such as `DRY RUN: showing what would change; no files will be written.`

Without the startup check, nothing would show that an update broke the tool. So a sync that would change framework files stops before it writes anything when the check cannot run for a reason you can fix:

```
✗ Sync needs the tool's .venv to check that the tool still starts after the update. Run 'uv sync', then cli sync again (or cli sync --no-verify to update without the check). Nothing was changed.
✗ Sync needs the tool's .venv to check that the tool still starts after the update, and .venv/bin/python is not executable. Run 'uv sync', then cli sync again (or cli sync --no-verify to update without the check). Nothing was changed.
✗ Sync cannot check that the tool still starts after the update: entry = "src/run_gone.py" under [tool.ngargparser] in pyproject.toml names a file that does not exist. Fix it, then run cli sync again (or cli sync --no-verify to update without the check). Nothing was changed.
```

The `entry` follows the build's rules: a missing file, a folder, a path outside the tool, a value that is not a quoted path and a script that is neither Python nor executable each stop the sync. Unlike the build, sync also checks an `entry` under `libs/`, so it stops when get_libs.sh has not fetched that file yet. A dry run prints `✗ A real run would stop here and change nothing.` with the same reason, shows the rest of the preview, and exits 1.

### Test drive

The test drive runs your entry script with `--help`, `predict --help`, `preprocess --help` and `postprocess --help`, then `<name> --help` for each other subcommand or alias the top-level usage lists, with `APP_ROOT` set to the tool folder. The entry script is `entry` under `[tool.ngargparser]` in `pyproject.toml`, else the single `src/run_*.py`. It runs with the tool's `.venv/bin/python`, or by itself when it is not a Python script. As in the build's startup check, `PYTHONPATH`, `PYTHONHOME` and `PYTHONSTARTUP` are removed and `PYTHONNOUSERSITE=1` is set. Sync also sets `PYTHONDONTWRITEBYTECODE=1`, puts `.venv/bin` first on `PATH` and sets `VIRTUAL_ENV` to the tool's `.venv`. Before each run of the screens, sync says so, since each screen may take up to its timeout:

```
Checking that the tool starts (its help screens) before the update...
Checking that the tool starts (its help screens) after the update...
```

Each screen may take 60 seconds, or the seconds that `NGARGPARSER_SYNC_HELP_TIMEOUT` sets. Each screen runs in a session of its own, and a timeout kills its whole process group, so a program it started in the background stops too. The first screen that takes longer ends that run of the screens, so a tool that hangs costs one timeout:

```
  └ ⚠ src/run_my_app.py --help timed out after 2s; the other 3 help screen(s) were not run. To allow more time, set NGARGPARSER_SYNC_HELP_TIMEOUT to the seconds each help screen may take.
```

A value that is not a number of seconds above 0 and at most 86400 (one day) is named once, at the start, and the 60 seconds stay: `⚠ NGARGPARSER_SYNC_HELP_TIMEOUT=abc is not a number of seconds above 0 and at most 86400, so each help screen may take 60s.` A screen that times out before the update stops the sync before it writes anything, with exit code 1: `✗ src/run_my_app.py --help timed out before the update, so sync cannot check that the tool still starts after it. Set NGARGPARSER_SYNC_HELP_TIMEOUT to the seconds each help screen may take, then run cli sync again (or cli sync --no-verify to update without the check). Nothing was changed.` One that times out only after the update rolls the sync back.

Sync runs none of the scripts when it cannot tell which one is the entry script. With several `src/run_*.py` and no `entry`, running the others with `--help` could do whatever they do; with no `entry` and no `src/run_*.py`, there is nothing to run. A sync that would change framework files then stops before it writes anything, with exit code 1, as it does without a `.venv`:

```
✗ Sync cannot check that the tool still starts after the update: several src/run_*.py scripts were found (src/run_my_app.py, src/run_other.py), and pyproject.toml names none of them as the entry script. Set entry = "src/run_<name>.py" under [tool.ngargparser] in pyproject.toml, then run cli sync again (or cli sync --no-verify to update without the check). Nothing was changed.
```

Without any `src/run_*.py`, the reason reads `no src/run_*.py entry script was found, and pyproject.toml names no entry script`. A dry run prints the same reason after `✗ A real run would stop here and change nothing.`, and exits 1.

`cli sync --no-verify` skips the known-clash check and the startup check, and says so. Undo on error still applies. Setting `NGARGPARSER_SYNC_NO_VERIFY=1` has the same effect.

Before any of these, sync refuses a folder with no sign that ngargparser made it, one without `src/`, a build or deployed copy (it has `.ngargparser-build`), and a `pyproject.toml` that is not valid TOML. A `pyproject.toml` that starts with a UTF-8 byte order mark syncs, and the mark stays. Sync then reads every file it may change, to compare it with the framework's copy and to keep a copy that undoes the sync. A file it cannot read stops it, with exit code 1 and nothing changed:

```
✗ Cannot read src/core/result_writer.py (Permission denied): sync reads every file it may change, to compare it with the framework's copy and to keep a copy that undoes the sync. Nothing was changed. Make it readable, then run cli sync again:
    chmod u+r src/core/result_writer.py
```

For a file another user owns, the line reads `src/core/result_writer.py is not yours: ask its owner to run chmod a+r src/core/result_writer.py`. Sync also refuses to write any file through a link that leads outside the tool: framework files, `.gitignore`, `pyproject.toml`, `deploy/install.sh` and `.ngargparser/`. A `README.md` that is a link is left alone instead, without its badge ([What cli sync changes](reference.md#what-cli-sync-changes)). It checks only the files this run would write, and says how to replace each link with a copy. When some of them are not framework files, the first line reads `✗ Sync stopped: these files sync would write are links (or sit in a linked folder) that lead outside this tool, so sync would write there:`. With framework files only:

```
✗ Sync stopped: these framework files are links (or sit in a linked folder) that lead outside this tool, so sync would write the new framework code there:

  scripts/core/build.sh -> /shared/core/build.sh (through scripts/core, a link)

  Nothing was changed. Replace each link with a copy of what it points to, then run cli sync again:
    cp -RL scripts/core scripts/core.copy && rm scripts/core && mv scripts/core.copy scripts/core
```

It also refuses a tool whose stamp is newer than the installed ngargparser ([Version stamp](#version-stamp)).

The startup check proves the tool still starts. It cannot prove that a full `predict`, `preprocess` or `postprocess` run still gives the right result, so run your own tests after a sync.

### Failed writes and restores

Every file sync writes, the hash record included, goes through a temporary file renamed over it, so a build that has the old file open keeps reading it. A write that fails, such as on a full disk, leaves the file whole, and the sync undoes itself.

An undo that fails anyway (a full or read-only disk) keeps what you need to finish it by hand, and says where and what to do:

```
  ✗ Restore did not finish (read-only file system). The files before the sync are in .ngargparser/failed-restore-<UTC time>/; backups of overwritten files are in <backups>.
  Copy those files back into the tool by hand (each one sits under its path in the tool) before you run cli sync again. Do not run cli sync --no-verify on a half-restored tool.
```

A write that fails names the file, and the read-only folder when that is the cause. When it was the run's first write, nothing needs putting back:

```
✗ Could not write src/core/result_writer.py: the folder src/core is read-only (Permission denied). Nothing was changed.
```

After an earlier write, the run undoes itself and says so. A full disk gives:

```
✗ Could not write README.md: File too large.
  Every file was restored to its state before the sync.
```

The undo puts back only the files this run wrote, and leaves every other file as it is. A file you edited while the sync ran, after the sync wrote it, is kept: the undo moves your version to `.ngargparser/edited-during-sync-<UTC time>/<path>` before it puts back the file from before the sync, and says so:

```
  ⚠ src/core/result_writer.py changed after the sync wrote it (an edit made while the sync ran); that version was moved to .ngargparser/edited-during-sync-20261019T035919Z/src/core/result_writer.py.
```

When the reader of sync's output quits while sync runs, as `head` does in `cli sync | head`, sync undoes itself the same way, exits 1, and says so on stderr:

```
✗ cli sync stopped: its output could not be written (Broken pipe).
  Every file was restored to its state before the sync.
```

Output that breaks once the sync stands, in its summary, does not undo it. Python buffers output to a pipe, so a short sync can finish before `head` quits; it then stands.

A sync started with stdout closed (`cli sync >&-`) runs as usual. When it rolls back, the undo still runs, and stderr gets the usual rollback lines, from `✗ Sync rolled back: these commands worked before the sync and fail after it.` to `Every file was restored to its state before the sync.`

### Unfinished sync recovery

Each real run keeps the files it may change, as they were before it, in `.ngargparser/sync-in-progress/<UTC time>Z-<pid>/`, with a marker `<that name>.json` beside it, until it ends. Each copy is written into `<that name>.partial/` first, and moved into the folder once it is whole and on disk, so the folder never holds a copy cut short. An undo writes the file it puts back as `<that name>.restore` there, then renames it over the tool's file. The marker records the pid, the host and the start time; on Linux, also the boot and the start time of the process, so a pid the system has since given to another process, or a restart, still counts as a sync that ended. Before sync writes a file, it adds the file to the marker's `written` list, or to its `created` list when the file did not exist; after the write, `left` records the file's sha256.

A sync that is killed with `kill -9`, or whose undo did not finish, leaves the folder and the marker. The next sync, dry runs included, stops with exit code 1 and changes nothing. It names the written files that differ from the saved copies and the files the sync created, then prints the commands that finish the restore, run from the tool folder, one `cp -a` per file:

```
✗ A previous cli sync (pid 3090572, started 2026-10-18 19:21:20 UTC) did not finish, and may have left framework files half updated. The files from before it are in .ngargparser/sync-in-progress/20261018T192120.000000Z-3090572/. Copy back the files it changed (or run git checkout -- src/core/result_writer.py if they are committed), then delete these files the sync created: src/core/newfile.py. Then delete that folder and run cli sync again. From the tool folder:
    cp -a .ngargparser/sync-in-progress/20261018T192120.000000Z-3090572/src/core/result_writer.py src/core/result_writer.py
    rm src/core/newfile.py
    rm -r .ngargparser/sync-in-progress/20261018T192120.000000Z-3090572
```

When no written file differs from its saved copy, the message says `none of the files it wrote differs from its copy there, so none needs copying back`, and no `cp -a` line is printed. A written file whose sha256 no longer matches `left` was edited after the sync stopped: the commands leave it as it is, and the message says `the sync wrote <file>, and it changed since, so the commands leave it as it is`, with a note to compare it with its saved copy. A copy left in `.partial/` by a sync killed while it made the copies is left out of the commands: the sync had not written that file yet. Run the commands, then `cli sync` again: it removes the stale marker. `make build` refuses to build until then, with the line in [Preflight stops](#preflight-stops).

A marker from another host, as in a tool folder on a shared file system, names that host, since its process cannot be seen from here:

```
✗ A cli sync on the host node7 (pid 4242, started 2026-10-18 19:21:20 UTC) is still running there, or did not finish and may have left framework files half updated. The files from before it are in .ngargparser/sync-in-progress/20261018T192120.788048Z-4242/. If it is not running on node7, copy back the files it changed (or run git checkout -- src/core/result_writer.py if they are committed), then delete that folder and run cli sync again. From the tool folder:
    cp -a .ngargparser/sync-in-progress/20261018T192120.788048Z-4242/src/core/result_writer.py src/core/result_writer.py
    rm -r .ngargparser/sync-in-progress/20261018T192120.788048Z-4242
```

A marker that does not parse, because a kill cut it short, records no pid or start time. Sync takes the pid from its name, and counts the sync as running only while that process runs `cli sync`; any other process, or none, means the sync ended. Its message then says `started at an unknown time`, lists no created files, and its commands copy back each file in the folder that differs from the tool's.

A failed restore renames the folder to `.ngargparser/failed-restore-<UTC time>/`, and its marker names it, so the next sync stops the same way until you delete it.

### Version stamp

`scaffold_version` under `[tool.ngargparser]` records the ngargparser a tool was last generated or synced with. A release stamps its version, such as `0.4.1`. A checkout's files can differ from the release of the same number, so `cli generate` and `cli sync` from a checkout add a local label:

| Installed ngargparser | Stamp |
|---|---|
| A release | `0.4.1` |
| A git checkout (an editable install) | `0.4.1+g<short commit>`, with `.dirty` when files under `ngargparser/` have uncommitted changes: `0.4.1+gff1ed80.dirty` |
| A copy installed from a local folder, or a checkout git cannot read | `0.4.1+local` |

`cli --version` prints the version without the label. Sync reads the stamp with tomllib (or tomli) when one is installed, so every form TOML allows counts; else it reads a quoted value line by line.

Sync refuses a tool whose stamp is newer than the installed ngargparser, with exit code 1:

```
✗ This tool was last synced with ngargparser 9.0.0, which is newer than the installed ngargparser 0.4.1. Syncing would replace its framework files with older ones, so nothing was changed.
  Upgrade ngargparser first (cli upgrade), then run cli sync again; or sync to 0.4.1 anyway: cli sync --allow-downgrade
```

Versions compare in [PEP 440 order](#version-order). With `--allow-downgrade`, sync prints a `Downgrading:` line and syncs to the installed version.

A stamp with the same number and a checkout's label stops a sync from files that may be older. The installed checkout goes ahead when its history holds the stamp's commit, or when the labels differ only in a `.dirty` the installed one adds. Otherwise:

```
✗ This tool was last synced from a development checkout of ngargparser (0.4.1+gdeadbee); the installed ngargparser is another checkout or local copy (0.4.1+gff1ed80.dirty), whose framework files may be older. Nothing was changed. Sync from that checkout again, or pass --allow-downgrade to take these files.
✗ This tool was last synced from a development checkout of ngargparser (0.4.1+gff1ed80); the installed ngargparser is the 0.4.1 release, whose framework files may be older. Nothing was changed. Sync from that checkout again, or pass --allow-downgrade to take the release's files.
```

A stamp that is not a quoted string:

| Stamp | What sync does |
|---|---|
| A number, `scaffold_version = 0.5` | Compares it as the version it spells, so `0.5` is newer and stops the sync. When sync gets to stamping (`--allow-downgrade`, or an older number), it does not edit the number: `⚠ pyproject.toml sets scaffold_version under [tool.ngargparser] to a value that is not a quoted string (0.5), which cli sync does not edit. Set scaffold_version = "0.4.1" in it by hand.` |
| Another type, `scaffold_version = true` | Warns and goes on without the newer-stamp check: `⚠ pyproject.toml sets scaffold_version = true under [tool.ngargparser], which is not a version string, so sync cannot tell which ngargparser last synced this tool; it goes on without that check.`, then the same set-by-hand line |
| Text that does not parse as a version | Not treated as newer |

A `[tool.ngargparser]` table written as an inline table, a dotted key or a multi-line string cannot be edited: sync warns, names the value to set by hand, and goes on. It still reads `entry` there for the startup check.

### Version order

`cli sync`, `cli upgrade`, the update notice and `cli --version` compare versions in PEP 440 order: `0.5` is `0.5.0`, and `0.4.2.dev1` < `0.4.2rc1` < `0.4.2` < `0.4.2.post1`. So an installed pre-release such as `0.4.2rc1` counts as ahead of `v0.4.1`, not behind it.

## Signals

### Signals in a build

A build you stop removes its build copy, as a failed build does: with Ctrl-C (exit 130), a `kill` (exit 143), or a closed terminal or dropped ssh session (SIGHUP, exit 129). A running get_libs.sh, hooks.sh or startup check stops at once, with the build, and so does every process it started. The build finds them with `ps`, or reads `/proc` where there is no `ps`, as in `python:3.11-slim`. get_libs.sh and hooks.sh also run in a process group of their own, so a job one of them starts with `&` stops with the build, after a Ctrl-C too.

A job that get_libs.sh or hooks.sh starts with `&` and leaves running when the script exits would go on writing into `libs/` or the build copy. The build stops it, TERM first and KILL after 5 seconds, before it checks `libs/` or goes on, and says so:

```
Notice: scripts/get_libs.sh left background jobs running; the build stopped them.
Notice: scripts/hooks.sh left background jobs running; the build stopped them.
```

A job that left the script's process group, such as one started with `setsid`, or a daemon, is out of reach.

The build sets its signal traps before it takes the lock, and the commands it runs, such as Python and uv, never hold the lock. So a build stopped during the preflight cleans up too, and leaves no process behind that would stop the next build.

Ctrl-C signals the whole process group. A `kill` of the `make` process alone does not reach `build.sh`, which keeps running, so send the signal to `build.sh`.

### Signals in a sync

Ctrl-C, a `kill` (SIGTERM), a closed terminal or dropped ssh session (SIGHUP), or Ctrl-\ (SIGQUIT) stops a sync, which restores every file and exits 130, 143, 129 or 131. It prints which signal stopped it; Ctrl-C prints `✗ Sync interrupted.` without the name:

```
✗ Sync interrupted (SIGTERM).
  Every file was restored to its state before the sync.
Cancelled; nothing changed.
```

A second Ctrl-C, SIGTERM, SIGHUP or SIGQUIT while sync puts the files back is ignored, so the undo runs once and to the end. When SIGHUP and SIGINT arrive together, as when a terminal closes, Python handles SIGHUP first, so the exit code is 129. A signal the caller set to be ignored, such as `nohup`'s SIGHUP or a background job's SIGINT, does not stop the sync.
