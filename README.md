# POSM Templates v2

Python POSM service using a compiled native renderer. Supported templates:
`sasa_202607001` and `sasa_202609001`. The service preserves `template`,
`promotion_list: list[dict]`, `product: list[list[str]]`, and the existing result
fields: `id`, `reference_jpg`, `fabric_model`, `message`, `successful`.

Layout source and editable configuration belong to a separate private repository.
This repository ships Python normalization, image loading, transport adapters,
and compiled renderer bundles. No browser is needed at runtime.

## Run

Use Python 3.12 and install `requirements.txt`. A platform-matched verified bundle
must be installed under `renderer/windows-x86_64` or `renderer/linux-x86_64` and
its manifest hash pinned in `app/release_pin.py`.

Windows and Amazon Linux 2023 x86-64 bundles are included. Windows also needs
the Microsoft Visual C++ 2015–2022 x64 runtime. The Linux executable is built
and checked in the pinned Lambda base image.

```
python -m unittest discover -s tests
python templates_api.py
```

The local HTTP adapter serves `/health`, `/create`, `/api/create` on port 8123.
`render_service.render_payload` accepts exactly one render item. The unchanged
Lambda handler and worker retain the existing claim/callback and retry contract.
The native `POSMImplementation` adapter replaces the browser lifecycle.

Images remain HTTP(S), data URLs, or local paths at the Python boundary. The
adapter normalizes and bounds them before calling the executable. A single 09001
offer is padded on the right; explicit empty slots retain their positions.
07006 is not registered in this initial release.

For an externally installed bundle, set `POSM_RENDERER_BUNDLE` and a trusted
`POSM_RENDERER_MANIFEST_SHA256`. The launcher hashes the manifest, executable,
fonts, and assets before every invocation. Missing or changed files fail closed.
`POSM_RENDER_TIMEOUT_SECONDS` defaults to 120.

The Dockerfile targets the existing x86-64 Lambda Python 3.12 base digest and
requires the Linux bundle. A Windows bundle does not satisfy that build. Container
packaging does not provision credentials or change the existing AWS deployment.

Fabric JSON targets 6.6.5 and retains editable text and semantic field bindings.
The current port is experimental: native/browser raster differences and remaining
template calibration are documented in the private renderer's validation report.
