# Redistribution status for the local developer preview

This archive contains project-authored Python controller code, original public regression fixtures,
an original example skill, documentation and the explicit release manifest. It contains no downloaded
packages, Codex binary, UB/frontend skills, companion tool sources, project histories, private grading
files, credentials, raw runs, local configuration or historical boundary receipts.

The owner approved the MIT license on 2026-11-04 for the project-authored code, original
examples and documentation; see LICENSE. This does not relicense third-party code.
Third-party UB/frontend skills are deliberately not bundled; their licenses and transitive
companion rights require separate review if added later. Public reports must not include
private task contents or raw model traces without a separate review.

Runtime prerequisites are user-supplied, not redistributed: Python (PSF licensing), Linux and its
system libraries, bubblewrap (LGPL licensing), and an independently installed Codex CLI (upstream
Apache-2.0 project; verify the particular binary distribution and applicable service terms).
The archive does not install or download dependencies. Existing legacy provisioning code remains
for engine compatibility but is not invoked by the portable commands. A future package that bundles
binaries or wheels needs its own notices and source-offer review.

No telemetry or uploads are implemented in Skill Lab. The approved native live command necessarily
sends the selected task and skill prompt to Codex's native service. Public report export emits a small
fixed-field summary, never a directory copy. Users must separately decide whether to share any local
raw evidence; it can contain their selected source or task content.
