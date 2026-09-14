from __future__ import annotations

import base64
import io
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock

import requests
from PIL import Image

from .. import logger


IMAGE_CACHE_MAX_ITEMS = 32
IMAGE_CACHE_MAX_BYTES = 256 * 1024 * 1024
IMAGE_CACHE_MAX_DECODED_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ImageCacheInfo:
    hits: int
    misses: int
    rejections: int
    evictions: int
    maxsize: int
    currsize: int
    max_bytes: int
    curr_bytes: int


@dataclass(frozen=True)
class ImageCacheEntry:
    key: str
    key_chars: int
    width: int
    height: int
    estimated_bytes: int


def _estimated_decoded_image_bytes(size: tuple[int, int]) -> int:
    width, height = size
    return width * height * 4


class _DecodedImageCache:
    def __init__(
        self,
        max_items: int,
        max_bytes: int,
        max_decoded_bytes: int,
    ) -> None:
        if max_items < 1 or max_bytes < 1 or max_decoded_bytes < 1:
            raise ValueError("Image cache limits must be positive")
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._max_decoded_bytes = max_decoded_bytes
        self._entries: OrderedDict[str, tuple[Image.Image, int]] = OrderedDict()
        self._current_bytes = 0
        self._hits = 0
        self._misses = 0
        self._rejections = 0
        self._evictions = 0
        self._lock = RLock()

    def __call__(self, url: str) -> Image.Image:
        with self._lock:
            cached = self._entries.pop(url, None)
            if cached is not None:
                self._entries[url] = cached
                self._hits += 1
                return cached[0].copy()
            self._misses += 1

        image = _load_image_impl(url, use_cache=True)
        decoded_bytes = _estimated_decoded_image_bytes(image.size)
        entry_bytes = decoded_bytes + len(url)
        if (
            decoded_bytes > self._max_decoded_bytes
            or entry_bytes > self._max_bytes
        ):
            with self._lock:
                self._rejections += 1
            return image

        with self._lock:
            cached = self._entries.pop(url, None)
            if cached is not None:
                self._entries[url] = cached
                copy = cached[0].copy()
                image.close()
                return copy

            while self._entries and (
                len(self._entries) >= self._max_items
                or self._current_bytes + entry_bytes > self._max_bytes
            ):
                _, (evicted_image, evicted_bytes) = self._entries.popitem(last=False)
                evicted_image.close()
                self._current_bytes -= evicted_bytes
                self._evictions += 1

            self._entries[url] = (image, entry_bytes)
            self._current_bytes += entry_bytes
            return image.copy()

    def cache_clear(self) -> None:
        with self._lock:
            for image, _ in self._entries.values():
                image.close()
            self._entries.clear()
            self._current_bytes = 0
            self._hits = 0
            self._misses = 0
            self._rejections = 0
            self._evictions = 0

    def cache_info(self) -> ImageCacheInfo:
        with self._lock:
            return ImageCacheInfo(
                hits=self._hits,
                misses=self._misses,
                rejections=self._rejections,
                evictions=self._evictions,
                maxsize=self._max_items,
                currsize=len(self._entries),
                max_bytes=self._max_bytes,
                curr_bytes=self._current_bytes,
            )

    def cache_entries(self) -> list[ImageCacheEntry]:
        with self._lock:
            return [
                ImageCacheEntry(
                    key=_redacted_cache_key(url),
                    key_chars=len(url),
                    width=image.width,
                    height=image.height,
                    estimated_bytes=entry_bytes,
                )
                for url, (image, entry_bytes) in self._entries.items()
            ]


def _redacted_cache_key(url: str) -> str:
    if url.startswith("data:"):
        media_type = url.partition(",")[0].partition(";")[0]
        return f"{media_type},<redacted>"
    return url.split("?", 1)[0].split("#", 1)[0]


def image_to_base64(image: Image.Image, format: str = "PNG") -> str:
    """
    Converts a PIL Image into a base64-encoded data URL string.

    Args:
        image (Image.Image): The PIL image object.
        format (str): The format to save the image as (e.g., 'PNG', 'JPEG', 'WEBP').
                      Defaults to 'PNG'.

    Returns:
        str: A string suitable for stuff.
             e.g., "data:image/png;base64,iVBORw0KG..."
    """

    buffer = io.BytesIO()
    if format.upper() == "PNG":
        image.save(buffer, format=format, compress_level=1)
    else:
        image.save(buffer, format=format)
    img_bytes = buffer.getvalue()
    base64_encoded = base64.b64encode(img_bytes).decode('utf-8')
    mime_type = f"image/{format.lower()}"
    url = f"data:{mime_type};base64,{base64_encoded}"
    return url


def _fetch_image(url: str) -> Image.Image:
    """Download an image over HTTP(S) with no caching."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    attempt = 0
    while True:
        try:
            response = requests.get(url, headers=headers, stream=True)
            response.raise_for_status()
            return Image.open(io.BytesIO(response.content))
        except Exception:
            attempt += 1
            if attempt == 3:
                raise


def download_image(url: str) -> Image.Image:
    """Downloads an image from a URL and returns it as a PIL Image object."""
    return _fetch_image(url)


def _is_mutable_local_asset(url: str) -> bool:
    """
    True for files served from the Express bridge's /uploads folder. These can be
    overwritten in place (same filename), so they must NOT be served from a
    URL-keyed cache — otherwise a re-uploaded product/template image keeps
    rendering the old version.
    """
    return "/uploads/" in url


def _load_image_impl(url: str, use_cache: bool) -> Image.Image:
    try:
        if url.startswith("data:image/"):
            # Case 1: Base64 encoded image
            # Format is usually "data:image/png;base64,iVBORw0KGgo..."
            header, encoded = url.split(",", 1)
            img_data = base64.b64decode(encoded)
            img = Image.open(io.BytesIO(img_data))

        elif url.startswith(("http://", "https://")):
            # Case 2: HTTP/HTTPS URL — cached unless it's a mutable local asset.
            img = download_image(url) if use_cache else _fetch_image(url)
        else:
            # Case 3: Local file path
            try:
                img = Image.open(url)
            except FileNotFoundError as e:
                logger.error(f"Cannot find path: {url}")
                raise e

        img.load()
        return img

    except Exception as e:
        raise RuntimeError(f"Failed to load image from URL: {url}\nError: {e}") from e


_load_image_cached = _DecodedImageCache(
    max_items=IMAGE_CACHE_MAX_ITEMS,
    max_bytes=IMAGE_CACHE_MAX_BYTES,
    max_decoded_bytes=IMAGE_CACHE_MAX_DECODED_BYTES,
)


def load_image_from_url(url: str) -> Image.Image:
    """
    Loads an image from a URL (HTTP/HTTPS) or a Base64 data string. Local
    /uploads assets are fetched fresh every time (they can be replaced in place);
    everything else is cached by URL.
    """
    if _is_mutable_local_asset(url):
        return _load_image_impl(url, use_cache=False)
    return _load_image_cached(url)


def get_image_cache_snapshot() -> dict[str, object]:
    info = _load_image_cached.cache_info()
    return {
        "items": info.currsize,
        "max_items": info.maxsize,
        "estimated_bytes": info.curr_bytes,
        "max_bytes": info.max_bytes,
        "hits": info.hits,
        "misses": info.misses,
        "rejections": info.rejections,
        "evictions": info.evictions,
        "entries": [
            {
                "key": entry.key,
                "key_chars": entry.key_chars,
                "width": entry.width,
                "height": entry.height,
                "estimated_bytes": entry.estimated_bytes,
            }
            for entry in _load_image_cached.cache_entries()
        ],
    }
