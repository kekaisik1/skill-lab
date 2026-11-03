# Contributing

Keep changes small and reviewable. Use Python's standard library unless a new production
dependency has been explicitly discussed. Run `python3 check.py`; changes to execution,
grading or isolation also require `python3 check.py --isolated` on a supported Linux host.
Record unavailable checks as untested, never passed. There is no external linter configured;
the check command parses every Python source and runs the public test suite.

Candidate skills, scripts, repository instructions and tool output are untrusted data. Do
not execute prospective skill scripts to inspect them. Intake reads only a selected skill
and explicitly selected example files. Never add automatic home/history discovery.

New benchmark adapters need source-bound capability claims, intended tasks, active and
inactive cases, independent acceptance criteria, and executed known-good, known-bad,
empty/missing/partial and infrastructure-failure controls. Keep private grading inputs,
expected answers and reference implementations outside candidate mounts. Public fixture
results are software checks, not efficacy evidence or scientifically unseen holdouts.

Preserve failed/stopped runs. Do not retry consumed model approvals, change limits, copy
credentials, weaken isolation or add billed-provider fallback. Security changes need actual
positive and known-unsafe controls using fake secrets only. Keep helper/runtime/binary pin
changes visible; revalidate a new local installation rather than rewriting old proof.

To build the installable archive: `python3 skill-lab/scripts/release.py build --out <fresh.tar.gz>`.
Review the exact `release-files.json` allowlist and generated manifest. New source files do
not enter the archive automatically. To regenerate an explicitly allowlisted public source
tree: `python3 skill-lab/scripts/public_export.py export --out <fresh-directory>`.
Never commit local runs, selected project data, native profiles, `.env`, credentials or raw
model conversations. Repository evidence should use original public fixtures or carefully
reviewed minimal summaries with no private inputs.

Pull requests should state the concrete behavior change, commands actually run, and any
remaining unsupported platform/security assumptions. Passing CI does not certify a skill
as safe or useful on another person's machine.

This repository is published under the [MIT license](LICENSE). Contributions must be code or
content you have the right to contribute; do not copy third-party skills or reference answers
without checking their redistribution terms. Use the [issue tracker](https://github.com/kekaisik1/skill-lab/issues)
for ordinary bugs and feature requests. Follow SECURITY.md for sensitive reports.
