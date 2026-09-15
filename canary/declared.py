"""Declared downtime as the canary reads it (docs/NODE_GUARD.md's
sibling: the platform's declared record, swf-epicprod
CONTINUOUS_PRODUCTION.md, Declared downtime).

A downtime declared for a queue is not evidence about the queue: the
policy does not move a queue's status on a passive sample that overlaps
one, and no probe is sent to a queue under a rule in force (the pilot
aborts on an OFFLINE queue, so the probe would only measure the
declaration). The first probe after a window's end goes at once, the
confirmation that the queue is back.

The platform supplies what is declared through the provider named by
``CANARY_DECLARED_PROVIDER`` (``module:function``), called as
``provider(queue_names, now)`` and answering ``{queue: {'in_force':
line or '', 'spans': [(start_iso, stop_iso or None), ...],
'last_end': iso or None}}``: the rule in force now with its line, every
span the queue was declared over (the rule's start to its end plus
the pilot's cache lag), and the latest span end. The default provider,
``http_provider``, reads the platform's record at
``CANARY_DECLARED_URL`` (default ``SWF_MONITOR_URL`` + ``/api/declared/``)
so a standalone process needs no ORM. No provider, or one that cannot
be imported or fails, reads as nothing declared, logged once per
process.
"""
import logging
import os
from datetime import datetime, timezone as dt_timezone

logger = logging.getLogger('canary.declared')

_provider = {'loaded': False, 'fn': None}
_url_warned = {'done': False}


def declared_url():
    """The platform's declared endpoint: CANARY_DECLARED_URL, else
    SWF_MONITOR_URL + /api/declared/, else ''."""
    from .config import DECLARED_URL
    if DECLARED_URL:
        return DECLARED_URL
    base = (os.environ.get('SWF_MONITOR_URL') or '').rstrip('/')
    return f'{base}/api/declared/' if base else ''


def http_provider(queue_names, now):
    """The default provider: one GET of the platform's declared record,
    answered per queue. Raises on a failed read (declared_info logs it
    and reads nothing declared)."""
    import json
    import ssl
    import urllib.request
    url = declared_url()
    if not url:
        if not _url_warned['done']:
            _url_warned['done'] = True
            logger.warning('no declared endpoint (CANARY_DECLARED_URL or SWF_MONITOR_URL); '
                           'nothing reads as declared')
        return {}
    ctx = ssl.create_default_context()
    if url.startswith('https://localhost'):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(url, context=ctx, timeout=20) as r:
        doc = json.load(r)
    queues = (doc or {}).get('queues') or {}
    names = set(queue_names or [])
    return {name: {'in_force': info.get('in_force') or '',
                   'spans': [tuple(s) for s in (info.get('spans') or [])],
                   'last_end': info.get('last_end')}
            for name, info in queues.items() if not names or name in names}


def _dt(text):
    if not text:
        return None
    if isinstance(text, datetime):
        return text if text.tzinfo else text.replace(tzinfo=dt_timezone.utc)
    try:
        dt = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=dt_timezone.utc)


def load_provider():
    """The provider function, imported once from the configured
    ``module:function``; None when unset or unimportable (logged)."""
    if _provider['loaded']:
        return _provider['fn']
    _provider['loaded'] = True
    from .config import DECLARED_PROVIDER
    spec = (DECLARED_PROVIDER or '').strip()
    if not spec or ':' not in spec:
        return None
    module_name, func_name = spec.split(':', 1)
    try:
        import importlib
        _provider['fn'] = getattr(importlib.import_module(module_name), func_name)
    except Exception as e:                                    # noqa: BLE001
        logger.warning('declared provider %s unavailable, nothing reads as declared: %s',
                       spec, e)
        _provider['fn'] = None
    return _provider['fn']


def declared_info(queue_names, now):
    """What the provider declares for the queues, ``{}`` when there is
    no provider or it failed (logged)."""
    fn = load_provider()
    if fn is None:
        return {}
    try:
        return fn(list(queue_names), now) or {}
    except Exception as e:                                    # noqa: BLE001
        logger.error('declared provider failed, nothing reads as declared: %s', e)
        return {}


def overlaps(info, window_start, window_end):
    """Whether a sample window overlaps any span the queue was declared
    over. Pure."""
    ws, we = _dt(window_start), _dt(window_end)
    if ws is None or we is None:
        return False
    for start, stop in (info or {}).get('spans') or []:
        s, e = _dt(start), _dt(stop)
        if s is None:
            continue
        if s < we and (e is None or e > ws):
            return True
    return False


def hold_reason(info, window_start=None, window_end=None):
    """Why the policy leaves a queue's status alone: a rule in force
    now, or a sample window overlapping a declaration; '' otherwise.
    Pure."""
    info = info or {}
    if info.get('in_force'):
        return f"declared downtime in force: {info['in_force']}"
    if window_start is not None and overlaps(info, window_start, window_end):
        return 'the sample overlaps a declared downtime'
    return ''


def probe_disposition(info, last_submitted_at, now):
    """The probe dispatch's reading of a queue: ``'hold'`` under a rule
    in force (no probe is sent), ``'due'`` when the last probe predates
    the latest declared end, so the first probe after a window confirms
    the queue is back, else ``''`` (the ordinary cadence). Pure."""
    info = info or {}
    if info.get('in_force'):
        return 'hold'
    last_end = _dt(info.get('last_end'))
    if last_end is not None and last_end <= now:
        last = _dt(last_submitted_at)
        if last is None or last < last_end:
            return 'due'
    return ''
