# Generated by Django 2.1.5 on 2019-10-16 13:33

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tab', '0013_merge_20191015_1156'),
    ]

    operations = [
        migrations.AddField(
            model_name='team',
            name='break_preference',
            field=models.IntegerField(
                choices=[(0, 'Varsity'), (1, 'Novice')], default=0),
        ),
    ]
