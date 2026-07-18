"""Assessor runs: source -> metrics -> optional store write."""
import logging

logger = logging.getLogger('canary.assessor.run')


def write_samples(assessment):
    """Write one PassiveSample per assessed queue. Requires the store
    (Django configured by the caller or a hosting project)."""
    from django.utils.dateparse import parse_datetime

    from ..store.models import PassiveSample, Queue

    window_start = parse_datetime(assessment['window_start'])
    window_end = parse_datetime(assessment['window_end'])
    written = []
    for entry in assessment['queues']:
        queue, _ = Queue.objects.get_or_create(name=entry['queue'])
        sample = PassiveSample.objects.create(
            queue=queue,
            window_start=window_start,
            window_end=window_end,
            njobs=entry['njobs'],
            wait_median_s=entry['wait_median_s'],
            wait_p90_s=entry['wait_p90_s'],
            failure_rate=entry['failure_rate'],
            metrics={k: entry[k] for k in
                     ('finished', 'failed', 'other_status', 'low_stats',
                      'finished_per_hour')},
        )
        written.append(str(sample.id))
    logger.info("wrote %d passive samples", len(written))
    return written


def format_table(assessment):
    """Readable per-queue table in the PANDA_USER_JOBS.md manner."""
    def dur(seconds):
        if seconds is None:
            return '-'
        if seconds < 600:
            return f'{seconds / 60:.1f} min'
        if seconds < 7200:
            return f'{seconds / 60:.0f} min'
        return f'{seconds / 3600:.1f} h'

    lines = [f"{'Queue':<32} {'Jobs':>7} {'Median wait':>12} "
             f"{'90th pct':>10} {'Fail':>6}"]
    for e in assessment['queues']:
        fail = ('-' if e['failure_rate'] is None
                else f"{e['failure_rate'] * 100:.0f}%")
        flag = ' (low stats)' if e['low_stats'] else ''
        lines.append(
            f"{e['queue']:<32} {e['njobs']:>7} "
            f"{dur(e['wait_median_s']):>12} {dur(e['wait_p90_s']):>10} "
            f"{fail:>6}{flag}")
    lines.append(f"window {assessment['window_start']} .. "
                 f"{assessment['window_end']}"
                 + (f" · {assessment['malformed_rows']} malformed rows"
                    if assessment['malformed_rows'] else ''))
    return '\n'.join(lines)
