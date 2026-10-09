"""SRT cue waveform fitting, timeline composition, and local file encoding."""

import io
from pathlib import Path
import shutil
import subprocess

import librosa
import numpy as np
import soundfile as sf


TARGET_SAMPLE_RATE = 44100
MAX_STRETCH_RATE = 1.5


class SrtAudioError(RuntimeError):
    def __init__(self, message: str, *, code: str = "srt_render_failed", cue_position: int | None = None, cue_index: int | None = None):
        super().__init__(message)
        self.code = code
        self.cue_position = cue_position
        self.cue_index = cue_index


def _mono_float(samples: np.ndarray) -> np.ndarray:
    result = np.asarray(samples, dtype=np.float32)
    if result.ndim == 2:
        result = result.mean(axis=1) if result.shape[1] <= 8 else result.mean(axis=0)
    if result.ndim != 1 or result.size == 0 or not np.all(np.isfinite(result)):
        raise SrtAudioError("Generated cue contains invalid audio samples.")
    return result


MIN_SPEED = 0.5
MAX_SPEED = 2.0


def apply_speed(samples: np.ndarray, speed: float) -> np.ndarray:
    """Đổi tốc độ đọc (giữ nguyên cao độ); speed=1.0 trả lại nguyên bản."""
    if not MIN_SPEED <= speed <= MAX_SPEED:
        raise SrtAudioError(f"Speed must be between {MIN_SPEED} and {MAX_SPEED}.", code="invalid_speed")
    if abs(speed - 1.0) < 1e-6:
        return samples
    return np.asarray(librosa.effects.time_stretch(_mono_float(samples), rate=speed), dtype=np.float32)


def validate_cue_fit(samples: np.ndarray, sample_rate: int, target_ms: int, cue_position: int, cue_index: int) -> float:
    if sample_rate <= 0 or target_ms <= 0:
        raise SrtAudioError("Cue sample rate and target duration must be positive.", cue_position=cue_position, cue_index=cue_index)
    mono = _mono_float(samples)
    target_samples_native = max(1, round(target_ms * sample_rate / 1000))
    rate = len(mono) / target_samples_native
    if rate > MAX_STRETCH_RATE + 1e-9:
        raise SrtAudioError(
            f"Cue {cue_index} needs {rate:.2f}x speed, above the 1.5x limit.",
            code="cue_too_long", cue_position=cue_position, cue_index=cue_index,
        )
    return rate


def prepare_cue(samples: np.ndarray, sample_rate: int, target_ms: int, cue_position: int, cue_index: int) -> np.ndarray:
    rate = validate_cue_fit(samples, sample_rate, target_ms, cue_position, cue_index)
    mono = _mono_float(samples)
    if rate > 1.0 + 1e-9:
        mono = librosa.effects.time_stretch(mono, rate=rate)
    target_samples = round(target_ms * TARGET_SAMPLE_RATE / 1000)
    if sample_rate != TARGET_SAMPLE_RATE:
        mono = librosa.resample(mono, orig_sr=sample_rate, target_sr=TARGET_SAMPLE_RATE)
    if len(mono) > target_samples:
        raise SrtAudioError("Time-stretched cue did not fit its subtitle interval without cropping.", code="cue_too_long", cue_position=cue_position, cue_index=cue_index)
    return np.asarray(mono, dtype=np.float32)


def render_srt_audio(cues: list[dict[str, int]], generated_wavs: list[np.ndarray], native_sample_rate: int, output_format: str, destination: Path) -> None:
    destination = Path(destination)
    if output_format not in {"wav", "mp3"} or len(cues) == 0 or len(cues) != len(generated_wavs):
        raise SrtAudioError("Cue, audio, or output format is invalid.")
    timeline_end_ms = int(cues[-1]["end_ms"])
    timeline_samples = round(timeline_end_ms * TARGET_SAMPLE_RATE / 1000)
    if timeline_samples <= 0:
        raise SrtAudioError("Timeline end must be positive.")
    track = np.zeros(timeline_samples, dtype=np.float32)
    for position, (cue, waveform) in enumerate(zip(cues, generated_wavs), 1):
        start_ms, end_ms, cue_index = int(cue["start_ms"]), int(cue["end_ms"]), int(cue["index"])
        if start_ms < 0 or end_ms <= start_ms or end_ms > timeline_end_ms:
            raise SrtAudioError("Cue timestamps are invalid.", cue_position=position, cue_index=cue_index)
        prepared = prepare_cue(waveform, native_sample_rate, end_ms - start_ms, position, cue_index)
        start_sample = round(start_ms * TARGET_SAMPLE_RATE / 1000)
        end_sample = start_sample + len(prepared)
        if end_sample > timeline_samples:
            raise SrtAudioError("Cue exceeds final timeline.", cue_position=position, cue_index=cue_index)
        track[start_sample:end_sample] = prepared

    temporary = destination.with_name(f".{destination.stem}.srt-tmp{destination.suffix}")
    try:
        if temporary.exists():
            raise FileExistsError(temporary)
        if output_format == "wav":
            with sf.SoundFile(temporary, mode="x", samplerate=TARGET_SAMPLE_RATE, channels=1, format="WAV", subtype="PCM_16") as output:
                output.write(track)
        else:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise SrtAudioError("ffmpeg with libmp3lame is required for MP3 output.", code="encoder_unavailable")
            raw = io.BytesIO()
            sf.write(raw, track, TARGET_SAMPLE_RATE, format="WAV", subtype="PCM_16")
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-ac", "1", "-ar", "44100", "-codec:a", "libmp3lame", "-b:a", "128k", "-write_xing", "0", "-f", "mp3", "pipe:1"],
                input=raw.getvalue(), capture_output=True, check=False,
            )
            if result.returncode or len(result.stdout) < 128 or not (result.stdout.startswith(b"ID3") or result.stdout.startswith((b"\xff\xfb", b"\xff\xf3"))):
                raise SrtAudioError("MP3 encoding failed: " + result.stderr.decode(errors="replace")[:500], code="encoder_failed")
            with temporary.open("xb") as output:
                output.write(result.stdout)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
