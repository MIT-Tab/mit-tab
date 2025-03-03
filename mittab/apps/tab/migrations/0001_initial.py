# -*- coding: utf-8 -*-
# Generated by Django 1.9 on 2017-12-23 00:51

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Bye',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('round_number', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='CheckIn',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('round_number', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='Debater',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('name', models.CharField(max_length=30, unique=True)),
                ('provider', models.CharField(blank=True, max_length=40)),
                ('novice_status',
                 models.IntegerField(choices=[(0, 'Varsity'), (1, 'Novice')])),
            ],
        ),
        migrations.CreateModel(
            name='Judge',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('name', models.CharField(max_length=30, unique=True)),
                ('rank', models.DecimalField(decimal_places=2, max_digits=4)),
                ('provider', models.CharField(blank=True, max_length=40)),
            ],
        ),
        migrations.CreateModel(
            name='NoShow',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('round_number', models.IntegerField()),
                ('lenient_late', models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name='Room',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('name', models.CharField(max_length=30, unique=True)),
                ('rank', models.DecimalField(decimal_places=2, max_digits=4)),
            ],
        ),
        migrations.CreateModel(
            name='Round',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('round_number', models.IntegerField()),
                ('pullup',
                 models.IntegerField(choices=[(0, 'NONE'), (1, 'GOV'),
                                              (2, 'OPP')],
                                     default=0)),
                ('victor',
                 models.IntegerField(choices=[(0, 'UNKNOWN'), (1, 'GOV'),
                                              (2, 'OPP'),
                                              (3, 'GOV via Forfeit'),
                                              (4, 'OPP via Forfeit'),
                                              (5, 'ALL DROP'), (6, 'ALL WIN')],
                                     default=0)),
                ('chair',
                 models.ForeignKey(blank=True,
                                   null=True,
                                   on_delete=django.db.models.deletion.CASCADE,
                                   related_name='chair',
                                   to='tab.Judge')),
            ],
        ),
        migrations.CreateModel(
            name='RoundStats',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('speaks', models.DecimalField(decimal_places=4,
                                               max_digits=6)),
                ('ranks', models.DecimalField(decimal_places=4, max_digits=6)),
                ('debater_role', models.CharField(max_length=4, null=True)),
                ('debater',
                 models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                   to='tab.Debater')),
                ('round',
                 models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                   to='tab.Round')),
            ],
        ),
        migrations.CreateModel(
            name='School',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('name', models.CharField(max_length=50, unique=True)),
            ],
        ),
        migrations.CreateModel(
            name='Scratch',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('scratch_type',
                 models.IntegerField(
                     choices=[(0, 'Discretionary Scratch'), (1,
                                                             'Tab Scratch')])),
                ('judge',
                 models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                   to='tab.Judge')),
            ],
        ),
        migrations.CreateModel(
            name='TabSettings',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('key', models.CharField(max_length=20)),
                ('value', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='Team',
            fields=[
                ('id',
                 models.AutoField(auto_created=True,
                                  primary_key=True,
                                  serialize=False,
                                  verbose_name='ID')),
                ('name', models.CharField(max_length=30, unique=True)),
                ('seed',
                 models.IntegerField(choices=[(0, 'Unseeded'), (
                     1, 'Free Seed'), (2, 'Half Seed'), (3, 'Full Seed')])),
                ('checked_in', models.BooleanField(default=True)),
                ('debaters', models.ManyToManyField(to='tab.Debater')),
                ('school',
                 models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                   to='tab.School')),
            ],
        ),
        migrations.AddField(
            model_name='scratch',
            name='team',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to='tab.Team'),
        ),
        migrations.AddField(
            model_name='round',
            name='gov_team',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='gov_team',
                to='tab.Team'),
        ),
        migrations.AddField(
            model_name='round',
            name='judges',
            field=models.ManyToManyField(blank=True,
                                         null=True,
                                         related_name='judges',
                                         to='tab.Judge'),
        ),
        migrations.AddField(
            model_name='round',
            name='opp_team',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='opp_team',
                to='tab.Team'),
        ),
        migrations.AddField(
            model_name='round',
            name='room',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to='tab.Room'),
        ),
        migrations.AddField(
            model_name='noshow',
            name='no_show_team',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to='tab.Team'),
        ),
        migrations.AddField(
            model_name='judge',
            name='schools',
            field=models.ManyToManyField(to='tab.School'),
        ),
        migrations.AddField(
            model_name='checkin',
            name='judge',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to='tab.Judge'),
        ),
        migrations.AddField(
            model_name='bye',
            name='bye_team',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to='tab.Team'),
        ),
    ]
