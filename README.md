# SYJ Autonomous Enterprise OS (SAEOS) — Phase 1 → 6

Multi-tenant, provider-agnostic AI Company Operating System.

- **Phase 1**: tenancy, AI Gateway, event bus, audit log model, reference department.
- **Phase 1.1**: registration, login, RBAC, Postgres row-level security.
- **Phase 2**: all 26 departments as callable agents, Approval Queue, audit logging, CEO Briefing.
- **Phase 3**: RAG (pure-Python vector store), multi-department workflow engine, permission management.
- **Phase 4**: async (Celery) workflow execution, OpenAI/Voyage embedding providers, pgvector, dashboard widgets (KPIs, sales pipeline, financials).
- **Phase 5**: Gemini provider, rate limiting (in-memory + Redis), server-rendered permission/role admin UI.
- **Phase 6**: real escalation logic (wired to the Approval Queue), platform-admin cross-tenant view, CSRF hardening, and a chain of serious RLS bugs found + fixed by finally testing against a genuine non-superuser Postgres role.

**⚠️ If you're deploying to Postgres, read `docs/TERMUX.md`'s
"Production Deployment tasks" section before going further.** Phase 6
found that Postgres superusers bypass row-level security
unconditionally — connecting as `postgres` silently defeats every
tenant-isolation guarantee this project's RLS migrations provide. A
dedicated non-superuser role is required, not optional.

Built on **Starlette**, not FastAPI — see `docs/TERMUX.md` for why. No
Pydantic, no Rust-based dependencies anywhere in the default install.

## Run in Termux (default path — unchanged since Phase 1)

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
# 80 passed, 2 skipped -- the 2 skips are Phase 4's live Postgres+pgvector
# and live Celery+Redis integration tests, which need real infra (see below).
```

## Phase 4 production features (need real infra, not part of Termux dev)

**pgvector** (faster similarity search at scale): run Postgres with the
`vector` extension, set `DATABASE_URL` to it and `VECTOR_STORE_BACKEND=pgvector`
in `.env`, then `alembic upgrade head`. Live-verified in this project's own
development against real Postgres 16 + pgvector 0.6.0 — see
`docs/ARCHITECTURE.md` §18 for the two real bugs that live-testing caught
(a silent `alembic.ini` vs `DATABASE_URL` mismatch, and an asyncpg
parameter-typing quirk) before it passed. To run the test yourself:
```bash
PGVECTOR_TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host/db pytest tests/test_pgvector_live.py -v
```

**Async workflow execution** (don't block the HTTP request on multi-step
AI calls): install `celery` and `redis`, set `WORKFLOW_ASYNC_ENABLED=true`
and `CELERY_BROKER_URL` in `.env`, run a worker with
`celery -A app.workflows.tasks worker` (must be `tasks`, not `celery_app`
— see the module docstring for why), then pass `"async": true` when
calling `POST /api/v1/workflows/{slug}/run`. Live-verified against a real
worker process in this project's own development. To run the test yourself:
```bash
CELERY_LIVE_TEST=true WORKFLOW_ASYNC_ENABLED=true CELERY_BROKER_URL=redis://localhost:6379/1 \
  pytest tests/test_async_workflow_live.py -v
```

## Try the full flow

```bash
# 1. Register your company
curl -X POST localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_name":"Acme","tenant_slug":"acme","email":"you@acme.test","password":"a-strong-password"}'

# 2. Ask a department something (needs an AI provider key/Ollama; 503 otherwise)
curl -X POST localhost:8000/api/v1/departments/engineering/invoke \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"prompt":"What should I check before deploying?"}'

# 3. Run a multi-department workflow (sync, default)
curl -X POST localhost:8000/api/v1/workflows/release_review/run \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"input":"Add rate limiting to the login endpoint"}'

# ...or async (needs Celery+Redis running, see above)
curl -X POST localhost:8000/api/v1/workflows/release_review/run \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"input":"Add rate limiting","async":true}'
# -> {"run_id": "...", "status": "queued"}  -- poll GET /api/v1/workflows/runs/<run_id>

# 4. Track a sales deal and record a KPI
curl -X POST localhost:8000/api/v1/dashboard/pipeline \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"name":"Acme Corp deal","value":15000}'
curl -X POST localhost:8000/api/v1/dashboard/kpis \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"department":"engineering","metric_name":"uptime","value":99.9}'

# 5. CEO briefing (real numbers: approvals, pipeline, finances + AI narrative if configured)
curl localhost:8000/api/v1/dashboard/briefing -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>"

# 6. Manage roles/permissions via the browser instead of curl
open http://localhost:8000/admin/login
# log in with your tenant slug, email, password from step 1 -- then create
# roles, assign permissions, and assign roles to staff users from the page.

# 7. Rate limiting (default: 20 requests/min per tenant+user on invoke/workflow endpoints)
# hit it 21+ times in under a minute and you'll get a 429 with Retry-After: 60

# 8. Escalation: some prompts automatically create an Approval Queue entry
curl -X POST localhost:8000/api/v1/departments/engineering/invoke \
  -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"prompt":"Deploy this to production right now"}'
# -> {"escalated": true, "escalation": {"approval_id": "...", "reason": "..."}, ...}

# 9. Platform admin cross-tenant view (needs is_platform_admin=true, granted
#    via direct DB update by you as the operator -- there is deliberately
#    no API to self-grant this)
curl localhost:8000/api/v1/platform/tenants -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>"
curl localhost:8000/api/v1/platform/stats -H "X-Tenant-ID: acme" -H "Authorization: Bearer <token>"
```

## What's real vs. stubbed

| Component | Status |
|---|---|
| AI Gateway (generate + embed) + provider fallback chain | Working, tested |
| All 26 departments, generic invoke endpoint | Working, tested |
| Escalation logic (keyword-triggered, auto-creates Approval Queue entries) | Working, tested — see docs/ARCHITECTURE.md §22 for why this was previously dead code |
| RAG: ingest/query, opt-in retrieval augmentation | Working, tested |
| pgvector production backend | **Live-verified** against real Postgres+pgvector, as a genuine non-superuser role (see docs/ARCHITECTURE.md §22) |
| Workflow engine (sync, default) | Working, tested |
| Workflow engine (async, Celery+Redis, opt-in) | **Live-verified** against a real Celery worker process |
| Rate limiting (in-memory default / Redis production) | Working, tested; Redis path **live-verified** |
| Permission management: roles, catalog, assignment | Working, tested |
| Permission/role admin UI (HTML, cookie-session, CSRF-protected) | Working, tested — including a simulated cross-session CSRF attack |
| Platform-admin cross-tenant view | **Live-verified** against genuine (non-superuser) Postgres RLS enforcement (see docs/ARCHITECTURE.md §22) |
| Dashboard: KPIs, sales pipeline, financial summary | Working, tested |
| Tenant middleware, auth, RBAC, audit logging, Approval Queue | Working, tested |
| Postgres row-level security | **Live-verified against a genuine non-superuser role in Phase 6** — a chain of real bugs (including that superusers bypass RLS entirely) meant this was never actually proven before, despite passing tests in earlier phases. See docs/ARCHITECTURE.md §22 for the full account. |
| Anthropic / Ollama / OpenRouter / OpenAI / Voyage / Gemini providers | Registration logic tested; real network calls need real API keys |
| Event bus (Redis) | Stubbed — Production Deployment only |

See `docs/ARCHITECTURE.md` for the full design doc (all phases) and
`docs/TERMUX.md` for platform compatibility notes, the install-failure
fix history, and the **required non-superuser Postgres role setup**.
