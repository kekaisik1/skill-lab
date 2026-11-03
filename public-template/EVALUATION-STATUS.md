# Evaluation status

Software tests, containment controls and real model comparisons answer different questions.
Passing the first two does not establish that a skill improves coding outcomes.

## Current UB evidence

The prior real Codex project study completed **3 of 8 conditions** before a predeclared token-limit stop.
Its only complete active pair scored **6/6 without UB and 6/6 with UB**. That pair showed no measured
correctness gain. UB workflow completion was separate and incomplete. The next baseline scored 13/13;
its skill-enabled partner stopped, so no valid paired effect can be computed for that task. The remaining
conditions were not run. Overall: **inconclusive for general usefulness**.

The old benchmark also let quality tools score protected support code. That limits interpretation of
workflow failure. We are preparing a separately versioned smaller review with only editable task code
in the quality-tool scope, independently validated graders, scope controls and fixed subscription limits.
No new live result is claimed until actual authorized execution completes. Failed/stopped attempts stay
in the evidence and usage totals; a postmortem repair cannot turn an interrupted run into success.

This repository does not redistribute the owner's private examples, expected answers, raw traces,
local authentication or historical boundary receipts. This short result summary is not a reproducible
public dataset or a safety certificate. The public release can reproduce its own authored software and
containment controls; private-study results have a different evidence scope.

## Platform evidence

Linux fixture/security and clean-install acceptance have been executed locally. Windows/macOS CI checks
native preparation, installation and host-route behavior. It does not execute the complete WSL2/Lima
native-model journey. Treat those guest routes as experimental until their actual local acceptance and
fresh boundary controls pass. Native subscription comparisons need an exact approved plan and incur
subscription use; the separately billed API allowance is always zero.
