# Generated by Django 2.1.5 on 2020-03-21 05:45

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tab', '0015_auto_20200310_2130'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='room',
            name='text_channel_id',
        ),
        migrations.RemoveField(
            model_name='room',
            name='voice_channel_id',
        ),
    ]