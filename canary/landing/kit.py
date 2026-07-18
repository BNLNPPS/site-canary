"""Landing kit orchestration: fingerprint + prmon-wrapped sample payload.

Produces the landing report: a JSON document with the environment
fingerprint and, unless suppressed, the prmon summary of a sample
payload run. The report is the raw material for the capability record
and rider packet schemas (PLAN.md increments 3 and 8).
"""
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from .. import __version__
from ..config import PRMON_PATH
from . import fingerprint

logger = logging.getLogger('canary.landing.kit')

SAMPLE_PAYLOAD = os.path.join(os.path.dirname(__file__), 'sample_payload.py')


def resolve_prmon():
    """Locate the prmon binary: CANARY_PRMON, PATH, then repo .prmon/."""
    import shutil
    candidates = []
    if PRMON_PATH:
        candidates.append(PRMON_PATH)
    on_path = shutil.which('prmon')
    if on_path:
        candidates.append(on_path)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    candidates.append(os.path.join(repo_root, '.prmon', 'bin', 'prmon'))
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def run_payload(prmon, seconds, interval=1):
    """Run the sample payload under prmon; return the result dict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        json_out = os.path.join(tmpdir, 'prmon.json')
        cmd = [prmon, '--interval', str(interval),
               '--filename', os.path.join(tmpdir, 'prmon.txt'),
               '--json-summary', json_out,
               '--log-filename', os.path.join(tmpdir, 'prmon.log'),
               '--units',
               '--', sys.executable, SAMPLE_PAYLOAD, str(seconds)]
        start = time.monotonic()
        try:
            result = subprocess.run(cmd, capture_output=True,
                                    timeout=seconds + 60, encoding='utf-8')
        except subprocess.TimeoutExpired:
            logger.error("prmon run timed out after %ss", seconds + 60)
            return {'error': f'prmon run timed out after {seconds + 60}s'}
        wall = round(time.monotonic() - start, 2)
        if result.returncode != 0:
            logger.error("prmon rc=%s: %s", result.returncode,
                         result.stderr.strip()[:500])
            return {'error': f'prmon rc={result.returncode}: '
                             f'{result.stderr.strip()[:500]}'}
        try:
            with open(json_out, encoding='utf-8') as f:
                summary = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("prmon summary read failed: %s", e)
            return {'error': f'prmon summary read failed: {e}'}
        return {'seconds_requested': seconds, 'wall_seconds': wall,
                'sample_interval': interval, 'prmon': summary}


def landing_report(payload_seconds=10, with_payload=True):
    """Assemble the landing report; error conditions are recorded in it."""
    report = {
        'schema': 'canary-landing-report/0',
        'canary_version': __version__,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'fingerprint': fingerprint.collect(),
    }
    if with_payload:
        prmon = resolve_prmon()
        if prmon is None:
            msg = ('prmon not found: set CANARY_PRMON, put prmon on PATH, '
                   'or run scripts/fetch_prmon.sh')
            logger.error(msg)
            report['payload'] = {'error': msg}
        else:
            report['prmon_path'] = prmon
            report['payload'] = run_payload(prmon, payload_seconds)
    return report
