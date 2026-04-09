from __future__ import annotations

import json
from urllib import error as urllib_error
from urllib import request as urllib_request


class OllamaClient:
    """Unified stdlib HTTP client for optional Ollama-backed analysis."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        """Send a JSON POST request to Ollama and return the decoded payload."""
        request = urllib_request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib_request.urlopen(request, timeout=self.timeout) as response:
                raw_response = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            error_message = self._decode_error_message(exc)
            suffix = f": {error_message}" if error_message else ""
            raise RuntimeError(
                f"Ollama request failed for {path}: HTTP {exc.code}{suffix}"
            ) from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(
                f"Ollama request failed for {path}: {exc.reason}"
            ) from exc

        try:
            decoded = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Ollama response for {path} was not valid JSON"
            ) from exc

        if not isinstance(decoded, dict):
            raise RuntimeError(
                f"Ollama response for {path} was not a JSON object"
            )
        return decoded

    def embeddings(self, model: str, prompt: str) -> list[float]:
        """Return a single embedding vector from Ollama's embeddings API."""
        payload = self._post(
            "/api/embeddings",
            {
                "model": model,
                "prompt": prompt,
            },
        )
        embedding = payload.get("embedding")
        if not isinstance(embedding, list):
            raise RuntimeError(
                "Ollama response for /api/embeddings is missing 'embedding'"
            )
        if any(not isinstance(value, (int, float)) for value in embedding):
            raise RuntimeError(
                "Ollama response for /api/embeddings contained a non-numeric value"
            )
        return [float(value) for value in embedding]

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        response_format: str | None = None,
        options: dict[str, object] | None = None,
    ) -> str:
        """Return raw response text from Ollama's generate API."""
        payload: dict[str, object] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if response_format is not None:
            payload["format"] = response_format
        if options:
            payload["options"] = options

        response_payload = self._post("/api/generate", payload)
        response_text = response_payload.get("response")
        if not isinstance(response_text, str) or not response_text.strip():
            raise RuntimeError(
                "Ollama response for /api/generate is missing 'response'"
            )
        return response_text.strip()

    def generate_json(self, model: str, prompt: str) -> dict:
        """Generate a structured JSON object from Ollama with one retry."""
        for attempt in range(2):
            response_text = self.generate_text(
                model,
                prompt,
                response_format="json",
            )
            try:
                decoded = json.loads(response_text)
            except json.JSONDecodeError as exc:
                if attempt == 0:
                    continue
                raise RuntimeError(
                    "Ollama generate_json returned invalid JSON after 2 attempts"
                ) from exc

            if not isinstance(decoded, dict):
                raise RuntimeError(
                    "Ollama generate_json expected a JSON object response"
                )
            return decoded

        raise RuntimeError("Ollama generate_json retry loop exited unexpectedly")

    @staticmethod
    def _decode_error_message(exc: urllib_error.HTTPError) -> str:
        """Extract a readable error message from an HTTP error body."""
        try:
            body = exc.read().decode("utf-8").strip()
        except Exception:
            return ""

        if not body:
            return ""

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return body

        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, str) and error.strip():
                return error.strip()
        return body
