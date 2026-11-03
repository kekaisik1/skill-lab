# Evaluation protocol

## Intake and behavior profile

Inventory local regular files using `intake`; reject symlinks, special files and oversized sources. The scanner provides line locations without copying potentially sensitive source text into reports. Review both instructions and code for installs, downloads, credentials, transmission, persistent hooks, external binaries, destructive git/file operations, permissions and dependencies. Obfuscation, indirect imports, dynamic commands and prose can evade the scanner. Inspect dependency manifests, lockfiles and transitive executable paths manually. Record expected versus prohibited effects and allowlisted filesystem/network scope. Hash inventory changes invalidate prior approvals. A declared commit does not prove a download came from it. Compare trusted archive or git tree bytes independently.

## Adaptation

Easy means bounded behavior and few dependencies; medium crosses interfaces with some ambiguity; complex adds recovery, concurrency, migration or multiple dependency boundaries. Do not use line count as difficulty. Include a read-only or unrelated task to detect inappropriate skill activation. For a documentation skill replace code tasks with grounded accuracy, source preservation and scope checks; for a refactoring skill preserve observable behavior and test deceptive quality-metric improvements.

Before execution freeze task IDs, public prompts, environment/base commit, target capabilities, acceptance rubrics, trusted oracle bytes, reviewer identities and thresholds. Do not tune holdouts after viewing outputs. Later fixes use a new version and fresh holdouts. Keep public regression and private holdout pools separate. Booking examples in this kit are public because the previous failure mode is already known; real trials require unseen variants and fault-injected integration checks.

## Matched design and grading

Predeclare at least five pairs per task for a pilot (configurable 2–100), independent seeds when supported, the same model/version/reasoning budget/context/tools/base commit, and balanced ordering. Reset environments and memory between conditions. No cross-condition solutions or leaked summaries. Record trial IDs, order, seed, timestamps and failure status. Do not quietly discard failed runs; retain receipts and report attrition. When infrastructure prevents grading, mark invalid and rerun a complete pair under the predeclared retry cap. Zero quality and infrastructure invalidity are different.

Grade functional outcomes with trusted external tests; test edits do not count as success. Review safety violations and changed files independently. Give qualitative graders randomized artifact identifiers without condition labels or candidate logs; disclose residual clues (code style, comments). Deterministic oracle grading in the demo sees outputs and task IDs only, but the orchestration process sees conditions. This is limited blinding, not independent personnel or OS isolation.

Require zero critical regressions and no unsafe side effects. Report paired differences and distributions by task and difficulty, negative controls, failures and resource overhead. The pilot defaults require gain >= 0.10 and total token ratio <= 1.50 with zero regressions; these are provisional configurable decision criteria, not validated universal standards. Repeated identical fixtures are plumbing checks, not independent statistical samples. A real study needs more diverse tasks, paired uncertainty intervals and task-clustered analysis before generalizing.

## Context, tokens and costs

Record provider-reported input, output and cached input tokens separately; define whether input already includes cache to avoid double counting. Total-token ratio here is input + output and assumes input includes cached input. Count validation/grader/subagent tokens separately as well as an end-to-end total; never omit the overhead needed to validate the skill. Record tool calls, wall time, compaction count, peak actual context occupancy, context capacity, retries and truncation/retrieval failures. Context size is not cumulative tokens. Unknown instrumentation is null, not estimated fact. Include progressive context-load conditions (short, near half capacity, near supported limit), with equivalent relevant/distractor material in both arms. Freeze those conditions before running.

Record provider/model/version, usage source, tariff date and billing mode. Subscription quota consumption and paid API dollars are distinct; unavailable quota telemetry remains unknown. Track task success per total token and validation overhead only when underlying data is measured. Store sanitized receipts; never raw credentials or private sessions. Hash local receipts for change detection, but do not call them authenticated provider proof.

## Approval checklist for first real trial

- Verify exact candidate and companion bytes and transitive dependencies; review all executable actions.
- Choose stock UB with its mandatory tools, or an explicitly named UB-derived intervention.
- Approve specific runtime/download/install commands and resource caps, never blanket skill execution.
- Provision isolated scratch environments without credentials or live repositories; deny persistent hooks and pushes.
- Place trusted hidden checks in evaluator-only storage and verify candidate inability to read them.
- Freeze model/version, subscription channel, budgets, context levels, task commits, toolchain, rubric and retry policy.
- Approve model access and disclosure scope; no paid API billing without separate approval.
- Independently grade, retain every receipt, report invalid trials and stop on critical safety failures.

The intake helper is intended for quiescent, reviewer-owned local snapshots. It does not provide race-proof ingestion against another process mutating the same directory. It rejects traversal errors and empty inventories; for adversarial concurrent writers, first acquire a trusted read-only snapshot under an isolated identity. Keep output paths in a reviewer-owned directory without untrusted parent symlinks.

The report exposes candidate `total_token_ratio` and separate `end_to_end_token_ratio`. For real numeric screening, `end_to_end_tokens` must include candidate, tool-model, reviewer, validation and retry token usage without double-counting. The controller must substantiate this number. Imported records always remain unverified/inconclusive in this implementation even when numeric thresholds are met.

## Runnable purpose mappings and partial reports (schema 2)

`manifests/ub-purpose-reviewed.json` maps the pinned original core to explicit
capabilities: boundary repair, root-cause repair, recovery/concurrency, and
companion-guided quality improvements. It also includes a read-only negative
control. Every task includes an adaptation rationale tied to the original
sections. This mapping is an authored design requiring independent semantic
review; neither the skill name nor a keyword scan can establish what makes a
valid benchmark. The example has unresolved model/environment controls and
must not be dispatched as a real experiment.

For a new skill, use its own intake inventory hash in
`purpose.source_sha256`, describe its claimed benefit in `purpose.claim`, name
the human or agent reviewer in `purpose.reviewer`, and enumerate its intended
`purpose.capabilities`. Each intended capability must match at least one task's
`capability`; every task, including controls, requires an
`adaptation_rationale`. Keep all three difficulty levels among the in-scope
tasks. The `plan` command validates this structure and binds it to the source
inventory; it does not independently validate the reviewer's interpretation.
Existing demo manifests without a purpose mapping remain readable, with an
explicit readiness gap. Real dispatch must resolve those gaps.

A report accepts missing trials and explicit statuses `infrastructure_failure`,
`timeout`, `cancelled`, `resource_limit`, and `safety_failure`. It emits retained
attrition and safety lists rather than treating failures as zero correctness or
aborting the report. Unknown statuses, duplicate/foreign records, changed
manifest hashes, malformed numeric grades, and invalid telemetry are rejected.
Only pairs with two completed outcomes enter quality comparisons. Effects from
remaining pairs are descriptive and may be biased by attrition. Negative
controls are reported separately from the difficulty summaries; they still
contribute to the overall regression count. A safety concern prevents numerical
screening even when functional outputs pass. Imported data remains unverified.

`usage_means`, `usage_totals`, and `usage_coverage` include failed attempts.
`cpu_seconds`, `peak_rss_bytes`, and `disk_bytes` complement token and wall-time
fields. Record how the controller measured these fields; they are not inferred
from output text. Unknown telemetry is null; malformed or negative numbers are
not silently converted to zero. Peaks and context capacities have no additive
total. A null total with partial coverage must not be described as free work.
The local JSON oracle validates its frozen check hash at grading time; hashes
remain tamper detection, not authentication against an actor owning all files.
