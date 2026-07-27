"""
Permission enforcement, called explicitly from route handlers.

Without FastAPI there is no Depends()-based automatic dependency
injection, so this is a plain async function a handler awaits directly:

    async def approve_payment(request):
        user = await get_current_user(request)
        try:
            require_permission(user, "finance.approve_payment")
        except PermissionDenied as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)
        ...

This is intentionally simple for Phase 1; Phase 2 can wrap this in a
small route decorator once there are enough protected endpoints to
justify the abstraction.
"""
from app.auth.models import User


class PermissionDenied(Exception):
    def __init__(self, permission_code: str):
        self.permission_code = permission_code
        super().__init__(f"Missing permission: {permission_code}")


def require_permission(user: User, permission_code: str) -> None:
    if user.is_platform_admin:
        return
    user_perm_codes = {p.code for role in user.roles for p in role.permissions}
    if permission_code not in user_perm_codes:
        raise PermissionDenied(permission_code)


async def get_current_user(request) -> User:
    # Phase 1.1: decode the bearer token via app.core.security.decode_access_token,
    # look up the User by `sub`/`tenant_id` claims. Left unimplemented here,
    # same as the prior Depends()-based placeholder -- not yet wired to a
    # real login endpoint.
    raise NotImplementedError("Wire to a real session/token lookup in Phase 1.1")
