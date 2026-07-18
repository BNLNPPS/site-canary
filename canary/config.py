"""Configuration via python-decouple, env prefix CANARY_.

All runtime configuration comes from environment variables or a .env
file. Settings are read here at import time so a missing or malformed
value fails at process start, not mid-run.
"""
from decouple import config

LOG_LEVEL = config('CANARY_LOG_LEVEL', default='INFO')
