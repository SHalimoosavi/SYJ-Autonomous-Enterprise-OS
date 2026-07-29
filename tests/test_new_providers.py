"""
OpenAI and Voyage provider registration + interface contracts. Real
network calls to these providers aren't exercised (same standard as the
Anthropic/OpenRouter providers since Phase 1 -- no live credentials in
this environment); what's verified is that they're correctly registered
only when their key is configured, and that Voyage's generate() fails
loud and clear rather than silently.
"""
import os

import pytest

from app.ai_gateway.providers.openai_provider import OpenAIProvider
from app.ai_gateway.providers.voyage_provider import VoyageProvider
from app.ai_gateway.providers.base import AIRequest


@pytest.mark.asyncio
async def test_voyage_generate_raises_not_implemented():
    provider = VoyageProvider(api_key="fake-key")
    with pytest.raises(NotImplementedError):
        await provider.generate(AIRequest(prompt="hi"), model="voyage-3-lite")


@pytest.mark.asyncio
async def test_voyage_health_check_reflects_key_presence():
    assert await VoyageProvider(api_key="").health_check() is False
    assert await VoyageProvider(api_key="a-key").health_check() is True


@pytest.mark.asyncio
async def test_openai_health_check_reflects_key_presence():
    assert await OpenAIProvider(api_key="").health_check() is False
    assert await OpenAIProvider(api_key="a-key").health_check() is True


def test_gateway_only_registers_providers_with_keys_configured(monkeypatch):
    """The actual bug class from Phase 2 (a provider registered with a
    non-functional placeholder key) -- verified here for the two new
    providers the same way it should have been for the original three."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("VOYAGE_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        from app.ai_gateway.gateway import build_default_gateway
        gateway = build_default_gateway()
        # ollama is always registered (no key required); nothing else should be.
        assert "ollama" in gateway._providers
        assert "openai" not in gateway._providers
        assert "voyage" not in gateway._providers
        assert "claude" not in gateway._providers
        assert "openrouter" not in gateway._providers
    finally:
        get_settings.cache_clear()


def test_gateway_registers_openai_and_voyage_when_keys_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-test")
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-fake-for-test")

    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        from app.ai_gateway.gateway import build_default_gateway
        gateway = build_default_gateway()
        assert "openai" in gateway._providers
        assert "voyage" in gateway._providers
    finally:
        get_settings.cache_clear()
