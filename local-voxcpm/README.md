# Local VoxCPM2 sidecar (developer-only)

This service runs VoxCPM2 in the separate `C:\Users\manhl\voxcpm\.venv` environment. It does not use the MiniMax proxy, Docker, PostgreSQL, or Redis. The API listens only on `127.0.0.1:7861`.

## Prerequisites

- VoxCPM 2.0.3 and its model checkpoint installed/cached by `C:\Users\manhl\voxcpm\test_voxcpm.py`.
- FastAPI, Uvicorn, and pytest in that virtual environment. Install missing service/test dependencies into the VoxCPM venv only (never the LLVoice server environment).

## Start / stop

From PowerShell in this directory:

```powershell
.\run.ps1
```

The model loads once on startup. Poll `http://127.0.0.1:7861/health`; status is `loading`, `ready`, or `error`. Stop with **Ctrl+C**. The app does not manage this process. `HF_HOME` points to `C:\Users\manhl\voxcpm\hf_cache`.

## API

- `GET /health`: readiness JSON.
- `POST /generate`: JSON such as `{"text":"Xin chào","style":"giọng ấm"}`. Returns a WAV response. Text is limited to 4,000 characters; optional style is translated to VoxCPM instruction syntax.
- `POST /clone`: multipart form fields `text`, optional `style`, and `reference_audio` (WAV or MP3). Transcript is not required. References are limited to 25 MiB and 30 seconds; MP3 is decoded locally with librosa. Source/conversion temporary files are deleted after each request, including failures.
- `POST /batch`: multipart fields `segments` (JSON string array, 1–100 non-empty strings, each <=4,000 characters), optional `style` (<=500 chars), and optional `reference_audio` (WAV/MP3, <=25 MiB and <=30 seconds). Returns `202` with a job UUID. The model encodes each segment as mono, 44.1 kHz, CBR 128 kbps MP3 using ffmpeg/libmp3lame.
- `GET /batch/{job_id}`: job status and completed/total segment counts; poll locally for progress. Failed jobs include `failed_segment` and do not provide a partial archive.
- `GET /batch/{job_id}/download`: completed jobs only; returns a ZIP containing flat `001.mp3`..`NNN.mp3` entries. Jobs are limited to eight retained jobs and expire after 15 minutes; temporary files are removed on expiry and service shutdown. MP3 files can be concatenated using an MP3-aware concat/remux tool; do not join by raw byte concatenation.
- `POST /srt`: multipart fields `cues` (JSON ordered list of `{index,start_ms,end_ms,text}`; 1–100 cues, <=4,000 chars/cue and <=40,000 total), `output_format` (`wav` or `mp3`), optional `style` (<=500 chars), and optional WAV/MP3 `reference_audio` (<=25 MiB, <=30 sec). Timeline must be ordered, non-overlapping and end within 30 minutes. Returns `202` with a job UUID.
- `GET /srt/{job_id}`: status, completed/total cue counts and cue-specific error information. The sidecar synthesizes all cues sequentially under the model lock; speech is pitch-preserving time-stretched only as needed, up to 1.5×. A cue that cannot fit fails the complete job, with no partial result.
- `GET /srt/{job_id}/download`: completed jobs only; returns one full timeline WAV or MP3 (`audio/wav` or `audio/mpeg`), with silence in subtitle gaps. WAV is mono 44.1 kHz PCM16; MP3 is mono 44.1 kHz CBR 128 kbps. SRT and batch jobs share an eight-job local capacity and 15-minute completed-job TTL; temporary files are removed at expiry and service shutdown. Start `local-voxcpm\run.ps1` and use the desktop tab **Lồng tiếng SRT** to load/paste/edit cues, pick output, then wait for per-cue progress; the resulting track preserves SRT gaps/timestamps.

## Test without CUDA/model downloads

```powershell
$env:PYTHONPATH = (Resolve-Path .).Path
C:\Users\manhl\voxcpm\.venv\Scripts\python.exe -m pytest -q
```

Tests inject a fake model and verify the HTTP contract. The real model remains in the external VoxCPM cache.
