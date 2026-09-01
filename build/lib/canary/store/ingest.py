"""Landing report ingest: the path from a landing to the map.

One transaction per report: the site record starts or updates, the node
environment is created or refreshed under horizontal identity, the
report is stored, and the site-level map is recomputed from the site's
node environments. Recomputation is deterministic — the site map is a
projection of the current node census, not an accumulating cache.
"""
import logging
from datetime import datetime

from django.db import transaction
from django.utils.dateparse import parse_datetime

from .models import LandingReport, NodeEnvironment, Site

logger = logging.getLogger('canary.store.ingest')

REPORT_SCHEMA = 'canary-landing-report/0'


class IngestError(Exception):
    """A landing report that cannot be ingested."""


def _site_map(site):
    """Recompute the site-level map from the site's node environments."""
    envs = list(site.node_environments.all())
    platforms = sorted({e.environment.get('os', 'unknown') for e in envs})
    arches = sorted({e.environment.get('arch', 'unknown') for e in envs})
    cores = [e.environment.get('cpu_cores') for e in envs
             if e.environment.get('cpu_cores')]
    mems = [e.environment.get('mem_gb') for e in envs
            if e.environment.get('mem_gb')]
    return {
        'environments': len(envs),
        'landings': sum(e.landing_count for e in envs),
        'platforms': platforms,
        'arches': arches,
        'cpu_cores': {'min': min(cores), 'max': max(cores)} if cores else None,
        'mem_gb': {'min': min(mems), 'max': max(mems)} if mems else None,
        'gpu_environments': sum(
            1 for e in envs if e.environment.get('gpu', {}).get('present')),
        'cvmfs_environments': sum(
            1 for e in envs
            if any(r.get('reachable')
                   for r in e.environment.get('cvmfs', {}).values())),
    }


@transaction.atomic
def ingest_report(report, site_name, queue_name=None, source='manual'):
    """Ingest one landing report dict. Returns an ingest summary.

    Raises IngestError on a malformed report; the caller decides how to
    surface it. Never ingests silently-partially.
    """
    schema = report.get('schema')
    if schema != REPORT_SCHEMA:
        raise IngestError(f'unsupported report schema: {schema!r}')
    fp = report.get('fingerprint') or {}
    fp_hash = fp.get('fingerprint')
    if not fp_hash:
        raise IngestError('report carries no fingerprint hash')
    landed_at = parse_datetime(report.get('generated_at') or '')
    if landed_at is None:
        raise IngestError(
            f'bad generated_at: {report.get("generated_at")!r}')

    site, site_created = Site.objects.get_or_create(name=site_name)
    if site.first_landing_at is None or landed_at < site.first_landing_at:
        site.first_landing_at = landed_at
    if site.last_landing_at is None or landed_at > site.last_landing_at:
        site.last_landing_at = landed_at

    env, env_created = NodeEnvironment.objects.get_or_create(
        site=site, fingerprint=fp_hash,
        defaults={'environment': fp, 'first_seen_at': landed_at})
    env.environment = fp
    topology = (report.get('payload', {}).get('prmon', {}).get('HW') or {})
    if topology:
        env.topology = topology
    env.landing_count += 1
    if env.first_seen_at is None or landed_at < env.first_seen_at:
        env.first_seen_at = landed_at
    if env.last_seen_at is None or landed_at > env.last_seen_at:
        env.last_seen_at = landed_at
    env.save()

    queue = None
    if queue_name:
        from .models import Queue
        queue, _ = Queue.objects.get_or_create(
            name=queue_name, defaults={'site': site})

    row = LandingReport.objects.create(
        site=site, queue=queue, node_environment=env,
        source=source, report=report, landed_at=landed_at)

    site.map = _site_map(site)
    site.save()

    summary = {
        'site': site.name,
        'site_created': site_created,
        'environment': fp_hash,
        'environment_created': env_created,
        'landing_count': env.landing_count,
        'report_id': str(row.id),
        'site_map': site.map,
    }
    logger.info("ingested landing: %s", summary)
    return summary
