# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-05-12

### Added
- `deploy/install.sh` scaffold for the nxg-tools-deployments deploy contract; `cli generate` writes it for new projects and `cli sync` creates it once in legacy projects (never overwritten).
- `[tool.ngargparser] scaffold_version` stamp in scaffolded `pyproject.toml`; `cli sync` keeps it current and uses it to drive future version-aware migrations.
- Scriptable `cli deps add/remove/list` for managing external tool deps in `paths.py`.
- README "Prerequisites" section pointing at the `uv` installer; install-from-GitHub guidance via `uv tool install git+https://github.com/IEDB/NGArgParser.git`.

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
