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


JOB_COLUMNS = (
    'pandaid', 'jeditaskid', 'jobstatus', 'computingsite', 'destinationsite',
    'modificationhost', 'creationtime', 'starttime', 'endtime', 'attemptnr',
    'exeerrorcode', 'exeerrordiag', 'piloterrorcode', 'piloterrordiag',
    'taskbuffererrorcode', 'taskbuffererrordiag',
)
TERMINAL_JOB_STATES = ('finished', 'failed', 'cancelled', 'closed')
FAILED_TASK_STATES = ('failed', 'aborted', 'broken', 'exhausted')
DONE_TASK_STATES = ('done', 'finished')


def _panda_probe_rows(dsn, task_ids):
    """The PanDA evidence for the open runs: every job of each task
    (active and archived tables), the task states as fallback, and the
    job metadata of finished jobs, where the pilot delivers the landing
    report (jobReport.json lifted whole into the metatable)."""
    import psycopg
    cols = ', '.join(f'"{c}"' for c in JOB_COLUMNS)
    jobs = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for table in ('jobsactive4', 'jobsarchived4'):
                cur.execute(
                    f'SELECT {cols} FROM "doma_panda"."{table}" '
                    'WHERE "jeditaskid" = ANY(%s)', (task_ids,))
                for row in cur.fetchall():
                    d = dict(zip(JOB_COLUMNS, row))
                    jobs.setdefault(d['jeditaskid'], {})[d['pandaid']] = d
            cur.execute(
                'SELECT "jeditaskid", "status" FROM "doma_panda"."jedi_tasks" '
                'WHERE "jeditaskid" = ANY(%s)', (task_ids,))
            tasks = dict(cur.fetchall())
            finished = [pid for per in jobs.values()
                        for pid, j in per.items()
                        if j['jobstatus'] == 'finished']
            metadata = {}
            if finished:
                cur.execute(
                    'SELECT "pandaid", "metadata" FROM "doma_panda"."metatable" '
                    'WHERE "pandaid" = ANY(%s)', (finished,))
                metadata = dict(cur.fetchall())
    return jobs, tasks, metadata


def _utc(dt):
    """PanDA timestamps are naive UTC; return ISO 8601 with the zone."""
    from datetime import timezone as dt_timezone
    if dt is None:
        return None
    return dt.replace(tzinfo=dt_timezone.utc).isoformat()


STARTED_JOB_STATES = ('running', 'holding', 'transferring', 'merging',
                      'finished', 'failed', 'cancelled', 'closed')


def _job_facts(job, queue):
    """What the run records about its job: where it landed, when it
    waited and ran, and every non-zero error component. A start is
    taken only from a job that has run: PanDA stamps ``starttime`` when
    a push-mode job is handed to its worker (``starting``) and again
    when the pilot reports ``running``, so the first stamp is the
    dispatch, not the site's start. The node likewise is the job's last
    modifier and names the pilot's host only once the job has run."""
    has_run = job['jobstatus'] in STARTED_JOB_STATES and bool(job['starttime'])
    started = job['starttime'] if has_run else None
    facts = {
        'pandaid': job['pandaid'],
        'job_status': job['jobstatus'],
        'attempt': job['attemptnr'],
        'landed_site': (job['destinationsite']
                        or (queue.site.name if queue.site_id else queue.name)),
        'host': (job['modificationhost'] or '') if has_run else '',
        'created_at': _utc(job['creationtime']),
        'started_at': _utc(started),
        'ended_at': _utc(job['endtime']),
        'wait_s': None,
        'run_s': None,
    }
    if started and job['creationtime']:
        facts['wait_s'] = int((started - job['creationtime']).total_seconds())
    if job['endtime'] and started:
        facts['run_s'] = int((job['endtime'] - started).total_seconds())
    errors = {}
    for component in ('exe', 'pilot', 'taskbuffer'):
        code = job.get(f'{component}errorcode') or 0
        if code:
            errors[component] = {'code': int(code),
                                 'diag': (job.get(f'{component}errordiag')
                                          or '')[:500]}
    facts['errors'] = errors
    return facts


def _landing_report(raw):
    """The landing report inside a job's metadata, else None with the
    reason. The pilot ships jobReport.json as the job metadata; the
    runner's canary mode puts the report under its ``canary`` key."""
    import json
    if not raw:
        return None, 'no job metadata'
    try:
        doc = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as e:
        return None, f'job metadata is not JSON: {e}'
    if not isinstance(doc, dict):
        return None, 'job metadata is not an object'
    report = doc.get('canary')
    if not isinstance(report, dict):
        return None, 'job metadata carries no canary report'
    return report, ''


def collect_run_outcomes():
    """Bring every open probe run up to date from PanDA and collect the
    landing report of every finished one into the store.

    Open runs are the submitted ones and the finished ones whose report
    has not been collected. For each, the run's job supplies the landed
    site and host, the creation-to-start wait, the run time, and the
    error components; a job that has started but not ended records its
    wait and stays submitted. A finished job's metadata carries the
    landing report, which is ingested (source probe, site as landed)
    and the run becomes ``collected``; a finished job without a report
    is an error and the run stays ``finished`` with the reason. A failed
    job fails the run with its errors. A task with no job record falls
    back to the task state. Reads PanDA through CANARY_PANDA_DSN, as
    the assessor's panda source does. Every failure is recorded on its
    run and logged; nothing raises past the loop. Returns the counts.
    """
    from canary.config import PANDA_DSN
    from canary.store.ingest import IngestError, ingest_report
    from canary.store.models import ProbeRun

    counts = {'open': 0, 'started': 0, 'collected': 0, 'finished': 0,
              'failed': 0, 'errors': 0}
    candidates = ProbeRun.objects.filter(
        jeditaskid__isnull=False,
        status__in=(ProbeRun.Status.SUBMITTED, ProbeRun.Status.FINISHED),
    ).select_related('queue', 'queue__site')
    open_runs = [r for r in candidates
                 if r.status == ProbeRun.Status.SUBMITTED
                 or not ((r.data or {}).get('report_id')
                         or (r.data or {}).get('report') == 'missing')]
    counts['open'] = len(open_runs)
    if not open_runs:
        return counts
    if not PANDA_DSN:
        logger.error('probe collection: CANARY_PANDA_DSN is not set; '
                     '%d open runs left as they are', len(open_runs))
        counts['errors'] = len(open_runs)
        return counts
    ids = sorted({r.jeditaskid for r in open_runs})
    try:
        jobs, tasks, metadata = _panda_probe_rows(PANDA_DSN, ids)
    except Exception as e:  # noqa: BLE001 - surfaced, never raised past here
        logger.error('probe collection: PanDA query failed for %d open '
                     'runs: %s', len(open_runs), e)
        counts['errors'] = len(open_runs)
        return counts

    for run_row in open_runs:
        try:
            _collect_one(run_row, jobs, tasks, metadata, ingest_report,
                         IngestError, ProbeRun, counts)
        except Exception as e:  # noqa: BLE001 - one bad run never stops the rest
            logger.error('probe collection: run %s (task %s) failed: %s',
                         run_row.id, run_row.jeditaskid, e, exc_info=True)
            run_row.data = dict(run_row.data or {}, collect_error=str(e)[:500])
            run_row.save(update_fields=['data', 'modified_at'])
            counts['errors'] += 1
    return counts


def _collect_one(run_row, jobs, tasks, metadata, ingest_report, IngestError,
                 ProbeRun, counts):
    data = dict(run_row.data or {})
    task_state = str(tasks.get(run_row.jeditaskid) or '')
    per_task = jobs.get(run_row.jeditaskid) or {}
    job = max(per_task.values(), key=lambda j: j['pandaid']) if per_task else None

    if job is None:
        # No job record yet, or none any more: the task state decides.
        if task_state in FAILED_TASK_STATES:
            run_row.status = ProbeRun.Status.FAILED
            counts['failed'] += 1
        elif task_state in DONE_TASK_STATES:
            run_row.status = ProbeRun.Status.FINISHED
            data['report'] = 'missing'
            logger.error('probe collection: task %s is %s but has no job '
                         'record; no report to collect',
                         run_row.jeditaskid, task_state)
            counts['finished'] += 1
        else:
            return
        data['task_status'] = task_state
        data['collect_note'] = 'no job record'
        run_row.data = data
        run_row.save(update_fields=['status', 'data', 'modified_at'])
        return

    already_started = bool(data.get('started_at'))
    data.update(_job_facts(job, run_row.queue))
    if task_state:
        data['task_status'] = task_state
    if job['jobstatus'] not in TERMINAL_JOB_STATES:
        if data.get('started_at') and not already_started:
            counts['started'] += 1
        run_row.data = data
        run_row.save(update_fields=['data', 'modified_at'])
        return

    if job['jobstatus'] == 'finished':
        report, reason = _landing_report(metadata.get(job['pandaid']))
        if report is None:
            run_row.status = ProbeRun.Status.FINISHED
            data['report'] = 'missing'
            data['collect_note'] = reason
            logger.error('probe collection: task %s job %s finished but %s',
                         run_row.jeditaskid, job['pandaid'], reason)
            counts['finished'] += 1
        else:
            try:
                summary = ingest_report(report, site_name=data['landed_site'],
                                        queue_name=run_row.queue.name,
                                        source='probe')
            except IngestError as e:
                run_row.status = ProbeRun.Status.FINISHED
                data['report'] = 'rejected'
                data['collect_note'] = str(e)[:500]
                logger.error('probe collection: task %s job %s report '
                             'rejected: %s', run_row.jeditaskid,
                             job['pandaid'], e)
                counts['errors'] += 1
            else:
                run_row.status = ProbeRun.Status.COLLECTED
                data['report'] = 'collected'
                data['report_id'] = summary['report_id']
                data['fingerprint'] = summary['environment']
                data['kit_exit_code'] = report.get('kit_exit_code')
                fp = report.get('fingerprint') or {}
                data['cvmfs'] = {repo: bool((v or {}).get('reachable'))
                                 for repo, v in (fp.get('cvmfs') or {}).items()}
                data['gpu'] = bool((fp.get('gpu') or {}).get('present'))
                counts['collected'] += 1
    else:
        run_row.status = ProbeRun.Status.FAILED
        counts['failed'] += 1
    run_row.data = data
    run_row.save(update_fields=['status', 'data', 'modified_at'])
