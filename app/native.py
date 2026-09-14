from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .release_pin import RENDERER_MANIFEST_SHA256, RENDERER_RELEASES


class RendererError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def contained_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise RendererError("RENDERER_FILE_INVALID")
    return path


@dataclass(frozen=True)
class Bundle:
    root: Path
    expected_manifest_sha256: str

    @classmethod
    def configured(cls) -> Bundle:
        target = runtime_target()
        root = Path(os.getenv("POSM_RENDERER_BUNDLE", str(Path(__file__).resolve().parents[1] / "renderer" / target))).resolve()
        digest = os.getenv("POSM_RENDERER_MANIFEST_SHA256", "") or RENDERER_RELEASES.get(target) or RENDERER_MANIFEST_SHA256
        if not digest:
            raise RendererError("RENDERER_RELEASE_NOT_CONFIGURED")
        return cls(root, digest)

    def verify(self) -> dict[str, Any]:
        manifest_path = contained_file(self.root, "manifest.json")
        if file_sha256(manifest_path) != self.expected_manifest_sha256:
            raise RendererError("RENDERER_MANIFEST_CHECKSUM")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("protocol_version") != 1 or manifest.get("fabric_version") != "6.6.5":
            raise RendererError("RENDERER_RELEASE_INCOMPATIBLE")
        files = manifest.get("files")
        if not isinstance(files, dict) or manifest.get("executable") not in files:
            raise RendererError("RENDERER_MANIFEST_INVALID")
        for relative, digest in files.items():
            if file_sha256(contained_file(self.root, relative)) != digest:
                raise RendererError("RENDERER_FILE_CHECKSUM")
        return manifest

    def invoke(self, request: dict[str, Any]) -> dict[str, Any]:
        manifest = self.verify()
        executable = contained_file(self.root, manifest["executable"])
        request_bytes = (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(request_bytes) > 2 * 1024 * 1024:
            raise RendererError("RENDERER_REQUEST_LIMIT")
        try:
            process = subprocess.run(
                [str(executable), "--assets", str(self.root / "assets")],
                input=request_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=float(os.getenv("POSM_RENDER_TIMEOUT_SECONDS", "120")), check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                cwd=self.root,
            )
        except subprocess.TimeoutExpired as error:
            raise RendererError("RENDERER_TIMEOUT") from error
        except OSError as error:
            raise RendererError("RENDERER_START_FAILED") from error
        if process.returncode:
            raise RendererError("RENDERER_PROCESS_FAILED")
        if len(process.stdout) > 1024 * 1024:
            raise RendererError("RENDERER_RESPONSE_LIMIT")
        try:
            response = json.loads(process.stdout)
        except (ValueError, UnicodeDecodeError) as error:
            raise RendererError("RENDERER_RESPONSE_INVALID") from error
        if response.get("protocol_version") != 1 or response.get("request_id") != request["request_id"]:
            raise RendererError("RENDERER_RESPONSE_MISMATCH")
        if response.get("ok") is not True:
            raise RendererError(response.get("error", {}).get("code", "RENDERER_FAILED"))
        return response["result"]


def runtime_target() -> str:
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RendererError("RENDERER_PLATFORM_UNSUPPORTED")
    if platform.system() == "Windows":
        return "windows-x86_64"
    if platform.system() == "Linux":
        return "linux-x86_64"
    raise RendererError("RENDERER_PLATFORM_UNSUPPORTED")
