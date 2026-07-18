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
    # Nullable until the queue-to-site mapping arrives from PanDA queue
    # configuration, the naming authority (docs/SWF_INTEGRATION.md).
    site = models.ForeignKey(Site, on_delete=models.PROTECT,
                             null=True, blank=True,
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


class PassiveSample(models.Model):
    """Per-queue health metrics for one window of accounting data.

    The core columns are the queue-responsiveness instrument
    (swf-epicprod PANDA_USER_JOBS.md): job count, creation-to-start
    wait median and 90th percentile, failure rate. Further measures
    ride in `metrics`. A low-statistics window keeps its row with null
    percentiles — quiet queues are probe targets, not gaps.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue = models.ForeignKey(Queue, on_delete=models.PROTECT,
                              related_name='passive_samples')
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    njobs = models.PositiveIntegerField(default=0)
    wait_median_s = models.FloatField(null=True, blank=True)
    wait_p90_s = models.FloatField(null=True, blank=True)
    failure_rate = models.FloatField(null=True, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'canary_passive_sample'
        ordering = ['-window_end']
        indexes = [
            models.Index(fields=['queue', '-window_end'],
                         name='canary_sample_queue_time_idx'),
        ]

    def __str__(self):
        return f'{self.queue.name}@{self.window_end.isoformat()}'


class Verdict(models.Model):
    """One policy evaluation of one queue: reproducible and recheckable.

    The evidence carries the sample identity, the values judged, and
    the rule that fired, so the reason for any verdict can be stated
    exactly (design principle 7: rules decide, AI advises).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue = models.ForeignKey(Queue, on_delete=models.PROTECT,
                              related_name='verdicts')
    verdict = models.CharField(max_length=16)
    policy_name = models.CharField(max_length=64)
    policy_version = models.CharField(max_length=32)
    evidence = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'canary_verdict'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['queue', '-created_at'],
                         name='canary_verdict_queue_time_idx'),
        ]

    def __str__(self):
        return f'{self.queue.name}:{self.verdict}'


class StatusChange(models.Model):
    """Status history with provenance: every queue status transition."""

    class Actor(models.TextChoices):
        POLICY = 'policy', 'Policy'
        MANUAL = 'manual', 'Manual'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue = models.ForeignKey(Queue, on_delete=models.PROTECT,
                              related_name='status_changes')
    old_status = models.CharField(max_length=16)
    new_status = models.CharField(max_length=16)
    actor = models.CharField(max_length=16, choices=Actor.choices)
    verdict = models.ForeignKey(Verdict, on_delete=models.SET_NULL,
                                null=True, blank=True,
                                related_name='status_changes')
    reason = models.TextField(blank=True, default='')
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'canary_status_change'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['queue', '-created_at'],
                         name='canary_change_queue_time_idx'),
        ]

    def __str__(self):
        return (f'{self.queue.name}:{self.old_status}->{self.new_status}'
                f' ({self.actor})')


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
