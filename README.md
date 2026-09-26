# Superpowers Lite 2

One Python installer for manually starting Superpowers workflows in Codex, OpenCode, and Claude Code, with skill chaining authorized inside that workflow. No startup bootstrap, session hook, or global workflow instructions are installed.

## Install

Requires Python 3.11 or newer. On Windows use `python`; on Linux substitute `python3` if needed.

```powershell
python .\superpowers-lite.py --only codex,opencode,claude --dry-run
python .\superpowers-lite.py --only codex,opencode,claude
```

Omitting `--only` selects harnesses found on PATH or with an existing configuration directory. Explicit `--only` also works before the harness is installed. OpenCode's major version is detected from its CLI; use `--opencode-version 1` or `2` when detection is unavailable. The adapters target v1 >=1.18.30 and v2 >=2.0.4; the exact native versions tested are listed below. Version overrides are your assertion about the target installation.

Restart affected harness sessions after installation. The default entry points are:

| Workflow | Codex | Claude / OpenCode |
|---|---|---|
| Brainstorming | `$superpowers-brainstorming` | `/superpowers-brainstorming` |
| Writing plans | `$superpowers-writing-plans` | `/superpowers-writing-plans` |
| Subagent development | `$superpowers-subagent-driven-development` | `/superpowers-subagent-driven-development` |

Supply your task after the invocation. After entering a workflow, required dependency skills are read from the private library without asking you to manually invoke each one. The generated entry scopes that authorization to the requested workflow.

To choose different public entry points:

```text
python superpowers-lite.py --only codex --skills brainstorming,executing-plans,systematic-debugging
```

This replaces only Codex's entry selection. Other harness installations retain their selections and libraries. A later update uses the default three entries unless you pass `--skills` again. An empty selection is an error; use `--uninstall` to remove entries. `--name-prefix` defaults to `superpowers-`; final names must be lowercase kebab case, 1–64 characters.

## Why the directories changed

The old shared `.agents/skills` installation plus Claude links exposed the same skills through multiple OpenCode discovery roots. [OpenCode issue #29950](https://github.com/anomalyco/opencode/issues/29950) reports unstable selection between such duplicates. Lite now creates distinct harness entry points:

| Harness | Entry location | Manual-entry control |
|---|---|---|
| Codex | `$CODEX_HOME/skills/<name>/SKILL.md`, default `~/.codex/skills` | Generated `agents/openai.yaml`: `policy.allow_implicit_invocation: false` |
| OpenCode v1 | `~/.config/opencode/skills/<name>/SKILL.md` | Exact `permission.skill.<name>: deny` rules hide model skill access; the native slash command expands the entry directly |
| OpenCode v2 | Same native OpenCode directory | `metadata.opencode/autoinvoke: false` |
| Claude Code | `~/.claude/commands/<name>.md` | `disable-model-invocation: true` on command-format skills |

Claude supports command files as skills. OpenCode does not discover that directory as a skill source, so installing all three harnesses creates no duplicate `.agents`/`.claude` skill trees. Lite does not disable discovery of unrelated Claude skills. Known same-name collisions in standard roots are rejected instead of silently overwritten.

Sources: [Codex skill policy](https://learn.chatgpt.com/docs/build-skills), [Claude command/skill compatibility](https://code.claude.com/docs/en/skills), [OpenCode v1 permissions](https://opencode.ai/docs/permissions/), [OpenCode v2 skill discovery and controls](https://opencode.ai/v2/docs/skills). Native checks confirmed Codex's private `$CODEX_HOME/skills` discovery and the v1 slash-command behavior.

The full supporting skill library lives outside discovery roots, under `~/.local/share/superpowers-lite/bundles/<content-hash>`. Original upstream directory names, scripts, templates, and relative paths are preserved. Only selected entry points are public. This avoids unreliable dependency inference from prose mentions and avoids breaking shell scripts by renaming their sibling directories. The bootstrap skills `using-superpowers` and `diagnosing-superpowers` are omitted; required `using-superpowers/references` files remain at their original paths.

## Source and configuration decisions

The script's `DEFAULT_REF` constant selects the pinned default source. The original reviewed baseline is Superpowers commit `5bf4e78011075bcfc0dc295f0724994cd123ee71` (upstream plugin version 6.4.1). One-off source overrides are explicit:

```text
python superpowers-lite.py --ref <commit-or-tag> --dry-run
python superpowers-lite.py --source /path/to/local/superpowers --dry-run
```

Remote tags/branches resolve to an exact commit before downloading. The manifest records that commit, archive SHA-256, library content hash, and installer version. A local checkout records its path, Git HEAD when available, and actual installed content hash; local edits are included. An upstream behavioral frontmatter extension, such as hooks or special agent context, is rejected for review rather than silently discarded. Descriptive upstream headers stay in the private library as data; harness policy files are generated independently.

To check the latest commit on upstream's default branch and update the script's pin in place:

```text
python superpowers-lite.py --update-default-ref --dry-run
python superpowers-lite.py --update-default-ref
```

This mode accepts only the optional `--dry-run` flag. It downloads both revisions, checks compatibility, and exercises fresh install, update, repeated update, and uninstall in temporary homes for all three harnesses and both OpenCode adapters. It does not run upstream helpers or native harnesses. On success it atomically replaces only the `DEFAULT_REF` value, preserving the script's other bytes and permission bits. Existing installations are unchanged; run a normal installation afterward to update them.

The compatibility check allows workflow prose changes, static diagrams, and new prose-only Markdown resources. It rejects removed bundled files, unsupported skill metadata, changed or added helper/non-prose assets, changed executable/example code blocks, changed template substitutions, and new unresolved local or `superpowers:` skill references. Existing unresolved references do not by themselves block an update. Failures exit nonzero, report the affected files or validation error, and leave the pin unchanged. A rejection means the change needs manual compatibility review; it is not necessarily proof of an upstream defect. There is no override flag.

This is an installer-compatibility check, not a semantic audit of workflow prose or a guarantee of model behavior. Changed helper implementations require review even if their interfaces happen to remain compatible. Concurrent ref updaters are excluded by a sibling `.superpowers-lite-ref.lock`; after an interrupted updater, ensure it is no longer running before removing that stale lock and retrying.

Normal installation honors `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG`, `XDG_CONFIG_HOME`, and `XDG_DATA_HOME`. If both OpenCode JSON and JSONC files exist, select the effective target with `--opencode-config`. Other config layers may still override it. Use the same environment/path settings for later updates and uninstall.

`--home <directory>` creates an isolated installation and ignores inherited harness path overrides. It is useful for testing or preparing a separate home; it does not reconfigure running CLIs to use that home. Explicit `--opencode-config` still applies.

The installer parses JSONC and TOML, preserves their values, and validates serialization before changing any target. When a config needs a rewrite, formatting and comments are not preserved in the active file; the original file is retained in the transaction backup. Unchanged configs are not rewritten. Configuration changes are recorded per key, including previous values. Uninstall preserves subsequent user edits. An update refuses to reapply an owned setting that you changed or deleted.

Codex multi-agent and model preferences are **opt-in global settings**, not settings scoped to Superpowers:

```text
python superpowers-lite.py --only codex --enable-codex-multi-agent
python superpowers-lite.py --only codex --codex-subagent-effort high
python superpowers-lite.py --only codex --codex-subagent-model <supported-model>
```

Effort works independently of model. These preferences persist across Lite updates and restore previous values on uninstall when still unchanged. To relinquish owned tuning, uninstall Codex's Lite entries and reinstall without those flags. Model availability and spawn argument schemas remain harness-specific.

## Migrate the original installer

```text
python superpowers-lite.py --audit
python superpowers-lite.py --only codex,opencode,claude --migrate-legacy --dry-run
python superpowers-lite.py --only codex,opencode,claude --migrate-legacy
```

Include every affected harness because the original layout was shared. Migration recognizes legacy ownership markers, removes verified shared entries and Claude links/copies, replaces old `ask` rules for those entries, and removes Lite's marked prompt/shell blocks. Previous files remain in transaction backups. It refuses unrelated or unrecognized entries.

Known full Superpowers plugins, hooks, discoverable full-library aliases, and unmarked bootstrap instructions block installation. Disable/remove those through their original harness or installation mechanism first; Lite does not silently uninstall another plugin. Audit checks known global/custom config locations and project ancestors of the current directory. It cannot certify all repositories, organization-managed policy, arbitrary launcher instructions, or custom plugin behavior.

The original Windows installer used `setx OPENCODE_DISABLE_CLAUDE_CODE_SKILLS 1` without recording ownership or the prior value. If you know that value came from the old installer, remove it explicitly:

```powershell
[Environment]::SetEnvironmentVariable('OPENCODE_DISABLE_CLAUDE_CODE_SKILLS', $null, 'User')
Remove-Item Env:OPENCODE_DISABLE_CLAUDE_CODE_SKILLS -ErrorAction SilentlyContinue
```

Restart terminal/IDE parent processes afterward. Lite removes its marked Linux shell blocks and exact owned fish file during migration, but existing shells retain their environment until restarted or `unset OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` is run. Unmarked launcher settings require manual correction. New Lite installs never set this variable.

## Uninstall and recovery

```text
python superpowers-lite.py --uninstall --only codex
python superpowers-lite.py --uninstall --prune
```

Uninstall removes only manifest-owned entries and releases owned config settings. Modified entries stop replacement/removal so your edits are not lost. `--prune` additionally removes hash-verified libraries no remaining installation references. Close old sessions first: their conversation may still reference an older library. Without `--prune`, libraries remain available for those sessions. Manifests and transaction backups remain for inspection.

Every install builds and validates its complete plan before applying changes. Files are staged beside destinations, replacements use same-volume renames, and ordinary failures trigger rollback. A lock covers ownership reads, planning, and application to prevent overlapping installers. Dry runs acquire a temporary lock, then remove it without changing installed files; newly created empty store directories are also removed. If the process is interrupted, first ensure no installer remains active, then run:

```text
python superpowers-lite.py --recover
```

Recovery rolls back an incomplete transaction, or completes backup archival for a committed one. A concurrent user edit blocks destructive recovery and reports the backup location. The transaction journal maps original paths to archived backups under `~/.local/share/superpowers-lite/transactions/<id>/backups/<index>`. These backups can contain private configuration; retain them accordingly. Successful historical transactions are not automatically reverted wholesale, because that could discard later user edits. There is no claim of filesystem-wide atomicity or power-loss durability.

## Supported subset and remaining tradeoffs

- Manual entry is a harness discovery/invocation policy, not a security boundary. A model with general file access can read private files if directed to them. Higher-priority project/agent permissions and custom skill sources can override global controls or introduce collisions outside the checked standard paths.
- Chaining reads upstream skill bodies directly. This preserves workflows and bundled helper assets but does not recreate arbitrary harness-specific substitutions, skill hooks, or context isolation. The audited source uses the supported subset; future behavioral frontmatter requires a deliberate adapter update.
- Claude Code 2.1.181 normal mode discovers the command-format entries. Its `--bare` mode skips this legacy command directory and reports an unknown command. Use normal mode for Lite. This directory choice provides independent harness installation without OpenCode duplicate discovery.
- Bash, Git, and Unix utilities are needed for upstream shell workflows. On Windows use Git Bash or WSL, translating Windows paths when crossing into WSL. Install separately inside WSL if the harness itself runs there. Optional brainstorming visualization needs Node. The installer does not install those runtimes or rewrite upstream workflows for PowerShell.
- Existing explicit harness settings can disable skills, restrict private-file access, or prohibit subagents. Lite does not bypass those policies. Workflow and model compliance are not guaranteed by installer tests.

## Verification performed

All installer tests use temporary homes. The table below records historical checks for the original 2.0 implementation; it is not a claim that every integration check has been rerun for each subsequent change.

| Check | Result |
|---|---|
| Python regression suite | 40 passed on Windows Python 3.13.3 and WSL Ubuntu Python 3.14.4 |
| Codex CLI 0.156.1, Windows | Three private user-scope entries, no parse errors; ordinary debug prompt hides them; actual explicit invocation expands the entry through a localhost mock Responses endpoint |
| OpenCode 1.18.30, Windows | Three stable entries across repeated discovery; explicit slash expands despite model skill deny; not advertised in the captured model request |
| Claude Code 2.1.181, Windows | Ordinary prompt hides entries; explicit slash expands the entry through a localhost mock Anthropic endpoint |
| OpenCode 2.0.16, WSL | Three stable entries, native `autoinvoke: false`, explicit activation appends the entry with inference disabled |
| Pinned upstream download, WSL | Install, directly executable task-start/task-done/review-package helpers, paths with spaces, and failing test exit-code propagation all passed |

The v2 API can return a catalog before plugin activation finishes; its smoke test uses a persistent local server and waits for discovery. Codex's `debug prompt-input` does not itself expand `$skill`; actual `exec` against the mock endpoint verifies that path. These are native registration/prompt integration checks, not a paid model run through an entire Superpowers project.

Run the portable regression suite with `python -m unittest discover -s tests -v`. It generates its own temporary source fixtures and requires no downloads or installed harnesses. Native file-symlink tests skip on Windows when the process lacks symlink privileges; run the suite on Linux/WSL to exercise them.

Optional integration scripts are `tests/native_harness_checks.py`, `tests/upstream_linux_check.py`, and `tests/v2_native_check.py`. The native harness checks require a local upstream checkout at `.superpowers-review/upstream`; the v2 check additionally requires a Linux OpenCode executable at `.superpowers-review/opencode-v2-bin/opencode`. These fixtures and generated reports are intentionally untracked. The Linux helper check downloads the pinned upstream archive and requires Bash and Git. Historical review reports and binary integrity records are not included in this checkout.
