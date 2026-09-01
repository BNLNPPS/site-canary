"""The Canary page: the landscape map, passive assessment, and health
states, in the System pulldown of the swf-monitor navigation.

Public read-only, matching the System Status page; the platform owns
access policy (docs/SWF_INTEGRATION.md).
"""
import logging

from django.shortcuts import render

from ..assessor.run import format_duration
from ..config import POLICY_PATH
from ..policy.loader import PolicyError, load_policy
from .models import PassiveSample, Queue

logger = logging.getLogger('canary.store.views')


def canary_page(request):
    policy_levels = None
    policy_error = ''
    try:
        policy = load_policy(POLICY_PATH or None)
        failure_thresholds = {
            rule['verdict']: float(rule['when']['value']) * 100
            for rule in policy['verdicts']
            if rule['when']['field'] == 'failure_rate'
        }
        policy_levels = {
            'degraded_failure_pct': (
                f"{failure_thresholds['degraded']:g}"
            ),
            'failing_failure_pct': (
                f"{failure_thresholds['failing']:g}"
            ),
            'min_jobs': policy['evidence']['min_jobs'],
        }
    except PolicyError as exc:
        policy_error = str(exc)
        logger.error('Canary policy configuration error: %s', exc)
    queues = list(Queue.objects.exclude(
        name__icontains='test').select_related('site'))
    latest = {}
    for sample in PassiveSample.objects.order_by('queue_id', '-window_end'):
        latest.setdefault(sample.queue_id, sample)
    queue_rows = []
    for queue in sorted(queues, key=lambda q: q.name):
        sample = latest.get(queue.id)
        queue_rows.append({
            'queue': queue,
            'sample': sample,
            'wait_median': format_duration(
                sample.wait_median_s if sample else None),
            'wait_p90': format_duration(
                sample.wait_p90_s if sample else None),
            'failure_pct': ('-' if not sample or sample.failure_rate is None
                            else f'{sample.failure_rate * 100:.0f}%'),
        })

    return render(request, 'canary/canary_page.html', {
        'queue_rows': queue_rows,
        'policy_levels': policy_levels,
        'policy_error': policy_error,
    })
