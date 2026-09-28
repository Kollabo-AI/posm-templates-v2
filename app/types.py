from __future__ import annotations

from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class PosmBinding(BaseModel):
    schemaVersion: int = 1
    template: str
    field: str
    role: str
    index: int = 0

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class FabricObject(BaseModel):
    type: str = "object"
    version: str = "6.6.5"
    name: str | None = None
    originX: str = "left"
    originY: str = "top"
    left: float = 0
    top: float = 0
    width: float = 0
    height: float = 0
    scaleX: float = 1
    scaleY: float = 1
    angle: float = 0
    skewX: float = 0
    skewY: float = 0
    flipX: bool = False
    flipY: bool = False
    opacity: float = 1
    fill: str | None = None
    stroke: str | None = None
    strokeWidth: float = 0
    strokeDashArray: list[float] | None = None
    strokeDashOffset: float = 0
    strokeLineCap: str = "butt"
    strokeLineJoin: str = "miter"
    strokeMiterLimit: float = 4
    strokeUniform: bool = False
    objectCaching: bool = False
    paintFirst: str = "fill"
    fillRule: str = "nonzero"
    direction: str = "ltr"
    styles: dict[str, JsonValue] = Field(default_factory=dict)
    shadow: JsonValue | None = None
    backgroundColor: str | None = None
    textBackgroundColor: str | None = None
    path: JsonValue | None = None
    visible: bool = True
    globalCompositeOperation: str = "source-over"
    posmBinding: PosmBinding | dict[str, JsonValue] | None = None

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class FabricTextbox(FabricObject):
    type: str = "textbox"
    text: str = ""
    fontSize: float = 0
    fontFamily: str = "Alibaba PuHuiTi"
    fontWeight: str | int = "400"
    fontStyle: str = "normal"
    underline: bool = False
    textAlign: str = "left"
    charSpacing: float = 0
    lineHeight: float = 1.16
    minWidth: float = 0
    splitByGrapheme: bool = False
    overline: bool = False
    linethrough: bool = False


class FabricImage(FabricObject):
    type: str = "image"
    src: str = ""
    cropX: float = 0
    cropY: float = 0
    filters: list[dict[str, JsonValue]] = Field(default_factory=list)


class FabricGroup(FabricObject):
    type: str = "group"
    objects: list[Any] = Field(default_factory=list)


FabricObjects: TypeAlias = list[FabricObject]


class FabricCanvas(BaseModel):
    version: str = "6.6.5"
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    objects: list[Any]
    backgroundImage: Any = None

    model_config = ConfigDict(extra="allow")
