# VoxCPM2 Local Sidecar Integration — Design

**Date:** 2026-09-30  
**Status:** Updated draft for user review (adds local reference-audio voice cloning)

## Goal

Let the developer test VoxCPM2 TTS from the existing LLVoiceTool Windows desktop app on their local RTX 3060 PC, without changing or disrupting the existing MiniMax proxy path.

This is a developer-local experiment, not a customer-facing feature or production deployment.

## Current project context

- `client/` is a PySide6 desktop application. It currently shows placeholder tabs and makes a background `GET /health` request to the proxy.
- `server/` is a FastAPI proxy skeleton whose only endpoint is `/health`; the project is designed around MiniMax API proxying and has no TTS engine abstraction.
- `deploy/docker-compose.yml` runs PostgreSQL and Redis for the existing proxy. The MiniMax server and its containers are to remain separate from local VoxCPM inference.
- VoxCPM2 is installed separately at `C:\Users\manhl\voxcpm` in a Python 3.12 virtual environment. The checkpoint is cached under that directory; a smoke test has generated Vietnamese and English WAV files using the RTX 3060.

## Chosen architecture

Run VoxCPM2 as a local sidecar service, separate from the MiniMax FastAPI proxy. The desktop client calls the sidecar directly over loopback (`127.0.0.1`). The sidecar owns the VoxCPM Python environment, model loading, GPU inference, and output encoding. The desktop client owns user input, request lifecycle, and playback/save UX.

```text
PySide6 desktop client
    ├── existing API client ──> MiniMax proxy :8000 (unchanged)
    └── local VoxCPM client ──> VoxCPM sidecar 127.0.0.1:<dedicated-port>
                                      └── RTX 3060 / local checkpoint
```

The sidecar will bind only to `127.0.0.1`, not `0.0.0.0`; it is not exposed to LAN or the Internet. It will run from the existing isolated VoxCPM venv and will not add PyTorch/CUDA dependencies to `server/` or the `llvoiceturn` shared environment.

## Initial scope

### Included

- A developer-local VoxCPM sidecar with a small HTTP API for health/readiness and speech generation.
- A minimal desktop TTS test view integrated into the existing UI, with a VoxCPM engine choice, text input, a generate action, and generated-audio playback/save.
- Vietnamese and English text generation, with voice design instructions supported using VoxCPM2's native text instruction format where practical.
- Optional voice cloning from a user-selected WAV or MP3 reference audio file, without requiring a transcript. When supplied, the file is sent only to the local sidecar and passed to VoxCPM2 as `reference_wav_path`; when omitted, standard TTS behavior is unchanged.
- MP3 reference support is local-only. The sidecar decodes it to a temporary WAV using the existing `librosa` dependency in the VoxCPM environment, and returns an actionable validation error if decoding fails. WAV input is also validated before inference. Reference files are limited to 25 MiB and 30 seconds.
- Loading the model once in the sidecar process and reusing it for requests.
- A single inference at a time (queue or explicit busy response); no promise of concurrent generation on one RTX 3060.
- Clear errors for unavailable sidecar, model-loading failure, invalid/empty text, and inference failure.
- A documented local start/stop workflow, using the existing `C:\Users\manhl\voxcpm\.venv` and cached model.

### Explicitly excluded

- Any MiniMax proxy, API key, authentication, credit/billing, customer account, database, or Redis changes.
- Shipping the sidecar to customers, remote GPU hosting, Dockerizing VoxCPM2, LAN/Internet access, or production hardening.
- Replacing or removing any MiniMax functionality.
- Broad redesign of the six-tab desktop interface.

## Request and response flow

1. Developer starts the VoxCPM sidecar using the isolated venv and a local command/script.
2. Desktop client probes the sidecar health endpoint without blocking the UI thread.
3. User enters text and requests generation in the local test view.
4. Client submits the request to loopback from a background worker, preserving UI responsiveness.
5. If reference audio was selected, client sends it to a separate multipart `POST /clone` endpoint on loopback; sidecar validates it locally, enforces the 25 MiB/30-second limits, decodes MP3 to a temporary WAV via existing `librosa`, and passes the WAV as VoxCPM2's `reference_wav_path`, without requiring transcript text. Plain TTS continues using JSON `POST /generate`.
6. Sidecar invokes the already-loaded VoxCPM2 model on CUDA and returns WAV audio.
7. Client presents a clear success/error state and makes the generated audio playable and saveable. Temporary reference files are removed after the request completes or fails.

The exact endpoint and payload shape will be fixed in the implementation plan, following the existing project conventions where they fit. It must not route VoxCPM requests through the MiniMax proxy.

## Lifecycle and errors

- Initial development workflow may start the sidecar manually in a separate terminal. Automatic child-process management by the desktop app is not required for the first slice unless exploration finds it is essential to a usable test.
- Sidecar readiness must distinguish “process reachable” from “model loaded and ready,” because the first model initialization can take time.
- The UI must not freeze during health checks, model load, or synthesis.
- If the sidecar is not running, show a concise instruction to start it; do not silently fall back to MiniMax or another engine.
- If CUDA/model initialization or generation fails, return a useful local error without changing proxy state.
- Generated audio, selected reference audio, and any temporary request/conversion files stay local. Temporary reference files are deleted after processing, including error paths; no telemetry or remote upload is added.
- Accept only `.wav` and `.mp3` reference files. Maximum upload size is 25 MiB and maximum decoded duration is 30 seconds; reject invalid or unsupported audio with a clear local error.

## Security and data handling

- Bind to loopback only.
- No API key is needed for loopback in this developer-only scope.
- Do not log full synthesis text, audio content, or uploaded filenames by default.
- The reference-audio endpoint is reachable only over loopback and is part of this developer-local test feature; no uploaded audio is retained after inference.

## Testing and acceptance criteria

1. Existing server and client tests continue to pass; existing MiniMax API client behavior is unchanged.
2. Sidecar unit/API tests use a mocked model and run without CUDA/model downloads.
3. The health endpoint reports not-ready during initialization and ready after model load.
4. A client request to an unavailable sidecar produces a visible actionable error and does not hang/freeze the UI.
5. On the developer's machine, starting the sidecar and generating one Vietnamese and one English sample succeeds using the RTX 3060 and produces valid WAV output.
6. The sidecar is confirmed to listen only on loopback; no Docker service or MiniMax configuration is changed by starting it.
7. A manual stop of the sidecar returns the app to an offline state while the MiniMax proxy and its existing services remain unaffected.
8. With a valid WAV or MP3 reference, cloning succeeds without a transcript; the output is a valid playable WAV. Without a reference file, existing plain-TTS behavior remains unchanged.
9. Invalid format, corrupt audio, oversized file, or excessive duration is rejected with a visible actionable error; temporary source/conversion files are removed after success and failure.

## Implementation details to settle in the plan

- Temporary-file ownership and cleanup on validation failure, inference error, and success.
- How to keep sidecar source discoverable in the repository while runtime stays in `C:\Users\manhl\voxcpm\.venv`.

Endpoint shape is multipart `POST /clone` for reference audio and JSON `POST /generate` for plain TTS. Limits are 25 MiB/30 seconds, and MP3 decoding uses existing VoxCPM-environment `librosa`. Remaining details must stay within the boundaries and constraints above.
