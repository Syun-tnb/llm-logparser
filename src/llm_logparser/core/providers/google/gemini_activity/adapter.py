from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ....utils import shorten_id


USER_TITLE_PREFIX = "送信したメッセージ: "
GEMINI_SERVICE_TOKEN = "Gemini"


def get_manifest() -> dict:
    return {
        "schema_version": "1.0",
        "provider": "google",
        "family": "gemini_activity",
        "input_format": "google_takeout_my_activity_json",
        "description": (
            "Adapter for Google Takeout My Activity Gemini records imported as "
            "deterministic event-scoped mini-threads."
        ),
        "expected_top_keys": ["title", "time", "products", "safeHtmlItem"],
        "id_fields": ["conversation_id", "message_id"],
    }


def get_policy() -> dict:
    return {
        "allow_partial_parse": True,
        "timestamp_fields": ["time"],
        "safe_null_handling": True,
        "event_first_import": True,
        "synthetic_mini_threads": True,
    }


def _load_json_payload(path: Path, logger: logging.Logger) -> Any | None:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("skip unreadable JSON file %s: %s", path, exc)
        return None


def _contains_gemini_label(value: Any) -> bool:
    return isinstance(value, str) and GEMINI_SERVICE_TOKEN in value


def _products_contain_gemini(products: Any) -> bool:
    return isinstance(products, list) and any(_contains_gemini_label(item) for item in products)


def _is_gemini_activity_record(record: Any) -> bool:
    if not isinstance(record, dict):
        return False

    if not isinstance(record.get("title"), str) or not record.get("title"):
        return False
    if not isinstance(record.get("time"), str) or not record.get("time"):
        return False
    if not isinstance(record.get("products"), list):
        return False

    return _contains_gemini_label(record.get("header")) or _products_contain_gemini(record.get("products"))


def is_gemini_activity_export(payload: Any) -> bool:
    if not isinstance(payload, list) or not payload:
        return False

    sample = payload[: min(len(payload), 10)]
    if not all(
        isinstance(item, dict)
        and "title" in item
        and "time" in item
        and "products" in item
        for item in sample
    ):
        return False

    return any(
        isinstance(item, dict)
        and "safeHtmlItem" in item
        and _is_gemini_activity_record(item)
        for item in sample
    )


def iter_input_records(
    input_path: Path,
    logger: logging.Logger,
):
    target = input_path.expanduser()
    candidates = [target]
    if target.is_dir():
        candidates = sorted(path for path in target.rglob("*.json") if path.is_file())

    for candidate in candidates:
        payload = _load_json_payload(candidate, logger)
        if is_gemini_activity_export(payload):
            yield payload, str(candidate)


def _to_epoch_ms(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 1_000_000_000_000:
            return int(numeric)
        return int(numeric * 1000)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            numeric = float(text)
        except ValueError:
            numeric = None
        if numeric is not None:
            if numeric > 1_000_000_000_000:
                return int(numeric)
            return int(numeric * 1000)
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return int(datetime.fromisoformat(text).timestamp() * 1000)
        except ValueError:
            return None

    return None


def extract_user_text(title: Any) -> str:
    if not isinstance(title, str):
        return ""
    text = title
    if text.startswith(USER_TITLE_PREFIX):
        text = text[len(USER_TITLE_PREFIX):]
    return text.strip()


class _HTMLTextExtractor(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "dl",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        del attrs
        normalized = tag.lower()
        if normalized == "br":
            self._parts.append("\n")
            return
        if normalized == "li":
            self._parts.append("\n- ")
            return
        if normalized in self._BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized == "li":
            self._parts.append("\n")
            return
        if normalized in self._BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts).replace("\r\n", "\n").replace("\r", "\n")
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        raw = re.sub(r"\n[ \t]+", "\n", raw)
        raw = "\n".join(re.sub(r"[ \t]{2,}", " ", line).strip() for line in raw.split("\n"))
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.get_text()


def _extract_html_strings(safe_html_items: Any) -> list[str]:
    if not isinstance(safe_html_items, list):
        return []
    items: list[str] = []
    for item in safe_html_items:
        if not isinstance(item, dict):
            continue
        html = item.get("html")
        if isinstance(html, str) and html.strip():
            items.append(html)
    return items


def extract_assistant_text(safe_html_items: Any) -> str:
    texts = [html_to_text(html) for html in _extract_html_strings(safe_html_items)]
    texts = [text for text in texts if text]
    return "\n\n".join(texts)


def _event_seed(record: dict[str, Any]) -> str:
    seed_payload = {
        "header": record.get("header"),
        "title": record.get("title"),
        "time": record.get("time"),
        "products": record.get("products"),
        "activityControls": record.get("activityControls"),
        "safeHtmlItem": record.get("safeHtmlItem"),
        "source_index": record.get("__llp_source_index"),
    }
    return json.dumps(seed_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def synthetic_conversation_id(record: dict[str, Any]) -> str:
    return shorten_id(f"google:gemini_activity:event:{_event_seed(record)}")


def synthetic_message_id(record: dict[str, Any], role: str) -> str:
    return shorten_id(f"google:gemini_activity:{role}:{_event_seed(record)}")


def _event_sort_key(record: dict[str, Any]) -> tuple[bool, int, str]:
    ts = _to_epoch_ms(record.get("time"))
    return (
        ts is None,
        ts or 0,
        synthetic_conversation_id(record),
    )


def expand_input_records(raw: Any) -> list[dict]:
    if not is_gemini_activity_export(raw):
        return []

    events: list[dict] = []
    for index, record in enumerate(raw):
        if not _is_gemini_activity_record(record):
            continue
        candidate = dict(record)
        candidate["__llp_source_index"] = index
        if extract_user_text(candidate.get("title")) or extract_assistant_text(candidate.get("safeHtmlItem")):
            events.append(candidate)

    events.sort(key=_event_sort_key)
    return events


def adapter(record: Any, *, source: str | None = None) -> list[dict]:
    del source

    if not _is_gemini_activity_record(record):
        return []

    ts = _to_epoch_ms(record.get("time"))
    if ts is None:
        return []

    user_text = extract_user_text(record.get("title"))
    assistant_text = extract_assistant_text(record.get("safeHtmlItem"))
    if not user_text and not assistant_text:
        return []

    conversation_id = synthetic_conversation_id(record)
    created_at = record.get("time")
    meta = {
        "service": "gemini_activity",
        "header": record.get("header"),
        "raw_title": record.get("title"),
        "products": record.get("products"),
        "activityControls": record.get("activityControls"),
        "safeHtmlItem": record.get("safeHtmlItem"),
    }

    out: list[dict] = []
    user_message_id: str | None = None

    if user_text:
        user_message_id = synthetic_message_id(record, "user")
        out.append(
            {
                "conversation_id": conversation_id,
                "conv_id": conversation_id,
                "message_id": user_message_id,
                "id": user_message_id,
                "role": "user",
                "ts": ts,
                "created_at": created_at,
                "content": {"content_type": "text", "parts": [user_text]},
                "text": user_text,
                "meta": meta,
            }
        )

    if assistant_text:
        assistant_message_id = synthetic_message_id(record, "assistant")
        assistant_doc = {
            "conversation_id": conversation_id,
            "conv_id": conversation_id,
            "message_id": assistant_message_id,
            "id": assistant_message_id,
            "role": "assistant",
            "ts": ts,
            "created_at": created_at,
            "content": {
                "content_type": "text",
                "parts": [assistant_text],
            },
            "text": assistant_text,
            "meta": meta,
        }
        if user_message_id is not None:
            assistant_doc["parent_id"] = user_message_id
        out.append(assistant_doc)

    return out


def get_adapter():
    return adapter


def get_input_records():
    return iter_input_records


def get_record_expander():
    return expand_input_records
