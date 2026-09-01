from django.db import migrations, models


def update_status_values(apps, schema_editor):
    Site = apps.get_model('canary', 'Site')
    Queue = apps.get_model('canary', 'Queue')
    for model in (Site, Queue):
        model.objects.filter(status='suspect').update(status='degraded')
        model.objects.filter(status='excluded').update(status='failing')
        model.objects.filter(status='recovering').update(status='unknown')


def restore_status_values(apps, schema_editor):
    Site = apps.get_model('canary', 'Site')
    Queue = apps.get_model('canary', 'Queue')
    for model in (Site, Queue):
        model.objects.filter(status='degraded').update(status='suspect')
        model.objects.filter(status='failing').update(status='excluded')
        model.objects.filter(status='insufficient').update(status='unknown')


class Migration(migrations.Migration):

    dependencies = [
        ('canary', '0003_verdict_statuschange_and_more'),
    ]

    operations = [
        migrations.RunPython(
            update_status_values,
            restore_status_values,
        ),
        migrations.AlterField(
            model_name='queue',
            name='status',
            field=models.CharField(
                choices=[
                    ('unknown', 'Unknown'),
                    ('insufficient', 'Insufficient'),
                    ('healthy', 'Healthy'),
                    ('degraded', 'Degraded'),
                    ('failing', 'Failing'),
                ],
                default='unknown',
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name='site',
            name='status',
            field=models.CharField(
                choices=[
                    ('unknown', 'Unknown'),
                    ('insufficient', 'Insufficient'),
                    ('healthy', 'Healthy'),
                    ('degraded', 'Degraded'),
                    ('failing', 'Failing'),
                ],
                default='unknown',
                max_length=16,
            ),
        ),
    ]
