# Skill Lab

[Source and releases](https://github.com/kekaisik1/skill-lab) · [Issue tracker](https://github.com/kekaisik1/skill-lab/issues) · [MIT license](LICENSE)

Skill Lab helps you decide whether a coding-agent skill helps on the work you care about.
It keeps task correctness, activation, workflow, security and resource use separate, with
local receipts you can inspect instead of trusting an agent's success claim.

Released under the MIT license. Third-party skills and tools keep their own licenses and are not bundled.

This is a **developer preview**. Start with the offline demonstration: it makes zero model
calls and shows known-good acceptance, known-bad rejection, activation/nonactivation,
missing-artifact rejection and auditable reports.

## What you can do today

- Install a self-contained Codex skill in a destination you choose.
- Inspect a local skill as untrusted data without running its scripts.
- Prepare a source-bound benchmark from your intended use and optional selected files.
- Run the bundled offline controls and read a human decision report in the calling chat.
- Prepare an exact bounded Codex subscription comparison for explicit approval.

Arbitrary project grading is not automatic. A new skill/task needs an independently
reviewed executable adapter. Public examples are regression fixtures, not scientifically
unseen holdouts. A clean scan or passing fixture does not certify safe installation.

## Quick start

Download the [developer prerelease](https://github.com/kekaisik1/skill-lab/releases/tag/v0.2.0-dev),
extract `skill-lab-0.2.0-dev.tar.gz`, and open the `skill-lab-preview` folder. The asset includes
its exact content manifest; it contains no credentials, private experiments or installed dependencies.
For Windows/macOS **native preparation only**, install and try the public example:

```sh
python3 skill-lab/scripts/install.py --destination "$HOME/.codex/skills/skill-lab" --apply
python3 "$HOME/.codex/skills/skill-lab/scripts/launcher.py" example --out "$HOME/skill-lab-example"
```

On Windows PowerShell, replace `python3` with `py -3` and `$HOME` with `$env:USERPROFILE`. To evaluate candidate code, follow
[Windows WSL2 / macOS Lima setup](PLATFORMS.md). Guest execution is an experimental route and
must pass its own local checks. Linux users can use the direct engine below.


The evaluation engine uses a Linux guest with Python 3.10+, distribution Python, bubblewrap
and working user/PID/network namespaces. See [platform/setup instructions](PORTABLE.md)
for Linux and the Windows/macOS host route and its validation limits. Windows/macOS host
checks do not establish that a guest or real Codex login has been tested on those systems.

From this source checkout, inside the supported Linux environment:

```sh
python3 check.py
python3 check.py --isolated
python3 skill-lab/scripts/release.py build --out /tmp/skill-lab-preview.tar.gz
mkdir /tmp/skill-lab-unpacked
tar -xzf /tmp/skill-lab-preview.tar.gz -C /tmp/skill-lab-unpacked
cd /tmp/skill-lab-unpacked/skill-lab-preview
python3 skill-lab/scripts/release.py verify .
python3 skill-lab/scripts/install.py --destination "$HOME/.codex/skills/skill-lab" --apply
LAB="$HOME/.codex/skills/skill-lab/scripts/launcher.py"
python3 "$LAB" setup --state /tmp/skill-lab-state
python3 "$LAB" doctor --state /tmp/skill-lab-state
python3 "$LAB" run offline --out /tmp/skill-lab-example
python3 "$LAB" report /tmp/skill-lab-example
```

Use new state/run directories; existing results are never overwritten. No dependency is
downloaded by these Python commands. Review system prerequisites before installing them.
After Codex discovers the installed skill, ask `$skill-lab run offline` and have it return
the verified report directly in your chat. See [PORTABLE.md](PORTABLE.md) for update/removal,
selected-file intake, benchmark authoring and failed/interrupted report commands.

## Real comparisons and privacy

The native adapter uses an existing ChatGPT-authenticated Codex subscription. The native
host retains authentication outside candidate mounts and model-facing tools. No separately
billed API fallback exists; the allowance is $0. Claude is not a supported evaluation backend
in this preview.

Setup and offline tests do not authorize inference. Before a live comparison, review the
exact model/configuration, skill/task hashes, session count, token/time/tool limits and
stopping rules, then approve that plan once. Every execution must pass fresh local boundary
controls. Changed security pins stop execution rather than refreshing themselves.

Selected examples, raw prompts/responses and private answers stay local. There is no lab
telemetry or automatic upload. Report export writes a small fixed summary, not a copy of
sources or raw evidence. An approved native comparison sends its selected public task/skill
prompt through Codex's native service; it is not an offline operation.

See [current evaluation status](EVALUATION-STATUS.md) for the actual incomplete UB findings and the distinction between software tests and model performance.

## Evidence and limitations

`check.py --isolated` executes public authored fixtures and fake-secret controls. It does not
measure real model performance. Host CI on Windows/macOS checks inert intake/compiler, route logic and packaging,
not the Linux guest's containment. Linux CI must fail if required namespaces are unavailable;
we do not disable isolation to turn that failure green. Native CLI dispatch and login need
separate local validation against the exact installed binary/profile.

No historical private study data, user histories, credentials, third-party skills or local
security proof are distributed here. Legacy engine modules are retained for compatibility;
the public preview CLI exposes only its documented supported adapters. Past results from a
different machine do not authorize your installation.

Read [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), and
[REDISTRIBUTION.md](REDISTRIBUTION.md) before sharing or extending the package.
