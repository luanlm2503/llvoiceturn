# VoxCPM2 Local Voice Cloning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing local VoxCPM2 test UI to clone a voice from a WAV or MP3 reference recording, without requiring a transcript or changing plain TTS/MiniMax behavior.

**Architecture:** Keep the current JSON `POST /generate` path for plain TTS. Add a distinct multipart `POST /clone` endpoint to the same loopback-only sidecar, passing a validated temporary WAV path to VoxCPM2 `generate(..., reference_wav_path=...)`. The desktop client submits the selected WAV/MP3 directly to the local sidecar, and the sidecar converts MP3 locally using the existing `librosa` dependency, then deletes request/conversion temp files.

**Tech Stack:** Python 3.12, FastAPI, VoxCPM 2.0.3, existing VoxCPM-environment `soundfile` and `librosa`, PySide6, httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-voxcpm2-local-sidecar-design.md`

## Global Constraints

- Sidecar binds only to `127.0.0.1`, never `0.0.0.0`.
- Run VoxCPM in `C:\Users\manhl\voxcpm\.venv`; do not add torch/CUDA/VoxCPM or audio-decoding dependencies to `server/`, `shared/`, or the client venv.
- Keep MiniMax proxy, API key/config, authentication, credit/billing, PostgreSQL and Redis behavior unchanged.
- Clone accepts WAV and MP3 only, with no transcript field. Reference audio is sent only over loopback.
- Maximum reference upload: 25 MiB. Maximum decoded duration: 30 seconds. Enforce these limits before inference.
- Convert MP3 to a temporary WAV using existing `librosa`; use `soundfile` for WAV validation and WAV serialization. No new runtime dependencies.
- All temporary source/conversion files are local and deleted after success or any error.
- Model loads once; use the existing inference lock for both `/generate` and `/clone`.
- HTTP/model work must not block the PySide6 UI thread. Do not log synthesis text, audio bytes, or reference filenames.

## Review Focus

- Invalid/renamed file extension, corrupt WAV/MP3, empty upload, and decoder errors: reject with actionable error before model invocation; Task 1 tests these cases.
- Exactly-at-limit and over-limit file sizes/durations: accept the stated limit and reject excess before inference; Task 1 tests boundaries.
- MP3 decode/inference fails midway: remove all temporary files and leave the process usable for a subsequent request; Task 1 tests cleanup/error mapping.
- User cancels the file dialog or selects a non-audio file: preserve plain TTS mode and do not send an empty/misleading reference; Task 3 tests this.
- Clone API unavailable or returns a non-WAV response: show an actionable UI error and restore controls; Tasks 2–3 test this.

---

## Existing implementation baseline

The base TTS/sidecar feature is in the current working tree per `docs/superpowers/plans/2026-09-30-voxcpm2-local-sidecar.md`:

- `local-voxcpm/app.py` has `POST /generate` JSON, a loaded model, and a single inference lock.
- `client/llvoice/voxcpm_api.py` has an isolated client for local sidecar requests.
- `client/llvoice/ui/tts_page.py` has text/style input, generate, playback and save controls.
- Existing focused tests pass: sidecar 6/6, client 13/13, server 2/2 at the last run. The pre-existing uncommitted Compose change must be preserved.

This plan changes only the clone capability and avoids redoing completed base tasks.

## Interfaces

Sidecar:
- Keep `POST /generate` JSON `{text, style}` unchanged.
- Add `POST /clone` multipart fields `text: str`, `style: str | None`, and `reference_audio: UploadFile`; success is `audio/wav` bytes and errors keep the existing `{detail:{code,message}}` shape.
- `VoxCPMProtocol.generate(*, text: str, cfg_value: float = 2.0, inference_timesteps: int = 10, reference_wav_path: str | None = None) -> numpy.ndarray`.
- VoxCPM receives `(style)text` when style is non-empty, and `reference_wav_path` only for clone requests.

Client:
- Preserve `VoxCPMClient.generate(text, style=None) -> bytes`.
- Add `VoxCPMClient.clone(text: str, reference_audio: Path, style: str | None = None) -> bytes`; uses HTTP multipart and validates WAV response exactly as `generate` does.

UI:
- Add `TtsPage.reference_path: Path | None`; add/select/clear controls. No reference means existing TTS call; selected valid reference means `.clone(...)`.

---

### Task 1: Add validated local clone endpoint

**Files:**
- Modify: `local-voxcpm/app.py`
- Modify: `local-voxcpm/tests/test_api.py`
- Modify: `local-voxcpm/README.md`

**Interfaces:**
- Consumes current model lifecycle and inference lock from `app.py`.
- Produces `POST /clone` with multipart `text`, optional `style`, and required `reference_audio`; updates `VoxCPMProtocol.generate` to accept optional `reference_wav_path` without changing the plain endpoint.
- Enforce max bytes `25 * 1024 * 1024`, supported extensions `.wav`/`.mp3`, nonempty audio, decodable audio, and duration `<=30.0` seconds.

- [x] **Step 1: Write failing endpoint tests**: `test_clone_wav_passes_reference_path_and_returns_wav`, `test_clone_mp3_is_decoded_and_passed_as_wav`, `test_clone_rejects_empty_unsupported_corrupt_and_oversized_uploads`, `test_clone_rejects_duration_over_30_seconds`, `test_clone_temp_files_are_removed_after_inference_failure`, `test_clone_preserves_plain_generate_without_reference`. Tests use a fake model and tiny generated audio and exercise real WAV/MP3 decode behavior.
- [x] **Step 2: Run RED** — observed expected missing-route 404s (5 failures); plain TTS regression test passed.
- [x] **Step 3: Implement endpoint** using multipart `UploadFile`; bounded temp writes; WAV validation via `soundfile`; MP3 decode via existing `librosa`; duration check; existing inference lock; `finally` cleanup.
- [x] **Step 4: Run sidecar tests** — full suite 12/12 pass without CUDA/model downloads.
- [x] **Step 5: Document clone endpoint and limits** in README: WAV/MP3, no transcript, 25 MiB max, 30-second max, local-only temporary processing.

### Task 2: Add desktop clone HTTP client

**Files:**
- Modify: `client/llvoice/voxcpm_api.py`
- Modify: `client/tests/test_voxcpm_api.py`

**Interfaces:**
- Consumes `POST /clone` from Task 1.
- Produces `VoxCPMClient.clone(text, reference_audio: Path, style=None) -> bytes`.

- [x] **Step 1: Write failing mocked HTTP tests**: `test_clone_posts_multipart_to_loopback_with_text_and_style`, `test_clone_rejects_non_wav_response`, `test_clone_unavailable_sidecar_has_start_instruction`, `test_clone_keeps_plain_generate_json_contract`. Assert URL stays loopback, multipart contract and WAV validation, and ordinary JSON unchanged.
- [x] **Step 2: Run RED** — expected missing `clone` method failures observed (3 failed, unchanged plain TTS contract passed).
- [x] **Step 3: Implement multipart client method**; closes the file handle within request lifetime and uses existing timeout/error/content checks.
- [x] **Step 4: Run targeted client tests** — 4/4 pass.

### Task 3: Add reference-file controls to the TTS page

**Files:**
- Modify: `client/llvoice/ui/tts_page.py`
- Modify: `client/llvoice/locales/vi.json`
- Modify: `client/tests/test_tts_page.py`

**Interfaces:**
- Consumes `VoxCPMClient.clone(text, reference_audio: Path, style=None)` from Task 2.
- Produces `TtsPage.reference_path: Path | None` and file selection/clear UI. Only `.wav` and `.mp3` can be selected; cancellation leaves current state unchanged. During worker execution, call `.clone` if a reference is selected, otherwise preserve `.generate`.

- [x] **Step 1: Write failing UI tests**: selection, cancel/clear fallback, clone-worker routing, unsupported extension, and clone failure recovery. Tests use mocked dialogs/client and assert TTS remains on `.generate` with no reference.
- [x] **Step 2: Run RED** — initial tests failed because chooser and clone routing were absent.
- [x] **Step 3: Implement minimal file chooser/clear controls**; selected basename shown, transcript not required and local handling explained. Clone call runs in background worker.
- [x] **Step 4: Run client suite** — offscreen full client tests pass 22/22.

### Task 4: Verify integrated behavior and update progress

**Files:**
- Modify: `docs/superpowers/specs/2026-09-30-voxcpm2-local-sidecar-design.md` only if implementation details become more precise (do not broaden scope).
- Modify: this plan's checkboxes/status as each step is verified.

- [x] **Step 1: Run full automated suites**: sidecar 12/12; client 22/22 offscreen; server 2/2. No CUDA needed for automated tests. Server/sidecar each report the existing Starlette/httpx deprecation warning.
- [x] **Step 2: Verify safety invariants**: pending final diff check; implementation paths do not include MiniMax server/proxy or Compose. Runner source remains bound to loopback; prior listener verification was from base TTS work.
- [ ] **Step 3: Manual real-GPU check (user-run)**: sidecar ready, plain TTS still works, clone from one WAV and one MP3 without transcript, play/save WAV, clear reference and verify plain TTS mode, stop sidecar and observe actionable offline message. This is intentionally left unchecked until the user performs it locally.

## Execution notes

- Execution approved inline/native on `main`; do not commit.
- **Task 1 complete:** sidecar clone API implemented; focused clone tests and full sidecar suite pass (12/12).
- **Task 2 complete:** isolated multipart client implemented; focused clone API tests pass (4/4).
- **Task 3 complete:** UI reference chooser/clear and clone routing implemented; focused UI clone tests pass (5/5).
- **Task 4 automated verification:** client 22/22, server 2/2, sidecar 12/12. Manual real-GPU WAV/MP3 clone test remains for the user.
- No new audio dependencies added; `librosa` existing MP3 decode path passed an MP3 fixture generated with ffmpeg in tests.
- Existing worktree changes including `deploy/docker-compose.yml` are preserved; no commit made.
- Final review: self-review (no subagent used).
