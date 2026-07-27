import httpx
from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse


class AnthropicProvider(AIProvider):
    name = "claude"

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                json={
                    "model": model,
                    "max_tokens": request.max_tokens,
                    "system": request.system or "",
                    "messages": [{"role": "user", "content": request.prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
            return AIResponse(text=text, provider=self.name, model=model, raw=data)

    async def health_check(self) -> bool:
        return bool(self.api_key)
