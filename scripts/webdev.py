#!/usr/bin/env python3
"""Development web harness for the Canary page, outside the platform.

Configures Django with canary.store plus the devweb stand-ins (stub
base template and swf_fmt filters) against the CANARY_DB_* database,
then dispatches Django management commands.

Usage:
  webdev.py check
  webdev.py runserver [addr:port]
"""
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, SCRIPTS_DIR)


def main(argv):
    from django.conf import settings

    from canary.config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER

    settings.configure(
        DEBUG=True,
        SECRET_KEY='canary-dev-only',
        ALLOWED_HOSTS=['*'],
        INSTALLED_APPS=['canary.store', 'devweb'],
        ROOT_URLCONF='canary.store.urls',
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [os.path.join(SCRIPTS_DIR, 'devweb', 'templates')],
            'APP_DIRS': True,
            'OPTIONS': {'context_processors': []},
        }],
        DATABASES={'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': DB_NAME,
            'USER': DB_USER,
            'PASSWORD': DB_PASSWORD,
            'HOST': DB_HOST,
            'PORT': DB_PORT,
        }},
        USE_TZ=True,
        TIME_ZONE='UTC',
        DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
    )
    from django.core.management import execute_from_command_line
    execute_from_command_line(['webdev.py'] + (argv or ['check']))


if __name__ == '__main__':
    main(sys.argv[1:])
