# pylint: disable=protected-access
from mittab.libs.backup.handlers import DB_NAME, MysqlDumpRestorer


def test_dump_cmd_excludes_all_scratch_tables_when_requested():
    cmd = MysqlDumpRestorer()._dump_cmd(include_scratches=False)

    assert f"--ignore-table={DB_NAME}.tab_scratch" in cmd
    assert f"--ignore-table={DB_NAME}.tab_judgejudgescratch" in cmd
    assert f"--ignore-table={DB_NAME}.tab_teamteamscratch" in cmd
