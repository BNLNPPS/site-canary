"""Policy loading and validation.

Strict: unknown keys, unknown fields, and unparseable conditions are
errors at load time, not surprises at evaluation time. Conditions are
parsed into (field, op, value) — no expression evaluation.
"""
import operator
import os

import yaml

DEFAULT_POLICY = os.path.join(os.path.dirname(__file__), 'epic.yaml')

TOP_KEYS = {'policy', 'version', 'evidence', 'verdicts'}
EVIDENCE_KEYS = {'sample_max_age_hours', 'min_jobs'}
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
    if not isinstance(doc['evidence'], dict):
        raise PolicyError('policy evidence must be a mapping')
    unknown_evidence = set(doc['evidence']) - EVIDENCE_KEYS
    if unknown_evidence:
        raise PolicyError(f'unknown evidence keys: '
                          f'{sorted(unknown_evidence)}')
    missing_evidence = EVIDENCE_KEYS - set(doc['evidence'])
    if missing_evidence:
        raise PolicyError(f'missing evidence keys: '
                          f'{sorted(missing_evidence)}')
    max_age = doc['evidence']['sample_max_age_hours']
    min_jobs = doc['evidence']['min_jobs']
    if (not isinstance(max_age, (int, float))
            or isinstance(max_age, bool) or max_age <= 0):
        raise PolicyError(
            'sample_max_age_hours must be a positive number'
        )
    if (not isinstance(min_jobs, int)
            or isinstance(min_jobs, bool) or min_jobs <= 0):
        raise PolicyError('min_jobs must be a positive integer')
    if not isinstance(doc['verdicts'], list):
        raise PolicyError('policy verdicts must be a list')
    rules = []
    for rule in doc['verdicts']:
        if not isinstance(rule, dict):
            raise PolicyError(f'verdict rule must be a mapping: {rule!r}')
        if set(rule) != {'verdict', 'when'}:
            raise PolicyError(f'rule must have exactly verdict+when: {rule}')
        rules.append({'verdict': str(rule['verdict']),
                      'when': _parse_condition(rule['when'])})
    by_verdict = {}
    for rule in rules:
        verdict = rule['verdict']
        if verdict in by_verdict:
            raise PolicyError(f'duplicate verdict rule {verdict!r}')
        by_verdict[verdict] = rule
    required_verdicts = {'healthy', 'degraded', 'failing'}
    missing_verdicts = required_verdicts - set(by_verdict)
    if missing_verdicts:
        raise PolicyError(f'missing verdict rules: '
                          f'{sorted(missing_verdicts)}')
    healthy = by_verdict['healthy']['when']
    degraded = by_verdict['degraded']['when']
    failing = by_verdict['failing']['when']
    for verdict, condition, operation in (
        ('healthy', healthy, '<'),
        ('degraded', degraded, '>='),
        ('failing', failing, '>='),
    ):
        if (condition['field'] != 'failure_rate'
                or condition['op'] != operation):
            raise PolicyError(
                f"{verdict} rule must be failure_rate {operation} value"
            )
    if healthy['value'] != degraded['value']:
        raise PolicyError(
            'healthy and degraded rules must share one boundary'
        )
    if degraded['value'] >= failing['value']:
        raise PolicyError(
            'degraded failure boundary must be below failing boundary'
        )
    if degraded['value'] < 0 or failing['value'] > 1:
        raise PolicyError('failure-rate boundaries must be between 0 and 1')
    doc['verdicts'] = rules
    doc['version'] = str(doc['version'])
    doc['path'] = path
    return doc
