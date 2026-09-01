"""Accounting row sources for the passive assessor.

Two sources produce identical row dicts (computingsite, creationtime,
starttime, endtime, jobstatus): the live PanDA accounting database and
a snapshot file. The snapshot format is the relay between them — the
platform side can produce one with a single query, and development
runs anywhere against it.
"""
import json
import logging

from ..config import PANDA_DSN

logger = logging.getLogger('canary.assessor.sources')

SNAPSHOT_SCHEMA = 'canary-accounting-snapshot/0'

# Creation-to-start over finished and failed jobs, per PANDA_USER_JOBS.md.
# Verification against the live BNL instance is a platform-side step.
PANDA_SQL = """
SELECT computingsite, creationtime, starttime, endtime, jobstatus
FROM doma_panda.jobsarchived4
WHERE modificationtime >= %(since)s
  AND jobstatus IN ('finished', 'failed')
"""


class SourceError(Exception):
    """A source that cannot deliver rows."""


def load_snapshot(path):
    """Load rows from a snapshot file. Raises SourceError on any fault."""
    try:
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise SourceError(f'snapshot read failed: {e}') from e
    if doc.get('schema') != SNAPSHOT_SCHEMA:
        raise SourceError(f'unsupported snapshot schema: '
                          f'{doc.get("schema")!r}')
    rows = doc.get('rows')
    if not isinstance(rows, list):
        raise SourceError('snapshot carries no rows list')
    return rows


def dump_snapshot(rows, path):
    """Write rows as a snapshot file (the platform side's export path)."""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'schema': SNAPSHOT_SCHEMA, 'rows': rows},
                  f, default=str)


def query_panda(since):
    """Query the PanDA accounting database. Requires CANARY_PANDA_DSN."""
    if not PANDA_DSN:
        raise SourceError('CANARY_PANDA_DSN is not set; use a snapshot '
                          'source away from the PanDA database host')
    try:
        import psycopg
    except ImportError as e:
        raise SourceError(f'psycopg required for the panda source: {e}') from e
    try:
        with psycopg.connect(PANDA_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(PANDA_SQL, {'since': since})
                columns = [d.name for d in cur.description]
                rows = [dict(zip(columns, r)) for r in cur.fetchall()]
    except psycopg.Error as e:
        raise SourceError(f'PanDA accounting query failed: {e}') from e
    logger.info("panda source: %d rows since %s", len(rows), since)
    return rows
