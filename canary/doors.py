"""The storage door canary (docs/STORAGE_DOORS.md): using the doors
production writes through, and the verdict on what they answered.

Two halves, kept apart the way the node guard keeps them. The probe
touches a door — one small write, one stat, one delete, and the date on
the certificate it serves — through the xrootd client and openssl, each
with its own timeout and no retry. ``decide_doors`` is pure: the
readings of one cycle in, a verdict per door out, the settings passed
in, nothing read from a database or a clock.

A reading is what ``probe_door`` returns: the door, the path it wrote,
and per step (``write``, ``stat``, ``delete``) a dict with ``ok``,
``rc``, ``text`` and ``seconds``. The caller adds ``expiry`` for the
door's certificate when it read one.
"""
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

DEFAULTS = {
    'probe_prefix': '/canary',
    'timeout_s': 60,
    'warn_days': 7,
}

# Verdicts.
UP = 'up'
DOWN = 'down'
UNKNOWN = 'unknown'

# Reasons, one per outcome of the decision.
WROTE = 'wrote'
REFUSED = 'refused'
NO_ANSWER = 'no_answer'
NOT_FORMED = 'not_formed'
STAT_UNCONFIRMED = 'stat_unconfirmed'
CANARY_CREDENTIAL = 'canary_credential'
CERTIFICATE_EXPIRED = 'certificate_expired'

# Answer classes of one step. An authorization refusal is told apart
# from any other refusal because it is the one a canary carrying a bad
# credential would see at every door at once.
OK = 'ok'
AUTH = 'auth'
NO_CLIENT = 'no_client'
SILENT = 'silent'
OTHER = 'other'

AUTH_ANSWER = re.compile(
    r"auth[a-z]* (failed|error)|not authori[sz]ed|permission denied"
    r"|no protocols left|invalid credential|proxy (expired|not found)"
    r"|operation not permitted", re.I)

# Our own runner's codes for what is not an answer from the door.
RC_TIMEOUT = 124
RC_NO_CLIENT = 127


def run_command(command, timeout_s, stdin_text=None):
    """(rc, text) from a command: rc 124 when it gives no answer in
    time, 127 when the client is not on the path, so neither is taken
    for an answer from the door."""
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout_s, input=stdin_text)
    except subprocess.TimeoutExpired:
        return RC_TIMEOUT, f"no answer in {timeout_s}s"
    except OSError as exc:
        return RC_NO_CLIENT, f"{exc.__class__.__name__}: {exc}"
    return proc.returncode, f"{proc.stdout}{proc.stderr}"


def answer_class(rc, text):
    """One step's answer as a class. Pure."""
    if rc == 0:
        return OK
    if rc == RC_NO_CLIENT:
        return NO_CLIENT
    if rc == RC_TIMEOUT:
        return SILENT
    return AUTH if AUTH_ANSWER.search(text or "") else OTHER


def door_of(protocol):
    """The door and its prefix from an RSE's write protocol, as the
    catalog gives it (``scheme``, ``hostname``, ``port``, ``prefix``):
    ``{'door': 'root://host:1094', 'prefix': '/eic/EPIC'}``, or None
    when the protocol names no host. Pure."""
    if not isinstance(protocol, dict):
        return None
    host = str(protocol.get('hostname') or '').strip()
    if not host:
        return None
    scheme = str(protocol.get('scheme') or 'root').strip() or 'root'
    port = protocol.get('port')
    authority = f"{host}:{port}" if port else host
    prefix = str(protocol.get('prefix') or '').rstrip('/')
    return {'door': f"{scheme}://{authority}", 'prefix': prefix}


def write_door(protocols, schemes=('root',)):
    """The door of the RSE's preferred write protocol among ``schemes``,
    with its priority, or None when the catalog gives none. Pure.

    The catalog ranks an RSE's protocols for writing in
    ``domains.wan.write``, lowest number first, and a protocol not for
    writing carries 0 or nothing: BNL-XRD offers https on 8443 at 3 and
    root on 1094 at 2, so root:1094 is the door its writes go through,
    the same one the payload's preserve step uses. Only xrootd doors are
    probed for now, because xrdcp and xrdfs are what the probe speaks;
    an RSE whose write protocols are all of another scheme is reported
    as not probed rather than guessed at.
    """
    ranked = []
    for protocol in protocols or ():
        if not isinstance(protocol, dict):
            continue
        if str(protocol.get('scheme') or '').lower() not in schemes:
            continue
        priority = ((protocol.get('domains') or {}).get('wan') or {}).get('write')
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            continue
        if priority <= 0:
            continue
        door = door_of(protocol)
        if door:
            ranked.append((priority, dict(door, priority=priority,
                                          scheme=protocol.get('scheme'))))
    if not ranked:
        return None
    return min(ranked, key=lambda item: item[0])[1]


def probe_path(prefix, probe_prefix, name):
    """The path the probe writes, fixed per door so a delete that fails
    is overwritten next cycle rather than accumulating. Pure.

    The prefix is kept as the catalog gives it, doubled slash and all:
    an RSE's xrootd prefix reads ``//eic/EPIC`` because in an xrootd URL
    the second slash begins the server's absolute path, so
    ``root://host:1094`` + ``//eic/EPIC/...`` is the URL the payload
    writes and ``/eic/EPIC/...`` is the path xrdfs takes
    (``server_path``).
    """
    base = (prefix or '').rstrip('/')
    tail = '/'.join(p.strip('/') for p in (probe_prefix, name) if p)
    return f"{base}/{tail}" if base else f"/{tail}"


def server_path(path):
    """The path as the door's own namespace holds it: the URL form's
    leading slashes collapsed to one. Pure."""
    return re.sub(r'^/+', '/', path or '/')


def certificate_expiry(enddate_text):
    """The notAfter openssl printed ("notAfter=Sep 20 23:59:59 2026 GMT")
    as an aware datetime, or None when the text carries none. Pure."""
    match = re.search(r"notAfter=(.+)", enddate_text or "")
    if not match:
        return None
    stamp = match.group(1).strip()
    if stamp.upper().endswith(" GMT"):
        stamp = stamp[:-4].strip()
    try:
        return datetime.strptime(stamp, "%b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def door_certificate(door, timeout_s=None, run=None):
    """The date on the certificate the door serves, or None when it
    serves none that can be read. One handshake, no retry."""
    timeout_s = timeout_s or DEFAULTS['timeout_s']
    run = run or run_command
    target = urlparse(door if '://' in door else f"//{door}")
    host, port = target.hostname, target.port or 1094
    if not host:
        return None
    _, served = run(["openssl", "s_client", "-connect", f"{host}:{port}",
                     "-servername", host], timeout_s, "")
    leaf = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
                     served or "", re.S)
    if not leaf:
        return None
    rc, ends = run(["openssl", "x509", "-noout", "-enddate"], timeout_s, leaf.group(0))
    return certificate_expiry(ends) if rc == 0 else None


def _step(run, command, timeout_s):
    started = time.monotonic()
    rc, text = run(command, timeout_s, None)
    return {
        'ok': rc == 0,
        'rc': rc,
        'class': answer_class(rc, text),
        'text': " ".join((text or "").split())[:300],
        'seconds': round(time.monotonic() - started, 2),
    }


def probe_door(door, path, timeout_s=None, run=None, size_bytes=1024):
    """Write a small file at the door, stat it, delete it, and return
    the reading. One cycle, no retries: a door that is down sees one
    touch and never a storm. The stat and the delete are not attempted
    when the write was refused — there is nothing there to find."""
    timeout_s = timeout_s or DEFAULTS['timeout_s']
    run = run or run_command
    reading = {'door': door, 'path': path}
    handle, local = tempfile.mkstemp(prefix='canary-door-', suffix='.probe')
    try:
        with os.fdopen(handle, 'wb') as f:
            f.write(b'site-canary storage door probe\n' * (size_bytes // 31 + 1))
        on_door = server_path(path)
        reading['write'] = _step(run, ['xrdcp', '-f', local, f"{door}{path}"], timeout_s)
        if reading['write']['ok']:
            reading['stat'] = _step(run, ['xrdfs', door, 'stat', on_door], timeout_s)
            reading['delete'] = _step(run, ['xrdfs', door, 'rm', on_door], timeout_s)
    finally:
        try:
            os.unlink(local)
        except OSError:
            pass
    return reading


def decide_doors(readings, settings=None, now=None):
    """The verdict per door of one cycle. Pure: the cycle's readings,
    the settings and the clock come in, a dict keyed by door comes out,
    each value carrying ``verdict``, ``reason`` and ``evidence``.

    A door is ``up`` when its write and its stat were answered, ``down``
    when its write was refused or the certificate it serves has already
    expired, and ``unknown`` when the probe was not formed or went
    unanswered with a certificate still in force. A failed delete never
    makes a door down: the door took the bytes, which is what production
    asks of it.

    One cross-door reading: when every probed door refuses in the same
    cycle with an authorization answer, none is down — that is a canary
    carrying a bad credential, and it is reported as its own condition
    rather than as a dead world.
    """
    settings = {**DEFAULTS, **(settings or {})}
    now = now or datetime.now(timezone.utc)
    warn_days = float(settings.get('warn_days') or 0)

    written = [r for r in readings if isinstance(r, dict) and r.get('write')]
    credential = (len(written) > 1
                  and all(r['write'].get('class') == AUTH for r in written))

    out = {}
    for reading in readings:
        if not isinstance(reading, dict) or not reading.get('door'):
            continue
        door = reading['door']
        write = reading.get('write') or {}
        stat = reading.get('stat') or {}
        delete = reading.get('delete') or {}
        expiry = reading.get('expiry')
        evidence = {
            'write': write.get('class') or NOT_FORMED,
            'write_seconds': write.get('seconds'),
            'stat': stat.get('class'),
            'delete': delete.get('class'),
            'said': write.get('text') or '',
            'path': reading.get('path'),
        }
        if expiry is not None:
            days = (expiry - now).total_seconds() / 86400.0
            evidence['certificate'] = expiry.date().isoformat()
            evidence['certificate_days_left'] = round(days, 2)
            evidence['certificate_expiring'] = days <= warn_days

        answer = write.get('class')
        if not write:
            verdict, reason = UNKNOWN, NOT_FORMED
        elif answer == OK:
            if stat.get('ok'):
                verdict, reason = UP, WROTE
            else:
                verdict, reason = UNKNOWN, STAT_UNCONFIRMED
        elif answer in (NO_CLIENT,):
            verdict, reason = UNKNOWN, NOT_FORMED
        elif answer == SILENT:
            verdict, reason = UNKNOWN, NO_ANSWER
        elif credential:
            verdict, reason = UNKNOWN, CANARY_CREDENTIAL
        else:
            verdict, reason = DOWN, REFUSED

        # Silence with an expired certificate is not doubt. A door whose
        # certificate has run out refuses every session the production
        # client opens, whatever this cycle's client made of it: the
        # dead BNL door answered the pilot's client "[FATAL] TLS error
        # ... error_ssl" and gave the agent's client nothing at all in
        # thirty seconds. The date is the same fact for both. A write
        # that succeeded outranks it, since the door plainly works.
        if verdict != UP and evidence.get('certificate_days_left') is not None \
                and evidence['certificate_days_left'] <= 0:
            verdict, reason = DOWN, CERTIFICATE_EXPIRED

        out[door] = {'verdict': verdict, 'reason': reason, 'evidence': evidence}
    return out
