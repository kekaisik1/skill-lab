# Inert external runner contract

There is intentionally no adapter loader, shell-command manifest, model call, install command or credential reader in this package. Implementing a runner is a later reviewed change, not something candidate instructions may supply.

A future trusted controller takes a frozen plan hash, verified source inventory hash, approved action allowlist, base commit/toolchain digest, fresh workspace ID, model/version/reasoning/context settings and hard budgets. It supplies only public task input and the selected condition to the candidate. Private test code and expected values remain in an evaluator-only process/container/host outside all candidate mounts; removing a filename from a prompt does not protect it. Network denial, read-only mounts, process/resource limits, short timeouts and secret-free environment must be enforced externally, not asserted in JSON.

Runner output: artifact hash and changed-file inventory, trial/pair identifiers, UTC start/end, exit status, completed/infrastructure_error/timeout/policy_block status, approved action log, actual model settings, input/output/cached tokens, context occupancy/capacity, compactions, tool and validation usage, wall time, billing mode and provider receipt hash. Null telemetry is allowed. Candidate test output is diagnostic only. Trusted evaluator reconstructs the patch against the frozen base in a separate bounded environment and runs frozen tests. It then produces condition-blind grades, including critical failures and safety violations, linked to artifact and oracle hashes. Join conditions only after grading.

The current `report` command aggregates locally supplied records; it cannot authenticate their producer. Treat imported scores as unverified until matched against independent evaluator receipts. It must never be used to certify candidate self-reported performance. `demo` creates trusted local synthetic records and marks them synthetic. A successful static review or demo cannot authorize the external runner.

A separate authored-fixture-only process runner now demonstrates namespace isolation and controller grading; see process-runner.md. It does not implement this external candidate/model contract.

## Skill lab direction — subscription-only adapters

The selected product is a general skill lab, with full UB as its first subject.
Users explicitly select local project snapshots or sanitized history excerpts.
An authoring agent analyzes the actual pinned skill, proposes claims/tasks and
cites the selected workflow examples. `benchmark_builder.py` validates and
freezes that draft; it does not replace semantic authoring or independent review.
The same task artifacts and grading criteria must work across adapters for Codex,
Claude Code or another coding agent. Support is earned by executable adapter
conformance tests, not by accepting an agent name in configuration.

For this product route the billing mode is `existing_subscription`. No paid API,
API-key discovery, API-key fallback or automatic purchase is allowed. User
subscription authentication stays in the trusted agent host. The candidate task
namespace sees neither credentials nor evaluator-private answers. A native tool
call that can bypass the controller blocks that adapter. The old Codex auth-mount
route remains retired. Claude's documented native-tool-disable interface is a
candidate for offline fake-provider tests, not a validated adapter yet.

Adapter conformance must establish: fresh session and workspace, frozen agent
version/settings and skill bytes, actual task/tool/artifact events, bounded
request/output handling, cancellation/cleanup, no hidden tool path, and truthful
failure/usage reporting. Provider-reported tokens and subscription quota changes
are nullable; no API-dollar estimate stands in for subscription consumption. A
quota error, uncertain completion or timeout consumes the attempt and stops the
run; do not retry or silently change accounts/models.

Both arms get the same toolchain, environment and public task. Only the exact
skill differs. Full UB requires actual Crapper/Dryer/Mutator runs, meaningful
metric/test counts, work logs and appropriately scoped commits/pushes to the
DISPOSABLE LOCAL remote. A structured answer about those actions is not evidence
that they occurred. Read-only controls must show no tool activation in observed
runtime events. Text claiming `activated: false` is insufficient.

Sharing is separate from local evaluation. Raw histories and projects remain
private by default; `shareable` metadata never automatically uploads or publishes
anything. A later export must show the exact sanitized package to its owner and
exclude references, solutions, credentials and private provenance unless explicitly
selected. General-purpose/private history ingestion and publication are not yet
implemented by the current compiler.
