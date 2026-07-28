# SYJ Autonomous Enterprise OS (SAEOS) — Phase 1 + 1.1 + 2

Multi-tenant, provider-agnostic AI Company Operating System.

- **Phase 1**: tenancy, AI Gateway, event bus, audit log model, reference department.
- **Phase 1.1**: registration, login, RBAC, Postgres row-level security.
- **Phase 2**: all 26 departments as callable agents, Approval Queue,
  audit logging actually wired in, CEO Briefing dashboard endpoint.

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

## Try the full flow

```bash
# 1. Register your company (creates tenant + you as owner)
curl -X POST localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_name":"Acme","tenant_slug":"acme","email":"you@acme.test","password":"a-strong-password"}'
# -> {"tenant_id": "...", "access_token": "...", ...}

# 2. See all 26 departments
curl localhost:8000/api/v1/departments -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>"

# 3. Ask one of them something (needs ANTHROPIC_API_KEY or a running Ollama
#    in .env -- without one, this correctly returns 503, not a crash)
curl -X POST localhost:8000/api/v1/departments/engineering/invoke \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"prompt":"What should I check before deploying?"}'

# 4. Create and decide an approval (the CEO-approval-queue from the spec)
curl -X POST localhost:8000/api/v1/approvals \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"title":"Approve $500 vendor payment","department":"finance"}'
curl -X POST localhost:8000/api/v1/approvals/<id>/decide \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"decision":"approved"}'

# 5. CEO briefing (real numbers, AI narrative if a provider is configured)
curl localhost:8000/api/v1/dashboard/briefing -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>"
```

Verified: clean virtualenv install produces zero source/native builds for
any package, all 5 Alembic migrations apply cleanly to SQLite, 39/39
tests pass, and the full flow above works against a live `uvicorn`
process — including the "no AI provider configured yet" state, which
degrades gracefully (503 / a clear `ai_synthesis_error` field) instead
of crashing. See `docs/TERMUX.md` for the verification log.

## What's real vs. stubbed

| Component | Status |
|---|---|
| AI Gateway + provider fallback chain | Working, tested (provider init bug from Phase 1 fixed) |
| All 26 departments, generic invoke endpoint | Working, tested |
| Tenant middleware + isolation choke point | Working, tested |
| Registration, login, JWT, password hashing | Working, tested |
| RBAC (`get_current_user`, `require_permission`, `protected()`) | Working, tested |
| Postgres row-level security | Written, correct no-op on SQLite; covers users/roles/audit_logs/approval_requests |
| Audit logging | Working, tested — every department invoke and approval decision is recorded |
| Approval Queue | Working, tested (create/list/decide, owner-only decisions) |
| CEO Briefing dashboard | Working, tested — real DB numbers + best-effort AI synthesis |
| Anthropic / Ollama / OpenRouter providers | Working (need real API keys / local Ollama to actually generate text) |
| Event bus (in-memory) | Working |
| Event bus (Redis), Celery | Stubbed — Production Deployment tasks |
| RAG / vector DB | Not started — Phase 3 |
| Role/Permission management API | Models exist, no endpoint yet — Phase 3 |
| Dashboard: KPIs, sales pipeline, financial summary | Not started — need real department data sources first |

See `docs/ARCHITECTURE.md` for the full design doc and `docs/TERMUX.md`
for platform compatibility notes and the install-failure fix history.
