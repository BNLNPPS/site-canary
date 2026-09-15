"""Configuration via python-decouple, env prefix CANARY_.

All runtime configuration comes from environment variables or a .env
file. Settings are read here at import time so a missing or malformed
value fails at process start, not mid-run.
"""
from decouple import config, Csv

LOG_LEVEL = config('CANARY_LOG_LEVEL', default='INFO')

# Path to the prmon binary; empty means search PATH, then repo .prmon/.
PRMON_PATH = config('CANARY_PRMON', default='')

# CVMFS repos the fingerprint probes for reachability and revision.
CVMFS_REPOS = config('CANARY_CVMFS_REPOS', cast=Csv(),
                     default='eic.opensciencegrid.org,'
                             'singularity.opensciencegrid.org')

# Policy file path; empty means the packaged ePIC policy.
POLICY_PATH = config('CANARY_POLICY', default='')

# The declared-downtime provider, ``module:function``, called as
# ``provider(queue_names, now)`` and answering, per queue, what is
# declared for it (canary/declared.py). The default reads the platform's
# declared record over HTTP at CANARY_DECLARED_URL, which defaults to
# SWF_MONITOR_URL + /api/declared/; with neither set, nothing reads as
# declared.
DECLARED_PROVIDER = config('CANARY_DECLARED_PROVIDER',
                           default='canary.declared:http_provider')
DECLARED_URL = config('CANARY_DECLARED_URL', default='')

# PanDA accounting database DSN for the passive assessor's live source,
# set only where that database is reachable (the platform host). Empty
# means the assessor requires a snapshot source.
PANDA_DSN = config('CANARY_PANDA_DSN', default='')

# Store database, used only by the standalone harness (scripts/storectl.py).
# In a hosted deployment the Django host project owns database settings.
DB_NAME = config('CANARY_DB_NAME', default='canary_dev')
DB_USER = config('CANARY_DB_USER', default='')
DB_PASSWORD = config('CANARY_DB_PASSWORD', default='')
DB_HOST = config('CANARY_DB_HOST', default='')
DB_PORT = config('CANARY_DB_PORT', default='5432')
