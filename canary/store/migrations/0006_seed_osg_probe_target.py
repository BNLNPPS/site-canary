# Seed the first probe target: the OSG pool queue, daily interval.
# The dispatch decision submits its first probe on the next tick, so
# the automatic apparatus is in use from deployment.
from django.db import migrations


def seed(apps, schema_editor):
    Queue = apps.get_model('canary', 'Queue')
    queue, _ = Queue.objects.get_or_create(name='BNL_OSG_EPIC_PROD_1')
    data = dict(queue.data or {})
    block = dict(data.get('probe') or {})
    block.setdefault('enabled', True)
    block.setdefault('interval_hours', 24.0)
    data['probe'] = block
    queue.data = data
    queue.save(update_fields=['data'])


def unseed(apps, schema_editor):
    Queue = apps.get_model('canary', 'Queue')
    queue = Queue.objects.filter(name='BNL_OSG_EPIC_PROD_1').first()
    if queue is None:
        return
    data = dict(queue.data or {})
    data.pop('probe', None)
    queue.data = data
    queue.save(update_fields=['data'])


class Migration(migrations.Migration):

    dependencies = [
        ('canary', '0005_proberun'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
