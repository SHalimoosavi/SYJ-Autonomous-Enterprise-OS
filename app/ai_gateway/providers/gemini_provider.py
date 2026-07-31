"""Google Gemini provider: both generate() (generateContent) and embed()
(embedContent). Settings.GEMINI_API_KEY has existed unused since Phase 2
-- this is what finally uses it, same pattern as OpenAI/Voyage."""
import httpx
from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta"):
        self.api_key = api_key
        self.base_url = base_url

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        async with httpx.AsyncClient(timeout=60) as client:
            payload = {
                "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
                "generationConfig": {"maxOutputTokens": request.max_tokens, "temperature": request.temperature},
            }
            if request.system:
                payload["systemInstruction"] = {"parts": [{"text": request.system}]}

            resp = await client.post(
                f"{self.base_url}/models/{model}:generateContent",
                params={"key": self.api_key},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            text = "".join(
                part.get("text", "")
                for part in data["candidates"][0]["content"]["parts"]
            )
            return AIResponse(text=text, provider=self.name, model=model, raw=data)

    async def embed(self, text: str, model: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/models/{model}:embedContent",
                params={"key": self.api_key},
                json={"model": f"models/{model}", "content": {"parts": [{"text": text}]}},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["embedding"]["values"]

    async def health_check(self) -> bool:
        return bool(self.api_key)
