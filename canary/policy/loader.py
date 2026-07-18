"""Policy loading and validation.

Strict: unknown keys, unknown fields, and unparseable conditions are
errors at load time, not surprises at evaluation time. Conditions are
parsed into (field, op, value) — no expression evaluation.
"""
import operator
import os

import yaml

DEFAULT_POLICY = os.path.join(os.path.dirname(__file__), 'epic.yaml')

TOP_KEYS = {'policy', 'version', 'evidence', 'verdicts', 'recovery'}
EVIDENCE_KEYS = {'sample_max_age_hours', 'min_jobs'}
RECOVERY_KEYS = {'passive'}
CONDITION_FIELDS = {'failure_rate', 'njobs', 'wait_median_s', 'wait_p90_s'}
OPS = {'>=': operator.ge, '>': operator.gt, '<=': operator.le,
       '<': operator.lt, '==': operator.eq}


class PolicyError(Exception):
    """A policy document that cannot be loaded."""


def _parse_condition(text):
    parts = str(text).split()
    if len(parts) != 3:
        raise PolicyError(f'condition must be "field op value": {text!r}')
    field, op, value = parts
    if field not in CONDITION_FIELDS:
        raise PolicyError(f'unknown condition field {field!r} in {text!r}')
    if op not in OPS:
        raise PolicyError(f'unknown operator {op!r} in {text!r}')
    try:
        value = float(value)
    except ValueError as e:
        raise PolicyError(f'non-numeric value in {text!r}') from e
    return {'field': field, 'op': op, 'value': value, 'text': str(text)}


def load_policy(path=None):
    """Load and validate a policy file. Returns the policy dict with
    parsed rule conditions. Raises PolicyError on any fault."""
    path = path or DEFAULT_POLICY
    try:
        with open(path, encoding='utf-8') as f:
            doc = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        raise PolicyError(f'policy read failed: {path}: {e}') from e
    if not isinstance(doc, dict):
        raise PolicyError(f'policy is not a mapping: {path}')
    unknown = set(doc) - TOP_KEYS
    if unknown:
        raise PolicyError(f'unknown policy keys: {sorted(unknown)}')
    for key in ('policy', 'version', 'evidence', 'verdicts'):
        if key not in doc:
            raise PolicyError(f'policy missing {key!r}')
    if set(doc['evidence']) - EVIDENCE_KEYS:
        raise PolicyError(f'unknown evidence keys: '
                          f'{sorted(set(doc["evidence"]) - EVIDENCE_KEYS)}')
    if set(doc.get('recovery', {})) - RECOVERY_KEYS:
        raise PolicyError(f'unknown recovery keys: '
                          f'{sorted(set(doc["recovery"]) - RECOVERY_KEYS)}')
    rules = []
    for rule in doc['verdicts']:
        if set(rule) != {'verdict', 'when'}:
            raise PolicyError(f'rule must have exactly verdict+when: {rule}')
        rules.append({'verdict': str(rule['verdict']),
                      'when': _parse_condition(rule['when'])})
    doc['verdicts'] = rules
    doc['version'] = str(doc['version'])
    doc.setdefault('recovery', {}).setdefault('passive', False)
    doc['path'] = path
    return doc
