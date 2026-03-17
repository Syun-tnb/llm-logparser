from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Pattern


DEFAULT_REPLACEMENT = "REDACTED"
DEFAULT_SCOPE = "content_parts"
DEFAULT_SENSITIVE_KEYWORDS = (
    "SECRET",
    "TOKEN",
    "API_KEY",
    "AUTHORIZATION",
    "COOKIE",
    "PASSWORD",
)
DEFAULT_MASK_PATTERNS = (
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    r"(?:\+?\d[\d().\s-]{7,}\d)\b",
)


def _compile_patterns(patterns: tuple[str, ...]) -> tuple[Pattern[str], ...]:
    compiled: list[Pattern[str]] = []
    for pattern in patterns:
        compiled.append(re.compile(pattern))
    return tuple(compiled)


@dataclass(frozen=True)
class SanitizePolicy:
    enabled: bool
    replacement: str
    scope: str
    sensitive_keywords: tuple[str, ...]
    custom_keywords: tuple[str, ...]
    mask_patterns: tuple[str, ...]
    custom_mask_patterns: bool
    _compiled_patterns: tuple[Pattern[str], ...]

    @classmethod
    def defaults(cls) -> SanitizePolicy:
        return cls.from_settings()

    @classmethod
    def from_settings(
        cls,
        *,
        enabled: bool = True,
        replacement: str = DEFAULT_REPLACEMENT,
        scope: str = DEFAULT_SCOPE,
        extra_keywords: tuple[str, ...] = (),
        mask_patterns: tuple[str, ...] | None = None,
    ) -> SanitizePolicy:
        if scope not in {"content_parts", "all_strings"}:
            raise SystemExit(
                "Invalid config: sanitize.scope must be one of: "
                "content_parts, all_strings"
            )

        effective_patterns = (
            DEFAULT_MASK_PATTERNS if mask_patterns is None else tuple(mask_patterns)
        )
        normalized_keywords = tuple(
            dict.fromkeys(
                [*DEFAULT_SENSITIVE_KEYWORDS, *(keyword.upper() for keyword in extra_keywords)]
            )
        )
        custom_keywords = tuple(dict.fromkeys(keyword.upper() for keyword in extra_keywords))

        return cls(
            enabled=enabled,
            replacement=replacement,
            scope=scope,
            sensitive_keywords=normalized_keywords,
            custom_keywords=custom_keywords,
            mask_patterns=effective_patterns,
            custom_mask_patterns=mask_patterns is not None,
            _compiled_patterns=_compile_patterns(effective_patterns),
        )

    def has_sensitive_key(self, key: str) -> bool:
        upper_key = key.upper()
        return any(keyword in upper_key for keyword in self.sensitive_keywords)

    def sanitize_string(self, value: str) -> str:
        sanitized = value
        for pattern in self._compiled_patterns:
            sanitized = pattern.sub(self.replacement, sanitized)
        return sanitized

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "scope": self.scope,
            "replacement": self.replacement,
            "extra_keyword_count": len(self.custom_keywords),
            "custom_keywords_supplied": bool(self.custom_keywords),
            "mask_pattern_count": len(self.mask_patterns),
            "custom_mask_patterns_supplied": self.custom_mask_patterns,
        }


def _is_content_string_path(path: tuple[str, ...]) -> bool:
    if len(path) >= 2 and path[-2] == "content" and path[-1] in {"parts", "text", "data"}:
        return True
    return False


def sanitize_value(
    value: Any,
    policy: SanitizePolicy,
    *,
    path: tuple[str, ...] = (),
) -> Any:
    if not policy.enabled:
        return value

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            if policy.has_sensitive_key(key):
                out[key] = policy.replacement
                continue
            out[key] = sanitize_value(child, policy, path=(*path, key))
        return out

    if isinstance(value, list):
        if policy.scope == "content_parts" and _is_content_string_path(path):
            return [
                policy.sanitize_string(item) if isinstance(item, str) else item
                for item in value
            ]
        return [sanitize_value(item, policy, path=path) for item in value]

    if isinstance(value, str):
        if policy.scope == "all_strings":
            return policy.sanitize_string(value)
        if policy.scope == "content_parts" and _is_content_string_path(path):
            return policy.sanitize_string(value)

    return value
