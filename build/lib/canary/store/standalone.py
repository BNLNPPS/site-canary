"""Standalone Django setup around canary.store, from CANARY_DB_* config.

Used by the storectl harness and by CLI paths that write to the store
outside a hosting Django project. Hosted deployments never call this —
the host project owns settings (docs/SWF_INTEGRATION.md).
"""


def setup_django():
    import django
    from django.conf import settings

    from ..config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER

    if settings.configured:
        return
    settings.configure(
        INSTALLED_APPS=['canary.store'],
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
    django.setup()
