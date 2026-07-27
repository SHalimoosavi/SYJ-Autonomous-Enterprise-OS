import httpx
from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse


class OpenRouterProvider(AIProvider):
    """Single provider that fronts many models (OpenAI, Gemini, Llama, Mistral,
    etc.) via one OpenAI-compatible API — useful as a broad fallback tier."""
    name = "openrouter"

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1"):
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

    async def health_check(self) -> bool:
        return bool(self.api_key)
