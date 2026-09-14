from __future__ import annotations

from .native import DoublePipeline, Pipeline


def get_pipeline(name: str) -> Pipeline:
    if name not in {"sasa_202607001", "sasa_202609001"}:
        raise ValueError(f"Unsupported template: {name}")
    return (DoublePipeline if name == "sasa_202609001" else Pipeline)(name)
