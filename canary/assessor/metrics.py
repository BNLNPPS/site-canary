"""Per-queue metric computation from accounting job rows.

Pure functions: rows in, metrics out. A job row carries at least
computingsite, creationtime, starttime, and jobstatus; timestamps are
datetimes or ISO strings. Queues below the statistics threshold keep
their entry with null percentiles and low_stats set — quiet queues are
probe targets and must stay visible.
"""
import logging
from datetime import datetime

logger = logging.getLogger('canary.assessor.metrics')

REQUIRED_FIELDS = ('computingsite', 'creationtime', 'starttime', 'jobstatus')


def _dt(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _percentile(sorted_values, fraction):
    """Nearest-rank percentile on a sorted list."""
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1,
              max(0, round(fraction * (len(sorted_values) - 1))))
    return sorted_values[idx]


def compute_queue_metrics(rows, window_start, window_end, min_jobs=50):
    """Compute per-queue metrics over the window. Returns a list of
    dicts sorted by descending job count. Malformed rows are counted
    and reported in the result, never silently dropped."""
    window_hours = (window_end - window_start).total_seconds() / 3600.0
    queues = {}
    malformed = 0
    for row in rows:
        if any(f not in row for f in ('computingsite', 'jobstatus')):
            malformed += 1
            continue
        q = queues.setdefault(row['computingsite'], {
            'finished': 0, 'failed': 0, 'other': 0, 'waits': []})
        status = row['jobstatus']
        if status in ('finished', 'failed'):
            q[status] += 1
        else:
            q['other'] += 1
        try:
            created, started = _dt(row.get('creationtime')), _dt(row.get('starttime'))
        except (ValueError, TypeError) as e:
            logger.error("bad timestamp in row for %s: %s",
                         row['computingsite'], e)
            malformed += 1
            continue
        if created and started and started >= created:
            q['waits'].append((started - created).total_seconds())

    results = []
    for name, q in queues.items():
        njobs = q['finished'] + q['failed']
        waits = sorted(q['waits'])
        low_stats = njobs < min_jobs
        entry = {
            'queue': name,
            'njobs': njobs,
            'finished': q['finished'],
            'failed': q['failed'],
            'other_status': q['other'],
            'low_stats': low_stats,
            'wait_median_s': None if low_stats else _percentile(waits, 0.5),
            'wait_p90_s': None if low_stats else _percentile(waits, 0.9),
            'failure_rate': (None if njobs == 0
                             else round(q['failed'] / njobs, 4)),
            'finished_per_hour': (round(q['finished'] / window_hours, 3)
                                  if window_hours > 0 else None),
        }
        results.append(entry)
    results.sort(key=lambda e: -e['njobs'])
    if malformed:
        logger.error("%d malformed rows skipped", malformed)
    return {'queues': results, 'malformed_rows': malformed,
            'window_start': window_start.isoformat(),
            'window_end': window_end.isoformat(),
            'min_jobs': min_jobs}
