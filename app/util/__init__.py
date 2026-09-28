from dataclasses import dataclass
from typing import Generic, TypeVar

from .image import (
    RENDERER_MAX_IMAGE_BYTES,
    RENDERER_MAX_IMAGE_DIMENSION,
    bounded_image_to_base64,
    image_to_base64,
    load_image_from_url,
)

T = TypeVar("T")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T

    def is_error(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value

    def unwrap_error(self) -> None:
        raise RuntimeError("Result contains a value")


@dataclass(frozen=True)
class Error(Generic[T]):
    error: str

    def is_error(self) -> bool:
        return True

    def unwrap(self) -> T:
        raise RuntimeError(self.error)

    def unwrap_error(self) -> str:
        return self.error


Result = Ok[T] | Error[T]

__all__ = [
    "Error",
    "Ok",
    "RENDERER_MAX_IMAGE_BYTES",
    "RENDERER_MAX_IMAGE_DIMENSION",
    "Result",
    "bounded_image_to_base64",
    "image_to_base64",
    "load_image_from_url",
]
