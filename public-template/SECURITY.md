# Security and privacy

Skill Lab's candidate instructions are untrusted data. Inspection never executes them.
Actual execution requires a working Linux namespace boundary and fresh fake-secret positive
and negative controls. The native Codex host retains its existing supported authentication;
authentication is never copied into candidate mounts or model-facing tools.

Security evidence is separate from usefulness. A clean heuristic scan can miss obfuscated or
transitive behavior. Hashes detect local drift, not publisher identity or forged evidence
from an attacker controlling the machine. The Linux kernel, native host and trusted controller
remain assumptions. Per-process resource limits are not an aggregate cgroup memory/disk quota.
Normal-laptop installation of a tested skill is not certified safe by isolated test results.

Do not include real credentials, user histories, private source or raw responses in public
issues. Report suspected issues privately through GitHub private vulnerability reporting if
the repository owner has enabled it; otherwise contact the owner through an agreed private
channel. There is no advertised security inbox until the owner provides one.

Useful reports include the affected version/file hashes, platform and boundary stage, and a
minimal reproduction using fake secrets only. A test must demonstrate both that allowed work
actually executes and that a deliberately unsafe fake setup is detected. Never disable a
boundary, expose real host secrets, or add `--privileged` merely to obtain passing results.

This project has no telemetry or automatic uploads. Public report export includes a small
fixed summary. Local run directories can contain selected source, task prompts and model
outputs: review them separately before sharing. Approved native inference necessarily sends
the selected prompt through the user's native Codex service and consumes subscription quota.
