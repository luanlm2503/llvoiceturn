from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Protocol

import librosa
import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, Field

from srt_audio import MAX_SPEED, MIN_SPEED, SrtAudioError, apply_speed, render_srt_audio, validate_cue_fit
from voice_settings import VoiceSettings, VoiceSettingsError


class VoxCPMProtocol(Protocol):
    tts_model: object

    def generate(
        self,
        *,
        text: str,
        cfg_value: float = 2.0,
        inference_timesteps: int = 10,
        reference_wav_path: str | None = None,
    ) -> np.ndarray: ...


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    style: str | None = Field(default=None, max_length=500)


def _load_model() -> VoxCPMProtocol:
    os.environ.setdefault("HF_HOME", r"C:\Users\manhl\voxcpm\hf_cache")
    from voxcpm import VoxCPM

    return VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)


def create_app(model_loader: Callable[[], VoxCPMProtocol] | None = None) -> FastAPI:
    loader = model_loader or _load_model
    state: dict[str, object] = {"status": "loading", "detail": None, "model": None}
    state_lock = threading.Lock()
    inference_lock = threading.Lock()
    jobs: dict[str, dict[str, object]] = {}
    jobs_lock = threading.Lock()
    batch_threads: list[threading.Thread] = []
    max_jobs = 8
    job_ttl_seconds = 15 * 60
    srt_jobs: dict[str, dict[str, object]] = {}
    srt_threads: list[threading.Thread] = []

    def load_model() -> None:
        try:
            model = loader()
        except Exception as exc:
            with state_lock:
                state.update(status="error", detail=str(exc) or type(exc).__name__)
        else:
            with state_lock:
                state.update(status="ready", detail=None, model=model)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        thread = threading.Thread(target=load_model, name="voxcpm-model-loader", daemon=True)
        thread.start()
        yield
        thread.join(timeout=2)
        for worker in batch_threads + srt_threads:
            worker.join()
        with jobs_lock:
            for job in list(jobs.values()) + list(srt_jobs.values()):
                shutil.rmtree(str(job["directory"]), ignore_errors=True)
            jobs.clear()
            srt_jobs.clear()

    app = FastAPI(title="Local VoxCPM2", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str | None]:
        with state_lock:
            return {"status": str(state["status"]), "detail": state["detail"]}  # type: ignore[dict-item]

    def ready_model():
        with state_lock:
            model = state["model"]
            status = state["status"]
            detail = state["detail"]
        if status != "ready" or model is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "model_not_ready", "message": str(detail or "VoxCPM2 is still loading; retry shortly.")},
            )
        return model

    def render(model, text: str, style: str | None, reference_wav_path: str | None = None) -> Response:
        if not text.strip():
            raise HTTPException(status_code=422, detail={"code": "invalid_text", "message": "Text must not be empty."})
        full_text = f"({style.strip()}){text}" if style and style.strip() else text
        with inference_lock:
            try:
                kwargs = {"text": full_text, "cfg_value": 2.0, "inference_timesteps": 10}
                if reference_wav_path is not None:
                    kwargs["reference_wav_path"] = reference_wav_path
                wav = model.generate(**kwargs)
                sample_rate = int(model.tts_model.sample_rate)
                audio = io.BytesIO()
                sf.write(audio, np.asarray(wav), sample_rate, format="WAV", subtype="PCM_16")
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail={"code": "generation_failed", "message": str(exc) or "Speech generation failed."},
                ) from exc
        return Response(content=audio.getvalue(), media_type="audio/wav")

    def cleanup_jobs() -> None:
        now = time.monotonic()
        with jobs_lock:
            expired = [job_id for job_id, job in jobs.items() if job["status"] in {"completed", "failed", "cancelled"} and now - float(job["updated"]) > job_ttl_seconds]
            for job_id in expired:
                shutil.rmtree(str(jobs[job_id]["directory"]), ignore_errors=True)
                del jobs[job_id]

    def reserve_job_slot(message: str) -> None:
        """Gọi khi đang giữ jobs_lock: chỉ job đang chờ/chạy mới chiếm chỗ; job đã xong cũ nhất bị dọn để nhường chỗ."""
        registries = (jobs, srt_jobs)
        active = sum(1 for registry in registries for job in registry.values() if job["status"] in {"queued", "running"})
        if active >= max_jobs:
            raise HTTPException(status_code=503, detail={"code": "job_capacity", "message": message})
        finished = sorted(
            ((float(job["updated"]), registry, job_id) for registry in registries for job_id, job in registry.items() if job["status"] not in {"queued", "running"}),
            key=lambda item: item[0],
        )
        for _, registry, job_id in finished:
            if len(jobs) + len(srt_jobs) < max_jobs:
                break
            shutil.rmtree(str(registry[job_id]["directory"]), ignore_errors=True)
            del registry[job_id]

    def request_cancel(registry: dict[str, dict[str, object]], job_id: str, missing: str) -> dict[str, object]:
        """Đánh dấu dừng; đoạn đang tạo vẫn chạy nốt rồi job dừng trước đoạn kế tiếp."""
        with jobs_lock:
            job = registry.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": missing})
            if job["status"] in {"queued", "running"}:
                job["cancel_requested"] = True
            return {"status": job["status"], "cancel_requested": bool(job.get("cancel_requested"))}

    def encode_mp3(wav: np.ndarray, sample_rate: int, destination: Path) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("encoder_unavailable: ffmpeg with libmp3lame is required in the VoxCPM environment.")
        raw = io.BytesIO()
        sf.write(raw, np.asarray(wav), sample_rate, format="WAV", subtype="PCM_16")
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-ac", "1", "-ar", "44100", "-codec:a", "libmp3lame", "-b:a", "128k", "-write_xing", "0", "-f", "mp3", "pipe:1"],
            input=raw.getvalue(), capture_output=True, check=False,
        )
        if result.returncode or len(result.stdout) < 128 or not (result.stdout.startswith(b"ID3") or result.stdout.startswith((b"\xff\xfb", b"\xff\xf3"))):
            raise RuntimeError("MP3 encoding failed: " + result.stderr.decode(errors="replace")[:500])
        destination.write_bytes(result.stdout)

    def check_speed(speed: float) -> float:
        if not MIN_SPEED <= speed <= MAX_SPEED:
            raise HTTPException(status_code=422, detail={"code": "invalid_speed", "message": f"Speed must be between {MIN_SPEED} and {MAX_SPEED}."})
        return speed

    def check_prompt_text(prompt_text: str | None, has_reference: bool) -> str | None:
        prompt_text = (prompt_text or "").strip() or None
        if prompt_text and not has_reference:
            raise HTTPException(status_code=422, detail={"code": "invalid_prompt_text", "message": "Reference transcript needs a reference audio file."})
        return prompt_text

    def model_call(voice: VoiceSettings, text: str, style: str | None, reference_path: str | None, prompt_text: str | None = None) -> dict[str, object]:
        """Có lời thoại mẫu thì dùng chế độ nối tiếp (ultimate cloning): model nói tiếp audio mẫu nên giữ
        sát giọng và cách phát âm; phần mô tả giọng bị bỏ vì nó sẽ bị đọc lẫn vào sau lời thoại mẫu."""
        if reference_path and prompt_text:
            return {"text": text, **voice.model_kwargs(), "reference_wav_path": reference_path, "prompt_wav_path": reference_path, "prompt_text": prompt_text}
        kwargs = {"text": voice.full_text(text, style), **voice.model_kwargs()}
        if reference_path:
            kwargs["reference_wav_path"] = reference_path
        return kwargs

    def anchor_voice(model, wav: np.ndarray, directory: Path) -> str | None:
        """Không có audio mẫu thì VoxCPM2 chọn giọng ngẫu nhiên cho từng đoạn; lấy giọng của đoạn đầu
        làm audio mẫu cho các đoạn sau để cả job đọc cùng một giọng."""
        if wav.size == 0:
            return None
        sample_rate = int(model.tts_model.sample_rate)
        path = directory / "anchor.wav"
        sf.write(str(path), wav[: sample_rate * 20], sample_rate, format="WAV")
        return str(path)

    def check_voice(stability: int, similarity: int, style_exaggeration: int, speaker_boost: bool) -> VoiceSettings:
        try:
            return VoiceSettings(stability, similarity, style_exaggeration, speaker_boost)
        except VoiceSettingsError as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_voice_settings", "message": str(exc)}) from exc

    def run_batch(job_id: str, segments: list[str], style: str | None, reference_path: str | None, speed: float = 1.0, voice: VoiceSettings = VoiceSettings(), prompt_text: str | None = None) -> None:
        job = jobs[job_id]
        job["status"] = "running"
        job["updated"] = time.monotonic()
        try:
            model = ready_model()
            if not shutil.which("ffmpeg"):
                raise RuntimeError("encoder_unavailable: ffmpeg with libmp3lame is required in the VoxCPM environment.")
            directory = Path(str(job["directory"]))
            voice_path = reference_path
            with inference_lock:
                for index, segment in enumerate(segments, 1):
                    if job.get("cancel_requested"):
                        break
                    try:
                        kwargs = model_call(voice, segment, style, voice_path, prompt_text)
                        raw = np.asarray(model.generate(**kwargs), dtype=np.float32)
                        if voice_path is None:
                            voice_path = anchor_voice(model, raw, directory)
                        wav = voice.boost(apply_speed(raw, speed))
                        encode_mp3(wav, int(model.tts_model.sample_rate), directory / f"{index:03d}.mp3")
                        job["completed_segments"] = index
                        job["updated"] = time.monotonic()
                    except Exception as exc:
                        job.update(status="failed", failed_segment=index, error_code="encoder_unavailable" if str(exc).startswith("encoder_unavailable:") else "batch_generation_failed", error=str(exc), updated=time.monotonic())
                        return
            # Khi bị dừng giữa chừng vẫn đóng gói các đoạn đã xong để client lưu lại.
            finished = int(job["completed_segments"])
            archive_path = directory / "result.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
                for index in range(1, finished + 1):
                    archive.write(directory / f"{index:03d}.mp3", arcname=f"{index:03d}.mp3")
            job.update(status="cancelled" if finished < len(segments) else "completed", archive_path=str(archive_path), updated=time.monotonic())
        except Exception as exc:
            code = "encoder_unavailable" if str(exc).startswith("encoder_unavailable:") else "batch_generation_failed"
            job.update(status="failed", failed_segment=job.get("completed_segments", 0) + 1, error_code=code, error=str(exc), updated=time.monotonic())
        finally:
            if reference_path:
                try:
                    os.unlink(reference_path)
                except FileNotFoundError:
                    pass

    @app.post("/batch", status_code=202)
    def start_batch(
        segments: str = Form(...),
        style: str | None = Form(default=None, max_length=500),
        speed: float = Form(default=1.0),
        stability: int = Form(default=50),
        similarity: int = Form(default=75),
        style_exaggeration: int = Form(default=0),
        speaker_boost: bool = Form(default=False),
        reference_audio: UploadFile | None = File(default=None),
        prompt_text: str | None = Form(default=None, max_length=1000),
    ) -> JSONResponse:
        check_speed(speed)
        voice = check_voice(stability, similarity, style_exaggeration, speaker_boost)
        prompt_text = check_prompt_text(prompt_text, reference_audio is not None)
        try:
            parsed = json.loads(segments)
        except (TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_segments", "message": "Segments must be a JSON array."}) from exc
        if not isinstance(parsed, list) or not 1 <= len(parsed) <= 100 or any(not isinstance(item, str) or not item.strip() or len(item) > 4000 for item in parsed):
            raise HTTPException(status_code=422, detail={"code": "invalid_segments", "message": "Provide 1 to 100 non-empty segments, each at most 4,000 characters."})
        cleanup_jobs()
        reference_path: str | None = None
        converted_path: str | None = None
        try:
            if reference_audio:
                suffix = Path(reference_audio.filename or "").suffix.lower()
                if suffix not in {".wav", ".mp3"}:
                    raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": "Choose a WAV or MP3 reference file."})
                total = 0
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="voxcpm-batch-ref-") as source:
                    reference_path = source.name
                    while chunk := reference_audio.file.read(1024 * 1024):
                        total += len(chunk)
                        if total > 25 * 1024 * 1024:
                            raise HTTPException(status_code=413, detail={"code": "audio_too_large", "message": "Reference audio must be 25 MiB or smaller."})
                        source.write(chunk)
                if not total:
                    raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": "Reference audio is empty."})
                if suffix == ".mp3":
                    samples, sample_rate = librosa.load(reference_path, sr=None, mono=False)
                    duration = samples.shape[-1] / sample_rate if samples.size else 0
                    if not duration or duration > 30:
                        raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": "Reference audio must be readable and 30 seconds or shorter."})
                    converted = tempfile.NamedTemporaryFile(delete=False, suffix=".wav", prefix="voxcpm-batch-ref-converted-")
                    converted_path = converted.name
                    converted.close()
                    sf.write(converted_path, samples.T if samples.ndim > 1 else samples, sample_rate, format="WAV")
                    os.unlink(reference_path)
                    reference_path = converted_path
                    converted_path = None
                else:
                    info = sf.info(reference_path)
                    if not info.frames or not info.samplerate or info.duration > 30:
                        raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": "Reference audio must be readable and 30 seconds or shorter."})
                    with sf.SoundFile(reference_path) as audio_file:
                        audio_file.read(frames=1, dtype="float32")
            with jobs_lock:
                reserve_job_slot("Batch queue is full; try again after a job finishes.")
                job_id = str(uuid.uuid4())
                directory = tempfile.mkdtemp(prefix=f"voxcpm-batch-{job_id}-")
                job = {"status": "queued", "completed_segments": 0, "total_segments": len(parsed), "failed_segment": None, "error": None, "error_code": None, "updated": time.monotonic(), "directory": directory}
                jobs[job_id] = job
            thread = threading.Thread(target=run_batch, args=(job_id, parsed, style, reference_path, speed, voice, prompt_text), name=f"voxcpm-batch-{job_id}", daemon=True)
            try:
                thread.start()
            except Exception:
                with jobs_lock:
                    jobs.pop(job_id, None)
                    shutil.rmtree(directory, ignore_errors=True)
                raise
            batch_threads.append(thread)
            reference_path = None
            return JSONResponse({"job_id": job_id}, status_code=202)
        finally:
            if reference_audio:
                reference_audio.file.close()
            for path in (reference_path, converted_path):
                if path:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass

    def cleanup_srt_jobs() -> None:
        now = time.monotonic()
        with jobs_lock:
            expired = [job_id for job_id, job in srt_jobs.items() if job["status"] in {"completed", "failed", "cancelled"} and now - float(job["updated"]) > job_ttl_seconds]
            for job_id in expired:
                shutil.rmtree(str(srt_jobs[job_id]["directory"]), ignore_errors=True)
                del srt_jobs[job_id]

    def run_srt(job_id: str, cues: list[dict[str, object]], style: str | None, reference_path: str | None, output_format: str, speed: float = 1.0, voice: VoiceSettings = VoiceSettings(), prompt_text: str | None = None) -> None:
        job = srt_jobs[job_id]
        job.update(status="running", updated=time.monotonic())
        try:
            model = ready_model()
            if output_format == "mp3" and not shutil.which("ffmpeg"):
                raise SrtAudioError("ffmpeg with libmp3lame is required for MP3 output.", code="encoder_unavailable")
            wavs: list[np.ndarray] = []
            voice_path = reference_path
            with inference_lock:
                for position, cue in enumerate(cues, 1):
                    if job.get("cancel_requested"):
                        break
                    try:
                        text = str(cue["text"])
                        kwargs = model_call(voice, text, style, voice_path, prompt_text)
                        raw = np.asarray(model.generate(**kwargs), dtype=np.float32)
                        if voice_path is None:
                            voice_path = anchor_voice(model, raw, Path(str(job["directory"])))
                        wav = voice.boost(apply_speed(raw, speed))
                        validate_cue_fit(wav, int(model.tts_model.sample_rate), int(cue["end_ms"]) - int(cue["start_ms"]), position, int(cue["index"]))
                        wavs.append(wav)
                        job.update(completed_cues=position, updated=time.monotonic())
                    except SrtAudioError as exc:
                        job.update(status="failed", failed_position=exc.cue_position, failed_index=exc.cue_index, error_code=exc.code, error=str(exc), updated=time.monotonic())
                        return
                    except Exception as exc:
                        job.update(status="failed", failed_position=position, failed_index=cue.get("index"), error_code="generation_failed", error=str(exc), updated=time.monotonic())
                        return
                if not wavs:
                    job.update(status="cancelled", result_path=None, updated=time.monotonic())
                    return
                output_path = Path(str(job["directory"])) / ("dubbed." + output_format)
                try:
                    # Bị dừng thì ghép các cue đã xong; timeline kết thúc ở cue cuối cùng đó.
                    render_srt_audio(cues[:len(wavs)], wavs, int(model.tts_model.sample_rate), output_format, output_path)
                except SrtAudioError as exc:
                    job.update(status="failed", failed_position=exc.cue_position, failed_index=exc.cue_index, error_code=exc.code, error=str(exc), updated=time.monotonic())
                    return
            job.update(status="cancelled" if len(wavs) < len(cues) else "completed", result_path=str(output_path), updated=time.monotonic())
        except Exception as exc:
            job.update(status="failed", failed_position=job.get("completed_cues", 0) + 1, error_code="srt_generation_failed", error=str(exc), updated=time.monotonic())
        finally:
            if reference_path:
                try:
                    os.unlink(reference_path)
                except FileNotFoundError:
                    pass

    @app.post("/srt", status_code=202)
    def start_srt(
        cues: str = Form(...),
        output_format: str = Form(...),
        style: str | None = Form(default=None, max_length=500),
        speed: float = Form(default=1.0),
        stability: int = Form(default=50),
        similarity: int = Form(default=75),
        style_exaggeration: int = Form(default=0),
        speaker_boost: bool = Form(default=False),
        reference_audio: UploadFile | None = File(default=None),
        prompt_text: str | None = Form(default=None, max_length=1000),
    ) -> JSONResponse:
        check_speed(speed)
        voice = check_voice(stability, similarity, style_exaggeration, speaker_boost)
        prompt_text = check_prompt_text(prompt_text, reference_audio is not None)
        try:
            parsed = json.loads(cues)
        except (TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_cues", "message": "Cues must be a JSON array."}) from exc
        if output_format not in {"wav", "mp3"}:
            raise HTTPException(status_code=422, detail={"code": "invalid_format", "message": "Output format must be wav or mp3."})
        if not isinstance(parsed, list) or not 1 <= len(parsed) <= 100:
            raise HTTPException(status_code=422, detail={"code": "invalid_cues", "message": "Provide 1 to 100 subtitle cues."})
        total_text = 0
        prior_end = 0
        seen_indices: set[int] = set()
        validated: list[dict[str, object]] = []
        for position, cue in enumerate(parsed, 1):
            if not isinstance(cue, dict) or set(cue) != {"index", "start_ms", "end_ms", "text"}:
                raise HTTPException(status_code=422, detail={"code": "invalid_cues", "message": f"Cue {position} has an invalid structure."})
            try:
                index, start_ms, end_ms = cue["index"], cue["start_ms"], cue["end_ms"]
                text = cue["text"]
                if any(isinstance(value, bool) or not isinstance(value, int) for value in (index, start_ms, end_ms)) or not isinstance(text, str):
                    raise ValueError
            except (KeyError, ValueError):
                raise HTTPException(status_code=422, detail={"code": "invalid_cues", "message": f"Cue {position} has invalid fields."})
            if index <= 0 or index in seen_indices or start_ms < 0 or end_ms <= start_ms or end_ms > 30 * 60 * 1000 or start_ms < prior_end or not text.strip() or len(text.strip()) > 4000:
                raise HTTPException(status_code=422, detail={"code": "invalid_cues", "message": f"Cue {index} has invalid timing, text, or overlaps a previous cue."})
            seen_indices.add(index)
            prior_end = end_ms
            total_text += len(text.strip())
            if total_text > 40000:
                raise HTTPException(status_code=422, detail={"code": "invalid_cues", "message": "Total subtitle text cannot exceed 40,000 characters."})
            validated.append({"index": index, "start_ms": start_ms, "end_ms": end_ms, "text": text.strip()})
        cleanup_srt_jobs()
        reference_path: str | None = None
        converted_reference_path: str | None = None
        try:
            if reference_audio:
                suffix = Path(reference_audio.filename or "").suffix.lower()
                if suffix not in {".wav", ".mp3"}:
                    raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": "Choose a WAV or MP3 reference file."})
                total = 0
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="voxcpm-srt-ref-") as source:
                    reference_path = source.name
                    while chunk := reference_audio.file.read(1024 * 1024):
                        total += len(chunk)
                        if total > 25 * 1024 * 1024:
                            raise HTTPException(status_code=413, detail={"code": "audio_too_large", "message": "Reference audio must be 25 MiB or smaller."})
                        source.write(chunk)
                if not total:
                    raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": "Reference audio is empty."})
                try:
                    if suffix == ".mp3":
                        samples, rate = librosa.load(reference_path, sr=None, mono=False)
                        duration = samples.shape[-1] / rate if samples.size else 0
                        if not duration or duration > 30:
                            raise ValueError("Reference audio must be readable and 30 seconds or shorter.")
                        converted = tempfile.NamedTemporaryFile(delete=False, suffix=".wav", prefix="voxcpm-srt-ref-converted-")
                        converted_reference_path = converted.name
                        converted.close()
                        sf.write(converted_reference_path, samples.T if samples.ndim > 1 else samples, rate, format="WAV")
                        os.unlink(reference_path)
                        reference_path = converted_reference_path
                        converted_reference_path = None
                    else:
                        info = sf.info(reference_path)
                        duration = info.duration if info.frames > 0 else 0
                        with sf.SoundFile(reference_path) as audio_file:
                            audio_file.read(frames=1, dtype="float32")
                    if not duration or duration > 30:
                        raise ValueError("Reference audio must be readable and 30 seconds or shorter.")
                except Exception as exc:
                    raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": str(exc)}) from exc
            with jobs_lock:
                reserve_job_slot("Local audio job capacity is full; try again after a job finishes.")
                job_id = str(uuid.uuid4())
                directory = tempfile.mkdtemp(prefix=f"voxcpm-srt-{job_id}-")
                srt_jobs[job_id] = {"status": "queued", "completed_cues": 0, "total_cues": len(validated), "failed_position": None, "failed_index": None, "error_code": None, "error": None, "updated": time.monotonic(), "directory": directory, "output_format": output_format}
            worker = threading.Thread(target=run_srt, args=(job_id, validated, style, reference_path, output_format, speed, voice, prompt_text), name=f"voxcpm-srt-{job_id}", daemon=True)
            try:
                worker.start()
            except Exception:
                with jobs_lock:
                    srt_jobs.pop(job_id, None)
                    shutil.rmtree(directory, ignore_errors=True)
                raise
            srt_threads.append(worker)
            reference_path = None
            return JSONResponse({"job_id": job_id}, status_code=202)
        finally:
            if reference_audio:
                reference_audio.file.close()
            if reference_path:
                try:
                    os.unlink(reference_path)
                except FileNotFoundError:
                    pass
            if converted_reference_path:
                try:
                    os.unlink(converted_reference_path)
                except FileNotFoundError:
                    pass

    @app.get("/srt/{job_id}")
    def srt_status(job_id: str) -> dict[str, object]:
        cleanup_srt_jobs()
        with jobs_lock:
            job = srt_jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "SRT job was not found or expired."})
            return {key: job.get(key) for key in ("status", "completed_cues", "total_cues", "failed_position", "failed_index", "error_code", "error")}

    @app.post("/srt/{job_id}/cancel")
    def srt_cancel(job_id: str) -> dict[str, object]:
        return request_cancel(srt_jobs, job_id, "SRT job was not found or expired.")

    @app.get("/srt/{job_id}/download")
    def srt_download(job_id: str) -> Response:
        cleanup_srt_jobs()
        with jobs_lock:
            job = srt_jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "SRT job was not found or expired."})
            if job["status"] not in {"completed", "cancelled"} or not job.get("result_path"):
                raise HTTPException(status_code=409, detail={"code": "job_not_completed", "message": "SRT job is not complete."})
            result_path = Path(str(job["result_path"]))
            content = result_path.read_bytes()
            output_format = job["output_format"]
        media_type = "audio/wav" if output_format == "wav" else "audio/mpeg"
        return Response(content=content, media_type=media_type, headers={"Content-Disposition": f"attachment; filename=dubbed.{output_format}"})

    @app.get("/batch/{job_id}")
    def batch_status(job_id: str) -> dict[str, object]:
        cleanup_jobs()
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "Batch job was not found or expired."})
            return {key: job.get(key) for key in ("status", "completed_segments", "total_segments", "failed_segment", "error", "error_code")}

    @app.post("/batch/{job_id}/cancel")
    def batch_cancel(job_id: str) -> dict[str, object]:
        return request_cancel(jobs, job_id, "Batch job was not found or expired.")

    @app.get("/batch/{job_id}/download")
    def batch_download(job_id: str) -> Response:
        cleanup_jobs()
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "Batch job was not found or expired."})
            if job["status"] not in {"completed", "cancelled"}:
                raise HTTPException(status_code=409, detail={"code": "job_not_completed", "message": "Batch is not complete."})
            content = Path(str(job["archive_path"])).read_bytes()
        return Response(content=content, media_type="application/zip")

    @app.post("/generate")
    def generate(request: GenerateRequest) -> Response:
        model = ready_model()
        return render(model, request.text, request.style)

    @app.post("/clone")
    def clone(
        text: str = Form(..., min_length=1, max_length=4000),
        style: str | None = Form(default=None, max_length=500),
        reference_audio: UploadFile = File(...),
    ) -> Response:
        if not text.strip():
            raise HTTPException(status_code=422, detail={"code": "invalid_text", "message": "Text must not be empty."})
        filename = (reference_audio.filename or "").lower()
        suffix = Path(filename).suffix
        if suffix not in {".wav", ".mp3"}:
            raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": "Choose a WAV or MP3 reference file."})

        source_path: str | None = None
        converted_path: str | None = None
        try:
            total = 0
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="voxcpm-ref-") as source:
                source_path = source.name
                while chunk := reference_audio.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 25 * 1024 * 1024:
                        raise HTTPException(status_code=413, detail={"code": "audio_too_large", "message": "Reference audio must be 25 MiB or smaller."})
                    source.write(chunk)
            if total == 0:
                raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": "Reference audio is empty."})

            try:
                if suffix == ".mp3":
                    samples, sample_rate = librosa.load(source_path, sr=None, mono=False)
                    if samples.size == 0:
                        raise ValueError("Audio contains no samples.")
                    duration = samples.shape[-1] / sample_rate
                    if duration > 30.0:
                        raise HTTPException(status_code=422, detail={"code": "audio_too_long", "message": "Reference audio must be 30 seconds or shorter."})
                    converted = tempfile.NamedTemporaryFile(delete=False, suffix=".wav", prefix="voxcpm-ref-converted-")
                    converted_path = converted.name
                    converted.close()
                    sf.write(converted_path, samples.T if samples.ndim > 1 else samples, sample_rate, format="WAV")
                    reference_path = converted_path
                else:
                    info = sf.info(source_path)
                    if info.frames <= 0 or info.samplerate <= 0:
                        raise ValueError("Audio contains no samples.")
                    if info.duration > 30.0:
                        raise HTTPException(status_code=422, detail={"code": "audio_too_long", "message": "Reference audio must be 30 seconds or shorter."})
                    with sf.SoundFile(source_path) as audio_file:
                        audio_file.seek(0)
                        audio_file.read(frames=1, dtype="float32")
                    reference_path = source_path
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": f"Could not read reference audio: {exc}"}) from exc

            model = ready_model()
            return render(model, text, style, reference_path)
        finally:
            reference_audio.file.close()
            for path in (source_path, converted_path):
                if path:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass

    return app


app = create_app()
