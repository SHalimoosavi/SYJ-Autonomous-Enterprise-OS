"""
Generic department invocation endpoint covering all 26 departments from
the registry. One route handles all of them because the RBAC gate, audit
logging, and AI Gateway call/fallback/error-handling are identical for
every department -- only the capability (system prompt, permission code)
differs, and that's looked up from app.departments.registry.

Optional RAG: pass "use_knowledge": true in the request body to retrieve
the top-k most relevant KnowledgeChunk rows for this tenant/department
(see app/knowledge/) and prepend them as context before generation. The
department agent itself stays unaware of RAG -- this router builds the
augmented prompt, agent.run() just sees a (possibly longer) prompt.
"""
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.ai_gateway.gateway import AllProvidersFailedError, get_gateway
from app.approvals import service as approval_service
from app.auth.rbac import PermissionDenied, Unauthorized, get_current_user, require_permission
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.departments.registry import get_agent, list_departments
from app.audit.service import record_audit
from app.knowledge.vector_store import top_k
from app.ratelimit.middleware import enforce_rate_limit

DEFAULT_KNOWLEDGE_K = 3


async def list_departments_route(request: Request):
    return JSONResponse({"departments": list_departments()})


async def _build_augmented_prompt(gateway, tenant_id: str, department_slug: str, prompt: str) -> str:
    """Retrieves relevant knowledge chunks and prepends them as context.
    Raises AllProvidersFailedError (same exception generation raises) if
    the embedding call itself fails, so the caller can handle both
    failure modes with one except clause and a consistent 503."""
    query_embedding = await gateway.embed(prompt)
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id)
        results = await top_k(session, tenant_id, query_embedding, k=DEFAULT_KNOWLEDGE_K, department=department_slug)

    if not results:
        return prompt

    context_block = "\n\n".join(f"- {chunk.content}" for chunk, _score in results)
    return (
        f"Relevant context from the knowledge base:\n{context_block}\n\n"
        f"Using the context above where relevant, respond to: {prompt}"
    )


async def invoke_department(request: Request):
    department_slug = request.path_params["department"]
    gateway = get_gateway()
    agent = get_agent(department_slug, gateway)
    if agent is None:
        return JSONResponse({"detail": f"Unknown department: {department_slug}"}, status_code=404)

    try:
        user = await get_current_user(request)
        require_permission(user, agent.capability.required_permission)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    except PermissionDenied as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)

    if (rate_limited := await enforce_rate_limit(user.tenant_id, user.id, "department_invoke")) is not None:
        return rate_limited

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    prompt = (body or {}).get("prompt", "").strip()
    use_knowledge = bool((body or {}).get("use_knowledge", False))
    if not prompt:
        return JSONResponse({"detail": "prompt is required"}, status_code=400)

    try:
        if use_knowledge:
            prompt = await _build_augmented_prompt(gateway, user.tenant_id, department_slug, prompt)
        response = await agent.run(prompt, tenant_id=user.tenant_id)
    except AllProvidersFailedError as exc:
        # A real, expected failure mode -- e.g. no API keys configured yet,
        # or Ollama isn't running -- not a bug. 503, not 500. Covers both
        # the generation call and (if use_knowledge) the embedding call.
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, user.tenant_id)
            await record_audit(
                session, user.tenant_id, user.id, f"{department_slug}.invoke_failed",
                resource=department_slug, metadata={"error": str(exc), "use_knowledge": use_knowledge},
            )
        return JSONResponse(
            {"detail": "No AI provider is currently available for this department.", "error": str(exc)},
            status_code=503,
        )

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        await record_audit(
            session, user.tenant_id, user.id, f"{department_slug}.invoke",
            resource=department_slug,
            metadata={"provider": response.provider, "model": response.model, "use_knowledge": use_knowledge},
        )

    # Automatic escalation: check the department's rules (see
    # app/departments/registry.py and app/departments/base/agent.py's
    # should_escalate) against the combined prompt+response text. This is
    # what turns EscalationRule from a declared-but-inert design into an
    # actual safety behavior -- a match creates a real ApprovalRequest
    # (the same Approval Queue a human uses) rather than letting the
    # department's response stand as if the founder had signed off on it.
    escalation_info = None
    matched_rule = agent.should_escalate({"text": f"{prompt}\n{response.text}"})
    if matched_rule is not None:
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, user.tenant_id)
            approval = await approval_service.create_approval(
                session, user.tenant_id, department_slug,
                title=f"[{department_slug}] {prompt[:150]}",
                description=f"AI response:\n{response.text}\n\nEscalation reason: {matched_rule.condition}",
                requested_by=f"agent:{department_slug}",
            )
            await record_audit(
                session, user.tenant_id, user.id, f"{department_slug}.escalated",
                resource=approval.id, metadata={"reason": matched_rule.condition, "escalate_to": matched_rule.escalate_to},
            )
        escalation_info = {"approval_id": approval.id, "reason": matched_rule.condition, "escalate_to": matched_rule.escalate_to}

    return JSONResponse(
        {
            "department": department_slug, "provider": response.provider, "model": response.model,
            "response": response.text, "escalated": escalation_info is not None, "escalation": escalation_info,
        }
    )


routes = [
    Route("/api/v1/departments", list_departments_route),
    Route("/api/v1/departments/{department}/invoke", invoke_department, methods=["POST"]),
]
