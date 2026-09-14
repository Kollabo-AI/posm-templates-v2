# app/schema.py

from __future__ import annotations
import uuid
import re
from pydantic import BaseModel, model_validator, RootModel, Field, ConfigDict, JsonValue
from .types import FabricCanvas
from .util import load_image_from_url
from PIL import Image
import io
import base64

URL_SAFE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class CreateParams(BaseModel):
    template: str
    promotion_list: list[dict[str, str | list[str]]]
    product: list[list[str]]

    @model_validator(mode='after')
    def check_promotion_field_types(self):
        for promotion in self.promotion_list:
            for field, value in promotion.items():
                if isinstance(value, list) and field != "gwp_image":
                    raise ValueError(
                        f"Promotion field '{field}' must be a string; "
                        "only 'gwp_image' accepts a list of strings"
                    )
        return self

    @model_validator(mode='after')
    def check_lengths(self):
        from .templates import get_pipeline
        pipeline = get_pipeline(self.template)

        if len(self.promotion_list) != len(self.product):
            raise ValueError(f"Length of promotion_list and product must be the same, got {len(self.promotion_list)} and {len(self.product)}")
        if self.template == "sasa_202609001" and len(self.promotion_list) == 1:
            self.promotion_list = [*self.promotion_list, {}]
            self.product = [*self.product, []]
        if len(self.promotion_list) != pipeline.get_expected_number_of_fields():
            raise ValueError(f"Length of promotion_list must be {pipeline.get_expected_number_of_fields()}, got {len(self.promotion_list)}")
        if self.template == "sasa_202609001":
            for index, (promotion, references) in enumerate(zip(self.promotion_list, self.product)):
                if not promotion and references:
                    raise ValueError(f"Empty promotion slot {index} requires an empty product list")
        return self


class CreatePayload(RootModel):
    # This says: The root of the JSON can be a List of MainEndpoint OR a single MainEndpoint
    root: list[CreateParams]

    @model_validator(mode='before')
    @classmethod
    def check_root(cls, data):
        if not isinstance(data, (list, dict)):
            raise ValueError("Input must be a list or a dictionary")
        if isinstance(data, dict):
            data = [data]
        assert isinstance(data, list)
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("All items in the list must be dictionaries")
        return data

    def get_items(self):
        """Helper to always return a list, even if only one item was sent."""
        return self.root


class GenerationResult(BaseModel):
    id: str
    """A unique identifier for the generation result, typically a UUID string."""

    reference_jpg: str
    """A base64 string of the generated JPG. If the generation failed, this will be an empty string."""

    fabric_model: FabricCanvas | None
    """The fabric model data structure representing the generated design, if the generation was successful."""

    message: str | None
    """An optional message providing additional information about the generation result, such as error details if the generation failed."""

    successful: bool
    """A boolean flag indicating whether the generation was successful. This is derived from the presence of a valid reference_jpg and fabric_model."""

    @model_validator(mode='after')
    def validate_success(self):
        """Ensures that the presence of a reference JPG is consistent with the successful flag."""
        if self.successful:
            if not self.reference_jpg:
                raise ValueError("successful is True but reference_jpg is empty")
            if not self.fabric_model:
                raise ValueError("successful is True but fabric_model is None")
        else:
            if self.reference_jpg:
                raise ValueError("successful is False but reference_jpg is not empty")
            if self.fabric_model:
                raise ValueError("successful is False but fabric_model is not None")
        return self

    @property
    def image(self) -> Image.Image | None:
        """Returns the generated image as a PIL Image object, or None if generation failed. Mostly used for debug"""
        if not self.successful:
            return None
        try:
            return load_image_from_url(self.reference_jpg)
        except Exception as e:
            raise ValueError(f"Failed to decode reference_jpg into an image: {e}")


class CreateResponse(BaseModel):
    result: list[GenerationResult]
