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
    INSUFFICIENT = 'insufficient', 'Insufficient'
    HEALTHY = 'healthy', 'Healthy'
    DEGRADED = 'degraded', 'Degraded'
    FAILING = 'failing', 'Failing'


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


class ProbeRun(models.Model):
    """One dispatched probe task against one queue — the record behind
    the Canary probes page: schedule state, the per-queue run history,
    and the collection ladder's pickup list."""

    class Status(models.TextChoices):
        SUBMITTED = 'submitted'
        FAILED_SUBMIT = 'failed_submit'
        FINISHED = 'finished'
        FAILED = 'failed'
        COLLECTED = 'collected'

    class Trigger(models.TextChoices):
        AUTO = 'auto'
        MANUAL = 'manual'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4,
                          editable=False)
    queue = models.ForeignKey(Queue, on_delete=models.PROTECT,
                              related_name='probe_runs')
    jeditaskid = models.BigIntegerField(null=True, blank=True)
    trigger = models.CharField(max_length=16, choices=Trigger.choices,
                               default=Trigger.AUTO)
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.SUBMITTED)
    submitted_at = models.DateTimeField()
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'canary_probe_run'
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['queue', '-submitted_at'],
                         name='canary_probe_queue_time_idx'),
        ]

    def __str__(self):
        return f'{self.queue.name} probe {self.jeditaskid or "unsubmitted"}'


class NodeMeasurement(models.Model):
    """Measured capability of one node environment under one workload:
    running distributions of the payload's per-stage measures over the
    jobs that ran there (docs/MEASUREMENTS.md).

    The environment is the PanDA queue and the processor description every
    job record carries, and the node environment when a fingerprint was
    carried; the workload is the container image, detector version,
    physics configuration, payload version and stage. One row per key,
    however many jobs it summarizes: ``metrics`` holds per measure the
    count, running mean and variance, minimum and maximum.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue = models.ForeignKey(Queue, on_delete=models.PROTECT,
                              related_name='measurements')
    processor = models.CharField(max_length=200, default='unknown')
    node_environment = models.ForeignKey(NodeEnvironment,
                                         on_delete=models.SET_NULL,
                                         null=True, blank=True,
                                         related_name='measurements')
    container_image = models.CharField(max_length=300, blank=True, default='')
    detector_version = models.CharField(max_length=50, blank=True, default='')
    physics_config = models.CharField(max_length=32, blank=True, default='')
    payload_version = models.CharField(max_length=32, blank=True, default='')
    stage = models.CharField(max_length=64)
    metrics = models.JSONField(default=dict)
    jobs = models.PositiveIntegerField(default=0)
    first_job_at = models.DateTimeField(null=True, blank=True)
    last_job_at = models.DateTimeField(null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'canary_node_measurement'
        ordering = ['queue', 'processor', 'stage']
        constraints = [
            models.UniqueConstraint(
                fields=['queue', 'processor', 'container_image',
                        'detector_version', 'physics_config',
                        'payload_version', 'stage'],
                name='canary_measure_key_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['queue', 'processor'],
                         name='canary_measure_queue_proc_idx'),
        ]

    def __str__(self):
        return f'{self.queue.name}:{self.processor}:{self.stage} ({self.jobs} jobs)'


class NodeState(models.Model):
    """The node guard's record of one node (docs/NODE_GUARD.md, The
    record): the queue pattern at node level. A node is the batch host
    within its queue, the identity a submit description excludes; when a
    landing has fingerprinted the host the record links to that
    NodeEnvironment.

    Status: ``clear``; ``black_hole``, tripped by the guard, latched until
    it expires; ``half_open``, expired, one job may land and its outcome
    decides; ``pinned``, a person's status that the guard never changes.
    ``opened_at`` and ``expires_at`` bound the current black hole;
    ``evidence`` is the verdict that set the status, refreshed while it
    stands; ``last_verdict`` the guard's latest reading of the node,
    whatever the status.
    """

    class Status(models.TextChoices):
        CLEAR = 'clear', 'Clear'
        BLACK_HOLE = 'black_hole', 'Black hole'
        HALF_OPEN = 'half_open', 'Half open'
        PINNED = 'pinned', 'Pinned'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue = models.ForeignKey(Queue, on_delete=models.PROTECT,
                              related_name='node_states')
    host = models.CharField(max_length=200)
    site = models.CharField(max_length=100, blank=True, default='')
    node_environment = models.ForeignKey(NodeEnvironment, on_delete=models.SET_NULL,
                                         null=True, blank=True,
                                         related_name='node_states')
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.CLEAR)
    reason = models.CharField(max_length=64, blank=True, default='')
    evidence = models.JSONField(default=dict, blank=True)
    last_verdict = models.JSONField(default=dict, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    reopened = models.PositiveIntegerField(default=0)
    trips = models.PositiveIntegerField(default=0)
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'canary_node_state'
        ordering = ['queue', 'host']
        constraints = [
            models.UniqueConstraint(fields=('queue', 'host'),
                                    name='canary_node_state_key_uniq'),
        ]
        indexes = [
            models.Index(fields=['status', '-modified_at'],
                         name='canary_node_state_status_idx'),
        ]

    def __str__(self):
        return f'{self.queue.name}/{self.host}:{self.status}'


class NodeStateChange(models.Model):
    """Status history with provenance: every node status transition, by
    the guard or a person, with the reason and the evidence at the time."""

    class Actor(models.TextChoices):
        GUARD = 'guard', 'Guard'
        MANUAL = 'manual', 'Manual'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node = models.ForeignKey(NodeState, on_delete=models.CASCADE,
                             related_name='changes')
    old_status = models.CharField(max_length=16)
    new_status = models.CharField(max_length=16)
    actor = models.CharField(max_length=16, choices=Actor.choices)
    username = models.CharField(max_length=100, blank=True, default='')
    reason = models.TextField(blank=True, default='')
    evidence = models.JSONField(default=dict, blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'canary_node_state_change'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['node', '-created_at'],
                         name='canary_node_change_time_idx'),
        ]

    def __str__(self):
        return f'{self.node}:{self.old_status}->{self.new_status} ({self.actor})'
