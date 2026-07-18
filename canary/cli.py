"""canary command-line interface.

Subcommands land with their increments. Unrecognized input is an error,
never silently accepted.
"""
import argparse
import json
import sys

from . import __version__


def cmd_landing(args):
    from .landing.kit import landing_report
    report = landing_report(payload_seconds=args.payload_seconds,
                            with_payload=not args.no_payload)
    text = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text + '\n')
        print(f'landing report written to {args.output}')
    else:
        print(text)
    # A payload error is a failed landing run; surface it in the exit code.
    payload = report.get('payload', {})
    return 1 if 'error' in payload else 0


def cmd_assess(args):
    from datetime import datetime, timedelta, timezone

    from .assessor import metrics, run, sources

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=args.window_days)
    try:
        if args.snapshot:
            rows = sources.load_snapshot(args.snapshot)
        else:
            rows = sources.query_panda(window_start)
    except sources.SourceError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1
    assessment = metrics.compute_queue_metrics(
        rows, window_start, window_end, min_jobs=args.min_jobs)
    if args.json:
        print(json.dumps(assessment, indent=2))
    else:
        print(run.format_table(assessment))
    if args.write:
        from .store.standalone import setup_django
        setup_django()
        written = run.write_samples(assessment)
        print(f'{len(written)} passive samples written')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='canary',
        description='Site health testing and capability assessment '
                    'for PanDA processing resources.')
    parser.add_argument('--version', action='version',
                        version=f'canary {__version__}')
    subparsers = parser.add_subparsers(dest='command')

    p_landing = subparsers.add_parser(
        'landing',
        help='characterize this node: fingerprint + prmon sample payload')
    p_landing.add_argument('--payload-seconds', type=float, default=10,
                           help='sample payload duration (default 10)')
    p_landing.add_argument('--no-payload', action='store_true',
                           help='fingerprint only, no prmon payload run')
    p_landing.add_argument('--output', '-o',
                           help='write report to file instead of stdout')
    p_landing.set_defaults(func=cmd_landing)

    p_assess = subparsers.add_parser(
        'assess',
        help='passive assessment: per-queue metrics from accounting data')
    src = p_assess.add_mutually_exclusive_group(required=True)
    src.add_argument('--snapshot', help='accounting snapshot file')
    src.add_argument('--panda', action='store_true',
                     help='query the PanDA accounting database '
                          '(needs CANARY_PANDA_DSN)')
    p_assess.add_argument('--window-days', type=float, default=14,
                          help='assessment window (default 14 days)')
    p_assess.add_argument('--min-jobs', type=int, default=50,
                          help='statistics threshold (default 50)')
    p_assess.add_argument('--write', action='store_true',
                          help='write passive samples to the store')
    p_assess.add_argument('--json', action='store_true',
                          help='JSON output instead of table')
    p_assess.set_defaults(func=cmd_assess)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    from .log import setup_logging
    setup_logging()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
