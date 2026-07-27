"""
Every AI provider implements this interface. Department agents NEVER see
provider-specific code — they only ever talk to the Gateway (gateway.py),
which resolves the provider from routing.yaml at call time.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AIRequest:
    prompt: str
    system: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.7
    tools: list[dict] | None = None


@dataclass
class AIResponse:
    text: str
    provider: str
    model: str
    raw: dict | None = None


class AIProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def generate(self, request: AIRequest, model: str) -> AIResponse: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
