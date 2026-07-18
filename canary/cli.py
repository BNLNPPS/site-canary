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

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    from .log import setup_logging
    setup_logging()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
