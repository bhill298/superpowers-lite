# Live GPT-5.6-Luna evaluation

Run on September 25, 2026 (September 26 UTC), against installer commit
`825c08d` and pinned upstream `5bf4e78011075bcfc0dc295f0724994cd123ee71`.
This used real authenticated Codex CLI 0.156.1 model sessions on Windows,
with `gpt-5.6-luna` and medium reasoning. It did not use mock model responses.

## Method

Each case installs all 13 selectable skills into a fresh temporary home and
creates a small disposable Python/Git project. The prompt names the public
`$superpowers-...` entry and gives a concrete task and stopping point. It does
not explain how to read the private skill or platform adapter. A separate
control prompt does not name any skill.

Invocation success requires the wrapper to appear in the model input and the
complete, unchanged upstream skill body to appear in actual tool results.
The evaluator compares the full body, not just a path or the model's claim
that it read it. Workflow milestones are assessed separately from tool
traces, saved artifacts, and final answers. An exit code of zero alone is not
a workflow pass. Model/effort values are checked in root and child session
records. All workflow sessions used Luna at medium reasoning. Codex's
separate `codex-auto-review` permission reviewer is not a task-solving agent.

## Baseline results

All 13 entries successfully expanded and delivered their full private skill
bodies. All 13 also read the complete Codex platform adapter. Twelve of the
13 requested workflow milestones passed; reviewer dispatch failed in one.

| Skill | Requested milestone | Result |
|---|---|---|
| brainstorming | Ask the first requirements question; no implementation | Pass |
| writing-plans | Write and self-review a concrete plan; stop for user review | Pass |
| subagent-driven-development | Inspect plan/spec, record preflight, stop before implementation | Pass; recovered from Bash path errors |
| dispatching-parallel-agents | Two isolated agents investigate separate seeded failures | Pass; two Luna children returned correct root causes |
| executing-plans | Check readiness, baseline tests, and next step without implementing | Pass; also loaded the TDD dependency |
| finishing-a-development-branch | Verify tests and present integration choices without acting | Pass |
| receiving-code-review | Evaluate deliberately incorrect reviewer advice | Pass; rejected advice using code/tests/spec |
| requesting-code-review | Dispatch a reviewer for a deliberately broken commit | **Fail at dispatch**, despite successful skill loading |
| systematic-debugging | Reproduce a negative-number failure and identify its cause | Pass |
| test-driven-development | Add a test, observe RED, stop before implementation | Pass |
| using-git-worktrees | Inspect isolation and ask before creating a worktree | Pass |
| verification-before-completion | Run the suite and verify the claimed result | Pass |
| writing-skills | Load required background and propose a baseline pressure test | Pass; followed the reference to TDD and read the testing guide |

The no-invocation control passed: no Superpowers wrapper was injected and no
Superpowers body was read. The model accurately described the project.
A stronger control repeated the same failing-test task as the explicit
systematic-debugging case, without naming the skill. It also passed: Luna
diagnosed the defect without loading a Superpowers wrapper or body.

A separate complete TDD task passed: Luna wrote positive/zero/negative tests,
observed the missing-function failure, implemented `double`, and passed all
seven tests. The driver independently checked three values, and the saved
artifact's full seven-test suite was independently rerun successfully.

## Complete subagent workflow: partial pass

A longer one-task SDD trial finished in 544 seconds. It used six isolated
Luna children at medium reasoning for implementation, task review, final
branch review, a documentation fix, scoped re-review, and final verification.
It ran the bundled `sdd-workspace`, `task-brief`, and `review-package` helpers.
The final reviewer caught an unchecked plan checklist; a fresh agent fixed
it and another reviewed that fix. The final code and all seven tests passed
independent verification. Only the disposable repository received commits.

However, this was **not a complete workflow pass**:

- It did not load the required `finishing-a-development-branch` skill. Its
  final answer improvised merge/cherry-pick/squash choices rather than using
  that skill's menu and decision gate.
- It left the plan workspace under `.superpowers/sdd/plan/`, although the
  SDD skill requires cleanup after a clean final review.

The initial SDD read was truncated when combined with a large tool-catalog
dump. Luna reread ranges of the skill; manual inspection confirmed coverage
of all its nonblank lines across those chunks. The automated full-body
substring check intentionally does not infer completeness from chunked reads.
The final handoff/cleanup omission therefore occurred after the relevant
instructions had been delivered.

This supports the wrapper mechanism and substantial subagent operation on
Luna, while leaving an observed weakness in following the entire workflow
through its final handoff. Loading success is not complete process compliance.

## Reviewer failure and diagnostic

The failing reviewer trial called `spawn_agent` with
`model: "GPT-5.6-Luna"`. The tool's explicit override allowlist did not contain
that model. Luna treated the rejection as model unavailability and stopped,
although omitting the override would inherit the already-running Luna model.
The parallel-agent trial used inheritance successfully without extra help.

A fresh diagnostic trial added this prompt instruction:

> For subagents, omit the model override to inherit this session's model;
> this session is already running GPT-5.6-Luna. Set medium reasoning explicitly.

With that instruction, Luna dispatched a real isolated reviewer and correctly
reported that `absolute(-3)` returns `-3` instead of `3`. This is a prompted
recovery, not a baseline pass. The production adapter was not changed.

## Practical limitations

- These are first-milestone tests for all skills, plus selected complete
  workflows. They do not validate every skill through a complete project.
- One baseline sample per case is not a reliability estimate. Successful
  extra brainstorming and writing-skills pilots do not remove that limitation.
- The SDD preflight initially passed Windows and Git Bash paths to WSL Bash.
  Both failed. Luna inspected the environment, switched to `/mnt/c/...`, and
  successfully ran the bundled `sdd-workspace` helper. Bash selection/path
  translation remains a source of extra calls and latency.
- Initial pilot reads were blocked by the native CLI's permission setup.
  Those runs are excluded. The scored runs used `--approve-for-me`, preserving
  automatic permission review. No sandbox-bypass flag was used.
- This tests the Codex adapter. OpenCode and Claude live-model behavior are
  untested. The CLI can still expose built-in skills and account-provided tool
  catalogs; temporary homes are not a hermetic machine/account sandbox.
- It does not test a long conversation, context compaction, adversarial
  prompts, optional visual companions, or all upstream helper interfaces.

## Reproduce and inspect

`live_skill_checks.py` is opt-in and consumes authenticated model usage. It
requires a file-backed Codex login, Python, Git, a local upstream checkout,
and Bash/WSL for upstream shell helpers. It copies authentication privately
into temporary homes, removes those homes afterward, and does not edit real
user harness configuration. Keep generated artifacts in the ignored directory.

```text
python tests/live_skill_checks.py --source <pinned-upstream-checkout> --output .superpowers-review/luna/new-run --jobs 3 --timeout 600
```

The output directory must be new. `--cases` selects comma-separated cases.
`--inherit-subagent-model` enables the explicitly labelled diagnostic prompt;
omit it for baseline runs. Source hashes, prompts, CLI events, session records,
project artifacts, and results are retained. Automated results are evidence
candidates; review actual tool calls and artifacts before grading behavior.

Local evidence from this evaluation is under `.superpowers-review/luna/`:
`full-run`, `pilot-auto-review`, `reviewer-inherit`, and `sdd-integration`.
The stronger negative control is in `implicit-control`; `final-runner-control`
is an additional runner-validation trial.
The initial infrastructure failures are in `pilot`. Raw logs are intentionally
untracked; this report and the reusable runner are tracked.

The existing portable installer suite was also rerun: 71 tests, 69 passed
and two Windows symlink-privilege skips. Runner syntax and Git whitespace
checks passed. No production installer changes were made for this evaluation.
