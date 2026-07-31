"""
Auth/permission enforcement for Starlette routes.

There's no FastAPI Depends() here, so this is a plain decorator a route
handler opts into:

    @protected(permission_code="finance.approve_payment")
    async def approve_payment(request):
        user = request.state.user   # already resolved + permission-checked
        ...

`protected()` with no permission_code just requires a valid bearer token
(authentication only, no specific authorization check).
"""
from functools import wraps

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.responses import JSONResponse

from app.auth.models import Role, User
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.core.security import decode_access_token
from app.tenancy.service import resolve_tenant_by_slug


class Unauthorized(Exception):
    pass


class PermissionDenied(Exception):
    def __init__(self, permission_code: str):
        self.permission_code = permission_code
        super().__init__(f"Missing permission: {permission_code}")


def require_permission(user: User, permission_code: str) -> None:
    """Raises PermissionDenied unless the user is a platform admin, the
    owner of their own tenant, or holds the exact permission via a role."""
    if user.is_platform_admin or user.is_tenant_owner:
        return
    user_perm_codes = {p.code for role in user.roles for p in role.permissions}
    if permission_code not in user_perm_codes:
        raise PermissionDenied(permission_code)


async def get_current_user(request) -> User:
    # Bearer header is the API's auth path (used by everything except the
    # HTML admin UI). Falling back to a "saeos_session" cookie lets the
    # exact same function -- same token validation, same tenant-match
    # check, same DB lookup -- serve browser requests too, rather than a
    # second parallel "who is this" implementation for app/admin/.
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    else:
        token = request.cookies.get("saeos_session")
        if not token:
            raise Unauthorized("Missing bearer token")

    payload = decode_access_token(token)
    if payload is None:
        raise Unauthorized("Invalid or expired token")

    tenant_slug = getattr(request.state, "tenant_id", None)  # see tenancy/service.py docstring
    if not tenant_slug:
        raise Unauthorized("Invalid or expired token")

    async with AsyncSessionLocal() as session:
        tenant = await resolve_tenant_by_slug(session, tenant_slug)
        if tenant is None or payload.get("tenant_id") != tenant.id:
            # Deliberately vague: don't reveal whether the token itself was
            # valid for a *different* tenant, which would leak tenant existence.
            raise Unauthorized("Invalid or expired token")

        await set_tenant_context(session, tenant.id)
        result = await session.execute(
            select(User)
            .where(User.id == payload["sub"], User.tenant_id == tenant.id)
            .options(selectinload(User.roles).selectinload(Role.permissions))
        )
        user = result.scalar_one_or_none()

    if user is None:
        raise Unauthorized("Invalid or expired token")
    return user


def protected(permission_code: str | None = None):
    def decorator(handler):
        @wraps(handler)
        async def wrapper(request):
            try:
                user = await get_current_user(request)
                if permission_code:
                    require_permission(user, permission_code)
            except Unauthorized as exc:
                return JSONResponse({"detail": str(exc)}, status_code=401)
            except PermissionDenied as exc:
                return JSONResponse({"detail": str(exc)}, status_code=403)

            request.state.user = user
            return await handler(request)

        return wrapper

    return decorator
