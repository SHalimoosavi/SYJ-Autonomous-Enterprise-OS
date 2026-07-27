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

from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse
from app.ai_gateway.providers.anthropic_provider import AnthropicProvider
from app.ai_gateway.providers.ollama_provider import OllamaProvider
from app.ai_gateway.providers.openrouter_provider import OpenRouterProvider
from app.core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


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

    async def generate(self, department: str, task_type: str, request: AIRequest) -> AIResponse:
        chain = self._resolve_chain(department, task_type)
        last_error: Exception | None = None

        for entry in chain:
            provider_name, model = entry["provider"], entry["model"]
            provider = self._providers.get(provider_name)
            if provider is None:
                logger.warning("gateway.provider_not_registered", provider=provider_name)
                continue
            try:
                return await provider.generate(request, model)
            except Exception as exc:  # noqa: BLE001 — intentional: fall through to next in chain
                logger.warning("gateway.provider_failed", provider=provider_name, model=model, error=str(exc))
                last_error = exc
                continue

        raise AllProvidersFailedError(
            f"All providers in fallback chain exhausted for {department}.{task_type}: {last_error}"
        )


def build_default_gateway() -> AIGateway:
    """Wires up the registry. Add new providers here — one line each."""
    registry: dict[str, AIProvider] = {
        "claude": AnthropicProvider(api_key="${ANTHROPIC_API_KEY}"),
        "ollama": OllamaProvider(),
        "openrouter": OpenRouterProvider(api_key="${OPENROUTER_API_KEY}"),
        # "openai": OpenAIProvider(...),   # add per same pattern
        # "gemini": GeminiProvider(...),
    }
    return AIGateway(settings.AI_ROUTING_CONFIG_PATH, registry)
