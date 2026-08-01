# Termux / Android Compatibility Notes

## Why FastAPI + Pydantic v2 were removed

The original Phase 1 skeleton used FastAPI + pydantic-settings. Installing
`fastapi` unconditionally pulls in `pydantic>=2.7`, which depends on
`pydantic-core` — a Rust extension built with PyO3. PyPI publishes
`pydantic-core` wheels for manylinux/musllinux/macOS/Windows, but **never**
for Android (Termux uses Bionic libc, not glibc), at any Python version.
So on Termux, `pip install fastapi` always falls through to building
`pydantic-core` from source, which requires:
- a Rust toolchain (`pkg install rust`, several hundred MB, slow first build)
- enough free memory to survive the build (frequently OOMs on-device)
- a PyO3 version that actually supports the installed Python version

The original failure (`PyO3 0.22.2 only supports Python <=3.13`) was a
symptom of an old `pydantic` pin (2.9.2) whose `pydantic-core` (2.23.4)
predates Python 3.14 support. Bumping the pin doesn't remove the
underlying problem — it's still a from-source Rust build on every Termux
install, which is exactly the "unnecessary native compilation" this
project needs to avoid. So instead of chasing pydantic-core compatibility,
**Pydantic and FastAPI were removed from the dependency tree entirely**:

| Removed | Replaced with | Why it's now compile-free |
|---|---|---|
| `fastapi` | `starlette` directly | Starlette has no pydantic dependency — only `anyio` + `typing_extensions`, both pure Python |
| `pydantic-settings` | `app/core/config.py` (stdlib `dataclasses` + `.env` parsing) | Zero dependencies |
| `python-jose[cryptography]` | Hand-rolled HS256 JWT in `app/core/security.py` (stdlib `hmac`/`hashlib`) | HS256 needs no asymmetric crypto library at all |
| `passlib[bcrypt]` | `passlib` with `pbkdf2_sha256` scheme only | `bcrypt>=4.0` is itself a Rust/PyO3 package (same class of problem); pbkdf2_sha256 is implemented via stdlib `hashlib.pbkdf2_hmac` |
| `uvicorn[standard]` | plain `uvicorn` | The `[standard]` extra pulls in `uvloop`/`httptools` (C/Cython, no Android wheels); plain uvicorn uses asyncio's default loop + pure-Python `h11` |

None of the department/gateway/tenancy/RBAC logic imports pydantic, so
this was a boundary-layer swap, not a rewrite of the business logic.

## Supported Python version

**Recommended: 3.12** (Termux's official `pkg install python` version).
**Supported range: 3.11–3.13.**
**3.14: not currently recommended.** Nothing in this project's own
dependency list blocks 3.14 (we verified `passlib` degrades gracefully
when the stdlib `crypt` module — removed in 3.13+ — is absent). The
caution is about the wider ecosystem: several transitive/optional tools
you may add later (build backends, linters, other libraries) are still
catching up to 3.14 as of this writing, and Termux's own `pkg install
python` may lag the newest CPython release by a version or two. If your
Termux `python` package is already on 3.14, this project will very likely
still work — it just hasn't been exhaustively verified here.

## One Termux `pkg` step required before `pip install`

SQLAlchemy's async engine (`AsyncEngine`, used throughout this project)
requires `greenlet` at runtime. `greenlet` is a C extension with no
Android wheel on PyPI, and building it from source on Termux is
unreliable. Termux's own package repository ships a working precompiled
build:

```bash
pkg install python-greenlet
pip install -r requirements.txt
```

Installing it via `pkg` first means pip sees the requirement already
satisfied and never attempts to build it. This is the same reason we
prefer `pkg install python-cryptography` over `pip install cryptography`
if a future phase needs it — Termux maintains real ARM64 binaries for a
number of common C-extension Python packages; PyPI does not.

## Verified working (this repo, this Phase)

Full clean-room pass in a fresh virtualenv (simulating a first-time
Termux install):

- `pip install -r requirements.txt`: zero "Building wheel" / source-build
  steps for any package — confirmed by grepping install logs.
- `pip list` confirms `pydantic`, `pydantic-core`, `fastapi`, `bcrypt`,
  and `python-jose` are absent from the installed tree entirely.
- `pytest -v`: 11/11 passing.
- `uvicorn app.main:app` boots and serves real HTTP requests
  (`/health`, `/api/v1/health` with and without a tenant header).
- `alembic revision --autogenerate` + `alembic upgrade head` run
  end-to-end against SQLite and create all 7 expected tables.

This pass also caught two real bugs unrelated to the compiled-dependency
issue, both now fixed and covered by tests: `Tenant.plan` was defaulting
to a non-existent enum member (`TenantPlan.TRIAL` — that value only
exists on `TenantStatus`), and the tenant-resolution middleware's
subdomain fallback misread bare `IP:port` hosts (e.g. `127.0.0.1:8000`,
used in local dev) as a 4-label subdomain and let unscoped requests
through instead of rejecting them.

## Production Deployment tasks (require Linux/cloud host)

- **CRITICAL: connect to Postgres as a non-superuser, non-`BYPASSRLS`
  role, or row-level security provides zero protection.** Discovered in
  Phase 6: Postgres superusers bypass RLS unconditionally, regardless of
  `FORCE ROW LEVEL SECURITY` -- this is documented Postgres behavior,
  not a bug in this project's policies, but it means connecting the
  application as `postgres` (or any role with `rolsuper`/`rolbypassrls`)
  silently defeats every tenant-isolation guarantee this project's RLS
  migrations are supposed to provide. Create a dedicated role before
  deploying:
  ```sql
  CREATE ROLE saeos_app LOGIN PASSWORD '...' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  GRANT USAGE ON SCHEMA public TO saeos_app;
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO saeos_app;
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO saeos_app;
  ```
  Run this **after** `alembic upgrade head` has created the tables (the
  grants only apply to tables that already exist when the `GRANT ...
  ALL TABLES` statement runs); migrations themselves still need a
  privileged role (superuser or table owner) to run `CREATE POLICY`/
  `ALTER TABLE ... FORCE ROW LEVEL SECURITY`, so a typical setup runs
  migrations as one role and points the running application's
  `DATABASE_URL` at `saeos_app` (or equivalent) instead.

- **Postgres (`asyncpg` driver)**: building the C extension on Termux
  ARM64 is unreliable. Develop against SQLite locally; point at Postgres
  only on the target deployment host, where prebuilt manylinux wheels
  install instantly.
- **Redis**: no maintained Termux `redis-server` package; the event bus
  and Celery broker must point at a remote Redis instance, or this layer
  stays on `EVENT_BUS_BACKEND=memory` until deployment.
- **Celery workers**: run on the production host, not in the Termux dev loop.
- **FastAPI (optional, production-only)**: if deploying to a glibc host,
  FastAPI + pydantic v2 can be layered back on top of the existing
  Starlette routes for automatic OpenAPI docs — `pydantic-core` installs
  as a prebuilt wheel there with zero compilation, so none of the
  Termux-specific concerns above apply.
- **Docker-based deployment**: build/push images from a CI runner or a
  Linux box; Termux is for code authorship, not container builds.
