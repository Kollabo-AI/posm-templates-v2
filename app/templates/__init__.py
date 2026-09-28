from __future__ import annotations

from ..base import POSMImplementation
from ..template_contract import native_template
from .sasa.sasa_202607001 import Pipeline as Layout07001Pipeline
from .sasa.sasa_202607006 import Pipeline as Layout07006Pipeline
from .sasa.sasa_202609001 import Pipeline as Layout09001Pipeline


def get_pipeline(name: str) -> POSMImplementation:
    native_template(name)
    if name == "sasa_202607001":
        return Layout07001Pipeline(name)
    if name == "sasa_202609001":
        return Layout09001Pipeline(name)
    if name == "sasa_202607006" or name in {"sasa_202604002", "sasa_202607002", "sasa_202607003", "sasa_202607004", "sasa_202607005"}:
        return Layout07006Pipeline(name)
    raise ValueError(f"Unsupported template: {name}")
