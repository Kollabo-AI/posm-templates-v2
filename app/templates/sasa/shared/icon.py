from __future__ import annotations
from PIL import Image
from .... import logger


def resolve_icon(icon_text: str | None) -> Image.Image | None:
    ICON_PATHS = {
        "獨家發售": "resources/icons/sasa_soloselling.png",
        "獨家代理": "resources/icons/sasa_soloagent.png",
    }
    if not icon_text or not icon_text.strip():
        return None

    icon_path = ICON_PATHS.get(icon_text.strip())
    if icon_path is None:
        logger.warning(f"Unknown icon text: {icon_text}. Skipping icon placement.")
        return None

    icon = Image.open(icon_path).convert("RGBA")
    return icon
