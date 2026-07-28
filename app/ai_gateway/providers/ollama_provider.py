import httpx
from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse


class OllamaProvider(AIProvider):
    """Local models (Qwen, DeepSeek, Llama, Gemma, Phi, Mistral) via Ollama.
    Runs fine on Termux with `pkg install ollama` on supported devices, or
    points at a remote Ollama host in production."""
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": request.prompt,
                    "system": request.system or "",
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return AIResponse(text=data.get("response", ""), provider=self.name, model=model, raw=data)

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def embed(self, text: str, model: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": model, "prompt": text},
            )
            resp.raise_for_status()
            data = resp.json()
            embedding = data.get("embedding")
            if not embedding:
                raise ValueError(f"Ollama returned no embedding for model {model}")
            return embedding
