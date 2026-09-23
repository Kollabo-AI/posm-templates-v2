"""Public template names and transport compatibility, without layout rules."""
from pathlib import Path

NATIVE_TEMPLATES = ("sasa_202607001", "sasa_202607006", "sasa_202609001", "sasa_202609002")
LEGACY_TEMPLATES = ("sasa_202604002", "sasa_202607002", "sasa_202607003", "sasa_202607004", "sasa_202607005")
SUPPORTED_TEMPLATES = (*NATIVE_TEMPLATES, *LEGACY_TEMPLATES)


def native_template(name: str) -> str:
    if name not in SUPPORTED_TEMPLATES:
        raise ValueError(f"Unsupported template: {name}")
    return "sasa_202607006" if name in LEGACY_TEMPLATES else name


def legacy_background(name: str) -> Path | None:
    if name in LEGACY_TEMPLATES and name != "sasa_202604002":
        return Path(__file__).resolve().parent / "legacy_backgrounds" / f"{name}.png"
    return None


def normalize_bindings(canvas, template: str) -> None:
    """Match the platform's existing semantic roles without moving artwork."""
    indexes: dict[str, int] = {}

    def visit(node):
        if not isinstance(node, dict):
            return
        binding = node.get("posmBinding")
        if isinstance(binding, dict):
            field = str(binding.get("field", ""))
            leaf = field.rsplit(".", 1)[-1]
            binding["template"] = template
            if leaf in {"brand_name", "product_name", "gwp_text", "tnc"}:
                binding["role"] = "line"
            elif leaf == "fab":
                binding["role"] = "formatted-line" if native_template(template) in {"sasa_202607001", "sasa_202609001"} else "line"
            elif leaf in {"price_recommended", "price_vip", "star_price"}:
                binding["role"] = "token"
            elif leaf == "discount_ball":
                binding["role"] = "token"
            binding["index"] = indexes.get(field, 0)
            indexes[field] = binding["index"] + 1
        for child in node.get("objects", []):
            visit(child)

    for node in canvas.objects:
        visit(node)
