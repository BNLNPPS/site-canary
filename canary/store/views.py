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
    """Retired standalone page: the site health table leads the canary
    probes page now. Relative redirect so both faces (direct and the
    swf-remote proxy) resolve it under their own prefix."""
    from django.http import HttpResponseRedirect
    return HttpResponseRedirect('probes/')


def _health_context():
    """The site health table: per-queue status and latest passive
    sample, with the policy thresholds for the caption."""
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
    return {
        'queue_rows': queue_rows,
        'policy_levels': policy_levels,
        'policy_error': policy_error,
    }


def _probe_writes_operable(request):
    """Probe controls are pandaserver02 actions: login on the direct
    face. Outside the hosted deployment (no monitor middleware) the
    controls render disabled."""
    try:
        from monitor_app.middleware import is_tunnel_request
    except Exception:
        return False
    return request.user.is_authenticated and not is_tunnel_request(request)


def probes_page(request):
    """Canary probes: one row per probe-configured queue — last run,
    next auto run, interval, Run now — with the run history one click
    through."""
    from canary import probe as probe_mod

    rows = []
    for queue in probe_mod.configured_queues():
        config = probe_mod.probe_config(queue)
        last = probe_mod.last_run(queue)
        rows.append({
            'queue': queue,
            'interval_hours': config['interval_hours'],
            'last': last,
            'last_wait': (format_duration((last.data or {}).get('wait_s'))
                          if last and (last.data or {}).get('wait_s') is not None
                          else ''),
            'next_due': probe_mod.next_due(queue, config, last),
            'runs_count': queue.probe_runs.count(),
        })
    enabled_ids = {row['queue'].id for row in rows}
    candidates = [q for q in Queue.objects.order_by('name')
                  if q.id not in enabled_ids
                  and 'test' not in q.name.lower()]
    context = _health_context()
    context.update({
        'rows': rows,
        'candidates': candidates,
        'operable': _probe_writes_operable(request),
        # A just-queued Run now: the page waits on the agent's completion
        # event for this queue and reloads to show the run's outcome.
        'queued': (request.GET.get('queued') or '').strip(),
    })
    return render(request, 'canary/probes.html', context)


def probe_runs_page(request, queue_name):
    """The run history of one queue's probe: every dispatched probe
    task, newest first."""
    from django.shortcuts import get_object_or_404

    from canary import probe as probe_mod

    queue = get_object_or_404(Queue, name=queue_name)
    config = probe_mod.probe_config(queue)
    runs = [_run_row(run)
            for run in queue.probe_runs.order_by('-submitted_at')[:200]]
    return render(request, 'canary/probe_runs.html', {
        'queue': queue,
        'config': config,
        'runs': runs,
        'operable': _probe_writes_operable(request),
    })


def _run_row(run):
    """One probe run for the history table: the run, its timings
    formatted, its landing, its report summary, and its notes."""
    data = run.data or {}
    errors = [f"{component} {err.get('code')}: {err.get('diag') or ''}"
              for component, err in (data.get('errors') or {}).items()]
    notes = []
    if data.get('task_status'):
        notes.append(f"task {data['task_status']}")
    for key in ('collect_note', 'collect_error', 'error'):
        if data.get(key):
            notes.append(str(data[key]))
    report = ''
    if data.get('report') == 'collected':
        cvmfs = data.get('cvmfs') or {}
        parts = [f"kit {data.get('kit_exit_code')}"]
        for repo, reachable in sorted(cvmfs.items()):
            parts.append(f"{repo.split('.')[0]} cvmfs "
                         f"{'yes' if reachable else 'NO'}")
        parts.append(f"gpu {'yes' if data.get('gpu') else 'no'}")
        report = ' · '.join(parts)
    elif data.get('report'):
        report = data['report']
    return {
        'run': run,
        'wait': (format_duration(data['wait_s'])
                 if data.get('wait_s') is not None else ''),
        'ran': (format_duration(data['run_s'])
                if data.get('run_s') is not None else ''),
        'landed': data.get('landed_site') or '',
        'host': data.get('host') or '',
        'fingerprint': (data.get('fingerprint') or '')[:8],
        'report': report,
        'errors': errors,
        'notes': ' · '.join(notes),
        'stderr': data.get('stderr') or '',
    }


def probe_config_update(request):
    """Set a queue's probe interval, enable it, or disable it. A write:
    login on the direct face."""
    from django.contrib import messages
    from django.shortcuts import get_object_or_404, redirect
    from django.urls import reverse

    url = reverse('canary:probes_page')
    if request.method != 'POST':
        return redirect(url)
    if not _probe_writes_operable(request):
        messages.error(request, 'Probe configuration is a '
                                'pandaserver02 action (login required).')
        return redirect(url)
    queue = get_object_or_404(Queue, name=request.POST.get('queue', ''))
    action = request.POST.get('action', 'save')
    data = dict(queue.data or {})
    block = dict(data.get('probe') or {})
    if action == 'disable':
        block['enabled'] = False
    else:
        block['enabled'] = True
        try:
            interval = float(request.POST.get('interval_hours', '') or 24)
        except ValueError:
            messages.error(request, 'Interval must be a number of hours.')
            return redirect(url)
        if interval <= 0:
            messages.error(request, 'Interval must be positive.')
            return redirect(url)
        block['interval_hours'] = interval
    data['probe'] = block
    queue.data = data
    queue.save(update_fields=['data'])
    messages.success(request, f'Probe configuration saved for {queue.name}.')
    return redirect(url)


def probe_run_now(request):
    """Queue one immediate probe for a queue through the canary agent.
    A write: login on the direct face."""
    import json as _json

    from django.contrib import messages
    from django.shortcuts import redirect
    from django.urls import reverse

    url = reverse('canary:probes_page')
    if request.method != 'POST':
        return redirect(url)
    if not _probe_writes_operable(request):
        messages.error(request, 'Run now is a pandaserver02 action '
                                '(login required).')
        return redirect(url)
    queue_name = (request.POST.get('queue') or '').strip()
    if not queue_name:
        messages.error(request, 'No queue named.')
        return redirect(url)
    try:
        from monitor_app.activemq_connection import ActiveMQConnectionManager
        sent = ActiveMQConnectionManager().send_message(
            '/queue/canary.ops', _json.dumps({
                'msg_type': 'probe_dispatch',
                'namespace': 'canary',
                'queue': queue_name,
                'created_by': getattr(request.user, 'username', '') or 'web',
            }))
    except Exception as e:
        logger.error('probe run-now enqueue failed for %s: %s',
                     queue_name, e)
        sent = False
    if sent:
        from urllib.parse import urlencode
        messages.success(request,
                         f'Probe queued for {queue_name} — the outcome '
                         f'shows here when the agent reports.')
        # The page opens the completion stream for this queue and reloads
        # on the agent's canary_probe_dispatch_complete event.
        return redirect(f'{url}?{urlencode({"queued": queue_name})}')
    messages.error(request, 'Probe trigger could not be queued; '
                            'the message bus refused it.')
    return redirect(url)
