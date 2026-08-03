#!/usr/bin/env python3
"""
Retention-purge procedure for audit_logs, respecting the immutability
trigger from Phase 7 (alembic/versions/f6a7b8c9d0e1_audit_log_immutability.py)
instead of working around it.

That trigger blocks UPDATE/DELETE unconditionally -- which is the point
for day-to-day operation, but a real deployment eventually needs to
purge old rows for storage/compliance reasons. This script is the
deliberate, visible, two-step operation the migration's own docstring
promised: drop the trigger, delete, recreate the trigger, all inside one
transaction so a failure partway through leaves the trigger intact
rather than silently disabled. The purge itself is recorded as two new
audit_logs rows (initiated + completed, with the actual row count) --
plain INSERTs, which the trigger never blocked -- so the deletion of old
history is itself part of the (new) history.

Usage:
    # See what would be deleted, without deleting anything:
    python scripts/purge_audit_logs.py --admin-url postgresql://postgres:pw@host:5432/db --older-than-days 730 --dry-run

    # Actually delete (requires the explicit --confirm flag; --dry-run
    # and --confirm are mutually exclusive on purpose, so a copy-pasted
    # dry-run command doesn't accidentally become destructive):
    python scripts/purge_audit_logs.py --admin-url postgresql://postgres:pw@host:5432/db --older-than-days 730 --confirm

Requires a role that owns audit_logs (or a superuser) -- DROP
TRIGGER/CREATE TRIGGER are DDL, not something the saeos_app-equivalent
runtime role (Phase 6/7) is granted. Same "human operator, occasional
maintenance action" category as provision_postgres_role.py, and for the
same reason, shells out to `psql` rather than adding a Python Postgres
driver dependency.
"""
import argparse
import subprocess
import sys


def run_psql(admin_url: str, sql: str) -> str:
    result = subprocess.run(["psql", admin_url, "-v", "ON_ERROR_STOP=1", "-c", sql], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAILED:\n{sql}\n{result.stderr}", file=sys.stderr)
        raise SystemExit(1)
    return result.stdout


def dry_run(admin_url: str, days: int) -> None:
    output = run_psql(
        admin_url,
        f"SELECT count(*) FROM audit_logs WHERE created_at < now() - interval '{days} days'",
    )
    print(f"DRY RUN: {output.strip().splitlines()[-2].strip() if len(output.strip().splitlines()) > 1 else output.strip()} "
          f"row(s) older than {days} days would be deleted. Re-run with --confirm to actually delete.")


def purge(admin_url: str, days: int) -> None:
    # One transaction: record intent, drop the trigger, delete, restore
    # the trigger, record the actual count -- all-or-nothing. The
    # RETURNING-into-CTE pattern gets the real deleted row count into the
    # completion record without a second round trip.
    sql = f"""
    BEGIN;

    INSERT INTO audit_logs (id, tenant_id, actor_id, action, resource, metadata_json)
    VALUES (gen_random_uuid()::text, 'platform', 'operator:retention_purge', 'audit.purge_initiated', '',
            jsonb_build_object('threshold_days', {days})::json);

    DROP TRIGGER audit_logs_immutable ON audit_logs;

    WITH deleted AS (
        DELETE FROM audit_logs
        WHERE created_at < now() - interval '{days} days'
          AND action NOT LIKE 'audit.purge%'
        RETURNING id
    )
    INSERT INTO audit_logs (id, tenant_id, actor_id, action, resource, metadata_json)
    SELECT gen_random_uuid()::text, 'platform', 'operator:retention_purge', 'audit.purge_completed', '',
           jsonb_build_object('threshold_days', {days}, 'rows_deleted', count(*))::json
    FROM deleted;

    CREATE TRIGGER audit_logs_immutable
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();

    COMMIT;
    """
    result = subprocess.run(["psql", admin_url, "-v", "ON_ERROR_STOP=1", "-c", sql], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAILED -- transaction rolled back, trigger untouched:\n{result.stderr}", file=sys.stderr)
        raise SystemExit(1)
    print(result.stdout)
    print("Purge complete. Immutability trigger restored -- verify with:\n"
          "  psql <url> -c \"SELECT tgname FROM pg_trigger WHERE tgrelid = 'audit_logs'::regclass\"")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-url", required=True, help="Privileged connection string (table owner or superuser)")
    parser.add_argument("--older-than-days", type=int, required=True, help="Delete audit_logs rows older than this many days")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Show what would be deleted, delete nothing")
    mode.add_argument("--confirm", action="store_true", help="Actually perform the purge")
    args = parser.parse_args()

    if args.older_than_days < 1:
        print("--older-than-days must be positive", file=sys.stderr)
        raise SystemExit(1)

    if args.dry_run:
        dry_run(args.admin_url, args.older_than_days)
    else:
        purge(args.admin_url, args.older_than_days)


if __name__ == "__main__":
    main()
