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

# Store database, used only by the standalone harness (scripts/storectl.py).
# In a hosted deployment the Django host project owns database settings.
DB_NAME = config('CANARY_DB_NAME', default='canary_dev')
DB_USER = config('CANARY_DB_USER', default='')
DB_PASSWORD = config('CANARY_DB_PASSWORD', default='')
DB_HOST = config('CANARY_DB_HOST', default='')
DB_PORT = config('CANARY_DB_PORT', default='5432')
