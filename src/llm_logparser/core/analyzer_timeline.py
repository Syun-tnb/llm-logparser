from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .l1_derivation import (
    discover_parsed_jsonl,
    iter_message_records,
    message_character_count,
    message_role,
    ts_to_seconds,
)


def _bucket_start(ts_seconds: float, bucket: str) -> datetime:
    dt = datetime.fromtimestamp(ts_seconds, tz=timezone.utc)
    if bucket == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "week":
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return start - timedelta(days=start.weekday())
    if bucket == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"unsupported bucket: {bucket}")


def _bucket_label(bucket_start: str, bucket: str) -> str:
    if bucket == "hour":
        return bucket_start
    return bucket_start[:10]


def analyze_timeline(input_path: Path, bucket: str = "day") -> dict[str, Any]:
    """Aggregate timestamped messages into UTC timeline buckets."""
    parsed_files = discover_parsed_jsonl(input_path)
    timeline_by_bucket: dict[str, dict[str, Any]] = {}

    for parsed_path in parsed_files:
        for row in iter_message_records(parsed_path):
            ts_seconds = ts_to_seconds(row.get("ts"))
            if ts_seconds is None:
                continue

            bucket_start = (
                _bucket_start(ts_seconds, bucket).isoformat().replace("+00:00", "Z")
            )
            item = timeline_by_bucket.setdefault(
                bucket_start,
                {
                    "bucket_start": bucket_start,
                    "message_count": 0,
                    "user_messages": 0,
                    "assistant_messages": 0,
                    "other_roles": 0,
                    "characters_total": 0,
                },
            )

            item["message_count"] += 1
            item["characters_total"] += message_character_count(row)

            role = message_role(row)
            if role == "user":
                item["user_messages"] += 1
            elif role == "assistant":
                item["assistant_messages"] += 1
            else:
                item["other_roles"] += 1

    return {
        "bucket": bucket,
        "timeline": [timeline_by_bucket[key] for key in sorted(timeline_by_bucket)],
    }


def render_timeline_text(timeline_data: dict[str, Any]) -> str:
    """Render timeline buckets in a compact terminal-friendly format."""
    lines = [f"Timeline (bucket: {timeline_data['bucket']})"]
    for item in timeline_data.get("timeline", []):
        lines.extend(
            [
                "",
                _bucket_label(item["bucket_start"], timeline_data["bucket"]),
                f"  messages: {item['message_count']}",
                f"  user: {item['user_messages']}",
                f"  assistant: {item['assistant_messages']}",
                f"  other: {item['other_roles']}",
                f"  characters: {item['characters_total']}",
            ]
        )
    return "\n".join(lines)


def render_timeline_json(timeline_data: dict[str, Any]) -> str:
    """Render timeline data as formatted JSON."""
    return json.dumps(timeline_data, ensure_ascii=False, indent=2)
