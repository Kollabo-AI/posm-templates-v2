from __future__ import annotations


def get_browser_font_context_key() -> str:
    """Compatibility key for the former browser text-mask cache."""

    return "native-skia"
