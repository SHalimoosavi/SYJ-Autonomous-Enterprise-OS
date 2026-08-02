#!/usr/bin/env python3
"""
Provisions the non-superuser application role that Phase 6 discovered is
required for Postgres row-level security to provide any protection at
all -- see docs/TERMUX.md's "Production Deployment tasks" section for
why. Previously a manual, ad-hoc `psql` command sequence (run by hand
during Phase 6 development); this is that sequence, scripted, idempotent,
and safe to re-run.

Usage:
    python scripts/provision_postgres_role.py \\
        --admin-url postgresql://postgres:postgres@localhost:5432/saeos_prod \\
        --app-role saeos_app \\
        --app-password <a-real-secret>

Requires a SUPERUSER (or equivalently privileged) connection to run --
CREATE ROLE and GRANT are administrative operations, and this is
explicitly a one-time (or one-time-per-environment) setup step run by a
human operator during deployment, not something the running application
does to itself. Run this AFTER `alembic upgrade head` has created the
tables (see below for why), then point the application's own
DATABASE_URL at --app-role for actual runtime traffic.

Deliberately plain psycopg2/asyncpg-free: this script only needs to run
occasionally, by a human, typically on the same machine as `psql` itself
-- shelling out to `psql` keeps this script's own dependency footprint
at zero, consistent with the rest of this project's Termux-first
dependency discipline, even though this script itself only ever runs on
a Postgres production host, never in Termux.
"""
import argparse
import subprocess
import sys
from urllib.parse import urlparse


def run_psql(admin_url: str, sql: str, database: str | None = None) -> None:
    """Runs `sql` via psql against admin_url (optionally against a
    different database on the same server, for CREATE ROLE which is
    cluster-wide, not per-database)."""
    url = admin_url if database is None else _with_database(admin_url, database)
    result = subprocess.run(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"FAILED: {sql}\n{result.stderr}", file=sys.stderr)
        raise SystemExit(1)
    print(result.stdout.strip() or f"OK: {sql[:80]}")


def _with_database(url: str, database: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(path=f"/{database}").geturl()


def role_exists(admin_url: str, role: str) -> bool:
    result = subprocess.run(
        ["psql", admin_url, "-t", "-c", f"SELECT 1 FROM pg_roles WHERE rolname='{role}'"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # A real gap this script had until it was actually tested against
        # a flaky connection: don't silently treat "couldn't connect" the
        # same as "role doesn't exist" -- that would proceed to attempt
        # CREATE ROLE, which then fails with a much more confusing error
        # than just saying the connection itself is the problem.
        print(f"Could not connect to check for existing role:\n{result.stderr}", file=sys.stderr)
        raise SystemExit(1)
    return "1" in result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-url", required=True, help="Superuser connection string, e.g. postgresql://postgres:pw@host:5432/dbname")
    parser.add_argument("--app-role", default="saeos_app", help="Name of the application role to create (default: saeos_app)")
    parser.add_argument("--app-password", required=True, help="Password for the application role")
    args = parser.parse_args()

    parsed = urlparse(args.admin_url)
    database = parsed.path.lstrip("/")
    if not database:
        print("--admin-url must include a database name (e.g. .../saeos_prod)", file=sys.stderr)
        raise SystemExit(1)

    print(f"Provisioning role '{args.app_role}' on database '{database}'...")

    if role_exists(args.admin_url, args.app_role):
        print(f"Role '{args.app_role}' already exists -- skipping CREATE ROLE, will still (re-)apply GRANTs.")
    else:
        # CREATE ROLE is cluster-wide (not per-database); NOSUPERUSER and
        # NOBYPASSRLS are the two attributes that actually matter for RLS
        # to have any effect -- see docs/TERMUX.md.
        run_psql(
            args.admin_url,
            f"CREATE ROLE {args.app_role} LOGIN PASSWORD '{args.app_password}' "
            f"NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE",
        )

    # Everything below is per-database and must run against the target
    # database, not necessarily the one --admin-url happened to connect to.
    run_psql(args.admin_url, f"GRANT USAGE ON SCHEMA public TO {args.app_role}", database=database)
    run_psql(
        args.admin_url,
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {args.app_role}",
        database=database,
    )
    run_psql(
        args.admin_url,
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {args.app_role}",
        database=database,
    )
    # Covers tables created by FUTURE migrations without needing to
    # re-run this script after every `alembic upgrade head` -- the
    # actual point of using ALTER DEFAULT PRIVILEGES over a one-time GRANT.
    run_psql(
        args.admin_url,
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {args.app_role}",
        database=database,
    )

    print(
        f"\nDone. Point the application's DATABASE_URL at the '{args.app_role}' role for runtime traffic:\n"
        f"  postgresql+asyncpg://{args.app_role}:<password>@<host>:<port>/{database}\n"
        f"Keep migrations (alembic upgrade head) running as a privileged role -- CREATE POLICY / "
        f"ALTER TABLE ... FORCE ROW LEVEL SECURITY require it."
    )


if __name__ == "__main__":
    main()
