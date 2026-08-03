"""
Live integration test for scripts/purge_audit_logs.py against a real
Postgres database. Skipped by default unless AUDIT_PURGE_LIVE_TEST=true,
same pattern as this project's other live tests.

Runs the actual script as a subprocess (not importing its functions),
since that's the real, complete artifact a deployment would run --
verifying "the script works" means verifying the actual command line
entry point, not just internal functions in isolation.

To run: apply migrations against a real Postgres database first, then:
    AUDIT_PURGE_LIVE_TEST=true PURGE_TEST_ADMIN_URL=postgresql://postgres:pw@host:5432/db \
      pytest tests/test_audit_purge_live.py -v
"""
import os
import subprocess
import sys
import uuid

import pytest

RUN_LIVE = os.environ.get("AUDIT_PURGE_LIVE_TEST") == "true"
pytestmark = pytest.mark.skipif(not RUN_LIVE, reason="AUDIT_PURGE_LIVE_TEST not set -- skipping live test")

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "purge_audit_logs.py")


def _psql(admin_url: str, sql: str) -> str:
    result = subprocess.run(["psql", admin_url, "-t", "-c", sql], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def admin_url():
    url = os.environ.get("PURGE_TEST_ADMIN_URL")
    if not url:
        pytest.skip("PURGE_TEST_ADMIN_URL not set")
    return url


def test_dry_run_reports_correct_count_without_deleting(admin_url):
    old_id = f"purge-test-old-{uuid.uuid4().hex[:8]}"
    recent_id = f"purge-test-recent-{uuid.uuid4().hex[:8]}"
    _psql(admin_url, f"""
        INSERT INTO audit_logs (id, tenant_id, actor_id, action, resource, metadata_json, created_at) VALUES
        ('{old_id}', 'purge-test-tenant', 'test-actor', 'test.old', '', '{{}}', now() - interval '999 days'),
        ('{recent_id}', 'purge-test-tenant', 'test-actor', 'test.recent', '', '{{}}', now())
    """)

    result = subprocess.run(
        [sys.executable, SCRIPT, "--admin-url", admin_url, "--older-than-days", "500", "--dry-run"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "would be deleted" in result.stdout

    # Nothing actually deleted.
    count = _psql(admin_url, f"SELECT count(*) FROM audit_logs WHERE id IN ('{old_id}', '{recent_id}')")
    assert count.strip() == "2"


def test_confirmed_purge_deletes_only_old_rows_and_restores_trigger(admin_url):
    old_id = f"purge-test-old-{uuid.uuid4().hex[:8]}"
    recent_id = f"purge-test-recent-{uuid.uuid4().hex[:8]}"
    _psql(admin_url, f"""
        INSERT INTO audit_logs (id, tenant_id, actor_id, action, resource, metadata_json, created_at) VALUES
        ('{old_id}', 'purge-test-tenant', 'test-actor', 'test.old', '', '{{}}', now() - interval '999 days'),
        ('{recent_id}', 'purge-test-tenant', 'test-actor', 'test.recent', '', '{{}}', now())
    """)

    result = subprocess.run(
        [sys.executable, SCRIPT, "--admin-url", admin_url, "--older-than-days", "500", "--confirm"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Purge complete" in result.stdout

    assert _psql(admin_url, f"SELECT count(*) FROM audit_logs WHERE id = '{old_id}'").strip() == "0"
    assert _psql(admin_url, f"SELECT count(*) FROM audit_logs WHERE id = '{recent_id}'").strip() == "1"

    # Purge itself was recorded.
    initiated = _psql(admin_url, "SELECT count(*) FROM audit_logs WHERE action = 'audit.purge_initiated'")
    completed = _psql(admin_url, "SELECT count(*) FROM audit_logs WHERE action = 'audit.purge_completed'")
    assert int(initiated) >= 1
    assert int(completed) >= 1

    # Trigger restored and genuinely still blocking mutation.
    trigger_exists = _psql(
        admin_url, "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'audit_logs'::regclass AND tgname = 'audit_logs_immutable'"
    )
    assert trigger_exists.strip() == "1"

    blocked = subprocess.run(
        ["psql", admin_url, "-c", f"DELETE FROM audit_logs WHERE id = '{recent_id}'"],
        capture_output=True, text=True,
    )
    assert blocked.returncode != 0
    assert "append-only" in blocked.stderr


def test_dry_run_and_confirm_are_mutually_exclusive(admin_url):
    result = subprocess.run(
        [sys.executable, SCRIPT, "--admin-url", admin_url, "--older-than-days", "500", "--dry-run", "--confirm"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
