# -*- coding: utf-8 -*-
from south.utils import datetime_utils as datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models


class Migration(SchemaMigration):

    def forwards(self, orm):
        # Adding field 'Judge.ballot_code'
        db.add_column(u'tab_judge', 'ballot_code',
                      self.gf('django.db.models.fields.CharField')(max_length=6, null=True, blank=True),
                      keep_default=False)


    def backwards(self, orm):
        # Deleting field 'Judge.ballot_code'
        db.delete_column(u'tab_judge', 'ballot_code')


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
            'ballot_code': ('django.db.models.fields.CharField', [], {'max_length': '6', 'null': 'True', 'blank': 'True'}),
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
            'lenient_late': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
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