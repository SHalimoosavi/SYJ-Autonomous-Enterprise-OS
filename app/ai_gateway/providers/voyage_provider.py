"""Voyage AI: embeddings only -- no chat-completion API, so generate()
correctly raises (inherited base behavior would too, but this is
explicit) rather than silently doing nothing useful. Voyage is
Anthropic's recommended embedding partner, which is why it's the second
entry in routing.yaml's embedding_fallback_chain after Ollama."""
import httpx
from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse


class VoyageProvider(AIProvider):
    name = "voyage"

    def __init__(self, api_key: str, base_url: str = "https://api.voyageai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        raise NotImplementedError("Voyage AI is an embeddings-only provider; use it in embedding_fallback_chain")

    async def embed(self, text: str, model: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": model, "input": [text]},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]

    async def health_check(self) -> bool:
        return bool(self.api_key)
