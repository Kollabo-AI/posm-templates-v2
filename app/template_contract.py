"""Public template names and transport compatibility, without layout rules."""
from pathlib import Path

NATIVE_TEMPLATES = ("sasa_202607001", "sasa_202607006", "sasa_202609001")
LEGACY_TEMPLATES = ("sasa_202604002", "sasa_202607002", "sasa_202607003", "sasa_202607004", "sasa_202607005")
LEGACY_BACKGROUND_TEMPLATES = ("sasa_202607002", "sasa_202607003", "sasa_202607004", "sasa_202607005")
SUPPORTED_TEMPLATES = (*NATIVE_TEMPLATES, *LEGACY_TEMPLATES)


def native_template(name: str) -> str:
    if name not in SUPPORTED_TEMPLATES:
        raise ValueError(f"Unsupported template: {name}")
    return "sasa_202607006" if name in LEGACY_TEMPLATES else name


def legacy_background(name: str) -> Path | None:
    if name not in LEGACY_BACKGROUND_TEMPLATES:
        return None
    return Path(__file__).resolve().parent / "legacy_backgrounds" / f"{name}.png"


def normalize_bindings(canvas, template: str) -> None:
    """Match the platform's existing semantic roles without moving artwork."""
    indexes: dict[str, int] = {}

    def visit(node):
        is_model = hasattr(node, "model_copy") and hasattr(node, "posmBinding")
        if is_model:
            binding = node.posmBinding
            children = getattr(node, "objects", [])
        elif isinstance(node, dict):
            binding = node.get("posmBinding")
            children = node.get("objects", [])
        else:
            return
        if binding is not None:
            if hasattr(binding, "model_dump"):
                values = binding.model_dump(mode="python")
            elif isinstance(binding, dict):
                # Work on a copy: ``binding.clear()`` below mutates the
                # original dictionary, so aliasing it would discard all
                # values before they can be written back.
                values = dict(binding)
            else:
                values = {}
            field = str(values.get("field", ""))
            leaf = field.rsplit(".", 1)[-1]
            values["template"] = template
            if leaf in {"brand_name", "product_name", "gwp_text", "tnc"}:
                values["role"] = "line"
            elif leaf == "fab":
                values["role"] = "formatted-line" if native_template(template) in {"sasa_202607001", "sasa_202609001"} else "line"
            elif leaf in {"price_recommended", "price_vip", "star_price"}:
                values["role"] = "token"
            elif leaf == "discount_ball":
                values["role"] = "token"
            values["index"] = indexes.get(field, 0)
            indexes[field] = values["index"] + 1
            if is_model:
                node.posmBinding = type(binding).model_validate(values)
            else:
                binding.clear()
                binding.update(values)
        for child in children:
            visit(child)

    for node in canvas.objects:
        visit(node)
