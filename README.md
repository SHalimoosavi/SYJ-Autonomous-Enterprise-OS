# SYJ Autonomous Enterprise OS (SAEOS) — Phase 1 Skeleton

Multi-tenant, provider-agnostic AI Company Operating System.
Foundation layer: tenancy, auth/RBAC, AI Gateway, event bus, audit log,
and one reference department (Executive Office).

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

Verified: clean virtualenv install produces zero source/native builds for
any package, and 10/10 tests pass. See `docs/TERMUX.md` for the full
verification log and per-dependency rationale.

## What's real vs. stubbed in this skeleton

| Component | Status |
|---|---|
| AI Gateway + provider fallback chain | Working, tested |
| Anthropic / Ollama / OpenRouter providers | Working (need API keys / local Ollama) |
| Tenant middleware + isolation choke point | Working, tested |
| Auth models, JWT issuing (stdlib HS256), password hashing (pbkdf2_sha256) | Working, tested |
| RBAC (`require_permission`) | Interface complete, `get_current_user` wiring is a Phase 1.1 task |
| Event bus (in-memory) | Working |
| Event bus (Redis) | Stubbed — Production Deployment task |
| Executive Office agent | Reference implementation |
| Other 25 departments | Follow the same `DepartmentAgent` pattern — Phase 2 |

See `docs/ARCHITECTURE.md` for the full Phase 1 design doc and
`docs/TERMUX.md` for platform compatibility notes and the Termux
install-failure fix history.
