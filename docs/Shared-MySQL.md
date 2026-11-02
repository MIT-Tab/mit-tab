# Backups on shared MySQL

`MYSQL_SHARED_CLUSTER=true` enables backups/restores for a restricted user with
DDL/DML privileges on exactly one InnoDB schema. It defaults to false, so existing
standalone deployments retain their existing backup commands and lock behavior.
The companion mittab-deploy shared-hosting release supplies the flag only to new
shared tenants; do not enable it on live tournaments as part of this rollout.

Shared dumps use `--single-transaction --skip-lock-tables --skip-add-locks
--no-tablespaces`. MySQL clients additionally receive `--set-gtid-purged=OFF`;
MariaDB clients are detected via `mysqldump --help` and omit that unsupported flag.
New dumps contain no table LOCK statements. The deployer also grants schema-only
LOCK TABLES so older standalone tournament dumps remain importable; global locks
remain forbidden. Passwords use the child process's MYSQL_PWD environment,
not command arguments or subprocess exception arguments. This does not isolate
credentials from other processes running as the same operating-system user.

A failed restore still attempts to restore the pre-upload snapshot, as before.
Snapshot consistency assumes all tournament tables are InnoDB and no schema
migration runs concurrently. Restore can still disrupt requests in that tenant;
it does not acquire a global cluster lock. No cross-tenant privileges are needed.

## Verification

The six unit cases cover default legacy behavior, snapshot flags, MariaDB
compatibility, scratch exclusions, subprocess credential handling and rollback.
The opt-in integration case creates two disposable schemas/users with restricted
schema grants, runs real Django migrations/initialization, submits registrations,
then dumps/restores and recovers from invalid SQL. It also proves that peer reads,
peer deletion and global locks are denied while the other tenant remains intact.

On a **private disposable** MySQL 8 server, configure the normal test DB connection
as an administrator permitted to create/drop users and databases, then run:

```bash
MITTAB_TEST_SHARED_MYSQL=1 .agents/skills/mittab-task/scripts/mt-test \
  mittab/libs/tests/test_shared_mysql_backups.py \
  mittab/libs/tests/test_shared_mysql_integration.py
```

CI enables that integration test against its disposable MySQL service. The test
was also run locally with MariaDB 10.11 CLI tools against MySQL 8.0.43, matching
the production image's default-mysql-client family. Managed DigitalOcean user
restrictions, App Platform database binding and simultaneous tournament load
still require the canary described in the deployer PR before shared hosting is
enabled for paid bookings. This backup change alone moves no database.
