"""
Live integration test against a real Postgres+pgvector instance. Skipped
automatically unless PGVECTOR_TEST_DATABASE_URL is set to a reachable
Postgres database with the pgvector migration applied -- this is NOT run
as part of the default `pytest` suite (which targets SQLite, the
Termux/dev default), since standing up Postgres+pgvector isn't something
CI or a Termux device can be assumed to have. It exists so the pgvector
code path has a real, repeatable correctness check available, not just
the pure-Python cosine path that the rest of the suite exercises.

To run: start Postgres with the vector extension, apply migrations with
DATABASE_URL pointed at it and VECTOR_STORE_BACKEND=pgvector, then:
    PGVECTOR_TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/test_pgvector_live.py -v
Uses a unique tenant slug per run, so no manual cleanup is needed between
runs, and connecting as a non-superuser application role (the realistic
production setup -- see docs/ARCHITECTURE.md's Phase 6 section on why
this matters for RLS specifically) works without any extra GRANT beyond
normal table SELECT/INSERT/UPDATE/DELETE.
"""
import os

import pytest

PGVECTOR_TEST_URL = os.environ.get("PGVECTOR_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not PGVECTOR_TEST_URL, reason="PGVECTOR_TEST_DATABASE_URL not set -- skipping live Postgres+pgvector test"
)


def _make_vec(primary_value: float, dim: int = 768) -> list[float]:
    v = [0.0] * dim
    v[0] = primary_value
    v[1] = 1.0 - abs(primary_value)
    return v


@pytest.mark.asyncio
async def test_pgvector_top_k_ranks_correctly_against_real_postgres():
    import os as _os
    _os.environ["DATABASE_URL"] = PGVECTOR_TEST_URL
    _os.environ["VECTOR_STORE_BACKEND"] = "pgvector"

    # Re-import with the env vars above already set, so the module-level
    # engine binds to the live Postgres instance, not the default SQLite.
    from app.core.config import get_settings
    get_settings.cache_clear()

    from sqlalchemy import text
    from app.core.database import AsyncSessionLocal, engine, set_tenant_context
    from app.knowledge.models import KnowledgeChunk
    from app.knowledge.vector_store import top_k, _vector_literal
    from app.tenancy.models import Tenant, TenantPlan, TenantStatus

    assert engine.dialect.name == "postgresql", "This test requires a real Postgres DATABASE_URL"

    # Deliberately no upfront `DELETE FROM knowledge_chunks`/`DELETE FROM
    # tenants` cleanup here: under genuine (non-superuser) RLS enforcement
    # those unscoped deletes would themselves need elevated privileges,
    # which is a reasonable thing to require of a one-off admin/maintenance
    # connection but not of this test. Use a fresh database per run
    # instead (see the module docstring's run instructions), and a unique
    # slug so repeated runs against the same database don't collide.
    import uuid
    slug = f"pgvec-live-{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as session:
        tenant = Tenant(name="PGVec Test", slug=slug,
                         plan=TenantPlan.SUBSCRIPTION_STARTER, status=TenantStatus.TRIAL)
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id

        # Set RLS context now that the tenant exists -- required for the
        # KnowledgeChunk INSERTs below under genuine (non-superuser) RLS
        # enforcement. A real gap this test had until Phase 6: it worked
        # "fine" every earlier time only because it ran against a
        # superuser connection, which bypasses RLS unconditionally.
        await set_tenant_context(session, tenant_id)

        for content, emb in [
            ("Refunds are processed within 30 days.", _make_vec(1.0)),
            ("Our office is in Hyderabad.", _make_vec(-1.0)),
            ("Refund requests take one month typically.", _make_vec(0.9)),
        ]:
            chunk = KnowledgeChunk(tenant_id=tenant_id, department="finance", source="test",
                                    content=content, embedding=emb, embedding_model="test")
            session.add(chunk)
            await session.flush()
            await session.execute(
                text("UPDATE knowledge_chunks SET embedding_vector = :vec WHERE id = :id"),
                {"vec": _vector_literal(emb), "id": chunk.id},
            )
        await session.commit()

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id)
        results = await top_k(session, tenant_id, _make_vec(1.0), k=3)

    assert len(results) == 3
    assert "Refunds are processed" in results[0][0].content
    assert results[0][1] > results[1][1] > results[2][1]
    assert "Hyderabad" in results[2][0].content
    assert results[0][1] == pytest.approx(1.0, abs=1e-6)
