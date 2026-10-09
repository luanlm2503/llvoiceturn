import io
import json
import time
import wave

import numpy as np
from fastapi.testclient import TestClient

from app import create_app


def reference_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000); stream.writeframes(b"\0\0" * 1600)
    return output.getvalue()


class Model:
    def __init__(self, fail_at=None):
        self.tts_model = type("Tts", (), {"sample_rate": 16000})()
        self.calls = []
        self.fail_at = fail_at
    def generate(self, *, text, cfg_value=2, inference_timesteps=10, reference_wav_path=None):
        self.calls.append((text, reference_wav_path))
        if len(self.calls) == self.fail_at: raise RuntimeError("inference failed")
        return np.ones(8000, dtype=np.float32) * .05


def post_job(client, cues, **fields):
    files = {"reference_audio": fields.pop("reference_audio")} if "reference_audio" in fields else None
    return client.post("/srt", data={"cues": json.dumps(cues), "output_format": "wav", **fields}, files=files)


def wait_done(client, job_id):
    for _ in range(500):
        state = client.get(f"/srt/{job_id}").json()
        if state["status"] in {"completed", "failed"}: return state
        time.sleep(.01)
    raise AssertionError("SRT job did not finish")


def test_srt_job_returns_progress_and_complete_wav_with_voice_reference():
    model = Model()
    cues = [{"index": 1, "start_ms": 0, "end_ms": 1000, "text": "one"}, {"index": 4, "start_ms": 1500, "end_ms": 2500, "text": "two"}]
    with TestClient(create_app(model_loader=lambda: model)) as client:
        response = post_job(client, cues, style="warm", reference_audio=("voice.wav", reference_wav(), "audio/wav"))
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        state = wait_done(client, job_id)
        result = client.get(f"/srt/{job_id}/download")
    assert state["status"] == "completed" and state["completed_cues"] == 2
    assert [call[0] for call in model.calls] == ["(warm)one", "(warm)two"]
    assert all(call[1] for call in model.calls)
    assert result.headers["content-type"].startswith("audio/wav") and result.content.startswith(b"RIFF")


def test_srt_rejects_overlap_and_bad_payload_before_inference():
    model = Model()
    with TestClient(create_app(model_loader=lambda: model)) as client:
        response = post_job(client, [{"index": 1, "start_ms": 0, "end_ms": 2000, "text": "a"}, {"index": 2, "start_ms": 1000, "end_ms": 3000, "text": "b"}])
        assert response.status_code == 422
        assert post_job(client, [{"index": 1, "start_ms": 0, "end_ms": 1000, "text": "x" * 4001}]).status_code == 422
    assert model.calls == []


def test_srt_failure_or_overlong_cue_has_no_download():
    model = Model()
    cues = [{"index": 8, "start_ms": 0, "end_ms": 100, "text": "long"}]
    with TestClient(create_app(model_loader=lambda: model)) as client:
        response = post_job(client, cues)
        job_id = response.json()["job_id"]
        state = wait_done(client, job_id)
        result = client.get(f"/srt/{job_id}/download")
    assert state["status"] == "failed" and state["error_code"] == "cue_too_long" and state["failed_index"] == 8
    assert result.status_code == 409


def test_srt_mp3_result_uses_audio_mpeg_content_type():
    model = Model()
    cues = [{"index": 1, "start_ms": 0, "end_ms": 1000, "text": "short"}]
    with TestClient(create_app(model_loader=lambda: model)) as client:
        response = client.post("/srt", data={"cues": json.dumps(cues), "output_format": "mp3"})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert wait_done(client, job_id)["status"] == "completed"
        result = client.get(f"/srt/{job_id}/download")
    assert result.headers["content-type"] == "audio/mpeg" and (result.content.startswith(b"ID3") or result.content.startswith((b"\xff\xfb", b"\xff\xf3")))


def test_srt_invalid_reference_and_unknown_job():
    model = Model()
    with TestClient(create_app(model_loader=lambda: model)) as client:
        response = post_job(client, [{"index": 1, "start_ms": 0, "end_ms": 1000, "text": "x"}], reference_audio=("bad.txt", b"bad", "text/plain"))
        assert response.status_code == 422
        assert client.get("/srt/bad-id").status_code == 404
    assert model.calls == []


def test_mp3_voice_reference_is_converted_to_wav_for_model(monkeypatch):
    import app

    model = Model()
    monkeypatch.setattr(app.librosa, "load", lambda *args, **kwargs: (np.zeros(1600, dtype=np.float32), 16000))
    with TestClient(create_app(model_loader=lambda: model)) as client:
        response = post_job(client, [{"index": 1, "start_ms": 0, "end_ms": 1000, "text": "x"}], reference_audio=("voice.mp3", b"fake-mp3", "audio/mpeg"))
        job_id = response.json()["job_id"]
        assert wait_done(client, job_id)["status"] == "completed"
    assert model.calls[0][1].lower().endswith(".wav")


def test_overlong_first_cue_stops_before_generating_later_cues():
    model = Model()
    cues = [
        {"index": 1, "start_ms": 0, "end_ms": 100, "text": "too long"},
        {"index": 2, "start_ms": 100, "end_ms": 2000, "text": "must not run"},
    ]
    with TestClient(create_app(model_loader=lambda: model)) as client:
        response = post_job(client, cues)
        job_id = response.json()["job_id"]
        state = wait_done(client, job_id)
    assert state["status"] == "failed" and state["failed_position"] == 1
    assert len(model.calls) == 1
