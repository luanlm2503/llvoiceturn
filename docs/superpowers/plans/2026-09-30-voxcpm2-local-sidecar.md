# VoxCPM2 Local Sidecar Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a developer-local VoxCPM2 speech-generation path to LLVoiceTool using an isolated loopback sidecar, without altering the MiniMax proxy path.

**Architecture:** The VoxCPM2 process lives outside the repo in `C:\Users\manhl\voxcpm\.venv` and exposes health/readiness and generation over `127.0.0.1`. The PySide6 client calls it with an independent client and background workers, then plays/saves returned WAV audio. MiniMax proxy and existing database/Redis containers remain untouched.

**Tech Stack:** Python 3.12, FastAPI/Uvicorn in the existing VoxCPM venv, `voxcpm==2.0.3`, PySide6, httpx, pytest, soundfile.

**Spec:** `docs/superpowers/specs/2026-09-30-voxcpm2-local-sidecar-design.md`

## Global Constraints

- Sidecar binds only to `127.0.0.1`, never `0.0.0.0`.
- Run VoxCPM in `C:\Users\manhl\voxcpm\.venv`; do not add torch/CUDA/VoxCPM to `server/`, `shared/`, or the client venv.
- Keep MiniMax proxy, API key/config, authentication, credit/billing, PostgreSQL and Redis behavior unchanged.
- First slice is developer-local TTS/voice design in Vietnamese and English; reference-audio cloning, customer deployment, Docker and remote access are excluded.
- Model loads once per sidecar process; serialize inference (one active CUDA generation at a time).
- HTTP/model work must not block the PySide6 UI thread.
- Do not log synthesis text/audio or send them to a remote service.

## Review Focus

- Sidecar process absent or not yet model-ready: client must show actionable offline/loading state without hanging; test in Task 4.
- Empty/whitespace or oversized text: reject with an actionable response without invoking the model; test in Task 2 and UI in Task 4.
- Model load and generate exception/OOM: readiness/error response must be clear and process must not return invalid WAV; test in Task 2.
- Simultaneous requests: serialize or return explicit busy/retryable response; test in Task 2.
- Audio response malformed or save destination invalid: present an error without freezing or corrupting an existing file; test in Task 3/4.

---

## File map

- Create `local-voxcpm/app.py`: sidecar FastAPI app factory, model lifecycle, `/health` readiness and `/generate` endpoint. Imports VoxCPM only in the external VoxCPM environment.
- Create `local-voxcpm/run.ps1`: launch sidecar with the existing external venv, loopback bind, stable port and UTF-8 output.
- Create `local-voxcpm/README.md`: local setup/start/stop and test commands.
- Create `client/llvoice/voxcpm_api.py`: independent typed HTTP client, health/generate methods, error mapping; never uses MiniMax `ApiClient` base URL.
- Create `client/llvoice/ui/tts_page.py`: minimal test page, engine selector, text input, generate, audio playback and save controls.
- Modify `client/llvoice/ui/main_window.py`: use the TTS page for the TTS tab; leave other tabs and proxy health status behavior intact.
- Modify `client/llvoice/locales/vi.json`: labels, progress, offline/loading/error/success strings.
- Create `client/tests/test_voxcpm_api.py`: mocked HTTP/API contract tests.
- Create `client/tests/test_tts_page.py`: UI behavior tests with a mocked sidecar client and temporary WAV fixtures.
- Create `local-voxcpm/tests/test_api.py`: sidecar endpoint tests with a mocked model, no CUDA or model download.

## Interfaces

Sidecar:
- `create_app(model_loader: Callable[[], VoxCPMProtocol] | None = None) -> FastAPI`
- `VoxCPMProtocol.generate(*, text: str, cfg_value: float = 2.0, inference_timesteps: int = 10) -> numpy.ndarray`
- `GET /health -> { "status": "loading" | "ready" | "error", "detail": str | null }`
- `POST /generate` JSON `{ "text": str, "style": str | null }` -> `audio/wav` bytes. The handler maps optional style to VoxCPM2's leading parenthetical instruction `(style)text`, applies a defined text limit (4,000 Unicode characters), serializes generation, and returns structured JSON errors for validation, not-ready/busy and model failures.

Client:
- `VoxCPMClient(base_url: str = "http://127.0.0.1:7861", timeout: float = 300.0)`
- `health() -> dict[str, object]`
- `generate(text: str, style: str | None = None) -> bytes`
- Client errors are a dedicated `VoxCPMApiError`, not `ApiError`; MiniMax `ApiClient` remains independent.

---

### Task 1: Add isolated VoxCPM sidecar API

**Files:**
- Create: `local-voxcpm/app.py`
- Create: `local-voxcpm/run.ps1`
- Create: `local-voxcpm/tests/test_api.py`
- Create: `local-voxcpm/README.md`

**Interfaces:**
- Produces `create_app(model_loader=None)`, `VoxCPMProtocol.generate(...)`, `GET /health`, `POST /generate` as defined above.
- The production loader calls `VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)` once at startup, with `HF_HOME=C:\Users\manhl\voxcpm\hf_cache`; no model load occurs at import time.
- The runner invokes `C:\Users\manhl\voxcpm\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 7861` with working directory `local-voxcpm` and UTF-8 Python output.

- [ ] **Step 1: Write sidecar API tests** named `test_health_loading_then_ready`, `test_generate_returns_wav_bytes`, `test_generate_rejects_empty_and_over_limit_text`, `test_generate_returns_not_ready_when_model_load_failed`, `test_generate_serializes_concurrent_calls`, and `test_generate_maps_model_exception_to_structured_error`. Use an injected fake model and `TestClient`; assert WAV content type/header, exact forwarded text/style and limits, response statuses, and that concurrent model calls never overlap.
- [ ] **Step 2: Run tests to verify failure** — `cd local-voxcpm; C:\Users\manhl\voxcpm\.venv\Scripts\python.exe -m pytest -q`; expected initial import/route failures.
- [ ] **Step 3: Implement sidecar** with startup model loading, readiness state, request validation (1–4,000 characters), a lock around generation, WAV encoding using `soundfile`, and structured errors. Style uses native `(description)text` syntax. Bind only on loopback through the runner.
- [ ] **Step 4: Run tests** with the same pytest command; expected all sidecar tests pass without CUDA/model download.
- [ ] **Step 5: Document and verify runner** — README documents manual start/stop and model cache; run `run.ps1`, verify listener is exactly `127.0.0.1:7861`, poll `/health` until ready, then stop and verify the port closes. Do not change Docker or MiniMax services.

### Task 2: Add isolated desktop HTTP client

**Files:**
- Create: `client/llvoice/voxcpm_api.py`
- Create: `client/tests/test_voxcpm_api.py`

**Interfaces:**
- Consumes sidecar contract from Task 1.
- Produces `VoxCPMClient`, `VoxCPMApiError`, `health()`, and `generate(text, style=None)` as defined above.

- [ ] **Step 1: Write mocked HTTP tests** named `test_health_uses_local_sidecar_url`, `test_generate_posts_text_and_style_and_returns_wav`, `test_unavailable_sidecar_raises_actionable_error`, `test_non_wav_response_is_rejected`, and `test_timeout_is_reported_as_sidecar_error`. Assert the client always uses its own loopback base URL and never the MiniMax `API_BASE_URL`.
- [ ] **Step 2: Run targeted test** — `cd client; ..\.venv\Scripts\python.exe -m pytest tests/test_voxcpm_api.py -q`; expected missing-module failures.
- [ ] **Step 3: Implement client** using an independent `httpx.Client`, timeout 300 seconds, WAV content-type/status checks, and dedicated exception messages for connection/timeout/server errors.
- [ ] **Step 4: Run targeted tests** — expected all pass.

### Task 3: Build responsive TTS test page with playback/save

**Files:**
- Create: `client/llvoice/ui/tts_page.py`
- Modify: `client/llvoice/locales/vi.json`
- Create: `client/tests/test_tts_page.py`

**Interfaces:**
- Consumes `VoxCPMClient.health()` and `.generate(text, style)` from Task 2.
- Produces a `TtsPage(QWidget)` accepting an injectable `VoxCPMClient` and emitting/running health/generate work via `QRunnable` and signals. Only WAV outputs are playable/saveable; use `QMediaPlayer` with `QAudioOutput`, temporary local WAV storage and a Save File dialog.

- [x] **Step 1: Write UI tests** named `test_generate_disabled_for_empty_text`, `test_generate_calls_sidecar_from_worker_and_displays_result`, `test_offline_health_shows_start_instruction`, `test_generation_error_is_visible_and_reenables_button`, and `test_save_uses_user_selected_path`. Use mocked client and temporary valid WAV; assert UI updates without direct MiniMax API calls.
- [x] **Step 2: Run targeted tests** — `cd client; ..\.venv\Scripts\python.exe -m pytest tests/test_tts_page.py -q`; expected missing page failures.
- [x] **Step 3: Implement page** with text and optional style fields, engine selector fixed to VoxCPM2 for this local test, asynchronous readiness/generation, loading/ready/offline/error states, playback and WAV save. Do not fall back to MiniMax.
- [x] **Step 4: Run targeted tests** — `cd client; ..\.venv\Scripts\python.exe -m pytest tests/test_tts_page.py -q`; 5/5 pass. Full client suite 13/13 pass.

### Task 4: Wire page into app and preserve existing proxy path

**Files:**
- Modify: `client/llvoice/ui/main_window.py`
- Modify: `client/tests/test_i18n.py` only if locale key assertions need extension; otherwise keep test file unchanged.
- Test: `client/tests/test_tts_page.py` and existing client tests.

**Interfaces:**
- Consumes `TtsPage` from Task 3.
- Existing `MainWindow(api: ApiClient)` signature and `/health` status bar behavior remain unchanged. Replace only the placeholder content for the existing `tab.tts`; retain other placeholder tabs and the existing proxy health check.

- [x] **Step 1: Add integration test** `test_main_window_uses_voxcpm_tts_page_and_keeps_other_tabs` using injected/mocked sidecar client support, asserting only TTS tab is functional, MiniMax proxy health is still called, and local TTS requests are not sent through `ApiClient`.
- [x] **Step 2: Run the test** — observed expected initial failure while API check ran asynchronously before queued worker completion; adjusted test to wait for worker signals.
- [x] **Step 3: Wire TTS page** into the TTS tab without changing other tab behavior or proxy health.
- [x] **Step 4: Run full tests** — server 2/2 passed; client 13/13 passed. Sidecar suite 6/6 passed.
- [ ] **Step 5: Manual end-to-end verification (left for user)** — start sidecar with its PowerShell script; start existing proxy/container services unchanged; launch desktop app; confirm status bar shows proxy status independently; generate Vietnamese and English audio from TTS tab; play and save valid WAV; stop sidecar and confirm actionable offline message while MiniMax proxy health and PostgreSQL/Redis remain available. Real model readiness/generation has not been fully verified in this session.

## Execution notes

- **Progress (2026-09-30):** Approved for inline implementation on `main`; do not commit. Baseline: client tests 2/2 passed; server tests 2/2 passed (one existing Starlette/httpx deprecation warning). Shared interfaces checked: Task 1 sidecar contract → Task 2 client → Task 3 TTS page → Task 4 app integration are consistent.
- **Task 1: mocked API tests 6/6; runner bind check verified `127.0.0.1:7861`.** Real model readiness was observed as `loading` during a short startup check, then process was stopped; full model-ready/generation end-to-end check remains for the user. **Task 2: HTTP client tests 5/5. Task 3: UI tests 5/5. Task 4: integration test 1/1; client 13/13, server 2/2, sidecar 6/6.** Manual real-GPU end-to-end verification remains unchecked for the user. Checkboxes below are updated after tests ran.
- Do not commit automatically; the repository owner commits changes.
- No need to edit the existing Docker Compose for this feature.
- The initial manual sidecar runner is intentional: it avoids coupling the desktop app to an external Python venv/model lifecycle. Automatic child-process launch can be a later improvement if requested.
- All files under `local-voxcpm/` are lightweight source/tests/docs; actual model weights remain in the already-populated external cache.
- **Final review:** self-review (no subagent used). Confirmed boundaries and test coverage; real-GPU e2e remains for user verification.
