"""Declared downtime as the canary reads it (canary/declared.py): the
policy's hold, the probe dispatch's disposition. Pure; no provider."""
from datetime import datetime, timezone as dt_timezone

from canary.declared import hold_reason, overlaps, probe_disposition


def _t(s):
    return datetime.fromisoformat(s).replace(tzinfo=dt_timezone.utc)


# E1_BNL on 2026-09-14: a rule set 02:20 UTC, expired 00:21 UTC the next
# day, its span carrying the ten-minute cache lag.
INFO = {'in_force': '', 'spans': [('2026-09-14T02:20:19+00:00', '2026-09-15T00:31:00+00:00')],
        'last_end': '2026-09-15T00:31:00+00:00'}
IN_FORCE = dict(INFO, in_force='offline until 09/15 00:21 UTC: scheduled downtime (xzhao@bnl.gov)')


def test_a_sample_window_over_the_declaration_holds_the_status():
    assert overlaps(INFO, _t('2026-09-14T12:00:00'), _t('2026-09-15T00:00:00'))
    assert overlaps(INFO, _t('2026-09-15T00:20:00'), _t('2026-09-15T12:00:00'))
    assert not overlaps(INFO, _t('2026-09-15T01:00:00'), _t('2026-09-15T13:00:00'))
    assert hold_reason(INFO, _t('2026-09-14T12:00:00'), _t('2026-09-15T00:00:00')) == \
        'the sample overlaps a declared downtime'
    assert hold_reason(INFO, _t('2026-09-15T01:00:00'), _t('2026-09-15T13:00:00')) == ''
    assert hold_reason(IN_FORCE).startswith('declared downtime in force: offline until')
    assert hold_reason(None) == '' and hold_reason({}) == ''


def test_no_probe_under_a_rule_and_one_at_once_after_its_end():
    now = _t('2026-09-14T12:00:00')
    assert probe_disposition(IN_FORCE, _t('2026-09-14T00:00:00'), now) == 'hold'
    after = _t('2026-09-15T01:00:00')
    # The last probe predates the window's end: due now, whatever the cadence.
    assert probe_disposition(INFO, _t('2026-09-14T00:00:00'), after) == 'due'
    assert probe_disposition(INFO, None, after) == 'due'
    # A probe already sent after the end: the ordinary cadence.
    assert probe_disposition(INFO, _t('2026-09-15T00:40:00'), after) == ''
    assert probe_disposition({}, _t('2026-09-14T00:00:00'), after) == ''
