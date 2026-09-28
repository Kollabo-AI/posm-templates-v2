from .sasa_202607006 import Pipeline as Pipe202607006
from ....native import Bundle


class Pipe202607005(Pipe202607006):
    @property
    def template_path(self) -> str:
        return str(Bundle.configured().root / "assets" / "templates" / "sasa_202607005.png")


class Pipe202607004(Pipe202607006):
    @property
    def template_path(self) -> str:
        return str(Bundle.configured().root / "assets" / "templates" / "sasa_202607004.png")


class Pipe202607003(Pipe202607006):
    @property
    def template_path(self) -> str:
        return str(Bundle.configured().root / "assets" / "templates" / "sasa_202607003.png")


class Pipe202607002(Pipe202607006):
    @property
    def template_path(self) -> str:
        return str(Bundle.configured().root / "assets" / "templates" / "sasa_202607002.png")
