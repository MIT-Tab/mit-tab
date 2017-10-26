# -*- coding: utf-8 -*-
from south.utils import datetime_utils as datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models


class Migration(SchemaMigration):

    def forwards(self, orm):
        # Adding model 'TabSettings'
        db.create_table(u'tab_tabsettings', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('key', self.gf('django.db.models.fields.CharField')(max_length=20)),
            ('value', self.gf('django.db.models.fields.IntegerField')()),
        ))
        db.send_create_signal(u'tab', ['TabSettings'])

        # Adding model 'School'
        db.create_table(u'tab_school', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('name', self.gf('django.db.models.fields.CharField')(unique=True, max_length=50)),
        ))
        db.send_create_signal(u'tab', ['School'])

        # Adding model 'Debater'
        db.create_table(u'tab_debater', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('name', self.gf('django.db.models.fields.CharField')(unique=True, max_length=30)),
            ('phone', self.gf('localflavor.us.models.PhoneNumberField')(max_length=20, blank=True)),
            ('provider', self.gf('django.db.models.fields.CharField')(max_length=40, blank=True)),
            ('novice_status', self.gf('django.db.models.fields.IntegerField')()),
        ))
        db.send_create_signal(u'tab', ['Debater'])

        # Adding model 'Team'
        db.create_table(u'tab_team', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('name', self.gf('django.db.models.fields.CharField')(unique=True, max_length=30)),
            ('school', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['tab.School'])),
            ('seed', self.gf('django.db.models.fields.IntegerField')()),
            ('checked_in', self.gf('django.db.models.fields.BooleanField')(default=True)),
        ))
        db.send_create_signal(u'tab', ['Team'])

        # Adding M2M table for field debaters on 'Team'
        m2m_table_name = db.shorten_name(u'tab_team_debaters')
        db.create_table(m2m_table_name, (
            ('id', models.AutoField(verbose_name='ID', primary_key=True, auto_created=True)),
            ('team', models.ForeignKey(orm[u'tab.team'], null=False)),
            ('debater', models.ForeignKey(orm[u'tab.debater'], null=False))
        ))
        db.create_unique(m2m_table_name, ['team_id', 'debater_id'])

        # Adding model 'Judge'
        db.create_table(u'tab_judge', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('name', self.gf('django.db.models.fields.CharField')(unique=True, max_length=30)),
            ('rank', self.gf('django.db.models.fields.DecimalField')(max_digits=4, decimal_places=2)),
            ('phone', self.gf('localflavor.us.models.PhoneNumberField')(max_length=20, blank=True)),
            ('provider', self.gf('django.db.models.fields.CharField')(max_length=40, blank=True)),
        ))
        db.send_create_signal(u'tab', ['Judge'])

        # Adding M2M table for field schools on 'Judge'
        m2m_table_name = db.shorten_name(u'tab_judge_schools')
        db.create_table(m2m_table_name, (
            ('id', models.AutoField(verbose_name='ID', primary_key=True, auto_created=True)),
            ('judge', models.ForeignKey(orm[u'tab.judge'], null=False)),
            ('school', models.ForeignKey(orm[u'tab.school'], null=False))
        ))
        db.create_unique(m2m_table_name, ['judge_id', 'school_id'])

        # Adding model 'Scratch'
        db.create_table(u'tab_scratch', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('judge', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['tab.Judge'])),
            ('team', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['tab.Team'])),
            ('scratch_type', self.gf('django.db.models.fields.IntegerField')()),
        ))
        db.send_create_signal(u'tab', ['Scratch'])

        # Adding model 'Room'
        db.create_table(u'tab_room', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('name', self.gf('django.db.models.fields.CharField')(unique=True, max_length=30)),
            ('rank', self.gf('django.db.models.fields.DecimalField')(max_digits=4, decimal_places=2)),
        ))
        db.send_create_signal(u'tab', ['Room'])

        # Adding model 'Round'
        db.create_table(u'tab_round', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('round_number', self.gf('django.db.models.fields.IntegerField')()),
            ('gov_team', self.gf('django.db.models.fields.related.ForeignKey')(related_name='gov_team', to=orm['tab.Team'])),
            ('opp_team', self.gf('django.db.models.fields.related.ForeignKey')(related_name='opp_team', to=orm['tab.Team'])),
            ('chair', self.gf('django.db.models.fields.related.ForeignKey')(blank=True, related_name='chair', null=True, to=orm['tab.Judge'])),
            ('pullup', self.gf('django.db.models.fields.IntegerField')(default=0)),
            ('room', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['tab.Room'])),
            ('victor', self.gf('django.db.models.fields.IntegerField')(default=0)),
        ))
        db.send_create_signal(u'tab', ['Round'])

        # Adding M2M table for field judges on 'Round'
        m2m_table_name = db.shorten_name(u'tab_round_judges')
        db.create_table(m2m_table_name, (
            ('id', models.AutoField(verbose_name='ID', primary_key=True, auto_created=True)),
            ('round', models.ForeignKey(orm[u'tab.round'], null=False)),
            ('judge', models.ForeignKey(orm[u'tab.judge'], null=False))
        ))
        db.create_unique(m2m_table_name, ['round_id', 'judge_id'])

        # Adding model 'Bye'
        db.create_table(u'tab_bye', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('bye_team', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['tab.Team'])),
            ('round_number', self.gf('django.db.models.fields.IntegerField')()),
        ))
        db.send_create_signal(u'tab', ['Bye'])

        # Adding model 'NoShow'
        db.create_table(u'tab_noshow', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('no_show_team', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['tab.Team'])),
            ('round_number', self.gf('django.db.models.fields.IntegerField')()),
            ('lenient_late', self.gf('django.db.models.fields.BooleanField')()),
        ))
        db.send_create_signal(u'tab', ['NoShow'])

        # Adding model 'RoundStats'
        db.create_table(u'tab_roundstats', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('debater', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['tab.Debater'])),
            ('round', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['tab.Round'])),
            ('speaks', self.gf('django.db.models.fields.DecimalField')(max_digits=6, decimal_places=4)),
            ('ranks', self.gf('django.db.models.fields.DecimalField')(max_digits=6, decimal_places=4)),
            ('debater_role', self.gf('django.db.models.fields.CharField')(max_length=4, null=True)),
        ))
        db.send_create_signal(u'tab', ['RoundStats'])

        # Adding model 'CheckIn'
        db.create_table(u'tab_checkin', (
            (u'id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('judge', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['tab.Judge'])),
            ('round_number', self.gf('django.db.models.fields.IntegerField')()),
        ))
        db.send_create_signal(u'tab', ['CheckIn'])


    def backwards(self, orm):
        # Deleting model 'TabSettings'
        db.delete_table(u'tab_tabsettings')

        # Deleting model 'School'
        db.delete_table(u'tab_school')

        # Deleting model 'Debater'
        db.delete_table(u'tab_debater')

        # Deleting model 'Team'
        db.delete_table(u'tab_team')

        # Removing M2M table for field debaters on 'Team'
        db.delete_table(db.shorten_name(u'tab_team_debaters'))

        # Deleting model 'Judge'
        db.delete_table(u'tab_judge')

        # Removing M2M table for field schools on 'Judge'
        db.delete_table(db.shorten_name(u'tab_judge_schools'))

        # Deleting model 'Scratch'
        db.delete_table(u'tab_scratch')

        # Deleting model 'Room'
        db.delete_table(u'tab_room')

        # Deleting model 'Round'
        db.delete_table(u'tab_round')

        # Removing M2M table for field judges on 'Round'
        db.delete_table(db.shorten_name(u'tab_round_judges'))

        # Deleting model 'Bye'
        db.delete_table(u'tab_bye')

        # Deleting model 'NoShow'
        db.delete_table(u'tab_noshow')

        # Deleting model 'RoundStats'
        db.delete_table(u'tab_roundstats')

        # Deleting model 'CheckIn'
        db.delete_table(u'tab_checkin')


    models = {
        u'tab.bye': {
            'Meta': {'object_name': 'Bye'},
            'bye_team': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['tab.Team']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'round_number': ('django.db.models.fields.IntegerField', [], {})
        },
        u'tab.checkin': {
            'Meta': {'object_name': 'CheckIn'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'judge': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['tab.Judge']"}),
            'round_number': ('django.db.models.fields.IntegerField', [], {})
        },
        u'tab.debater': {
            'Meta': {'object_name': 'Debater'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '30'}),
            'novice_status': ('django.db.models.fields.IntegerField', [], {}),
            'phone': ('localflavor.us.models.PhoneNumberField', [], {'max_length': '20', 'blank': 'True'}),
            'provider': ('django.db.models.fields.CharField', [], {'max_length': '40', 'blank': 'True'})
        },
        u'tab.judge': {
            'Meta': {'object_name': 'Judge'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '30'}),
            'phone': ('localflavor.us.models.PhoneNumberField', [], {'max_length': '20', 'blank': 'True'}),
            'provider': ('django.db.models.fields.CharField', [], {'max_length': '40', 'blank': 'True'}),
            'rank': ('django.db.models.fields.DecimalField', [], {'max_digits': '4', 'decimal_places': '2'}),
            'schools': ('django.db.models.fields.related.ManyToManyField', [], {'to': u"orm['tab.School']", 'symmetrical': 'False'})
        },
        u'tab.noshow': {
            'Meta': {'object_name': 'NoShow'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'lenient_late': ('django.db.models.fields.BooleanField', [], {}),
            'no_show_team': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['tab.Team']"}),
            'round_number': ('django.db.models.fields.IntegerField', [], {})
        },
        u'tab.room': {
            'Meta': {'object_name': 'Room'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '30'}),
            'rank': ('django.db.models.fields.DecimalField', [], {'max_digits': '4', 'decimal_places': '2'})
        },
        u'tab.round': {
            'Meta': {'object_name': 'Round'},
            'chair': ('django.db.models.fields.related.ForeignKey', [], {'blank': 'True', 'related_name': "'chair'", 'null': 'True', 'to': u"orm['tab.Judge']"}),
            'gov_team': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'gov_team'", 'to': u"orm['tab.Team']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'judges': ('django.db.models.fields.related.ManyToManyField', [], {'blank': 'True', 'related_name': "'judges'", 'null': 'True', 'symmetrical': 'False', 'to': u"orm['tab.Judge']"}),
            'opp_team': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'opp_team'", 'to': u"orm['tab.Team']"}),
            'pullup': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'room': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['tab.Room']"}),
            'round_number': ('django.db.models.fields.IntegerField', [], {}),
            'victor': ('django.db.models.fields.IntegerField', [], {'default': '0'})
        },
        u'tab.roundstats': {
            'Meta': {'object_name': 'RoundStats'},
            'debater': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['tab.Debater']"}),
            'debater_role': ('django.db.models.fields.CharField', [], {'max_length': '4', 'null': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'ranks': ('django.db.models.fields.DecimalField', [], {'max_digits': '6', 'decimal_places': '4'}),
            'round': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['tab.Round']"}),
            'speaks': ('django.db.models.fields.DecimalField', [], {'max_digits': '6', 'decimal_places': '4'})
        },
        u'tab.school': {
            'Meta': {'object_name': 'School'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '50'})
        },
        u'tab.scratch': {
            'Meta': {'object_name': 'Scratch'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'judge': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['tab.Judge']"}),
            'scratch_type': ('django.db.models.fields.IntegerField', [], {}),
            'team': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['tab.Team']"})
        },
        u'tab.tabsettings': {
            'Meta': {'object_name': 'TabSettings'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'key': ('django.db.models.fields.CharField', [], {'max_length': '20'}),
            'value': ('django.db.models.fields.IntegerField', [], {})
        },
        u'tab.team': {
            'Meta': {'object_name': 'Team'},
            'checked_in': ('django.db.models.fields.BooleanField', [], {'default': 'True'}),
            'debaters': ('django.db.models.fields.related.ManyToManyField', [], {'to': u"orm['tab.Debater']", 'symmetrical': 'False'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '30'}),
            'school': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['tab.School']"}),
            'seed': ('django.db.models.fields.IntegerField', [], {})
        }
    }

    complete_apps = ['tab']