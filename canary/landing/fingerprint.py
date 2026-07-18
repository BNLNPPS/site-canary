"""Environment fingerprint: bounded facts about the execution environment.

The fingerprint hash covers environment-discriminating fields only.
Hostname is recorded as metadata outside the hash: identity is
horizontal (distinguish environments within the current map), not
longitudinal (track hosts over time) — see docs/DESIGN.md.

Every collector is bounded: external probes run in subprocesses with
timeouts, and a collector failure is recorded in the fingerprint as an
error value, never raised past it and never silently dropped.
"""
import hashlib
import json
import logging
import os
import platform
import shutil
import socket
import subprocess

from ..config import CVMFS_REPOS

logger = logging.getLogger('canary.landing.fingerprint')

PROBE_TIMEOUT = 5  # seconds; bounds every external probe

# Hash scope: the environment-discriminating fields.
HASH_FIELDS = ('os', 'kernel', 'arch', 'cpu_model', 'cpu_cores', 'mem_gb',
               'container', 'cvmfs', 'gpu', 'glibc', 'python')


def _os_release():
    try:
        with open('/etc/os-release', encoding='utf-8') as f:
            for line in f:
                if line.startswith('PRETTY_NAME='):
                    return line.split('=', 1)[1].strip().strip('"')
    except OSError as e:
        logger.error("os-release read failed: %s", e)
        return f'error: {e}'
    return 'unknown'


def _cpu_model():
    try:
        with open('/proc/cpuinfo', encoding='utf-8') as f:
            for line in f:
                if line.startswith('model name'):
                    return line.split(':', 1)[1].strip()
    except OSError as e:
        logger.error("cpuinfo read failed: %s", e)
        return f'error: {e}'
    return 'unknown'


def _mem_gb():
    try:
        with open('/proc/meminfo', encoding='utf-8') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    return round(int(line.split()[1]) / 1024 / 1024)
    except (OSError, ValueError) as e:
        logger.error("meminfo read failed: %s", e)
    return None


def _container():
    """Detect containment and available container runtimes."""
    inside = None
    if os.environ.get('APPTAINER_CONTAINER') or os.environ.get('SINGULARITY_CONTAINER'):
        inside = 'apptainer'
    elif os.path.exists('/run/.containerenv'):
        inside = 'podman'
    elif os.path.exists('/.dockerenv'):
        inside = 'docker'
    runtimes = [r for r in ('apptainer', 'singularity', 'podman', 'docker')
                if shutil.which(r)]
    return {'inside': inside, 'runtimes': runtimes}


def _cvmfs():
    """Probe configured CVMFS repos: reachability and revision.

    Each probe is a subprocess with a timeout — a stat on a broken CVMFS
    mount can hang indefinitely, and the fingerprint must stay bounded.
    """
    repos = {}
    for repo in CVMFS_REPOS:
        path = f'/cvmfs/{repo}'
        try:
            result = subprocess.run(
                ['python3', '-c',
                 'import os,sys\n'
                 f'os.stat({path!r})\n'
                 'try:\n'
                 f'    print(os.getxattr({path!r}, "user.revision").decode())\n'
                 'except OSError:\n'
                 '    print("")'],
                capture_output=True, timeout=PROBE_TIMEOUT, encoding='utf-8')
            if result.returncode == 0:
                revision = result.stdout.strip()
                repos[repo] = {'reachable': True,
                               'revision': revision or None}
            else:
                repos[repo] = {'reachable': False}
        except subprocess.TimeoutExpired:
            logger.error("cvmfs probe timed out: %s", path)
            repos[repo] = {'reachable': False, 'error': 'timeout'}
        except OSError as e:
            logger.error("cvmfs probe failed: %s: %s", path, e)
            repos[repo] = {'reachable': False, 'error': str(e)}
    return repos


def _gpu():
    if not shutil.which('nvidia-smi'):
        return {'present': False}
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,driver_version,memory.total',
             '--format=csv,noheader'],
            capture_output=True, timeout=PROBE_TIMEOUT, encoding='utf-8')
        if result.returncode != 0:
            logger.error("nvidia-smi rc=%s: %s", result.returncode,
                         result.stderr.strip()[:200])
            return {'present': True, 'error': result.stderr.strip()[:200]}
        gpus = [g.strip() for g in result.stdout.strip().splitlines()]
        return {'present': True, 'gpus': gpus}
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.error("nvidia-smi failed: %s", e)
        return {'present': True, 'error': str(e)}


def collect():
    """Collect the fingerprint. Returns a dict including its own hash."""
    fp = {
        'os': _os_release(),
        'kernel': platform.release(),
        'arch': platform.machine(),
        'cpu_model': _cpu_model(),
        'cpu_cores': os.cpu_count(),
        'mem_gb': _mem_gb(),
        'container': _container(),
        'cvmfs': _cvmfs(),
        'gpu': _gpu(),
        'glibc': '-'.join(platform.libc_ver()),
        'python': platform.python_version(),
        # Metadata outside the hash:
        'hostname': socket.gethostname(),
    }
    hashed = {k: fp[k] for k in HASH_FIELDS}
    canonical = json.dumps(hashed, sort_keys=True, separators=(',', ':'))
    fp['fingerprint'] = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return fp
