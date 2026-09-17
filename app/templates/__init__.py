from __future__ import annotations

from .native import DoublePipeline, Pipeline
from ..template_contract import native_template


def get_pipeline(name: str) -> Pipeline:
    native_template(name)
    return (DoublePipeline if name == "sasa_202609001" else Pipeline)(name)
