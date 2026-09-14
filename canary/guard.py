"""The node guard's decision (docs/NODE_GUARD.md, The detector): from the
terminal production jobs of a window, grouped by queue and host, which
nodes are black holes.

Pure: rows in, verdicts out, thresholds passed in. Nothing here reads a
database or a clock; the caller supplies the rows, the per-queue
calibration and the settings, and records what comes back.

A row is a dict with ``queue``, ``host`` (the job record's
modificationhost, as given), ``jobstatus`` (``finished`` or ``failed``),
``jeditaskid``, ``duration_s`` (end minus start in seconds, or None when
either is missing) and ``endtime`` (anything orderable, or None). A row
missing ``queue``, ``host`` or ``jobstatus`` is malformed and counted,
never silently dropped.
"""

DEFAULTS = {
    'min_jobs': 8,
    'failed_fraction': 0.8,
    'fast_fraction': 0.5,
    'fast_ratio': 0.5,
    'storm_nodes': 10,
    'not_nodes': (),
}

# Reason codes, one per outcome of the decision.
BLACK_HOLE = 'black_hole'
BELOW_FLOOR = 'below_floor'
FAILED_FRACTION = 'failed_fraction'
NOT_FAST = 'not_fast'
TASKS_FAIL_EVERYWHERE = 'tasks_fail_everywhere'
NO_CALIBRATION = 'no_calibration'
QUEUE_EVENT = 'queue_event'
NO_OTHER_NODE = 'no_other_node'


def normalize_host(value):
    """The batch host from a job record's modificationhost: the slot
    prefix (``slot1_8@host``) stripped, surrounding whitespace removed,
    an empty or missing value returned as ''. The name is otherwise kept
    as the record gives it; a bare name is distinct within its queue."""
    if value is None:
        return ''
    text = str(value).strip()
    if '@' in text:
        text = text.rsplit('@', 1)[1].strip()
    return text


def _settings(settings):
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        if settings and settings.get(key) is not None:
            out[key] = settings[key]
    out['min_jobs'] = max(1, int(out['min_jobs']))
    out['storm_nodes'] = max(1, int(out['storm_nodes']))
    for key in ('failed_fraction', 'fast_fraction', 'fast_ratio'):
        out[key] = float(out[key])
    out['not_nodes'] = tuple(normalize_host(h) for h in (out['not_nodes'] or ()) if h)
    return out


def decide_nodes(rows, calibration, settings=None, finished_elsewhere=None):
    """Judge every host of every queue in ``rows``.

    ``calibration`` maps queue to its median finished walltime in seconds
    (None or absent when unknown). ``finished_elsewhere`` is the
    attribution's longer look back: ``{queue: {task: {host, ...}}}``, the
    hosts on which each task finished over the attribution window; the
    window's own finishes are added to it. Returns::

        {'queues': {queue: {'median_finished_s', 'fast_under_s', 'jobs',
                            'hosts': int, 'judged': int, 'tripped': int,
                            'queue_event': bool, 'not_nodes': {host: {...}},
                            'nodes': {host: verdict}}},
         'tripped': [(queue, host), ...],
         'judged': int, 'hosts': int, 'jobs': int, 'malformed': int,
         'settings': {...}}

    ``nodes`` holds only the hosts at or above the job floor, each with
    its state (``tripped`` or ``clear``), reason code and evidence; hosts
    under the floor are counted in ``hosts`` and not listed. A host named
    in ``not_nodes`` (a submit host a job dies on before it lands) is
    never judged; its outcomes are reported under ``not_nodes``. When a
    queue trips more than ``storm_nodes`` hosts at once, the burst is the
    queue's condition, not the nodes': the queue reads ``queue_event``
    and every one of those hosts is cleared with that reason.
    """
    cfg = _settings(settings)
    calibration = calibration or {}
    per_queue = {}
    malformed = 0
    jobs = 0
    for row in rows or ():
        queue = (row or {}).get('queue')
        host = normalize_host((row or {}).get('host'))
        status = (row or {}).get('jobstatus')
        if not queue or not host or status not in ('finished', 'failed'):
            malformed += 1
            continue
        jobs += 1
        q = per_queue.setdefault(queue, {'hosts': {}, 'finished_tasks': {}, 'not_nodes': {}})
        if host in cfg['not_nodes']:
            nn = q['not_nodes'].setdefault(host, {'failed': 0, 'finished': 0})
            nn[status] += 1
            continue
        h = q['hosts'].setdefault(host, [])
        h.append({'status': status, 'task': row.get('jeditaskid'),
                  'duration_s': row.get('duration_s'), 'endtime': row.get('endtime')})
        if status == 'finished' and row.get('jeditaskid') is not None:
            q['finished_tasks'].setdefault(row['jeditaskid'], set()).add(host)

    out_queues = {}
    tripped = []
    judged_total = 0
    hosts_total = 0
    for queue, q in per_queue.items():
        median = calibration.get(queue)
        median = float(median) if median else None
        fast_under = median * cfg['fast_ratio'] if median else None
        finished_tasks = {t: set(h) for t, h in ((finished_elsewhere or {}).get(queue) or {}).items()}
        for t, hosts in q['finished_tasks'].items():
            finished_tasks.setdefault(t, set()).update(hosts)
        finishing_hosts = set().union(*finished_tasks.values()) if finished_tasks else set()
        nodes = {}
        q_tripped = []
        q_jobs = sum(v['failed'] + v['finished'] for v in q['not_nodes'].values())
        for host, entries in q['hosts'].items():
            hosts_total += 1
            q_jobs += len(entries)
            if len(entries) < cfg['min_jobs']:
                continue
            judged_total += 1
            verdict = _judge(host, entries, finished_tasks, finishing_hosts, median, fast_under, cfg)
            nodes[host] = verdict
            if verdict['state'] == 'tripped':
                q_tripped.append(host)
        queue_event = len(q_tripped) > cfg['storm_nodes']
        if queue_event:
            for host in q_tripped:
                nodes[host]['state'] = 'clear'
                nodes[host]['reason'] = QUEUE_EVENT
        else:
            tripped.extend((queue, host) for host in q_tripped)
        out_queues[queue] = {
            'median_finished_s': median, 'fast_under_s': fast_under,
            'jobs': q_jobs, 'hosts': len(q['hosts']), 'judged': len(nodes),
            'tripped': 0 if queue_event else len(q_tripped),
            'queue_event': queue_event, 'storm_hosts': len(q_tripped) if queue_event else 0,
            'not_nodes': q['not_nodes'], 'nodes': nodes,
        }
    return {'queues': out_queues, 'tripped': tripped, 'judged': judged_total,
            'hosts': hosts_total, 'jobs': jobs, 'malformed': malformed,
            'settings': cfg}


def _judge(host, entries, finished_tasks, finishing_hosts, median, fast_under, cfg):
    n = len(entries)
    failed = [e for e in entries if e['status'] == 'failed']
    finished = n - len(failed)
    failed_fraction = len(failed) / n
    fast = 0
    no_duration = 0
    for e in failed:
        d = e.get('duration_s')
        if d is None:
            no_duration += 1
        elif fast_under is not None and float(d) < fast_under:
            fast += 1
    fast_fraction = (fast / len(failed)) if failed else 0.0
    tasks_failed = sorted({e['task'] for e in failed if e.get('task') is not None})
    finished_elsewhere = sorted(
        t for t in tasks_failed
        if any(other != host for other in finished_tasks.get(t, ())))
    ends = [e['endtime'] for e in entries if e.get('endtime') is not None]
    evidence = {
        'jobs': n, 'failed': len(failed), 'finished': finished,
        'failed_fraction': round(failed_fraction, 3),
        'fast_failed': fast, 'failed_no_duration': no_duration,
        'fast_fraction': round(fast_fraction, 3),
        'median_finished_s': median, 'fast_under_s': fast_under,
        'tasks_failed': tasks_failed,
        'tasks_finished_elsewhere': finished_elsewhere,
        'other_nodes_finishing': len(finishing_hosts - {host}),
        'first_end': min(ends) if ends else None,
        'last_end': max(ends) if ends else None,
    }
    if failed_fraction < cfg['failed_fraction']:
        return {'state': 'clear', 'reason': FAILED_FRACTION, 'evidence': evidence}
    if fast_under is None:
        return {'state': 'clear', 'reason': NO_CALIBRATION, 'evidence': evidence}
    if fast_fraction < cfg['fast_fraction']:
        return {'state': 'clear', 'reason': NOT_FAST, 'evidence': evidence}
    if not finished_elsewhere:
        # No other node of the queue finished anything in the attribution
        # window: node and queue are one thing here, the queue detector's
        # case. Otherwise the tasks this node failed finish nowhere on the
        # queue, which is the task's condition.
        reason = NO_OTHER_NODE if not (finishing_hosts - {host}) else TASKS_FAIL_EVERYWHERE
        return {'state': 'clear', 'reason': reason, 'evidence': evidence}
    return {'state': 'tripped', 'reason': BLACK_HOLE, 'evidence': evidence}
