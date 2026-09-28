from pydantic import BaseModel, ConfigDict
from .box import BoundingBox
from ..types import FabricObjects


class LayoutElement(BaseModel):
    """A base class for layout elements."""
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    def placement_boxes(self) -> list[BoundingBox]:
        raise NotImplementedError("Subclasses must implement placement_boxes()")

    def placement_box(self) -> BoundingBox | None:
        boxes = self.placement_boxes()
        if not boxes:
            return None
        return BoundingBox.merge(boxes)

    def render(self) -> FabricObjects:
        """Renders the layout element into a list of FabricObjects."""
        raise NotImplementedError("Subclasses must implement render()")

    def __bool__(self) -> bool:
        """Returns True if the layout element is not Empty"""
        return bool(self.placement_boxes())
