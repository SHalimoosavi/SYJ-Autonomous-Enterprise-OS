"""OpenAI provider: both generate() (chat completions) and embed()
(embeddings endpoint). This fills in the "add per same pattern" comment
left in gateway.py since Phase 2 -- Settings already had OPENAI_API_KEY,
nothing used it until now."""
import httpx
from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": [
                        *([{"role": "system", "content": request.system}] if request.system else []),
                        {"role": "user", "content": request.prompt},
                    ],
                    "max_tokens": request.max_tokens,
                    "temperature": request.temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return AIResponse(text=text, provider=self.name, model=model, raw=data)

    async def embed(self, text: str, model: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": model, "input": text},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]

    async def health_check(self) -> bool:
        return bool(self.api_key)
