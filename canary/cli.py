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


def cmd_evaluate(args):
    from .policy.engine import apply
    from .policy.loader import PolicyError, load_policy
    from .store.standalone import setup_django

    from .config import POLICY_PATH
    try:
        policy = load_policy(args.policy or POLICY_PATH or None)
    except PolicyError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1
    setup_django()
    results = apply(policy, write=args.write)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            arrow = (f"{r['status_before']} -> {r['status_after']}"
                     if r['transition'] else r['status_after'])
            note = f" [{r['blocked']}]" if r['blocked'] else ''
            print(f"{r['queue']:<32} {r['verdict']:<12} {arrow:<22} "
                  f"{r['reason']}{note}")
        mode = 'applied' if args.write else 'dry run — nothing written'
        print(f"policy {policy['policy']}/{policy['version']} · "
              f"{sum(1 for r in results if r['transition'])} transitions "
              f"({mode})")
    return 0


def cmd_probe_dispatch(args):
    from datetime import datetime, timezone as dt_timezone

    from .store.standalone import setup_django

    setup_django()
    from . import probe
    now = datetime.now(dt_timezone.utc)
    collected = probe.collect_run_outcomes()
    if args.collect_only:
        results = []
    else:
        results = probe.dispatch(
            now, queue_names=args.queue or None, force=bool(args.queue))
    if args.json:
        print(json.dumps({'collected': collected, 'results': results},
                         indent=2))
    else:
        print('collection: ' + ', '.join(
            f'{k} {v}' for k, v in collected.items()))
        if args.collect_only:
            pass
        elif not results:
            print('no queues due')
        for r in results:
            extra = (f" jediTaskID={r['jeditaskid']}"
                     if r.get('jeditaskid') else '')
            print(f"{r['queue']:<32} {r['outcome']}{extra}")
    submissions_ok = (all(r['outcome'] in ('submitted',) for r in results)
                      or not results)
    return 0 if submissions_ok and not collected['errors'] else 1


def cmd_payload_canary(args):
    """One payload canary: the named PCS task's first manifest row through
    the production payload on the named queue, as a canary task with an
    expiring output dataset; the run is a ProbeRun of kind payload whose
    verdict the next collection cycle reads from the payload report."""
    from datetime import datetime, timezone as dt_timezone

    from .store.standalone import setup_django

    setup_django()
    from . import probe
    now = datetime.now(dt_timezone.utc)
    result = probe.dispatch_payload_canary(
        now, args.task, args.queue, row=args.row, row_text=args.row_text,
        mem_limit_mb=args.mem_limit_mb, signature=args.signature,
        pandaid=args.pandaid, container=args.container,
        request_id=args.request_id, threads=args.threads,
        payload_ref=args.payload_ref, switches=args.switch,
        credential=args.credential, sender=args.sender)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        extra = (f" jediTaskID={result['jeditaskid']}"
                 if result.get('jeditaskid') else '')
        print(f"{result.get('queue', args.queue):<32} {result['outcome']}"
              f"{extra} {result.get('stamp', '')}")
    return 0 if result.get('outcome') == 'submitted' else 1


def cmd_workdir_hygiene(args):
    from .hygiene import HygieneError, workdir_hygiene_report

    try:
        report = workdir_hygiene_report(
            root=args.root,
            root_parent_of_cwd=args.root_parent_of_cwd,
            name_regex=args.name_regex,
            min_age_hours=args.min_age_hours,
            delete=args.delete,
            max_job_runtime_hours=args.max_job_runtime_hours,
            safety_margin_hours=args.safety_margin_hours,
            max_delete=args.max_delete,
            protected_paths=args.protect,
            report_limit=args.report_limit,
        )
    except HygieneError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as output:
            output.write(text + '\n')
        print(f'workdir hygiene report written to {args.output}')
    else:
        print(text)
    return 1 if report['status'] == 'error' else 0


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

    p_eval = subparsers.add_parser(
        'evaluate',
        help='evaluate policy over stored evidence; verdicts, no actuation')
    p_eval.add_argument('--policy', help='policy file '
                        '(default: packaged epic.yaml, or CANARY_POLICY)')
    p_eval.add_argument('--write', action='store_true',
                        help='record verdicts and apply status transitions')
    p_eval.add_argument('--json', action='store_true',
                        help='JSON output instead of table')
    p_eval.set_defaults(func=cmd_evaluate)

    p_probe = subparsers.add_parser(
        'probe-dispatch',
        help='collect the outcomes of open probe runs, then submit probes '
             'for due queues (or forced named queues)')
    p_probe.add_argument('--queue', action='append',
                         help='force a probe for this queue now '
                              '(repeatable); default: the due queues')
    p_probe.add_argument('--collect-only', action='store_true',
                         help='collect outcomes of open runs; submit nothing')
    p_probe.add_argument('--json', action='store_true',
                         help='JSON output')
    p_probe.set_defaults(func=cmd_probe_dispatch)

    p_payload = subparsers.add_parser(
        'payload-canary',
        help='run the production payload on one manifest row of a PCS '
             'task as a canary task on a queue, outputs to an expiring '
             'dataset under epic:/TEST/; the verdict is collected by the '
             'next probe-dispatch cycle')
    p_payload.add_argument('--task', required=True,
                           help='the PCS task (composed name) whose first '
                                'manifest row and configuration the canary runs')
    p_payload.add_argument('--queue', required=True,
                           help='the PanDA queue to run it on')
    p_payload.add_argument('--row', type=int, default=0,
                           help='this manifest row of the task (1-based) '
                                'instead of the first')
    p_payload.add_argument('--row-text', default='',
                           help='this exact manifest row '
                                '(file,ext,nevents,ichunk) instead of one '
                                "of the task's: a crashed job's row")
    p_payload.add_argument('--mem-limit-mb', type=int, default=0,
                           help='an address-space limit on the payload, MB')
    p_payload.add_argument('--signature', default='',
                           help='the crash signature this run reproduces')
    p_payload.add_argument('--pandaid', type=int, default=0,
                           help='the crashed job this run reproduces')
    p_payload.add_argument('--container', default='',
                           help="the container image to run instead of the "
                                "task's configuration's (the image the "
                                "crashed task ran)")
    p_payload.add_argument('--threads', type=int, default=0,
                           help='cores the job requests and threads npsim '
                                'and eicrecon run with, where the image '
                                'supports it (swf-epicprod EPICPROD_PAYLOAD.md, '
                                'Multithreading); memory stays per core')
    p_payload.add_argument('--request-id', default='',
                           help='the identity of the reproduction request '
                                'this run answers, recorded on the run')
    p_payload.add_argument('--payload-ref', default='',
                           help="a developer's canary (swf-epicprod "
                                'DEVELOPING_THE_PAYLOAD.md): the payload of '
                                'pr:<N>, <branch> or <owner>:<branch> instead '
                                'of the deployed one')
    p_payload.add_argument('--switch', action='append', default=[],
                           help='EPICPROD_SWITCH_<NAME>=<value> in the job '
                                'environment; repeatable')
    p_payload.add_argument('--credential', choices=['x509', 'token', 'panda'], default='x509',
                           help='x509 ships the eicprod proxy (default); token '
                                'ships no proxy and the CANARY_TOKEN_FILE token; '
                                'panda ships an expired placeholder certificate, '
                                'so the job has only the tokens it gets from the '
                                'PanDA server')
    p_payload.add_argument('--sender', default='',
                           help='who sent the canary, recorded on the run')
    p_payload.add_argument('--json', action='store_true',
                           help='JSON output')
    p_payload.set_defaults(func=cmd_payload_canary)

    p_hygiene = subparsers.add_parser(
        'workdir-hygiene',
        help='inspect or clean stale PanDA workdirs as an ordinary payload')
    root = p_hygiene.add_mutually_exclusive_group(required=True)
    root.add_argument('--root', help='exact shared PanDA work-root path')
    root.add_argument(
        '--root-parent-of-cwd', action='store_true',
        help='use the parent of this cleanup job workdir as the work root')
    p_hygiene.add_argument(
        '--name-regex', default=r'eic_[A-Za-z0-9]+',
        help='full-match regex for owned workdir names '
             '(default: eic_[A-Za-z0-9]+)')
    p_hygiene.add_argument(
        '--min-age-hours', type=float, default=168,
        help='minimum top-level workdir age (default: 168 hours)')
    p_hygiene.add_argument(
        '--delete', action='store_true',
        help='delete eligible workdirs; default is scan-only')
    p_hygiene.add_argument(
        '--max-job-runtime-hours', type=float,
        help='queue-enforced maximum runtime; required with --delete')
    p_hygiene.add_argument(
        '--safety-margin-hours', type=float, default=24,
        help='age margin beyond maximum runtime (default: 24 hours)')
    p_hygiene.add_argument(
        '--max-delete', type=int, default=100,
        help='maximum top-level workdirs deleted per run (default: 100)')
    p_hygiene.add_argument(
        '--protect', action='append', default=[], metavar='PATH',
        help='additional active workdir path to protect; repeatable')
    p_hygiene.add_argument(
        '--report-limit', type=int, default=100,
        help='maximum per-workdir records in JSON (default: 100)')
    p_hygiene.add_argument(
        '--output', '-o', help='write JSON report to a payload output file')
    p_hygiene.set_defaults(func=cmd_workdir_hygiene)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    # The hygiene payload is deliberately standard-library-only so staged
    # source can run on a worker without installing the controller stack.
    if args.command == 'workdir-hygiene':
        return args.func(args)
    from .log import setup_logging
    setup_logging()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
