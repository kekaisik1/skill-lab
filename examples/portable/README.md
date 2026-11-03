# Public extension example

This original synthetic example demonstrates intake and benchmark preparation for a fourth skill, `boundary-normalizer`. It contains no imported skill, real project, user history or private grading corpus. Its cases and expected answers are deliberately public regression fixtures, not scientifically unseen holdouts. Distribution remains subject to the project owner's release-license decision.

From a source or extracted release root:

```bash
python3 skill-evaluator/scripts/preview_intake.py example --out /tmp/skill-lab-example-NEW
python3 skill-evaluator/scripts/preview_intake.py validate --out /tmp/skill-lab-example-NEW
```

The equivalent user-selected workflow is:

```bash
python3 skill-evaluator/scripts/preview_intake.py prepare \
  --skill examples/portable/skill \
  --intended-use 'Repair Python normalization; explain without activating on read-only requests.' \
  --example examples/portable/selected/request.txt \
  --draft examples/portable/draft.json \
  --out /tmp/skill-lab-selected-NEW
```

Select individual example files with repeated `--example`; directories, links and special files are refused. The skill directory is explicitly selected and bounded; its scripts are inspected as data, never executed. Omit `--draft` to get a pinned intake and authoring requirements first. Author schema-1 drafts using this example, with exact quotes from the selected skill, `example-1` etc. links, an independent acceptance criterion per task, explicit activation and nonactivation, and expected outcomes. The compiler rejects unsupported or inconsistent mappings.

`private/` in an output is local sensitive preparation data, not a security boundary and not a shareable export. Candidate packets receive only public inputs and starter files; never mount the output's private directory or grader into a candidate environment. The compiler's oracle replay verifies its own good/bad/missing-answer controls; it does not execute a proposed repair. An independent reviewer must check semantic validity, ground truth, coverage and leakage, and a trusted task-specific executable adapter must be implemented before live evaluation. Neither this extension nor its intake grants permission to run arbitrary skills or projects. No model calls, uploads or telemetry occur.
