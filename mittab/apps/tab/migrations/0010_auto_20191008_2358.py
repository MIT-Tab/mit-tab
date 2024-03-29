# Generated by Django 2.1.5 on 2019-10-08 23:58

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tab', '0009_auto_20190818_1756'),
    ]

    operations = [
        migrations.AddField(
            model_name='team',
            name='team_code',
            field=models.CharField(blank=True, max_length=256, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name='team',
            name='hybrid_school',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='hybrid_school', to='tab.School'),
        ),
    ]
