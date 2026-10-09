# VoxCPM2 Local SRT Dubbing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Implement the desktop SRT tab to parse/edit subtitles, synthesize locally through VoxCPM2, preserve timeline with bounded pitch-preserving acceleration, and export a complete WAV or MP3.

**Architecture:** Client owns SRT parsing, validation, editable cue table, output selection and asynchronous progress UI. Sidecar adds bounded `/srt` job API, synthesizes the full cue list under the existing inference lock, stretches/composes timeline audio, and serves completed-only audio result.

**Tech Stack:** Python, PySide6, FastAPI, VoxCPM2, librosa, soundfile, ffmpeg/libmp3lame, pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-voxcpm2-srt-dubbing-design.md`

## Global Constraints

- Use VoxCPM2 sidecar bound only to `127.0.0.1`; never MiniMax/cloud fallback.
- Do not alter server/backend, secrets, Compose, PostgreSQL, Redis, or existing user changes.
- Accept 1–100 cues; cue text <=4,000 chars, total <=40,000 chars; timeline <=30 minutes.
- Preserve cue start/end timeline; reject overlaps; stretch only as needed, maximum rate 1.5×; fail without final output when it cannot fit.
- One complete mono 44.1 kHz track; WAV PCM16 or MP3 CBR 128 kbps.
- Delete intermediate/reference/result files on failure, expiry and shutdown; no partial result downloads.
- Keep job/UI work asynchronous; no commit.

## Review Focus

- Malformed SRT blocks, UTF-8 BOM/invalid encoding, timestamps and multi-line cue text must fail precisely without silently dropping content.
- Cue overlap, invalid duration, cue-count/text/timeline bounds must be rejected before inference.
- Cue time stretching must not crop speech, exceed 1.5×, or create output on failure.
- Uploaded references, job temp directories, result polling/download and shutdown cleanup must be safe under concurrent jobs.
- Qt worker lifetimes, output overwrite/collision and failure paths must not freeze/crash or claim false success.

---

### Task 1: SRT parser and timeline validator

**Files:** Create `client/llvoice/srt.py`; create `client/tests/test_srt.py`.

**Interfaces:** `SrtCue(index: int, start_ms: int, end_ms: int, text: str)` dataclass; `parse_srt(content: str) -> list[SrtCue]`; `parse_srt_bytes(content: bytes) -> list[SrtCue]`; `validate_cues(cues: list[SrtCue]) -> list[SrtCue]` returns normalized ordered cues or raises `SrtError` with block/cue context. Implement 100 cue, 4,000 cue text, 40,000 total chars, nonnegative timestamps with start<end, unique positive indices, nonempty text, 30-minute end cap and no overlaps.

- [x] Write tests for UTF-8/BOM, CRLF/LF, multiline text, absent trailing blank, malformed/duplicate indices, invalid timestamps/empty cue, overlap/adjacent cues, non-contiguous indices, and all limits.
- [x] Run focused tests; observed missing module, then one test expectation mistakenly used chronologically ordered input. Corrected it to truly out-of-order; no production deviation.
- [x] Implement parser/validator in `client/llvoice/srt.py` with readable cue-specific `SrtError`.
- [x] Run focused tests 12/12 and full client suite 55/55.

### Task 2: Sidecar timeline renderer and time stretch

**Files:** Create `local-voxcpm/tests/test_srt_audio.py`; modify `local-voxcpm/app.py` or create focused `local-voxcpm/srt_audio.py`.

**Interfaces:** `render_srt_audio(cues, generated_wavs, native_sample_rate, output_format, destination)` (or testable equivalent) validates fit, applies `librosa.effects.time_stretch` only when needed and <=1.5×, lays mono samples on 44.1 kHz timeline with silence, ends at final cue end, emits PCM16 WAV or CBR 128k MP3. Overlong cue raises structured cue position/index error; never leaves destination.

- [x] Add deterministic waveform tests for no-stretch fit, necessary stretch/boundary, overlong cue, gaps/start/end duration, mono/rate/PCM output and MP3 profile.
- [x] Run focused tests; observed missing helper module, then corrected test fixtures to represent >1.0× and exact 1.5× stretch correctly.
- [x] Implement testable audio rendering and atomic final output.
- [x] Run focused tests 5/5 and full sidecar suite 27/27.

### Task 3: Sidecar asynchronous SRT job API

**Files:** Create `local-voxcpm/tests/test_srt_api.py`; modify `local-voxcpm/app.py`; update `local-voxcpm/README.md`.

**Interfaces:** `POST /srt` multipart fields `cues` (JSON array with index/start_ms/end_ms/text), optional `style`, `output_format`, optional `reference_audio`; returns 202 `{job_id}`. `GET /srt/{job_id}` status `{status, completed_cues, total_cues, failed_position, failed_index, error_code, error}`. `GET /srt/{job_id}/download` returns final matching WAV/MP3 only on completed. Validate limits/reference before inference; retain max 8 jobs, TTL 15 minutes, clean on shutdown/expiry; use shared inference lock for whole job, progress sequentially, no partial download.

- [x] Write fake-model API tests covering order/reference/style, progress, WAV, overlap/limits/reference rejection pre-inference, failure/no download and unknown jobs.
- [x] Run focused test; all four route tests observed expected 404 before implementation.
- [x] Implement job registry/workers, validation, reference cleanup, renderer integration and routes.
- [x] Run focused tests 4/4 and full sidecar suite 31/31. README documentation remains to add in final task pass.

### Task 4: Desktop SRT API client

**Files:** Modify `client/llvoice/voxcpm_api.py`; create `client/tests/test_voxcpm_api_srt.py`.

**Interfaces:** `start_srt(cues: list[SrtCue], style: str|None, output_format: str, reference_audio: Path|None) -> str`; `srt_status(job_id: str) -> dict`; `download_srt(job_id: str) -> bytes`. UUID-validate jobs, enforce loopback endpoint and response format/content type.

- [x] Write tests for multipart body/reference/format, status/download and invalid IDs/responses.
- [x] Run focused tests; observed missing methods. Initial implementation accidentally marked an instance method static; focused test exposed it and it was corrected.
- [x] Implement methods using existing timeout/error mapping and no external API.
- [x] Run focused tests 3/3 and full client suite 58/58.

### Task 5: SRT desktop page and tab integration

**Files:** Create `client/llvoice/ui/srt_page.py`; modify `client/llvoice/ui/main_window.py`, `client/llvoice/locales/vi.json`; create `client/tests/test_srt_page.py`; modify `client/tests/test_main_window.py` as needed.

**Interfaces:** `SrtPage(client, thread_pool=None)` offers file open and pasted SRT input through the same `parse_srt`, editable cue table (read-only original cue index; editable start/end/text), style/reference, WAV/MP3 and output path. Before submit, re-validate edits; asynchronous submit/poll/download/save; display cue progress and recover controls on all failures. Replace only SRT placeholder.

- [x] Write UI tests for paste/file shared parser, edit reflected in submitted cue payload, invalid/overlap disabled, success/progress, error/control recovery, format/save/collision; retain other placeholders.
- [x] Run focused tests offscreen; observed missing SRT page module. One success-string assertion was corrected to match localized success wording.
- [x] Implement page and replace only SRT placeholder; save uses exclusive-create to avoid overwrite.
- [x] Run focused UI/window tests 4/4 and full client suite 61/61.

### Task 6: Final regression and scope verification

- [ ] Run full sidecar, client offscreen, and server suites after final safety fix; previous full results sidecar 34/34, client 63/63, server 2/2. Warnings: Starlette/httpx deprecation; existing client duplicate ZIP fixture. A combined PowerShell line used an invalid server-relative path once; server suite reran successfully from `server/`.
- [x] Run `git diff --check`; verify server/config unchanged and pre-existing Compose diff preserved; sidecar listener is `127.0.0.1:7861`.
- [ ] Manual RTX 3060 model test pending user run.
- [x] Do not commit.
