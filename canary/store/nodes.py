"""The node guard's record (docs/NODE_GUARD.md, The record): the latch,
its expiry and half open, applied to NodeState from the guard's
verdicts; a person's clear and pin; the current exclusion.

``transition`` is pure: the node's current state and the cycle's
reading in, the next state and the change to record out. ``apply``
wraps it over the store in one transaction per node.
"""
from datetime import timedelta

CLEAR = 'clear'
BLACK_HOLE = 'black_hole'
HALF_OPEN = 'half_open'
PINNED = 'pinned'

# Reasons a transition carries, beside the verdict's own reason codes.
EXPIRED = 'expired'
REOPENED = 'reopened'
CLEAN_LANDING = 'clean_landing'
FAST_LANDING = 'fast_landing'


def transition(current, reading, now, expiry_h):
    """Decide one node's next state.

    ``current``: ``{'status', 'opened_at', 'expires_at', 'reopened'}`` or
    None for a node with no record. ``reading``: what the cycle saw:
    ``{'tripped': bool, 'reason': str, 'finished': int, 'fast_failed':
    int}``, the counts being the node's window outcomes (a half-open
    node is judged on them whether or not it reached the floor).
    Returns ``(next, change)``: ``next`` the fields to store
    (``status``, ``opened_at``, ``expires_at``, ``reopened``, ``trip``
    True when a black hole opens or reopens) and ``change`` None or
    ``(old_status, new_status, reason)``.
    """
    cur = dict(current or {})
    status = cur.get('status') or CLEAR
    opened_at = cur.get('opened_at')
    expires_at = cur.get('expires_at')
    reopened = int(cur.get('reopened') or 0)
    tripped = bool(reading.get('tripped'))
    reason = str(reading.get('reason') or '')
    finished = int(reading.get('finished') or 0)
    fast_failed = int(reading.get('fast_failed') or 0)
    expiry = timedelta(hours=float(expiry_h))

    def keep():
        return ({'status': status, 'opened_at': opened_at, 'expires_at': expires_at,
                 'reopened': reopened, 'trip': False}, None)

    def open_hole(times, why, reopened_count):
        return ({'status': BLACK_HOLE, 'opened_at': now,
                 'expires_at': now + expiry * times, 'reopened': reopened_count,
                 'trip': True}, (status, BLACK_HOLE, why))

    if status == PINNED:
        return keep()
    if status == CLEAR:
        if tripped:
            return open_hole(1, reason, 0)
        return keep()
    if status == BLACK_HOLE:
        expired = expires_at is not None and now >= expires_at
        if not expired:
            return keep()
        if tripped:
            return open_hole(2 ** (reopened + 1), REOPENED, reopened + 1)
        return ({'status': HALF_OPEN, 'opened_at': opened_at, 'expires_at': expires_at,
                 'reopened': reopened, 'trip': False}, (status, HALF_OPEN, EXPIRED))
    if status == HALF_OPEN:
        if tripped or fast_failed:
            return open_hole(2 ** (reopened + 1), REOPENED if tripped else FAST_LANDING,
                             reopened + 1)
        if finished:
            return ({'status': CLEAR, 'opened_at': None, 'expires_at': None,
                     'reopened': 0, 'trip': False}, (status, CLEAR, CLEAN_LANDING))
        return keep()
    # An unknown status is left alone and reported by the caller.
    return keep()


def apply(readings, *, now, expiry_h, mode='shadow', username='node-guard'):
    """Apply the cycle's readings to the store.

    ``readings``: ``{(queue, host): {'tripped', 'reason', 'evidence',
    'finished', 'fast_failed', 'site'}}``, every judged node plus every
    node that holds a record (so a latched or half-open node is
    revisited each cycle, with zero counts when it had no job). Returns
    the changes recorded, ``[(queue, host, old, new, reason)]``, and the
    nodes whose black hole opened or reopened this cycle.
    """
    from django.db import transaction
    from .models import NodeEnvironment, NodeState, NodeStateChange, Queue

    changes = []
    trips = []
    for (queue_name, host), reading in sorted(readings.items()):
        with transaction.atomic():
            queue, _ = Queue.objects.get_or_create(name=queue_name)
            node = (NodeState.objects.select_for_update()
                    .filter(queue=queue, host=host).first())
            if node is None:
                if not reading.get('tripped'):
                    continue            # a clear node with no record needs none
                node = NodeState(queue=queue, host=host, first_seen_at=now)
            current = {'status': node.status, 'opened_at': node.opened_at,
                       'expires_at': node.expires_at, 'reopened': node.reopened}
            nxt, change = transition(current, reading, now, expiry_h)
            node.last_seen_at = now
            node.last_verdict = {'at': now.isoformat(), 'tripped': bool(reading.get('tripped')),
                                 'reason': reading.get('reason', ''),
                                 'finished': reading.get('finished', 0),
                                 'fast_failed': reading.get('fast_failed', 0),
                                 'mode': mode}
            if reading.get('site'):
                node.site = reading['site']
            if node.node_environment_id is None:
                env = (NodeEnvironment.objects
                       .filter(environment__hostname=host).order_by('-last_seen_at').first())
                if env is not None:
                    node.node_environment = env
            if nxt['status'] == BLACK_HOLE and (nxt['trip'] or node.status == BLACK_HOLE):
                # the evidence of the standing black hole, refreshed while it stands
                if reading.get('evidence'):
                    node.evidence = reading['evidence']
            if nxt['trip']:
                node.trips += 1
                node.reason = reading.get('reason', '')
            node.status = nxt['status']
            node.opened_at = nxt['opened_at']
            node.expires_at = nxt['expires_at']
            node.reopened = nxt['reopened']
            node.save()
            if change:
                old, new, why = change
                NodeStateChange.objects.create(
                    node=node, old_status=old, new_status=new,
                    actor=NodeStateChange.Actor.GUARD, username=username, reason=why,
                    evidence=reading.get('evidence') or {},
                    data={'mode': mode, 'expires_at': (node.expires_at.isoformat()
                                                       if node.expires_at else None)})
                changes.append((queue_name, host, old, new, why))
            if nxt['trip']:
                trips.append((queue_name, host))
    return changes, trips


def set_status(queue_name, host, status, *, username, reason='', now=None):
    """A person's decision on one node: ``clear`` (the guard may trip it
    again), ``pinned`` (held as is; the guard never changes it) with
    the status it pins recorded in the reason, or ``black_hole`` (opened
    by hand, no expiry). Returns the node."""
    from django.db import transaction
    from django.utils import timezone
    from .models import NodeState, NodeStateChange, Queue

    if status not in (CLEAR, PINNED, BLACK_HOLE):
        raise ValueError(f'a person sets clear, pinned or black_hole, not {status!r}')
    now = now or timezone.now()
    with transaction.atomic():
        queue, _ = Queue.objects.get_or_create(name=queue_name)
        node, _ = (NodeState.objects.select_for_update()
                   .get_or_create(queue=queue, host=host,
                                  defaults={'first_seen_at': now, 'last_seen_at': now}))
        old = node.status
        node.status = status
        if status == CLEAR:
            node.opened_at = node.expires_at = None
            node.reopened = 0
        elif status == BLACK_HOLE:
            node.opened_at = now
            node.expires_at = None
            node.trips += 1
            node.reason = 'manual'
        node.save()
        NodeStateChange.objects.create(
            node=node, old_status=old, new_status=status,
            actor=NodeStateChange.Actor.MANUAL, username=username, reason=reason,
            evidence=node.evidence or {})
    return node


def current_exclusion():
    """The nodes a carrier must not take work on: every black hole,
    with what the exclusion needs. Half open is not excluded: one job
    may land there."""
    from .models import NodeState
    out = []
    for node in (NodeState.objects.filter(status=BLACK_HOLE)
                 .select_related('queue', 'node_environment').order_by('queue__name', 'host')):
        out.append({
            'queue': node.queue.name, 'host': node.host, 'site': node.site,
            'fingerprint': node.node_environment.fingerprint if node.node_environment else None,
            'since': node.opened_at.isoformat() if node.opened_at else None,
            'until': node.expires_at.isoformat() if node.expires_at else None,
            'reason': node.reason, 'trips': node.trips,
        })
    return out
