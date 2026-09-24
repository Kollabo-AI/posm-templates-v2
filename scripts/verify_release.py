"""Release gate: only compiled renderer bundles; exercise their real contract."""
from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from app.native import Bundle, file_sha256
from app.release_pin import RENDERER_RELEASES
from app.template_contract import NATIVE_TEMPLATES, SUPPORTED_TEMPLATES, legacy_background
from render_service import render_payload
from worker import successful_callback


def verify_files() -> None:
    if (ROOT / ".git").exists():
        tracked = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=ROOT).decode().split("\0")
        for entry in filter(None, tracked):
            metadata, filename = entry.split("\t", 1)
            path = Path(filename)
            assert not metadata.startswith("160000"), f"Submodule cannot enter public service: {filename}"
            assert path.suffix.lower() not in {".rs", ".toml", ".pdb", ".debug", ".cpp", ".h"}, filename
            assert path.name != "Cargo.lock" and not {"src", "target", "private-symbols", "posm-renderer"}.intersection(path.parts[:-1]), filename
    for target, digest in RENDERER_RELEASES.items():
        folder = ROOT / "renderer" / target
        if not folder.exists():
            continue  # Container includes only its Linux bundle.
        manifest = Bundle(folder, digest).verify()
        assert manifest["development"] is False, target
        expected = {*manifest["files"], "manifest.json"}
        actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
        assert actual == expected, f"Unexpected or missing runtime files: {actual ^ expected}"
        for filename in expected:
            assert Path(filename).suffix.lower() not in {".rs", ".toml", ".pdb", ".debug", ".cpp", ".h"}, filename
    folder = ROOT / "app" / "legacy_backgrounds"
    hashes = json.loads((folder / "checksums.json").read_text())
    assert set(hashes) == {f"sasa_20260700{i}.png" for i in range(2, 6)}
    for filename, digest in hashes.items():
        assert file_sha256(folder / filename) == digest, filename


def smoke(output: Path | None = None) -> None:
    described = Bundle.configured().invoke({"protocol_version": 1, "request_id": "release-gate", "method": "describe"})
    assert described["devtools"] is False
    assert {entry["id"] for entry in described["templates"]} == set(NATIVE_TEMPLATES)
    image = Image.new("RGBA", (96, 160), (30, 100, 180, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    reference = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    fields = {"hk_mo_price": "HK", "brand_name": "Release Test", "product_name": "Product 30ml",
              "price_recommended": "$200", "price_vip": "$150", "fab": "Feature one\nFeature two",
              "tnc": "Terms apply", "discount_ball": "75折", "gwp_text": "Gift 10ml", "gwp_image": [reference]}
    for template in SUPPORTED_TEMPLATES:
        request = {"template": template, "promotion_list": [fields], "product": [[reference]]}
        if template == "sasa_202609002":
            request.update(width=1000, height=1000)
        result = render_payload(request)
        assert result.successful, (template, result.message)
        callback = successful_callback(result)
        assert callback["result"]["thumbnailUrl"].startswith("data:image/jpeg;base64,")
        canvas = result.fabric_model.model_dump(mode="json")
        preview = Image.open(io.BytesIO(base64.b64decode(result.reference_jpg.split(",", 1)[1])))
        assert preview.size == (canvas["width"], canvas["height"])
        assert canvas["version"] == "6.6.5" and canvas["objects"]
        bindings = []
        def visit(node):
            if "posmBinding" in node:
                bindings.append(node["posmBinding"])
            for child in node.get("objects", []): visit(child)
        for node in canvas["objects"]: visit(node)
        assert bindings and all(b["template"] == template for b in bindings)
        assert any(b["field"].endswith("brand_name") and b["role"] == "line" for b in bindings)
        assert all(b["role"] == "token" for b in bindings if b["field"].endswith("discount_ball"))
        background = legacy_background(template)
        if background:
            actual = Image.open(io.BytesIO(base64.b64decode(canvas["backgroundImage"]["src"].split(",", 1)[1]))).convert("RGBA")
            expected = Image.open(background).convert("RGBA")
            assert actual.size == expected.size and actual.tobytes() == expected.tobytes(), template
        if output:
            output.mkdir(parents=True, exist_ok=True)
            (output / f"{template}.json").write_text(json.dumps(canvas, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"template": template, "successful": True, "size": preview.size, "bindings": len(bindings)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--files-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    verify_files()
    if not args.files_only: smoke(args.output)
