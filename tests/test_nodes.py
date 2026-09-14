#!/usr/bin/env python3
"""Dict tests for the node record's transition (canary.store.nodes,
docs/NODE_GUARD.md, The record). Pure: no database."""
import os
import sys
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from canary.store import nodes  # noqa: E402

T0 = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
TRIP = {'tripped': True, 'reason': 'black_hole', 'finished': 0, 'fast_failed': 12}
QUIET = {'tripped': False, 'reason': '', 'finished': 0, 'fast_failed': 0}


def test_clear_trips_to_black_hole_with_expiry():
    nxt, change = nodes.transition(None, TRIP, T0, 24)
    assert nxt['status'] == nodes.BLACK_HOLE and nxt['trip']
    assert nxt['opened_at'] == T0 and nxt['expires_at'] == T0 + 24 * H and nxt['reopened'] == 0
    assert change == (nodes.CLEAR, nodes.BLACK_HOLE, 'black_hole')


def test_clear_stays_clear():
    nxt, change = nodes.transition({'status': 'clear'}, QUIET, T0, 24)
    assert nxt['status'] == nodes.CLEAR and change is None and not nxt['trip']


def test_black_hole_is_latched_until_expiry():
    cur = {'status': 'black_hole', 'opened_at': T0, 'expires_at': T0 + 24 * H, 'reopened': 0}
    clean = {'tripped': False, 'reason': 'failed_fraction', 'finished': 5, 'fast_failed': 0}
    nxt, change = nodes.transition(cur, clean, T0 + 3 * H, 24)
    assert nxt['status'] == nodes.BLACK_HOLE and change is None and not nxt['trip']
    nxt, change = nodes.transition(cur, TRIP, T0 + 3 * H, 24)
    assert nxt['status'] == nodes.BLACK_HOLE and change is None and nxt['expires_at'] == T0 + 24 * H


def test_expiry_goes_half_open():
    cur = {'status': 'black_hole', 'opened_at': T0, 'expires_at': T0 + 24 * H, 'reopened': 0}
    nxt, change = nodes.transition(cur, QUIET, T0 + 24 * H, 24)
    assert nxt['status'] == nodes.HALF_OPEN and change == ('black_hole', 'half_open', nodes.EXPIRED)
    assert nxt['opened_at'] == T0 and nxt['expires_at'] == T0 + 24 * H


def test_expiry_while_still_tripping_reopens_doubled():
    cur = {'status': 'black_hole', 'opened_at': T0, 'expires_at': T0 + 24 * H, 'reopened': 0}
    t = T0 + 25 * H
    nxt, change = nodes.transition(cur, TRIP, t, 24)
    assert nxt['status'] == nodes.BLACK_HOLE and nxt['trip'] and nxt['reopened'] == 1
    assert nxt['expires_at'] == t + 48 * H
    assert change == ('black_hole', 'black_hole', nodes.REOPENED)


def test_half_open_clean_landing_clears():
    cur = {'status': 'half_open', 'opened_at': T0, 'expires_at': T0 + 24 * H, 'reopened': 0}
    nxt, change = nodes.transition(cur, {'tripped': False, 'reason': '', 'finished': 1, 'fast_failed': 0},
                                   T0 + 26 * H, 24)
    assert nxt['status'] == nodes.CLEAR and nxt['opened_at'] is None and nxt['expires_at'] is None
    assert change == ('half_open', 'clear', nodes.CLEAN_LANDING)


def test_half_open_fast_failure_reopens_doubling_each_time():
    cur = {'status': 'half_open', 'opened_at': T0, 'expires_at': T0 + 24 * H, 'reopened': 0}
    t = T0 + 26 * H
    nxt, change = nodes.transition(cur, {'tripped': False, 'reason': '', 'finished': 0, 'fast_failed': 1}, t, 24)
    assert nxt['status'] == nodes.BLACK_HOLE and nxt['reopened'] == 1 and nxt['expires_at'] == t + 48 * H
    assert change == ('half_open', 'black_hole', nodes.FAST_LANDING)
    cur2 = {'status': 'half_open', 'opened_at': t, 'expires_at': t + 48 * H, 'reopened': 1}
    nxt, change = nodes.transition(cur2, TRIP, t + 50 * H, 24)
    assert nxt['reopened'] == 2 and nxt['expires_at'] == t + 50 * H + 96 * H


def test_half_open_with_nothing_landed_waits():
    cur = {'status': 'half_open', 'opened_at': T0, 'expires_at': T0 + 24 * H, 'reopened': 0}
    nxt, change = nodes.transition(cur, QUIET, T0 + 30 * H, 24)
    assert nxt['status'] == nodes.HALF_OPEN and change is None


def test_pinned_is_never_touched():
    cur = {'status': 'pinned', 'opened_at': None, 'expires_at': None, 'reopened': 0}
    for reading in (TRIP, QUIET, {'tripped': False, 'reason': '', 'finished': 3, 'fast_failed': 2}):
        nxt, change = nodes.transition(cur, reading, T0, 24)
        assert nxt['status'] == nodes.PINNED and change is None and not nxt['trip']


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
