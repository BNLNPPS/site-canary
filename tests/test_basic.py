#!/usr/bin/env python3
"""Functionality tests for the canary package scaffold.

Plain python, no test framework: each test_* function raises on failure;
the runner reports PASS/FAIL per test and exits nonzero on any failure.
"""
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _run_cli(*args):
    env = {**os.environ, 'PYTHONPATH': REPO_ROOT}
    return subprocess.run(
        [sys.executable, '-m', 'canary', *args],
        capture_output=True, encoding='utf-8', timeout=30, env=env)


def test_import():
    import canary
    assert canary.__version__


def test_config_default():
    from canary import config
    assert config.LOG_LEVEL


def test_logging_setup():
    from canary.log import setup_logging
    logger = setup_logging('canary.test')
    assert logger.name == 'canary.test'


def test_cli_help():
    result = _run_cli('--help')
    assert result.returncode == 0, result.stderr
    assert 'canary' in result.stdout


def test_cli_version():
    result = _run_cli('--version')
    assert result.returncode == 0, result.stderr
    assert '0.1.0' in result.stdout


def test_cli_rejects_unknown():
    result = _run_cli('nosuchcommand')
    assert result.returncode != 0


def test_store_check():
    try:
        import django  # noqa: F401
    except ImportError:
        print('  (django not installed; store check skipped)')
        return
    result = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, 'scripts', 'storectl.py'),
         'check'],
        capture_output=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr


def test_landing_fingerprint():
    result = _run_cli('landing', '--no-payload')
    assert result.returncode == 0, result.stderr
    import json
    report = json.loads(result.stdout)
    fp = report['fingerprint']
    for key in ('os', 'kernel', 'cpu_model', 'cpu_cores', 'mem_gb',
                'fingerprint'):
        assert key in fp, key


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith('test_') and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f'PASS {test.__name__}')
        except Exception as e:
            failures += 1
            print(f'FAIL {test.__name__}: {e!r}')
    print(f'{len(tests) - failures}/{len(tests)} passed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
