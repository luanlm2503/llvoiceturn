# VoxCPM2 Local SRT Dubbing — Design

**Date:** 2026-09-30  
**Status:** Draft for user review

## Goal

Implement the desktop **Lồng tiếng SRT** tab as a local-only workflow: import or paste subtitles, review/edit cues, synthesize Vietnamese/English speech with VoxCPM2, preserve cue start times, and export one complete WAV or MP3 track aligned to the subtitle timeline.

## User-approved behavior

- Accept both `.srt` file import and pasted SRT text.
- Display parsed cues in an editable table; users can correct cue text and timing before synthesis.
- Export one complete WAV or MP3 file, not one file per cue.
- Preserve each cue's start time from the edited SRT timeline.
- Reject malformed SRT cues and any cues whose time intervals overlap; report cue numbers and do not begin synthesis for invalid timelines.
- Generate each cue using VoxCPM2 through the local sidecar only. Use the selected style and optional WAV/MP3 voice reference for every cue. No MiniMax request or remote fallback.
- If synthesized speech does not fit its cue interval, accelerate it only as much as needed and never above 1.5×. If it still does not fit at 1.5×, report the cue and do not create/export a partial final file.
- Place generated audio at the cue's original start timestamp, with silence in gaps. Final duration reaches the last cue's subtitle end time (at minimum; if audio ends earlier, silence fills to that end).
- Encode MP3 explicitly with the existing profile (mono, 44.1 kHz, CBR 128 kbps); WAV is PCM 16-bit mono, 44.1 kHz.
- Keep the job asynchronous in the desktop, display per-cue progress, and preserve existing desktop-local VoxCPM2 architecture.

## Current project context

- `client/` is a PySide6 desktop application whose TTS page has a local VoxCPM2 client and batch-job progress pattern.
- `local-voxcpm/` is a loopback FastAPI sidecar at `127.0.0.1:7861`, currently exposing health, single generate/clone, and MP3 batch jobs.
- The VoxCPM environment has `librosa`, `soundfile`, and ffmpeg available. Model inference is serialized under a shared lock; the local model is loaded once.
- Client tabs for SRT, voices, queue, and history are currently placeholders. The client has no SRT parser or timeline audio compositor yet.
- MiniMax server/config remains in the repo but is no longer used by desktop. Do not alter server/backend, secrets, Compose, PostgreSQL, Redis, or the user's existing Compose change.

## Proposed architecture

Use a dedicated local SRT job endpoint in the VoxCPM sidecar rather than asking the desktop to coordinate many single-generation requests. The desktop owns SRT parsing, validation feedback, editable cue table, output selection, and job progress UI. It submits validated cues, style, output format, and optional reference audio to the sidecar. The sidecar synthesizes cues in source order while holding the inference lock for the SRT job, applies pitch-preserving time stretch when needed, composes a single timeline-aligned track, encodes the selected format, and exposes job status/result download. This keeps timing and audio manipulation close to the VoxCPM/audio libraries while preserving UI responsiveness.

```text
Desktop SRT tab
  ├─ open .srt / paste → shared parser → editable cue table
  ├─ validate timestamp order/non-overlap → local SRT client
  └─ poll progress asynchronously → save final result
                                 │ loopback only
                                 ▼
VoxCPM sidecar
  ├─ validate bounded cue payload and optional local reference audio
  ├─ synthesize cues sequentially under shared inference lock
  ├─ time-stretch each cue only as required, maximum rate 1.5×
  ├─ compose timeline with silence and final cue end duration
  └─ return one complete WAV or MP3 result; no partial file on failure
```

## SRT parser and editing

- Support standard SubRip cue sequence, timestamp line `HH:MM:SS,mmm --> HH:MM:SS,mmm`, and one or more text lines per cue.
- Decode UTF-8 and UTF-8 BOM. If UTF-8 decoding fails, show a readable encoding error and do not silently corrupt text.
- Normalize CRLF/LF; tolerate blank lines between cues and a missing final blank line.
- Require positive cue indices, valid timestamps, start < end, non-empty text after trimming, and unique cue indices. Preserve parsed source order; display the cue number even if cue numbers are non-contiguous.
- For pasted/file content, a malformed cue produces an error identifying its cue/block and reason. Do not silently discard malformed blocks or synthesize a partial parse.
- Table fields: cue number (read-only), start time, end time, and text (editable). Time values are editable in `HH:MM:SS,mmm` form. Re-validate after edits and before submitting.
- Reject cue interval overlap (`next.start < previous.end`) before any sidecar request. Adjacent cues where `next.start == previous.end` are valid.
- Require 1–100 cues; each cue text is at most 4,000 Unicode characters; total text is at most 40,000 characters. These bounds align with local request and runtime expectations.
- Show cues in playback order and provide a readable preview/count. A parse or validation error leaves the editable table intact where possible and disables synthesis until corrected.

## Timeline synthesis and output

- Input to the sidecar is an ordered list of `{index, start_ms, end_ms, text}` plus optional style, `output_format` (`wav` or `mp3`), and optional reference audio.
- Validate full cue list, duration, output format, style, and reference file before model inference. Maximum timeline end is 30 minutes; reject larger timelines to bound local memory/rendering.
- For each cue, generate a waveform at VoxCPM's native sample rate. Determine target duration as `end_ms - start_ms`.
- If speech duration is at or below target, keep natural playback speed. Otherwise use pitch-preserving `librosa.effects.time_stretch` by the exact required rate, provided rate <= 1.5. If required rate exceeds 1.5, fail at that cue with a structured `cue_too_long` error (1-based cue position plus original SRT cue number); never crop speech or move its start time.
- Convert each prepared cue to mono float audio at the common timeline sample rate 44.1 kHz. Place it at `start_ms` on a silent buffer and fill timeline gaps with digital silence. Since subtitle cue intervals are non-overlapping and prepared audio must fit within its interval, no speech layers overlap.
- Final output duration is the final cue's `end_ms`, including any remaining silence. Output includes one full track only after all cues have synthesized and fit.
- MP3: encode mono/44.1 kHz/libmp3lame CBR 128 kbps; WAV: PCM signed 16-bit mono/44.1 kHz. Use available libraries/ffmpeg only; do not add dependencies without an independently verified need.
- Sidecar result is one attachment named `dubbed.wav` or `dubbed.mp3`. Failed/cancelled jobs provide no download result; any intermediate reference, cue, render, or archive files are removed on failure, expiry, and shutdown.

## Local job API and lifecycle

Exact path names may follow the existing `/batch` conventions, but the plan must define them consistently. Required behavior:

- A multipart local request creates an asynchronous job and returns a UUID immediately. It includes JSON cue payload, optional style/reference file, and output format.
- A status request returns `queued|running|completed|failed`, completed cue count, total cue count, failed position/SRT index, and structured error code/message.
- A result endpoint returns the final audio only when completed and matching the requested format; failed or active jobs have no result.
- The SRT job holds the shared inference lock over the whole cue sequence so other TTS/batch requests cannot interleave between cues.
- Bound job registry, request sizes, timeline length and TTL consistently with the existing local batch job implementation. Clean all job temp data on expiry/shutdown; ensure cleanup does not delete files used by a running worker.
- Loopback binding remains `127.0.0.1`; no auth, public endpoint, Docker, or proxy involvement is introduced.

## Desktop UI and interaction

- Replace only the SRT placeholder with a dedicated SRT page; TTS remains unchanged. Other placeholder tabs remain placeholders.
- Provide `Mở file SRT`, editable paste/input area or paste action, parse/preview action, and editable cue table. File import and paste must call the same parser.
- Provide voice-style field and optional WAV/MP3 reference consistent with the TTS page. Reference is sent only to local sidecar.
- Provide output format choice WAV/MP3 and output file chooser; do not overwrite an existing file without confirmation or an explicit new path.
- Disable synthesis for parse/timeline errors, empty cue lists, over-limit cue count/text/timeline, or missing output path.
- Run submit, polling, download, and final save asynchronously using Qt worker patterns; show `cue i of N`. Disable conflicting controls while a job is active; restore controls on success or failure.
- Show actionable errors with SRT cue number and distinguish invalid SRT/timing, sidecar unavailable/model loading, too-long cue, encoder failure, and save permission/collision errors. Never present a failed job as successful or save a partial track.
- Keep all text/audio local; do not log payloads or file contents.

## Testing and acceptance criteria

1. Parser tests cover UTF-8/BOM, CRLF/LF, multi-line cue text, missing final blank separator, invalid encoding, malformed/missing/duplicate index, invalid timestamps, empty text and precise cue-specific errors.
2. Timeline validation tests cover zero/reversed timestamps, non-contiguous index preservation, overlaps, adjacent cues, 100/101 cues, per-cue and total text boundaries, and 30-minute maximum.
3. Audio helper tests with deterministic waveforms verify silence/gap placement, cue starts/duration, output sample rate/channels/subtype, final duration and no overlap.
4. Time-stretch tests verify rate 1.0 for fitting audio, exact required rate below 1.5, accepted boundary at 1.5, and `cue_too_long` above boundary without returning a final track.
5. Sidecar API tests use a fake model to verify sequential cue calls, reference forwarded to every cue, shared-lock job execution, progress, WAV/MP3 result type, failed cue index, no partial result, malformed payload/reference rejection before inference, and temporary-file cleanup on failure/expiry/shutdown.
6. Client tests verify loopback-only request fields, status/result validation, format correctness, actionable sidecar errors, and no MiniMax URL use.
7. UI tests verify file and pasted SRT use the same parser, edits are exactly what is submitted, invalid/overlapping cues disable generation, progress and failure restore controls, result save/collision behavior, and placeholder tabs remain.
8. Existing client, sidecar, and server tests continue passing; no backend/config/Compose/database/Redis changes.
9. Manual RTX 3060 test: a real SRT with gaps produces correctly timed speech/silence in WAV and MP3; long cue speeds up up to 1.5×; overlong cue shows correct cue number without creating output; optional WAV and MP3 references clone each cue locally. This test remains user-run unless actually performed with the real model/GPU.

## Explicitly out of scope

- MiniMax, cloud TTS, and remote fallback.
- Video import/rendering, replacing original dialogue, or mixing original audio/music.
- Automatic subtitle translation, speech recognition, speaker diarization, or multiple voices per cue.
- Moving subtitle cue timestamps, cropping speech, or allowing overlapping speech tracks.
- Persistent job recovery across desktop/sidecar restarts.
- Implementing the remaining voices, queue, or history tabs as part of this SRT feature.

This design is intentionally limited to one complete subtitle-aligned voice track, one optional local reference voice, and one output format per run.
