"""
CSRF protection for the admin UI's POST forms -- double-submit-cookie
pattern, no JavaScript required since every form here is a plain HTML
<form> submission.

How it works: on login, a random token is set as an httponly cookie
(the browser sends it automatically on every subsequent request) AND
embedded directly into the server-rendered HTML as a hidden form field
(no JS needed to "copy" it there -- the server already knows the value
it just set). On each POST, the handler compares the cookie value
against the submitted form field; they only match if the request
actually came from a page this server rendered after a real login,
which a cross-site form/link an attacker tricks the browser into
submitting cannot reproduce (they can't read the httponly cookie to put
its value in their own form).

Deliberately not relying on SameSite=Lax alone (the previous state):
SameSite=Lax still allows the cookie to be sent on a plain top-level
GET navigation from another site, and while our forms are POST (which
Lax does block cross-site), defense-in-depth against any future GET
mutation or a browser/proxy that doesn't enforce SameSite correctly is
cheap to add and worth having for anything that mutates state.
"""
import hmac
import secrets

CSRF_COOKIE = "saeos_csrf"


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_token_from_request(request) -> str | None:
    return request.cookies.get(CSRF_COOKIE)


async def verify_csrf(request) -> bool:
    """Compares the CSRF cookie against the submitted form's csrf_token
    field using a constant-time comparison (hmac.compare_digest) to
    avoid a timing side-channel, however marginal, on the comparison
    itself. Returns False (not True) if either side is missing --
    a missing cookie or missing form field is a mismatch, not a pass."""
    cookie_value = request.cookies.get(CSRF_COOKIE)
    if not cookie_value:
        return False
    form = await request.form()
    submitted = form.get("csrf_token")
    if not submitted:
        return False
    return hmac.compare_digest(cookie_value, submitted)
