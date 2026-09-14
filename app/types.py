from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class FabricCanvas(BaseModel):
    version: str = "6.6.5"
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    objects: list[dict[str, JsonValue]]
    backgroundImage: dict[str, JsonValue] | None = None

    model_config = ConfigDict(extra="allow")
