# SYJ Autonomous Enterprise OS (SAEOS) — Phase 1 → 3

Multi-tenant, provider-agnostic AI Company Operating System.

- **Phase 1**: tenancy, AI Gateway, event bus, audit log model, reference department.
- **Phase 1.1**: registration, login, RBAC, Postgres row-level security.
- **Phase 2**: all 26 departments as callable agents, Approval Queue, audit logging, CEO Briefing.
- **Phase 3**: RAG (pure-Python vector store), multi-department workflow engine, permission management.

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
# 1. Register your company
curl -X POST localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_name":"Acme","tenant_slug":"acme","email":"you@acme.test","password":"a-strong-password"}'
# -> {"tenant_id": "...", "access_token": "...", ...}

# 2. Ask a department something (needs ANTHROPIC_API_KEY or a running
#    Ollama in .env -- without one, this correctly returns 503, not a crash)
curl -X POST localhost:8000/api/v1/departments/engineering/invoke \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"prompt":"What should I check before deploying?"}'

# 3. Give a knowledge base entry to a department, then ask with RAG on
#    (needs an embedding model, e.g. `ollama pull nomic-embed-text`)
curl -X POST localhost:8000/api/v1/knowledge/ingest \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"content":"Our refund policy is 30 days.","department":"finance"}'
curl -X POST localhost:8000/api/v1/departments/finance/invoke \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"prompt":"What is our refund policy?","use_knowledge":true}'

# 4. Run a multi-department workflow
curl -X POST localhost:8000/api/v1/workflows/release_review/run \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"input":"Add rate limiting to the login endpoint"}'
# -> {"run_id": "...", "status": "completed", "steps": [...]}
curl localhost:8000/api/v1/workflows/runs/<run_id> -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>"

# 5. Give a staff user access to just Engineering, without making them owner
ROLE_ID=$(curl -s -X POST localhost:8000/api/v1/roles \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"name":"Engineer"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
curl -X POST localhost:8000/api/v1/roles/$ROLE_ID/permissions \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"permission_code":"engineering.act"}'
curl -X POST localhost:8000/api/v1/users/<staff_user_id>/roles \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d "{\"role_id\":\"$ROLE_ID\"}"

# 6. CEO briefing (real numbers, AI narrative if a provider is configured)
curl localhost:8000/api/v1/dashboard/briefing -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>"
```

Verified: clean virtualenv install produces zero source/native builds for
any package, all 7 Alembic migrations apply cleanly to SQLite, 65/65
tests pass, and the full flow above works against a live `uvicorn`
process — including every "no AI/embedding provider configured yet"
state, which degrades gracefully (503 with a clear message) instead of
crashing. See `docs/TERMUX.md` for the verification log.

## What's real vs. stubbed

| Component | Status |
|---|---|
| AI Gateway (generate + embed) + provider fallback chain | Working, tested |
| All 26 departments, generic invoke endpoint | Working, tested |
| RAG: ingest/query, opt-in retrieval augmentation on invoke | Working, tested (embedding generation needs a real provider; retrieval/ranking logic is tested independent of that) |
| Multi-department workflow engine (synchronous execution) | Working, tested — see `app/workflows/router.py` docstring for why sync, not background+polling |
| Permission management: roles, permission catalog, assignment | Working, tested |
| Tenant middleware + isolation choke point | Working, tested |
| Registration, login, JWT, password hashing | Working, tested |
| RBAC (`get_current_user`, `require_permission`, `protected()`) | Working, tested |
| Postgres row-level security | Written, correct no-op on SQLite; covers all 6 tenant-scoped tables |
| Audit logging | Working, tested |
| Approval Queue | Working, tested |
| CEO Briefing dashboard | Working, tested |
| Event bus (in-memory) | Working |
| Event bus (Redis), Celery, async workflow execution | Stubbed/deferred — Phase 4, Production Deployment |
| Dashboard: KPIs, sales pipeline, financial summary | Not started — need real department data sources first |

See `docs/ARCHITECTURE.md` for the full design doc and `docs/TERMUX.md`
for platform compatibility notes and the install-failure fix history.
