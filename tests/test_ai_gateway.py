"""
Tests the loose-coupling contract: gateway resolves provider/model purely
from routing.yaml, falls back correctly when a provider fails, and agent
code never needs to know which provider actually served the request.
"""
import pytest

from app.ai_gateway.gateway import AIGateway, AllProvidersFailedError
from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse


class FakeFailingProvider(AIProvider):
    name = "failing"

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        raise ConnectionError("simulated outage")

    async def health_check(self) -> bool:
        return False


class FakeWorkingProvider(AIProvider):
    name = "working"

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        return AIResponse(text=f"echo:{request.prompt}", provider=self.name, model=model)

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def routing_file(tmp_path):
    content = """
default_fallback_chain:
  - provider: working
    model: test-model
departments:
  engineering:
    code_generation:
      - provider: failing
        model: broken-model
      - provider: working
        model: backup-model
"""
    p = tmp_path / "routing.yaml"
    p.write_text(content)
    return str(p)


@pytest.mark.asyncio
async def test_falls_back_to_next_provider_on_failure(routing_file):
    gateway = AIGateway(routing_file, {"failing": FakeFailingProvider(), "working": FakeWorkingProvider()})
    response = await gateway.generate("engineering", "code_generation", AIRequest(prompt="hello"))
    assert response.provider == "working"
    assert response.model == "backup-model"


@pytest.mark.asyncio
async def test_uses_default_chain_for_unmapped_task(routing_file):
    gateway = AIGateway(routing_file, {"working": FakeWorkingProvider()})
    response = await gateway.generate("marketing", "unmapped_task", AIRequest(prompt="hi"))
    assert response.provider == "working"
    assert response.model == "test-model"


@pytest.mark.asyncio
async def test_raises_when_all_providers_fail(routing_file):
    gateway = AIGateway(routing_file, {"failing": FakeFailingProvider()})
    with pytest.raises(AllProvidersFailedError):
        await gateway.generate("engineering", "code_generation", AIRequest(prompt="hi"))
