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
CAL = {Q: 3600.0}          # median finished walltime: fast means under 1800 s


def _rows(host, failed=0, finished=0, task=1, duration=60, queue=Q, end=None):
    # failures spread from duration to 2 x duration, so they never read as
    # a fixed-time kill unless a test builds one on purpose
    rows = []
    for i in range(failed):
        d = None if duration is None else duration * (1 + i / max(1, failed - 1))
        rows.append({'queue': queue, 'host': host, 'jobstatus': 'failed',
                     'jeditaskid': task, 'duration_s': d, 'endtime': end or i})
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
    rows = _rows('bad.site.edu', failed=7, duration=60) + _rows('good.site.edu', finished=1)
    out = guard.decide_nodes(rows, CAL)
    assert out['tripped'] == []
    assert 'bad.site.edu' not in out['queues'][Q]['nodes']
    assert out['queues'][Q]['hosts'] == 2 and out['queues'][Q]['judged'] == 0


def test_task_failing_everywhere_is_not_a_node():
    rows = _rows('a.site.edu', failed=12, duration=60) + _rows('b.site.edu', failed=12, duration=60)
    rows += _rows('c.site.edu', finished=40, task=2)    # the queue's other nodes finish other tasks
    out = guard.decide_nodes(rows, CAL)
    assert out['tripped'] == []
    for host in ('a.site.edu', 'b.site.edu'):
        assert out['queues'][Q]['nodes'][host]['reason'] == guard.TASKS_FAIL_EVERYWHERE


def test_single_node_queue_is_the_queues_case():
    # f-6: the queue's only node that day; nothing finished on any other node
    rows = _rows('fxz4', failed=132, duration=184) + _rows('fxz4', finished=7)
    out = guard.decide_nodes(rows, {Q: 4672.8})
    v = out['queues'][Q]['nodes']['fxz4']
    assert v['state'] == 'clear' and v['reason'] == guard.NO_OTHER_NODE
    assert v['evidence']['other_nodes_finishing'] == 0


def test_slow_failures_are_not_fast():
    rows = _rows('bad.site.edu', failed=12, duration=3400) + _rows('good.site.edu', finished=2)
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
    assert out['settings']['min_jobs'] == 5 and out['settings']['fast_ratio'] == 0.5


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


def test_attribution_is_contemporaneous():
    # The look back supplies the finishes; only those at or after the
    # node's first failure attribute. fxz4 fails from t=1000 on.
    rows = _rows('fxz4', failed=12, duration=180, end=None)
    for i, r in enumerate(rows):
        r['endtime'] = 1000 + i
    out = guard.decide_nodes(rows, CAL)
    assert out['queues'][Q]['nodes']['fxz4']['reason'] == guard.NO_OTHER_NODE
    # another task finishing elsewhere is not this node's task
    out = guard.decide_nodes(rows, CAL, finished_elsewhere={Q: {2: {'other.node': 2000}}})
    assert out['queues'][Q]['nodes']['fxz4']['reason'] == guard.TASKS_FAIL_EVERYWHERE
    # the task finished elsewhere after the node started failing: a trip
    out = guard.decide_nodes(rows, CAL, finished_elsewhere={Q: {1: {'other.node': 1500}}})
    assert out['tripped'] == [(Q, 'fxz4')]
    assert out['queues'][Q]['nodes']['fxz4']['evidence']['first_failure_s'] == 1000
    # the task finished elsewhere only before the node's first failure:
    # yesterday's finishes say nothing about today (the dead door)
    out = guard.decide_nodes(rows, CAL, finished_elsewhere={Q: {1: {'other.node': 900}}})
    assert out['tripped'] == []
    assert out['queues'][Q]['nodes']['fxz4']['reason'] == guard.TASKS_FAIL_EVERYWHERE
    # a finish of unknown time counts (a bare set of hosts too)
    out = guard.decide_nodes(rows, CAL, finished_elsewhere={Q: {1: {'other.node': None}}})
    assert out['tripped'] == [(Q, 'fxz4')]
    out = guard.decide_nodes(rows, CAL, finished_elsewhere={Q: {1: {'other.node'}}})
    assert out['tripped'] == [(Q, 'fxz4')]
    # a finish on the same host only is not elsewhere
    out = guard.decide_nodes(rows, CAL, finished_elsewhere={Q: {1: {'fxz4': 1500}}})
    assert out['tripped'] == []


def test_queue_failing_around_the_node_is_the_queues_event():
    # 2026-09-21: the BNL-XRD door dead, every Perlmutter node failing at
    # registration in turn, the tasks finished elsewhere the day before.
    rows = []
    for k in range(6):
        rows += _rows(f'nid00{k}', failed=10, duration=600)
    rows += _rows('nid_ok', finished=2)
    out = guard.decide_nodes(rows, CAL, finished_elsewhere={Q: {1: {'nid_yesterday': -100}}})
    assert out['tripped'] == []
    q = out['queues'][Q]
    assert q['queue_failing'] and q['queue_event']
    assert q['failed_fraction'] == round(60 / 62, 3)
    for k in range(6):
        v = q['nodes'][f'nid00{k}']
        assert v['state'] == 'clear' and v['reason'] == guard.QUEUE_FAILING, v['reason']
        assert v['evidence']['queue_others_failed_fraction'] >= 0.8


def test_black_hole_in_a_working_queue_still_trips():
    # the black hole eats most of the queue's jobs, but the rest of the
    # queue works: the others' failed fraction is what is judged
    rows = _rows('bad.site.edu', failed=40, duration=120)
    rows += _rows('good1.site.edu', finished=5) + _rows('good2.site.edu', finished=5)
    rows += _rows('good3.site.edu', finished=3, failed=1)
    out = guard.decide_nodes(rows, CAL)
    assert out['tripped'] == [(Q, 'bad.site.edu')]
    v = out['queues'][Q]['nodes']['bad.site.edu']
    assert v['evidence']['queue_others_jobs'] == 14
    assert v['evidence']['queue_others_failed_fraction'] == round(1 / 14, 3)
    assert not out['queues'][Q]['queue_failing']


def test_not_nodes_are_reported_not_judged():
    rows = _rows('slot1_1@osgsub01.sdcc.bnl.gov', failed=30, duration=5) + _rows('good.site.edu', finished=2)
    out = guard.decide_nodes(rows, CAL, {'not_nodes': ['osgsub01.sdcc.bnl.gov']})
    assert out['tripped'] == []
    assert 'osgsub01.sdcc.bnl.gov' not in out['queues'][Q]['nodes']
    assert out['queues'][Q]['not_nodes'] == {'osgsub01.sdcc.bnl.gov': {'failed': 30, 'finished': 0}}
    assert out['queues'][Q]['hosts'] == 1 and out['queues'][Q]['jobs'] == 32


def test_storm_is_the_queues_event():
    rows = []
    for i in range(12):
        rows += _rows(f'nid{i:04d}', failed=10, duration=60)
    rows += _rows('healthy', finished=3)
    # twelve hosts dying inside one interval: the burst reading
    out = guard.decide_nodes(rows, CAL, {'storm_nodes': 10})
    q = out['queues'][Q]
    assert out['tripped'] == [] and q['queue_event'] and q['storm_hosts'] == 12 and q['tripped'] == 0
    assert all(v['reason'] == guard.QUEUE_EVENT for v in q['nodes'].values())
    # the same twelve spread over hours, the queue failing around each:
    # the queue's condition, before any count of tripped hosts is taken
    for i, r in enumerate(rows):
        r['endtime'] = 600 * i
    out = guard.decide_nodes(rows, CAL, {'storm_nodes': 10})
    q = out['queues'][Q]
    assert out['tripped'] == [] and q['queue_event'] and q['queue_failing'] and q['tripped'] == 0
    assert all(v['reason'] == guard.QUEUE_FAILING for v in q['nodes'].values())
    # the same twelve in a queue that works around them: the storm cap
    rows += _rows('healthy', finished=600, end=10 ** 6)
    out = guard.decide_nodes(rows, CAL, {'storm_nodes': 10})
    q = out['queues'][Q]
    assert out['tripped'] == [] and q['queue_event'] and q['storm_hosts'] == 12 and q['tripped'] == 0
    assert all(v['reason'] == guard.QUEUE_EVENT for h, v in q['nodes'].items() if h != 'healthy')
    out = guard.decide_nodes(rows, CAL, {'storm_nodes': 12})
    assert len(out['tripped']) == 12 and not out['queues'][Q]['queue_event']


def test_slots_of_one_host_merge():
    rows = _rows('slot1_1@warlock12', failed=5, duration=2135) + _rows('slot1_2@warlock12', failed=5, duration=2135)
    rows += _rows('other.node', finished=2)
    out = guard.decide_nodes(rows, {Q: 6588.0})          # the OSG median; fast under 3294 s
    assert out['tripped'] == [(Q, 'warlock12')]
    assert out['queues'][Q]['nodes']['warlock12']['evidence']['jobs'] == 10


def test_evidence_carries_site_durations_codes_and_samples():
    rows = []
    for i in range(10):
        rows.append({'queue': Q, 'host': 'slot1_1@warlock12', 'jobstatus': 'failed', 'jeditaskid': 1,
                     'duration_s': 2100 + i * 10, 'endtime': i, 'site': 'BEOCAT-SLATE',
                     'pandaid': 1000 + i, 'error': 'pilot 1305' if i % 3 else 'trans 139'})
    rows += _rows('other.node', finished=2) + _rows('third.node', finished=1)
    out = guard.decide_nodes(rows, {Q: 6588.0})
    e = out['queues'][Q]['nodes']['warlock12']['evidence']
    assert e['site'] == 'BEOCAT-SLATE' and e['sites'] == ['BEOCAT-SLATE']
    assert e['failed_duration_s'] == {'p10': 2110.0, 'median': 2150.0, 'p90': 2180.0}
    assert e['error_codes'] == [('pilot 1305', 6), ('trans 139', 4)]
    assert e['sample_jobs'] == [1009, 1008, 1007, 1006, 1005]
    assert e['tasks_finished_elsewhere_hosts'] == {1: 2}
    # rows without the optional keys still judge, with empty evidence fields
    out = guard.decide_nodes(_rows('bare', failed=8, duration=60) + _rows('other', finished=1), CAL)
    e = out['queues'][Q]['nodes']['bare']['evidence']
    assert e['site'] == '' and e['error_codes'] == [] and e['sample_jobs'] == []
    assert 60.0 <= e['failed_duration_s']['p10'] < e['failed_duration_s']['p90'] <= 120.0


def test_fixed_time_kill_trips_under_the_floor_and_slow():
    # voh5, 2026-09-14: seven jobs, all failed at 40 to 41 min and 2.27 GB,
    # nothing finished, the queue's median 17 min (fast would be under 8.5).
    rows = []
    for i in range(7):
        rows.append({'queue': Q, 'host': f'slot1_{i % 2 + 2}@voh5', 'jobstatus': 'failed', 'jeditaskid': 39951,
                     'duration_s': 2400 + i * 15, 'endtime': i})
    rows += _rows('other.node', finished=2, task=39951)
    out = guard.decide_nodes(rows, {Q: 17 * 60.0})
    assert out['tripped'] == [(Q, 'voh5')]
    v = out['queues'][Q]['nodes']['voh5']
    assert v['reason'] == guard.FIXED_TIME and v['evidence']['fixed_time']
    # a fast fixed-time killer reads as the standard black hole first
    fast = [dict(r, duration_s=300 + (i % 2)) for i, r in enumerate(rows[:7])] + rows[7:] + _rows('slot1_2@voh5', failed=1, duration=300, task=39951)
    out = guard.decide_nodes(fast, {Q: 17 * 60.0})
    assert out['queues'][Q]['nodes']['voh5']['reason'] == guard.BLACK_HOLE
    assert v['evidence']['duration_spread'] <= 1.2 and v['evidence']['finished'] == 0
    assert out['queues'][Q]['judged'] == 1        # listed though under the 8-job floor
    # the same deaths spread out in time are not a fixed-time kill, and under the floor stay unlisted
    rows2 = [dict(r, duration_s=600 + i * 600) for i, r in enumerate(rows[:7])] + rows[7:]
    out = guard.decide_nodes(rows2, {Q: 17 * 60.0})
    assert out['tripped'] == [] and 'voh5' not in out['queues'][Q]['nodes']
    # one finished job on the node and it is not a fixed-time kill
    rows3 = rows + _rows('slot1_2@voh5', finished=1, task=39951)
    out = guard.decide_nodes(rows3, {Q: 17 * 60.0})
    assert out['tripped'] == [] and out['queues'][Q]['nodes']['voh5']['reason'] == guard.NOT_FAST
    # fewer than fixed_min_jobs failures: not judged at all
    out = guard.decide_nodes(rows[:4] + rows[7:], {Q: 17 * 60.0})
    assert 'voh5' not in out['queues'][Q]['nodes']
    # no calibration does not stop a fixed-time kill
    out = guard.decide_nodes(rows, {})
    assert out['tripped'] == [(Q, 'voh5')]


def test_burst_is_the_queues_event_not_the_big_nodes():
    # task 39973, 2026-09-15: 443 deaths on 412 hosts of BNL_OSG_PanDA_1
    # inside four minutes (the input file gone from the door); most hosts
    # lost one job, three Fir hosts with many slots lost eight each at
    # the same moment and had finished nothing in the window, and the
    # guard tripped those three as fixed-time kills.
    t0 = 1_800_000_000.0                      # a real axis: seconds
    rows = []
    for i in range(30):                       # the fleet: one death each, same minutes
        rows.append({'queue': Q, 'host': f'fc{i:05d}', 'jobstatus': 'failed', 'jeditaskid': 39973,
                     'duration_s': 400 + i, 'endtime': t0 + 10 * i})
    for big in ('fc30560', 'fc30607', 'fc30651'):
        for j in range(8):                    # a big node: eight deaths at the same moment
            rows.append({'queue': Q, 'host': f'slot1_{j}@{big}', 'jobstatus': 'failed',
                         'jeditaskid': 39973, 'duration_s': 420 + j, 'endtime': t0 + 60 + j})
    rows += _rows('other.node', finished=5, task=39973, end=t0 + 3600)   # the task goes on elsewhere
    out = guard.decide_nodes(rows, {Q: 17 * 60.0})
    q = out['queues'][Q]
    assert out['tripped'] == [] and q['queue_event'] and q['tripped'] == 0
    assert q['bursts'] and q['bursts'][0]['hosts'] == 33 and q['storm_hosts'] == 33
    for big in ('fc30560', 'fc30607', 'fc30651'):
        v = q['nodes'][big]
        assert v['state'] == 'clear' and v['reason'] == guard.QUEUE_EVENT and v['evidence']['burst']
    # the same three nodes dying alone, no fleet-wide burst: fixed-time kills, as before
    alone = [r for r in rows if not r['host'].startswith('fc0')]
    out = guard.decide_nodes(alone, {Q: 17 * 60.0})
    assert sorted(h for _, h in out['tripped']) == ['fc30560', 'fc30607', 'fc30651']
    assert not out['queues'][Q]['queue_event'] and out['queues'][Q]['bursts'] == []
    # a black hole whose deaths run for hours trips through a burst it did not cause
    hole = []
    for j in range(12):
        hole.append({'queue': Q, 'host': f'slot1_{j % 4}@warlock12', 'jobstatus': 'failed',
                     'jeditaskid': 39973, 'duration_s': 90 + j, 'endtime': t0 - 3 * 3600 + j * 900})
    out = guard.decide_nodes(rows + hole, {Q: 17 * 60.0})
    assert out['tripped'] == [(Q, 'warlock12')] and out['queues'][Q]['queue_event']
    assert out['queues'][Q]['nodes']['warlock12']['reason'] == guard.BLACK_HOLE
    # under the cap it is nobody's burst: ten hosts do not make a queue
    # event, in a queue that otherwise works
    few = [r for r in rows if not r['host'].startswith('fc0') or int(r['host'][2:]) < 10]
    few += _rows('other.node', finished=60, task=39973, end=t0 + 3600)
    out = guard.decide_nodes(few, {Q: 17 * 60.0}, {'storm_nodes': 13})
    assert not out['queues'][Q]['queue_event'] and len(out['tripped']) == 3
    # endtimes as ISO strings and as datetimes bucket the same way
    from datetime import datetime, timezone
    iso = [dict(r, endtime=datetime.fromtimestamp(r['endtime'], timezone.utc).isoformat()) for r in rows]
    dts = [dict(r, endtime=datetime.fromtimestamp(r['endtime'], timezone.utc)) for r in rows]
    assert guard.decide_nodes(iso, {Q: 17 * 60.0})['queues'][Q]['storm_hosts'] == 33
    assert guard.decide_nodes(dts, {Q: 17 * 60.0})['queues'][Q]['storm_hosts'] == 33
    assert guard._seconds('not a time') is None and guard._seconds(None) is None


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
