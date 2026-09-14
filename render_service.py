from __future__ import annotations

from typing import Any

from app.schema import CreatePayload, GenerationResult
from app.templates import get_pipeline


def render_payload(payload: Any) -> GenerationResult:
    items = CreatePayload(payload).get_items()
    if len(items) != 1:
        raise ValueError(f"A Lambda task must contain exactly one render item, got {len(items)}")

    item = items[0]
    return get_pipeline(item.template).run(item)
