from __future__ import annotations

import logging
from typing import Any

from worker import client_from_environment, process_record

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, list[dict[str, str]]]:
    client = client_from_environment()
    failures: list[dict[str, str]] = []

    for record in event.get("Records", []):
        message_id = str(record.get("messageId", ""))
        try:
            process_record(record, client)
        except Exception:
            logger.exception("SQS record processing failed: %s", message_id or "unknown")
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}
