"""Policy evaluation: pure verdict decision, and its application to
the store.

decide() is a pure function — evidence and policy in, verdict and its
exact reason out — so any verdict can be recomputed and rechecked.
apply() records verdicts and performs status transitions under the
standing rules: a manually pinned status is never overridden, and an
excluded queue is not recovered by passive evidence unless the policy
says so.
"""
import logging

from .loader import OPS

logger = logging.getLogger('canary.policy.engine')

# Verdicts that carry no status implication.
NON_STATUS_VERDICTS = {'insufficient'}


def decide(policy, evidence):
    """Decide one queue's verdict from its passive evidence.

    evidence: dict with sample_age_hours (None if no sample), njobs,
    failure_rate, wait_median_s, wait_p90_s, low_stats.
    Returns (verdict, reason) with reason stating the rule exactly.
    """
    max_age = policy['evidence']['sample_max_age_hours']
    if evidence.get('sample_age_hours') is None:
        return 'unknown', 'no passive sample'
    if evidence['sample_age_hours'] > max_age:
        return 'unknown', (f'sample age {evidence["sample_age_hours"]:.1f}h '
                           f'exceeds {max_age}h')
    if evidence.get('low_stats') or (
            evidence.get('njobs', 0) < policy['evidence']['min_jobs']):
        return 'insufficient', (f'{evidence.get("njobs", 0)} jobs below '
                                f'min_jobs {policy["evidence"]["min_jobs"]}')
    for rule in policy['verdicts']:
        cond = rule['when']
        value = evidence.get(cond['field'])
        if value is None:
            continue
        if OPS[cond['op']](value, cond['value']):
            return rule['verdict'], f'{cond["text"]} (value {value})'
    return 'unknown', 'no rule matched'


def apply(policy, write=False):
    """Evaluate every queue; optionally record verdicts and apply
    transitions. Returns the per-queue results."""
    from django.utils import timezone

    from ..store.models import PassiveSample, Queue, StatusChange, Verdict

    now = timezone.now()
    latest = {}
    for sample in PassiveSample.objects.order_by('queue_id', '-window_end'):
        latest.setdefault(sample.queue_id, sample)

    results = []
    for queue in Queue.objects.order_by('name'):
        sample = latest.get(queue.id)
        evidence = {
            'sample_id': str(sample.id) if sample else None,
            'sample_age_hours': (
                (now - sample.window_end).total_seconds() / 3600.0
                if sample else None),
            'njobs': sample.njobs if sample else 0,
            'failure_rate': sample.failure_rate if sample else None,
            'wait_median_s': sample.wait_median_s if sample else None,
            'wait_p90_s': sample.wait_p90_s if sample else None,
            'low_stats': bool(sample and sample.metrics.get('low_stats')),
        }
        verdict, reason = decide(policy, evidence)

        pinned = bool(queue.data.get('manual_pin'))
        new_status = queue.status
        blocked = ''
        if verdict in NON_STATUS_VERDICTS:
            blocked = 'verdict carries no status implication'
        elif pinned:
            blocked = 'status manually pinned'
        elif (queue.status == 'excluded'
              and not policy['recovery']['passive']):
            blocked = 'passive recovery disabled'
        else:
            new_status = verdict

        result = {
            'queue': queue.name, 'verdict': verdict, 'reason': reason,
            'status_before': queue.status, 'status_after': new_status,
            'transition': new_status != queue.status,
            'blocked': blocked if new_status == queue.status else '',
        }
        results.append(result)

        if write:
            row = Verdict.objects.create(
                queue=queue, verdict=verdict,
                policy_name=policy['policy'],
                policy_version=policy['version'],
                evidence={**evidence, 'rule': reason,
                          'pinned': pinned, 'blocked': blocked})
            if new_status != queue.status:
                StatusChange.objects.create(
                    queue=queue, old_status=queue.status,
                    new_status=new_status, actor='policy', verdict=row,
                    reason=f'policy {policy["policy"]}/'
                           f'{policy["version"]}: {reason}')
                queue.status = new_status
                queue.save()
                logger.info("%s: %s -> %s (%s)", queue.name,
                            result['status_before'], new_status, reason)
    return results
