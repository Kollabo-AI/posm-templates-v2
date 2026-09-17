# POSM Templates v2

Python POSM service using a compiled native renderer. Native templates:
`sasa_202607001`, `sasa_202607006`, and `sasa_202609001`. Legacy names
`sasa_202604002` and `sasa_202607002` through `sasa_202607005` reuse the square
layout, preserving their artwork and semantic template identity. The service preserves `template`,
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
Missing image references are skipped, matching the existing worker. Text binding
roles and indices are normalized for the existing editor and regional conversion.
The platform does not currently support regional conversion of the two-slot 09001.

For an externally installed bundle, set `POSM_RENDERER_BUNDLE` and a trusted
`POSM_RENDERER_MANIFEST_SHA256`. The launcher hashes the manifest, executable,
fonts, and assets before every invocation. Missing or changed files fail closed.
`POSM_RENDER_TIMEOUT_SECONDS` defaults to 120.

The Dockerfile targets the existing x86-64 Lambda Python 3.12 base digest and
requires the Linux bundle. A Windows bundle does not satisfy that build. Container
packaging includes the existing runtime secret resolver. Secrets are resolved only
inside Lambda, using `JOB_WORKER_SECRET_ARN`; no secret values belong in this repo.

## Releases and dev deployment

Run `python scripts/verify_release.py` to check manifest hashes, production-only
bundles, all eight template names, callbacks, previews, and legacy artwork.
CI runs this gate on Ubuntu and inside the read-only Lambda container.
Pushes to `main` run CI. Pushes to `dev` additionally publish the verified image
to the existing Singapore ECR repository and update `posm-templates-lambda-dev`
through CloudFormation using GitHub OIDC. The deployment checks ECR findings,
the image digest, x86-64 architecture, the SQS mapping, and a Lambda invocation.
Production has no deployment trigger in this repository.

Build the private renderer in its own checkout using its release build scripts.
Install only its stripped runtime bundle using the private `install_bundle.py`;
this updates `renderer/<platform>` and `app/release_pin.py`. Never copy private
Rust source, layouts, Cargo files, debug symbols, or private repository history
into this repository or its Docker context. CI never checks out that repository.
The Docker allowlist includes only the Linux runtime bundle. Keep binary bundle
files byte-for-byte intact and retain the Linux executable bit when committing.

To roll back dev, restore both `DevPosterImageUri` and `DevPosterArchitecture`
on the `posm-singapore-data-audit` stack. The previous digest and architecture are
recorded in each successful deployment's Actions summary. Legacy images use arm64;
v2 images use x86_64. Preserve every other stack parameter.

Fabric JSON targets 6.6.5 and retains editable text and semantic field bindings.
The current port is experimental: native/browser raster differences and remaining
template calibration are documented in the private renderer's validation report.
