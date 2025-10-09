import subprocess
import re
from decimal import Decimal
from django.db import transaction
from mittab.apps.tab.models import Judge

from mittab import settings

DB_SETTINGS = settings.DATABASES["default"]
DB_HOST = DB_SETTINGS["HOST"]
DB_NAME = DB_SETTINGS["NAME"]
DB_USER = DB_SETTINGS["USER"]
DB_PASS = DB_SETTINGS["PASSWORD"]
DB_PORT = DB_SETTINGS["PORT"]


class MysqlDumpRestorer:

    def dump(self, include_scratches=True, include_judge_scores=True):
        # If we need to anonymize judge ranks, do it at the ORM level before dumping
        if not include_judge_scores:
            return self._dump_with_anonymized_judge_ranks(include_scratches)
        else:
            return subprocess.check_output(self._dump_cmd(
                include_scratches=include_scratches))
    
    def _dump_with_anonymized_judge_ranks(self, include_scratches=True):
        """
        Create a backup with judge ranks set to 99.00 using Django ORM.
        Uses a transaction that gets rolled back to restore original values automatically.
        """
        try:
            with transaction.atomic():
                # Set all judge ranks to 99
                for judge in Judge.objects.all():
                    judge.rank = Decimal('99.00')
                    judge.save()
                
                # Create the backup with anonymized judge ranks
                dump_output = subprocess.check_output(self._dump_cmd(
                    include_scratches=include_scratches))
                
                # Force rollback to restore original judge ranks
                raise Exception("Intentional rollback to restore original ranks")
                
        except Exception as e:
            # The transaction rollback restores the original judge ranks
            # But we still have the dump with anonymized ranks
            if "Intentional rollback" not in str(e):
                raise
        
        return dump_output

    def restore(self, content):
        """
        This is a multi-stage restore to avoid the worst-case scenario
        where you dump the existing db, but the restore from the new db fails,
        crashing the app with an empty database

        The process is:
            1. Dump existing DB to a new file
            2. Restore from given database
            3. If error for #2, restore from the dumped file again

        Can be improved by using rename database, just need to test that out first
        """
        before = self.dump()
        try:
            subprocess.check_output(self._restore_cmd(), input=content)
        except Exception as e:
            subprocess.check_output(self._restore_cmd(), input=before)
            raise e

    def _restore_cmd(self):  # Note: refactor to just use a python mysql client
        cmd = [
            "mysql",
            DB_NAME,
            "--port={}".format(DB_PORT),
            "--host={}".format(DB_HOST),
            "--user={}".format(DB_USER),
        ]

        if DB_PASS:
            cmd.append("--password={}".format(DB_PASS))

        return cmd

    def _dump_cmd(self, include_scratches=True):
        cmd = [
            "mysqldump",
            DB_NAME,
            "--quick",
            "--lock-all-tables",
            "--complete-insert",
            "--port={}".format(DB_PORT),
            "--host={}".format(DB_HOST),
            "--user={}".format(DB_USER),
        ]

        if DB_PASS:
            cmd.append("--password={}".format(DB_PASS))

        if not include_scratches:
            cmd.append("--ignore-table={}.tab_scratch".format(DB_NAME))

        return cmd
