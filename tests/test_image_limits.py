import base64
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from app.renderer.raster import _externalize_images, _validate_resources
from app.renderer.runtime import RendererError
from app.util.image import (
    RENDERER_MAX_IMAGE_BYTES,
    RENDERER_MAX_IMAGE_DIMENSION,
    bounded_image_to_base64,
    image_to_base64,
)


def _decode_data_url(url: str) -> tuple[str, bytes]:
    header, encoded = url.split(",", 1)
    return header.split(";")[0], base64.b64decode(encoded, validate=True)


def _noise_image(size: tuple[int, int], mode: str) -> Image.Image:
    width, height = size
    bands = 4 if mode == "RGBA" else 3
    data = bytearray(os.urandom(width * height * bands))
    if mode == "RGBA":
        data[3::4] = bytes([128]) * (width * height)
    return Image.frombytes(mode, (width, height), bytes(data))


def _png_bytes(image: Image.Image) -> bytes:
    return _decode_data_url(image_to_base64(image.convert("RGBA"), format="PNG"))[1]


class BoundedImageEncodingTests(unittest.TestCase):

    def test_compliant_images_encode_unchanged(self) -> None:
        image = Image.new("RGBA", (400, 300), (200, 40, 80, 255))
        self.assertEqual(image_to_base64(image, format="PNG"), bounded_image_to_base64(image, format="PNG"))

    def test_dimensions_are_clamped_to_the_renderer_limit(self) -> None:
        image = Image.new("RGBA", (2400, 600), (200, 40, 80, 255))
        header, payload = _decode_data_url(bounded_image_to_base64(image, format="PNG"))
        self.assertEqual("data:image/png", header)
        with Image.open(io.BytesIO(payload)) as decoded:
            self.assertEqual((RENDERER_MAX_IMAGE_DIMENSION, 500), decoded.size)

    def test_oversized_opaque_png_falls_back_to_jpeg(self) -> None:
        image = _noise_image((1800, 1800), "RGB")
        self.assertGreater(len(_png_bytes(image)), RENDERER_MAX_IMAGE_BYTES)
        header, payload = _decode_data_url(bounded_image_to_base64(image, format="PNG"))
        self.assertEqual("data:image/jpeg", header)
        self.assertLessEqual(len(payload), RENDERER_MAX_IMAGE_BYTES)

    def test_oversized_transparent_png_shrinks_until_it_fits(self) -> None:
        image = _noise_image((1800, 1800), "RGBA")
        self.assertGreater(len(_png_bytes(image)), RENDERER_MAX_IMAGE_BYTES)
        header, payload = _decode_data_url(bounded_image_to_base64(image, format="PNG"))
        self.assertEqual("data:image/png", header)
        self.assertLessEqual(len(payload), RENDERER_MAX_IMAGE_BYTES)
        with Image.open(io.BytesIO(payload)) as decoded:
            self.assertLess(decoded.width, image.width)
            self.assertLessEqual(decoded.width, RENDERER_MAX_IMAGE_DIMENSION)
            self.assertLessEqual(decoded.height, RENDERER_MAX_IMAGE_DIMENSION)


class ResourceValidationTests(unittest.TestCase):

    def _resource(self, directory: Path, name: str, width: int, height: int, payload: bytes) -> dict[str, object]:
        path = directory / name
        path.write_bytes(payload)
        return {"path": str(path), "sha256": "", "width": width, "height": height}

    def test_valid_resources_pass(self) -> None:
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            resources = {"image:0": self._resource(directory, "a.bin", 100, 100, b"small")}
            _validate_resources(resources, {"image:0": "Main product"})

    def test_resource_count_above_the_renderer_limit_is_reported(self) -> None:
        resources = {f"image:{index}": {"path": "", "sha256": "", "width": 1, "height": 1}
                     for index in range(33)}
        with self.assertRaisesRegex(RendererError, "IMAGE_LIMIT: 33 images in one poster") as context:
            _validate_resources(resources, {})
        self.assertIn("32", str(context.exception))

    def test_dimension_violation_names_the_resource(self) -> None:
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            resources = {"image:0": self._resource(directory, "a.bin", 2001, 500, b"small")}
            with self.assertRaisesRegex(RendererError, "IMAGE_LIMIT") as context:
                _validate_resources(resources, {"image:0": "Main product"})
            message = str(context.exception)
            self.assertIn("image:0 'Main product'", message)
            self.assertIn("2001x500", message)
            self.assertIn(f"{RENDERER_MAX_IMAGE_DIMENSION} px", message)

    def test_byte_violation_names_the_resource(self) -> None:
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            oversized = b"\0" * (RENDERER_MAX_IMAGE_BYTES + 1)
            resources = {"image:3": self._resource(directory, "a.bin", 500, 500, oversized)}
            with self.assertRaisesRegex(RendererError, "IMAGE_LIMIT") as context:
                _validate_resources(resources, {"image:3": "Gift image"})
            message = str(context.exception)
            self.assertIn("image:3 'Gift image'", message)
            self.assertIn("5.00 MiB", message)
            self.assertIn("5 MiB", message)

    def test_externalized_images_carry_labels_for_diagnostics(self) -> None:
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            canvas = {"objects": [{"name": "Main product", "src": image_to_base64(Image.new("RGBA", (64, 64), (1, 2, 3, 255)))}]}
            resources, labels = _externalize_images(canvas, directory)
            self.assertEqual({"image:0"}, set(resources))
            self.assertEqual({"image:0": "Main product"}, labels)
            self.assertEqual("image:0", canvas["objects"][0]["src"])
            _validate_resources(resources, labels)


if __name__ == "__main__":
    unittest.main()
