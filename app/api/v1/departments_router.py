"""
Generic department invocation endpoint covering all 26 departments from
the registry. One route handles all of them because the RBAC gate, audit
logging, and AI Gateway call/fallback/error-handling are identical for
every department -- only the capability (system prompt, permission code)
differs, and that's looked up from app.departments.registry.
"""
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.ai_gateway.gateway import AllProvidersFailedError, get_gateway
from app.auth.rbac import PermissionDenied, Unauthorized, get_current_user, require_permission
from app.core.database import AsyncSessionLocal
from app.departments.registry import get_agent, list_departments
from app.audit.service import record_audit


async def list_departments_route(request: Request):
    return JSONResponse({"departments": list_departments()})


async def invoke_department(request: Request):
    department_slug = request.path_params["department"]
    agent = get_agent(department_slug, get_gateway())
    if agent is None:
        return JSONResponse({"detail": f"Unknown department: {department_slug}"}, status_code=404)

    try:
        user = await get_current_user(request)
        require_permission(user, agent.capability.required_permission)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    except PermissionDenied as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    prompt = (body or {}).get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"detail": "prompt is required"}, status_code=400)

    try:
        response = await agent.run(prompt, tenant_id=user.tenant_id)
    except AllProvidersFailedError as exc:
        # A real, expected failure mode -- e.g. no API keys configured yet,
        # or Ollama isn't running -- not a bug. 503, not 500.
        async with AsyncSessionLocal() as session:
            await record_audit(
                session, user.tenant_id, user.id, f"{department_slug}.invoke_failed",
                resource=department_slug, metadata={"error": str(exc)},
            )
        return JSONResponse(
            {"detail": "No AI provider is currently available for this department.", "error": str(exc)},
            status_code=503,
        )

    async with AsyncSessionLocal() as session:
        await record_audit(
            session, user.tenant_id, user.id, f"{department_slug}.invoke",
            resource=department_slug,
            metadata={"provider": response.provider, "model": response.model},
        )

    return JSONResponse(
        {"department": department_slug, "provider": response.provider, "model": response.model, "response": response.text}
    )


routes = [
    Route("/api/v1/departments", list_departments_route),
    Route("/api/v1/departments/{department}/invoke", invoke_department, methods=["POST"]),
]
