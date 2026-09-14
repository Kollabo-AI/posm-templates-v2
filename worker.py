from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, Any, Callable

import requests
from PIL import Image

if TYPE_CHECKING:
    from app.schema import GenerationResult

logger = logging.getLogger(__name__)
MAX_ERROR_RESPONSE_CHARS = 1000


@dataclass(frozen=True)
class TaskMessage:
    version: int
    job_id: str
    item_index: int

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> TaskMessage:
        body = json.loads(record["body"])
        version = body.get("version")
        job_id = str(body.get("jobId", "")).strip()
        item_index = body.get("itemIndex")

        if version != 1:
            raise ValueError(f"Unsupported task message version: {version!r}")
        if not job_id:
            raise ValueError("jobId is required")
        if not isinstance(item_index, int) or item_index < 0:
            raise ValueError("itemIndex must be a non-negative integer")

        return cls(version=version, job_id=job_id, item_index=item_index)


class JobAlreadyHandled(Exception):
    pass


def create_thumbnail_data_url(image_data_url: str, max_size: int = 640) -> str:
    encoded = image_data_url.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as image:
        thumbnail = image.convert("RGB")
        thumbnail.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        output = BytesIO()
        thumbnail.save(output, format="JPEG", quality=82, optimize=True)
    encoded_thumbnail = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded_thumbnail}"


class ExpressJobClient:
    def __init__(
        self,
        base_url: str,
        worker_secret: str,
        session: requests.Session | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("EXPRESS_INTERNAL_URL is required")
        if not worker_secret:
            raise ValueError("JOB_WORKER_SECRET is required")
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Job-Worker-Secret": worker_secret}
        self._session = session or requests.Session()

    def claim(self, task: TaskMessage) -> Any:
        url = self._item_url(task)
        response = self._session.get(
            url,
            headers=self._headers,
            timeout=30,
        )
        if response.status_code == 409:
            raise JobAlreadyHandled
        self._raise_for_status("GET", url, response)
        response_data = response.json()
        return response_data["payload"]

    def complete(self, task: TaskMessage, callback: dict[str, Any]) -> None:
        url = f"{self._item_url(task)}/result"
        response = self._session.post(
            url,
            json=callback,
            headers=self._headers,
            timeout=60,
        )
        self._raise_for_status("POST", url, response)

    def _item_url(self, task: TaskMessage) -> str:
        return f"{self._base_url}/jobs/{task.job_id}/items/{task.item_index}"

    @staticmethod
    def _raise_for_status(method: str, url: str, response: requests.Response) -> None:
        if response.status_code < 400:
            return
        response_body = " ".join(str(response.text or "").split())
        if len(response_body) > MAX_ERROR_RESPONSE_CHARS:
            response_body = f"{response_body[:MAX_ERROR_RESPONSE_CHARS]}..."
        logger.error(
            "Express request failed: method=%s url=%s status=%s response=%r",
            method,
            url,
            response.status_code,
            response_body,
        )
        response.raise_for_status()


def successful_callback(result: GenerationResult) -> dict[str, Any]:
    serialized = result.model_dump(mode="json")
    try:
        serialized["thumbnailUrl"] = create_thumbnail_data_url(serialized["reference_jpg"])
    except (KeyError, ValueError, OSError, TypeError, binascii.Error):
        logger.exception("Thumbnail generation failed for result %s", result.id)
    return {"successful": True, "result": serialized}


def failed_callback(error: str) -> dict[str, Any]:
    return {"successful": False, "error": error}


def process_record(
    record: dict[str, Any],
    client: ExpressJobClient,
    renderer: Callable[[Any], GenerationResult] | None = None,
) -> None:
    task = TaskMessage.from_record(record)
    try:
        payload = client.claim(task)
    except JobAlreadyHandled:
        return

    try:
        if renderer is None:
            from render_service import render_payload

            renderer = render_payload
        result = renderer(payload)
        callback = (
            successful_callback(result)
            if result.successful
            else failed_callback(result.message or "Poster rendering failed")
        )
    except Exception as error:
        logger.exception("Poster rendering failed for job %s item %s", task.job_id, task.item_index)
        callback = failed_callback(str(error))

    client.complete(task, callback)


def client_from_environment() -> ExpressJobClient:
    return ExpressJobClient(
        base_url=os.getenv("EXPRESS_INTERNAL_URL", ""),
        worker_secret=os.getenv("JOB_WORKER_SECRET", ""),
    )
