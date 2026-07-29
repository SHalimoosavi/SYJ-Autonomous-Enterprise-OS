"""
The AI Gateway: the ONLY component department agents are allowed to call
for inference. Resolves provider + model from routing.yaml per
department/task, tries each entry in the fallback chain in order, and
returns a uniform AIResponse regardless of which provider ultimately served
the request.

This is the loose-coupling boundary requested: to add a new provider,
implement AIProvider and register it in PROVIDER_REGISTRY below. To change
which model handles a task, edit routing.yaml. Agent code never changes.
"""
import yaml
import structlog
from functools import lru_cache

from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse
from app.ai_gateway.providers.anthropic_provider import AnthropicProvider
from app.ai_gateway.providers.ollama_provider import OllamaProvider
from app.ai_gateway.providers.openrouter_provider import OpenRouterProvider
from app.ai_gateway.providers.openai_provider import OpenAIProvider
from app.ai_gateway.providers.voyage_provider import VoyageProvider
from app.core.config import get_settings

logger = structlog.get_logger()


class AllProvidersFailedError(RuntimeError):
    pass


class AIGateway:
    def __init__(self, routing_config_path: str, provider_registry: dict[str, AIProvider]):
        with open(routing_config_path) as f:
            self._routing = yaml.safe_load(f)
        self._providers = provider_registry

    def _resolve_chain(self, department: str, task_type: str) -> list[dict]:
        dept_cfg = self._routing.get("departments", {}).get(department, {})
        chain = dept_cfg.get(task_type) or dept_cfg.get("default")
        return chain or self._routing["default_fallback_chain"]

    async def _walk_chain(self, chain: list[dict], call, failure_label: str):
        """Shared fallback-chain walker used by both generate() and embed():
        try each [provider, model] entry in order, skip unregistered
        providers, catch any failure (including a provider that simply
        doesn't implement the operation, e.g. embed() on Anthropic) and
        fall through, raising AllProvidersFailedError only once every
        entry has been tried."""
        last_error: Exception | None = None
        for entry in chain:
            provider_name, model = entry["provider"], entry["model"]
            provider = self._providers.get(provider_name)
            if provider is None:
                logger.warning("gateway.provider_not_registered", provider=provider_name)
                continue
            try:
                return await call(provider, model)
            except Exception as exc:  # noqa: BLE001 — intentional: fall through to next in chain
                logger.warning("gateway.provider_failed", provider=provider_name, model=model, error=str(exc))
                last_error = exc
                continue
        raise AllProvidersFailedError(f"All providers in fallback chain exhausted for {failure_label}: {last_error}")

    async def generate(self, department: str, task_type: str, request: AIRequest) -> AIResponse:
        chain = self._resolve_chain(department, task_type)
        return await self._walk_chain(chain, lambda p, m: p.generate(request, m), f"{department}.{task_type}")

    async def embed(self, text: str) -> list[float]:
        chain = self._routing.get("embedding_fallback_chain") or []
        if not chain:
            raise AllProvidersFailedError("No embedding_fallback_chain configured in routing.yaml")
        return await self._walk_chain(chain, lambda p, m: p.embed(text, m), "embedding")


def build_default_gateway() -> AIGateway:
    """
    Wires up the registry from real settings (see app/core/config.py).
    Providers requiring a key are only registered if that key is actually
    present -- an unregistered provider is skipped by the fallback chain
    (logged as a warning) rather than raising with a bogus/empty key.
    Add a new provider here in one line, same pattern.

    Calls get_settings() fresh rather than using a module-level capture:
    get_settings() is itself lru_cache'd (see app/core/config.py), so this
    is still cheap, but a module-level `settings = get_settings()` here
    would freeze whatever was true at import time -- invisible to config
    changes (e.g. in tests that clear the settings cache to simulate a
    key being added/removed) for the lifetime of the process.
    """
    settings = get_settings()
    registry: dict[str, AIProvider] = {
        "ollama": OllamaProvider(base_url=settings.OLLAMA_BASE_URL),  # no key required
    }
    if settings.ANTHROPIC_API_KEY:
        registry["claude"] = AnthropicProvider(api_key=settings.ANTHROPIC_API_KEY)
    if settings.OPENROUTER_API_KEY:
        registry["openrouter"] = OpenRouterProvider(api_key=settings.OPENROUTER_API_KEY)
    if settings.OPENAI_API_KEY:
        registry["openai"] = OpenAIProvider(api_key=settings.OPENAI_API_KEY)
    if settings.VOYAGE_API_KEY:
        registry["voyage"] = VoyageProvider(api_key=settings.VOYAGE_API_KEY)
    # if settings.GEMINI_API_KEY: registry["gemini"] = GeminiProvider(...)
    return AIGateway(settings.AI_ROUTING_CONFIG_PATH, registry)


@lru_cache
def get_gateway() -> AIGateway:
    """Process-wide singleton so routing.yaml is parsed once, not per-request."""
    return build_default_gateway()
