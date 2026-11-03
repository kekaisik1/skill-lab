# Evaluation status

Software tests, containment controls and real model comparisons answer different questions.
Passing the first two does not establish that a skill improves coding outcomes.

## Completed focused UB pilot — 2026-11-04

A new six-session comparison completed using native Codex subscription authentication,
gpt-6.1-sol with low reasoning and unchanged full UB revision
`57a8bdd28e45ed41900ace0bafa2ff76dd9ed70f`. Two compact authored coding tasks each ran
once with and without UB; an explanation-only task supplied the matched nonactivation control.

| Observed measure | Baseline | UB |
| --- | ---: | ---: |
| Input-selection correctness | 10/10 | 10/10 |
| Idempotency correctness | 10/10 | 10/10 |
| Complete requested quality workflow | 1/2 tasks | 2/2 tasks |
| Explanation-only control | Passed, zero tools | Passed, zero tools |
| Active-task observed tokens | 165,604 | 177,938 |
| Active-task summed host time | 106.600 s | 114.911 s |

UB completed the requested mutation-testing step that the baseline omitted on one task.
Both arms had the same task requirements, tools and limits; workflow diagnostics did not
inflate correctness scores. Unlike the older study, quality tools saw only editable task
code. UB used 7.45% more observed tokens and 7.80% more active-task host time in aggregate;
the direction varied by task. This is a narrow observed workflow benefit, with **no measured
correctness improvement**. One pair per task cannot establish a repeatable effect or general
usefulness, and these authored cases are not scientifically unseen holdouts.

All six sessions used 362,507 observed tokens, 28 model responses, 52 tool calls and 236.100
seconds of summed host time. The 240,128 cached input tokens are already included in the
356,606 input tokens; output was 5,901 tokens. Time excludes setup, controls, grading and
review. Subscription quota consumption and monetary equivalent are unknown; separately
billed API allowance was $0, with no fallback or controller retry.

The run passed its independently reviewed grader controls, fresh fake-secret containment
controls and local native-dispatch boundary before inference. These controls and a clean
run are not a safe-to-install certificate. Input preservation checks are same-interpreter
diagnostics, not a formal defense against adversarial introspection.

## Earlier incomplete project study

The prior real Codex project study completed **3 of 8 conditions** before a predeclared token-limit stop.
Its only complete active pair scored **6/6 without UB and 6/6 with UB**. That pair showed no measured
correctness gain. UB workflow completion was separate and incomplete. The next baseline scored 13/13;
its skill-enabled partner stopped, so no valid paired effect can be computed for that task. The remaining
conditions were not run. Overall: **inconclusive for general usefulness**.

The old benchmark also let quality tools score protected support code. That limits interpretation of
workflow failure. The completed pilot above is separately versioned and does not complete or replace
the old study. Failed/stopped attempts stay in that study's evidence and usage totals; a postmortem
repair cannot turn an interrupted run into success. The new pilot's usage totals exclude those older attempts.

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
