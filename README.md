# Superpowers Lite 2.2

One Python installer for manually starting Superpowers workflows in Codex, OpenCode, Claude Code, Pi, and Antigravity CLI, with skill chaining authorized inside that workflow. A short global reminder reinforces required handoffs and cleanup after explicit invocation. No startup bootstrap or session hook is installed.

## Install

Requires Python 3.11 or newer. On Windows use `python`; on Linux substitute `python3` if needed.

```powershell
python .\superpowers-lite.py --only codex,opencode,claude,pi,antigravity --dry-run
python .\superpowers-lite.py --only codex,opencode,claude,pi,antigravity
```

Omitting `--only` selects harnesses found on PATH or with an existing configuration directory. Explicit `--only` also works before the harness is installed. OpenCode's major version is detected from its CLI; use `--opencode-version 1` or `2` when detection is unavailable. The adapters target v1 >=1.18.30 and v2 >=2.0.4; the exact native versions tested are listed below. Version overrides are your assertion about the target installation.

Restart affected harness sessions after installation. The default entry points are:

| Workflow | Codex | Claude / OpenCode / Antigravity | Pi |
|---|---|---|---|
| Brainstorming | `$superpowers-brainstorming` | `/superpowers-brainstorming` | `/skill:superpowers-brainstorming` |
| Writing plans | `$superpowers-writing-plans` | `/superpowers-writing-plans` | `/skill:superpowers-writing-plans` |
| Subagent development | `$superpowers-subagent-driven-development` | `/superpowers-subagent-driven-development` | `/skill:superpowers-subagent-driven-development` |

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
| Pi | `$PI_CODING_AGENT_DIR/skills/<name>/SKILL.md`, default `~/.pi/agent/skills` | `disable-model-invocation: true` hides entries from automatic skill advertising; `/skill:<name>` expands them explicitly |
| Antigravity CLI | `~/.gemini/config/skills/<name>/SKILL.md` | Generated `disable-model-invocation: true`; explicit `/<name>` invocation |

Claude supports command files as skills. OpenCode does not discover that directory as a skill source, so installing all five harnesses creates no duplicate `.agents`/`.claude` skill trees. Lite does not disable discovery of unrelated Claude skills. Known same-name collisions in standard roots are rejected instead of silently overwritten.

Sources: [Codex skill policy](https://learn.chatgpt.com/docs/build-skills), [Claude command/skill compatibility](https://code.claude.com/docs/en/skills), [OpenCode v1 permissions](https://opencode.ai/docs/permissions/), [OpenCode v2 skill discovery and controls](https://opencode.ai/v2/docs/skills). Native checks confirmed Codex's private `$CODEX_HOME/skills` discovery and the v1 slash-command behavior.

The full supporting skill library lives outside discovery roots, under `~/.local/share/superpowers-lite/bundles/<content-hash>`. Original upstream directory names, scripts, templates, and relative paths are preserved. Only selected entry points are public. This avoids unreliable dependency inference from prose mentions and avoids breaking shell scripts by renaming their sibling directories. The bootstrap skills `using-superpowers` and `diagnosing-superpowers` are omitted; required `using-superpowers/references` files remain at their original paths.

## Scoped workflow reminders

By default, Lite installs a short instruction block that applies only after you explicitly start a Lite workflow. It asks the agent to read complete skill instructions, track required stages and handoffs, load required finishing skills, and check verification/cleanup before claiming completion. It preserves user stopping points and approval gates. A matching task alone does not authorize starting a workflow, and subagents are told to complete their assigned task rather than restart the whole process.

| Harness | Global instruction file |
|---|---|
| Codex | `$CODEX_HOME/AGENTS.md`, or the existing nonempty `AGENTS.override.md` when that takes precedence |
| OpenCode | `$OPENCODE_CONFIG_DIR/AGENTS.md`, normally `~/.config/opencode/AGENTS.md` |
| Claude Code | `$CLAUDE_CONFIG_DIR/CLAUDE.md`, normally `~/.claude/CLAUDE.md` |
| Pi | First existing file in `$PI_CODING_AGENT_DIR`: `AGENTS.md`, `AGENTS.MD`, `CLAUDE.md`, `CLAUDE.MD`; creates `AGENTS.md` if none exists |
| Antigravity CLI | `~/.gemini/config/AGENTS.md` |

The block is enclosed by `<!-- superpowers-lite:workflow-<harness>:begin -->` and the matching `:end -->` marker. Ownership is recorded in the manifest. Updates replace only an unchanged owned block; uninstall removes it and removes an installer-created file only if nothing else remains. Existing user text, UTF-8 BOMs, and line endings outside the block are preserved. Edited, missing, malformed, duplicate, or unowned blocks stop replacement rather than silently overwriting instructions. Symlinks are retained and their resolved targets tracked; retargeting a managed file requires reconciliation. Project instruction files are not edited.

Creating OpenCode v1's global `AGENTS.md` would hide an existing global Claude instruction fallback. When that fallback was active, Lite preserves it as a reversible `instructions` entry in the OpenCode config. OpenCode v2 does not use that fallback. If Codex's override selection changes, rerun the installer to move its owned block to the effective file.

To remove or disable the reminder while retaining manual skills:

```text
python superpowers-lite.py --only codex --no-workflow-guidance
python superpowers-lite.py --only codex --workflow-guidance
```

The preference persists per harness. Include your desired `--skills` selection when updating, as with other installer options. These are model instructions, not an enforced workflow engine: they add a small amount of context to each session, and model compliance still needs testing. Restart sessions after changing them. The 2.1 installer upgrades ownership manifests from schema 2 to 3; use 2.1 or newer for subsequent updates/uninstall.

Instruction loading follows [Codex's global-file precedence](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [OpenCode v1 rules](https://opencode.ai/docs/rules/), [OpenCode v2 instructions](https://opencode.ai/v2/docs/instructions), and [Claude's user instructions](https://code.claude.com/docs/en/memory).

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

This mode accepts only the optional `--dry-run` flag. It downloads both revisions, checks compatibility, and exercises fresh install, update, repeated update, and uninstall in temporary homes for all five harnesses and both OpenCode adapters. It does not run upstream helpers or native harnesses. On success it atomically replaces only the `DEFAULT_REF` value, preserving the script's other bytes and permission bits. Existing installations are unchanged; run a normal installation afterward to update them.

The compatibility check allows workflow prose changes, static diagrams, and new prose-only Markdown resources. It rejects removed bundled files, unsupported skill metadata, changed or added helper/non-prose assets, changed executable/example code blocks, changed template substitutions, and new unresolved local or `superpowers:` skill references. Existing unresolved references do not by themselves block an update. Failures exit nonzero, report the affected files or validation error, and leave the pin unchanged. A rejection means the change needs manual compatibility review; it is not necessarily proof of an upstream defect. There is no override flag.

This is an installer-compatibility check, not a semantic audit of workflow prose or a guarantee of model behavior. Changed helper implementations require review even if their interfaces happen to remain compatible. Concurrent ref updaters are excluded by a sibling `.superpowers-lite-ref.lock`; after an interrupted updater, ensure it is no longer running before removing that stale lock and retrying.

Normal installation honors `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `PI_CODING_AGENT_DIR`, `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG`, `XDG_CONFIG_HOME`, and `XDG_DATA_HOME`. If both OpenCode JSON and JSONC files exist, select the effective target with `--opencode-config`. Other config layers may still override it. Use the same environment/path settings for later updates and uninstall.

`--home <directory>` creates an isolated installation and ignores inherited harness path overrides. It is useful for testing or preparing a separate home; it does not reconfigure running CLIs to use that home. Explicit `--opencode-config` still applies.

The installer parses JSONC and TOML, preserves their values, and validates serialization before changing any target. When a config needs a rewrite, formatting and comments are not preserved in the active file; the original file is retained in the transaction backup. Unchanged configs are not rewritten. Configuration changes are recorded per key, including previous values. Uninstall preserves subsequent user edits. An update refuses to reapply an owned setting that you changed or deleted.

Codex multi-agent and model preferences are **opt-in global settings**, not settings scoped to Superpowers:

```text
python superpowers-lite.py --only codex --enable-codex-multi-agent
python superpowers-lite.py --only codex --codex-subagent-effort high
python superpowers-lite.py --only codex --codex-subagent-model <supported-model>
```

Effort works independently of model. These preferences persist across Lite updates and restore previous values on uninstall when still unchanged. To relinquish owned tuning, uninstall Codex's Lite entries and reinstall without those flags. Model availability and spawn argument schemas remain harness-specific.

## Pi and optional subagents

Install only the Pi adapter with `python superpowers-lite.py --only pi`, then restart Pi or run `/reload`. Invoke entries with `/skill:superpowers-brainstorming <task>` and the other names in the table above. Support is verified against Pi 0.79.9. Pi's `enableSkillCommands: false` hides interactive command discovery but still permits a manually typed `/skill:<name>`. Lite leaves this preference and other Pi settings unchanged. After adding Pi, use installer 2.2 or newer for updates/uninstall; earlier versions do not recognize Pi ownership records.

Pi has no built-in subagent tool. An installed extension can supply one, such as Pi's [reference subagent extension](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/examples/extensions/subagent). Lite supplies tool guidance, but does not install an extension, agent roles, model settings, or a separate orchestration engine. The agent must use the actual extension's schema and available roles; different extensions are not interchangeable. Required independent reviews need a suitable reviewer role, and implementation needs a worker with the necessary tools. If a required capability is missing, the guidance tells the agent to report the blocker rather than invent a tool or substitute self-review. This also applies to skills that delegate only part of their workflow, such as plan review.

For extensions without resume support, the guidance permits a fresh isolated worker with the previous report and remaining findings. That loses the original worker's private context. Explicit model constraints must be preserved; an unsupported override is not permission to switch models. These instructions need model compliance and do not guarantee that every third-party extension can execute every upstream workflow.

Pi also discovers shared `.agents/skills`, project `.pi/skills`, and configured/package skill sources. Lite checks standard roots, including nested skills with ordinary declared names, and known Superpowers extension/package/bootstrap locations. It conservatively ignores skill ignore-filters when checking collisions. Arbitrary custom skill sources, complex YAML name encodings, dynamically registered resources, and extension behavior are outside this audit. Review Pi's startup diagnostics if you add extra sources. Pi's [skill documentation](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md) describes manual invocation and discovery.

## Antigravity CLI paths

Use `python superpowers-lite.py --only antigravity`; CLI detection looks for `agy`. Lite installs into the shared `~/.gemini/config` profile. Antigravity's other surfaces can also discover skills there; this is not an isolated CLI-only profile. The generated entries use the same slash-command names as Claude/OpenCode.

Installer 2.2.1 ignores `ANTIGRAVITY_CONFIG_DIR`, because the installed `agy` CLI does not honor that variable, and prints a notice when it is set during a normal Antigravity installation or uninstall. Use `--home <temporary-directory>` for isolated installer tests; that option does not reconfigure a running CLI.

If the original Antigravity adapter installed entries into a custom directory using `ANTIGRAVITY_CONFIG_DIR`, the new installer refuses to retarget the existing ownership record. First use the installer from commit `9737b5c` with the original environment and `--only antigravity --uninstall`, then unset the variable and install with the current version. Preserve or reconcile any user-modified owned files before uninstalling. Default-path installations update normally.

The 13 Antigravity-specific regression tests cover the audited rule/skill roots, plugin conflicts, ignored override, preservation of existing instructions, modified entries, repeated installation, and uninstall. For 2.2.1, the full 114-test suite passed on WSL Ubuntu and passed on Windows with five symlink-privilege skips. The installed CLI examined during review was 1.2.16. End-to-end model prompt behavior and subagent workflows have not been tested natively.

## Migrate the original installer

```text
python superpowers-lite.py --audit
python superpowers-lite.py --only codex,opencode,claude,pi,antigravity --migrate-legacy --dry-run
python superpowers-lite.py --only codex,opencode,claude,pi,antigravity --migrate-legacy
```

Include every affected harness because the original layout was shared. Migration recognizes legacy ownership markers, removes verified shared entries and Claude links/copies, replaces old `ask` rules for those entries, and removes Lite's old marked prompt/shell blocks. The new scoped workflow reminder is installed independently unless disabled. Previous files remain in transaction backups. It refuses unrelated or unrecognized entries.

Known full Superpowers plugins, hooks, discoverable full-library aliases, and unmarked bootstrap instructions block installation. Disable/remove those through their original harness or installation mechanism first; Lite does not silently uninstall another plugin. Audit checks known global/custom config locations and project ancestors of the current directory. It cannot certify all repositories, organization-managed policy, arbitrary launcher instructions, or custom plugin behavior.

For Antigravity, the bootstrap audit includes `~/.gemini/AGENTS.md`, `~/.gemini/GEMINI.md`, the equivalent files in `~/.gemini/config`, and modular rules in `~/.gemini/config/rules` and `~/.gemini/antigravity-cli/rules`. It also checks project/ancestor `GEMINI.md` and customization-directory instructions/rules. These additional files are inspected, not rewritten; the managed reminder stays in `~/.gemini/config/AGENTS.md`. See the [Antigravity rule locations](https://www.antigravity.google/docs/rules/).

Antigravity skill collision checks cover `~/.gemini/config/skills`, `~/.gemini/antigravity-cli/skills`, `~/.gemini/skills`, the shared `.agents/skills` root, and project/ancestor customization roots. Known full Superpowers plugins and bootstrap skill directories are also rejected under the global Antigravity roots. Arbitrary paths registered through customization JSON files and plugins with unrelated names remain outside this audit. The [CLI skill documentation](https://www.antigravity.google/docs/skills/#cli-skill-locations) identifies the dedicated CLI directory.

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

Baseline real GPT-5.6-Luna checks are documented in [tests/LUNA_EVALUATION.md](tests/LUNA_EVALUATION.md). All 13 Codex entries delivered their complete private skill bodies; 12 of 13 first-milestone trials passed. One reviewer-dispatch trial failed on model override selection and passed a separate diagnostic with explicit model inheritance. A complete TDD task passed. A longer SDD task produced verified code and completed reviews but skipped its final skill handoff and workspace cleanup. The opt-in `tests/live_skill_checks.py` runner uses disposable projects and temporary homes, consumes authenticated model usage, and records tool traces for manual grading. These results do not establish OpenCode or Claude live-model reliability.

Version 2.1 reminder checks: 90 portable tests passed on WSL Linux; Windows passed with four file-symlink privilege skips. Native mock-provider checks confirmed global reminder loading in Codex, OpenCode v1, and Claude, including Codex override precedence and preservation of OpenCode's existing Claude fallback. A live Luna control remained manual-only; an SDD rerun with explicit model inheritance loaded the finishing skill, cleaned up, and presented the prescribed choices. This is one successful controlled trial, not a reliability estimate; see the evaluation report for the stopped wrong-model attempt and remaining limitations. OpenCode v2 reminder loading has not been rerun natively; its local binary fixture was unavailable.

Run the portable regression suite with `python -m unittest discover -s tests -v`. It generates its own temporary source fixtures and requires no downloads or installed harnesses. Native file-symlink tests skip on Windows when the process lacks symlink privileges; run the suite on Linux/WSL to exercise them.

Optional integration scripts are `tests/native_harness_checks.py`, `tests/upstream_linux_check.py`, and `tests/v2_native_check.py`. The native harness checks use `.superpowers-review/upstream` by default, or accept `--source <checkout>`; the v2 check additionally requires a Linux OpenCode executable at `.superpowers-review/opencode-v2-bin/opencode`. These fixtures and generated reports are intentionally untracked. The Linux helper check downloads the pinned upstream archive and requires Bash and Git. Historical review reports and binary integrity records are not included in this checkout.

Pi has a separate offline native SDK check:

```text
python tests/native_pi_checks.py --source <upstream-checkout> --pi-package <installed-pi-coding-agent-package-directory>
```

It requires Node and the installed Pi npm package, uses temporary homes and an in-memory model transport, and needs no credentials or model usage. On Pi 0.79.9 it verified three manual entries, absence from automatic skill advertising, explicit command expansion (even with command discovery disabled), global instruction/fallback preservation, and uninstall. It also checked that an extension-registered delegation tool can coexist with the entries, using a synthetic extension without starting child agents. This is a harness integration test, not a live-model workflow test or certification of a particular subagent extension.

The 2.2 portable suite passed all 101 tests on WSL Ubuntu and passed on Windows with five symlink-privilege skips. This includes Pi install/update/uninstall, custom configuration paths, instruction-file precedence, modified-file protection, discovery collisions, and the shared transaction/recovery checks.
