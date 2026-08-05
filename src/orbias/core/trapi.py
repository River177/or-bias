"""Single TRAPI adapter used by model-calling workflows."""

from __future__ import annotations

import threading
from typing import Any


class TrapiClient:
    """Own TRAPI authentication and requests without client token limits."""

    _clients: dict[tuple[str, str, int], Any] = {}
    _lock = threading.Lock()

    def __init__(self, instance: str, api_version: str, *, timeout_seconds: int):
        self.instance = instance
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds

    def _client(self) -> Any:
        key = (self.instance, self.api_version, self.timeout_seconds)
        with self._lock:
            if key not in self._clients:
                from openai import AzureOpenAI
                from trapi import TrapiClient as AuthClient

                auth = AuthClient()
                token = auth.get_token()
                endpoint = f"https://trapi.research.microsoft.com/{self.instance}"
                self._clients[key] = AzureOpenAI(
                    api_key=token,
                    azure_endpoint=endpoint,
                    api_version=self.api_version,
                    timeout=self.timeout_seconds,
                )
            return self._clients[key]

    def chat(
        self,
        *,
        deployment: str,
        system: str,
        user: str,
        reasoning_effort: str | None = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any]]:
        forbidden = {"max" + "_tokens", "max" + "_completion_tokens"}
        overlap = forbidden.intersection(kwargs)
        if overlap:
            raise ValueError(f"Client token limits are forbidden: {sorted(overlap)}")
        request: dict[str, Any] = {
            "model": deployment,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        }
        if reasoning_effort is not None:
            request["reasoning_effort"] = reasoning_effort
        response = self._client().chat.completions.create(**request)
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Empty model response")
        metadata = response.model_dump() if hasattr(response, "model_dump") else {}
        return content, metadata

