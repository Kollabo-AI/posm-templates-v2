from __future__ import annotations

from typing import Any

from app.schema import CreatePayload, GenerationResult
from app.templates import get_pipeline
from app.template_contract import normalize_bindings


def render_payload(payload: Any) -> GenerationResult:
    items = CreatePayload(payload).get_items()
    if len(items) != 1:
        raise ValueError(f"A Lambda task must contain exactly one render item, got {len(items)}")

    item = items[0]
    result = get_pipeline(item.template).run(item)
    if result.successful and result.fabric_model is not None:
        normalize_bindings(result.fabric_model, item.template)
    return result
