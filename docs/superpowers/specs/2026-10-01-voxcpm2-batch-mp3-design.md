# VoxCPM2 Batch MP3 Desktop Workflow — Design

**Date:** 2026-10-01  
**Status:** Approved design; implementation complete pending manual RTX 3060 end-to-end verification

## Goal

Make LLVoiceTool desktop a local VoxCPM2-only TTS workflow for the developer: split text into ordered chunks using the agreed delimiters, synthesize each chunk to a joinable MP3 file, and optionally clone the selected reference voice. The desktop must not call MiniMax. Existing MiniMax server/proxy/configuration files remain in the repository as-is for possible future use.

## User-approved behavior

- Split at `.`, `,`, `/`, and line/paragraph breaks. The delimiters are removed; surrounding whitespace is trimmed; empty chunks are omitted.
- Show the exact ordered chunk preview before synthesis.
- Produce one numbered MP3 per chunk (`001.mp3`, `002.mp3`, …), preserving order.
- Use consistent MP3 encoding settings across all files so the outputs can be concatenated/joined.
- Use the current VoxCPM2 local sidecar for every chunk. If the user selected a WAV/MP3 voice reference, apply it to every chunk; otherwise use regular TTS. There is no MiniMax or remote fallback.
- The user chooses an output directory for the generated MP3 files.
- Remove the Tài khoản (Account) tab and the desktop's MiniMax proxy health-check/status bar. Keep other not-yet-implemented tabs as placeholders.
- Keep the MiniMax server, proxy configuration, environment files, and deployment setup in the repository untouched. This request removes MiniMax from the desktop application experience; it does not delete backend code/configuration or database/Redis files.

## Current project context

- `client/` is a PySide6 Windows desktop app. Its TTS tab now has a VoxCPM2 local sidecar health check, plain TTS/voice-style input, optional WAV/MP3 reference selection, playback, and WAV save.
- `local-voxcpm/` is a loopback-only FastAPI sidecar at `127.0.0.1:7861`; it has JSON `/generate` and multipart `/clone` endpoints. Clone input is limited to 25 MiB and 30 seconds and MP3 is decoded locally with the already-installed `librosa` in `C:\Users\manhl\voxcpm\.venv`.
- The sidecar uses VoxCPM 2.0.3 in a separate venv and loads its model once. Inference is serialized.
- The desktop currently receives one WAV per TTS request. It also still constructs the old MiniMax `ApiClient`, checks proxy `/health`, shows server status, and includes a placeholder Account tab.
- Existing code changes are uncommitted. `deploy/docker-compose.yml` already has a user change and must not be reverted or altered by this task.

## Proposed architecture

Continue to use VoxCPM2 as the only speech-generation engine in the desktop TTS view. Add chunk preview and batch controls to the existing TTS page. Send the ordered list in one local batch request to the sidecar; the sidecar synthesizes sequentially under the existing inference lock, converts each generated waveform to MP3 with the same explicit encoding profile, and returns the numbered files in one archive. The desktop extracts only validated numbered MP3 entries into the user-selected output directory. The existing single-item `/generate` and `/clone` endpoints remain available for compatibility unless implementation findings justify a shared internal refactor.

```text
PySide6 desktop (text split + preview + output directory)
   └── local VoxCPM client ──> 127.0.0.1:7861 batch endpoint
                                  ├── VoxCPM2 inference, one chunk at a time
                                  ├── optional local reference audio for every chunk
                                  └── numbered, consistently encoded MP3 files

MiniMax server/config remains in repository but is no longer constructed or called by desktop.
```

## Text segmentation

- Delimiters: period (`.`), comma (`,`), slash (`/`), CR/LF line breaks, and blank paragraph lines.
- Each delimiter ends the current chunk and is omitted from synthesized text.
- Trim chunks and drop empty chunks; keep source order.
- Consecutive delimiters do not produce empty outputs.
- The preview must be generated using the same segmentation function as batch submission, so displayed chunk count/order/text match the request.
- If all input is empty/delimiters/whitespace, disable generation and show a helpful validation state.

## MP3 output and file handling

- Every segment becomes its own `.mp3` named with a three-digit minimum; numbering starts at `001.mp3` and preserves order (`001.mp3`, `002.mp3`, …).
- Use one documented encoding profile for all files: mono, 44.1 kHz, constant 128 kbps MP3. The profile must be explicit, not delegated to per-file defaults.
- Ensure each MP3 is independently decodable and has consistent encoder delay/padding behavior appropriate for concatenation; document that simple byte concatenation is not the join method. Files must be joinable using a standard MP3-aware concat/remux operation.
- Use a local archive response (ZIP) for batch transport; the client extracts only safe, expected numbered `.mp3` entries. No generated audio is uploaded elsewhere.
- Sidecar reports progress by chunk index/count through an optional local job-status endpoint only if a single blocking batch response cannot meet UX requirements; the implementation plan should select one simple mechanism.
- Avoid overwriting existing output files silently. If names collide, report/ask the user to choose an empty or different directory before extracting.
- On partial synthesis/conversion failure, return a clear error including the failed segment index; do not present a partial batch as complete. No temporary audio or archive remains after request cleanup.

## Local batch API

The implementation plan will define exact names/payload, with these requirements:

- Add a sidecar batch endpoint accepting a JSON text chunk list, optional style, and either no reference or one optional WAV/MP3 reference file.
- Validate 1–4,000 characters per chunk, non-empty chunk list, bounded count (maximum 100 segments), supported reference file and existing 25 MiB/30-second reference limits before starting inference.
- Synthesize sequentially under the shared inference lock; one request owns the lock for its whole batch so chunks from separate batch requests cannot interleave.
- Return a ZIP archive containing exactly `001.mp3` through `NNN.mp3`, with safe flat filenames only. Structured errors identify validation, readiness, inference, conversion, and archive-generation failures.
- Sidecar remains loopback-only; no MiniMax calls or server proxy involvement.

## Desktop UI and interaction

- Keep engine fixed to VoxCPM2 local; no engine selector suggesting MiniMax.
- Add/retain text input and optional style/reference audio controls.
- Show a live or explicit “Preview chunks” ordered list/count generated by the canonical splitter.
- Add an output-directory chooser.
- Generate all chunks asynchronously; show progress (`segment i of N`), disable conflicting controls while running, and allow a user-visible error with failed segment number.
- On success, extract numbered MP3 files into the chosen directory and show its path/count. Provide an option to open the folder if consistent with existing Windows conventions.
- Keep playback/save WAV controls for the existing one-shot experience only if useful, but batch outputs must be MP3. Do not replace a completed batch silently on a second run.
- Remove Account tab and all desktop proxy health/status work. Keep TTS tab plus the existing SRT/voices/queue/history placeholders.

## Error handling and security

- Do not freeze the Qt UI during health checks, batch generation, MP3 conversion, or extraction.
- If the sidecar is missing/loading, show the existing actionable local start instruction; do not fall back to MiniMax.
- Reject unsafe ZIP paths, unexpected extensions/names, duplicate entries, and unexpected entry counts before writing any file.
- Use temporary directories with guaranteed cleanup for received archives and sidecar intermediates.
- Keep reference audio, text, generated audio, and ZIP contents local; do not log or upload their contents.
- Sidecar stays bound to `127.0.0.1`; no auth/remote hosting/Docker changes are introduced.
- Do not alter MiniMax server source, credentials, config, Compose, PostgreSQL, Redis, or the existing uncommitted Compose adjustment.

## Testing and acceptance criteria

1. Unit tests cover splitting by every delimiter, CRLF/LF and paragraphs, consecutive delimiters, trimming/order, and all-empty input.
2. Sidecar API tests use a fake model and tiny audio, verify 1..N numbered MP3 ZIP contents, consistent profile metadata, reference forwarded for every chunk, sequential call order, and one lock across the batch.
3. Sidecar rejects empty/over-limit chunk lists, oversized individual text, malformed reference audio, unsupported extensions, and more than 100 chunks without inference.
4. Batch failures identify the failing segment, return no successful-looking partial archive, and remove all temp files.
5. Client tests validate the loopback URL, multipart/JSON request shape, ZIP response content, and actionable errors without using the MiniMax API base URL.
6. Desktop UI tests confirm preview matches submitted chunks, output directory is required, progress/error/success states update, safe extraction rejects traversal/collisions, and no MiniMax client or health-check is called/constructed.
7. Desktop tests confirm Account tab is absent and other placeholder tabs remain.
8. Existing client, server, and sidecar test suites continue to pass; backend MiniMax source/config and `deploy/docker-compose.yml` remain unchanged.
9. On the developer's RTX 3060, plain local TTS and reference-clone batches generate ordered playable MP3 files that can be joined with a standard MP3 concat/remux tool. This final GPU check is manual and must be recorded as such if not run by the agent.

## Open implementation choices for the plan

- Confirm which already-installed encoder is usable inside the VoxCPM venv (system `ffmpeg` is present and `pydub` is installed in the inspected environment); do not add dependencies without a verified need.
- Exact batch multipart field name, archive response headers, error detail schema, and whether server returns progress via polling or the desktop updates progress after each item. Prefer the smallest local HTTP design that keeps accurate segment progress and does not block UI.
- The current local sidecar API may need a small internal refactor to share generation/encoding while preserving existing `/generate` and `/clone` behavior.

These implementation details must preserve the user-approved scope above.
