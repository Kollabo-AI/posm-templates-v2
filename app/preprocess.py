import re
from collections.abc import Mapping
from typing import Literal, TypeAlias

from . import logger
from .price_tokens import is_price_line

NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?")
CURRENCY_PATTERN = re.compile(r"MOP|\$", re.IGNORECASE)
REPEATED_MOP_PATTERN = re.compile(r"(?:MOP){2,}", re.IGNORECASE)

PromotionFieldValue = str | list[str]
HKMOPrice: TypeAlias = Literal["HK", "MO"]


def hk_mo_price(fields: Mapping[str, PromotionFieldValue]) -> HKMOPrice | None:
    """Return an explicit canonical region, or None for a legacy payload."""
    value = fields.get("hk_mo_price", "")
    if not isinstance(value, str):
        raise TypeError("Promotion field 'hk_mo_price' must be a string")

    normalized = value.strip()
    if not normalized:
        return None
    if normalized not in ("HK", "MO"):
        raise ValueError("Promotion field 'hk_mo_price' must be 'HK' or 'MO'")
    return normalized


def _currency_marker(region: HKMOPrice) -> str:
    if region == "HK":
        return "$"
    if region == "MO":
        return "MOP"
    raise ValueError("region must be 'HK' or 'MO'")


def normalize_currency_markers(
    value: str | None,
    region: HKMOPrice | None,
) -> str | None:
    """Replace explicit currency markers without inserting new ones."""
    if value is None or region is None:
        return value
    return CURRENCY_PATTERN.sub(_currency_marker(region), value)


def required_text(fields: Mapping[str, PromotionFieldValue], name: str) -> str:
    value = fields.get(name, "")
    if not isinstance(value, str):
        raise TypeError(f"Promotion field '{name}' must be a string")
    value = value.strip()
    if not value:
        logger.error(f"Promotion field '{name}' is required.")
        return f"No {name} provided"
    return value


def optional_text(fields: Mapping[str, PromotionFieldValue], name: str) -> str | None:
    value = fields.get(name, "")
    if not isinstance(value, str):
        raise TypeError(f"Promotion field '{name}' must be a string")
    value = value.strip()
    return value or None


def gwp_image_references(fields: Mapping[str, PromotionFieldValue]) -> list[str]:
    value = fields.get("gwp_image", "")
    references = [value] if isinstance(value, str) else value
    return [reference.strip() for reference in references if reference.strip()]


def normalize_price(value: str, region: HKMOPrice | None = None) -> str:
    logger.debug(f"Normalizing price input: {value!r}")
    if not value:
        logger.debug("Normalized price output: ''")
        return ""

    currency_marker = _currency_marker(region) if region is not None else "$"

    def normalize_number(number: str) -> str:
        number = number.replace(",", "")
        integer, dot, fraction = number.partition(".")

        formatted = f"{int(integer):,}"
        return formatted + dot + fraction if dot else formatted

    normalized_lines: list[str] = []
    inserted_currency = False
    for raw_line in value.strip().splitlines():
        line = re.sub(r"\${2,}", "$", raw_line.strip())
        if region is not None:
            line = CURRENCY_PATTERN.sub(currency_marker, line)
            if currency_marker == "$":
                line = re.sub(r"\${2,}", "$", line)
            else:
                line = REPEATED_MOP_PATTERN.sub("MOP", line)
        first_number = NUMBER_PATTERN.search(line)
        has_currency = CURRENCY_PATTERN.search(line) is not None
        structured_price_line = is_price_line(line)
        inserted_currency_for_line = False
        if first_number is not None and not has_currency and structured_price_line:
            line = (
                f"{line[:first_number.start()]}"
                f"{currency_marker}"
                f"{line[first_number.start():]}"
            )
            inserted_currency = True
            inserted_currency_for_line = True
            has_currency = True
        normalized_line = (
            NUMBER_PATTERN.sub(
                lambda match: normalize_number(match.group()),
                line,
            )
            if structured_price_line or has_currency
            else line
        )
        normalized_lines.append(normalized_line)
        logger.debug(
            f"Normalized price line: input={raw_line!r}, "
            f"structured={structured_price_line}, "
            f"currency_inserted={inserted_currency_for_line}, "
            f"output={normalized_line!r}"
        )

    if inserted_currency:
        logger.warning(
            f"Price '{value}' has a price without a currency symbol. "
            f"Normalizing with '{currency_marker}'."
        )
    normalized_value = "\n".join(normalized_lines)
    logger.debug(f"Normalized price output: {normalized_value!r}")
    return normalized_value


def preprocess_vip_and_star_prices(
    vip_price: str | None,
    star_price: str | None,
    region: HKMOPrice | None = None,
) -> tuple[str, str | None]:
    normalized_vip_price = normalize_price(vip_price or "", region)
    normalized_star_price = (
        normalize_price(star_price, region)
        if star_price
        else None
    )

    if not normalized_star_price:
        return normalized_vip_price, None

    if (
        normalized_vip_price
        and NUMBER_PATTERN.search(normalized_star_price) is None
    ):
        normalized_star_price = None

    return normalized_vip_price, normalized_star_price


def recommended_price(value: str, region: HKMOPrice | None = None) -> str:
    stripped = value.strip()
    if stripped.startswith("建議價"):
        stripped = stripped[3:].strip()
    return normalize_price(stripped, region)
