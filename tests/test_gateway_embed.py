"""AIGateway.embed() -- fallback chain behavior for embeddings, same
pattern as tests/test_ai_gateway.py's generate() tests."""
import pytest

from app.ai_gateway.gateway import AIGateway, AllProvidersFailedError
from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse


class FakeEmbeddingProvider(AIProvider):
    name = "fake_embed"

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True

    async def embed(self, text: str, model: str) -> list[float]:
        return [float(len(text)), 0.5, 0.25]


class FakeNoEmbedProvider(AIProvider):
    """Simulates Anthropic/OpenRouter: generate() works, embed() doesn't."""
    name = "fake_no_embed"

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        return AIResponse(text="ok", provider=self.name, model=model)

    async def health_check(self) -> bool:
        return True
    # embed() not overridden -> raises NotImplementedError from the base class


@pytest.fixture
def routing_file(tmp_path):
    content = """
default_fallback_chain:
  - provider: fake_embed
    model: test-model
embedding_fallback_chain:
  - provider: fake_no_embed
    model: irrelevant
  - provider: fake_embed
    model: test-embed-model
"""
    p = tmp_path / "routing.yaml"
    p.write_text(content)
    return str(p)


@pytest.mark.asyncio
async def test_embed_falls_back_past_a_provider_that_doesnt_support_it(routing_file):
    gateway = AIGateway(routing_file, {"fake_embed": FakeEmbeddingProvider(), "fake_no_embed": FakeNoEmbedProvider()})
    result = await gateway.embed("hello world")
    assert result == [11.0, 0.5, 0.25]


@pytest.mark.asyncio
async def test_embed_raises_when_no_embedding_chain_configured(tmp_path):
    p = tmp_path / "routing.yaml"
    p.write_text("default_fallback_chain:\n  - provider: fake_embed\n    model: m\n")
    gateway = AIGateway(str(p), {"fake_embed": FakeEmbeddingProvider()})
    with pytest.raises(AllProvidersFailedError):
        await gateway.embed("hello")


@pytest.mark.asyncio
async def test_embed_raises_when_all_embedding_providers_fail(routing_file):
    gateway = AIGateway(routing_file, {"fake_no_embed": FakeNoEmbedProvider()})
    with pytest.raises(AllProvidersFailedError):
        await gateway.embed("hello")
