"""RAG endpoints: ingest text into the tenant's knowledge base, and query
it for relevant chunks. Wiring retrieved chunks into a department's
generation call is in app/api/v1/departments_router.py's invoke handler
(the `use_knowledge` flag)."""
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.ai_gateway.gateway import AllProvidersFailedError, get_gateway
from app.audit.service import record_audit
from app.auth.rbac import Unauthorized, get_current_user
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, engine, set_tenant_context
from app.knowledge.models import KnowledgeChunk
from app.knowledge.vector_store import _vector_literal, top_k

EMBEDDING_MODEL = "nomic-embed-text"  # must match routing.yaml's embedding_fallback_chain


async def ingest(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    content = (body or {}).get("content", "").strip()
    department = (body or {}).get("department", "").strip() or "general"
    source = (body or {}).get("source", "").strip() or "manual"

    if not content:
        return JSONResponse({"detail": "content is required"}, status_code=400)

    gateway = get_gateway()
    try:
        embedding = await gateway.embed(content)
    except AllProvidersFailedError as exc:
        return JSONResponse(
            {"detail": "No embedding provider is currently available.", "error": str(exc)}, status_code=503
        )

    settings = get_settings()
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        chunk = KnowledgeChunk(
            tenant_id=user.tenant_id,
            department=department,
            source=source,
            content=content,
            embedding=embedding,
            embedding_model=EMBEDDING_MODEL,
        )
        session.add(chunk)
        await session.commit()
        # No refresh(): chunk.id is a client-side UUID default.

        # Also populate the native pgvector column for fast search, when
        # running against Postgres with the pgvector migration applied.
        # The JSON `embedding` column above is always the canonical,
        # portable copy; this is purely a production performance path.
        if engine.dialect.name == "postgresql" and settings.VECTOR_STORE_BACKEND == "pgvector":
            from sqlalchemy import text
            # Re-set tenant context: the commit() above already reset the
            # is_local=true setting from set_tenant_context() at the top
            # of this function, and this UPDATE runs in a new implicit
            # transaction. Without this, RLS's USING clause would filter
            # this statement to zero matching rows (a silent no-op, not
            # an error) rather than actually setting embedding_vector --
            # caught live-testing against genuine (non-superuser) RLS
            # enforcement in Phase 6, same root cause as record_audit's fix.
            await set_tenant_context(session, user.tenant_id)
            await session.execute(
                text("UPDATE knowledge_chunks SET embedding_vector = :vec WHERE id = :id"),
                {"vec": _vector_literal(embedding), "id": chunk.id},
            )
            await session.commit()

        await record_audit(session, user.tenant_id, user.id, "knowledge.ingest", resource=chunk.id)

    return JSONResponse({"id": chunk.id, "department": chunk.department}, status_code=201)


async def query(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    query_text = (body or {}).get("query", "").strip()
    department = (body or {}).get("department", "").strip() or None
    k = int((body or {}).get("k", 5))

    if not query_text:
        return JSONResponse({"detail": "query is required"}, status_code=400)

    gateway = get_gateway()
    try:
        query_embedding = await gateway.embed(query_text)
    except AllProvidersFailedError as exc:
        return JSONResponse(
            {"detail": "No embedding provider is currently available.", "error": str(exc)}, status_code=503
        )

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        results = await top_k(session, user.tenant_id, query_embedding, k=k, department=department)

    return JSONResponse(
        {
            "results": [
                {"id": chunk.id, "content": chunk.content, "department": chunk.department,
                 "source": chunk.source, "score": round(score, 4)}
                for chunk, score in results
            ]
        }
    )


routes = [
    Route("/api/v1/knowledge/ingest", ingest, methods=["POST"]),
    Route("/api/v1/knowledge/query", query, methods=["POST"]),
]
