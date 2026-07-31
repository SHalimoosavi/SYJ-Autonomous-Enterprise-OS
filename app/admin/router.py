"""
Server-rendered HTML admin UI for role/permission management -- a
browser-usable alternative to curl-scripting the JSON API from
app/permissions/router.py. Both call the exact same
app/permissions/service.py functions; this module's job is purely
cookie-session auth (see app/auth/rbac.py's get_current_user cookie
fallback) and HTML rendering (app/admin/templates.py), not business
logic.

Deliberately owner/platform-admin only, same restriction as the JSON
permission-management API -- this is administrative surface.
"""
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from app.admin.templates import render_dashboard, render_login_page
from app.auth.rbac import Unauthorized, get_current_user
from app.auth.service import authenticate
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.permissions import service
from app.tenancy.service import resolve_tenant_by_slug

SESSION_COOKIE = "saeos_session"
TENANT_COOKIE = "saeos_tenant"
COOKIE_MAX_AGE = 60 * 60 * 8  # 8 hours


async def login_page(request: Request):
    return HTMLResponse(render_login_page())


async def login_submit(request: Request):
    form = await request.form()
    tenant_slug = (form.get("tenant_slug") or "").strip().lower()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""

    if not tenant_slug or not email or not password:
        return HTMLResponse(render_login_page(error="All fields are required."), status_code=400)

    async with AsyncSessionLocal() as session:
        user = await authenticate(session, tenant_slug, email, password)

    if user is None:
        return HTMLResponse(render_login_page(error="Invalid tenant, email, or password."), status_code=401)
    if not service.is_owner_or_admin(user):
        return HTMLResponse(render_login_page(error="Only the tenant owner can access the admin panel."), status_code=403)

    from app.core.security import create_access_token
    token = create_access_token(subject=user.id, tenant_id=user.tenant_id)

    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    response.set_cookie(TENANT_COOKIE, tenant_slug, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return response


async def logout(request: Request):
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(TENANT_COOKIE)
    return response


async def _current_admin_or_redirect(request: Request):
    """Returns the authenticated owner/admin User, or a RedirectResponse
    to the login page if not authenticated/authorized. Route handlers
    check `isinstance(result, RedirectResponse)` before proceeding."""
    try:
        user = await get_current_user(request)
    except Unauthorized:
        return RedirectResponse(url="/admin/login", status_code=303)
    if not service.is_owner_or_admin(user):
        return RedirectResponse(url="/admin/login", status_code=303)
    return user


async def dashboard(request: Request):
    user = await _current_admin_or_redirect(request)
    if isinstance(user, RedirectResponse):
        return user

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        tenant = await resolve_tenant_by_slug(session, request.cookies.get(TENANT_COOKIE))
        roles = await service.list_tenant_roles(session, user.tenant_id)
        permissions = await service.list_permission_catalog(session)
        users = await service.list_tenant_users(session, user.tenant_id)

    return HTMLResponse(
        render_dashboard(
            tenant_name=tenant.name if tenant else "SAEOS",
            roles=[{"id": r.id, "name": r.name, "permissions": [p.code for p in r.permissions]} for r in roles],
            permissions=[{"code": p.code, "description": p.description} for p in permissions],
            users=[
                {"id": u.id, "email": u.email, "is_tenant_owner": u.is_tenant_owner, "roles": [r.name for r in u.roles]}
                for u in users
            ],
        )
    )


async def create_role_submit(request: Request):
    user = await _current_admin_or_redirect(request)
    if isinstance(user, RedirectResponse):
        return user

    form = await request.form()
    name = (form.get("name") or "").strip()
    if name:
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, user.tenant_id)
            await service.create_role(session, user.tenant_id, user.id, name)

    return RedirectResponse(url="/admin", status_code=303)


async def assign_permission_submit(request: Request):
    user = await _current_admin_or_redirect(request)
    if isinstance(user, RedirectResponse):
        return user

    role_id = request.path_params["role_id"]
    form = await request.form()
    permission_code = (form.get("permission_code") or "").strip()

    if permission_code:
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, user.tenant_id)
            try:
                await service.assign_permission_to_role(session, user.tenant_id, user.id, role_id, permission_code)
            except service.NotFound:
                pass  # silently ignore a stale/invalid role_id from a race with another admin tab; page just re-renders

    return RedirectResponse(url="/admin", status_code=303)


async def assign_role_submit(request: Request):
    user = await _current_admin_or_redirect(request)
    if isinstance(user, RedirectResponse):
        return user

    target_user_id = request.path_params["user_id"]
    form = await request.form()
    role_id = (form.get("role_id") or "").strip()

    if role_id:
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, user.tenant_id)
            try:
                await service.assign_role_to_user(session, user.tenant_id, user.id, target_user_id, role_id)
            except service.NotFound:
                pass

    return RedirectResponse(url="/admin", status_code=303)


routes = [
    Route("/admin/login", login_page, methods=["GET"]),
    Route("/admin/login", login_submit, methods=["POST"]),
    Route("/admin/logout", logout, methods=["POST"]),
    Route("/admin", dashboard, methods=["GET"]),
    Route("/admin/roles/create", create_role_submit, methods=["POST"]),
    Route("/admin/roles/{role_id}/permissions", assign_permission_submit, methods=["POST"]),
    Route("/admin/users/{user_id}/roles", assign_role_submit, methods=["POST"]),
]
