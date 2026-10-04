# Antigravity end-to-end checks

Run on 2026-10-03 with installer 2.2.1 and Antigravity CLI 1.2.16 on
Windows. The installed private bundle was
`447f735d828638d17ac3d3fd68d3896c0c33b8d67cb228a1c917aeb172558542`.
All installations used temporary homes and synthetic projects. No real
harness configuration was modified.

## Native CLI with a scripted model transport

`native_antigravity_checks.py` runs the installed CLI against a localhost
Gemini SSE fixture, using a fake API key. All 17 cases passed:

- Ordinary prompts load the managed global workflow reminder and existing
  user rules, without advertising any of the 13 installed skills.
- Each of the 13 explicit slash commands expands its generated wrapper.
  The real CLI executes `view_file` calls for the complete private skill,
  Antigravity adapter notes, and a second skill body for chaining (except
  when the entry itself is that second skill). Assertions inspect the
  returned file contents, not merely the requested paths.
- Repeated installation preserves explicit command behavior.
- Uninstall removes the reminder and command expansion, and restores the
  original user instruction file byte for byte, including CRLF newlines.

The fixture deliberately chooses the tool calls. It verifies registration,
prompt delivery, file access, and lifecycle behavior; it cannot establish
whether a model will choose or follow those instructions correctly.

## Live model trials

`live_antigravity_checks.py` uses the existing OS keyring login and consumes
model usage. These trials used the CLI's default model selection, with no
model override. The stream did not expose a reliable model identifier, so
these are not GPT-5.6-Luna results.

| Trial | Result | Evidence |
|---|---|---|
| Ordinary debugging control | Pass, 32.0 s | Diagnosed the seeded negative-number defect, read no skill files, and left the working tree unchanged. |
| Explicit brainstorming | Pass at first clarification milestone, 39.6 s | Read the full skill and adapter notes, inspected the project, asked the first interface question, and left the working tree unchanged. |
| Complete TDD task | Pass, 78.2 s | Read the full skill, notes, and supporting test guide; observed missing-function failure, initial green, zero/negative failures, and final green. Seven tests passed independently, and a separate check verified doubling plus preservation of existing functions. Changes remained uncommitted as requested. |
| Complete subagent-driven task | Functional pass; strict workflow compliance incomplete, 286.3 s | Ran bundled workspace/brief/review-package helpers; dispatched a native implementer, task reviewer, and final whole-branch reviewer; read the finishing skill; removed the plan workspace; ran seven passing tests; presented the prescribed three integration choices. Independent tests and doubling checks passed, and the Git working tree was clean. However, it skipped handling a minor final-review finding. |

The TDD trial updated the plan checkboxes as well as code and tests. It did
not separately read `verification-before-completion`; it did run fresh
verification before reporting completion. This trial does not establish
that every possible skill handoff will happen.

The subagent trial retained separate native child transcripts. Both
reviewers inspected the generated diff packages and approved the change;
neither reported critical or important findings. The task reviewer
correctly noted that unchanged `slug` code was outside its diff. The
controller had inspected that file, and the final tests included its
existing cases. The final branch remained `fixture`, with only the
requested implementation and tests committed; no integration action was
taken.

**Observed compliance failure:** the final reviewer suggested type hints
and a docstring as a minor improvement, with an incorrect `math_utils.py:21`
line reference (the file has five lines). The controller did not dispatch a
fixer or scoped re-review, and its final answer said the reviews passed
without parked findings. The upstream skill's Final Review section says
to dispatch one fix subagent when final review returns findings, followed
by one scoped re-review. The controller silently dropped this advisory
finding instead. The code remains correct, but this trial is not a strict
full-workflow pass. Successful finishing-skill handoff and cleanup do not
prove complete instruction compliance. No prompt workaround was added for
this failure.

## Test setup corrections and limits

Early live attempts returned CLI `SUCCESS` with an empty response and
`denied_actions`, because headless mode denied directory/test commands.
Windows command permissions needed regex rules matching commands with
arguments. These attempts are excluded from the successful trials.
An initial control also mistakenly used a fixture without a seeded defect;
the corrected control above replaced it.

The first subagent-workflow attempt hit the documented Windows/WSL path
boundary: `bash` resolved to WSL and could not open a `C:/...` helper path.
The model selected Git Bash next, but the temporary profile had not allowed
its absolute executable path. A retry then stopped at an unpermitted
read-only `Get-Command ... | Format-Table` discovery command. The successful
trial put Git Bash first on PATH, told the model that fact, and permitted
that installed executable plus read-only shell discovery. The runner now
includes this setup. No installer or upstream skill changes were made to
obtain these results, and no model-inheritance guidance was added.

These are individual controlled trials, not a reliability estimate. All
13 skills were exercised through the native transport, but only selected
workflows used a live model. Other Antigravity surfaces and Linux-native
Antigravity were not tested. The portable suite was rerun: 114 tests passed
on WSL Ubuntu; Windows ran 114 with five symlink-privilege skips.

## Reproduce

Use a local upstream checkout and `agy` on PATH. Output directories must
not already exist. The live runner additionally requires authenticated
CLI access, Git, Python, and Bash for upstream shell helpers.

```text
python tests/native_antigravity_checks.py --source <upstream-checkout> --output .superpowers-review/antigravity/native
python tests/live_antigravity_checks.py --source <upstream-checkout> --output .superpowers-review/antigravity/live
```

Use `--cases control,brainstorming` to select live trials, or
`--cases tdd-complete,sdd-complete` for implementation trials. The default
per-case live timeout is 480 seconds. The native runner uses assertions;
the live runner collects evidence for manual grading. Neither exit code
zero nor native `SUCCESS` alone is a workflow pass. Check denied actions,
tool errors, skill reads, test output, artifacts, reviews, and cleanup.

Raw local evidence is intentionally ignored by Git. The evaluated runs
are under `.superpowers-review/antigravity/final-native`,
`corrected-control`, `sdd-native-shell`, and `live-regex` (only its
brainstorming and TDD cases count as successful). Failed setup attempts
remain under `live-pilot`, `live-run`, and `sdd-gitbash`, plus the excluded
`live-regex` cases. Generated requests and transcripts are retained for
inspection; credentials and HTTP headers are not copied into the reports.
