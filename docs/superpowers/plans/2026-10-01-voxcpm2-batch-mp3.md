# VoxCPM2 Batch MP3 Desktop Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use `- [ ]` checkbox syntax for tracking.

**Goal:** Make the LLVoiceTool desktop a VoxCPM2-local-only workflow that previews split text and exports each segment as ordered, joinable MP3 files, optionally cloned from a local reference recording.

**Architecture:** A pure client-side splitter creates the exact ordered preview. The desktop submits one multipart batch job to the loopback VoxCPM2 sidecar; the sidecar serializes inference, encodes each result with one explicit MP3 profile, and exposes job progress/result endpoints so the UI can show segment progress without blocking. The desktop validates the returned ZIP completely before extracting into the selected directory. Remove MiniMax proxy health-check/status and Account tab from desktop only; leave backend/config/Compose unchanged.

**Tech Stack:** Python 3.12, PySide6, FastAPI, existing `librosa`/`soundfile`, `ffmpeg` MP3 encoder available on this developer machine, `httpx`, `zipfile`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-01-voxcpm2-batch-mp3-design.md`

## Global Constraints

- Split on `.`, `,`, `/`, CR/LF; omit delimiters, trim, preserve order, and omit empty chunks.
- Create `001.mp3`, `002.mp3`, etc.; output profile is mono, 44.1 kHz, constant 128 kbps.
- Accept at most 100 segments and 4,000 characters per segment; reference is WAV/MP3, at most 25 MiB and 30 seconds.
- All synthesis/reference processing stays local on `127.0.0.1:7861`; no MiniMax fallback or remote request.
- Remove Account tab and desktop MiniMax API construction/health/status; preserve other placeholder tabs.
- Do not modify MiniMax server/config, PostgreSQL, Redis, Docker Compose, or the pre-existing uncommitted Compose edit.
- No silent overwrite; validate all ZIP members and destination collisions before writing any MP3.
- No new runtime dependency; use installed `ffmpeg` in the VoxCPM environment and fail actionably if unavailable.
- Do not commit; user owns commits.

## Review Focus

- Segmentation of Windows CRLF, consecutive delimiters, and blank lines must match the exact visible preview; pin in splitter unit tests (Task 1).
- Multipart requests missing or containing malformed/oversized reference audio must fail before any model inference; pin in sidecar validation tests (Task 2).
- Concurrent batch jobs must not interleave model calls or leak/delete another job's reference/temp files; pin in sidecar job lifecycle/concurrency tests (Task 2).
- Malicious, duplicate, unexpected, or oversized ZIP entries must be rejected before any file is written; pin in safe extraction tests (Task 3).
- A job failure, lost sidecar connection, and closing/starting a second UI job must not leave controls stuck or imply partial success; pin UI/client state tests (Task 4).

---

### Task 1: Canonical text segmentation

**Files:**
- Create: `client/llvoice/text_segments.py`
- Test: `client/tests/test_text_segments.py`

**Interfaces:**
- Produces `split_text_segments(text: str) -> list[str]`, imported by both preview and submit code.
- Splits on `.`, `,`, `/`, LF, CRLF, or CR. Delimiters are discarded. Trims each segment and drops empty values. Preserves original order.

- [x] **Step 1: Write failing unit tests** named `test_split_each_punctuation_delimiter`, `test_split_lf_crlf_and_blank_paragraphs`, `test_consecutive_delimiters_drop_empty_segments`, `test_trims_segments_and_preserves_order`, `test_empty_or_delimiter_only_input_returns_no_segments`.
- [x] **Step 2: Run RED** — import failed as expected because `llvoice.text_segments` did not exist.
- [x] **Step 3: Implement** `split_text_segments` with one regex splitting punctuation and all CR/LF forms.
- [x] **Step 4: Run tests** — focused tests 5/5 and full client suite 27/27 pass.

### Task 2: Sidecar batch job API and MP3 encoding

**Files:**
- Modify: `local-voxcpm/app.py`
- Modify: `local-voxcpm/tests/test_clone_api.py` or create `local-voxcpm/tests/test_batch_api.py`
- Modify: `local-voxcpm/README.md`
- Modify: `local-voxcpm/run.ps1` only if needed to configure/discover the existing ffmpeg executable

**Interfaces:**
- `POST /batch` multipart fields: `segments` (JSON array of strings), `style` (optional string), and `reference_audio` (optional uploaded WAV/MP3). Returns `202` JSON `{"job_id":"<uuid>"}`.
- `GET /batch/{job_id}` returns JSON `{"status":"queued|running|completed|failed","completed_segments":N,"total_segments":N,"failed_segment":N|null,"error":string|null}`. `completed_segments` advances only after each MP3 has been encoded.
- `GET /batch/{job_id}/download` returns `application/zip` only when completed; archive has exactly flat `001.mp3` through `NNN.mp3` members in order.
- Batch count 1..100; each segment nonblank and <=4,000 chars; style <=500 chars. Reference upload is streamed to a temp file with the existing 25 MiB bound, extension validation, and WAV/MP3 decode/duration checks (<=30 seconds). Validate the full payload/reference before queueing model work; cleanup upload/conversion files on every path.
- Worker holds the shared inference lock for the entire batch, calls VoxCPM2 sequentially, and updates job status after each segment. Reference applies to every segment. Existing `/generate` and `/clone` behavior remains unchanged.
- Encode generated arrays via `ffmpeg` subprocess using explicit mono, 44,100 Hz, libmp3lame CBR 128 kbps settings. Resolve binary with `shutil.which("ffmpeg")`; when unavailable, fail with structured `encoder_unavailable`. Keep only a bounded in-memory job registry (maximum 8 active/retained jobs); completed/failed jobs expire after 15 minutes, and all job directories are removed on expiry/shutdown. Never return a partial ZIP on failure; status identifies the 1-based failed segment.

- [x] **Step 1: Write failing sidecar tests**: `test_batch_returns_job_id_and_progresses_in_order`, `test_batch_archive_has_numbered_mp3_files_and_profile`, `test_clone_reference_is_used_for_every_segment`, `test_batch_serializes_whole_job`, `test_batch_rejects_invalid_lists_and_references_before_inference`, `test_batch_failure_reports_segment_and_has_no_download`, `test_batch_requires_ffmpeg_actionably`, unknown job behavior, and job directory cleanup on service shutdown.
- [x] **Step 2: Run RED** — five route tests observed 404s; one test initially referenced unavailable `shutil` before implementation, then was rerun against the endpoint contract.
- [x] **Step 3: Implement** bounded job registry, payload/reference validation, asynchronous workers, whole-batch inference locking, ffmpeg encoding, progress/status, completed-only ZIP download, expiry and shutdown cleanup.
- [x] **Step 4: Run sidecar tests** — batch-focused 9/9 and full sidecar 22/22 pass (with existing Starlette/httpx deprecation warning).
- [x] **Step 5: Document** endpoints, polling, limits, profile, join/remux guidance, expiry and ffmpeg requirement in README.

### Task 3: Local batch client and safe ZIP extraction

**Files:**
- Modify: `client/llvoice/voxcpm_api.py`
- Create: `client/llvoice/batch_audio.py`
- Test: `client/tests/test_voxcpm_api_batch.py`
- Test: `client/tests/test_batch_audio.py`

**Interfaces:**
- `VoxCPMClient.start_batch(segments: list[str], style: str | None, reference_audio: Path | None) -> str` returns `job_id`.
- `VoxCPMClient.batch_status(job_id: str) -> dict[str, object]` reads sidecar progress; `VoxCPMClient.download_batch(job_id: str) -> bytes` accepts only ZIP responses.
- `extract_batch_mp3(archive: bytes, destination: Path, expected_count: int) -> list[Path]` validates the entire archive before writing; permits exactly `001.mp3`..`NNN.mp3`, no directories, traversal, duplicates, extras, encrypted files, zero-byte files, or members above 25 MiB each; rejects any existing destination name and writes atomically via temporary siblings.

- [x] **Step 1: Write failing tests** for multipart contract/loopback URL/job-id validation/status/download and valid extraction, traversal/absolute/unexpected/duplicate entries, count mismatch, empty members, collisions, and no partial writes.
- [x] **Step 2: Run RED** — ZIP extractor import missing; after implementation two contract expectations needed updating to valid UUID and collision exceptions must propagate; corrected tests then passed.
- [x] **Step 3: Implement** typed client methods with current 300-second timeout and safe ZIP validation before writes; extraction uses temporary siblings and atomic replace.
- [x] **Step 4: Run tests** — focused 12/12 and client suite 39/39 pass (one expected duplicate-ZIP-name fixture warning).

### Task 4: Desktop batch UI and removal of MiniMax from desktop

**Files:**
- Modify: `client/llvoice/ui/tts_page.py`
- Modify: `client/llvoice/ui/main_window.py`
- Modify: `client/llvoice/main.py`
- Modify: `client/llvoice/locales/vi.json`
- Test: `client/tests/test_tts_page_batch.py`
- Test: `client/tests/test_main_window.py`
- Test: `client/tests/test_main.py` (create if entry-point behavior is not already tested)

**Interfaces:**
- TTS preview calls `split_text_segments`; a `QTimer` fires every 250 ms while one job is active, but every `batch_status` HTTP request runs inside a QRunnable (never on the UI thread). Show `segment completed_segments of total_segments`; stop polling at terminal state. A poll/network failure is shown as an error, never success.
- UI requires a non-empty output directory and at least one segment, offers reference/style as now, starts one sidecar batch, polls status, downloads ZIP, invokes `extract_batch_mp3`, then displays file count/path. While active, disable text/reference/style/output/generate controls; enable them on success/error.
- `MainWindow(voxcpm_client=None, thread_pool=None)` owns only the VoxCPM TTS dependency, has tabs `tab.tts`, `tab.srt`, `tab.voices`, `tab.queue`, `tab.history`, no Account tab, and no server `ApiClient`, proxy health-check runnable, or proxy status message.
- `main()` constructs and closes the VoxCPM client only; it must not construct the legacy MiniMax `ApiClient`.

- [x] **Step 1: Write failing UI/entry tests**: preview/submission parity, output path and empty-input gating, batch success/error state, Account absence/placeholder tabs, and local-client-only main entry.
- [x] **Step 2: Run RED** — tests failed on missing preview, old MainWindow API, and absent VoxCPM entry-point construction.
- [x] **Step 3: Implement** preview/output-directory/batch progress UI with QRunnable workers and QTimer polling; desktop now constructs only `VoxCPMClient`, and has no MiniMax health work or Account tab.
- [x] **Step 4: Run tests** — focused UI 5/5 and full client suite 43/43 pass offscreen; legacy UI tests now assert local MP3 batch behavior. QRunnable instances remain referenced until callbacks finish; page shutdown waits for active tasks.

### Task 5: Full regression, safety verification, and user test instructions

**Files:**
- Modify: `docs/superpowers/specs/2026-10-01-voxcpm2-batch-mp3-design.md` (mark implemented only after verification)
- Modify: `docs/superpowers/plans/2026-10-01-voxcpm2-batch-mp3.md` (execution checkboxes/results)
- No changes to MiniMax backend/config or `deploy/docker-compose.yml`

- [x] **Step 1: Run full suites** — sidecar 22/22, client 43/43, server 2/2. Warnings: one existing Starlette/httpx deprecation in sidecar/server and a duplicate ZIP fixture warning in client.
- [x] **Step 2: Verify scope/safety** — `git diff --check` passes. Sidecar listener is `127.0.0.1:7861`. No diffs in server or legacy client API/config; Compose diff is the pre-existing user's edit and was preserved.
- [ ] **Step 3: Manual RTX 3060 test (user-run)** — start sidecar and desktop, generate plain and reference-cloned batches, verify ordered MP3 playback/profile and MP3-aware remux, collision protection, missing-sidecar message. Not run by agent; leave unchecked.
- [x] **Step 4: Do not commit.** No commit created; leave worktree changes for user inspection.
