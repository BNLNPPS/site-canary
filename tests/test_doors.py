#!/usr/bin/env python3
"""Dict tests for the storage door canary (canary.doors,
docs/STORAGE_DOORS.md).

Plain python, no framework: each test_* raises on failure; run by
tests/run_tests.sh or directly. No door is touched — the probe runs
against a fake client.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from canary import doors  # noqa: E402

NOW = datetime(2026, 9, 22, 18, 0, tzinfo=timezone.utc)
EXPIRED = datetime(2026, 9, 20, 23, 59, 59, tzinfo=timezone.utc)
VALID = NOW + timedelta(days=133)
SOON = NOW + timedelta(days=3)

BNL = 'root://epicxrd1.sdcc.bnl.gov:1094'
JLAB = 'root://dtn-rucio.jlab.org:1094'

# What the doors actually said, 2026-09-22.
TLS_SAID = ('[FATAL] TLS error: resource temporarily unavailable: Unable to '
            'connect to epicxrd1.sdcc.bnl.gov; error_ssl (destination)')
AUTH_SAID = '[FATAL] Auth failed: No protocols left to try'


def _reading(door, write='ok', stat='ok', delete='ok', said='', expiry=None):
    """A reading in the shape probe_door returns."""
    def step(kind):
        if kind is None:
            return None
        rc = {'ok': 0, 'auth': 54, 'other': 51, 'silent': doors.RC_TIMEOUT,
              'no_client': doors.RC_NO_CLIENT}[kind]
        return {'ok': kind == 'ok', 'rc': rc, 'class': kind,
                'text': said, 'seconds': 0.4}
    reading = {'door': door, 'path': '/eic/EPIC/canary/probe', 'write': step(write)}
    if write == 'ok':
        reading['stat'] = step(stat)
        reading['delete'] = step(delete)
    if expiry is not None:
        reading['expiry'] = expiry
    return reading


def test_answer_class():
    assert doors.answer_class(0, 'Path: /x') == doors.OK
    assert doors.answer_class(54, AUTH_SAID) == doors.AUTH
    assert doors.answer_class(51, TLS_SAID) == doors.OTHER
    assert doors.answer_class(doors.RC_TIMEOUT, 'no answer in 60s') == doors.SILENT
    assert doors.answer_class(doors.RC_NO_CLIENT, 'FileNotFoundError') == doors.NO_CLIENT
    assert doors.answer_class(3, 'permission denied') == doors.AUTH
    assert doors.answer_class(3, '[ERROR] No such file or directory') == doors.OTHER


def test_door_from_the_catalogs_protocol():
    got = doors.door_of({'scheme': 'root', 'hostname': 'epicxrd1.sdcc.bnl.gov',
                         'port': 1094, 'prefix': '/eic/EPIC/'})
    assert got == {'door': BNL, 'prefix': '/eic/EPIC'}
    # a protocol without a port keeps the bare host; without a host there
    # is no door to probe
    assert doors.door_of({'hostname': 'door.example'})['door'] == 'root://door.example'
    assert doors.door_of({'scheme': 'root', 'port': 1094}) is None
    assert doors.door_of(None) is None


def test_probe_path_keeps_the_catalogs_doubled_slash():
    """BNL-XRD's xrootd prefix is '//eic/EPIC': in an xrootd URL the
    second slash begins the server's absolute path, and the payload
    writes root://epicxrd1.sdcc.bnl.gov:1094//eic/EPIC/..."""
    assert doors.probe_path('//eic/EPIC', '/canary', 'probe') == '//eic/EPIC/canary/probe'
    assert doors.server_path('//eic/EPIC/canary/probe') == '/eic/EPIC/canary/probe'
    assert doors.probe_path('/eic/EPIC', '/canary', 'probe') == '/eic/EPIC/canary/probe'
    assert doors.probe_path('', 'canary/', 'probe') == '/canary/probe'
    # the same path every cycle: a delete that failed is overwritten
    assert (doors.probe_path('/a', '/b', 'p') == doors.probe_path('/a', '/b', 'p'))


# The protocols the JLab catalog gives for these RSEs, 2026-09-22.
BNL_PROTOCOLS = [
    {'scheme': 'https', 'hostname': 'epicxrd1.sdcc.bnl.gov', 'port': 8443,
     'prefix': '/eic/EPIC', 'domains': {'wan': {'write': 3, 'read': 3}}},
    {'scheme': 'root', 'hostname': 'epicxrd1.sdcc.bnl.gov', 'port': 1094,
     'prefix': '//eic/EPIC', 'domains': {'wan': {'write': 2, 'read': 2}}},
    {'scheme': 'root', 'hostname': 'epicxrd1.sdcc.bnl.gov', 'port': 1095,
     'prefix': '//eic/EPIC', 'domains': {'wan': {'write': 0, 'read': 0}}},
]
JLAB_PROTOCOLS = [
    {'scheme': 'root', 'hostname': 'dtn-eic.jlab.org', 'port': 1094,
     'prefix': '//volatile/eic/EPIC', 'domains': {'wan': {'read': 1}}},
    {'scheme': 'https', 'hostname': 'dtn-rucio.jlab.org', 'port': 1094,
     'prefix': '//volatile/eic/EPIC', 'domains': {'wan': {'write': 1}}},
    {'scheme': 'root', 'hostname': 'dtn-rucio.jlab.org', 'port': 1094,
     'prefix': '//volatile/eic/EPIC', 'domains': {'wan': {'write': 2}}},
]


def test_the_write_door_is_the_catalogs_preferred_xrootd_one():
    got = doors.write_door(BNL_PROTOCOLS)
    assert got['door'] == BNL and got['prefix'] == '//eic/EPIC' and got['priority'] == 2
    # JLab ranks https first; the xrootd door the probe speaks is next
    got = doors.write_door(JLAB_PROTOCOLS)
    assert got['door'] == JLAB and got['priority'] == 2
    # a protocol that is not for writing is not a write door
    assert doors.write_door([JLAB_PROTOCOLS[0]]) is None
    assert doors.write_door([]) is None
    assert doors.write_door([{'scheme': 'root', 'hostname': 'h',
                              'domains': {'wan': {'write': 'x'}}}]) is None


def test_certificate_expiry():
    assert doors.certificate_expiry('notAfter=Sep 20 23:59:59 2026 GMT') == EXPIRED
    assert doors.certificate_expiry('notAfter=Feb  2 12:00:00 2027') == \
        datetime(2027, 2, 2, 12, 0, tzinfo=timezone.utc)
    assert doors.certificate_expiry('unable to load certificate') is None
    assert doors.certificate_expiry('notAfter=whenever') is None
    assert doors.certificate_expiry('') is None


def test_a_door_that_takes_the_bytes_is_up():
    out = doors.decide_doors([_reading(JLAB, expiry=VALID)], now=NOW)
    assert out[JLAB]['verdict'] == doors.UP
    assert out[JLAB]['reason'] == doors.WROTE
    assert out[JLAB]['evidence']['certificate'] == '2027-02-02'
    assert out[JLAB]['evidence']['certificate_expiring'] is False


def test_a_failed_delete_does_not_make_a_door_down():
    """The door took the bytes, which is what production asks of it."""
    out = doors.decide_doors([_reading(JLAB, delete='other')], now=NOW)
    assert out[JLAB]['verdict'] == doors.UP
    assert out[JLAB]['evidence']['delete'] == doors.OTHER


def test_a_refused_write_is_down():
    out = doors.decide_doors([_reading(BNL, write='other', said=TLS_SAID,
                                       expiry=VALID)], now=NOW)
    assert out[BNL]['verdict'] == doors.DOWN
    assert out[BNL]['reason'] == doors.REFUSED
    assert 'error_ssl' in out[BNL]['evidence']['said']


def test_a_refusal_at_a_door_whose_certificate_ran_out_is_named_for_it():
    """The reason a reader sees is the cause, not the symptom."""
    out = doors.decide_doors([_reading(BNL, write='other', said=TLS_SAID,
                                       expiry=EXPIRED)], now=NOW)
    assert out[BNL]['verdict'] == doors.DOWN
    assert out[BNL]['reason'] == doors.CERTIFICATE_EXPIRED
    assert out[BNL]['evidence']['certificate_days_left'] < 0
    assert 'error_ssl' in out[BNL]['evidence']['said']


def test_silence_alone_is_never_down():
    out = doors.decide_doors([_reading(BNL, write='silent', expiry=VALID)], now=NOW)
    assert out[BNL]['verdict'] == doors.UNKNOWN
    assert out[BNL]['reason'] == doors.NO_ANSWER
    # and with no certificate read at all
    out = doors.decide_doors([_reading(BNL, write='silent')], now=NOW)
    assert out[BNL]['verdict'] == doors.UNKNOWN


def test_silence_with_an_expired_certificate_is_down():
    """What the dead BNL door gave this canary, 2026-09-22: xrdcp said
    nothing in thirty seconds while the certificate read 1.76 days
    past. The date is the fact; the client's silence is not."""
    out = doors.decide_doors([_reading(BNL, write='silent', expiry=EXPIRED)], now=NOW)
    assert out[BNL]['verdict'] == doors.DOWN
    assert out[BNL]['reason'] == doors.CERTIFICATE_EXPIRED


def test_an_expired_certificate_outranks_the_canarys_own_credential():
    readings = [_reading(BNL, write='auth', said=AUTH_SAID, expiry=EXPIRED),
                _reading(JLAB, write='auth', said=AUTH_SAID, expiry=VALID)]
    out = doors.decide_doors(readings, now=NOW)
    assert out[BNL]['verdict'] == doors.DOWN
    assert out[BNL]['reason'] == doors.CERTIFICATE_EXPIRED
    assert out[JLAB]['verdict'] == doors.UNKNOWN
    assert out[JLAB]['reason'] == doors.CANARY_CREDENTIAL


def test_a_door_that_takes_bytes_outranks_its_certificate_date():
    """A write that succeeded says the door works, whatever a date
    read beside it says."""
    out = doors.decide_doors([_reading(JLAB, expiry=EXPIRED)], now=NOW)
    assert out[JLAB]['verdict'] == doors.UP


def test_no_client_is_a_probe_that_was_not_formed():
    out = doors.decide_doors([_reading(BNL, write='no_client')], now=NOW)
    assert out[BNL]['verdict'] == doors.UNKNOWN and out[BNL]['reason'] == doors.NOT_FORMED
    # and a reading with no write step at all
    out = doors.decide_doors([{'door': BNL}], now=NOW)
    assert out[BNL]['verdict'] == doors.UNKNOWN and out[BNL]['reason'] == doors.NOT_FORMED


def test_a_write_the_stat_did_not_confirm_is_unknown():
    out = doors.decide_doors([_reading(JLAB, stat='other')], now=NOW)
    assert out[JLAB]['verdict'] == doors.UNKNOWN
    assert out[JLAB]['reason'] == doors.STAT_UNCONFIRMED


def test_every_door_refusing_authorization_is_the_canarys_own_fault():
    """A proxy that has expired on the agent would otherwise condemn
    every door at once."""
    readings = [_reading(BNL, write='auth', said=AUTH_SAID),
                _reading(JLAB, write='auth', said=AUTH_SAID)]
    out = doors.decide_doors(readings, now=NOW)
    assert [v['verdict'] for v in out.values()] == [doors.UNKNOWN, doors.UNKNOWN]
    assert {v['reason'] for v in out.values()} == {doors.CANARY_CREDENTIAL}


def test_one_door_refusing_authorization_while_another_works_is_down():
    readings = [_reading(BNL, write='auth', said=AUTH_SAID), _reading(JLAB)]
    out = doors.decide_doors(readings, now=NOW)
    assert out[BNL]['verdict'] == doors.DOWN
    assert out[JLAB]['verdict'] == doors.UP


def test_a_single_door_refusing_alone_is_down():
    """With one door probed there is nothing to compare it against, so
    the reading stands as what it says."""
    out = doors.decide_doors([_reading(BNL, write='auth', said=AUTH_SAID)], now=NOW)
    assert out[BNL]['verdict'] == doors.DOWN


def test_a_certificate_near_its_end_is_reported_while_the_door_works():
    out = doors.decide_doors([_reading(JLAB, expiry=SOON)], now=NOW,
                             settings={'warn_days': 7})
    assert out[JLAB]['verdict'] == doors.UP
    assert out[JLAB]['evidence']['certificate_expiring'] is True
    out = doors.decide_doors([_reading(JLAB, expiry=SOON)], now=NOW,
                             settings={'warn_days': 1})
    assert out[JLAB]['evidence']['certificate_expiring'] is False


def test_malformed_readings_are_skipped_not_dropped_into_a_verdict():
    out = doors.decide_doors([None, {}, {'path': '/x'}, _reading(JLAB)], now=NOW)
    assert list(out) == [JLAB]


def test_the_probe_touches_the_door_once_and_skips_what_is_moot():
    """A refused write leaves nothing to stat or delete."""
    calls = []

    def fake(command, timeout_s, stdin_text=None):
        calls.append(command)
        if command[0] == 'xrdcp':
            return 51, TLS_SAID
        return 0, ''
    reading = doors.probe_door(BNL, '/eic/EPIC/canary/probe', 30, run=fake)
    assert [c[0] for c in calls] == ['xrdcp']
    assert reading['write']['class'] == doors.OTHER
    assert 'stat' not in reading and 'delete' not in reading

    calls.clear()

    def working(command, timeout_s, stdin_text=None):
        calls.append(command)
        return 0, 'Path: /eic/EPIC/canary/probe\nSize: 1024\n'
    reading = doors.probe_door(BNL, '/eic/EPIC/canary/probe', 30, run=working)
    assert [c[0] for c in calls] == ['xrdcp', 'xrdfs', 'xrdfs']
    assert calls[1][2] == 'stat' and calls[2][2] == 'rm'
    assert reading['write']['ok'] and reading['stat']['ok'] and reading['delete']['ok']
    # the local probe file is not left behind
    assert not [c for c in calls if c[0] == 'xrdcp' and not os.path.dirname(c[2])]
    assert not os.path.exists(calls[0][2])


def test_the_certificate_read_is_one_handshake():
    served = ('CONNECTED\n-----BEGIN CERTIFICATE-----\nMIIB\n'
              '-----END CERTIFICATE-----\n')
    calls = []

    def fake(command, timeout_s, stdin_text=None):
        calls.append(command[1])
        if command[1] == 's_client':
            return 0, served
        return 0, 'notAfter=Sep 20 23:59:59 2026 GMT\n'
    assert doors.door_certificate(BNL, 20, run=fake) == EXPIRED
    assert calls == ['s_client', 'x509']

    def serves_nothing(command, timeout_s, stdin_text=None):
        return 1, 'connect: errno=111'
    assert doors.door_certificate(BNL, 20, run=serves_nothing) is None
    # a door string naming no host is not a handshake anyone can make
    assert doors.door_certificate('root://', 20, run=fake) is None


if __name__ == '__main__':
    names = [n for n in dir() if n.startswith('test_')]
    failed = 0
    for name in names:
        try:
            globals()[name]()
            print(f'PASS {name}')
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f'FAIL {name}: {type(exc).__name__}: {exc}')
    sys.exit(1 if failed else 0)
