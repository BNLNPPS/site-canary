"""Inspect and, when explicitly enabled, remove orphaned PanDA workdirs.

This module is a payload: it is intended to run as an ordinary PanDA job at
the target queue.  It deliberately has no elevated access.  Scan-only is the
default; deletion is guarded by work-root, naming, ownership, filesystem,
age, runtime-envelope, current-workdir, and concurrency checks.
"""
import fcntl
import os
import re
import shutil
import socket
import stat
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__


class HygieneError(ValueError):
    """The hygiene job configuration is unsafe or cannot be evaluated."""


def _utc_now():
    return datetime.now(timezone.utc)


def _filesystem_stats(path):
    try:
        values = os.statvfs(path)
        device = os.stat(path).st_dev
    except OSError as exc:
        raise HygieneError(f'cannot measure filesystem at {path}: {exc}')
    block_size = values.f_frsize or values.f_bsize
    total_bytes = values.f_blocks * block_size
    free_bytes = values.f_bfree * block_size
    available_bytes = values.f_bavail * block_size
    total_inodes = values.f_files
    free_inodes = values.f_ffree
    available_inodes = values.f_favail
    used_inodes = (total_inodes - free_inodes) if total_inodes else None
    inode_used_percent = (
        round(100.0 * used_inodes / total_inodes, 3)
        if total_inodes and used_inodes is not None else None
    )
    return {
        'device': device,
        'total_bytes': total_bytes,
        'free_bytes': free_bytes,
        'available_bytes': available_bytes,
        'total_inodes': total_inodes,
        'free_inodes': free_inodes,
        'available_inodes': available_inodes,
        'used_inodes': used_inodes,
        'inode_used_percent': inode_used_percent,
    }


def _resolve_root(root, root_parent_of_cwd, current_workdir):
    current = Path(current_workdir or os.getcwd()).resolve()
    if bool(root) == bool(root_parent_of_cwd):
        raise HygieneError('select exactly one of root or root_parent_of_cwd')
    selected = current.parent if root_parent_of_cwd else Path(root).resolve()
    if selected == Path(selected.anchor):
        raise HygieneError('filesystem root is never a valid work root')
    if not selected.is_dir():
        raise HygieneError(f'work root is not a directory: {selected}')
    return selected, current


def _protected_top_level_names(root, current, protected_paths):
    protected = set()
    for path in (current, *(Path(p).resolve() for p in protected_paths)):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if relative.parts:
            protected.add(relative.parts[0])
    return protected


def _tree_stays_on_device(path, device):
    """Return an explanation if traversal would cross a filesystem."""
    try:
        for dirpath, dirnames, _filenames in os.walk(
                path, topdown=True, followlinks=False):
            directory_stat = os.lstat(dirpath)
            if directory_stat.st_dev != device:
                return f'filesystem boundary at {dirpath}'
            # Symlinked directories are unlinked by rmtree, not traversed.
            for name in list(dirnames):
                child = os.path.join(dirpath, name)
                child_stat = os.lstat(child)
                if stat.S_ISLNK(child_stat.st_mode):
                    dirnames.remove(name)
                elif child_stat.st_dev != device:
                    return f'filesystem boundary at {child}'
    except OSError as exc:
        return f'tree verification failed: {exc}'
    return None


@contextmanager
def _cleanup_lock(root):
    """Serialize cleanup payloads on the shared work root."""
    lock_path = root / '.canary-workdir-hygiene.lock'
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise HygieneError(f'cannot open cleanup lock {lock_path}: {exc}')
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise HygieneError(f'another cleanup job holds {lock_path}: {exc}')
        yield
    finally:
        os.close(descriptor)


def _candidate_snapshot(root, name_pattern, min_age_hours, now,
                        protected_names):
    root_stat = os.lstat(root)
    candidates = []
    counts = {
        'entries_seen': 0,
        'name_matches': 0,
        'eligible': 0,
        'protected': 0,
        'too_young': 0,
        'owner_mismatch': 0,
        'not_directory': 0,
        'symlink': 0,
        'other_filesystem': 0,
        'stat_error': 0,
    }
    effective_uid = os.geteuid()
    try:
        entries = os.scandir(root)
    except OSError as exc:
        raise HygieneError(f'cannot scan work root {root}: {exc}')
    with entries:
        for entry in entries:
            counts['entries_seen'] += 1
            if not name_pattern.fullmatch(entry.name):
                continue
            counts['name_matches'] += 1
            item = {'name': entry.name, 'eligible': False, 'action': 'skipped'}
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                item['reason'] = f'stat error: {exc}'
                counts['stat_error'] += 1
                candidates.append(item)
                continue
            age_hours = max(
                0.0, now.timestamp() - entry_stat.st_mtime) / 3600
            item.update({
                'age_hours': round(age_hours, 3),
                'modified_at': datetime.fromtimestamp(
                    entry_stat.st_mtime, timezone.utc).isoformat(),
                'uid': entry_stat.st_uid,
            })
            if stat.S_ISLNK(entry_stat.st_mode):
                item['reason'] = 'symlink'
                counts['symlink'] += 1
            elif not stat.S_ISDIR(entry_stat.st_mode):
                item['reason'] = 'not a directory'
                counts['not_directory'] += 1
            elif entry.name in protected_names:
                item['reason'] = 'current or explicitly protected workdir'
                counts['protected'] += 1
            elif entry_stat.st_uid != effective_uid:
                item['reason'] = 'not owned by this payload identity'
                counts['owner_mismatch'] += 1
            elif entry_stat.st_dev != root_stat.st_dev:
                item['reason'] = 'different filesystem from work root'
                counts['other_filesystem'] += 1
            elif age_hours < min_age_hours:
                item['reason'] = 'younger than minimum age'
                counts['too_young'] += 1
            else:
                item['eligible'] = True
                item['action'] = 'would_delete'
                item['_identity'] = {
                    'device': entry_stat.st_dev,
                    'inode': entry_stat.st_ino,
                    'mtime_ns': entry_stat.st_mtime_ns,
                }
                counts['eligible'] += 1
            candidates.append(item)
    return candidates, counts, root_stat.st_dev


def _delete_candidate(root, item, device):
    path = root / item['name']
    try:
        current_stat = os.lstat(path)
    except OSError as exc:
        return f'revalidation stat failed: {exc}'
    identity = item['_identity']
    if (current_stat.st_dev != identity['device'] or
            current_stat.st_ino != identity['inode'] or
            current_stat.st_mtime_ns != identity['mtime_ns']):
        return 'changed after scan'
    boundary_error = _tree_stays_on_device(path, device)
    if boundary_error:
        return boundary_error
    try:
        shutil.rmtree(path)
    except OSError as exc:
        return f'deletion failed: {exc}'
    return None


def workdir_hygiene_report(*, root=None, root_parent_of_cwd=False,
                           current_workdir=None,
                           name_regex=r'eic_[A-Za-z0-9]+',
                           min_age_hours=168.0,
                           delete=False, max_job_runtime_hours=None,
                           safety_margin_hours=24.0, max_delete=100,
                           protected_paths=(), report_limit=100, now=None):
    """Build a bounded hygiene report and optionally remove safe candidates.

    Deletion requires the payload to be running below the selected work root
    and a declared queue maximum runtime.  The minimum candidate age must be
    at least that runtime plus the safety margin.
    """
    started = time.monotonic()
    root, current = _resolve_root(root, root_parent_of_cwd, current_workdir)
    now = now or _utc_now()
    if min_age_hours <= 0:
        raise HygieneError('min_age_hours must be positive')
    if safety_margin_hours < 0:
        raise HygieneError('safety_margin_hours cannot be negative')
    if max_delete <= 0:
        raise HygieneError('max_delete must be positive')
    if report_limit <= 0:
        raise HygieneError('report_limit must be positive')
    try:
        pattern = re.compile(name_regex)
    except re.error as exc:
        raise HygieneError(f'invalid workdir name regex: {exc}')
    if pattern.fullmatch(''):
        raise HygieneError('workdir name regex cannot match an empty name')

    if delete:
        try:
            current_relative = current.relative_to(root)
        except ValueError:
            raise HygieneError(
                'cleanup payload must run from inside the selected work root')
        if not current_relative.parts:
            raise HygieneError(
                'cleanup payload must run in a workdir below the work root')
        if max_job_runtime_hours is None or max_job_runtime_hours <= 0:
            raise HygieneError(
                'delete mode requires a positive max_job_runtime_hours')
        safe_age = max_job_runtime_hours + safety_margin_hours
        if min_age_hours < safe_age:
            raise HygieneError(
                f'min_age_hours must be at least runtime plus margin '
                f'({safe_age:g} hours)')
        if not os.access(root, os.W_OK | os.X_OK):
            raise HygieneError(f'work root is not writable: {root}')
        if report_limit < max_delete:
            raise HygieneError(
                'report_limit must cover every possible deletion '
                '(report_limit >= max_delete)')

    protected_names = _protected_top_level_names(
        root, current, protected_paths)
    before = _filesystem_stats(root)
    deleted = 0
    delete_errors = 0
    lock = _cleanup_lock(root) if delete else nullcontext()
    with lock:
        candidates, counts, device = _candidate_snapshot(
            root, pattern, min_age_hours, now, protected_names)
        eligible = sorted(
            (item for item in candidates if item['eligible']),
            key=lambda item: item['age_hours'], reverse=True)
        if delete:
            for item in eligible[:max_delete]:
                error = _delete_candidate(root, item, device)
                if error:
                    item['action'] = 'delete_error'
                    item['reason'] = error
                    delete_errors += 1
                else:
                    item['action'] = 'deleted'
                    deleted += 1
            for item in eligible[max_delete:]:
                item['action'] = 'delete_limit'
                item['reason'] = 'maximum directories per run reached'
    after = _filesystem_stats(root)

    # Private revalidation data never enters the external report.
    for item in candidates:
        item.pop('_identity', None)
    action_priority = {
        'delete_error': 0, 'deleted': 1, 'delete_limit': 2,
        'would_delete': 3, 'skipped': 4,
    }
    reported = sorted(
        candidates,
        key=lambda item: (action_priority.get(item['action'], 9),
                          -item.get('age_hours', 0), item['name'])
    )[:report_limit]
    report = {
        'schema': 'canary-workdir-hygiene-report/0',
        'canary_version': __version__,
        'generated_at': now.isoformat(),
        'mode': 'delete' if delete else 'scan',
        'status': 'error' if delete_errors else 'ok',
        'work_root': str(root),
        'current_workdir': str(current),
        'execution': {
            'hostname': socket.gethostname(),
            'uid': os.geteuid(),
            'gid': os.getegid(),
            'panda_environment': {
                key: os.environ[key]
                for key in ('PandaID', 'PanDA_TaskID', 'PANDA_SITE_NAME',
                            'PANDA_QUEUE', 'computingSite')
                if key in os.environ
            },
        },
        'policy': {
            'name_regex': name_regex,
            'min_age_hours': min_age_hours,
            'max_job_runtime_hours': max_job_runtime_hours,
            'safety_margin_hours': safety_margin_hours,
            'max_delete': max_delete,
            'same_owner_required': True,
            'single_filesystem_required': True,
        },
        'filesystem_before': before,
        'filesystem_after': after,
        'observed_change': {
            'free_bytes': after['free_bytes'] - before['free_bytes'],
            'free_inodes': (
                after['free_inodes'] - before['free_inodes']
                if (after['free_inodes'] is not None and
                    before['free_inodes'] is not None) else None),
        },
        'scan': counts,
        'actions': {
            'deleted': deleted,
            'delete_errors': delete_errors,
            'eligible_not_deleted': counts['eligible'] - deleted,
        },
        'workdirs': reported,
        'workdirs_truncated': len(candidates) > report_limit,
        'workdirs_total': len(candidates),
        'duration_seconds': round(time.monotonic() - started, 3),
    }
    return report
