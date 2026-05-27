from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=10.0),
            limits=httpx.Limits(max_connections=10),
        )

    async def embed_batch(
        self, model: str, texts: list[str], batch_size: int = 32
    ) -> list[list[float]]:
        """Embed texts using /api/embed (batch endpoint)."""
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = await self._client.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": batch},
            )
            resp.raise_for_status()
            all_embeddings.extend(resp.json()["embeddings"])
        return all_embeddings

    async def chat_stream(
        self,
        model: str,
        messages: list[dict],
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        """Stream chat, yielding text deltas."""
        payload: dict = {"model": model, "messages": messages, "stream": True}
        if options:
            payload["options"] = options
        async with self._client.stream(
            "POST", f"{self.base_url}/api/chat", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("done"):
                    return
                content = data.get("message", {}).get("content", "")
                if content:
                    yield content

    async def chat(
        self,
        model: str,
        messages: list[dict],
        options: dict | None = None,
    ) -> str:
        """Non-streaming chat, returns full response."""
        payload: dict = {"model": model, "messages": messages, "stream": False}
        if options:
            payload["options"] = options
        resp = await self._client.post(f"{self.base_url}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    async def vision_describe(self, model: str, image_path: Path) -> str:
        """Describe an image using a vision model."""
        image_b64 = base64.b64encode(image_path.read_bytes()).decode()
        messages = [
            {
                "role": "user",
                "content": "Describe this image in detail for document search purposes. Include any text, diagrams, charts, or visual content.",
                "images": [image_b64],
            }
        ]
        return await self.chat(model, messages)

    async def list_models(self) -> list[dict]:
        resp = await self._client.get(f"{self.base_url}/api/tags")
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> OllamaClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
