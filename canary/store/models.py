"""Persistent state for site-canary: the map spine.

Sites, queues, node environments, and landing reports. Tables for
passive samples, verdicts, status history, and capability checks are
added by the increments that produce their data (PLAN.md), so schemas
follow measured output.
"""

import uuid

from django.db import models


class Health(models.TextChoices):
    UNKNOWN = 'unknown', 'Unknown'
    HEALTHY = 'healthy', 'Healthy'
    SUSPECT = 'suspect', 'Suspect'
    EXCLUDED = 'excluded', 'Excluded'
    RECOVERING = 'recovering', 'Recovering'


class Site(models.Model):
    """A processing site: the operational unit and the map's site level."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=16, choices=Health.choices,
                              default=Health.UNKNOWN)
    map = models.JSONField(default=dict)
    first_landing_at = models.DateTimeField(null=True, blank=True)
    last_landing_at = models.DateTimeField(null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'canary_site'
        ordering = ['name']

    def __str__(self):
        return f'{self.name}:{self.status}'


class Queue(models.Model):
    """A PanDA queue served by a site."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    site = models.ForeignKey(Site, on_delete=models.PROTECT,
                             related_name='queues')
    status = models.CharField(max_length=16, choices=Health.choices,
                              default=Health.UNKNOWN)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'canary_queue'
        ordering = ['name']

    def __str__(self):
        return f'{self.name}:{self.status}'


class NodeEnvironment(models.Model):
    """The map's node level: one distinct execution environment at a site.

    Identity is horizontal — the fingerprint distinguishes environments
    within the current map; individual hosts are not tracked over time
    (docs/DESIGN.md). The census a site's rows provide: how many
    distinct platforms, GPU environments, memory classes serve it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT,
                             related_name='node_environments')
    fingerprint = models.CharField(max_length=64)
    environment = models.JSONField(default=dict)
    topology = models.JSONField(default=dict, blank=True)
    landing_count = models.PositiveIntegerField(default=0)
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'canary_node_environment'
        constraints = [
            models.UniqueConstraint(
                fields=['site', 'fingerprint'],
                name='canary_nodeenv_site_fp_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['site', '-last_seen_at'],
                         name='canary_nodeenv_site_seen_idx'),
        ]

    def __str__(self):
        return f'{self.site.name}:{self.fingerprint}'


class LandingReport(models.Model):
    """One landing report as delivered: the map's evidence stream."""

    class Source(models.TextChoices):
        PROBE = 'probe', 'Probe'
        RIDER = 'rider', 'Rider'
        MANUAL = 'manual', 'Manual'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT,
                             related_name='landing_reports')
    queue = models.ForeignKey(Queue, on_delete=models.SET_NULL,
                              null=True, blank=True,
                              related_name='landing_reports')
    node_environment = models.ForeignKey(NodeEnvironment,
                                         on_delete=models.PROTECT,
                                         related_name='landing_reports')
    source = models.CharField(max_length=16, choices=Source.choices,
                              default=Source.MANUAL)
    report = models.JSONField(default=dict)
    landed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'canary_landing_report'
        ordering = ['-landed_at']
        indexes = [
            models.Index(fields=['site', '-landed_at'],
                         name='canary_landing_site_time_idx'),
        ]

    def __str__(self):
        return f'{self.site.name}@{self.landed_at.isoformat()}'
