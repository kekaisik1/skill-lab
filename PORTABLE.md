# Skill Lab local developer preview

This package installs a self-contained Codex skill. Native Windows/macOS skill inspection and
benchmark preparation plus optional WSL2/Lima execution routes are described in [PLATFORMS.md](PLATFORMS.md).
Guest execution remains experimental until verified on those hosts. Linux execution is verified locally. It supports offline evaluator controls,
selected local skill intake, source-bound benchmark preparation, human decision reports and an original
four-session native Codex normalization smoke adapter. Arbitrary projects are not automatically graded.
UB/frontend/private project studies are not bundled. Claude is deferred.

## Prerequisites and support boundary

Linux x86_64, Python 3.10+, `/usr/bin/python3`, `/usr/bin/bwrap`, `/usr`, `/lib`, `/lib64`, working
unprivileged user/PID/network namespaces. The controller uses only Python's standard library.
Install prerequisites through your distribution; this package downloads nothing. Setup checks these
requirements; doctor executes an actual namespace command and preserves actionable stderr on failure.
Do not disable isolation when your container/security policy blocks namespaces.

Native runs additionally need the native Codex ELF executable and sibling `codex-code-mode-host` and
`../codex-resources/bwrap`, with an existing native ChatGPT subscription login. Setup discovers PATH's
Codex npm launcher or accepts `--codex /path/to/native/bin/codex`; `SKILL_LAB_CODEX` is another override.
The installed CLI must pass the full fresh fake-provider boundary: merely having a version number is
insufficient. Experimental App Server surfaces can change between builds. No tokens are copied and no
API-key/external-token authentication route is used. The separately billed API allowance is always $0.
The lab never falls back to Claude or a billed API.

## Install and use

Extract the local artifact into a new directory and review REDISTRIBUTION.md for the owner-approved MIT license and excluded third-party material.
From the extracted `skill-lab-preview` directory:

```sh
python3 skill-lab/scripts/release.py verify .
# Preview first; add --apply to perform the same install.
python3 skill-lab/scripts/install.py --destination "$HOME/.codex/skills/skill-lab"
python3 skill-lab/scripts/install.py --destination "$HOME/.codex/skills/skill-lab" --apply
```

Any destination is supported, including a disposable home or project skill directory; the installer
never selects a global destination implicitly. Restart/reload Codex skill discovery as needed. Ask
`$skill-lab list`, then `$skill-lab run offline`. The calling Codex agent should resolve the launcher
relative to the installed SKILL.md and return the actual human report in the same chat.
Automatic discovery by a newly started interactive Codex session is not verified by CLI acceptance.

The same journey can be invoked explicitly (choose fresh state and output directories):

```sh
LAB="$HOME/.codex/skills/skill-lab/scripts/launcher.py"
python3 "$LAB" list
python3 "$LAB" setup --state /tmp/my-lab-state
python3 "$LAB" doctor --state /tmp/my-lab-state
python3 "$LAB" plan offline
python3 "$LAB" run offline --out /tmp/my-lab-offline
python3 "$LAB" report /tmp/my-lab-offline
python3 "$LAB" report /tmp/my-lab-offline --export /tmp/my-lab-public-summary
python3 "$LAB" boundary --state /tmp/my-lab-state --out /tmp/my-lab-boundary
python3 "$LAB" example --out /tmp/my-lab-benchmark-example
```

Keep state and evidence **outside the installation's kit/** directory. Kit is immutable release content.
Missing artifacts, skipped cases, drift and interrupted execution produce nonzero incomplete reports.
Report export contains only fixed decision fields and a short summary, not raw evidence, source,
paths, histories or private grading answers. Export does not upload anything. Local evidence can contain
selected data and must be reviewed separately before sharing.

## Select another skill and examples

```sh
python3 "$LAB" prepare --skill /selected/skill --pin declared-revision \
  --intended-use 'Normalize labels while preserving verbatim display requests' \
  --example /selected/request.txt --example /selected/component.py --out /tmp/my-proposal
```

Examples are optional for inert intake; a runnable benchmark draft requires explicit examples.
Only the explicitly selected skill tree and regular selected example files are read; symlinks/special
files and oversized inputs are refused. No shell scripts or instructions in a prospective skill are
executed. Review capability flags and actual hashes; a declared revision is not an authenticated pin.
No clean scan is a safe-install certificate. Selected examples remain local.

Use the original `examples/portable/draft.json` as an authoring schema example. Each task connects exact
skill quotes, selected example IDs, activation/nonactivation, observable acceptance and independent
criteria. Pass an authored `--draft /selected/draft.json` to prepare a frozen compiler bundle.
`example` exercises build, validate and oracle selftest. Oracle replay is compiler regression evidence,
not executable task validation. An independent reviewer must validate task relevance, difficulty,
criteria, grader and good/bad/empty/partial controls before a new adapter can execute it. Never let the
candidate edit the grader or see private references, expected answers or other held-out cases. Public
examples are regression fixtures, never scientifically unseen holdouts.

Only `offline` and `codex-demo` are portable runnable adapters. A prepared arbitrary skill cannot be
passed to run. `codex-demo` uses the original bundled trim-labels fixture, not an imported skill.

## Native comparison: separate approval required

Do not run this as part of installation or offline acceptance. Prepare a plan with explicitly selected
common instruction files (if native Codex loads any) and configured MCP names that must be disabled:

```sh
python3 "$LAB" plan codex-demo --state /tmp/my-lab-state \
  --instruction /selected/common-AGENTS.md --disable-mcp selected-server --out /tmp/native-plan.json
```

Omit those options if there are none. Unexpected instruction sources or enabled MCP servers stop the
native host before a candidate turn; no global config is edited. The plan binds exact local hashes,
model `gpt-6.1-sol`, low effort, four sessions (baseline/skill active and nonactivation), 16 tools per
session, active/read-only limits of 300/90 seconds and 32,000/8,000 observed tokens. The summed thresholds
are 80,000 tokens and 780 seconds, not a provider-side spending cap. One response can overshoot a token
threshold; native transport retries are not independently capped. Quota remaining and dollar-equivalent
subscription use are unknown. Controller retries are zero; stop on boundary/pin drift, failed controls,
host failure, invalid artifacts, rejected calls, limit or interruption. API allowance $0.

After the owner approves that exact file and hash, the calling agent may use:

```sh
python3 "$LAB" run codex-demo --plan /tmp/native-plan.json \
  --approved-plan-sha256 EXACT_APPROVED_HASH --out /tmp/native-run
python3 "$LAB" report /tmp/native-run
```

A plan hash is a binding, not evidence of user consent: the calling agent must obtain approval. A consumed
approval cannot be reused. Every run creates new boundary controls before native login preflight/dispatch;
no distributed or historical receipt authorizes execution. Fresh setup pins local binaries and helpers;
changes require review and a new state, never silently updated pins. Authentication stays in the native
host outside candidate mounts/tools. Candidates receive only their current work tree and public prompt;
grading mounts only the frozen artifact and current input. Trusted kernel/native host remain assumptions;
there is no aggregate cgroup disk/memory quota. The finite controls are not a universal safety proof.

## Updates and removal

Run the new extracted release's installer with the same destination to update. It checks all collisions
before changing files and refuses owner-edited collisions. Save/reconcile edits explicitly; there is no
force-overwrite switch. Do not manually overwrite the kit. Removal from an extracted release:

```sh
python3 skill-lab/scripts/install.py --remove --destination "$HOME/.codex/skills/skill-lab" --apply
```

Only unchanged owned files are removed. Edited and unrelated files, separately stored state and runs
remain. Inspect the receipt for preserved files. Local ownership hashes are not signatures against an
attacker who can rewrite the installation or controller.

## Developer verification

In the source project: `python3 check.py --isolated`. Build a fresh archive:
`python3 skill-lab/scripts/release.py build --out /tmp/skill-lab-preview.tar.gz`.
Run `python3 acceptance.py --artifact /tmp/skill-lab-preview.tar.gz --out /tmp/skill-lab-acceptance
--codex /path/to/native/bin/codex` (one command line). It mounts only system runtime, public native
executables and a disposable directory: the original checkout and real home are absent. It installs,
renames the extracted source away, runs the installed commands, fresh security/native controls and
failure/report/ownership checks. All native provider traffic in acceptance is fake local traffic.
No real model calls or real authentication are used. See the project handoff for actual executed results;
new native demo performance and another person's interactive skill discovery still require validation.
