from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Protocol


class ProviderRecordExpander(Protocol):
    """Expand one provider input record into zero or more adapter inputs."""

    def __call__(self, raw: Any) -> Iterable[Any]:
        """Return expanded adapter inputs for one raw provider record."""


class ProviderInputRecords(Protocol):
    """Yield provider input records along with their concrete source path."""

    def __call__(
        self,
        input_path: Path,
        logger: logging.Logger,
    ) -> Iterable[tuple[Any, str]]:
        """Return provider input records with source metadata."""


class ProviderAdapter(Protocol):
    """Callable provider normalization boundary used by the parser."""

    __llp_input_records__: ProviderInputRecords | None
    __llp_record_expander__: ProviderRecordExpander | None

    def __call__(
        self,
        raw: Any,
        *,
        source: str | None = None,
        logger: logging.Logger | None = None,
    ) -> Iterable[dict[str, Any]]:
        """Normalize one provider input record into canonical message rows."""
