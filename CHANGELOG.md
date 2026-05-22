# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
