from __future__ import annotations

from ....types import PosmBinding
from ....renderer import LayoutElement, SemanticTextBinding
from .preprocess import HKMOPrice


REGISTERED_SASA_TEMPLATE_NAMES = frozenset(
    {
        "sasa_202604002",
        "sasa_202607001",
        "sasa_202607002",
        "sasa_202607003",
        "sasa_202607004",
        "sasa_202607005",
        "sasa_202607006",
    }
)


def conversion_binding_template(
    template_name: str,
    region: HKMOPrice | None,
) -> str | None:
    """Return the exact registered template eligible for semantic bindings."""

    if region is None or template_name not in REGISTERED_SASA_TEMPLATE_NAMES:
        return None
    return template_name


def promotion_field(name: str, promotion_index: int = 0) -> str:
    return f"promotion_list.{promotion_index}.{name}"


def promotion_binding(
    *,
    template: str | None,
    field: str,
    role: str,
    index: int,
) -> PosmBinding | None:
    if template is None:
        return None
    return PosmBinding(
        schemaVersion=1,
        template=template,
        field=promotion_field(field),
        role=role,
        index=index,
    )


def bind_promotion_text(
    element: LayoutElement,
    *,
    template: str | None,
    field: str,
    role: str,
    name_prefixes: tuple[str, ...] = (),
) -> LayoutElement:
    """Bind one rendered promotion field without affecting its layout."""

    if template is None:
        return element
    return SemanticTextBinding(
        element=element,
        template=template,
        field=promotion_field(field),
        role=role,
        name_prefixes=name_prefixes,
    )
