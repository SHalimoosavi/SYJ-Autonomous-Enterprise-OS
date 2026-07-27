# SYJ Autonomous Enterprise OS (SAEOS) — Phase 1 + 1.1

Multi-tenant, provider-agnostic AI Company Operating System.
Foundation layer (Phase 1): tenancy, AI Gateway, event bus, audit log,
reference department. Auth layer (Phase 1.1): registration, login, RBAC,
Postgres row-level security.

Built on **Starlette**, not FastAPI — see `docs/TERMUX.md` for why. No
Pydantic, no Rust-based dependencies anywhere in this repo.

## Run in Termux

```bash
pkg install python python-greenlet
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Test:
```bash
pytest -v
```

Try the real auth flow:
```bash
curl -X POST localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_name":"Acme","tenant_slug":"acme","email":"you@acme.test","password":"a-strong-password"}'
# -> {"tenant_id": "...", "access_token": "...", ...}

curl localhost:8000/api/v1/auth/me \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token from above>"
```

Verified: clean virtualenv install produces zero source/native builds for
any package, all 3 Alembic migrations apply cleanly to SQLite, 26/26
tests pass, and the above `curl` flow works against a live `uvicorn`
process. See `docs/TERMUX.md` for the full verification log.

## What's real vs. stubbed in this skeleton

| Component | Status |
|---|---|
| AI Gateway + provider fallback chain | Working, tested |
| Anthropic / Ollama / OpenRouter providers | Working (need API keys / local Ollama) |
| Tenant middleware + isolation choke point | Working, tested |
| Tenant registration, login, JWT issuing (stdlib HS256), password hashing (pbkdf2_sha256) | Working, tested |
| `get_current_user`, `require_permission`, `protected()` RBAC | Working, tested |
| Postgres row-level security policies | Written, correct no-op on SQLite; needs a real Postgres instance to verify the enforcement path itself |
| Event bus (in-memory) | Working |
| Event bus (Redis) | Stubbed — Production Deployment task |
| Executive Office agent | Reference implementation; not yet wired to a real route (Phase 2) |
| Other 25 departments | Follow the same `DepartmentAgent` pattern — Phase 2 |
| Role/Permission management (assigning permissions to roles via API) | Models exist, no endpoint yet — Phase 2 |

See `docs/ARCHITECTURE.md` for the full design doc (Phase 1 + 1.1) and
`docs/TERMUX.md` for platform compatibility notes and the install-failure
fix history.
