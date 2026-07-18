"""The Canary page: the landscape map, passive assessment, and health
states, in the System pulldown of the swf-monitor navigation.

Public read-only, matching the System Status page; the platform owns
access policy (docs/SWF_INTEGRATION.md).
"""
from django.shortcuts import render

from ..assessor.run import format_duration
from .models import PassiveSample, Queue, Site


def canary_page(request):
    sites = list(Site.objects.prefetch_related('node_environments'))
    environments = [env for site in sites
                    for env in site.node_environments.all()]

    queues = list(Queue.objects.select_related('site'))
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
            'low_stats': bool(sample and sample.metrics.get('low_stats')),
        })

    return render(request, 'canary/canary_page.html', {
        'sites': sites,
        'environments': environments,
        'queue_rows': queue_rows,
    })
