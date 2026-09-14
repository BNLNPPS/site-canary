#!/usr/bin/env python3
"""Dict tests for the node guard's decision (canary.guard, docs/NODE_GUARD.md).

Plain python, no framework: each test_* raises on failure; run by
tests/test_basic.py's runner or directly.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from canary import guard  # noqa: E402

Q = 'BNL_OSG_EPIC_PROD_1'
CAL = {Q: 3600.0}          # median finished walltime: fast means under 900 s


def _rows(host, failed=0, finished=0, task=1, duration=60, queue=Q, end=None):
    rows = []
    for i in range(failed):
        rows.append({'queue': queue, 'host': host, 'jobstatus': 'failed',
                     'jeditaskid': task, 'duration_s': duration, 'endtime': end or i})
    for i in range(finished):
        rows.append({'queue': queue, 'host': host, 'jobstatus': 'finished',
                     'jeditaskid': task, 'duration_s': 3500, 'endtime': end or i})
    return rows


def test_normalize_host():
    assert guard.normalize_host('slot1_8@node12.cluster.edu') == 'node12.cluster.edu'
    assert guard.normalize_host(' nid001234 ') == 'nid001234'
    assert guard.normalize_host('n358') == 'n358'
    assert guard.normalize_host(None) == ''
    assert guard.normalize_host('') == ''


def test_black_hole_trips():
    rows = _rows('slot1_1@bad.site.edu', failed=12, duration=120)
    rows += _rows('good.site.edu', finished=3)          # the task finishes elsewhere
    out = guard.decide_nodes(rows, CAL)
    assert out['tripped'] == [(Q, 'bad.site.edu')], out['tripped']
    v = out['queues'][Q]['nodes']['bad.site.edu']
    assert v['state'] == 'tripped' and v['reason'] == guard.BLACK_HOLE
    e = v['evidence']
    assert e['jobs'] == 12 and e['failed'] == 12 and e['fast_failed'] == 12
    assert e['tasks_finished_elsewhere'] == [1]
    assert out['queues'][Q]['judged'] == 1        # good.site.edu is under the floor
    assert out['queues'][Q]['hosts'] == 2
    assert out['malformed'] == 0


def test_below_floor_not_judged():
    rows = _rows('bad.site.edu', failed=9, duration=60) + _rows('good.site.edu', finished=1)
    out = guard.decide_nodes(rows, CAL)
    assert out['tripped'] == []
    assert 'bad.site.edu' not in out['queues'][Q]['nodes']
    assert out['queues'][Q]['hosts'] == 2 and out['queues'][Q]['judged'] == 0


def test_task_failing_everywhere_is_not_a_node():
    rows = _rows('a.site.edu', failed=12, duration=60) + _rows('b.site.edu', failed=12, duration=60)
    out = guard.decide_nodes(rows, CAL)
    assert out['tripped'] == []
    for host in ('a.site.edu', 'b.site.edu'):
        assert out['queues'][Q]['nodes'][host]['reason'] == guard.TASKS_FAIL_EVERYWHERE


def test_slow_failures_are_not_fast():
    rows = _rows('bad.site.edu', failed=12, duration=3000) + _rows('good.site.edu', finished=2)
    out = guard.decide_nodes(rows, CAL)
    assert out['tripped'] == []
    assert out['queues'][Q]['nodes']['bad.site.edu']['reason'] == guard.NOT_FAST


def test_failed_fraction_under_threshold():
    rows = _rows('mixed.site.edu', failed=7, finished=5, duration=60)
    out = guard.decide_nodes(rows, CAL)
    v = out['queues'][Q]['nodes']['mixed.site.edu']
    assert v['state'] == 'clear' and v['reason'] == guard.FAILED_FRACTION
    assert v['evidence']['failed_fraction'] == round(7 / 12, 3)


def test_no_calibration_does_not_trip():
    rows = _rows('bad.site.edu', failed=12, duration=60) + _rows('good.site.edu', finished=2)
    out = guard.decide_nodes(rows, {})
    assert out['tripped'] == []
    assert out['queues'][Q]['nodes']['bad.site.edu']['reason'] == guard.NO_CALIBRATION


def test_missing_duration_counts_against_tripping():
    rows = _rows('bad.site.edu', failed=6, duration=60) + _rows('bad.site.edu', failed=6, duration=None)
    rows += _rows('good.site.edu', finished=2)
    out = guard.decide_nodes(rows, CAL)
    v = out['queues'][Q]['nodes']['bad.site.edu']
    assert v['evidence']['failed_no_duration'] == 6 and v['evidence']['fast_failed'] == 6
    assert v['state'] == 'tripped'          # exactly half fast, at the default 0.5
    out2 = guard.decide_nodes(rows, CAL, {'fast_fraction': 0.6})
    assert out2['queues'][Q]['nodes']['bad.site.edu']['reason'] == guard.NOT_FAST


def test_settings_override_and_seed():
    rows = _rows('bad.site.edu', failed=5, duration=60) + _rows('good.site.edu', finished=2)
    out = guard.decide_nodes(rows, CAL, {'min_jobs': 5})
    assert out['tripped'] == [(Q, 'bad.site.edu')]
    assert out['settings']['min_jobs'] == 5 and out['settings']['fast_ratio'] == 0.25


def test_malformed_rows_are_counted():
    rows = _rows('bad.site.edu', failed=12, duration=60) + _rows('good.site.edu', finished=2)
    rows += [{'queue': Q, 'host': '', 'jobstatus': 'failed'},
             {'queue': None, 'host': 'x', 'jobstatus': 'failed'},
             {'queue': Q, 'host': 'y', 'jobstatus': 'running'}, None]
    out = guard.decide_nodes(rows, CAL)
    assert out['malformed'] == 4
    assert out['tripped'] == [(Q, 'bad.site.edu')]


def test_hosts_are_distinct_per_queue():
    rows = _rows('n358', failed=12, duration=60, queue='UM_GREX_PanDA_1')
    rows += _rows('other', finished=2, queue='UM_GREX_PanDA_1')
    rows += _rows('n358', finished=12, queue=Q)
    out = guard.decide_nodes(rows, {'UM_GREX_PanDA_1': 7200.0, Q: 3600.0})
    assert out['tripped'] == [('UM_GREX_PanDA_1', 'n358')]
    assert out['queues'][Q]['nodes']['n358']['state'] == 'clear'


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
