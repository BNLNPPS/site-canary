"""The measurement fold: one job's per-stage measures into the running
distributions of its node environment and workload (docs/MEASUREMENTS.md).

The store knows nothing of how a measure was obtained. The caller names
the queue and processor the job ran on, the workload keys, and per stage
the measures it computed; this folds them in with Welford's running mean
and variance, so a row stays one row however many jobs it summarizes and
the spread is kept.
"""

from django.db import transaction

from .models import NodeMeasurement, Queue

# The measures a row may carry; anything else a caller passes is ignored.
MEASURES = ('cpu_s_per_event', 'wall_s_per_event', 'cpu_efficiency',
            'rss_max_kb')
WORKLOAD_KEYS = ('container_image', 'detector_version', 'physics_config',
                 'payload_version')


def fold(dist, value):
    """One value into a running distribution: count, mean, variance,
    minimum, maximum (Welford)."""
    n = int(dist.get('n') or 0) + 1
    mean = float(dist.get('mean') or 0.0)
    m2 = float(dist.get('m2') or 0.0)
    delta = value - mean
    mean += delta / n
    m2 += delta * (value - mean)
    dist['n'] = n
    dist['mean'] = mean
    dist['m2'] = m2
    dist['variance'] = m2 / (n - 1) if n > 1 else 0.0
    dist['min'] = value if n == 1 else min(float(dist['min']), value)
    dist['max'] = value if n == 1 else max(float(dist['max']), value)
    return dist


def record_job(*, queue_name, processor, workload, stages, job_at,
               node_environment=None):
    """Fold one job's measures. ``stages`` is ``{stage: {measure: value}}``;
    ``workload`` carries the WORKLOAD_KEYS. Returns the rows touched."""
    if not queue_name:
        raise ValueError('a measurement needs the queue the job ran on')
    queue, _ = Queue.objects.get_or_create(name=queue_name)
    keys = {k: str(workload.get(k) or '')[:300] for k in WORKLOAD_KEYS}
    touched = 0
    with transaction.atomic():
        for stage, measures in (stages or {}).items():
            values = {}
            for name, value in (measures or {}).items():
                if name in MEASURES and value is not None:
                    try:
                        values[name] = float(value)
                    except (TypeError, ValueError):
                        continue
            if not values:
                continue
            row, _ = NodeMeasurement.objects.select_for_update().get_or_create(
                queue=queue, processor=(processor or 'unknown')[:200],
                stage=str(stage)[:64], **keys,
                defaults={'node_environment': node_environment})
            metrics = dict(row.metrics or {})
            for name, value in values.items():
                metrics[name] = fold(dict(metrics.get(name) or {}), value)
            row.metrics = metrics
            row.jobs = int(row.jobs or 0) + 1
            if row.first_job_at is None or job_at < row.first_job_at:
                row.first_job_at = job_at
            if row.last_job_at is None or job_at > row.last_job_at:
                row.last_job_at = job_at
            if node_environment is not None and row.node_environment_id is None:
                row.node_environment = node_environment
            row.save()
            touched += 1
    return touched


def queue_summary(queue_name, measure='cpu_s_per_event'):
    """Per processor and stage, the job-weighted mean of one measure over
    every workload the queue ran: ``{processor: {stage: {'mean', 'jobs',
    'workloads'}}}``. A database read for pages."""
    out = {}
    rows = (NodeMeasurement.objects.filter(queue__name=queue_name)
            .values('processor', 'stage', 'metrics', 'jobs'))
    for row in rows:
        dist = (row['metrics'] or {}).get(measure) or {}
        n = int(dist.get('n') or 0)
        if not n:
            continue
        cell = out.setdefault(row['processor'], {}).setdefault(
            row['stage'], {'sum': 0.0, 'jobs': 0, 'workloads': 0})
        cell['sum'] += float(dist.get('mean') or 0.0) * n
        cell['jobs'] += n
        cell['workloads'] += 1
    for stages in out.values():
        for cell in stages.values():
            cell['mean'] = cell['sum'] / cell['jobs'] if cell['jobs'] else None
            del cell['sum']
    return out
