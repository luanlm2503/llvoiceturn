"""Independent HTTP client for the developer-local VoxCPM sidecar."""

from dataclasses import dataclass
from pathlib import Path

import json
import re

import httpx


class VoxCPMApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


MIN_SPEED = 0.5
MAX_SPEED = 2.0
JOB_STATUSES = {"queued", "running", "completed", "failed", "cancelled"}


def _add_speed(data: dict[str, str], speed: float) -> None:
    if not MIN_SPEED <= speed <= MAX_SPEED:
        raise VoxCPMApiError(f"Speed must be between {MIN_SPEED} and {MAX_SPEED}.")
    if abs(speed - 1.0) > 1e-6:  # 1.0 thì không gửi, giữ tương thích sidecar cũ
        data["speed"] = f"{speed:.2f}"


@dataclass(frozen=True)
class VoiceSettings:
    """ElevenLabs-style settings; the sidecar maps them onto VoxCPM2 knobs."""

    stability: int = 50
    similarity: int = 75
    style_exaggeration: int = 0
    speaker_boost: bool = False


DEFAULT_VOICE = VoiceSettings()


def _add_voice(data: dict[str, str], voice: VoiceSettings | None) -> None:
    voice = voice or DEFAULT_VOICE
    for name in ("stability", "similarity", "style_exaggeration"):
        value = getattr(voice, name)
        if not 0 <= value <= 100:
            raise VoxCPMApiError(f"{name} must be between 0 and 100.")
        if value != getattr(DEFAULT_VOICE, name):  # chỉ gửi giá trị khác mặc định
            data[name] = str(int(value))
    if voice.speaker_boost:
        data["speaker_boost"] = "true"


MAX_PROMPT_TEXT = 1000


def _add_prompt_text(data: dict[str, str], prompt_text: str | None, reference_audio: Path | None) -> None:
    """Lời thoại của audio mẫu: chỉ có nghĩa khi có audio mẫu (VoxCPM2 nói tiếp audio đó)."""
    prompt_text = (prompt_text or "").strip()
    if not prompt_text:
        return
    if reference_audio is None:
        raise VoxCPMApiError("Reference transcript needs a reference audio file.")
    if len(prompt_text) > MAX_PROMPT_TEXT:
        raise VoxCPMApiError(f"Reference transcript must be at most {MAX_PROMPT_TEXT} characters.")
    data["prompt_text"] = prompt_text


class VoxCPMClient:
    def __init__(self, base_url: str = "http://127.0.0.1:7861", timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=3.0))

    def health(self) -> dict[str, object]:
        response = self._request("GET", "/health")
        try:
            result = response.json()
        except ValueError as exc:
            raise VoxCPMApiError("Local VoxCPM2 returned an invalid health response.") from exc
        if not isinstance(result, dict):
            raise VoxCPMApiError("Local VoxCPM2 returned an invalid health response.")
        return result

    def generate(self, text: str, style: str | None = None) -> bytes:
        response = self._request("POST", "/generate", json={"text": text, "style": style})
        if not response.headers.get("content-type", "").lower().startswith("audio/wav") or not response.content.startswith(b"RIFF"):
            raise VoxCPMApiError("Local VoxCPM2 did not return a valid WAV audio file.")
        return response.content

    def clone(self, text: str, reference_audio: Path, style: str | None = None) -> bytes:
        try:
            with Path(reference_audio).open("rb") as audio_file:
                response = self._request(
                    "POST",
                    "/clone",
                    data={"text": text, "style": style or ""},
                    files={"reference_audio": (Path(reference_audio).name, audio_file)},
                )
        except OSError as exc:
            raise VoxCPMApiError(f"Cannot read the selected reference audio file: {exc}") from exc
        if not response.headers.get("content-type", "").lower().startswith("audio/wav") or not response.content.startswith(b"RIFF"):
            raise VoxCPMApiError("Local VoxCPM2 did not return a valid WAV audio file.")
        return response.content

    def start_batch(
        self, segments: list[str], style: str | None = None, reference_audio: Path | None = None, speed: float = 1.0,
        voice: VoiceSettings | None = None, prompt_text: str | None = None,
    ) -> str:
        if not 1 <= len(segments) <= 100 or any(not segment.strip() or len(segment) > 4000 for segment in segments):
            raise VoxCPMApiError("Batch requires 1 to 100 non-empty segments, each at most 4,000 characters.")
        data = {"segments": json.dumps(segments, ensure_ascii=False), "style": style or ""}
        _add_speed(data, speed)
        _add_voice(data, voice)
        _add_prompt_text(data, prompt_text, reference_audio)
        files = None
        try:
            if reference_audio is not None:
                audio_file = Path(reference_audio).open("rb")
                files = {"reference_audio": (Path(reference_audio).name, audio_file)}
            try:
                response = self._request("POST", "/batch", data=data, files=files)
            finally:
                if files:
                    audio_file.close()
        except OSError as exc:
            raise VoxCPMApiError(f"Cannot read the selected reference audio file: {exc}") from exc
        try:
            payload = response.json()
            job_id = payload.get("job_id")
        except (ValueError, AttributeError) as exc:
            raise VoxCPMApiError("Local VoxCPM2 returned an invalid batch job response.") from exc
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id):
            raise VoxCPMApiError("Local VoxCPM2 returned an invalid batch job ID.")
        return job_id

    def batch_status(self, job_id: str) -> dict[str, object]:
        self._validate_job_id(job_id)
        response = self._request("GET", f"/batch/{job_id}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise VoxCPMApiError("Local VoxCPM2 returned invalid batch status.") from exc
        if not isinstance(payload, dict) or payload.get("status") not in JOB_STATUSES:
            raise VoxCPMApiError("Local VoxCPM2 returned invalid batch status.")
        return payload

    def cancel_batch(self, job_id: str) -> None:
        """Yêu cầu dừng; sidecar làm nốt đoạn đang tạo rồi báo trạng thái "cancelled"."""
        self._validate_job_id(job_id)
        self._request("POST", f"/batch/{job_id}/cancel")

    def download_batch(self, job_id: str) -> bytes:
        self._validate_job_id(job_id)
        response = self._request("GET", f"/batch/{job_id}/download")
        if not response.headers.get("content-type", "").lower().startswith("application/zip") or not response.content.startswith(b"PK"):
            raise VoxCPMApiError("Local VoxCPM2 did not return a valid ZIP batch archive.")
        return response.content

    def start_srt(
        self, cues, style: str | None, output_format: str, reference_audio: Path | None = None, speed: float = 1.0,
        voice: VoiceSettings | None = None, prompt_text: str | None = None,
    ) -> str:
        if output_format not in {"wav", "mp3"}:
            raise VoxCPMApiError("SRT output format must be WAV or MP3.")
        if not 1 <= len(cues) <= 100:
            raise VoxCPMApiError("SRT requires 1 to 100 cues.")
        serialized = []
        total_text = 0
        prior_end = 0
        for cue in cues:
            if len(cue.text.strip()) > 4000 or not cue.text.strip():
                raise VoxCPMApiError("Each SRT cue must have 1 to 4,000 characters.")
            total_text += len(cue.text.strip())
            if cue.start_ms < prior_end:
                raise VoxCPMApiError(f"SRT cue {cue.index} overlaps a previous cue.")
            prior_end = cue.end_ms
            serialized.append({"index": cue.index, "start_ms": cue.start_ms, "end_ms": cue.end_ms, "text": cue.text})
        if total_text > 40000 or prior_end > 30 * 60 * 1000:
            raise VoxCPMApiError("SRT exceeds local cue text or 30-minute timeline limits.")
        data = {"cues": json.dumps(serialized, ensure_ascii=False), "output_format": output_format, "style": style or ""}
        _add_speed(data, speed)
        _add_voice(data, voice)
        _add_prompt_text(data, prompt_text, reference_audio)
        files = None
        try:
            if reference_audio is not None:
                audio_file = Path(reference_audio).open("rb")
                files = {"reference_audio": (Path(reference_audio).name, audio_file)}
            try:
                response = self._request("POST", "/srt", data=data, files=files)
            finally:
                if files:
                    audio_file.close()
        except OSError as exc:
            raise VoxCPMApiError(f"Cannot read the selected reference audio file: {exc}") from exc
        try:
            job_id = response.json().get("job_id")
        except (ValueError, AttributeError) as exc:
            raise VoxCPMApiError("Local VoxCPM2 returned an invalid SRT job response.") from exc
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id):
            raise VoxCPMApiError("Local VoxCPM2 returned an invalid SRT job ID.")
        return job_id

    def srt_status(self, job_id: str) -> dict[str, object]:
        self._validate_job_id(job_id)
        response = self._request("GET", f"/srt/{job_id}")
        try:
            result = response.json()
        except ValueError as exc:
            raise VoxCPMApiError("Local VoxCPM2 returned invalid SRT status.") from exc
        if not isinstance(result, dict) or result.get("status") not in JOB_STATUSES:
            raise VoxCPMApiError("Local VoxCPM2 returned invalid SRT status.")
        return result

    def cancel_srt(self, job_id: str) -> None:
        self._validate_job_id(job_id)
        self._request("POST", f"/srt/{job_id}/cancel")

    def download_srt(self, job_id: str, output_format: str) -> bytes:
        self._validate_job_id(job_id)
        if output_format not in {"wav", "mp3"}:
            raise VoxCPMApiError("SRT output format must be WAV or MP3.")
        response = self._request("GET", f"/srt/{job_id}/download")
        content_type = response.headers.get("content-type", "").lower()
        if output_format == "wav":
            valid = content_type.startswith("audio/wav") and response.content.startswith(b"RIFF")
            expected = "WAV"
        else:
            valid = content_type.startswith("audio/mpeg") and (response.content.startswith(b"ID3") or response.content.startswith((b"\xff\xfb", b"\xff\xf3")))
            expected = "MP3"
        if not valid:
            raise VoxCPMApiError(f"Local VoxCPM2 did not return a valid {expected} SRT audio file.")
        return response.content

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id):
            raise VoxCPMApiError("Invalid batch job ID.")

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise VoxCPMApiError("The local VoxCPM2 request timed out. Try again or use shorter text.") from exc
        except httpx.HTTPError as exc:
            raise VoxCPMApiError("Cannot reach VoxCPM2. Start the local VoxCPM2 service with local-voxcpm\\run.ps1.") from exc
        if response.is_success:
            return response
        try:
            body = response.json()
            detail = body.get("detail", body)
            if isinstance(detail, dict):
                message = str(detail.get("message", "Local VoxCPM2 request failed."))
                code = detail.get("code")
            else:
                message, code = str(detail), None
        except (ValueError, AttributeError):
            message, code = "Local VoxCPM2 request failed.", None
        raise VoxCPMApiError(message, status_code=response.status_code, code=code)

    def close(self) -> None:
        self._http.close()
