#!/usr/bin/env python3
"""Standalone harness for the canary store: dev and ops, outside any
hosting Django project.

Configures Django around canary.store using the CANARY_DB_* settings
and dispatches store operations. In the swf-monitor deployment the host
project owns settings and migrations and this harness is not used
(docs/SWF_INTEGRATION.md).

Usage:
  storectl.py check                         validate models
  storectl.py makemigrations                generate migrations
  storectl.py migrate                       apply migrations
  storectl.py ingest FILE --site NAME [--queue NAME] [--source S]
  storectl.py map [--site NAME]             show the landscape map
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def setup_django():
    from canary.store.standalone import setup_django as _setup
    _setup()


def cmd_ingest(args):
    from canary.store.ingest import IngestError, ingest_report
    try:
        with open(args.file, encoding='utf-8') as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f'ERROR reading report: {e}', file=sys.stderr)
        return 1
    try:
        summary = ingest_report(report, args.site, queue_name=args.queue,
                                source=args.source)
    except IngestError as e:
        print(f'ERROR ingesting report: {e}', file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_map(args):
    from canary.store.models import Site
    sites = Site.objects.all()
    if args.site:
        sites = sites.filter(name=args.site)
    out = []
    for site in sites:
        out.append({
            'site': site.name,
            'status': site.status,
            'first_landing_at': site.first_landing_at,
            'last_landing_at': site.last_landing_at,
            'map': site.map,
            'environments': [
                {'fingerprint': e.fingerprint,
                 'landings': e.landing_count,
                 'last_seen_at': e.last_seen_at,
                 'os': e.environment.get('os'),
                 'cpu': e.environment.get('cpu_model'),
                 'cores': e.environment.get('cpu_cores'),
                 'mem_gb': e.environment.get('mem_gb'),
                 'gpu': e.environment.get('gpu', {}).get('present'),
                 'topology': e.topology or None}
                for e in site.node_environments.all()
            ],
        })
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_set_status(args):
    from canary.store.models import Health, Queue, StatusChange
    if args.status not in Health.values:
        print(f'ERROR: status must be one of {list(Health.values)}',
              file=sys.stderr)
        return 1
    try:
        queue = Queue.objects.get(name=args.queue)
    except Queue.DoesNotExist:
        print(f'ERROR: unknown queue {args.queue!r}', file=sys.stderr)
        return 1
    old = queue.status
    queue.status = args.status
    if args.pin:
        queue.data['manual_pin'] = True
    if args.unpin:
        queue.data.pop('manual_pin', None)
    queue.save()
    StatusChange.objects.create(
        queue=queue, old_status=old, new_status=args.status,
        actor='manual', reason=args.reason,
        data={'pinned': bool(queue.data.get('manual_pin'))})
    print(f'{queue.name}: {old} -> {args.status}'
          f'{" (pinned)" if queue.data.get("manual_pin") else ""}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='storectl', description='canary store harness')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    sub.add_parser('makemigrations')
    sub.add_parser('migrate')
    p_ingest = sub.add_parser('ingest')
    p_ingest.add_argument('file')
    p_ingest.add_argument('--site', required=True)
    p_ingest.add_argument('--queue')
    p_ingest.add_argument('--source', default='manual',
                          choices=['probe', 'rider', 'manual'])
    p_map = sub.add_parser('map')
    p_map.add_argument('--site')
    p_status = sub.add_parser('set-status')
    p_status.add_argument('queue')
    p_status.add_argument('status')
    p_status.add_argument('--pin', action='store_true',
                          help='pin: the policy evaluator will not override')
    p_status.add_argument('--unpin', action='store_true')
    p_status.add_argument('--reason', default='')
    args = parser.parse_args(argv)

    setup_django()
    if args.command in ('check', 'makemigrations', 'migrate'):
        from django.core.management import call_command
        call_command(args.command, *(
            ['canary'] if args.command == 'makemigrations' else []))
        return 0
    if args.command == 'ingest':
        return cmd_ingest(args)
    if args.command == 'map':
        return cmd_map(args)
    if args.command == 'set-status':
        return cmd_set_status(args)
    return 1


if __name__ == '__main__':
    sys.exit(main())
