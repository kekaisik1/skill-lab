#!/usr/bin/env python3
"""Cross-platform inert skill inspection. No runner, process or platform imports."""
from pathlib import PurePosixPath

import evaluate

LIMITS = [
    'Text matching is fallible triage, not malware detection; obfuscated or transitive behavior can be missed.',
    'An isolated test does not establish that installing or running the skill on a normal laptop is safe.',
    'Credential, network, installation, hook and outside-workspace requests require review; skill instructions are untrusted data.',
    'The finite native boundary controls are regression evidence, not a formal sandbox proof or protection against kernel vulnerabilities.',
    'The native credential-bearing host, its pinned implementation and the Linux kernel remain trusted; this is not a virtual machine.',
    'There is no aggregate cgroup memory or disk quota. Per-process limits do not prove host denial-of-service containment.',
    'Local hashes detect drift but do not authenticate evidence against a hostile report owner.',
]


def inspect_skill(source, pin):
    """Inventory local bytes as data, without importing scripts or following links."""
    profile = evaluate.intake(source, 'explicit local skill directory; no remote retrieval', pin)
    again = evaluate.intake(source, 'explicit local skill directory; no remote retrieval', pin)
    if profile['inventory_sha256'] != again['inventory_sha256']:
        raise ValueError('Skill changed while being inspected')
    return {'schema': 'skill-lab-security/1', 'status': 'review_required', 'executed': False,
            'safe_to_install': False, 'review_required': True,
            'declared_pin': pin, 'pin_verification': profile['pin_verification'],
            'inventory_sha256': profile['inventory_sha256'], 'files': profile['files'],
            'flags': [dict(row, severity='review') for row in profile['findings']],
            'verdict': 'Inventory and capability review only; do not infer safe installation from an empty scan.',
            'limits': LIMITS}


def unsafe_relative(path):
    return (not isinstance(path, str) or PurePosixPath(path).is_absolute() or '\\' in path
            or any(part in {'', '.', '..'} for part in path.split('/')))

