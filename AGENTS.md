# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Python 3.11+ installer for manually invoked Superpowers workflows in Codex, OpenCode, and Claude Code.

- `superpowers-lite.py`: the complete standard-library-only implementation, including configuration parsing, harness adapters, ownership tracking, and transactional installation/recovery.
- `README.md`: installation, migration, configuration, and verification documentation.
- `LICENSE`: project licensing.

There are no separate source, asset, or test directories in this checkout. Supporting workflow assets come from upstream. Use the actual hyphenated script filename; README examples currently use `superpowers_lite.py`.

## Build, Test, and Development Commands

Run commands from the repository root. No dependency installation or build step is required; substitute `python3` on Linux when needed.

- `python superpowers-lite.py --help`: inspect supported options.
- `python -m py_compile superpowers-lite.py`: check syntax; creates `__pycache__/`.
- `python superpowers-lite.py --home <temporary-directory> --only codex --dry-run`: validate an isolated installation plan without changing installation targets.
- Add `--source <local-upstream-checkout>` to exercise local upstream content instead of downloading the pinned source.

## Coding Style & Naming Conventions

Match existing Python style: four-space indentation, snake_case functions and variables, PascalCase classes, UPPER_SNAKE_CASE constants, and predominantly single-quoted strings. Use `pathlib.Path` for paths and `SetupError` for actionable validation failures. Keep the installer standard-library-only. No formatter or linter configuration is checked in.

Generated entry names must be lowercase kebab-case, 1–64 characters, with the default `superpowers-` prefix.

## Testing Guidelines

The README documents `python -m unittest discover -s tests -v`, but the referenced suite and integration fixtures are absent. No coverage threshold is configured. When adding tests, use standard-library `unittest` and `tests/test_*.py` names.

Exercise install/update/uninstall, modified-file protection, configuration round trips, rollback, and recovery using temporary homes. Verify Windows and Linux path behavior when changing filesystem operations. Report checks actually run and any unavailable fixtures.

## Commit & Pull Request Guidelines

History contains only short descriptive subjects (`Initial commit`, `Create LICENSE`); no formal prefix convention is established. Use concise, imperative subjects.

PR descriptions should explain the behavior change, affected harnesses, validation commands/results, and configuration or migration implications. Link related issues when applicable. Preserve ownership checks, backup handling, and user edits; never test installation against real user configuration.
