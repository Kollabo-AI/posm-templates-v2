from __future__ import annotations

import os
import sys


def secret_reference(secret_arn: str) -> str:
    value = secret_arn.strip()
    if not value.startswith("arn:") or ":secretsmanager:" not in value:
        raise ValueError("JOB_WORKER_SECRET_ARN must be a Secrets Manager ARN")
    return f"{{{{resolve:secretsmanager:{value}}}}}"


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        raise RuntimeError("A Lambda handler name is required")
    os.environ["JOB_WORKER_SECRET"] = secret_reference(
        os.environ.get("JOB_WORKER_SECRET_ARN", "")
    )
    os.environ.pop("JOB_WORKER_SECRET_ARN", None)
    os.execv(
        sys.executable,
        [
            sys.executable,
            "/var/task/asm-exec",
            "--",
            "/lambda-entrypoint.sh",
            sys.argv[1],
        ],
    )


if __name__ == "__main__":
    main()
