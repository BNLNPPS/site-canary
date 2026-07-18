from django.apps import AppConfig


class CanaryStoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'canary.store'
    label = 'canary'
    verbose_name = 'site-canary'
