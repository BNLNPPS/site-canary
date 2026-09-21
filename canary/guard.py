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
never silently dropped. Optional, carried into the evidence when
present: ``site`` (the execute site the caller resolved, the actual
site behind a pool queue), ``pandaid``, and ``error`` (the failure's
error label as the caller composed it, e.g. ``pilot 1305``).
"""

DEFAULTS = {
    'min_jobs': 8,
    'failed_fraction': 0.8,
    'fast_fraction': 0.5,
    'fast_ratio': 0.5,
    'storm_nodes': 10,
    'storm_minutes': 5,
    'not_nodes': (),
    'fixed_min_jobs': 5,
    'fixed_time_ratio': 1.2,
}

# Reason codes, one per outcome of the decision.
BLACK_HOLE = 'black_hole'
FIXED_TIME = 'fixed_time'
BELOW_FLOOR = 'below_floor'
FAILED_FRACTION = 'failed_fraction'
NOT_FAST = 'not_fast'
TASKS_FAIL_EVERYWHERE = 'tasks_fail_everywhere'
NO_CALIBRATION = 'no_calibration'
QUEUE_EVENT = 'queue_event'
QUEUE_FAILING = 'queue_failing'
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


def _seconds(value):
    """An endtime as seconds on one axis, for the burst buckets: a
    datetime by its timestamp, an ISO string parsed, a number as it is;
    None when it is none of these."""
    if value is None:
        return None
    if hasattr(value, 'timestamp'):
        try:
            return float(value.timestamp())
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return float(parsed.timestamp())
    except (TypeError, ValueError):
        return None


def _bursts(hosts, cfg):
    """The queue's synchronous failures: the failures of every host
    bucketed by ``storm_minutes``; a bucket in which more than
    ``storm_nodes`` distinct hosts failed is a burst, the queue's event.
    Returns {bucket: hosts failed in it} for the bursts only."""
    width = cfg['storm_minutes'] * 60.0
    buckets = {}
    for host, entries in hosts.items():
        for e in entries:
            if e['status'] != 'failed':
                continue
            t = _seconds(e.get('endtime'))
            if t is None:
                continue
            buckets.setdefault(int(t // width), set()).add(host)
    return {b: len(h) for b, h in buckets.items() if len(h) > cfg['storm_nodes']}


def _burst_failures(entries, bursts, cfg):
    """How many of a node's failures fall in the queue's burst buckets."""
    if not bursts:
        return 0
    width = cfg['storm_minutes'] * 60.0
    n = 0
    for e in entries:
        if e['status'] != 'failed':
            continue
        t = _seconds(e.get('endtime'))
        if t is not None and int(t // width) in bursts:
            n += 1
    return n


def _in_bursts(entries, bursts, cfg):
    """Whether a node's failures are the queue's burst: at least
    ``failed_fraction`` of its timed failures fall in burst buckets."""
    if not bursts:
        return False
    width = cfg['storm_minutes'] * 60.0
    timed = inside = 0
    for e in entries:
        if e['status'] != 'failed':
            continue
        t = _seconds(e.get('endtime'))
        if t is None:
            continue
        timed += 1
        if int(t // width) in bursts:
            inside += 1
    return timed > 0 and inside / timed >= cfg['failed_fraction']


def _settings(settings):
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        if settings and settings.get(key) is not None:
            out[key] = settings[key]
    out['min_jobs'] = max(1, int(out['min_jobs']))
    out['storm_nodes'] = max(1, int(out['storm_nodes']))
    out['storm_minutes'] = max(1, int(out['storm_minutes']))
    out['fixed_min_jobs'] = max(2, int(out['fixed_min_jobs']))
    for key in ('failed_fraction', 'fast_fraction', 'fast_ratio', 'fixed_time_ratio'):
        out[key] = float(out[key])
    out['not_nodes'] = tuple(normalize_host(h) for h in (out['not_nodes'] or ()) if h)
    return out


def decide_nodes(rows, calibration, settings=None, finished_elsewhere=None):
    """Judge every host of every queue in ``rows``.

    ``calibration`` maps queue to its median finished walltime in seconds
    (None or absent when unknown). ``finished_elsewhere`` is the
    attribution's longer look back: ``{queue: {task: {host: endtime}}}``,
    the hosts on which each task finished over the attribution window
    with the latest finish on each (a set of hosts, with no times, is
    taken as finishes whose time is unknown); the window's own finishes
    are added to it. The attribution is contemporaneous: a task counts as
    finishing elsewhere only by a finish on another host at or after the
    node's first failure in the window, since the queue that finished
    the node's tasks yesterday and fails them everywhere today is the
    queue's condition (Perlmutter under the dead BNL-XRD door, 2026-09-21:
    every node tripped in turn on finishes from before the door died).
    For the same reason a node whose queue fails around it, the other
    hosts' terminal jobs at ``failed_fraction`` failed or worse over at
    least ``min_jobs`` of them, is the queue's event (``queue_failing``),
    not a black hole. Returns::

        {'queues': {queue: {'median_finished_s', 'fast_under_s', 'jobs',
                            'hosts': int, 'judged': int, 'tripped': int,
                            'queue_event': bool, 'not_nodes': {host: {...}},
                            'nodes': {host: verdict}}},
         'tripped': [(queue, host), ...],
         'judged': int, 'hosts': int, 'jobs': int, 'malformed': int,
         'settings': {...}}

    ``nodes`` holds only the hosts at or above the job floor, each with
    its state (``tripped`` or ``clear``), reason code and evidence; hosts
    under the floor are counted in ``hosts`` and not listed, except one
    that trips the fixed-time rule: a host that finished nothing and whose
    failures all die at the same time (the 90th over the 10th percentile
    of their durations under ``fixed_time_ratio``), from ``fixed_min_jobs``
    failures up, whatever their speed; the memory ceiling and the
    fixed-time kill are black holes for the work they take. A host named
    in ``not_nodes`` (a submit host a job dies on before it lands) is
    never judged; its outcomes are reported under ``not_nodes``. Two
    readings make a queue's event, ``queue_event``, rather than a node's:
    more than ``storm_nodes`` hosts tripping at once, and a burst, more
    than ``storm_nodes`` distinct hosts failing inside one
    ``storm_minutes`` interval, whatever their per-host counts (task
    39973, 2026-09-15: 443 deaths on 412 hosts in four minutes tripped
    the three hosts big enough to pass the floor). A tripped host whose
    failures fall in the bursts (``failed_fraction`` of them) is cleared
    with that reason; the queue reads ``queue_event`` with the host
    count and lists its bursts.
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
                  'duration_s': row.get('duration_s'), 'endtime': row.get('endtime'),
                  'site': row.get('site') or '', 'pandaid': row.get('pandaid'),
                  'error': row.get('error') or ''})
        if status == 'finished' and row.get('jeditaskid') is not None:
            _note_finish(q['finished_tasks'], row['jeditaskid'], host, row.get('endtime'))

    out_queues = {}
    tripped = []
    judged_total = 0
    hosts_total = 0
    for queue, q in per_queue.items():
        median = calibration.get(queue)
        median = float(median) if median else None
        fast_under = median * cfg['fast_ratio'] if median else None
        finished_tasks = {}
        for t, hosts in ((finished_elsewhere or {}).get(queue) or {}).items():
            if isinstance(hosts, dict):
                for h, end in hosts.items():
                    _note_finish(finished_tasks, t, h, end)
            else:
                for h in hosts:
                    _note_finish(finished_tasks, t, h, None)
        for t, hosts in q['finished_tasks'].items():
            for h, end in hosts.items():
                _note_finish(finished_tasks, t, h, end)
        finishing_hosts = set().union(*(set(h) for h in finished_tasks.values())) if finished_tasks else set()
        nodes = {}
        q_tripped = []
        queue_failing = False
        q_jobs = sum(v['failed'] + v['finished'] for v in q['not_nodes'].values())
        bursts = _bursts(q['hosts'], cfg)
        # The queue's jobs outside its bursts, which are explained already.
        burst_by_host = {h: _burst_failures(entries, bursts, cfg) for h, entries in q['hosts'].items()}
        q_failed_all = sum(1 for entries in q['hosts'].values() for e in entries if e['status'] == 'failed')
        q_n_all = sum(len(entries) for entries in q['hosts'].values())
        q_burst = sum(burst_by_host.values())
        for host, entries in q['hosts'].items():
            hosts_total += 1
            q_jobs += len(entries)
            if len(entries) < cfg['fixed_min_jobs']:
                continue
            verdict = _judge(host, entries, finished_tasks, finishing_hosts, median, fast_under, cfg)
            if len(entries) < cfg['min_jobs'] and verdict['state'] != 'tripped':
                continue            # under the floor: judged for the fixed-time rule only
            judged_total += 1
            nodes[host] = verdict
            if verdict['state'] == 'tripped' and _in_bursts(entries, bursts, cfg):
                # its deaths are the queue's burst, not its own
                verdict['state'] = 'clear'
                verdict['reason'] = QUEUE_EVENT
                verdict['evidence']['burst'] = True
                continue
            # The rest of the queue, its bursts set aside: a node is a black
            # hole against a queue that works; a queue failing around a
            # node that would trip is the queue's condition, whatever the
            # node's own count.
            others_n = (q_n_all - q_burst) - (len(entries) - burst_by_host[host])
            others_failed = (q_failed_all - q_burst) - (verdict['evidence']['failed'] - burst_by_host[host])
            others_fraction = (others_failed / others_n) if others_n else 0.0
            verdict['evidence']['queue_others_jobs'] = others_n
            verdict['evidence']['queue_others_failed_fraction'] = round(others_fraction, 3)
            if verdict['state'] == 'tripped':
                if others_n >= cfg['min_jobs'] and others_fraction >= cfg['failed_fraction']:
                    verdict['state'] = 'clear'
                    verdict['reason'] = QUEUE_FAILING
                    queue_failing = True
                    continue
                q_tripped.append(host)
        storm = len(q_tripped) > cfg['storm_nodes']
        if storm:
            for host in q_tripped:
                nodes[host]['state'] = 'clear'
                nodes[host]['reason'] = QUEUE_EVENT
        else:
            tripped.extend((queue, host) for host in q_tripped)
        queue_event = storm or bool(bursts) or queue_failing
        width = cfg['storm_minutes'] * 60
        out_queues[queue] = {
            'median_finished_s': median, 'fast_under_s': fast_under,
            'jobs': q_jobs, 'hosts': len(q['hosts']), 'judged': len(nodes),
            'tripped': 0 if storm else len(q_tripped),
            'queue_event': queue_event, 'queue_failing': queue_failing,
            'failed_fraction': round(q_failed_all / q_n_all, 3) if q_n_all else None,
            'storm_hosts': max([len(q_tripped) if storm else 0] + list(bursts.values())),
            'bursts': [{'from_s': b * width, 'to_s': (b + 1) * width, 'hosts': n}
                       for b, n in sorted(bursts.items())],
            'not_nodes': q['not_nodes'], 'nodes': nodes,
        }
    return {'queues': out_queues, 'tripped': tripped, 'judged': judged_total,
            'hosts': hosts_total, 'jobs': jobs, 'malformed': malformed,
            'settings': cfg}


def _note_finish(finished_tasks, task, host, end):
    """Record a finish of ``task`` on ``host`` at ``end`` (seconds, or
    None when unknown), keeping the latest per host; a known time never
    gives way to an unknown one."""
    hosts = finished_tasks.setdefault(task, {})
    t = _seconds(end)
    prev = hosts.get(host, None)
    if host not in hosts or (t is not None and (prev is None or t > prev)):
        hosts[host] = t


def _finished_elsewhere_since(task_hosts, host, since):
    """The other hosts on which a task finished at or after ``since``
    (seconds); a finish of unknown time counts, and every finish counts
    when the node's own failures carry no time."""
    out = set()
    for other, end in (task_hosts or {}).items():
        if other == host:
            continue
        if end is None or since is None or end >= since:
            out.add(other)
    return out


def _pct(values, q):
    """The q-th percentile of a sorted list by nearest rank; None when
    the list is empty."""
    if not values:
        return None
    k = max(0, min(len(values) - 1, int(q * (len(values) - 1) + 0.5)))
    return round(values[k], 1)


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
    # The attribution is contemporaneous: a finish elsewhere at or after
    # this node's first failure in the window.
    fail_times = [t for t in (_seconds(e.get('endtime')) for e in failed) if t is not None]
    first_failure = min(fail_times) if fail_times else None
    elsewhere = {t: _finished_elsewhere_since(finished_tasks.get(t), host, first_failure)
                 for t in tasks_failed}
    finished_elsewhere = sorted(t for t, hosts in elsewhere.items() if hosts)
    ends = [e['endtime'] for e in entries if e.get('endtime') is not None]
    sites = {}
    for e in entries:
        if e.get('site'):
            sites[e['site']] = sites.get(e['site'], 0) + 1
    site = max(sites, key=sites.get) if sites else ''
    durations = sorted(float(e['duration_s']) for e in failed if e.get('duration_s') is not None)
    errors = {}
    for e in failed:
        if e.get('error'):
            errors[e['error']] = errors.get(e['error'], 0) + 1
    error_codes = sorted(errors.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    recent = sorted((e for e in failed if e.get('pandaid') is not None),
                    key=lambda e: (e.get('endtime') is None, e.get('endtime')), reverse=True)
    sample_jobs = [e['pandaid'] for e in recent[:5]]
    elsewhere_hosts = {t: len(elsewhere[t]) for t in finished_elsewhere}
    p10, p90 = _pct(durations, 0.1), _pct(durations, 0.9)
    spread = round(p90 / p10, 3) if (p10 and p90) else None
    # The fixed-time kill: nothing finished, every failure at one time.
    fixed_time = (finished == 0 and len(failed) >= cfg['fixed_min_jobs']
                  and len(durations) >= cfg['fixed_min_jobs']
                  and spread is not None and spread <= cfg['fixed_time_ratio'])
    evidence = {
        'site': site, 'sites': sorted(sites),
        'failed_duration_s': {'p10': p10, 'median': _pct(durations, 0.5), 'p90': p90},
        'duration_spread': spread, 'fixed_time': fixed_time,
        'error_codes': error_codes, 'sample_jobs': sample_jobs,
        'tasks_finished_elsewhere_hosts': elsewhere_hosts,
        'jobs': n, 'failed': len(failed), 'finished': finished,
        'failed_fraction': round(failed_fraction, 3),
        'fast_failed': fast, 'failed_no_duration': no_duration,
        'fast_fraction': round(fast_fraction, 3),
        'median_finished_s': median, 'fast_under_s': fast_under,
        'tasks_failed': tasks_failed,
        'tasks_finished_elsewhere': finished_elsewhere,
        'first_failure_s': first_failure,
        'other_nodes_finishing': len(finishing_hosts - {host}),
        'first_end': min(ends) if ends else None,
        'last_end': max(ends) if ends else None,
    }
    # The standard reading first: mostly failed, failing fast, the tasks
    # finishing elsewhere. Then the fixed-time kill, whatever the speed.
    standard = (n >= cfg['min_jobs'] and failed_fraction >= cfg['failed_fraction']
                and fast_under is not None and fast_fraction >= cfg['fast_fraction'])
    if standard and finished_elsewhere:
        return {'state': 'tripped', 'reason': BLACK_HOLE, 'evidence': evidence}
    if fixed_time and finished_elsewhere:
        return {'state': 'tripped', 'reason': FIXED_TIME, 'evidence': evidence}
    if n < cfg['min_jobs']:
        return {'state': 'clear', 'reason': BELOW_FLOOR, 'evidence': evidence}
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
    return {'state': 'tripped', 'reason': BLACK_HOLE, 'evidence': evidence}   # unreachable: kept for the reader
