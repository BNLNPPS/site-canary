"""canary command-line interface.

Subcommands land with their increments. Unrecognized input is an error,
never silently accepted.
"""
import argparse
import sys

from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='canary',
        description='Site health testing and capability assessment '
                    'for PanDA processing resources.')
    parser.add_argument('--version', action='version',
                        version=f'canary {__version__}')
    parser.add_subparsers(dest='command')
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
