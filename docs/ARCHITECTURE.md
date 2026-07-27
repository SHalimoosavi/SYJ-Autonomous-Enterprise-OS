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

## 11. Next (Phase 2 preview)

Wire `_get_current_user` + login endpoint, implement remaining 25
departments on the `DepartmentAgent` pattern, add RAG/vector DB
abstraction, wire Celery for async department workflows, and start the
dashboard API surface (CEO Briefing, KPIs, Approval Queue).
