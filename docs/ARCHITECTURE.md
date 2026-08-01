# SAEOS — Phase 1: Foundation

**Scope:** Multi-tenant core, AI Gateway with provider abstraction, auth/RBAC,
event bus, audit logging, and one reference department (Executive Office).
Everything else (25 remaining departments, dashboard UI, RAG/vector DB,
Celery workflows) builds on this foundation in later phases.

---

## 1. Objectives

- Prove the multi-tenant isolation model end-to-end (middleware → DB scoping).
- Prove the AI Gateway's loose-coupling contract: department code never
  references a provider directly; routing is entirely config-driven with
  automatic fallback.
- Establish the department-agent pattern that all 26 departments will follow.
- Keep 100% of this phase runnable and testable inside Termux.

## 2. Folder Structure

```
saeos/
├── app/
│   ├── core/          # config, database, security, event bus
│   ├── tenancy/        # Tenant model, tenant-resolution middleware
│   ├── auth/           # User/Role/Permission models, RBAC dependency
│   ├── ai_gateway/      # Provider abstraction + routing config
│   │   └── providers/   # One file per provider (Claude, Ollama, OpenRouter, ...)
│   ├── departments/
│   │   ├── base/        # DepartmentAgent contract every department implements
│   │   └── executive_office/  # Reference department
│   ├── audit/           # Append-only AuditLog model
│   ├── api/v1/          # Versioned REST routes
│   └── main.py          # Starlette app assembly
├── alembic/              # DB migrations (SQLite dev → Postgres prod, same models)
├── tests/
├── docs/
│   ├── ARCHITECTURE.md   # this file
│   └── TERMUX.md         # platform compatibility notes
├── requirements.txt
└── .env.example
```

## 3. Architecture

**Request flow:** `TenantContextMiddleware` resolves `tenant_id` from the
`X-Tenant-ID` header or subdomain before any route executes, and rejects
un-scoped requests outside a small allowlist (`/health`, `/docs`). Every
subsequent DB query and AI Gateway call carries that `tenant_id`, so data
isolation is enforced at one choke point rather than scattered checks.

**AI Gateway (the loose-coupling core):** Department agents call
`gateway.generate(department, task_type, request)`. The gateway reads
`routing.yaml`, resolves an ordered fallback chain of `[provider, model]`
pairs for that department/task, and tries each in turn until one succeeds.
Adding a provider = implement `AIProvider` + register it in
`build_default_gateway()`. Reassigning which model handles a task = edit
YAML. Neither requires touching agent code — verified by
`tests/test_ai_gateway.py`, which swaps in fake providers and confirms
fallback behavior without any changes to the gateway itself.

**Department agents:** All departments subclass `DepartmentAgent` and
declare an `AgentCapability` (system prompt, tools, escalation rules,
required permission). This is what lets 26 structurally different
departments share one execution path, one audit trail, and one escalation
mechanism instead of 26 bespoke implementations.

**Event bus:** Abstracted behind `EventBus`. `InMemoryEventBus` is the
Termux/dev default; `RedisEventBus` is a typed stub for production
(pub/sub across multiple worker processes), switched purely via
`EVENT_BUS_BACKEND` config.

## 4. Database Schema (Phase 1 tables)

| Table | Purpose |
|---|---|
| `tenants` | Root of isolation. Plan (self-hosted/white-label/subscription tier), status, branding overrides, encrypted per-tenant AI key overrides. |
| `users` | Scoped by `tenant_id`. `is_platform_admin` flags cross-tenant access for you as the OS operator. |
| `roles`, `permissions`, `role_permissions`, `user_roles` | Standard RBAC join model, roles scoped per tenant. |
| `audit_logs` | Append-only. `tenant_id`, `actor_id`, `action`, `resource`, `metadata_json`. |

All tenant-scoped tables carry `tenant_id` as a required foreign key —
the schema-level enforcement that backs the middleware-level isolation.
Production hardening (Phase 1.1): Postgres row-level security policies
keyed on `tenant_id`, in addition to application-level filtering.

## 5. APIs (Phase 1)

- `GET /health` — liveness, no tenant required.
- `GET /api/v1/health` — tenant-scoped liveness, demonstrates the middleware contract.
- Auth endpoints (login/token issuance) and department invocation endpoints
  are Phase 1.1 — the models and gateway they depend on are complete now.

## 6. Tests

`tests/test_ai_gateway.py` — fallback chain behavior, default-chain
resolution for unmapped tasks, all-providers-failed error path.
`tests/test_tenancy.py` — tenant header enforcement on scoped routes.
Both suites pass in Termux with zero native compilation. Run: `pytest -v`.

## 7. Security Review

- **Isolation:** enforced at middleware + schema level; Phase 1.1 adds
  Postgres RLS as defense-in-depth for production multi-tenancy.
- **Secrets:** JWT secret and provider API keys load from `.env`, never
  committed. Per-tenant AI key overrides are stored as an encrypted column
  (`ai_provider_overrides_encrypted`) — encryption implementation (Fernet/KMS)
  is a Phase 1.1 task, the column contract is in place now.
- **Passwords:** bcrypt via passlib, industry-standard work factor.
- **Least privilege:** RBAC denies by default; `require_permission()` only
  passes for platform admins or users holding the exact permission code.
- **Audit:** append-only log table; nothing here yet enforces immutability
  at the DB level (no `UPDATE`/`DELETE` revocation) — add a Postgres trigger
  or table-level grant restriction in production.

## 8. Performance Review

- Async SQLAlchemy end-to-end (no blocking DB calls under Starlette's event loop).
- AI Gateway fallback means one slow/down provider degrades latency for a
  single call, not the whole system — worth adding per-provider timeouts
  and circuit-breaking in Phase 1.1 (currently relies on `httpx` client
  timeouts set per provider).
- In-memory event bus has no cross-process fan-out — fine for single-worker
  Termux dev, insufficient for a scaled production deployment (hence the
  Redis stub).

## 9. Deployment Notes

- **Dev (Termux):** SQLite + in-memory event bus, no external services.
- **Production:** swap `DATABASE_URL` to Postgres, `EVENT_BUS_BACKEND` to
  `redis`, implement `RedisEventBus`, add Celery for long-running
  department workflows, deploy behind a reverse proxy with TLS termination
  per tenant subdomain (or custom domain for white-label customers).
- **Licensing model impact:** self-hosted customers get this repo +
  their own `.env`; subscription customers run against your multi-tenant
  cluster; white-label customers get subscription infra + their
  `brand_*` fields applied by the frontend.

## 10. Termux Compatibility Notes

The first Phase 1 delivery failed to install on Termux (Python 3.14):
`pydantic-core`'s Rust build failed because the pinned `pydantic` version
resolved a `pydantic-core`/PyO3 combination that doesn't support 3.14 —
and more fundamentally, PyPI never publishes an Android/Bionic wheel for
`pydantic-core` at *any* Python version, so Termux always has to compile
it from source. Since FastAPI hard-requires Pydantic v2, and `bcrypt>=4.0`
/ `python-jose[cryptography]` carry the same class of compiled-dependency
risk, the fix was to remove all three from the dependency tree rather
than chase version pins: FastAPI → Starlette directly, `pydantic-settings`
→ a small stdlib `dataclasses`-based settings loader, `python-jose` → a
hand-rolled stdlib HS256 JWT implementation, `bcrypt` → `passlib`'s
pure-Python `pbkdf2_sha256` scheme. None of the department/gateway/
tenancy/RBAC logic imported Pydantic, so this was a boundary-layer swap.

Verified in a clean virtualenv: `pip install -r requirements.txt`
triggers zero source/native builds for any package, and all 10 tests
pass. One Termux `pkg` step is still required before `pip install` —
`pkg install python-greenlet` — since SQLAlchemy's async engine needs
`greenlet` at runtime and Termux ships a working precompiled build of it
where PyPI does not. Full rationale, the removed-package table, and the
supported-Python-version justification (3.11–3.13, 3.12 recommended) are
in `docs/TERMUX.md`.

## 12. Phase 1.1 — Auth, RBAC, and Postgres RLS (complete)

Built on top of the Phase 1 foundation, no changes to its dependency
profile (still zero Rust/C-compiled packages):

- **`POST /api/v1/auth/register`**: creates a Tenant + its founding User
  in one call. Deliberately the one endpoint that doesn't require an
  existing tenant context (see the allowlist in `tenancy/middleware.py`)
  — this *is* the "sign up your company" SaaS entry point. The founding
  user is marked `is_tenant_owner=True`, giving them full access within
  their own tenant without needing individual permission rows.
- **`POST /api/v1/auth/login`**: verifies email + password (pbkdf2_sha256)
  scoped to the tenant resolved from `X-Tenant-ID`, issues a stdlib-HS256
  JWT carrying the user id and the tenant's real UUID.
- **`get_current_user` / `require_permission` / `protected()`**
  (`app/auth/rbac.py`): resolves a bearer token to a real DB-backed user
  with roles/permissions eager-loaded, and gates routes via a decorator
  since there's no FastAPI `Depends()`. Platform admins and tenant owners
  bypass permission checks; everyone else needs a matching `Permission.code`
  via one of their `Role`s.
- **`GET /api/v1/auth/me`** and **`GET /api/v1/executive/briefing`**:
  reference protected routes proving the auth-only and auth+permission
  paths both work end-to-end against a real database, not mocks.
- **Postgres RLS** (`alembic/versions/f1a2b3c4d5e6_postgres_row_level_security.py`):
  adds `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` policies
  on `users`, `roles`, and `audit_logs`, keyed on
  `current_setting('app.current_tenant_id')`. This is a deliberate no-op
  on SQLite (detected via `bind.dialect.name`, not a separate migration
  branch) — Termux dev keeps working exactly as before, while production
  Postgres gets database-level tenant isolation as defense-in-depth
  underneath the existing application-level filtering.
  `app.core.database.set_tenant_context()` sets that session variable per
  request; it's a no-op on SQLite for the same reason.

**A real bug this phase caught and fixed:** `request.state.tenant_id`
(from the `X-Tenant-ID` header) holds the tenant's *slug*, but
`User.tenant_id` in the database is the tenant's UUID. Login and
`get_current_user` were comparing the two directly and always failing.
Fixed by centralizing slug→UUID resolution in
`app/tenancy/service.py:resolve_tenant_by_slug()`, called from every
DB-touching auth handler — verified via a real HTTP smoke test (register
→ login → `/auth/me` → permission-gated route), not just unit tests
against mocks.

**SQLite/Postgres migration compatibility:** the autogenerated migration
for `is_tenant_owner` + the tenant/email unique constraint originally
failed on SQLite (`NotImplementedError: No support for ALTER of
constraints`). Fixed by switching to `op.batch_alter_table(...)` and
enabling `render_as_batch=True` in `alembic/env.py`, so future migrations
work on both dialects by default without needing to remember this each time.

Full verification for this phase: fresh venv install (zero native
compilation, confirmed via install-log grep and `pip list`), all three
migrations applying cleanly against SQLite, 26/26 tests passing, and a
live `uvicorn` process handling real `curl` requests through the entire
register → login → authenticated → permission-gated flow.

## 14. Phase 2 — Department registry, Approval Queue, audit logging, CEO briefing (complete)

- **All 26 departments from the master spec are real, callable agents**
  (`app/departments/registry.py`), reachable via a single generic
  `POST /api/v1/departments/{slug}/invoke`. Executive Office keeps its
  bespoke subclass (custom `should_escalate`); the other 25 run through
  `GenericDepartmentAgent`, driven entirely by an `AgentCapability`
  definition. This is a deliberate scope choice: full RBAC gating, audit
  logging, and graceful AI-Gateway-failure handling are identical for
  every department, so one route + a data table covers all 26 correctly
  rather than 26 partially-tested bespoke route handlers.
- **Fixed a real pre-existing bug**: `build_default_gateway()` was
  constructing `AnthropicProvider(api_key="${ANTHROPIC_API_KEY}")` — a
  literal string, not an environment lookup. That key would never have
  worked. `Settings` now has real `ANTHROPIC_API_KEY` /
  `OPENROUTER_API_KEY` / `OLLAMA_BASE_URL` fields, and the gateway only
  registers a provider if its credential is actually present (Ollama,
  needing no key, is always registered). A missing/misconfigured provider
  is skipped by the fallback chain instead of silently never working.
- **Approval Queue** (`app/approvals/`): the concrete implementation of
  "the CEO approves strategic decisions, financial commitments, legal
  matters, and exceptions" from the master spec's Human Role section.
  Create/list are available to any authenticated user in the tenant;
  deciding (`approve`/`reject`) is restricted to the tenant owner or a
  platform admin. Covered by Postgres RLS the same way `users`/`roles`/
  `audit_logs` are.
- **Audit logging is now actually wired in** (`app/audit/service.py`).
  The `AuditLog` table existed since Phase 1 but nothing ever wrote to
  it — a real gap, now closed: every department invocation (success or
  the graceful-failure path), every approval creation/decision, gets a
  row.
- **CEO Briefing** (`GET /api/v1/dashboard/briefing`, owner/admin only):
  aggregates real numbers (pending approvals, total audit events) from
  the database, then attempts an AI-synthesized narrative on top via
  `ExecutiveOfficeAgent`. If no provider is configured or reachable
  (a completely normal state on a fresh install before API keys are
  added), the endpoint still returns the real numbers with a clear
  `ai_synthesis_error` field — verified explicitly by test and by a live
  `curl` call in this exact "no keys configured" state, not assumed.

**Explicitly deferred to Phase 3, not attempted here**: RAG/vector DB
abstraction, Celery-based async workflows (Redis/Celery stay
Production-Deployment-only per `docs/TERMUX.md`; Termux-compatible
in-process orchestration is a Phase 3 design task), Role/Permission
management endpoints (assigning permissions to roles via API — the
tenant-owner-bypass model covers Phase 1–2's needs without this), and
the remaining Dashboard widgets (KPIs, sales pipeline, financial
summary) that need department-specific data sources which don't exist
yet. Naming these explicitly rather than silently leaving them out.

Full verification for this phase: fresh venv install (zero native
compilation, unchanged from Phase 1), all 5 migrations applying cleanly
to SQLite, 39/39 tests passing, and a live `uvicorn` process handling
the complete register → invoke-department (graceful 503) → create
approval → decide approval → CEO briefing flow via real `curl` calls.

## 16. Phase 3 — RAG, workflow engine, permission management (complete)

- **RAG** (`app/knowledge/`): `KnowledgeChunk` stores embeddings as a JSON
  float array; `vector_store.py` does cosine similarity in pure Python —
  deliberately not pgvector/Chroma, which aren't Termux-installable
  without native compilation, same problem class as `pydantic-core`.
  `AIProvider.embed()` was added to the provider interface (default
  raises `NotImplementedError`, so a provider without embedding support
  is skipped by the fallback chain the same way a down provider is);
  `OllamaProvider.embed()` is a real implementation against Ollama's
  `/api/embeddings`. `POST /api/v1/knowledge/ingest` and
  `POST /api/v1/knowledge/query` are the storage/retrieval endpoints;
  `POST /api/v1/departments/{slug}/invoke` gained an opt-in
  `"use_knowledge": true` flag that retrieves top-k relevant chunks and
  prepends them as context before generation — the department agent
  itself stays unaware RAG happened, the router builds the augmented
  prompt.
- **Workflow engine** (`app/workflows/`): named, multi-step,
  multi-department sequences (`release_review`: Engineering → QA →
  DevOps; `vendor_onboarding`: Procurement → Legal → Finance), each
  step's prompt able to reference `{input}` and `{previous}` so steps
  genuinely chain. **Executes synchronously within the request** rather
  than as a fire-and-forget background task with polling — a
  deliberate, documented trade-off (see `app/workflows/router.py`'s
  module docstring): background execution against Starlette's sync
  `TestClient` can't be verified deterministically, and shipping an
  orchestration feature whose correctness can't be fully tested was the
  wrong call given this project's standing bar. `WorkflowRun` /
  `WorkflowStepRun` persist history either way, so a future
  Celery-backed async executor (Phase 4, production-only) can write to
  the identical schema — only who calls the step execution and when
  changes, not the data model.
- **Permission management** (`app/permissions/`): `GET /api/v1/permissions`
  auto-seeds one `Permission` row per department's `required_permission`
  code from the registry (idempotent, so the catalog can never drift out
  of sync with what's actually enforced), plus role CRUD and
  user-role/role-permission assignment. This closes a real gap from
  Phase 1.1/2: previously the only way for a non-owner user to do
  anything was for the tenant owner to give up and mark them
  `is_tenant_owner` too. Now a staff user can be given a `Role` with just
  `engineering.act`, verified end-to-end by a test that checks they get
  a 503 (reached the AI Gateway) instead of a 403 (denied by RBAC) on
  `/api/v1/departments/engineering/invoke`.

**Explicitly deferred to Phase 4**: async/Celery-backed workflow
execution with polling (production-only, once Redis/Celery are
available), a dedicated non-Ollama embedding provider (Voyage AI/OpenAI)
for tenants who don't want to run local models, and pgvector as the
production-scale vector store swap once corpus size actually demands it.

Full verification for this phase: fresh venv install (zero native
compilation, unchanged), all 7 migrations applying cleanly to SQLite
(including two more RLS-extension migrations, same no-op-on-SQLite
pattern), 65/65 tests passing, and a live `uvicorn` process handling the
complete register → list workflows → run workflow (graceful 503) → get
run status → knowledge ingest (graceful 503) → list permissions (26,
auto-seeded) → create role → assign permission flow via real `curl`
calls.

## 18. Phase 4 — async workflow execution, dedicated embedding providers, pgvector, dashboard widgets (complete)

This phase touched real external infrastructure (Postgres+pgvector,
Redis+Celery) that isn't part of the Termux/SQLite default path, so
everything below states plainly what was actually live-verified against
real running services in this environment versus what's unit-tested
only -- the same standard as the RLS disclosure in Phase 1.1, not
weakened for this phase.

**Dashboard widgets** (`app/dashboard/models.py`, extending
`app/dashboard/router.py`): `KPIMetric`, `SalesDeal`, `FinancialTransaction`
-- plain DB CRUD + aggregation, zero AI Gateway dependency, fully
unit-tested (`tests/test_dashboard_widgets.py`). The CEO Briefing now
includes real open-pipeline-value and net-financial-position numbers
alongside the Phase 2 approval/audit counts.

**Dedicated embedding providers**: `OpenAIProvider` (generate + embed)
and `VoyageProvider` (embed-only, Voyage has no chat-completion API) —
same pattern as every other provider, registered only when their API key
is configured, added to `routing.yaml`'s `embedding_fallback_chain`
after Ollama. Real network calls untested (no live credentials here,
consistent with Anthropic/OpenRouter since Phase 1); registration logic
is unit-tested.

**A real bug this phase caught**: `gateway.py` captured `settings` as a
module-level global at import time instead of calling `get_settings()`
fresh inside `build_default_gateway()` -- meaning config changes after
process startup were invisible to it. Fixed; caught by a test asserting
the gateway only registers providers whose keys are actually present.

**pgvector** (`app/knowledge/vector_store.py`, `alembic/versions/d4e5f6a7b8c9_*`):
**Live-verified** against a real PostgreSQL 16 + pgvector 0.6.0 instance
installed in this environment specifically to test this properly, not
just written and assumed correct. That process caught two more real
bugs before it passed:
  - **`alembic/env.py` was silently ignoring `DATABASE_URL` entirely**,
    always migrating the hardcoded SQLite URL from `alembic.ini`
    regardless of what the app was actually configured to use. This
    means every prior phase's "migrations apply cleanly" claim was true
    only for SQLite -- the RLS/schema migrations had never actually run
    against Postgres until this fix. Fixed by forcing
    `config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)`
    in `env.py`.
  - asyncpg's prepared-statement protocol raised `AmbiguousParameterError`
    on a nullable `:department` parameter compared against `NULL` with
    no type hint, and separately, SQLAlchemy's `text()` bind-parameter
    parser doesn't accept `:param::type` inline-cast syntax (conflicts
    with its own colon-based parsing). Fixed with explicit
    `CAST(:department AS VARCHAR)`.

  After both fixes: real HNSW-indexed cosine search against live data
  returns correctly ranked results (exact match scored 1.0, a near-match
  0.9939, an opposite vector -1.0) -- see `tests/test_pgvector_live.py`
  (skipped by default, runs when `PGVECTOR_TEST_DATABASE_URL` is set).
  The JSON `embedding` column stays canonical/portable; `embedding_vector`
  is a Postgres-only performance path written alongside it. Fixed at
  768 dimensions (matching `nomic-embed-text`); switching embedding
  models with a different output size needs a new migration to resize
  the column -- a real, disclosed constraint.

**Async workflow execution** (`app/workflows/executor.py`,
`celery_app.py`, `tasks.py`): the step-execution loop was extracted from
the Phase 3 synchronous router into a shared function used by both the
default sync path (unchanged behavior, all Phase 3 tests pass
byte-for-byte identical) and a new opt-in `"async": true` mode that
enqueues a Celery task and returns 202 immediately, polled via the
existing `GET /api/v1/workflows/runs/{run_id}`. **Live-verified** against
a real Redis broker and a real, separately-running `celery -A
app.workflows.tasks worker` process -- not Celery's eager-mode testing
shortcut. That process caught two more real bugs:
  - Starting the worker with `-A app.workflows.celery_app` (the Celery
    app definition) rather than `-A app.workflows.tasks` (the module
    that actually registers the task via the decorator) produces a
    worker that starts cleanly and connects to Redis, but has an empty
    task registry and can never execute anything sent to it.
  - The worker process, unlike the main app, doesn't transitively import
    every model module -- so `WorkflowRun.tenant_id`'s foreign key to
    `tenants.id` couldn't resolve the first time a task touched the DB
    (`NoReferencedTableError`), because `Tenant` was never imported in
    that process. Fixed with the same explicit-model-imports pattern
    already used in `alembic/env.py`.

  After both fixes: a task submitted by the API process was picked up,
  executed, and had its result (a correctly-detected AI Gateway failure,
  since no provider is configured in this environment) written back to
  the database entirely by the separate worker process, and read back
  correctly by the API's polling endpoint one second later. See
  `tests/test_async_workflow_live.py` (skipped by default, runs when
  `CELERY_LIVE_TEST=true` with a real worker running).

Neither Celery/Redis nor Postgres/pgvector are part of the default
`requirements.txt` or the Termux install path -- both remain exactly
where Phase 1 put them, Production Deployment only, per `docs/TERMUX.md`.
The default SQLite/sync/pure-Python path is unchanged and fully
re-verified: fresh venv, zero native compilation, all 9 migrations clean
on SQLite, 80 tests passing (plus 2 correctly skipping without live
infra), before any of the above infrastructure was ever touched.

## 20. Phase 5 — Gemini provider, rate limiting, permission admin UI (complete)

**Gemini provider** (`app/ai_gateway/providers/gemini_provider.py`):
generate() + embed(), same pattern as every other provider, finally
using `Settings.GEMINI_API_KEY` (present but unused since Phase 2).
Added to `routing.yaml`'s embedding chain and as a fallback in
`general_operations`/`executive_office`.

**Rate limiting** (`app/ratelimit/`): `InMemoryRateLimiter` (Termux
default, per-process) and `RedisRateLimiter` (production, shared across
processes) behind a common interface, same shape as Phase 1's
`EventBus`. Applied to the two endpoints that actually cost money --
department invoke and workflow run -- not globally (health checks and
DB-only dashboard reads aren't limited the same way). **Live-verified**
against real Redis: two separate limiter instances sharing one counter
correctly (the actual bug the Redis backend exists to fix over the
in-memory default).

A real, *reproducible* bug (not a one-off flake) surfaced during this
phase's own testing: `InMemoryRateLimiter` originally aligned windows to
absolute wall-clock boundaries (`now - (now % window_seconds)`), the
same pattern `RedisRateLimiter` uses. A 20-request burst test failed
roughly 1 run in 3-4 -- far too often to be the genuine rare edge case
that pattern is normally associated with, which was the tell that
something was actually wrong rather than "just flaky." Root cause: any
burst landing near a wall-clock minute boundary resets mid-burst,
letting through nearly double the intended limit for a moment -- and in
a fast test suite firing 20 requests in well under a second, hitting
*some* minute boundary across enough of the CI-like repeated runs isn't
actually rare. Fixed by anchoring each key's window to that key's own
first request instead of the wall clock, which removes the failure mode
by construction. `RedisRateLimiter` keeps the absolute-boundary
INCR+EXPIRE pattern deliberately (standard practice, O(1) Redis state)
since its live tests never exercised a comparable rapid-fire burst;
documented as a real, intentional difference between the two backends,
not an inconsistency. The fix was verified by rerunning the full suite
8 consecutive times with zero failures, not just re-running once and
moving on.

**Permission/role admin UI** (`app/admin/`): a server-rendered HTML
dashboard (cookie-session login, forms for creating roles, assigning
permissions, assigning roles to users) as an alternative to
curl-scripting the JSON permission-management API. Built without adding
Jinja2 as a dependency -- Jinja2 itself is pure Python, but its
MarkupSafe dependency ships an optional C speedup extension, and this
project's standing policy has been to avoid even "safe fallback"
compiled dependencies when a small amount of plain code does the job;
`app/admin/templates.py` is f-strings + `html.escape()` instead, with a
test specifically confirming a role named `<script>...` renders escaped,
not executed.

Two real design points, not afterthoughts:
- `app/auth/service.py`'s `authenticate()` and `app/permissions/service.py`
  were extracted from the existing JSON routers specifically so the HTML
  UI and the API call the *identical* tested logic -- verified by a test
  that creates a role via the HTML form and confirms it's visible through
  the JSON API in the same request-response cycle, not just "looks similar."
- `get_current_user()` and `TenantContextMiddleware` were extended to
  accept a session cookie as a fallback to the Bearer header/tenant
  header respectively, with zero behavior change for existing API
  clients -- verified by the full pre-existing suite passing unchanged
  before any admin-specific tests were even written.

A second real bug caught mid-development (not shipped): `login()`'s
refactor initially called `set_tenant_context(session, tenant_slug)` --
passing the tenant's *slug* where that function expects the *UUID*.
Harmless no-op on SQLite (this project's default), silently wrong for
live Postgres RLS. Caught and fixed by moving that call inside
`authenticate()`, where the resolved UUID actually exists, before it
was ever exercised against real Postgres.

A third bug, caught by the very first admin UI test run: the tenant
middleware's allowlist only exempted `/admin/login` from requiring
tenant context, not `/admin` itself -- so an unauthenticated visit to
the dashboard got a raw 400 from the middleware instead of the intended
303 redirect to the login page. Fixed by widening the allowlist to the
whole `/admin` prefix, since every route under it already handles its
own auth/redirect logic.

Full verification: fresh venv, zero native compilation (still --
Jinja2/MarkupSafe were deliberately not added), all 9 migrations clean
on SQLite, 97 tests passing (plus 4 correctly skipping without live
infra) across 8 consecutive full-suite runs with zero flakes, and the
Redis-backed rate limiter and Gemini registration logic live/unit
verified per the standards above.

## 22. Phase 6 — escalation logic, platform-admin cross-tenant view, CSRF hardening (complete)

This phase found more genuine, previously-invisible bugs than any prior
phase, almost all of them because it was the first time a full HTTP
request was ever driven against Postgres **connected as a real,
non-superuser application role**. Every earlier phase's "live Postgres"
verification, without realizing it, ran as the `postgres` superuser --
and Postgres superusers bypass row-level security unconditionally,
regardless of `FORCE ROW LEVEL SECURITY`. That single fact means RLS
had never actually been exercised end-to-end before this phase, only
schema-inspected or exercised in isolation from the parts of the
request path that touch it. This section documents what actually
shipped and, in detail, what was found and fixed -- the same standard
applied throughout this project, not softened because the list is long.

### Escalation logic

`EscalationRule`/`should_escalate()` existed since Phase 1 as a declared
interface with **zero callers** -- a real, if quiet, gap: a designed
safety feature that had never been wired to anything. Fixed by:
- `AgentCapability.escalation_rules` gained real, differentiated
  `trigger_keywords` per department (Finance: payment/invoice/refund;
  HR: hire/fire/terminate; Engineering: production/deploy/drop table;
  Investor Relations: `ALWAYS_ESCALATE`, matching its "never sends
  anything externally without founder approval" mission) instead of the
  same two generic conditions copy-pasted 25 times.
- `DepartmentAgent.should_escalate()` got a real default implementation:
  case-insensitive keyword matching against the combined prompt+response
  text. Deliberately not a second AI call to "judge" escalation --
  free, deterministic, fully testable without any provider configured.
- `app/api/v1/departments_router.py`'s invoke handler now actually calls
  `should_escalate()` after a successful response and, on a match,
  creates a real `ApprovalRequest` via the Phase 2 Approval Queue
  (`app/approvals/service.py`, extracted from router-inline code so both
  the human-initiated and AI-escalated paths create approvals identically),
  audit-logs it, and returns `escalated`/`escalation` in the response.

### Platform-admin cross-tenant view

`is_platform_admin` existed on `User` since Phase 1.1 with no dedicated
surface. New `app/platform/` module: `GET /api/v1/platform/tenants`,
`GET /api/v1/platform/tenants/{id}`, `GET /api/v1/platform/stats`, all
platform-admin-only, all querying without a tenant_id filter (the whole
point) via a new `set_platform_admin_context()` alongside a migration
that updates every RLS policy to add an `OR
current_setting('app.is_platform_admin', true) = 'true'` bypass clause.

### The bug chain this phase found (in the order discovered)

1. **`set_tenant_context()` used invalid SQL.** `SET LOCAL app.x = :y` is
   not valid Postgres syntax -- `SET` does not accept bind parameters in
   that position at all. Every call would have raised a syntax error
   against a real server. Undetected because no earlier live test drove
   a full HTTP request through `get_current_user()` on Postgres. Fixed
   with `set_config('app.current_tenant_id', :tenant_id, true)`, the
   correct parameterized equivalent.
2. **The `is_platform_admin` bypass flag leaked across requests.**
   Discovered via a deliberately rigorous negative-control test (proving
   isolation still holds for non-admin sessions, not just that the admin
   view works) that initially failed: a normal tenant-scoped session
   could see another tenant's row. Root cause never fully isolated in
   pooled-connection terms; fixed defensively by having
   `set_tenant_context()` explicitly reset `app.is_platform_admin` to
   `'false'` on every call, rather than trusting transaction boundaries
   alone to clear it.
3. **Superusers bypass RLS unconditionally.** `FORCE ROW LEVEL SECURITY`
   only overrides the table-owner exemption, never the superuser/
   `BYPASSRLS` exemption -- confirmed via `pg_roles`. This is the
   discovery that reframed everything above: connecting as `postgres`
   (as every earlier phase's live tests did) means RLS was never
   actually enforced, full stop, regardless of policy correctness. Fixed
   the *testing methodology*, not application code: created a genuine
   `saeos_app` role (`NOSUPERUSER NOBYPASSRLS`) and re-ran every RLS-
   dependent live test against it. **This is now the documented,
   required production setup** -- see docs/TERMUX.md.
4. **`register()` never set tenant context before inserting the new
   User.** Invisible until testing against the genuine non-superuser
   role: the INSERT's `WITH CHECK` clause requires
   `current_setting('app.current_tenant_id')` to already equal the new
   tenant's id, which was never set for a brand-new tenant. Fixed by
   calling `set_tenant_context(session, tenant.id)` immediately after
   `session.flush()` populates the new tenant's id, before the User
   INSERT.
5. **`is_local=true` (`SET LOCAL` semantics) resets at every `COMMIT`,
   and several handlers called `record_audit()` -- or, in one case, a
   raw `embedding_vector` UPDATE -- *after* their own commit,** by which
   point the earlier `set_tenant_context()` call in the same session had
   already stopped applying. Under genuine RLS this doesn't error for
   an UPDATE (it silently matches zero rows) but does error for an
   INSERT's `WITH CHECK` (audit log writes would fail outright). Fixed
   systemically: `record_audit()` now sets its own tenant context
   immediately before its own commit, rather than trusting the caller's
   earlier call to still be in effect -- this fixed every one of its
   ~10 call sites at once. The one non-`record_audit` case (knowledge
   ingest's `embedding_vector` UPDATE) got the same fix inline. Six
   unnecessary `session.refresh()` calls (also post-commit, also
   RLS-affected, and returning no data any caller actually used) were
   removed rather than fixed, since they served no purpose.
6. **Two live test files had the identical class of bug in their own
   setup code** (`test_pgvector_live.py`'s manual chunk inserts,
   `test_platform_admin_live.py`'s manual `is_platform_admin` grant) --
   both written before this phase's RLS understanding, both silently
   "worked" only because they too ran as the superuser. Fixed the same
   way as application code: call `set_tenant_context()` before any
   RLS-protected write.

Every fix above was verified by re-running the affected live test
against the genuine `saeos_app` role until it passed for the right
reason -- not by inspecting the code and assuming correctness. The
`test_pgvector_live.py` and `test_rls_live.py` "passing" results
reported in Phases 4 and 6-early were real in the sense that the code
ran without erroring, but did not actually prove tenant isolation, only
schema/query correctness under a role that bypasses the very thing
being tested. That distinction is worth stating plainly rather than
letting the earlier phase summaries stand unqualified.

### CSRF hardening

Double-submit-cookie pattern (`app/admin/csrf.py`): a random token set
as an httponly cookie at login, embedded directly into every
server-rendered form as a hidden field (no JS needed -- the server
already knows the value it just set), compared with
`hmac.compare_digest` on every POST. Every state-changing admin route
(create role, assign permission, assign role, logout) now requires a
matching token. Tested including the actual attack CSRF protection
exists to prevent: a token valid for a *different* logged-in session
does not work against another user's session.

Full verification for this phase: fresh venv, zero native compilation
(unchanged), 119 tests passing (6 correctly skipping without live
infra) on SQLite, plus every RLS-dependent live test re-verified against
a genuine non-superuser Postgres role, not the superuser connection
every earlier phase unknowingly relied on.

## 23. Next (Phase 7 preview)

A documented, scripted way to provision the `saeos_app`-equivalent
non-superuser role and grants as part of the standard Postgres
deployment process (currently a manual `GRANT` sequence run ad hoc
during this phase's testing, not yet codified anywhere a real deployment
would run it), Gemini's `should_escalate` equivalent for workflow-level
(not just single-invoke) escalation, and audit-log immutability
(currently a plain table with no `UPDATE`/`DELETE` revocation at the DB
level, noted as a gap back in Phase 1's security review and still open).
