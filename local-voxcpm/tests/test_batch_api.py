import io
import json
import os
import tempfile
import time
import wave
import zipfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app import create_app


def wav_bytes(seconds=0.02, sample_rate=16000):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0\0" * int(seconds * sample_rate))
    return stream.getvalue()


class FakeModel:
    def __init__(self, fail_at=None, delay=0):
        self.tts_model = type("Tts", (), {"sample_rate": 16000})()
        self.calls = []
        self.fail_at = fail_at
        self.delay = delay

    def generate(self, *, text, cfg_value=2.0, inference_timesteps=10, reference_wav_path=None):
        self.calls.append((text, reference_wav_path))
        if self.delay:
            time.sleep(self.delay)
        if len(self.calls) == self.fail_at:
            raise RuntimeError("inference failed")
        return np.zeros(160, dtype=np.float32)


def ready_client(model):
    context = TestClient(create_app(model_loader=lambda: model))
    context.__enter__()
    return context


def run_until_done(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/batch/{job_id}").json()
        if state["status"] in {"completed", "failed"}:
            return state
        time.sleep(0.02)
    raise AssertionError("batch job did not finish")


def start(client, segments, **extra):
    reference = extra.pop("reference_audio", None)
    data = {"segments": json.dumps(segments), **extra}
    files = {"reference_audio": reference} if reference else None
    response = client.post("/batch", data=data, files=files)
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def test_batch_returns_job_id_and_progresses_in_order():
    model = FakeModel()
    with ready_client(model) as client:
        job_id = start(client, ["one", "two", "three"])
        state = run_until_done(client, job_id)
    assert state["status"] == "completed", state
    assert state["completed_segments"] == state["total_segments"] == 3
    assert [call[0] for call in model.calls] == ["one", "two", "three"]


def test_batch_archive_has_numbered_mp3_files_and_profile():
    model = FakeModel()
    with ready_client(model) as client:
        job_id = start(client, ["one", "two"])
        assert run_until_done(client, job_id)["status"] == "completed"
        response = client.get(f"/batch/{job_id}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["001.mp3", "002.mp3"]
        assert all(len(archive.read(name)) > 128 and (archive.read(name).startswith(b"ID3") or archive.read(name).startswith((b"\xff\xfb", b"\xff\xf3"))) for name in archive.namelist())


def test_clone_reference_is_used_for_every_segment():
    model = FakeModel()
    with ready_client(model) as client:
        job_id = start(client, ["one", "two"], style="warm", reference_audio=("voice.wav", wav_bytes(), "audio/wav"))
        assert run_until_done(client, job_id)["status"] == "completed"
    assert [call[0] for call in model.calls] == ["(warm)one", "(warm)two"]
    assert all(call[1] and not os.path.exists(call[1]) for call in model.calls)


def test_batch_serializes_whole_job_against_plain_generation():
    model = FakeModel(delay=0.04)
    with ready_client(model) as client:
        job_id = start(client, ["a1", "a2", "a3"])
        client.post("/generate", json={"text": "plain"})
        assert run_until_done(client, job_id)["status"] == "completed"
    assert [call[0] for call in model.calls] in (["a1", "a2", "a3", "plain"], ["plain", "a1", "a2", "a3"])


def test_batch_rejects_invalid_lists_and_references_before_inference():
    model = FakeModel()
    with ready_client(model) as client:
        requests = [
            client.post("/batch", data={"segments": "[]"}),
            client.post("/batch", data={"segments": json.dumps([" "])}),
            client.post("/batch", data={"segments": json.dumps(["x" * 4001])}),
            client.post("/batch", data={"segments": json.dumps(["x"] * 101)}),
            client.post("/batch", data={"segments": json.dumps(["x"]), "reference_audio": ("a.txt", b"x", "text/plain")}),
            client.post("/batch", data={"segments": json.dumps(["x"]), "reference_audio": ("bad.wav", b"bad", "audio/wav")}),
            client.post("/batch", data={"segments": json.dumps(["x"]), "reference_audio": ("empty.wav", b"", "audio/wav")}),
        ]
    assert [response.status_code for response in requests] == [422] * len(requests)
    assert model.calls == []


def test_batch_failure_reports_segment_and_has_no_download():
    model = FakeModel(fail_at=2)
    with ready_client(model) as client:
        job_id = start(client, ["one", "two", "three"])
        state = run_until_done(client, job_id)
        download = client.get(f"/batch/{job_id}/download")
    assert state["status"] == "failed"
    assert state["failed_segment"] == 2
    assert download.status_code == 409


def test_batch_requires_ffmpeg_actionably(monkeypatch):
    import app

    monkeypatch.setattr(app.shutil, "which", lambda _: None)
    model = FakeModel()
    with ready_client(model) as client:
        job_id = start(client, ["one"])
        state = run_until_done(client, job_id)
    assert state["status"] == "failed"
    assert state["error_code"] == "encoder_unavailable"
    assert model.calls == []


def test_batch_download_rejects_unknown_job():
    with ready_client(FakeModel()) as client:
        assert client.get("/batch/not-a-job").status_code == 404
        assert client.get("/batch/not-a-job/download").status_code == 404


def test_batch_job_directory_is_removed_on_service_shutdown(tmp_path, monkeypatch):
    import app

    original_mkdtemp = app.tempfile.mkdtemp
    created = []

    def make_job_dir(*args, **kwargs):
        kwargs["dir"] = tmp_path
        path = original_mkdtemp(*args, **kwargs)
        created.append(Path(path))
        return path

    monkeypatch.setattr(app.tempfile, "mkdtemp", make_job_dir)
    model = FakeModel()
    with TestClient(create_app(model_loader=lambda: model)) as client:
        job_id = start(client, ["one"])
        assert run_until_done(client, job_id)["status"] == "completed"
        assert created and created[0].exists()
    assert created and not created[0].exists()
