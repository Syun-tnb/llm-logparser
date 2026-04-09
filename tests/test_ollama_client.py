from __future__ import annotations

import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from llm_logparser.core.ollama_client import OllamaClient


class OllamaClientTests(unittest.TestCase):
    def test_embeddings_returns_vector(self) -> None:
        client = OllamaClient()

        with patch.object(
            client,
            "_post",
            return_value={"embedding": [0.1, 0.2, 0.3]},
        ) as post_mock:
            result = client.embeddings("nomic-embed-text", "hello")

        self.assertEqual(result, [0.1, 0.2, 0.3])
        post_mock.assert_called_once_with(
            "/api/embeddings",
            {"model": "nomic-embed-text", "prompt": "hello"},
        )

    def test_generate_json_returns_parsed_dict(self) -> None:
        client = OllamaClient()

        with patch.object(
            client,
            "_post",
            return_value={"response": '{"key": "value"}'},
        ):
            result = client.generate_json("llama3.1", "return a JSON object")

        self.assertEqual(result, {"key": "value"})

    def test_generate_json_retries_once_on_parse_failure(self) -> None:
        client = OllamaClient()

        with patch.object(
            client,
            "_post",
            side_effect=[
                {"response": "{invalid"},
                {"response": '{"key": "value"}'},
            ],
        ) as post_mock:
            result = client.generate_json("llama3.1", "return a JSON object")

        self.assertEqual(result, {"key": "value"})
        self.assertEqual(post_mock.call_count, 2)

    def test_generate_json_raises_after_retry_failure(self) -> None:
        client = OllamaClient()

        with patch.object(
            client,
            "_post",
            side_effect=[
                {"response": "{invalid"},
                {"response": "{still invalid"},
            ],
        ) as post_mock:
            with self.assertRaisesRegex(
                RuntimeError,
                "invalid JSON after 2 attempts",
            ):
                client.generate_json("llama3.1", "return a JSON object")

        self.assertEqual(post_mock.call_count, 2)

    def test_post_normalizes_http_error_details(self) -> None:
        client = OllamaClient()
        error = HTTPError(
            url="http://localhost:11434/api/generate",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b'{"error": "model not found"}'),
        )

        with patch(
            "llm_logparser.core.ollama_client.urllib_request.urlopen",
            side_effect=error,
        ):
            with self.assertRaises(RuntimeError) as raised:
                client._post("/api/generate", {"model": "missing"})

        message = str(raised.exception)
        self.assertIn("HTTP 404", message)
        self.assertIn("model not found", message)
        self.assertIn("/api/generate", message)

    def test_post_normalizes_url_error_details(self) -> None:
        client = OllamaClient()

        with patch(
            "llm_logparser.core.ollama_client.urllib_request.urlopen",
            side_effect=URLError("connection refused"),
        ):
            with self.assertRaises(RuntimeError) as raised:
                client._post("/api/embeddings", {"model": "nomic-embed-text"})

        message = str(raised.exception)
        self.assertIn("connection refused", message)
        self.assertIn("/api/embeddings", message)


if __name__ == "__main__":
    unittest.main()
