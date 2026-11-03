# Windows, macOS and Linux

Skill Lab separates **inert preparation** from **isolated execution**. Inspection, selected-file
benchmark preparation and public example compilation use standard-library Python on the host.
Candidate execution always uses the Linux boundary, directly or inside an explicitly selected guest.
No native Windows/macOS subprocess fallback is allowed.

| Capability | Linux x86_64 | Windows | macOS |
| --- | --- | --- | --- |
| Install, inspect selected skills, prepare examples | Implemented | Implemented portable Python | Implemented portable Python |
| Execute isolated fixtures and native Codex adapter | Direct Linux | WSL2 Linux guest route | Lima Linux guest route |
| Execution evidence at this release | Actual local namespace and installed acceptance | Host code/route tests; real WSL2 acceptance pending | Host code/route tests; real Lima acceptance pending |

A green host unit job is not a guest acceptance result. Actual WSL2/Lima runs must pass guest doctor,
fresh boundary and installed acceptance before calling that environment validated. Linux containers
that block nested namespaces are unsupported; do not fix that by enabling privileged execution.

## Native preparation, no guest required

Install the extracted release with Python 3.10+ using `skill-lab/scripts/install.py --destination <your
Codex skills directory>/skill-lab --apply`. On Windows, use `py -3` if that is your Python launcher.
The destination is your explicit choice; the installer does not change another project or existing
owner-edited files. From the installed skill, run:

```sh
python scripts/launcher.py prepare --skill /selected/skill --intended-use "Normalize labels" --out /new/proposal
python scripts/launcher.py example --out /new/public-example
```

Use native host paths for these two operations (for example `C:\\Users\\you\\selected-skill` on Windows).
Only explicit selections are read. Candidate instructions/scripts are data, never executed. Preparing a
benchmark does not establish its semantic validity or supply an arbitrary-project executable grader.

## Windows execution through WSL2

Use a dedicated, already-running Linux x86_64 WSL2 distribution with the Linux prerequisites in
PORTABLE.md. Install Skill Lab and Codex **inside the Linux guest** and perform normal native Codex
ChatGPT login there; never copy the Windows Codex credential directory into it. Keep work and local
state on the guest's Linux filesystem, not the mounted Windows filesystem. The user manages WSL itself.

A dedicated distribution can disable automatic Windows drive mounts and Windows process interoperability
in `/etc/wsl.conf` using `[automount] enabled=false`, `mountFsTab=false` and `[interop] enabled=false`,
`appendWindowsPath=false`. These are optional host-reduction settings for a dedicated guest, not a
replacement for bubblewrap. Review impact before changing them; Skill Lab never edits this file.
The candidate boundary excludes host drives regardless, and verifies actual containment locally.

Configure only the host-side bridge, using the guest's actual absolute paths:

```powershell
py -3 scripts/platform_bridge.py configure --provider wsl --guest SkillLab --guest-launcher /home/you/.codex/skills/skill-lab/scripts/launcher.py --guest-state /home/you/skill-lab-state --out platform.json
py -3 scripts/platform_bridge.py doctor --config platform.json
py -3 scripts/launcher.py run offline --out /home/you/skill-lab-runs/first
```

Run guest setup first: `python3 <guest-launcher> setup --state /home/you/skill-lab-state`.
The bridge checks that the named distribution is running; it never selects a default distribution,
installs WSL, configures mounts or forwards WSLENV. The running-state check is best effort: WSL can
start that named guest if it stops between the check and dispatch. No promise of an atomic no-start
operation is made. Commands use `--exec` without a default Linux shell and use guest `/` as workdir.

## macOS execution through Lima

Use an explicitly selected Linux x86_64 Lima guest. On Apple Silicon this currently requires x86_64
emulation; native ARM Linux execution is not validated. This can be slower than Linux x86_64 hardware.
The user installs Lima/QEMU; Skill Lab does not download, install, start or configure those dependencies.
Lima's documented `--plain` mode disables automatic mounts, port forwarding and containerd. For example,
`limactl start --vm-type=qemu --arch=x86_64 --plain` creates a guest for you to name/select explicitly.
Use the actual guest name returned by your setup, not an assumed default.

Install the package, prerequisites and normal Codex login inside that guest. Do not mount the macOS
home/auth directory or forward your SSH agent as a convenience. Then configure the installed host skill:

```sh
python3 scripts/platform_bridge.py configure --provider lima --guest skill-lab --guest-launcher /home/you/.codex/skills/skill-lab/scripts/launcher.py --guest-state /home/you/skill-lab-state --out platform.json
python3 scripts/platform_bridge.py doctor --config platform.json
python3 scripts/launcher.py run offline --out /home/you/skill-lab-runs/first
```

The bridge invokes `limactl shell --workdir=/ <guest> -- <python> <launcher> ...` with argument arrays.
Lima quotes guest command arguments; no task text is interpolated into a shell program by Skill Lab.
Use `platform_bridge.py command --config platform.json -- <operation>` to inspect a route without running it.

## Context, reports and verification

Once routed, all run/report/state paths refer to **guest paths**. Prepare/example remain host-local even
when platform.json exists. Moving a selected benchmark into the guest is an explicit data-transfer action,
not permission to mount your home/history. Reports print back through the bridge to the calling chat.
The host application does not provide candidate authentication; Codex retains its login inside the guest.
`doctor` is offline readiness only. Native comparisons still need a reviewed exact plan, fresh boundary,
subscription preflight and approval. Claude evaluation is deferred.

platform.json is owner configuration: updates/removal preserve it. Delete or edit it explicitly to
change routes; it contains no credentials. The bridge does not certify its own VM/WSL isolation or let
mocked platform tests stand in for an executed guest journey.

Current primary documentation used for implementation:
- [WSL basic commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)
- [WSL configuration](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)
- [Lima shell usage](https://lima-vm.io/docs/usage/)
- [Lima foreign architecture](https://lima-vm.io/docs/config/multi-arch/)
- [Lima plain mode](https://lima-vm.io/docs/faq/)
