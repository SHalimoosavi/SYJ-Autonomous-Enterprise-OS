"""
Self-service registration and login. Registration is deliberately
tenant-creating (this is the SaaS "sign up your company" flow) --
`/auth/register` is the one auth endpoint that does NOT require an
existing tenant context (see the allowlist in tenancy/middleware.py).
Login *does* require tenant context, since a given email is only unique
within its tenant, not globally.
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.auth.models import User
from app.auth.service import authenticate
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.core.security import create_access_token, hash_password
from app.tenancy.models import Tenant, TenantPlan, TenantStatus

MIN_PASSWORD_LENGTH = 8


async def _parse_json(request: Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


async def register(request: Request):
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"detail": "Invalid JSON body"}, status_code=400)

    tenant_name = (body.get("tenant_name") or "").strip()
    tenant_slug = (body.get("tenant_slug") or "").strip().lower()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    missing = [f for f in ("tenant_name", "tenant_slug", "email", "password")
               if not body.get(f) or not str(body.get(f)).strip()]
    if missing:
        return JSONResponse({"detail": f"Missing required fields: {', '.join(missing)}"}, status_code=400)
    if len(password) < MIN_PASSWORD_LENGTH:
        return JSONResponse(
            {"detail": f"Password must be at least {MIN_PASSWORD_LENGTH} characters"}, status_code=400
        )
    if not tenant_slug.replace("-", "").isalnum():
        return JSONResponse({"detail": "tenant_slug may only contain letters, numbers, and hyphens"}, status_code=400)

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        if existing.scalar_one_or_none() is not None:
            return JSONResponse({"detail": "Tenant slug already taken"}, status_code=409)

        tenant = Tenant(
            name=tenant_name,
            slug=tenant_slug,
            plan=TenantPlan.SUBSCRIPTION_STARTER,
            status=TenantStatus.TRIAL,
        )
        session.add(tenant)
        await session.flush()  # populate tenant.id for the FK below

        # Only now does the tenant actually exist -- set RLS context for
        # it before the User INSERT below, which the WITH CHECK clause on
        # every RLS-protected table (see alembic/versions/*_rls.py)
        # requires. This was a real, serious bug: registration silently
        # relied on nothing enforcing RLS to "work" -- undetected because
        # every earlier phase's "live Postgres" verification happened to
        # run as a Postgres superuser, which bypasses RLS unconditionally
        # regardless of FORCE ROW LEVEL SECURITY (a Postgres behavior, not
        # a policy bug -- see docs/ARCHITECTURE.md's Phase 6 section).
        # Under a genuine non-superuser application role, this INSERT
        # would fail with "new row violates row-level security policy"
        # without this call.
        await set_tenant_context(session, tenant.id)

        user = User(
            tenant_id=tenant.id,
            email=email,
            hashed_password=hash_password(password),
            is_platform_admin=False,
            is_tenant_owner=True,  # the founding user administers their own tenant fully
        )
        session.add(user)

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return JSONResponse({"detail": "Tenant slug or email already in use"}, status_code=409)

        # No session.refresh(user) here: user.id is already populated
        # client-side (the model's UUID default runs on construction, not
        # on INSERT), and nothing in the response below needs any
        # server-generated column. A refresh() would issue a new SELECT
        # in a new implicit transaction post-commit, by which point
        # set_tenant_context()'s is_local=true setting above has already
        # reset -- RLS would then hide the very row just inserted from
        # its own refresh. Real bug hit and fixed during Phase 6 live
        # Postgres testing, not a hypothetical.
        token = create_access_token(subject=user.id, tenant_id=tenant.id)

    return JSONResponse(
        {
            "tenant_id": tenant.id,
            "tenant_slug": tenant.slug,
            "user_id": user.id,
            "access_token": token,
            "token_type": "bearer",
        },
        status_code=201,
    )


async def login(request: Request):
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"detail": "Invalid JSON body"}, status_code=400)

    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    tenant_slug = getattr(request.state, "tenant_id", None)  # see tenancy/service.py docstring

    if not email or not password:
        return JSONResponse({"detail": "email and password are required"}, status_code=400)

    async with AsyncSessionLocal() as session:
        user = await authenticate(session, tenant_slug, email, password)

    if user is None:
        return JSONResponse({"detail": "Invalid email or password"}, status_code=401)

    token = create_access_token(subject=user.id, tenant_id=user.tenant_id)
    return JSONResponse({"access_token": token, "token_type": "bearer"})


routes = [
    Route("/api/v1/auth/register", register, methods=["POST"]),
    Route("/api/v1/auth/login", login, methods=["POST"]),
]
