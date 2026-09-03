"""Probe scheduling policy: which queues carry probes, at what
interval, and which are due. The configuration lives on the store's
Queue rows (``data['probe']``); the agent's dispatch handler and the
probes page both read through here.
"""
import logging
import os
from datetime import timedelta

logger = logging.getLogger('canary.probe')

DEFAULT_INTERVAL_HOURS = 24.0


def probe_config(queue):
    """The queue's probe configuration, defaults applied: a dict with
    ``enabled`` and ``interval_hours``. A queue with no probe block is
    not probed."""
    block = (queue.data or {}).get('probe') or {}
    return {
        'enabled': bool(block.get('enabled', False)),
        'interval_hours': float(block.get('interval_hours',
                                          DEFAULT_INTERVAL_HOURS)),
    }


def configured_queues():
    """Queues carrying an enabled probe configuration."""
    from canary.store.models import Queue
    return [q for q in Queue.objects.all().order_by('name')
            if probe_config(q)['enabled']]


def last_run(queue):
    """The queue's most recent probe run, else None."""
    return queue.probe_runs.order_by('-submitted_at').first()


def last_submitted_run(queue):
    """The most recent run that actually reached PanDA — the schedule
    anchor. A failed submission never consumes the interval; it
    retries on the next tick."""
    return (queue.probe_runs.filter(jeditaskid__isnull=False)
            .order_by('-submitted_at').first())


def next_due(queue, config=None, last=None):
    """When the queue's next automatic probe is due: the last
    successful submission plus the interval, or now when nothing has
    ever reached PanDA."""
    config = config or probe_config(queue)
    last = last or last_submitted_run(queue)
    if last is None or last.jeditaskid is None:
        return None
    return last.submitted_at + timedelta(hours=config['interval_hours'])


def due_queues(now):
    """The enabled queues whose next probe is due at ``now`` and which
    have no probe run still awaiting an outcome."""
    from canary.store.models import ProbeRun
    due = []
    for queue in configured_queues():
        config = probe_config(queue)
        latest = last_run(queue)
        if latest is not None and latest.status == ProbeRun.Status.SUBMITTED:
            continue
        due_at = next_due(queue, config, last_submitted_run(queue))
        if due_at is None or due_at <= now:
            due.append(queue)
    return due


def dispatch(now, queue_names=None, force=False, submit_cmd=None):
    """Submit probes for the due queues (or the named queues when
    ``force``) through the submission doer, recording one ProbeRun per
    attempt. Returns a summary list; every failure is recorded on its
    run and reported, never raised past the loop."""
    import os
    import re
    import subprocess
    from pathlib import Path

    from canary.store.models import ProbeRun, Queue

    if submit_cmd is None:
        # The submission doer ships inside the package (canary/probe_kit),
        # so the deployed install runs the release's copy and a checkout
        # runs its own. CANARY_PROBE_SUBMIT_CMD overrides.
        submit_cmd = os.environ.get(
            'CANARY_PROBE_SUBMIT_CMD',
            str(Path(__file__).resolve().parent / 'probe_kit' / 'run.sh'))
    if force and queue_names:
        targets = list(Queue.objects.filter(name__in=queue_names))
        missing = set(queue_names) - {q.name for q in targets}
        results = [{'queue': name, 'outcome': 'unknown queue'}
                   for name in sorted(missing)]
    else:
        targets = due_queues(now)
        results = []
    # The probe runs the current campaign's production container, as PCS
    # records it, so a probe measures the site and not the nightly image;
    # unresolved, the submit script's own fallback applies and the run says so.
    container, container_note = resolve_probe_container()
    env = dict(os.environ)
    if container:
        env['CANARY_CONTAINER_IMAGE'] = container
    for queue in targets:
        run_row = ProbeRun.objects.create(
            queue=queue, submitted_at=now,
            trigger=(ProbeRun.Trigger.MANUAL if force
                     else ProbeRun.Trigger.AUTO),
            data={'container': container or '',
                  'container_note': container_note})
        try:
            p = subprocess.run(['bash', submit_cmd, queue.name],
                               capture_output=True, text=True,
                               timeout=600, env=env)
        except subprocess.TimeoutExpired:
            run_row.status = ProbeRun.Status.FAILED_SUBMIT
            run_row.data = {'error': 'submission timed out after 600s'}
            run_row.save(update_fields=['status', 'data', 'modified_at'])
            results.append({'queue': queue.name, 'outcome': 'timeout'})
            continue
        match = re.search(r'jediTaskID[=:\s]+(\d+)', p.stdout or '')
        if p.returncode == 0 and match:
            run_row.jeditaskid = int(match.group(1))
            run_row.save(update_fields=['jeditaskid', 'modified_at'])
            results.append({'queue': queue.name, 'outcome': 'submitted',
                            'jeditaskid': run_row.jeditaskid})
        else:
            run_row.status = ProbeRun.Status.FAILED_SUBMIT
            run_row.data = dict(run_row.data or {},
                                rc=p.returncode,
                                stdout=(p.stdout or '')[-2000:],
                                stderr=(p.stderr or '')[-2000:])
            run_row.save(update_fields=['status', 'data', 'modified_at'])
            results.append({'queue': queue.name, 'outcome': 'failed',
                            'rc': p.returncode})
    return results


def resolve_probe_container():
    """The current campaign's production container as PCS records it:
    the campaign from the campaign status API, the container from that
    campaign's Standard Production configuration. Returns (image, note);
    image is '' when the lookup fails, with the reason in the note, and
    the submit script's fallback then applies. Never raises."""
    import json
    import ssl
    import urllib.request

    base = (os.environ.get('SWF_MONITOR_URL') or '').rstrip('/')
    if not base:
        return '', 'SWF_MONITOR_URL not set; submit script fallback'
    ctx = ssl.create_default_context()
    if base.startswith('https://localhost'):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    def _get(path):
        with urllib.request.urlopen(base + path, context=ctx, timeout=20) as r:
            return json.load(r)

    try:
        campaign = str(_get('/pcs/api/campaigns/status/').get('campaign') or '')
        if not campaign:
            return '', 'no current campaign in the status API; fallback'
        rows = _get('/pcs/api/prod-configs/?search='
                    + urllib.request.quote(f'{campaign} Standard Production'))
        rows = rows if isinstance(rows, list) else rows.get('results') or []
        for row in rows:
            name = str(row.get('name') or '')
            if name.startswith(campaign) and 'Standard Production' in name \
                    and row.get('container_image'):
                return row['container_image'], f'{name} (PCS)'
        return '', f'no Standard Production config with a container for {campaign}; fallback'
    except Exception as e:                                      # noqa: BLE001
        logger.warning('probe container lookup failed: %s', e)
        return '', f'PCS lookup failed ({type(e).__name__}); fallback'


def refresh_run_statuses():
    """Advance submitted runs from the PanDA task record: finished and
    failed terminal states land on the run; anything else stays
    submitted. Reads PanDA through CANARY_PANDA_DSN, as the assessor's
    panda source does, so the refresh works under the package's own
    settings (the CLI on the agent) as well as the host deployment's."""
    from canary.config import PANDA_DSN
    from canary.store.models import ProbeRun

    open_runs = list(ProbeRun.objects.filter(
        status=ProbeRun.Status.SUBMITTED, jeditaskid__isnull=False))
    if not open_runs:
        return 0
    if not PANDA_DSN:
        logger.error('probe status refresh: CANARY_PANDA_DSN is not set; '
                     '%d open runs left as submitted', len(open_runs))
        return 0
    ids = [r.jeditaskid for r in open_runs]
    try:
        import psycopg
        with psycopg.connect(PANDA_DSN) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    'SELECT "jeditaskid", "status" FROM '
                    '"doma_panda"."jedi_tasks" '
                    'WHERE "jeditaskid" = ANY(%s)', (ids,))
                states = dict(cursor.fetchall())
    except Exception as e:
        logger.error('probe status refresh: PanDA task-state query '
                     'failed for %d open runs: %s', len(open_runs), e)
        return 0
    advanced = 0
    for run_row in open_runs:
        state = str(states.get(run_row.jeditaskid) or '')
        if state in ('done', 'finished'):
            run_row.status = ProbeRun.Status.FINISHED
        elif state in ('failed', 'aborted', 'broken', 'exhausted'):
            run_row.status = ProbeRun.Status.FAILED
        else:
            continue
        run_row.data = dict(run_row.data or {}, task_status=state)
        run_row.save(update_fields=['status', 'data', 'modified_at'])
        advanced += 1
    return advanced
